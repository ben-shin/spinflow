"""Replay equivalence and memory limits for private interactive caches."""
import gc
import hashlib
import importlib.util
import json
import weakref

import numpy as np
import pytest

from nmrnext.analysis import dataset_digest
from nmrnext.errors import NMRNextError
from nmrnext.model import Axis, Dataset
from nmrnext.operations import quadrature_index_orientation, run_operations
from nmrnext.planning import plan_operations


def cache_type():
    assert importlib.util.find_spec('nmrnext._execution_cache') is not None, 'prefix reuse is not implemented'
    from nmrnext._execution_cache import RecipeCache
    return RecipeCache


def identity_type():
    assert importlib.util.find_spec('nmrnext._identity') is not None, 'bounded identity memoization is not implemented'
    from nmrnext._identity import NumericIdentityCache
    return NumericIdentityCache


def source(dtype=np.float64):
    return Dataset(np.arange(64, dtype=dtype).reshape(2, 32) / 100,
                   [Axis('h', 32, step=.001)], ['h'],
                   {'nested': {'input': 3}}, [{'source': 'test'}])


def recipe(p0=20):
    return [{'op': 'window', 'axis': 'h', 'lb_hz': 2},
            {'op': 'zero_fill', 'axis': 'h', 'size': 64},
            {'op': 'fft', 'axis': 'h'},
            {'op': 'phase', 'axis': 'h', 'p0': p0, 'p1': 3, 'pivot': 4}]


def assert_replay(actual, expected):
    np.testing.assert_array_equal(actual.data, expected.data)
    assert actual.to_metadata() == expected.to_metadata()
    assert not actual.data.flags.writeable


@pytest.mark.parametrize('dtype', [np.float32, np.float64])
def test_exact_repeat_and_changed_last_phase_reuse_preserve_complete_replay(dtype):
    cache = cache_type()(capacity_bytes=128 * 1024)
    original = source(dtype)
    first, plan = cache.run(original, recipe(), memory_bytes=8 * 1024**2)
    again, _ = cache.run(original, recipe(), memory_bytes=8 * 1024**2)
    assert again is first
    assert cache.last_reused_steps == 4
    changed, _ = cache.run(original, recipe(41), memory_bytes=8 * 1024**2)
    assert cache.last_reused_steps == 3
    assert_replay(changed, run_operations(original, recipe(41), memory_bytes=8 * 1024**2))
    assert len(changed.provenance) == 5
    assert cache.retained_bytes + plan['estimated_working_bytes'] <= 8 * 1024**2


def test_invalid_suffix_and_lower_memory_are_checked_before_exact_cache_hit():
    cache = cache_type()(capacity_bytes=128 * 1024)
    original = source()
    cache.run(original, recipe(), memory_bytes=8 * 1024**2)
    invalid = recipe() + [{'op': 'crop', 'axis': 'missing', 'start': 0, 'stop': 2}]
    with pytest.raises(NMRNextError) as error:
        cache.run(original, invalid, memory_bytes=8 * 1024**2)
    assert error.value.code == 'UNKNOWN_AXIS'
    with pytest.raises(NMRNextError) as error:
        cache.run(original, recipe(), memory_bytes=1)
    assert error.value.code == 'MEMORY_BUDGET_EXCEEDED'


def test_source_replacement_prefix_edit_backend_and_memory_policy_do_not_hit():
    cache = cache_type()(capacity_bytes=128 * 1024)
    original = source()
    cache.run(original, recipe(), memory_bytes=8 * 1024**2)
    changed_prefix = recipe()
    changed_prefix[0]['lb_hz'] = 7
    output, _ = cache.run(original, changed_prefix, memory_bytes=8 * 1024**2)
    assert cache.last_reused_steps == 0
    assert_replay(output, run_operations(original, changed_prefix))
    output, _ = cache.run(source(), changed_prefix, memory_bytes=8 * 1024**2)
    assert cache.last_reused_steps == 0
    output, _ = cache.run(original, recipe(), backend='native', memory_bytes=8 * 1024**2)
    assert cache.last_reused_steps == 0
    assert_replay(output, run_operations(original, recipe(), backend='native'))
    cache.run(original, recipe(), backend='native', memory_bytes=9 * 1024**2)
    assert cache.last_reused_steps == 0
    with pytest.raises(NMRNextError) as error:
        cache.run(original, recipe(), backend='numpy', threads=2, memory_bytes=8 * 1024**2)
    assert error.value.code == 'UNSUPPORTED_THREADS'


