"""Public immutability and privately owned recipe-buffer contracts."""
from contextlib import contextmanager
import sys
import warnings
import weakref

import numpy as np
import pytest

from nmrnext import Axis, Dataset, NMRNextError
from nmrnext.operations import apply_operation, run_operations


def source(dtype=np.float64):
    return Dataset(np.arange(32, dtype=dtype).reshape(4, 2, 4),
                   (Axis("a", 2, encoding="states"), Axis("b", 4)), ("a", "b"),
                   {"opaque": {"field": [17]}}, [{"source": "synthetic"}])


@contextmanager
def numeric_boundary_counts():
    """Observe real full-array checks and byte freezes, without replacing results."""
    counts = {"finite": 0, "freeze": 0}
    original = np.isfinite
    prior_profile = sys.getprofile()
    def finite(value, *args, **kwargs):
        counts["finite"] += 1
        return original(value, *args, **kwargs)
    def profile(frame, event, function):
        if event == "c_call" and getattr(function, "__name__", None) == "tobytes":
            if isinstance(getattr(function, "__self__", None), np.ndarray):
                counts["freeze"] += 1
    np.isfinite = finite
    sys.setprofile(profile)
    try:
        yield counts
    finally:
        sys.setprofile(prior_profile)
        np.isfinite = original


@pytest.mark.parametrize("attack", ["shape", "dtype", "base_shape", "base_dtype"])
def test_public_array_metadata_cannot_corrupt_dataset_or_shared_reference(attack):
    original = source()
    frequency = apply_operation(original, {"op": "fft", "axis": "b"})
    referenced = apply_operation(frequency, {"op": "reference", "axis": "b",
                                           "reference_ppm": 4.7, "reference_hz": 0,
                                           "observation_mhz": 500})
    expected = frequency.data.copy()
    exposed = frequency.data
    target = exposed.base if attack.startswith("base_") else exposed
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        if attack.endswith("shape"):
            target.shape = (target.size,)
        else:
            target.dtype = np.float32
    for snapshot in (frequency, referenced):
        assert snapshot.data.shape == (4, 2, 4)
        assert snapshot.data.dtype == np.dtype("float64")
        np.testing.assert_array_equal(snapshot.data, expected)
        assert snapshot.data.flags.c_contiguous
    assert frequency.axis("b").reference_ppm is None
    assert referenced.axis("b").reference_ppm == 4.7


def test_every_public_array_base_rejects_writeability():
    original = source()
    exposed = original.data
    expected = exposed.copy()
    while isinstance(exposed, np.ndarray):
        with pytest.raises(ValueError):
            exposed.setflags(write=True)
        exposed = exposed.base
    assert isinstance(exposed, bytes)
    np.testing.assert_array_equal(original.data, expected)


def test_metadata_only_reference_shares_immutable_samples_without_scans_or_freezes():
    original = apply_operation(source(), {"op": "fft", "axis": "b"})
    history = original.provenance
    with numeric_boundary_counts() as counts:
        result = run_operations(original, [
            {"op": "reference", "axis": "b", "reference_ppm": 4.7,
             "reference_hz": 0, "observation_mhz": 500},
            {"op": "reference", "axis": "b", "reference_ppm": 5.2,
             "reference_hz": 12, "observation_mhz": 600},
        ])
    assert counts == {"finite": 0, "freeze": 0}
    assert np.shares_memory(original.data, result.data)
    np.testing.assert_array_equal(result.data, original.data)
    assert original.provenance == history
    assert len(result.provenance) == len(history) + 2
    assert result.metadata == original.metadata
    assert result.axis("b").reference_ppm == 5.2
    exposed = result.metadata
    exposed["opaque"]["field"].append(99)
    assert original.metadata["opaque"]["field"] == [17]
    assert result.metadata["opaque"]["field"] == [17]


