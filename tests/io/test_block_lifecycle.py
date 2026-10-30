"""Native file-to-file processing preserves science and transaction boundaries."""
import importlib.util
import io
import json
import os
from pathlib import Path
import time
import tracemalloc
from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from nmrnext.analysis import dataset_digest
from nmrnext.errors import NMRNextError
from nmrnext.io import read_dataset, write_dataset
from nmrnext.model import Axis, Dataset
from nmrnext.operations import run_operations


def blocks():
    assert importlib.util.find_spec('nmrnext.blocks') is not None, 'bounded file lifecycle is not implemented'
    import nmrnext.blocks as module
    return module


def fixture(path, dimensions=3, dtype='float32', frequency=False):
    sizes = (3, 4, 5) if dimensions == 3 else (2, 3, 4, 5)
    axes = [Axis(f'a{i}', n, step=.001, domain='frequency' if frequency else 'time',
                 unit='Hz' if frequency else 's') for i, n in enumerate(sizes)]
    values = np.arange(2**dimensions*np.prod(sizes), dtype=dtype).reshape(2**dimensions, *sizes)/100
    dataset = Dataset(values, axes, [a.id for a in axes], {'sampling': 'fully_sampled', 'nested': {'source': 1}}, [{'source': 'fixture'}])
    write_dataset(dataset, path, format='native')
    return dataset


def operations(dimensions):
    return [{'op': 'window', 'axis': 'a0', 'lb_hz': 2, 'first_point_scale': .5},
            {'op': 'zero_fill', 'axis': 'a0', 'size': 6}, {'op': 'fft', 'axis': 'a0'},
            {'op': 'reference', 'axis': 'a0', 'reference_ppm': 4.7, 'observation_mhz': 500,
             'reference_hz': 0, 'spectral_width_hz': 1200, 'frequency_scale': -1},
            {'op': 'phase', 'axis': 'a0', 'p0': 23, 'p1': -9, 'pivot': 2},
            {'op': 'reverse', 'axis': 'a0'}, {'op': 'crop', 'axis': 'a0', 'start': 1, 'stop': 5},
            {'op': 'discard_imaginary', 'axis': 'a0', 'version': '1'},
            {'op': 'transpose', 'order': [f'a{i}' for i in reversed(range(dimensions))]}]


@pytest.mark.parametrize('dimensions', [3, 4])
@pytest.mark.parametrize('dtype', ['float32', 'float64'])
def test_file_result_matches_eager_all_samples_axes_records_and_numeric_digest(tmp_path, dimensions, dtype):
    source, target = tmp_path/'in.nmrh5', tmp_path/'out.nmrh5'
    original = fixture(source, dimensions, dtype)
    expected = run_operations(original, operations(dimensions), backend='native')
    report = blocks().process_to_file(source, target, operations(dimensions), memory_bytes=64*1024**2)
    actual = read_dataset(target)
    np.testing.assert_array_equal(actual.data, expected.data)
    assert actual.to_metadata() == expected.to_metadata()
    assert report['result']['dataset_digest'] == dataset_digest(expected)
    assert report['input']['dataset_digest'] == dataset_digest(original)
    assert report['execution_mode'] == 'bounded_native_hdf5'
    assert report['estimated_working_bytes'] <= 64*1024**2
    assert report['result']['sha256']
    assert not list(tmp_path.glob('.spinflow-blocks-*'))


