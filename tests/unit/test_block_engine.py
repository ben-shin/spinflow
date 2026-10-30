"""Complete-axis block execution preserves eager scientific contracts."""
import importlib
import math
import tracemalloc

import h5py
import numpy as np
import pytest

from nmrnext import Axis, Dataset, NMRNextError
from nmrnext.model import DatasetHeader
from nmrnext.operations import _transition, run_operations, validate_backend
import nmrnext.operations as operations_module


def engine():
    try:
        return importlib.import_module("nmrnext._block_engine")
    except ModuleNotFoundError:
        pytest.fail("The bounded complete-axis execution engine is not implemented")


def headers(dataset, operation):
    before = DatasetHeader.from_dataset(dataset) if isinstance(dataset, Dataset) else dataset
    axes, bits, metadata, resolved = _transition(before.axes, before.complex_axes, before.metadata,
                                                 before.provenance, operation)
    entry_builder = getattr(operations_module, "_operation_entry", None)
    assert callable(entry_builder), "The shared scalar scientific provenance entry is not implemented"
    after_shape = (2**len(bits), *(axis.size for axis in axes))
    backend = validate_backend("native", 1)
    entry = entry_builder(before.axes, before.complex_axes, before.shape, after_shape,
                          before.dtype, resolved, backend, output_axes=axes, output_complex_axes=bits)
    after = DatasetHeader(axes, bits, before.dtype, metadata, before.provenance+[entry])
    return before, after, resolved, backend


def make_source(dimensions, dtype=np.float64, *, bits=None):
    sizes = (5, 6, 7, 3)[:dimensions]
    axes = tuple(Axis(f"t{i}", size, step=.001*(i+1), encoding="states" if i < dimensions-1 else "complex")
                 for i, size in enumerate(sizes))
    bits = tuple(axis.id for axis in axes) if bits is None else tuple(bits)
    count = 2**len(bits)*math.prod(sizes)
    samples = (np.arange(count, dtype=np.float64).reshape(2**len(bits), *sizes)-count/3)/17
    return Dataset(samples.astype(dtype), axes, bits,
                   {"opaque": {"keep": [17]}, "sampling": "fully_sampled"}, [{"source": "synthetic"}])


def test_scalar_entry_helper_preserves_full_shapes_and_reference_calibration():
    source = run_operations(make_source(3), [{"op": "fft", "axis": "t1"}], backend="native")
    op = {"op": "reference", "axis": "t1", "reference_ppm": 4.7, "reference_hz": 12,
          "observation_mhz": 500, "spectral_width_hz": 2000, "frequency_scale": -1}
    _, after, _, _ = headers(source, op)
    expected = run_operations(source, [op], backend="native")
    assert after.provenance == expected.provenance
    assert after.provenance[-1]["input_shape"] == [8, 5, 6, 7]
    assert after.provenance[-1]["output_shape"] == [8, 5, 6, 7]
    assert after.provenance[-1]["reference_calibration"]["new_grid"]["spectral_width_hz"] == 2000


def test_plan_refuses_a_complete_line_that_cannot_fit_before_any_payload_selection():
    block = engine()
    before = DatasetHeader((Axis("x", 100003, step=.001), Axis("y", 9999, step=.002)),
                           ("x", "y"), "float64")
    before, after, op, backend = headers(before, {"op": "fft", "axis": "x"})
    with pytest.raises(NMRNextError) as caught:
        block.plan_stage(before, op, after, backend, memory_bytes=1024, cache_bytes=0)
    assert caught.value.code == "MEMORY_BUDGET_EXCEEDED"
    assert caught.value.details["minimum_working_bytes"] > 1024


