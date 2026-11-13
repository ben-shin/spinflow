import hashlib
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

from nmrnext.io import read_dataset, write_dataset
from nmrnext.errors import NMRNextError
from nmrnext.model import Axis, Dataset


def _source(tmp_path, shape):
    axes = tuple(Axis(f'a{i}', n, domain='frequency', unit='Hz', start=-n*10.,
                      step=20., observation_mhz=500., reference_ppm=4.7,
                      label='H1', encoding='real') for i, n in enumerate(shape))
    words = np.arange(np.prod(shape), dtype=np.float32).reshape((1, *shape))
    words.flat[0] = np.float32(-0.0)
    source = Dataset(words, axes)
    path = tmp_path / ('source.ft1' if len(shape) == 1 else 'source.ft2')
    write_dataset(source, path, format='nmrpipe-real')
    return path


def test_real_reader_hashes_same_bytes_without_second_file_scan(tmp_path):
    import nmrnext
    import nmrnext.io as io_module
    import nmrnext.model as model_module
    import import_origin_support as origin_helper
    origin_helper.assert_package_origin(nmrnext.__file__, origin_helper.__file__)
    package = Path(nmrnext.__file__).resolve().parent
    assert Path(io_module.__file__).resolve() == package / 'io/__init__.py'
    assert Path(model_module.__file__).resolve() == package / 'model.py'
    path = _source(tmp_path, (8, 16))
    original = read_dataset(path, format='nmrpipe-real')
    full_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    assert original.metadata['import']['source_sha256'] == full_hash
    # The eligible path must use a digest of its bounded bytes owner; a separate
    # path hash after an independent reader may race or rescan the full file.
    with mock.patch('nmrnext.io._hash_file', side_effect=AssertionError('second file scan')):
        result = read_dataset(path, format='nmrpipe-real')
    assert result.metadata['import']['source_sha256'] == full_hash
    assert result.to_metadata() == original.to_metadata()
    assert result.data.tobytes() == original.data.tobytes()
    assert result.data.flags.writeable is False
    for alias in (result.data, result.data.base):
        if isinstance(alias, np.ndarray):
            try:
                alias.setflags(write=True)
            except ValueError:
                pass
            else:
                raise AssertionError('mutable dataset alias')
    base = result.data.base
    while isinstance(base, np.ndarray):
        assert base.flags.writeable is False
        base = base.base
    assert isinstance(base, memoryview) and base.readonly and type(base.obj) is bytes


def test_public_constructor_still_copies_mutable_input(tmp_path):
    path = _source(tmp_path, (16,))
    result = read_dataset(path, format='nmrpipe-real')
    source = result.data.copy()
    copy = Dataset(source, result.axes, result.complex_axes, result.metadata, result.provenance)
    before = copy.data.tobytes()
    source[0, 0] = np.float32(1000)
    assert copy.data.tobytes() == before


def test_changed_file_after_header_inspection_refuses_before_payload(tmp_path):
    from nmrnext import io
    path = _source(tmp_path, (8, 16))
    original = io.inspect_dataset

    def grow_after_inspection(*args, **kwargs):
        header = original(*args, **kwargs)
        with path.open('ab') as stream:
            stream.write(b'x' * 4096)
        return header

    with mock.patch('nmrnext.io.inspect_dataset', side_effect=grow_after_inspection):
        with pytest.raises(NMRNextError) as caught:
            read_dataset(path, format='nmrpipe-real')
    assert caught.value.code == 'INPUT_CHANGED'


def test_nonfinite_and_unsupported_budget_keep_public_contract(tmp_path):
    path = _source(tmp_path, (8, 16))
    # A small public budget still permits the reader's original 512-byte array;
    # it only makes the private higher-memory route ineligible.
    original = read_dataset(path, format='nmrpipe-real', max_bytes=512)
    assert original.data.shape == (1, 8, 16)
    with path.open('r+b') as stream:
        stream.seek(2048)
        stream.write(np.float32(float('nan')).tobytes())
    with pytest.raises(NMRNextError) as caught:
        read_dataset(path, format='nmrpipe-real')
    assert caught.value.code == 'NONFINITE_DATA'


def test_eligible_file_reads_have_exact_finite_caps(tmp_path):
    from nmrnext import io
    path = _source(tmp_path, (8, 16))
    actual_open = io.os.fdopen
    sizes = []

    class TracedFile:
        def __init__(self, inner):
            self.inner = inner

        def __enter__(self):
            self.inner.__enter__()
            return self

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

        def read(self, count):
            sizes.append(count)
            assert isinstance(count, int) and 0 <= count <= path.stat().st_size
            return self.inner.read(count)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    with mock.patch('nmrnext.io.os.fdopen', side_effect=lambda *a, **k: TracedFile(actual_open(*a, **k))):
        read_dataset(path, format='nmrpipe-real')
    assert sizes == [2048, path.stat().st_size, 1]


def test_header_race_cannot_reach_filename_fallback(tmp_path):
    from nmrnext import io
    path = _source(tmp_path, (8, 16))
    native = path.read_bytes()
    changed = np.frombuffer(native, dtype='<u4').byteswap().tobytes() + b'\0' * 64
    actual_fdopen = io.os.fdopen
    ng = io._nmrglue()
    actual_read = ng.pipe.read
    filename_calls = []
    closed = []

    class ChangeOnHeaderRead:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): self.stream.__enter__(); return self
        def __exit__(self, *args):
            result = self.stream.__exit__(*args)
            closed.append(self.stream.closed)
            return result
        def __getattr__(self, name): return getattr(self.stream, name)
        def read(self, count):
            if count == 2048:
                path.write_bytes(changed)
            return self.stream.read(count)

    def deny_filename(arg, *args, **kwargs):
        if isinstance(arg, str):
            filename_calls.append(arg)
            raise AssertionError('filename fallback after race')
        return actual_read(arg, *args, **kwargs)

    with mock.patch('nmrnext.io.os.fdopen', side_effect=lambda *a, **k: ChangeOnHeaderRead(actual_fdopen(*a, **k))), \
         mock.patch.object(ng.pipe, 'read', side_effect=deny_filename):
        with pytest.raises(NMRNextError) as caught:
            read_dataset(path, format='nmrpipe-real')
    assert caught.value.code == 'INPUT_CHANGED'
    assert filename_calls == []
    assert closed == [True]