def test_plan_does_not_select_samples_and_tiny_budget_refuses_before_scratch(tmp_path, monkeypatch):
    source = tmp_path/'in.nmrh5'
    fixture(source)
    module = blocks()
    def forbidden(*args, **kwargs):
        pytest.fail('header-only block plan selected samples')
    monkeypatch.setattr(h5py.Dataset, '__getitem__', forbidden)
    monkeypatch.setattr(h5py.Dataset, 'read_direct', forbidden)
    plan = module.plan_file_operations(source, operations(3), memory_bytes=64*1024**2,
                                       output_path=tmp_path/'out.nmrh5')
    assert plan['input_validation'] == 'header_only'
    assert plan['streaming'] is True
    assert plan['temporary_disk_bytes'] > 0
    with pytest.raises(NMRNextError) as error:
        module.process_to_file(source, tmp_path/'out.nmrh5', operations(3), memory_bytes=1)
    assert error.value.code == 'MEMORY_BUDGET_EXCEEDED'
    assert not (tmp_path/'out.nmrh5').exists()
    assert not list(tmp_path.glob('.spinflow-blocks-*'))


def test_nonfinite_input_cannot_be_hidden_by_component_discard(tmp_path):
    source = tmp_path/'in.nmrh5'
    fixture(source, frequency=True)
    with h5py.File(source, 'r+') as h:
        h['data'][-1, -1, -1, -1] = np.nan
    with pytest.raises(NMRNextError) as error:
        blocks().process_to_file(source, tmp_path/'out.nmrh5',
                                 [{'op': 'discard_imaginary', 'axis': 'a0', 'version': '1'}])
    assert error.value.code == 'NONFINITE_DATA'
    assert not (tmp_path/'out.nmrh5').exists()
    assert not list(tmp_path.glob('.spinflow-blocks-*'))


def test_output_refusal_alias_and_failed_stage_preserve_inputs_and_foreign_output(tmp_path, monkeypatch):
    source, target = tmp_path/'in.nmrh5', tmp_path/'out.nmrh5'
    fixture(source)
    original_bytes = source.read_bytes()
    target.write_bytes(b'foreign output')
    module = blocks()
    with pytest.raises(NMRNextError) as error:
        module.process_to_file(source, target, operations(3))
    assert error.value.code == 'OUTPUT_EXISTS'
    assert target.read_bytes() == b'foreign output'
    with pytest.raises(NMRNextError) as error:
        module.process_to_file(source, source, operations(3), overwrite=True)
    assert error.value.code == 'INPUT_OVERWRITE'
    alias = tmp_path/'alias.nmrh5'
    try:
        os.link(source, alias)
    except OSError:
        alias = None
    if alias is not None:
        with pytest.raises(NMRNextError) as error:
            module.process_to_file(source, alias, operations(3), overwrite=True)
        assert error.value.code == 'INPUT_OVERWRITE'
    def fail(*args, **kwargs):
        raise OSError('injected write failure')
    monkeypatch.setattr(module._engine, 'execute_stage', fail)
    with pytest.raises(NMRNextError):
        module.process_to_file(source, tmp_path/'new.nmrh5', operations(3))
    assert source.read_bytes() == original_bytes
    assert target.read_bytes() == b'foreign output'
    assert not (tmp_path/'new.nmrh5').exists()
    assert not list(tmp_path.glob('.spinflow-blocks-*'))


def test_source_change_and_final_publish_race_are_refused(tmp_path, monkeypatch):
    source, target = tmp_path/'in.nmrh5', tmp_path/'out.nmrh5'
    fixture(source)
    module = blocks()
    real = module._engine.execute_stage
    touched = False
    def alter_source(*args, **kwargs):
        nonlocal touched
        result = real(*args, **kwargs)
        if not touched:
            stat = source.stat()
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns+10_000_000))
            touched = True
        return result
    monkeypatch.setattr(module._engine, 'execute_stage', alter_source)
    with pytest.raises(NMRNextError) as error:
        module.process_to_file(source, target, operations(3))
    assert error.value.code == 'INPUT_CHANGED'
    assert not target.exists()
    monkeypatch.setattr(module._engine, 'execute_stage', real)
    created = False
    def race(*args, **kwargs):
        nonlocal created
        result = real(*args, **kwargs)
        if not created:
            target.write_bytes(b'foreign raced output')
            created = True
        return result
    monkeypatch.setattr(module._engine, 'execute_stage', race)
    with pytest.raises(NMRNextError) as error:
        module.process_to_file(source, target, operations(3))
    assert error.value.code == 'OUTPUT_EXISTS'
    assert target.read_bytes() == b'foreign raced output'
    assert not list(tmp_path.glob('.spinflow-blocks-*'))