def test_scalar_plan_keeps_every_component_and_complete_axis_at_exact_minimum(monkeypatch):
    block = engine()
    before, after, op, backend = headers(make_source(4, np.float32), {"op": "fft", "axis": "t1"})
    generous = block.plan_stage(before, op, after, backend, memory_bytes=64*1024**2, cache_bytes=128*1024)
    for name in ("empty", "zeros", "ones", "arange", "frombuffer", "asarray"):
        monkeypatch.setattr(np, name, lambda *a, **k: pytest.fail("Scalar block planning allocated sample arrays"))
    plan = block.plan_stage(before, op, after, backend,
                            memory_bytes=generous["minimum_working_bytes"], cache_bytes=128*1024)
    assert plan["read_block_shape"][0] == 16
    assert plan["read_block_shape"][2] == 6
    assert math.prod(plan["read_block_shape"][1:]) == 6
    assert plan["estimated_working_bytes"] <= plan["memory_bytes"]
    assert plan["external_reserve_bytes"] == 128*1024


class ObservedStore:
    def __init__(self, value):
        self.value = value
        self.shape, self.dtype = value.shape, value.dtype
        self.read_shapes = []

    def __getitem__(self, selection):
        result = self.value[selection]
        self.read_shapes.append(result.shape)
        return result

    def read_direct(self, destination, source_sel=None, dest_sel=None):
        self.read_shapes.append(destination.shape)
        self.value.read_direct(destination, source_sel=source_sel, dest_sel=dest_sel)


@pytest.mark.parametrize("dimensions", [1, 2, 3, 4])
@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_bounded_fft_matches_eager_for_every_physical_axis_and_partial_batches(tmp_path, dimensions, dtype):
    block = engine()
    source = make_source(dimensions, dtype)
    for axis in source.axes:
        before, after, op, backend = headers(source, {"op": "fft", "axis": axis.id})
        generous = block.plan_stage(before, op, after, backend, 64*1024**2, 0)
        budget = generous["minimum_working_bytes"] + generous["per_batch_working_bytes"]*2
        plan = block.plan_stage(before, op, after, backend, budget, 0)
        with h5py.File(tmp_path/f"{axis.id}.h5", "w", rdcc_nbytes=0) as handle:
            store = ObservedStore(handle.create_dataset("source", data=source.data))
            target = handle.create_dataset("target", shape=after.shape, dtype=after.dtype)
            result = block.execute_stage(store, target, before, after, op, plan, backend)
            expected = run_operations(source, [op], backend="native")
            np.testing.assert_array_equal(target[:], expected.data)
        assert store.read_shapes
        assert all(shape[0] == source.data.shape[0] for shape in store.read_shapes)
        position = source.axis_index(axis.id)+1
        assert all(shape[position] == axis.size for shape in store.read_shapes)
        assert max(math.prod(shape)*before.dtype.itemsize for shape in store.read_shapes) <= plan["max_read_bytes"]
        assert result["processed_blocks"] == len(store.read_shapes)
        if dimensions > 1:
            assert result["processed_blocks"] > 1
            assert max(math.prod(shape) for shape in store.read_shapes) < math.prod(source.data.shape)


def test_block_selection_iterator_does_not_materialize_the_number_of_batches():
    block = engine()
    header = DatasetHeader((Axis("x", 100000), Axis("y", 11)), (), "float32")
    tracemalloc.start()
    try:
        iterator = block._selections(header, [1, 11])
        assert next(iterator) == (slice(0, 1), slice(0, 1), slice(0, 11))
        assert next(iterator) == (slice(0, 1), slice(1, 2), slice(0, 11))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 64*1024


def test_scalar_entry_extraction_keeps_public_subclass_axis_annotation_behavior():
    class AnnotatedAxisDataset(Dataset):
        def axis(self, identity):
            from dataclasses import replace
            return replace(super().axis(identity), encoding="states")
    source = AnnotatedAxisDataset(np.arange(8, dtype=np.float64).reshape(2, 4),
                                  (Axis("x", 4, domain="frequency", unit="Hz"),), ("x",))
    result = run_operations(source, [{"op": "discard_imaginary", "axis": "x"}], backend="native")
    assert result.provenance[-1]["discard_imaginary"]["input_axis_encoding"] == "states"