def test_zero_capacity_and_no_headroom_use_uncached_reference_execution():
    original = source()
    cache = cache_type()(capacity_bytes=0)
    first, plan = cache.run(original, recipe())
    second, _ = cache.run(original, recipe())
    assert first is not second
    assert cache.entry_count == 0
    assert cache.retained_bytes == 0
    cache.capacity_bytes = 128 * 1024
    output, _ = cache.run(original, recipe(), memory_bytes=plan['estimated_working_bytes'])
    assert cache.entry_count == 0
    assert cache.last_reused_steps == 0
    assert_replay(output, first)


def test_capacity_reduction_evicts_and_shared_reference_owner_is_counted_once():
    cache = cache_type()(capacity_bytes=1024 * 1024)
    original = Dataset(np.ones((2, 32768)),
                       [Axis('h', 32768, domain='frequency', unit='Hz')], ['h'])
    reference_ops = [{'op': 'reference', 'axis': 'h', 'reference_ppm': 4.7,
                      'reference_hz': 0, 'observation_mhz': 500}]
    cache.run(original, reference_ops * 2)
    assert 0 < cache.retained_bytes <= cache.capacity_bytes
    assert cache.entry_count == 2
    assert cache.retained_bytes < 2 * original.data.nbytes
    cache.capacity_bytes = 1
    assert cache.entry_count == 0
    assert cache.retained_bytes == 0
    cache.capacity_bytes = 128 * 1024
    cache.run(source(), recipe())
    cache.clear()
    assert cache.entry_count == cache.retained_bytes == cache.last_reused_steps == 0


def test_failed_numerical_suffix_does_not_publish_any_new_checkpoint():
    cache = cache_type()(capacity_bytes=128 * 1024)
    original = Dataset(np.full((2, 32), np.finfo(np.float64).max / 4),
                       [Axis('h', 32, step=.001)], ['h'])
    safe = [{'op': 'window', 'axis': 'h', 'lb_hz': 2}]
    first, _ = cache.run(original, safe)
    before = cache.entry_count
    with pytest.raises(NMRNextError) as error:
        cache.run(original, safe + [{'op': 'fft', 'axis': 'h'}])
    assert error.value.code == 'NUMERICAL_OVERFLOW'
    assert cache.entry_count == before
    again, _ = cache.run(original, safe)
    assert again is first


def test_mutated_public_metadata_and_recipe_do_not_poison_cache_or_source_lifetime():
    cache = cache_type()(capacity_bytes=128 * 1024)
    original = source()
    ops = recipe()
    first, _ = cache.run(original, ops)
    first.metadata['nested']['input'] = 999
    first.provenance.clear()
    ops[-1]['p0'] = 47
    output, _ = cache.run(original, ops)
    assert_replay(output, run_operations(original, ops))
    reference = weakref.ref(original)
    del original
    gc.collect()
    assert reference() is None
    assert cache.entry_count == 0