def test_empty_recipe_preserves_complete_native_records_and_still_validates_input(tmp_path):
    source, target = tmp_path/'in.nmrh5', tmp_path/'out.nmrh5'
    original = fixture(source, 4)
    report = blocks().process_to_file(source, target, [])
    assert read_dataset(target).to_metadata() == original.to_metadata()
    np.testing.assert_array_equal(read_dataset(target).data, original.data)
    assert report['operations'] == 0
    assert report['result']['dataset_digest'] == dataset_digest(original)


def test_transaction_cleanup_never_removes_a_foreign_replacement(tmp_path):
    module = blocks()
    directory = tmp_path/'transaction'
    transaction = module._Transaction(directory)
    owned = transaction.create_path('stage.nmrh5')
    owned.write_bytes(b'ours')
    transaction.refresh(owned)
    transaction._close(owned)  # A completed, closed stage may be replaced before cleanup.
    replacement = tmp_path/'replacement'
    replacement.write_bytes(b'foreign')
    os.replace(replacement, owned)
    warnings = transaction.cleanup()
    assert owned.read_bytes() == b'foreign'
    assert warnings


def test_transaction_refuses_a_replaced_parent_before_creating_another_store(tmp_path):
    module = blocks()
    directory = tmp_path/'transaction'
    transaction = module._Transaction(directory)
    directory.rename(tmp_path/'original-directory')
    directory.mkdir()
    with pytest.raises(NMRNextError) as error:
        transaction.create_path('new.nmrh5')
    assert error.value.code == 'TEMPORARY_DIRECTORY_CHANGED'
    assert not list(directory.iterdir())
    assert transaction.cleanup()
    assert directory.is_dir()


def native_header_file(path, sizes, *, chunks=None, compression=None):
    axes = [Axis(f'a{i}', size, step=.001) for i, size in enumerate(sizes)]
    record = {'schema_version':'1', 'axes':[a.to_dict() for a in axes],
              'complex_axes':[a.id for a in axes], 'metadata':{'sampling':'fully_sampled'}, 'provenance':[]}
    with h5py.File(path, 'w') as h:
        h.attrs['schema_version'] = '1'
        h.attrs['metadata_json'] = json.dumps(record)
        h.create_dataset('data', shape=(2**len(axes), *sizes), dtype='float32', chunks=chunks, compression=compression)


def test_unbounded_source_chunk_and_unaccounted_filters_refuse_before_payload(tmp_path, monkeypatch):
    module = blocks()
    source = tmp_path/'large-chunk.nmrh5'
    native_header_file(source, (128,128,128), chunks=(8,128,128,128))
    def forbidden(*args, **kwargs):
        pytest.fail('refused source selected its payload')
    monkeypatch.setattr(h5py.Dataset, '__getitem__', forbidden)
    monkeypatch.setattr(h5py.Dataset, 'read_direct', forbidden)
    with pytest.raises(NMRNextError) as error:
        module.plan_file_operations(source, [{'op':'fft','axis':'a2'}], memory_bytes=64*1024**2)
    assert error.value.code == 'MEMORY_BUDGET_EXCEEDED'
    compressed = tmp_path/'filtered.nmrh5'
    native_header_file(compressed, (3,4,5), compression='gzip')
    with pytest.raises(NMRNextError) as error:
        module.plan_file_operations(compressed, [])
    assert error.value.code == 'UNSUPPORTED_BLOCK_STORAGE'