def execute_recipe(handle, source, recipe):
    block = engine()
    store = handle.create_dataset("source", data=source.data)
    before = DatasetHeader.from_dataset(source)
    for index, operation in enumerate(recipe):
        before, after, op, backend = headers(before, operation)
        large = block.plan_stage(before, op, after, backend, 64*1024**2, 0)
        plan = block.plan_stage(before, op, after, backend,
                                large["minimum_working_bytes"]+2*large["per_batch_working_bytes"], 0)
        output = handle.create_dataset(f"step{index}", shape=after.shape, dtype=after.dtype)
        stats = block.execute_stage(store, output, before, after, op, plan, backend)
        assert stats["read_values"] == math.prod(before.shape)
        assert stats["written_values"] == math.prod(after.shape)
        assert stats["processed_blocks"] == plan["block_count"]
        expected = run_operations(source, recipe[:index+1], backend="native")
        np.testing.assert_array_equal(output[:], expected.data)
        assert after.axes == expected.axes
        assert after.complex_axes == expected.complex_axes
        assert after.metadata == expected.metadata
        assert after.provenance == expected.provenance
        store, before = output, after
    return store[:], before


@pytest.mark.parametrize("dimensions", [3, 4])
@pytest.mark.parametrize("dtype", [np.float32, np.float64])
@pytest.mark.parametrize("partial_bits", [False, True])
def test_all_typed_block_operations_preserve_permuted_bits_global_phase_and_full_history(tmp_path, dimensions, dtype, partial_bits):
    bits = (f"t{dimensions-1}", "t0") if partial_bits else tuple(f"t{i}" for i in reversed(range(dimensions)))
    source = make_source(dimensions, dtype, bits=bits)
    direct = f"t{dimensions-1}"
    recipe = [
        {"op": "crop", "axis": "t0", "start": 1, "stop": 5},
        {"op": "window", "axis": direct, "kind": "sine", "power": 1.5, "off": .35, "end": .95, "first_point_scale": .5},
        {"op": "zero_fill", "axis": direct, "size": 9},
        {"op": "phase", "axis": "t0", "p0": 23, "p1": -17, "pivot": 1.25},
        {"op": "fft", "axis": direct},
        {"op": "phase", "axis": direct, "p0": -13, "p1": 29, "pivot": 4.5},
        {"op": "reference", "axis": direct, "reference_ppm": 4.7, "reference_hz": 17,
         "observation_mhz": 500, "spectral_width_hz": 2000, "frequency_scale": -1},
        {"op": "reverse", "axis": direct},
        {"op": "crop", "axis": direct, "start": 1, "stop": 8},
        {"op": "discard_imaginary", "axis": direct},
        {"op": "transpose", "order": [axis.id for axis in reversed(source.axes)]},
        {"op": "window", "axis": "t0", "kind": "exponential", "lb_hz": 3},
        {"op": "fft", "axis": "t0"},
        {"op": "fft", "axis": "t0", "inverse": True},
    ]
    with h5py.File(tmp_path/"all-ops.h5", "w", rdcc_nbytes=0) as handle:
        execute_recipe(handle, source, recipe)


