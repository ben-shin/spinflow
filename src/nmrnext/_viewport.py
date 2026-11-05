"""Bounded display frames; numerical datasets and analysis stay untouched.

The conservative estimate reserves 64 bytes per displayed sample, 64 per axis
coordinate and 64 KiB fixed overhead. This covers our bounded NumPy copies and
scratch work with margin. Resident parent storage and opaque Qt/native/OS
allocations are separate; this is not a process-RSS cap.
"""
from dataclasses import dataclass
import math

import numpy as np

from .errors import NMRNextError


DISPLAY_LIMIT = 32 * 1024**2
_OVERHEAD = 64 * 1024


@dataclass(frozen=True)
class DisplayFrame:
    values: np.ndarray
    coordinates: tuple
    units: tuple
    slices: tuple
    strides: tuple
    sampled: bool
    estimated_bytes: int
    limit_bytes: int
    rect: tuple | None


def axis_coordinates(axis, indices):
    """Evaluate only requested indices, using Dataset.coordinates' operation order."""
    with np.errstate(over='ignore', invalid='ignore'):
        values = axis.start + np.asarray(indices, dtype=np.float64) * axis.step
        if axis.domain == 'frequency' and axis.observation_mhz and axis.reference_ppm is not None:
            values = axis.reference_ppm + (values - axis.reference_hz) / axis.observation_mhz
            unit = 'ppm'
        elif axis.domain == 'time':
            values = values * 1000.
            unit = 'ms'
        else:
            unit = axis.unit
    if not np.isfinite(values).all():
        raise NMRNextError('DISPLAY_COORDINATES', 'Display coordinates must be finite')
    return values, unit


def _coordinate(axis, index):
    return float(axis_coordinates(axis, index)[0])


def _spacing(axis, index=0):
    # The mathematical grid spacing also works for singleton source axes.
    step = axis.step
    if axis.domain == 'frequency' and axis.observation_mhz and axis.reference_ppm is not None:
        step /= axis.observation_mhz
    elif axis.domain == 'time':
        step *= 1000.
    if not math.isfinite(step) or step == 0:
        raise NMRNextError('DISPLAY_COORDINATES', 'Display coordinate spacing is not representable')
    return abs(step)


def full_extent(axis):
    start, stop = _coordinate(axis, 0), _coordinate(axis, axis.size - 1)
    half = _spacing(axis) / 2
    result = (min(start, stop) - half, max(start, stop) + half)
    if not all(math.isfinite(v) for v in result) or result[0] == result[1]:
        raise NMRNextError('DISPLAY_COORDINATES', 'Display coordinate extent is not representable')
    return result


def _visible_interval(axis, bounds):
    first_value, last_value = _coordinate(axis, 0), _coordinate(axis, axis.size - 1)
    if bounds is None:
        return 0, axis.size
    if (not isinstance(bounds, (tuple, list)) or len(bounds) != 2
            or not all(isinstance(v, (float, int, np.number)) and math.isfinite(v) for v in bounds)
            or bounds[0] > bounds[1]):
        raise NMRNextError('DISPLAY_RANGE', 'Display range must be two ordered finite coordinates')
    low, high = bounds
    descending = last_value < first_value

    def lower_bound(value, strict=False):
        lo, hi = 0, axis.size
        while lo < hi:
            mid = (lo + hi) // 2
            current = _coordinate(axis, axis.size - 1 - mid if descending else mid)
            if current < value or (strict and current == value):
                lo = mid + 1
            else:
                hi = mid
        return lo

    start, stop = lower_bound(low), lower_bound(high, strict=True)
    return ((axis.size - stop, axis.size - start) if descending else (start, stop))


def _estimate(shape):
    return _OVERHEAD + 64 * math.prod(shape) + 64 * sum(shape)


def display_frame(dataset, component, ranges=None, *, memory_bytes=DISPLAY_LIMIT):
    """Copy selected immutable samples after bounded slicing, never before it."""
    ndim = len(dataset.axes)
    if ndim not in (1, 2):
        raise NMRNextError('DISPLAY_DIMENSIONS', 'Display frames require one or two axes')
    if isinstance(component, bool) or not isinstance(component, int) or not 0 <= component < dataset.data.shape[0]:
        raise NMRNextError('DISPLAY_COMPONENT', 'Choose a valid signed component')
    if isinstance(memory_bytes, bool) or not isinstance(memory_bytes, int) or memory_bytes < _estimate((1,) * ndim):
        raise NMRNextError('DISPLAY_MEMORY', 'Display working-array budget is too small')
    limit = min(DISPLAY_LIMIT, memory_bytes)
    if ranges is not None and (not isinstance(ranges, (tuple, list)) or len(ranges) != ndim):
        raise NMRNextError('DISPLAY_RANGE', 'Declare one visible range per physical axis')
    intervals = tuple(_visible_interval(axis, None if ranges is None else ranges[i])
                      for i, axis in enumerate(dataset.axes))
    lengths = tuple(stop - start for start, stop in intervals)
    caps = (20000,) if ndim == 1 else (1024, 1024)
    strides = [max(1, math.ceil(length / cap)) for length, cap in zip(lengths, caps)]
    shape = tuple(math.ceil(length / stride) for length, stride in zip(lengths, strides))
    while _estimate(shape) > limit:
        axis = max(range(ndim), key=lambda i: shape[i])
        strides[axis] *= 2
        shape = tuple(math.ceil(length / stride) for length, stride in zip(lengths, strides))
    slices = tuple(slice(start, stop, stride) for (start, stop), stride in zip(intervals, strides))
    values = np.array(dataset.data[(component,) + slices], copy=True, order='C')
    values.setflags(write=False)
    coordinates, units = [], []
    for axis, selected in zip(dataset.axes, slices):
        coordinate, unit = axis_coordinates(axis, np.arange(selected.start, selected.stop, selected.step))
        coordinate.setflags(write=False)
        coordinates.append(coordinate)
        units.append(unit)
    rect = None
    if ndim == 2 and values.size:
        edges = []
        for axis, coordinate, stride in zip(dataset.axes, coordinates, strides):
            spacing = (abs(float(coordinate[-1] - coordinate[0])) / (len(coordinate) - 1)
                       if len(coordinate) > 1 else _spacing(axis) * stride)
            if not math.isfinite(spacing) or spacing <= 0:
                raise NMRNextError('DISPLAY_COORDINATES', 'Image sample spacing is not representable')
            edge, length = float(min(coordinate[0], coordinate[-1])) - spacing / 2, spacing * len(coordinate)
            if not math.isfinite(edge) or not math.isfinite(length):
                raise NMRNextError('DISPLAY_COORDINATES', 'Image extent is not representable')
            edges.append((edge, length))
        rect = (edges[1][0], edges[0][0], edges[1][1], edges[0][1])
    return DisplayFrame(values, tuple(coordinates), tuple(units), slices, tuple(strides),
                        any(stride > 1 for stride in strides), _estimate(shape), limit, rect)