def legacy_digest(dataset):
    header = {'axes': [a.to_dict() for a in dataset.axes], 'complex_axes': list(dataset.complex_axes),
              'dtype': dataset.data.dtype.str, 'shape': list(dataset.data.shape)}
    orientation = quadrature_index_orientation(dataset)
    overrides = {a.id: orientation[a.id] for a in dataset.axes if a.id in orientation
                 and orientation[a.id] != (1 if a.step > 0 else -1)}
    if overrides:
        header['quadrature_index_orientation'] = overrides
    result = hashlib.sha256(json.dumps(header, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
    result.update(memoryview(dataset.data).cast('B'))
    return result.hexdigest()


def test_identity_is_exact_legacy_bytes_with_orientation_and_no_metadata_identity_change():
    identity = identity_type()(capacity_bytes=32 * 1024)
    original = run_operations(source(), recipe())
    flipped = run_operations(original, [{'op': 'reference', 'axis': 'h', 'frequency_scale': -1,
                                        'reference_ppm': 4.7, 'reference_hz': 0, 'observation_mhz': 500}])
    for dataset in (original, flipped):
        assert identity.digest(dataset) == legacy_digest(dataset) == dataset_digest(dataset)
    same = Dataset(original.data, original.axes, original.complex_axes,
                   {**original.metadata, 'irrelevant': 4}, original.provenance)
    assert identity.digest(same) == identity.digest(original)
    assert identity.entry_count == 3
    assert identity.retained_bytes <= identity.capacity_bytes


def test_identity_memoization_does_not_rescan_samples_and_releases_dead_owners(monkeypatch):
    identity = identity_type()(capacity_bytes=32 * 1024)
    original = source()
    expected = legacy_digest(original)
    assert identity.digest(original) == expected
    def fail_hash(*args, **kwargs):
        raise AssertionError('a repeated immutable digest rescanned the payload')
    monkeypatch.setattr(hashlib, 'sha256', fail_hash)
    assert identity.digest(original) == expected
    reference = weakref.ref(original)
    del original
    gc.collect()
    assert reference() is None
    assert identity.entry_count == identity.retained_bytes == 0


def test_identity_capacity_eviction_and_zero_capacity_never_return_another_owner_digest():
    identity = identity_type()(capacity_bytes=1800)
    for index in range(30):
        dataset = source()
        changed = Dataset(dataset.data + index, dataset.axes, dataset.complex_axes)
        assert identity.digest(changed) == legacy_digest(changed)
        assert identity.retained_bytes <= identity.capacity_bytes
    identity.capacity_bytes = 0
    assert identity.entry_count == identity.retained_bytes == 0
    assert identity.digest(source()) == legacy_digest(source())
    with pytest.raises(NMRNextError):
        identity.digest(object())


class UnhashableDataset(Dataset):
    __hash__ = None


class EqualDataset(Dataset):
    def __eq__(self, other):
        return isinstance(other, EqualDataset)

    def __hash__(self):
        return 1


@pytest.mark.parametrize('dataset_type', [UnhashableDataset, EqualDataset])
def test_dataset_subclass_digests_do_not_require_hashability_or_conflate_equal_objects(dataset_type):
    identity = identity_type()()
    first = dataset_type(np.ones((1, 4)), [Axis('h', 4)])
    second = dataset_type(np.full((1, 4), 3.), [Axis('h', 4)])
    assert identity.digest(first) == legacy_digest(first)
    assert identity.digest(second) == legacy_digest(second)
    assert identity.digest(first) != identity.digest(second)
    assert identity.entry_count == 0
    assert dataset_digest(first) != dataset_digest(second)


def test_mutable_dataset_subclass_never_reuses_digest_or_recipe_and_still_refuses_nan():
    class LiveDataset(Dataset):
        @property
        def data(self):
            return self.samples
    dataset = LiveDataset(np.ones((2, 4)), [Axis('h', 4)], ['h'])
    dataset.samples = np.ones((2, 4))
    cache = cache_type()()
    identity = identity_type()()
    operations = [{'op': 'window', 'axis': 'h', 'lb_hz': 0}]
    initial, _ = cache.run(dataset, operations)
    before = identity.digest(dataset)
    dataset.samples[:] = 3
    result, _ = cache.run(dataset, operations)
    np.testing.assert_array_equal(initial.data, np.ones((2, 4)))
    np.testing.assert_array_equal(result.data, np.full((2, 4), 3.))
    assert_replay(result, run_operations(dataset, operations))
    assert identity.digest(dataset) != before
    assert cache.entry_count == cache.last_reused_steps == identity.entry_count == 0
    dataset.samples[:] = np.nan
    with pytest.raises(NMRNextError) as reference_error:
        run_operations(dataset, operations)
    with pytest.raises(NMRNextError) as cached_error:
        cache.run(dataset, operations)
    assert cached_error.value.code == reference_error.value.code == 'NUMERICAL_OVERFLOW'


def test_stable_dataset_data_override_uses_its_public_samples_without_caching():
    class ScaledDataset(Dataset):
        @property
        def data(self):
            return super().data * 2
    dataset = ScaledDataset(np.ones((2, 4)), [Axis('h', 4)], ['h'])
    cache = cache_type()()
    result, _ = cache.run(dataset, [{'op': 'window', 'axis': 'h', 'lb_hz': 0}])
    np.testing.assert_array_equal(result.data, np.full((2, 4), 2.))
    assert cache.entry_count == 0


def test_mutable_axis_subclass_changes_are_visible_to_recipe_and_digest():
    class MutableAxis(Axis):
        @property
        def step(self):
            return self.step_values[0]

        @step.setter
        def step(self, value):
            object.__setattr__(self, 'step_values', [value])
    axis = MutableAxis('h', 4, step=.001)
    dataset = Dataset(np.ones((2, 4)), [axis], ['h'])
    cache = cache_type()()
    identity = identity_type()()
    operations = [{'op': 'window', 'axis': 'h', 'lb_hz': 20}]
    first, _ = cache.run(dataset, operations)
    before = identity.digest(dataset)
    axis.step_values[0] = .002
    result, _ = cache.run(dataset, operations)
    assert result.data[0, -1] != first.data[0, -1]
    assert_replay(result, run_operations(dataset, operations))
    assert identity.digest(dataset) == legacy_digest(dataset) != before
    assert cache.entry_count == cache.last_reused_steps == identity.entry_count == 0


def test_one_checkpoint_capacity_retains_predecessor_for_all_seven_phase_edits():
    dataset = Dataset(np.arange(8192, dtype=np.float64).reshape(2, 4096) / 100,
                      [Axis('h', 4096, step=.001)], ['h'])
    cache = cache_type()(capacity_bytes=100_000)
    operations = [{'op': 'window', 'axis': 'h', 'lb_hz': 2},
                  {'op': 'fft', 'axis': 'h'},
                  {'op': 'phase', 'axis': 'h', 'p0': 0}]
    result, plan = cache.run(dataset, operations, memory_bytes=8 * 1024**2)
    assert cache.entry_count == 1
    assert_replay(result, run_operations(dataset, operations))
    for phase in range(1, 8):
        operations[-1]['p0'] = phase
        result, _ = cache.run(dataset, operations, memory_bytes=8 * 1024**2)
        assert cache.last_reused_steps == 2
        assert_replay(result, run_operations(dataset, operations))
        assert cache.entry_count == 1
        assert cache.retained_bytes <= 100_000
        assert cache.retained_bytes + plan['estimated_working_bytes'] <= 8 * 1024**2
    # Identical requests still agree when their final cannot also be retained.
    again, _ = cache.run(dataset, operations, memory_bytes=8 * 1024**2)
    assert cache.last_reused_steps == 2
    assert_replay(again, result)
    operations[0]['lb_hz'] = 4
    cache.run(dataset, operations, memory_bytes=8 * 1024**2)
    assert cache.last_reused_steps == 0


def test_two_checkpoint_capacity_preserves_final_hit_and_failed_suffix_keeps_predecessor():
    dataset = Dataset(np.ones((2, 4096)), [Axis('h', 4096)], ['h'])
    cache = cache_type()(capacity_bytes=200_000)
    operations = [{'op': 'window', 'axis': 'h', 'lb_hz': 0},
                  {'op': 'fft', 'axis': 'h'},
                  {'op': 'phase', 'axis': 'h', 'p0': 0}]
    first, _ = cache.run(dataset, operations)
    repeated, _ = cache.run(dataset, operations)
    assert repeated is first
    assert cache.last_reused_steps == 3
    assert cache.entry_count == 2
    assert cache.retained_bytes <= cache.capacity_bytes

    cache.capacity_bytes = 100_000
    large = Dataset(np.full((2, 4096), np.finfo(np.float64).max * .8),
                    [Axis('h', 4096, domain='frequency', unit='Hz')], ['h'])
    phase_ops = [{'op': 'reference', 'axis': 'h', 'reference_ppm': 4.7,
                  'reference_hz': 0, 'observation_mhz': 500},
                 {'op': 'phase', 'axis': 'h', 'p0': 0}]
    cache.run(large, phase_ops)
    phase_ops[-1]['p0'] = 45
    with pytest.raises(NMRNextError) as error:
        cache.run(large, phase_ops)
    assert error.value.code == 'NUMERICAL_OVERFLOW'
    phase_ops[-1]['p0'] = 0
    result, _ = cache.run(large, phase_ops)
    assert cache.last_reused_steps == 1
    assert_replay(result, run_operations(large, phase_ops))
    assert cache.entry_count == 1
    assert cache.retained_bytes <= cache.capacity_bytes