def test_missing_input_unsupported_profiles_and_insufficient_disk_are_structured(tmp_path, monkeypatch):
    module = blocks()
    with pytest.raises(NMRNextError) as error:
        module.plan_file_operations(tmp_path/'missing.nmrh5', [])
    assert error.value.code == 'INPUT_NOT_FOUND'
    source = tmp_path/'input.nmrh5'
    fixture(source)
    for options, code in [({'backend':'numpy'},'UNSUPPORTED_BLOCK_BACKEND'),
                          ({'threads':2},'UNSUPPORTED_BLOCK_BACKEND'),
                          ({'output_format':'nmrpipe-real'},'UNSUPPORTED_BLOCK_FORMAT')]:
        with pytest.raises(NMRNextError) as error:
            module.process_to_file(source, tmp_path/'out.nmrh5', [], **options)
        assert error.value.code == code
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda path: SimpleNamespace(free=0))
    with pytest.raises(NMRNextError) as error:
        module.process_to_file(source, tmp_path/'out.nmrh5', [])
    assert error.value.code == 'TEMPORARY_DISK_BUDGET_EXCEEDED'
    assert not list(tmp_path.glob('.spinflow-blocks-*'))


def test_arithmetic_overflow_cannot_be_hidden_by_a_later_crop(tmp_path):
    source = tmp_path/'input.nmrh5'
    dataset = fixture(source, dtype='float64')
    with h5py.File(source, 'r+') as h:
        h['data'][:, 0, :, :] = np.finfo(np.float64).max/2
    ops = [{'op':'window','axis':'a0','lb_hz':0,'first_point_scale':4},
           {'op':'crop','axis':'a0','start':1,'stop':3}]
    with pytest.raises(NMRNextError) as error:
        blocks().process_to_file(source, tmp_path/'out.nmrh5', ops)
    assert error.value.code == 'NUMERICAL_OVERFLOW'
    assert not (tmp_path/'out.nmrh5').exists()
    assert not list(tmp_path.glob('.spinflow-blocks-*'))


def test_foreign_replacement_of_closed_final_scratch_is_preserved(tmp_path, monkeypatch):
    source, target = tmp_path/'input.nmrh5', tmp_path/'out.nmrh5'
    fixture(source)
    module = blocks()
    publish = module._publish
    replaced = []
    def replace_owned(transaction, temporary, *args):
        replacement = tmp_path/'foreign'
        replacement.write_bytes(b'foreign replacement')
        os.replace(replacement, temporary)
        replaced.append(temporary)
        return publish(transaction, temporary, *args)
    monkeypatch.setattr(module, '_publish', replace_owned)
    with pytest.raises(NMRNextError) as error:
        module.process_to_file(source, target, [])
    assert error.value.code == 'TEMPORARY_FILE_CHANGED'
    assert replaced[0].read_bytes() == b'foreign replacement'
    assert error.value.details['cleanup_warnings']
    assert not target.exists()


def test_larger_than_budget_cube_has_bounded_sample_reads_and_stage_arrays(tmp_path, monkeypatch):
    source, target = tmp_path/'input.nmrh5', tmp_path/'out.nmrh5'
    native_header_file(source, (16,18,20,100))
    budget = 16*1024**2
    max_read = 0
    original = h5py.Dataset.__getitem__
    def bounded(self, selection):
        nonlocal max_read
        result = original(self, selection)
        max_read = max(max_read, result.nbytes)
        assert result.nbytes < budget
        return result
    monkeypatch.setattr(h5py.Dataset, '__getitem__', bounded)
    report = blocks().process_to_file(source, target, [{'op':'fft','axis':'a3'}], memory_bytes=budget)
    assert np.prod(report['input']['shape'])*4 > budget
    assert max_read <= 256*1024
    assert report['stages'][0]['processed_blocks'] > 1
    assert report['stages'][0]['max_observed_read_bytes'] <= report['plan']['steps'][0]['block']['max_read_bytes'] < budget
    assert report['estimated_working_bytes'] <= budget


