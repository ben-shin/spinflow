"""Private display preparation retains exact source samples within a bounded frame."""
from unittest.mock import patch

import numpy as np
import pytest

from nmrnext._viewport import display_frame, axis_coordinates, full_extent
from nmrnext.errors import NMRNextError
from nmrnext.model import Axis, Dataset


@pytest.mark.parametrize("step", [-3., 3.])
@pytest.mark.parametrize("reference", [False, True])
def test_bounded_2d_frame_has_exact_signed_values_and_coordinates(step, reference):
    data = np.arange(2 * 1050 * 2050, dtype=np.float64).reshape(2, 1050, 2050)
    data[1] *= -1
    axes = (Axis("n", 1050, domain="frequency", unit="Hz", start=6000., step=-step,
                 observation_mhz=60. if reference else None, reference_ppm=120. if reference else None),
            Axis("h", 2050, domain="frequency", unit="Hz", start=4000., step=step,
                 observation_mhz=600. if reference else None, reference_ppm=8. if reference else None))
    source = Dataset(data, axes, ("h",))
    coordinates = [axis_coordinates(axis, np.arange(axis.size))[0] for axis in axes]
    bounds = [sorted((values[start], values[stop])) for values, start, stop in
              zip(coordinates, (100, 110), (120, 140))]
    with patch.object(Dataset, 'coordinates', side_effect=AssertionError("full axis allocation")):
        frame = display_frame(source, 1, bounds)
    np.testing.assert_array_equal(frame.values, data[1, 100:121, 110:141])
    for actual, expected in zip(frame.coordinates, (coordinates[0][100:121], coordinates[1][110:141])):
        np.testing.assert_array_equal(actual, expected)
    assert frame.units == (('ppm', 'ppm') if reference else ('Hz', 'Hz'))
    assert frame.strides == (1, 1) and not frame.sampled
    assert not frame.values.flags.writeable
    assert not np.shares_memory(source.data, frame.values)
    np.testing.assert_array_equal(source.data, data)
    # The ascending image geometry uses actual strided sample-center spacing.
    x, y, width, height = frame.rect
    for values, edge, length in ((frame.coordinates[1], x, width), (frame.coordinates[0], y, height)):
        spacing = abs(values[1] - values[0])
        assert edge + length / (2 * len(values)) == pytest.approx(min(values), abs=1e-12)
        assert edge + length * (len(values) - .5) / len(values) == pytest.approx(max(values), abs=1e-12)
        assert length == pytest.approx(spacing * len(values), abs=1e-10)


def test_overview_and_small_memory_frame_respect_caps_and_declared_estimate():
    data = np.zeros((1, 2048, 4096), dtype=np.float64)
    source = Dataset(data, [Axis('y', 2048), Axis('x', 4096)])
    full = display_frame(source, 0)
    assert full.sampled and max(full.values.shape) <= 1024
    assert full.estimated_bytes <= full.limit_bytes <= 32 * 1024**2
    small = display_frame(source, 0, memory_bytes=1024**2)
    assert small.values.size < full.values.size
    assert small.estimated_bytes <= small.limit_bytes == 1024**2
    np.testing.assert_array_equal(small.values, source.data[(0,) + small.slices])
    # Actual sampled centers, not original steps, determine the overview rectangle.
    x, y, width, height = full.rect
    assert width / full.values.shape[1] == pytest.approx(full.strides[1] * 1000.)
    assert height / full.values.shape[0] == pytest.approx(full.strides[0] * 1000.)


def test_curve_zoom_recovers_signed_impulses_lost_by_overview_striding():
    data = np.zeros((1, 50001), dtype=np.float64)
    data[0, 25000:25002] = [10., -8.]
    axis = Axis('t', 50001, step=.0001)
    source = Dataset(data, [axis])
    overview = display_frame(source, 0)
    assert overview.values.size <= 20000 and overview.sampled
    assert not np.any(overview.values)
    detail = display_frame(source, 0, [(2500., 2500.4)])
    assert detail.units == ('ms',) and detail.strides == (1,)
    np.testing.assert_array_equal(detail.values, data[0, 25000:25005])
    np.testing.assert_array_equal(detail.coordinates[0], source.coordinates('t')[25000:25005] * 1000.)
    assert detail.values.max() == 10. and detail.values.min() == -8.


@pytest.mark.parametrize('bounds', [((float('nan'), 2),), ((0, float('inf')),), ((2, 1),)])
def test_invalid_bounds_refuse_without_parent_scan(bounds):
    source = Dataset(np.zeros((1, 10)), [Axis('x', 10)])
    with pytest.raises(NMRNextError) as caught:
        display_frame(source, 0, bounds)
    assert caught.value.code == 'DISPLAY_RANGE'


def test_huge_bounds_clamp_outside_is_empty_and_singleton_geometry_is_finite():
    source = Dataset(np.array([[[2., -3., 5.]]]), [Axis('y', 1), Axis('x', 3)])
    huge = display_frame(source, 0, [(-1e300, 1e300)] * 2)
    np.testing.assert_array_equal(huge.values, source.data[0])
    assert huge.rect == (-500., -500., 3000., 1000.)
    assert full_extent(source.axes[1]) == (-500., 2500.)
    outside = display_frame(source, 0, [(1e30, 1e31), (-1e300, 1e300)])
    assert outside.values.size == 0 and outside.rect is None


def test_overflowing_display_coordinates_refuse():
    source = Dataset(np.zeros((1, 2)), [Axis('x', 2, start=1e308, step=1e308)])
    with pytest.raises(NMRNextError) as caught:
        display_frame(source, 0)
    assert caught.value.code == 'DISPLAY_COORDINATES'