@pytest.mark.parametrize("dimensions", [3, 4])
def test_signed_separable_fourier_modes_have_analytic_centered_peaks_in_every_dimension(tmp_path, dimensions):
    sizes = (3, 4, 5, 7)[:dimensions]
    axes = tuple(Axis(f"t{i}", size, step=.001*(i+1), encoding="states") for i, size in enumerate(sizes))
    samples = np.zeros((2**dimensions, *sizes), dtype=np.float64)
    peaks = [(1., [1]*dimensions), (-.2, [-1]*dimensions)]
    for amplitude, modes in peaks:
        for channel in range(2**dimensions):
            term = np.asarray(amplitude)
            for index, (size, mode) in enumerate(zip(sizes, modes)):
                angles = 2*np.pi*mode*np.arange(size)/size
                wave = np.sin(angles) if channel & (1 << index) else np.cos(angles)
                shape = [1]*dimensions; shape[index] = size
                term = term*wave.reshape(shape)
            samples[channel] += term
    source = Dataset(samples, axes, tuple(axis.id for axis in axes))
    recipe = [{"op": "fft", "axis": axis.id} for axis in axes]
    with h5py.File(tmp_path/"analytic.h5", "w", rdcc_nbytes=0) as handle:
        actual, _ = execute_recipe(handle, source, recipe)
    expected = np.zeros_like(samples)
    for amplitude, modes in peaks:
        location = tuple((size//2+mode) % size for size, mode in zip(sizes, modes))
        expected[(0, *location)] += amplitude*math.prod(sizes)
    np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-12)


@pytest.mark.parametrize("operation", [{"op": "crop", "axis": "t0", "start": 0, "stop": 1},
                                       {"op": "discard_imaginary", "axis": "t0"}])
def test_nonfinite_source_cannot_be_hidden_by_crop_or_component_discard(tmp_path, operation):
    block = engine()
    source = make_source(2, np.float32)
    if operation["op"] == "discard_imaginary":
        source = run_operations(source, [{"op": "fft", "axis": "t0"}], backend="native")
    before, after, op, backend = headers(source, operation)
    plan = block.plan_stage(before, op, after, backend, 8*1024**2, 0)
    bad = source.data.copy(); bad[-1, -1, -1] = np.nan
    with h5py.File(tmp_path/"bad.h5", "w", rdcc_nbytes=0) as handle:
        origin = handle.create_dataset("source", data=bad)
        target = handle.create_dataset("target", shape=after.shape, dtype=after.dtype)
        with pytest.raises(NMRNextError) as caught:
            block.execute_stage(origin, target, before, after, op, plan, backend)
        assert caught.value.code == "NONFINITE_DATA"


def test_operation_overflow_fails_before_any_later_discard_can_hide_it(tmp_path):
    block = engine()
    source = Dataset(np.array([[3e38, 3e38], [0., 0.]], dtype=np.float32),
                     (Axis("x", 2, step=.001),), ("x",))
    before, after, op, backend = headers(source, {"op": "fft", "axis": "x"})
    plan = block.plan_stage(before, op, after, backend, 8*1024**2, 0)
    with h5py.File(tmp_path/"overflow.h5", "w", rdcc_nbytes=0) as handle:
        origin = handle.create_dataset("source", data=source.data)
        target = handle.create_dataset("target", shape=after.shape, dtype=after.dtype)
        with pytest.raises(NMRNextError) as caught:
            block.execute_stage(origin, target, before, after, op, plan, backend)
        assert caught.value.code == "NUMERICAL_OVERFLOW"
        np.testing.assert_array_equal(origin[:], source.data)


@pytest.mark.parametrize("alteration", ["geometry", "source_shape", "target_dtype"])
def test_execution_rechecks_plan_and_storage_before_selecting_payload(tmp_path, alteration):
    block = engine()
    source = make_source(2)
    before, after, op, backend = headers(source, {"op": "fft", "axis": "t0"})
    plan = block.plan_stage(before, op, after, backend, 8*1024**2, 0)
    with h5py.File(tmp_path/"changed.h5", "w", rdcc_nbytes=0) as handle:
        shape = (before.shape[0], before.shape[1], before.shape[2]+1) if alteration == "source_shape" else before.shape
        origin = ObservedStore(handle.create_dataset("source", shape=shape, dtype=before.dtype))
        target = handle.create_dataset("target", shape=after.shape, dtype="float32" if alteration == "target_dtype" else after.dtype)
        if alteration == "geometry":
            plan["read_block_shape"][-1] += 1
        with pytest.raises(NMRNextError):
            block.execute_stage(origin, target, before, after, op, plan, backend)
        assert origin.read_shapes == []


@pytest.mark.parametrize("backend,threads", [("numpy", 1), ("native", 2)])
def test_unaccounted_backend_or_thread_policies_are_explicitly_rejected(backend, threads):
    block = engine()
    before, after, op, _ = headers(make_source(3), {"op": "fft", "axis": "t0"})
    with pytest.raises(NMRNextError) as caught:
        block.plan_stage(before, op, after, validate_backend(backend, threads), 64*1024**2, 0)
    assert caught.value.code == "UNSUPPORTED_BLOCK_BACKEND"