def test_digest_selection_first_yield_has_constant_control_memory():
    module = blocks()
    peaks = []
    for slabs in (100_000, 1_000_000):
        header = SimpleNamespace(axes=[SimpleNamespace(size=n) for n in (slabs, 1, 32768)],
                                 dtype=np.dtype('float64'), shape=(1, slabs, 1, 32768))
        tracemalloc.start()
        try:
            selections = module._selections(header)
            assert next(selections) == (slice(0, 1), slice(0, 1), slice(0, 1), slice(0, 32768))
            peaks.append(tracemalloc.get_traced_memory()[1])
            selections.close()
        finally:
            tracemalloc.stop()
    assert max(peaks) < 64*1024, peaks


@pytest.mark.parametrize('dimensions', [3, 4])
@pytest.mark.parametrize('dtype', ['float32', 'float64'])
def test_partial_edge_digest_and_empty_copy_are_component_first_c_order(tmp_path, monkeypatch, dimensions, dtype):
    source, target = tmp_path/'in.nmrh5', tmp_path/'out.nmrh5'
    original = fixture(source, dimensions, dtype)
    module = blocks()
    monkeypatch.setattr(module, '_HASH_BYTES', 3*np.dtype(dtype).itemsize)
    pieces = [original.data[selection].ravel() for selection in module._selections(module.describe_dataset(source))]
    np.testing.assert_array_equal(np.concatenate(pieces), original.data.ravel())
    report = module.process_to_file(source, target, [])
    assert report['input']['dataset_digest'] == dataset_digest(original)
    assert report['result']['dataset_digest'] == dataset_digest(original)
    np.testing.assert_array_equal(read_dataset(target).data, original.data)
    assert read_dataset(target).to_metadata() == original.to_metadata()


@pytest.mark.parametrize('recipe', [[], [{'op':'window', 'axis':'a0', 'lb_hz':2}]])
def test_preopen_path_replacement_never_truncates_foreign_file(tmp_path, monkeypatch, recipe):
    source, target = tmp_path/'input.nmrh5', tmp_path/'output.nmrh5'
    fixture(source)
    source_bytes = source.read_bytes()
    module = blocks()
    create = module._Transaction.create_path
    marker = b'foreign scratch marker; must survive the writer unchanged'
    replaced, prevented = [], []
    def replace_before_writer(transaction, name):
        path = create(transaction, name)
        foreign = tmp_path/'foreign.nmrh5'
        foreign.write_bytes(marker)
        try:
            os.replace(foreign, path)
        except PermissionError:
            # Windows may prevent replacement while the exclusive owned handle is live.
            prevented.append(foreign)
        else:
            replaced.append(path)
        return path
    monkeypatch.setattr(module._Transaction, 'create_path', replace_before_writer)
    try:
        report = module.process_to_file(source, target, recipe)
    except NMRNextError as error:
        assert error.code == 'TEMPORARY_FILE_CHANGED'
        assert replaced
        assert not target.exists()
    else:
        assert prevented and not replaced
        assert report['cleanup_warnings'] == []
    for path in replaced + prevented:
        assert path.read_bytes() == marker
    assert source.read_bytes() == source_bytes


def test_api_elapsed_includes_transaction_cleanup(tmp_path, monkeypatch):
    source = tmp_path/'input.nmrh5'
    fixture(source)
    module = blocks()
    cleanup = module._Transaction.cleanup
    def delayed_cleanup(transaction):
        time.sleep(.05)
        return cleanup(transaction)
    monkeypatch.setattr(module._Transaction, 'cleanup', delayed_cleanup)
    start = time.perf_counter()
    report = module.process_to_file(source, tmp_path/'output.nmrh5', [])
    outer = time.perf_counter()-start
    assert report['elapsed_seconds'] >= .05
    assert outer-report['elapsed_seconds'] < .025