def test_private_three_step_execution_scans_each_numeric_output_once_and_freezes_once():
    original = source()
    with numeric_boundary_counts() as counts:
        result = run_operations(original, [
            {"op": "window", "axis": "b", "kind": "exponential", "lb_hz": 1},
            {"op": "fft", "axis": "b"}, {"op": "phase", "axis": "b", "p0": 17},
        ])
    assert counts == {"finite": 3, "freeze": 1}
    assert not result.data.flags.writeable
    np.testing.assert_array_equal(original.data, np.arange(32).reshape(4, 2, 4))


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
@pytest.mark.parametrize("backend", ["numpy", "native"])
def test_private_recipe_matches_public_boundaries_for_hypercomplex_views_and_rounding(dtype, backend):
    original = source(dtype)
    operations = [
        {"op": "crop", "axis": "b", "start": 1, "stop": 4},
        {"op": "reverse", "axis": "b"},
        {"op": "transpose", "order": ["b", "a"]},
        {"op": "window", "axis": "a", "kind": "exponential", "lb_hz": 0.1},
        {"op": "zero_fill", "axis": "a", "size": 4},
        {"op": "phase", "axis": "a", "p0": 23, "p1": -17, "pivot": 1},
        {"op": "fft", "axis": "a"},
        {"op": "phase", "axis": "a", "p0": -9, "p1": 11, "pivot": 2},
        {"op": "discard_imaginary", "axis": "a"},
        {"op": "reference", "axis": "a", "reference_ppm": 4.7,
         "reference_hz": 0, "observation_mhz": 500},
    ]
    expected = original
    snapshots = []
    for operation in operations:
        expected = apply_operation(expected, operation, backend=backend)
        snapshots.append((expected, expected.data.copy(), expected.to_metadata()))
    actual = run_operations(original, operations, backend=backend)
    np.testing.assert_array_equal(actual.data, expected.data)
    assert actual.to_metadata() == expected.to_metadata()
    for snapshot, samples, record in snapshots:
        np.testing.assert_array_equal(snapshot.data, samples)
        assert snapshot.to_metadata() == record
    assert weakref.ref(actual)() is actual


def test_selected_prefix_snapshots_and_resumed_suffix_are_immutable_and_equivalent():
    from nmrnext.operations import _run_plan
    from nmrnext.planning import plan_operations

    original = source(np.float32)
    operations = [
        {"op": "window", "axis": "b", "kind": "sine", "power": 2},
        {"op": "zero_fill", "axis": "b", "size": 8},
        {"op": "fft", "axis": "b"},
        {"op": "phase", "axis": "b", "p0": 37, "p1": -19},
    ]
    plan = plan_operations(original, operations)
    snapshots = {}
    frozen = {}
    def retain(index, result):
        assert isinstance(result, Dataset)
        assert not result.data.flags.writeable
        snapshots[index] = result
        frozen[index] = (result.data.copy(), result.to_metadata())
    actual = _run_plan(original, plan, on_step=retain, snapshot_steps={1, 3})
    assert set(snapshots) == {1, 3}
    resumed = _run_plan(snapshots[1], plan, start=2)
    expected = run_operations(original, operations)
    np.testing.assert_array_equal(actual.data, expected.data)
    np.testing.assert_array_equal(resumed.data, expected.data)
    assert actual.to_metadata() == resumed.to_metadata() == expected.to_metadata()
    for index, snapshot in snapshots.items():
        np.testing.assert_array_equal(snapshot.data, frozen[index][0])
        assert snapshot.to_metadata() == frozen[index][1]


def test_private_metadata_sharing_refuses_different_sample_layout():
    original = source()
    with pytest.raises(NMRNextError) as caught:
        original._share_samples((Axis("a", 2, encoding="states"), Axis("b", 8)),
                                original.complex_axes, {}, [])
    assert caught.value.code == "INVALID_SHAPE"


def test_private_execution_reuses_validated_nested_metadata_without_deepcopy_walks():
    from nmrnext.operations import _run_plan
    from nmrnext.planning import plan_operations

    raw = np.arange(8, dtype=np.float64).reshape(2, 4)
    metadata = {"vendor": {"nested": [{"values": list(range(100))}]}}
    history = [{"import": {"opaque": [17]}}]
    original = Dataset(raw, (Axis("x", 4),), ("x",), metadata, history)
    metadata["vendor"]["nested"][0]["values"][0] = -99
    history[0]["import"]["opaque"][0] = -99
    operations = [{"op": "window", "axis": "x", "kind": "exponential", "lb_hz": .1},
                  {"op": "fft", "axis": "x"},
                  {"op": "phase", "axis": "x", "p0": 17}]
    plan = plan_operations(original, operations)
    walks = []
    prior_profile = sys.getprofile()
    def observe(frame, event, value):
        if event == "call" and frame.f_code.co_name == "deepcopy":
            walks.append(frame.f_code.co_filename)
    sys.setprofile(observe)
    try:
        result = _run_plan(original, plan)
    finally:
        sys.setprofile(prior_profile)
    assert walks == []
    for snapshot in (original, result):
        exposed = snapshot.metadata
        exposed["vendor"]["nested"][0]["values"][0] = -77
        exposed_history = snapshot.provenance
        exposed_history[0]["import"]["opaque"][0] = -77
        assert snapshot.metadata["vendor"]["nested"][0]["values"][0] == 0
        assert snapshot.provenance[0]["import"]["opaque"][0] == 17


def test_private_execution_keeps_preflight_and_intermediate_overflow_errors():
    original = Dataset(np.array([[3e38, 3e38], [0, 0]], dtype=np.float32),
                       (Axis("x", 2),), ("x",))
    with pytest.raises(NMRNextError) as caught:
        run_operations(original, [{"op": "fft", "axis": "x"},
                                  {"op": "phase", "axis": "x", "p0": 0}])
    assert caught.value.code == "NUMERICAL_OVERFLOW"
    np.testing.assert_array_equal(original.data[0], np.array([3e38, 3e38], dtype=np.float32))
    with pytest.raises(NMRNextError) as caught:
        run_operations(original, [{"op": "fft", "axis": "x"},
                                  {"op": "zero_fill", "axis": "x", "size": 4}])
    assert caught.value.code == "INVALID_DOMAIN"


class AnnotatedDataset(Dataset):
    @property
    def metadata(self):
        return {**super().metadata, "extension_annotation": {"source": "visible"}}

    @property
    def provenance(self):
        return super().provenance + [{"extension_import": "immutable"}]


class ScaledDataset(Dataset):
    @property
    def data(self):
        values = super().data
        return np.frombuffer((values*2).tobytes(), dtype=values.dtype).reshape(values.shape)


class NonfiniteDataset(Dataset):
    @property
    def data(self):
        return super().data*np.nan


def test_dataset_subclass_public_metadata_and_provenance_survive_execution():
    original = AnnotatedDataset(np.ones((2, 4)), (Axis("x", 4),), ("x",),
                                {"core": 1}, [{"base": "import"}])
    result = run_operations(original, [{"op": "window", "axis": "x", "lb_hz": 0},
                                       {"op": "phase", "axis": "x", "p0": 0}])
    assert result.metadata == {"core": 1, "extension_annotation": {"source": "visible"}}
    assert result.provenance[:2] == [{"base": "import"}, {"extension_import": "immutable"}]
    assert len(result.provenance) == 4


@pytest.mark.parametrize("operation,frequency,expected", [
    ({"op": "reference", "axis": "x", "reference_ppm": 4.7,
      "reference_hz": 0, "observation_mhz": 500}, True, [[2, 4], [6, 8]]),
    ({"op": "reverse", "axis": "x"}, False, [[4, 2], [8, 6]]),
    ({"op": "crop", "axis": "x", "start": 0, "stop": 1}, False, [[2], [6]]),
    ({"op": "window", "axis": "x", "lb_hz": 0}, False, [[2, 4], [6, 8]]),
    ({"op": "phase", "axis": "x", "p0": 0}, False, [[2, 4], [6, 8]]),
])
def test_readonly_dataset_subclass_public_samples_are_used(operation, frequency, expected):
    axis = Axis("x", 2, domain="frequency" if frequency else "time",
                unit="Hz" if frequency else "s")
    original = ScaledDataset(np.array([[1., 2.], [3., 4.]]), (axis,), ("x",))
    assert not original.data.flags.writeable
    result = run_operations(original, [operation])
    np.testing.assert_array_equal(result.data, expected)
    np.testing.assert_array_equal(original.data, [[2, 4], [6, 8]])
    assert not np.shares_memory(result.data, original.data)


@pytest.mark.parametrize("operation,frequency", [
    ({"op": "reference", "axis": "x", "reference_ppm": 4.7,
      "reference_hz": 0, "observation_mhz": 500}, True),
    ({"op": "crop", "axis": "x", "start": 0, "stop": 1}, False),
    ({"op": "reverse", "axis": "x"}, False),
    ({"op": "window", "axis": "x", "lb_hz": 0}, False),
    ({"op": "phase", "axis": "x", "p0": 0}, False),
])
def test_dataset_subclass_nonfinite_public_samples_keep_legacy_operation_error(operation, frequency):
    axis = Axis("x", 2, domain="frequency" if frequency else "time",
                unit="Hz" if frequency else "s")
    original = NonfiniteDataset(np.ones((2, 2)), (axis,), ("x",))
    with pytest.raises(NMRNextError) as caught:
        run_operations(original, [operation, {"op": "reverse", "axis": "x"}])
    assert caught.value.code == "NUMERICAL_OVERFLOW"


def test_empty_recipe_preserves_dataset_subclass_identity_and_does_not_add_sample_validation():
    original = NonfiniteDataset(np.ones((2, 2)), (Axis("x", 2),), ("x",))
    assert run_operations(original, []) is original


def test_dataset_subclass_can_crop_away_nonfinite_public_samples_as_before():
    class LiveDataset(Dataset):
        @property
        def data(self):
            return self.samples
    original = LiveDataset(np.ones((2, 2)), (Axis("x", 2),), ("x",))
    original.samples = np.array([[3., np.nan], [7., np.nan]])
    result = run_operations(original, [{"op": "crop", "axis": "x", "start": 0, "stop": 1},
                                       {"op": "window", "axis": "x", "lb_hz": 0}])
    np.testing.assert_array_equal(result.data, [[3], [7]])