@pytest.mark.parametrize('fail', [False, True])
def test_owned_writer_descriptors_close_on_success_and_failure(tmp_path, monkeypatch, fail):
    source = tmp_path/'input.nmrh5'
    fixture(source)
    module = blocks()
    captured = []
    create = module._Transaction.create_path
    def capture(transaction, name):
        path = create(transaction, name)
        stream = transaction.handles[path]
        assert isinstance(stream, io.FileIO)
        assert transaction.owned[path] == module._stat_identity(os.fstat(stream.fileno()))
        captured.append(stream)
        return path
    monkeypatch.setattr(module._Transaction, 'create_path', capture)
    if fail:
        def failure(*args, **kwargs):
            raise OSError('injected execution failure')
        monkeypatch.setattr(module._engine, 'execute_stage', failure)
        with pytest.raises(NMRNextError):
            module.process_to_file(source, tmp_path/'output.nmrh5', operations(3))
    else:
        module.process_to_file(source, tmp_path/'output.nmrh5', [])
    assert captured and all(handle.closed for handle in captured)


@pytest.mark.parametrize('recipe', [[], [{'op':'window', 'axis':'a0', 'lb_hz':2}]])
def test_replacement_after_writer_check_cannot_redirect_writes(tmp_path, monkeypatch, recipe):
    source, target = tmp_path/'input.nmrh5', tmp_path/'output.nmrh5'
    fixture(source)
    source_bytes = source.read_bytes()
    module = blocks()
    create, opening = module._Transaction.create_path, module._open
    marker = b'foreign object after pathname check'
    paths, replaced, prevented = [], [], []
    def capture(transaction, name):
        path = create(transaction, name)
        paths.append(path)
        return path
    def replace_then_open(subject, mode='r'):
        if mode == 'w':
            assert isinstance(subject, io.FileIO)
            foreign = tmp_path/'foreign.nmrh5'
            foreign.write_bytes(marker)
            try:
                os.replace(foreign, paths[-1])
            except PermissionError:
                prevented.append(foreign)
            else:
                replaced.append(paths[-1])
        return opening(subject, mode)
    monkeypatch.setattr(module._Transaction, 'create_path', capture)
    monkeypatch.setattr(module, '_open', replace_then_open)
    try:
        module.process_to_file(source, target, recipe)
    except NMRNextError as error:
        assert error.code == 'TEMPORARY_FILE_CHANGED'
        assert replaced and not target.exists()
    else:
        assert prevented and not replaced
    assert all(path.read_bytes() == marker for path in replaced+prevented)
    assert source.read_bytes() == source_bytes


@pytest.mark.parametrize('method', ['flush', 'close'])
def test_owned_writer_flush_and_close_failures_do_not_publish_or_leak_handles(tmp_path, monkeypatch, method):
    source, target = tmp_path/'input.nmrh5', tmp_path/'output.nmrh5'
    fixture(source)
    source_bytes = source.read_bytes()
    module = blocks()
    original, create = getattr(h5py.File, method), module._Transaction.create_path
    captured, injected = [], []
    def capture(transaction, name):
        path = create(transaction, name)
        captured.append(transaction.handles[path])
        return path
    def fail_owned(handle, *args, **kwargs):
        if handle.id.valid and handle.driver == 'fileobj' and not injected:
            injected.append(True)
            if method == 'close':
                original(handle, *args, **kwargs)
            raise OSError(f'injected {method} failure')
        return original(handle, *args, **kwargs)
    monkeypatch.setattr(module._Transaction, 'create_path', capture)
    monkeypatch.setattr(h5py.File, method, fail_owned)
    with pytest.raises(NMRNextError) as error:
        module.process_to_file(source, target, [])
    assert error.value.code == 'BLOCK_IO_FAILURE'
    assert injected and captured and all(handle.closed for handle in captured)
    assert source.read_bytes() == source_bytes
    assert not target.exists()
    assert not list(tmp_path.glob('.spinflow-blocks-*'))
