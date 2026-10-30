"""Bounded, inspectable frequency-domain analysis (report schema 1).

No concentration inference or line deconvolution is performed. Integrals have
positive coordinate measure and retain the sign of the selected component.
Reports refer to immutable data/axis snapshots, not mutable Dataset metadata.

Automatic phase estimation fits isolated positive complex Lorentzian lines,
or positive separable four-quadrature Lorentzian cross-peaks in 2D.
This is an intentionally restricted model, inspired by line-symmetry methods
(Heuer, JMR 91, 241–253, doi:10.1016/0022-2364(91)90189-Z), not ACME and not a
general solution for edited, mixed-sign, crowded or non-Lorentzian spectra.
Implementation is original; no third-party algorithm source was copied.
MAD scaling follows scipy.stats.median_abs_deviation(scale='normal').
"""
from functools import wraps
import hashlib
from itertools import product
import json
import math

import numpy as np
from scipy import ndimage, optimize, signal

from .errors import NMRNextError
from .model import Dataset, finite
from .operations import quadrature_index_orientation, run_operations


MAX_POINTS = 2_000_000
MAX_REGIONS = 1000
_MAD_NORMAL = 0.6744897501960817


def _guard(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                result = function(*args, **kwargs)
        except (FloatingPointError, OverflowError) as exc:
            raise NMRNextError("ANALYSIS_NUMERICAL_FAILURE", "Analysis exceeded finite numerical range") from exc
        except MemoryError as exc:
            raise NMRNextError("MEMORY_ALLOCATION_FAILED", "Analysis working arrays could not be allocated") from exc
        report = result[1] if isinstance(result, tuple) else result
        def numpy_scalar(value):
            if isinstance(value, np.generic):
                return value.item()
            raise TypeError("Not a JSON value")
        try:
            report = json.loads(json.dumps(report, allow_nan=False, default=numpy_scalar))
        except (TypeError, ValueError) as exc:
            raise NMRNextError("INVALID_ANALYSIS_REPORT", "Analysis parameters and coordinates must be finite JSON values") from exc
        return (result[0], report) if isinstance(result, tuple) else report
    return wrapped


def _validate(dataset, channel=0):
    if not isinstance(dataset, Dataset):
        raise NMRNextError("INVALID_DATASET", "Analysis requires a Dataset")
    if len(dataset.axes) not in (1, 2) or any(a.role != "spectral" or a.domain != "frequency" for a in dataset.axes):
        raise NMRNextError("UNSUPPORTED_ANALYSIS_PROFILE", "Analysis supports fully frequency-domain 1D/2D spectral datasets")
    if math.prod(a.size for a in dataset.axes) > MAX_POINTS:
        raise NMRNextError("ANALYSIS_SIZE_LIMIT", f"Analysis is limited to {MAX_POINTS} points per component; crop first")
    if isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel < dataset.data.shape[0]:
        raise NMRNextError("INVALID_CHANNEL", "channel must be an existing integer component index")
    if any(a.size < 2 for a in dataset.axes):
        raise NMRNextError("INSUFFICIENT_DATA", "Each analysis axis needs at least two points")
    if any(not math.isfinite(a.start+(a.size-1)*a.step) for a in dataset.axes):
        raise NMRNextError("UNAVAILABLE_COORDINATES", "Axis endpoint exceeds finite coordinate range")
    quadrature_index_orientation(dataset)
    return dataset.data[channel].astype(np.float64)


def dataset_digest(dataset):
    """SHA256 of dtype/shape, stable axes, quadrature IDs and C-order samples.

    Unrelated metadata/provenance are excluded. Nondefault index-handedness
    overrides are included because they change complex-line interpretation;
    equivalent explicit and legacy-derived forms have the same identity. Hashing uses the immutable buffer without a data copy.
    """
    from ._identity import numeric_digest
    return numeric_digest(dataset)


def _report(dataset, kind, parameters):
    return {"schema_version": "1", "kind": kind, "dataset_digest": dataset_digest(dataset),
            "axes": [a.to_dict() for a in dataset.axes], "parameters": parameters, "flags": [],
            "implementation": "nmrnext.analysis/1", "numpy_version": np.__version__}


def _positive(value, name, allow_zero=False):
    value = finite(value, name)
    if value < 0 or (value == 0 and not allow_zero):
        raise NMRNextError("INVALID_PARAMETER", f"{name} must be {'nonnegative' if allow_zero else 'positive'}")
    return value


def _valid_mask(mask, shape):
    if mask is None:
        return np.ones(shape, dtype=bool)
    if np.ma.isMaskedArray(mask):
        raise NMRNextError("INVALID_MASK", "Use an explicit boolean inclusion mask")
    arr = np.asarray(mask)
    if arr.dtype != np.bool_ or arr.shape != shape or not arr.any():
        raise NMRNextError("INVALID_MASK", "mask must be boolean, match spectral shape, and include at least one point")
    return arr


def _mask_digest(valid, supplied):
    return hashlib.sha256(valid.tobytes()).hexdigest() if supplied is not None else None


def _selection_record(valid):
    return {"included_points": int(valid.sum()), "total_points": int(valid.size),
            "scope": "Only accepted, fully included fit windows inform phase; excluded regions are not certified"}


def _masked_phase_variation(channels, valid):
    scales = []
    for dim in range(valid.ndim):
        left = [slice(None)]*valid.ndim
        right = list(left)
        left[dim], right[dim] = slice(None, -1), slice(1, None)
        pairs = valid[tuple(left)] & valid[tuple(right)]
        if int(pairs.sum()) < 3:
            continue
        for channel in channels:
            differences = np.diff(channel, axis=dim)[pairs]
            for part in ((differences.real, differences.imag) if np.iscomplexobj(differences) else (differences,)):
                scales.append(float(np.median(np.abs(part-np.median(part)))/(_MAD_NORMAL*math.sqrt(2))))
    if not scales:
        raise NMRNextError("INSUFFICIENT_NOISE_POINTS", "Phase inclusion mask needs at least three adjacent sample pairs")
    return max(scales)


def _noise(data, valid=None):
    """Robust marginal amplitude SD, not the SD of sample differences.

    Zero filling/apodization correlate nearby spectral samples. Differencing
    would then shrink the estimated amplitude noise by sqrt(1-rho). The MAD of
    amplitudes retains the Gaussian marginal scale under these correlations;
    correlation reduces effective sample count, not the amplitude denominator.
    Sparse signal is tolerated, but signal tails/nonstationary baselines can
    inflate this global estimate. It is not a local-baseline or FDR model.
    """
    if valid is None:
        valid = np.ones(data.shape, dtype=bool)
    values = data[valid]
    if values.size < 3:
        raise NMRNextError("INSUFFICIENT_NOISE_POINTS", "At least three included amplitudes are needed for noise estimation")
    sigma = float(np.median(np.abs(values - np.median(values))) / _MAD_NORMAL)
    return {"sigma": sigma, "method": "amplitude_MAD_normal",
            "sample_count": int(values.size), "sample_count_is_effective_count": False,
            "assumption": "stationary Gaussian marginal amplitudes with sparse signal; signal tails and varying baselines may inflate the global estimate; no FDR guarantee"}


def _phase_candidate_scale(data):
    """Legacy local-variation heuristic used only to screen phase-fit seeds.

    Dispersive line tails invalidate a whole-spectrum amplitude MAD for this
    purpose. This scale is NOT an amplitude-noise estimate or significance
    guarantee; actual acceptance uses the bounded complex line-model residual.
    """
    differences = np.diff(data)
    return float(np.median(np.abs(differences-np.median(differences))) / (_MAD_NORMAL*math.sqrt(2)))


def _axis_values(dataset, axis, unit):
    if unit == "points":
        return 0., 1.
    if unit not in ("Hz", "ppm"):
        raise NMRNextError("INVALID_UNIT", "Analysis coordinates require points, Hz or calibrated ppm")
    if unit == "Hz":
        return axis.start, axis.step
    if axis.observation_mhz is None or axis.reference_ppm is None:
        raise NMRNextError("UNAVAILABLE_COORDINATES", f"Axis {axis.id} has no ppm calibration")
    start = axis.reference_ppm + (axis.start - axis.reference_hz) / axis.observation_mhz
    step = axis.step / axis.observation_mhz
    if not math.isfinite(start) or not math.isfinite(step) or step == 0:
        raise NMRNextError("UNAVAILABLE_COORDINATES", "ppm coordinates exceed finite numerical range")
    return start, step


def _widths(dataset, value, unit, name):
    if isinstance(value, dict):
        if set(value) != {a.id for a in dataset.axes}:
            raise NMRNextError("INVALID_PARAMETER", f"{name} must specify every axis ID")
        values = {a.id: _positive(value[a.id], name) for a in dataset.axes}
    else:
        values = {a.id: _positive(value, name) for a in dataset.axes}
    widths = [values[a.id] / abs(_axis_values(dataset, a, unit)[1]) for a in dataset.axes]
    if any(not math.isfinite(width) or width == 0 for width in widths):
        raise NMRNextError("INVALID_PARAMETER", f"{name} exceeds finite numerical range")
    return widths


def _coordinates(dataset, point):
    result = {}
    for axis, index in zip(dataset.axes, point):
        hz = float(axis.start + index * axis.step)
        result[axis.id] = {"Hz": hz}
        if axis.observation_mhz is not None and axis.reference_ppm is not None:
            result[axis.id]["ppm"] = float(axis.reference_ppm + (hz - axis.reference_hz) / axis.observation_mhz)
    return result


@_guard
def pick_peaks(dataset, *, channel=0, sign="positive", threshold=None, snr=5.,
               min_separation=1., separation_unit="points", mask=None,
               baseline="median", baseline_value=None, max_peaks=1000, noise_mask=None):
    """Pick local extrema in an explicit real quadrature component.

    The threshold is absolute height above the declared constant baseline, or
    snr*noise when omitted. Equal plateaus yield the point nearest their centroid
    (lexicographic tie break). Peaks at edges are retained and flagged. Greedy
    separation is in a box: suppress when every coordinate distance is strictly
    less than its minimum. Strongest absolute height wins, then point order.
    mask=True includes a point; mask boundaries are retained and flagged.
    noise_mask selects separate user-declared signal-free samples for the MAD
    amplitude scale; it does not restrict detection or estimate a local baseline.
    """
    data = _validate(dataset, channel)
    if sign not in ("positive", "negative", "both"):
        raise NMRNextError("INVALID_PARAMETER", "sign must be positive, negative or both")
    if isinstance(max_peaks, bool) or not isinstance(max_peaks, int) or not 1 <= max_peaks <= 10000:
        raise NMRNextError("INVALID_PARAMETER", "max_peaks must be an integer in [1,10000]")
    valid = _valid_mask(mask, data.shape)
    noise_valid = valid if noise_mask is None else _valid_mask(noise_mask, data.shape)
    noise = _noise(data, noise_valid)
    noise["selection"] = "detection_mask" if noise_mask is None else "explicit_noise_mask"
    snr = _positive(snr, "snr")
    cut = snr * noise["sigma"] if threshold is None else _positive(threshold, "threshold", True)
    separations = _widths(dataset, min_separation, separation_unit, "min_separation")
    if baseline == "median" and baseline_value is None:
        offset = float(np.median(data[valid]))
    elif baseline == "none" and baseline_value is None:
        offset = 0.
    elif baseline == "constant" and baseline_value is not None:
        offset = finite(baseline_value, "baseline_value")
    else:
        raise NMRNextError("INVALID_BASELINE", "Use median, none, or constant with baseline_value")
    centered = data - offset
    candidates = []
    structure = np.ones((3,) * data.ndim, dtype=bool)
    near_mask = ndimage.maximum_filter(~valid, size=3, mode="constant", cval=0)
    for polarity in ((1, -1) if sign == "both" else (1,) if sign == "positive" else (-1,)):
        work = np.where(valid, polarity * centered, -np.inf)
        maximum = ndimage.maximum_filter(work, size=3, mode="constant", cval=-np.inf)
        local = valid & (work == maximum) & (work >= cut) & (work > 0)
        labels, count = ndimage.label(local, structure=structure)
        if count > 50000:
            raise NMRNextError("TOO_MANY_CANDIDATES", "More than 50000 peak candidates; raise threshold or crop")
        for label, region in enumerate(ndimage.find_objects(labels), start=1):
            pts = np.argwhere(labels[region] == label)
            pts += np.array([s.start for s in region])
            distances = np.sum((pts - pts.mean(axis=0))**2, axis=1)
            point = tuple(int(v) for v in pts[np.argmin(distances)])
            neighborhood = tuple(slice(max(0, s.start-1), min(size, s.stop+1)) for s, size in zip(region, data.shape))
            plateau = labels[neighborhood] == label
            border = ndimage.binary_dilation(plateau, structure=structure) & ~plateau & valid[neighborhood]
            if not np.any(work[neighborhood][border] < work[point]):
                # A constant spectrum or isolated mask island is not a peak.
                continue
            flags = []
            if len(pts) > 1:
                flags.append("plateau")
            if any(p == 0 or p == size - 1 for p, size in zip(point, data.shape)):
                flags.append("spectrum_edge")
            if near_mask[point]:
                flags.append("mask_edge")
            candidates.append((abs(float(centered[point])), point, polarity, flags))
    candidates.sort(key=lambda item: (-item[0], item[1], -item[2]))
    chosen, occupied = [], {}
    truncated = False
    for item in candidates:
        cell = tuple(math.floor(x/d) for x, d in zip(item[1], separations))
        neighbors = (occupied.get(tuple(c+v for c, v in zip(cell, offset)))
                     for offset in product((-1, 0, 1), repeat=data.ndim))
        if any(other is not None and all(abs(x-y) < d for x, y, d in zip(item[1], other[1], separations)) for other in neighbors):
            continue
        if len(chosen) == max_peaks:
            truncated = True
            break
        chosen.append(item)
        occupied[cell] = item
    params = {"channel": channel, "sign": sign, "threshold": threshold, "snr": snr,
              "min_separation": min_separation, "separation_unit": separation_unit,
              "separation_rule": "axis-aligned exclusion box, strict <; strongest first; lexicographic ties",
              "baseline": baseline, "baseline_value": baseline_value, "max_peaks": max_peaks,
              "mask_digest": hashlib.sha256(valid.tobytes()).hexdigest() if mask is not None else None,
              "noise_mask_digest": hashlib.sha256(noise_valid.tobytes()).hexdigest() if noise_mask is not None else None}
    report = _report(dataset, "peak_picking", params)
    report.update(noise=noise, threshold=float(cut), baseline={"method": baseline, "value": offset})
    report["peaks"] = [{"id": f"peak-{i+1}", "points": dict(zip((a.id for a in dataset.axes), point)),
                        "coordinates": _coordinates(dataset, point), "intensity": float(data[point]),
                        "height_above_baseline": float(centered[point]),
                        "sign": "positive" if polarity == 1 else "negative", "flags": flags}
                       for i, (_, point, polarity, flags) in enumerate(chosen)]
    if noise["sigma"] == 0:
        report["flags"].append("zero_noise_estimate")
    if truncated:
        report["flags"].append("max_peaks_reached")
    return report


def _region_bounds(dataset, regions):
    if not isinstance(regions, (list, tuple)) or not 1 <= len(regions) <= MAX_REGIONS:
        raise NMRNextError("INVALID_REGION", f"regions must be a nonempty list of at most {MAX_REGIONS} regions")
    parsed, ids = [], set()
    for region in regions:
        if not isinstance(region, dict) or set(region) - {"id", "bounds", "unit", "flags", "peak_id"}:
            raise NMRNextError("INVALID_REGION", "Region fields are id, bounds, unit and optional flags/peak_id")
        identity, bounds, unit = region.get("id"), region.get("bounds"), region.get("unit", "points")
        if not isinstance(identity, str) or not identity or identity in ids:
            raise NMRNextError("INVALID_REGION", "Region IDs must be nonempty and unique")
        ids.add(identity)
        if not isinstance(bounds, dict) or set(bounds) != {a.id for a in dataset.axes}:
            raise NMRNextError("INVALID_REGION", "Every region must bound every stable axis ID")
        points = {}
        for axis in dataset.axes:
            pair = bounds[axis.id]
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise NMRNextError("INVALID_REGION", "Each bound is a two-element inclusive pair")
            start, step = _axis_values(dataset, axis, unit)
            lo, hi = sorted((finite(v, "region bound") - start) / step for v in pair)
            if lo < -1e-9 or hi > axis.size - 1 + 1e-9 or hi <= lo:
                raise NMRNextError("INVALID_REGION", "Bounds must span positive width inside the sample centers")
            lo, hi = max(0., lo), min(float(axis.size - 1), hi)
            if hi <= lo:
                raise NMRNextError("INVALID_REGION", "Region has no width within the dataset")
            points[axis.id] = [lo, hi]
        flags = region.get("flags", [])
        if not isinstance(flags, list) or not all(isinstance(f, str) for f in flags):
            raise NMRNextError("INVALID_REGION", "Region flags must be strings")
        parsed.append({"id": identity, "bounds": {k: list(v) for k, v in bounds.items()},
                       "unit": unit, "point_bounds": points, "flags": list(flags)})
    return parsed


def _overlaps(regions):
    overlaps = {r["id"]: [] for r in regions}
    for index, left in enumerate(regions):
        for right in regions[index+1:]:
            if all(max(pair[0], right["point_bounds"][axis][0]) < min(pair[1], right["point_bounds"][axis][1])
                   for axis, pair in left["point_bounds"].items()):
                overlaps[left["id"]].append(right["id"])
                overlaps[right["id"]].append(left["id"])
    return overlaps


@_guard
def propose_regions(dataset, peaks_report, *, half_width=8., unit="points"):
    """Propose fixed-width editable boxes, without claiming line separation.

    This is a geometric proposal, not linewidth fitting. Edge clipping and
    overlapping boxes are flagged. Inspect/edit report['regions'] before use.
    """
    _validate(dataset)
    if not isinstance(peaks_report, dict) or peaks_report.get("kind") != "peak_picking" or peaks_report.get("dataset_digest") != dataset_digest(dataset):
        raise NMRNextError("STALE_ANALYSIS", "Peak report must belong to the current dataset")
    widths = _widths(dataset, half_width, unit, "half_width")
    regions = []
    peaks = peaks_report.get("peaks")
    if not isinstance(peaks, list) or len(peaks) > MAX_REGIONS:
        raise NMRNextError("INVALID_PEAK_REPORT", f"Proposal supports at most {MAX_REGIONS} peaks")
    for peak in peaks:
        if not isinstance(peak, dict) or not isinstance(peak.get("points"), dict) or set(peak["points"]) != {a.id for a in dataset.axes}:
            raise NMRNextError("INVALID_PEAK_REPORT", "Peak points must identify every axis")
        bounds, flags = {}, ["requires_review", "fixed_width_proposal"]
        for axis, width in zip(dataset.axes, widths):
            center = finite(peak["points"][axis.id], "peak point")
            if not 0 <= center <= axis.size - 1:
                raise NMRNextError("INVALID_PEAK_REPORT", "Peak is outside the dataset")
            lo, hi = max(0., center-width), min(float(axis.size-1), center+width)
            if lo == 0 or hi == axis.size - 1:
                flags.append("spectrum_edge")
            bounds[axis.id] = [lo, hi]
        regions.append({"id": f"region-{len(regions)+1}", "bounds": bounds, "unit": "points", "flags": sorted(set(flags))})
    if regions:
        overlaps = _overlaps(_region_bounds(dataset, regions))
        for region in regions:
            if overlaps[region["id"]]:
                region["flags"].append("overlapping_proposal")
    report = _report(dataset, "region_proposal", {"half_width": half_width, "unit": unit})
    report["regions"] = regions
    report["flags"] = ["requires_review"]
    return report


def _sample_region(data, axes, bounds):
    """Crop first, then multilinearly interpolate fractional end sample centers."""
    pairs = [bounds[a.id] for a in axes]
    starts = [math.floor(p[0]) for p in pairs]
    block = data[tuple(slice(start, math.ceil(pair[1])+1) for start, pair in zip(starts, pairs))]
    if all(lo == int(lo) and hi == int(hi) for lo, hi in pairs):
        return block, [np.arange(lo, hi+1) for lo, hi in pairs]
    grids = []
    for dimension, ((lo, hi), start) in enumerate(zip(pairs, starts)):
        grid = np.unique(np.concatenate(([lo], np.arange(math.ceil(lo), math.floor(hi)+1, dtype=float), [hi])))
        relative = grid-start
        lower = np.floor(relative).astype(int)
        upper = np.minimum(lower+1, block.shape[dimension]-1)
        shape = [1]*data.ndim
        shape[dimension] = len(grid)
        fraction = (relative-lower).reshape(shape)
        block = np.take(block, lower, axis=dimension)*(1-fraction) + np.take(block, upper, axis=dimension)*fraction
        grids.append(grid)
    return block, grids


def _reference_baseline(data, axes, regions, mask):
    valid = _valid_mask(mask, data.shape)
    # Fractional endpoints sample floor/ceil centers: exclude their full support.
    for region in regions:
        support = tuple(slice(math.floor(region["point_bounds"][a.id][0]),
                              math.ceil(region["point_bounds"][a.id][1])+1) for a in axes)
        if valid[support].any():
            raise NMRNextError("CONTAMINATED_BASELINE_MASK", "Baseline reference mask must exclude every integration region and its interpolation support")
    points = np.nonzero(valid)
    count = len(points[0])
    if count < data.ndim+2:
        raise NMRNextError("INSUFFICIENT_BASELINE_POINTS", "Affine baseline needs at least dimension+2 reference samples")
    design = np.column_stack([np.ones(count), *(p/(a.size-1)-.5 for p, a in zip(points, axes))])
    coef, _, rank, singular = np.linalg.lstsq(design, data[valid], rcond=None)
    if rank != data.ndim+1 or singular[-1] < singular[0]*1e-8:
        raise NMRNextError("UNDERDETERMINED_BASELINE", "Reference samples must identify a well-conditioned affine baseline on every axis")
    rms = float(np.sqrt(np.mean((design@coef-data[valid])**2)))
    return {"method": "reference_linear", "coefficients": coef.tolist(), "sample_count": count,
            "coordinate_convention": "intercept + sum(coefficient[axis+1]*(point/(axis.size-1)-0.5)) in logical axis order",
            "mask_digest": _mask_digest(valid, mask), "reference_rms": rms,
            "assumption": "User-selected reference samples are signal-free and share one affine baseline with integrated regions; off-region signal contamination is not automatically detectable"}


@_guard
def integrate_regions(dataset, regions, *, channel=0, baseline="none", baseline_value=None, allow_overlap=False, baseline_mask=None):
    """Signed trapezoidal area/volume with absolute Hz steps, never peak height.

    Bounds are inclusive sample-center coordinates (fractional endpoints are
    linearly interpolated). Baselines: none; explicit constant; edge_linear
    (line between endpoints in 1D, least-squares perimeter plane in 2D).
    Edge fits can remove real tails and are flagged. Overlap fails unless the
    caller explicitly allows independent overlapping integrals. No sum is given.
    reference_linear fits an affine baseline once to an explicit baseline_mask
    outside all region interpolation supports. Selected samples must be signal
    free; off-region signal contamination cannot be automatically certified.
    """
    data = _validate(dataset, channel)
    if not isinstance(allow_overlap, bool):
        raise NMRNextError("INVALID_PARAMETER", "allow_overlap must be boolean")
    if baseline not in ("none", "constant", "edge_linear", "reference_linear") or (baseline == "constant") != (baseline_value is not None):
        raise NMRNextError("INVALID_BASELINE", "Use none, edge_linear, reference_linear with baseline_mask, or constant with baseline_value")
    if (baseline == "reference_linear") != (baseline_mask is not None):
        raise NMRNextError("INVALID_BASELINE", "baseline_mask is required only for reference_linear")
    value = finite(baseline_value, "baseline_value") if baseline == "constant" else 0.
    parsed = _region_bounds(dataset, regions)
    overlaps = _overlaps(parsed)
    if not allow_overlap and any(overlaps.values()):
        raise NMRNextError("OVERLAPPING_REGIONS", "Regions overlap; edit bounds or explicitly allow independent overlap", details={"overlaps": overlaps})
    reference = _reference_baseline(data, dataset.axes, parsed, baseline_mask) if baseline_mask is not None else None
    for region in parsed:
        block, grids = _sample_region(data, dataset.axes, region["point_bounds"])
        baseline_record = {"method": baseline, "value": value if baseline == "constant" else None}
        if baseline == "edge_linear":
            normalized = [(g-g[0])/(g[-1]-g[0]) for g in grids]
            if data.ndim == 1:
                correction = block[0] + (block[-1]-block[0])*normalized[0]
                baseline_record["coefficients"] = [float(block[0]), float(block[-1]-block[0])]
            else:
                edge = np.zeros(block.shape, dtype=bool)
                edge[[0, -1], :] = True
                edge[:, [0, -1]] = True
                edge_points = np.nonzero(edge)
                design = np.column_stack([np.ones(int(edge.sum())), *(g[i] for g, i in zip(normalized, edge_points))])
                coef = np.linalg.lstsq(design, block[edge], rcond=None)[0]
                correction = coef[0] + coef[1]*normalized[0][:, None] + coef[2]*normalized[1][None, :]
                baseline_record["coefficients"] = coef.tolist()
            block = block-correction
            region["flags"].append("edge_baseline_may_remove_signal")
        elif baseline == "reference_linear":
            baseline_record = dict(reference)
            coef = reference["coefficients"]
            correction = coef[0]
            for dim, (grid, axis) in enumerate(zip(grids, dataset.axes)):
                shape = [1]*data.ndim
                shape[dim] = len(grid)
                correction = correction + coef[dim+1]*(grid/(axis.size-1)-.5).reshape(shape)
            block = block-correction
            region["flags"].append("reference_baseline_requires_signal_free_mask")
        elif baseline == "constant":
            block = block-value
        integral = block
        for axis, grid in reversed(list(zip(dataset.axes, grids))):
            integral = np.trapezoid(integral, x=(grid-grid[0])*abs(axis.step), axis=-1)
        region.update(integral=float(integral), integral_unit="intensity*Hz" if data.ndim == 1 else "intensity*Hz^2",
                      baseline=baseline_record, overlaps=overlaps[region["id"]])
        # Full endpoint coordinates, including ppm only where calibrated.
        endpoints = [_coordinates(dataset, [region["point_bounds"][a.id][k] for a in dataset.axes]) for k in (0, 1)]
        region["coordinate_bounds"] = {a.id: {u: [endpoints[k][a.id][u] for k in (0, 1)]
                                               for u in endpoints[0][a.id]} for a in dataset.axes}
        if any(pair[0] == 0 or pair[1] == a.size-1 for a, pair in ((a, region["point_bounds"][a.id]) for a in dataset.axes)):
            region["flags"].append("spectrum_edge")
        if overlaps[region["id"]]:
            region["flags"].append("overlap_allowed_independent_integral")
        region["flags"] = sorted(set(region["flags"]))
    report = _report(dataset, "integration", {"channel": channel, "baseline": baseline, "baseline_value": baseline_value,
                                            "allow_overlap": allow_overlap, "baseline_mask_digest": None if reference is None else reference["mask_digest"], "quadrature": "trapezoid, multilinear endpoint interpolation",
                                            "coordinate_measure": "positive absolute Hz steps; signed intensity",
                                            "bounds_convention": "inclusive sample centers"})
    report["regions"] = parsed
    report["flags"] = ["relative_integrals_not_absolute_concentration", "no_deconvolution"]
    return report


@_guard
def autophase(dataset, *, axis=None, pivot=None, first_order=True, max_p1=180.,
              max_peaks=8, min_snr=15., backend="numpy", threads=1, inclusion_mask=None, memory_bytes=None):
    """Estimate a phase ramp from isolated positive Lorentzian-like resonances.

    Fits up to eight well-resolved lines with complex affine local backgrounds.
    A shared phase-ramp refinement removes the local constant-phase approximation.
    p0/p1 are correction degrees; pivot is a zero-based POINT, as in operations.
    First order needs at least two accepted lines spanning 15% of the spectrum.
    Failed/ambiguous fits raise structured errors; successful estimates still
    require visual inspection and do not imply quantitative phase certification.
    The historical min_snr parameter is a multiplier for the phase candidate
    variation heuristic, not a statistical amplitude-SNR significance level.
    For 2D, both independent complex axes are estimated together; axis must be
    omitted and pivot, if supplied, maps both axis IDs to points. The report
    contains operations (plural), axis_results and an explicit 180-degree gauge.
    inclusion_mask selects trusted positive anchors; entire line-fit windows
    must be included. Correction applies to all data, but excluded regions are
    not phase-certified. Every accepted 1D line must satisfy relative RMS <=.05.
    """
    if (memory_bytes is not None and isinstance(dataset, Dataset) and len(dataset.axes) in (1, 2)
            and set(dataset.complex_axes) == {a.id for a in dataset.axes}
            and math.prod(a.size for a in dataset.axes) <= MAX_POINTS
            and all(a.size >= 2 and a.role == "spectral" and a.domain == "frequency" for a in dataset.axes)):
        from .planning import plan_operations
        phase_steps = [{"op": "phase", "axis": a.id, "p0": 0., "p1": 0., "pivot": 0.} for a in dataset.axes]
        plan_operations(dataset, phase_steps, backend=backend, threads=threads, memory_bytes=memory_bytes)
    _validate(dataset)
    if len(dataset.axes) == 2:
        return _autophase_2d(dataset, axis=axis, pivot=pivot, first_order=first_order,
                             max_p1=max_p1, max_peaks=max_peaks, min_snr=min_snr,
                             backend=backend, threads=threads, inclusion_mask=inclusion_mask, memory_bytes=memory_bytes)
    if len(dataset.axes) != 1 or dataset.complex_axes != (dataset.axes[0].id,):
        raise NMRNextError("UNSUPPORTED_AUTOPHASE", "Automatic phase requires complex frequency data")
    a = dataset.axes[0]
    if axis is not None and axis != a.id:
        raise NMRNextError("UNKNOWN_AXIS", "Automatic phase axis must identify the 1D spectral axis")
    if not isinstance(first_order, bool):
        raise NMRNextError("INVALID_PARAMETER", "first_order must be boolean")
    if isinstance(max_peaks, bool) or not isinstance(max_peaks, int) or not 1 <= max_peaks <= 8:
        raise NMRNextError("INVALID_PARAMETER", "max_peaks must be an integer in [1,8]")
    max_p1 = _positive(max_p1, "max_p1")
    if max_p1 > 180:
        raise NMRNextError("INVALID_PARAMETER", "max_p1 is limited to 180 degrees; correct large ramps explicitly")
    min_snr = _positive(min_snr, "min_snr")
    pivot = (a.size-1)/2 if pivot is None else finite(pivot, "pivot")
    if not 0 <= pivot <= a.size-1:
        raise NMRNextError("INVALID_PARAMETER", "pivot must be an in-range zero-based point")
    if a.size < 64:
        raise NMRNextError("INSUFFICIENT_DATA", "Automatic phase requires at least 64 points")
    valid = _valid_mask(inclusion_mask, tuple(a.size for a in dataset.axes))
    original = dataset.data[0].astype(float) + 1j*dataset.data[1].astype(float)
    scale = float(np.max(np.abs(original[valid])))
    if scale == 0:
        raise NMRNextError("AUTOPHASE_NO_SIGNAL", "Zero spectrum cannot determine phase")
    z = original/scale
    candidate_scale = max(_phase_candidate_scale(z.real[valid]), _phase_candidate_scale(z.imag[valid])) if inclusion_mask is None else _masked_phase_variation(z[None], valid)
    magnitude = np.abs(z)
    centers, properties = signal.find_peaks(magnitude, prominence=max(min_snr*candidate_scale, .02*float(magnitude[valid].max())), distance=5)
    selected = valid[centers]
    centers = centers[selected]
    properties["prominences"] = properties["prominences"][selected]
    if centers.size == 0:
        raise NMRNextError("AUTOPHASE_NO_SIGNAL", "No resolved high-prominence resonance found")
    widths = signal.peak_widths(magnitude, centers, rel_height=.5)[0] / (2*math.sqrt(3))
    ranked = sorted(range(len(centers)), key=lambda i: (-properties["prominences"][i], int(centers[i])))
    lines, rejected = [], []
    orientation = quadrature_index_orientation(dataset)[a.id]
    # A fixed candidate budget also bounds rejected-line nonlinear fits.
    for index in ranked[:32]:
        center, width = int(centers[index]), float(widths[index])
        radius = max(8, int(math.ceil(width*5)))
        # Reject unresolved/edge lines; a local line model is not deconvolution.
        if width < .8 or radius > a.size//8 or center-radius < 0 or center+radius >= a.size:
            continue
        if not valid[center-radius:center+radius+1].all():
            rejected.append({"points": [center], "reason": "fit_window_outside_inclusion_mask"})
            continue
        if any(abs(center-other) < radius for other in centers if other != center):
            continue
        points = np.linspace(center-radius, center+radius, min(129, 2*radius+1))
        samples = np.interp(points, np.arange(a.size), z.real) + 1j*np.interp(points, np.arange(a.size), z.imag)
        local = (points-center)/radius
        background = (samples[0]+samples[-1])/2
        amplitude = z[center]-background
        initial = [center, width, amplitude.real, amplitude.imag, background.real, background.imag, 0., 0.]
        def residual(params):
            c, w, ar, ai, br, bi, sr, si = params
            model = (ar+1j*ai)/(1+1j*orientation*(points-c)/w) + br+1j*bi+(sr+1j*si)*local
            delta = model-samples
            return np.concatenate((delta.real, delta.imag))
        fit = optimize.least_squares(residual, initial,
                    bounds=([center-width*2, .3, -5., -5., -5., -5., -5., -5.],
                            [center+width*2, radius, 5., 5., 5., 5., 5., 5.]),
                    max_nfev=120, ftol=1e-9, xtol=1e-9, gtol=1e-9)
        c, w, ar, ai, br, bi, sr, si = fit.x
        amp = math.hypot(ar, ai)
        error = float(np.sqrt(np.mean(residual(fit.x)**2)))
        if not fit.success or not max(min_snr*candidate_scale, .01) <= amp < 5. or error/amp > .05:
            continue
        lines.append({"center": c, "width": w, "amplitude": amp, "phase": math.degrees(math.atan2(ai, ar)),
                      "points": points, "samples": samples, "local": local, "background": [br, bi, sr, si],
                      "relative_residual": error/amp, "radius": radius})
        if len(lines) >= max_peaks:
            break
    needed = 2 if first_order else 1
    if len(lines) < needed:
        raise NMRNextError("AUTOPHASE_INSUFFICIENT_LINES", f"Need {needed} isolated Lorentzian-like strong lines; found {len(lines)}", details={"accepted_lines": len(lines)})
    lines.sort(key=lambda line: line["center"])
    coverage = (lines[-1]["center"]-lines[0]["center"])/a.size
    if first_order and coverage < .15:
        raise NMRNextError("AUTOPHASE_UNDERDETERMINED", "Accepted lines span less than 15% of spectrum; first-order phase is underdetermined")
    positions = np.array([(line["center"]-pivot)/a.size for line in lines])
    phases = np.rad2deg(np.unwrap(np.deg2rad([line["phase"] for line in lines])))
    if first_order:
        slope, intercept = np.polyfit(positions, phases, 1)
    else:
        slope, intercept = 0., float(np.mean(phases))
    if first_order and abs(slope) >= max_p1:
        raise NMRNextError("AUTOPHASE_BOUND_REACHED", "Estimated first-order phase exceeds the allowed range")
    initial = [intercept, slope] if first_order else [intercept]
    lower, upper = ([intercept-90, -max_p1], [intercept+90, max_p1]) if first_order else ([intercept-90], [intercept+90])
    count_global = len(initial)
    for line in lines:
        initial.extend([line["center"], line["width"], line["amplitude"], *line["background"]])
        lower.extend([line["center"]-line["width"], .3, 0., -5., -5., -5., -5.])
        upper.extend([line["center"]+line["width"], line["radius"], 5., 5., 5., 5., 5.])
    def joint_residual(params, by_line=False):
        p0, p1 = params[0], params[1] if first_order else 0.
        residuals = []
        for index, line in enumerate(lines):
            c, w, amp, br, bi, sr, si = params[count_global+7*index:count_global+7*(index+1)]
            model = amp/(1+1j*orientation*(line["points"]-c)/w)*np.exp(1j*np.deg2rad(p0+p1*(line["points"]-pivot)/a.size))
            delta = model + br+1j*bi+(sr+1j*si)*line["local"] - line["samples"]
            residuals.append(np.concatenate((delta.real, delta.imag)))
        return residuals if by_line else np.concatenate(residuals)
    fit = optimize.least_squares(joint_residual, initial, bounds=(lower, upper), max_nfev=120,
                                ftol=1e-9, xtol=1e-9, gtol=1e-9)
    p0, p1 = float(fit.x[0]), float(fit.x[1]) if first_order else 0.
    if not fit.success:
        raise NMRNextError("AUTOPHASE_NOT_CONVERGED", "Joint phase model failed to converge")
    if first_order and abs(p1) > .98*max_p1:
        raise NMRNextError("AUTOPHASE_BOUND_REACHED", "First-order phase reached the fitting bound")
    rms = float(np.sqrt(np.mean(joint_residual(fit.x)**2)))
    per_line_rms = [float(np.sqrt(np.mean(delta**2))/line["amplitude"])
                    for delta, line in zip(joint_residual(fit.x, True), lines)]
    if max(per_line_rms) > .05:
        raise NMRNextError("AUTOPHASE_MODEL_MISMATCH", "Accepted lines do not share a consistent positive phase model",
                           details={"per_line_relative_rms": per_line_rms, "acceptance_limit": .05})
    operation = {"op": "phase", "axis": a.id, "p0": float((-p0+180)%360-180), "p1": -p1, "pivot": pivot}
    result = run_operations(dataset, [operation], backend=backend, threads=threads, memory_bytes=memory_bytes)
    report = _report(dataset, "autophase", {"axis": a.id, "pivot": pivot, "pivot_unit": "points",
                    "first_order": first_order, "max_p1": max_p1, "max_peaks": max_peaks, "min_snr": min_snr,
                    "min_snr_semantics": "phase candidate variation multiplier, not amplitude SNR",
                    "method": "isolated_positive_complex_lorentzian_joint_phase_with_local_affine_background",
                    "inclusion_mask_digest": _mask_digest(valid, inclusion_mask)})
    report.update(operation=operation, result_dataset_digest=dataset_digest(result),
                  quadrature_index_orientation={a.id: orientation},
                  selection=_selection_record(valid), rejected_candidates=rejected,
                  objective={"name": "complex_least_squares", "normalized_rms": rms,
                             "per_line_relative_rms": per_line_rms, "acceptance_limit": .05,
                             "initial_rms": float(np.sqrt(np.mean(joint_residual(np.array(initial))**2)))},
                  convergence={"success": True, "evaluations": int(fit.nfev)},
                  noise={"sigma": float(max(_noise(z.real)["sigma"], _noise(z.imag)["sigma"])*scale),
                    "method": "maximum real/imag amplitude_MAD_normal",
                    "assumption": "global marginal scale may include dispersive tails/baseline; not used for phase candidate screening"},
                  candidate_screening={"scale": float(candidate_scale*scale),
                    "method": "maximum real/imag adjacent_difference_MAD_normal/sqrt(2)",
                    "not_amplitude_noise_sd": True,
                    "assumption": "heuristic seed screen; correlated samples lower this scale; fit residuals govern final acceptance"},
                  lines=[{key: float(line[key]) for key in ("center", "width", "amplitude", "phase", "relative_residual")} for line in lines],
                  line_span_fraction=float(coverage),
                  fit_units={"center": "points", "width": "HWHM points", "phase": "degrees before correction",
                             "amplitude": "normalized intensity", "intensity_scale": scale,
                             "reported_lines": "initial independent fits used to seed shared phase refinement"})
    report["flags"] = ["inspect_phase", "positive_lorentzian_line_assumption", "background_fitted_for_estimation_only"]
    if inclusion_mask is not None:
        report["flags"].append("excluded_regions_not_phase_certified")
    if not first_order:
        report["flags"].append("first_order_not_estimated")
    return result, report


def _tensor_line(z0, z1):
    """Canonical quadratures [RR, IR, RI, II], with independent imaginary units."""
    return np.array([np.outer(z0.real, z1.real).ravel(),
                     np.outer(z0.imag, z1.real).ravel(),
                     np.outer(z0.real, z1.imag).ravel(),
                     np.outer(z0.imag, z1.imag).ravel()])


def _hypercomplex_design(line):
    # Multiplication by 1, i0, i1, i0*i1. In particular (i0*i1)^2 = +1.
    return np.stack([line, line[[1, 0, 3, 2]] * np.array([-1, 1, -1, 1])[:, None],
                     line[[2, 3, 0, 1]] * np.array([-1, -1, 1, 1])[:, None],
                     line[[3, 2, 1, 0]] * np.array([1, -1, -1, 1])[:, None]], axis=-1)


def _autophase_2d(dataset, *, axis, pivot, first_order, max_p1, max_peaks,
                  min_snr, backend, threads, inclusion_mask=None, memory_bytes=None):
    """Bounded full-quadrature fit of isolated, positive, separable 2D lines.

    This is an original extension of the isolated-line estimator, not an
    implementation of a published universal 2D autophase algorithm. See Heuer,
    doi:10.1016/0022-2364(91)90189-Z (line symmetry), and NMRPipe TP/PS:
    https://spin.niddk.nih.gov/bax-apps/NMRPipe/ref/nmrpipe/tp.html
    Both independent imaginary units and their cross term enter every fit.
    Norms locate seeds only. Affine backgrounds are eliminated by orthogonal
    projection; they are never removed from the returned data. Mixed-sign,
    antiphase, overlapping, non-Lorentzian or insufficiently sampled lines are
    outside this model. Its residual gate is not a universal model classifier.
    """
    axes = dataset.axes
    ids = [a.id for a in axes]
    if set(dataset.complex_axes) != set(ids):
        raise NMRNextError("UNSUPPORTED_AUTOPHASE", "2D automatic phase requires both independent complex axes (four quadratures)")
    if axis is not None:
        raise NMRNextError("INVALID_PARAMETER", "2D automatic phase estimates both axes together; omit axis")
    if not isinstance(first_order, bool):
        raise NMRNextError("INVALID_PARAMETER", "first_order must be boolean")
    if isinstance(max_peaks, bool) or not isinstance(max_peaks, int) or not 1 <= max_peaks <= 8:
        raise NMRNextError("INVALID_PARAMETER", "max_peaks must be an integer in [1,8]")
    max_p1 = _positive(max_p1, "max_p1")
    if max_p1 > 180:
        raise NMRNextError("INVALID_PARAMETER", "max_p1 is limited to 180 degrees; correct large ramps explicitly")
    min_snr = _positive(min_snr, "min_snr")
    if any(a.size < 64 for a in axes):
        raise NMRNextError("INSUFFICIENT_DATA", "2D automatic phase needs at least 64 points on each axis")
    if pivot is not None and (not isinstance(pivot, dict) or set(pivot) != set(ids)):
        raise NMRNextError("INVALID_PARAMETER", "2D pivot must map every axis ID to a zero-based point")
    pivots = [(a.size-1)/2 if pivot is None else finite(pivot[a.id], "pivot") for a in axes]
    if any(not 0 <= p <= a.size-1 for a, p in zip(axes, pivots)):
        raise NMRNextError("INVALID_PARAMETER", "pivot must be an in-range zero-based point")
    bits = [1 << dataset.complex_axes.index(a.id) for a in axes]
    canonical_channels = [0, bits[0], bits[1], bits[0] | bits[1]]
    valid = _valid_mask(inclusion_mask, tuple(a.size for a in axes))
    scale = float(np.max(np.abs(dataset.data[:, valid])))
    if not scale:
        raise NMRNextError("AUTOPHASE_NO_SIGNAL", "Zero spectrum cannot determine phase")
    z = dataset.data[canonical_channels].astype(float)/scale
    magnitude = np.sqrt(np.sum(z*z, axis=0))
    variation = max(float(np.median(np.abs(d-np.median(d))) / (_MAD_NORMAL*math.sqrt(2)))
                    for dim in (1, 2) for d in [np.diff(z, axis=dim)])
    if inclusion_mask is not None:
        variation = _masked_phase_variation(z, valid)
    cut = max(min_snr*variation, .02*float(magnitude[valid].max()))
    maxima = (magnitude == ndimage.maximum_filter(magnitude, size=5)) & (magnitude > cut) & valid
    centers = np.argwhere(maxima)
    ranked = sorted(centers.tolist(), key=lambda c: (-float(magnitude[tuple(c)]), c))[:32]
    lines, rejected = [], []
    orientation = quadrature_index_orientation(dataset)
    orientations = [orientation[a.id] for a in axes]
    for center in ranked:
        widths, radii = [], []
        for dim in range(2):
            trace = magnitude[:, center[1]] if dim == 0 else magnitude[center[0], :]
            peaks, _ = signal.find_peaks(trace)
            if center[dim] not in peaks:
                break
            width = float(signal.peak_widths(trace, [center[dim]], rel_height=.5)[0][0]/(2*math.sqrt(3)))
            widths.append(width)
            radii.append(max(6, int(math.ceil(4*width))))
        if len(widths) != 2 or any(w < .7 or r > a.size//6 or c-r < 0 or c+r >= a.size
                                  for c, w, r, a in zip(center, widths, radii, axes)):
            rejected.append({"points": center, "reason": "edge_or_unresolved_width"})
            continue
        window = tuple(slice(c-r, c+r+1) for c, r in zip(center, radii))
        if not valid[window].all():
            rejected.append({"points": center, "reason": "fit_window_outside_inclusion_mask"})
            continue
        if any(other != center and all(abs(other[d]-center[d]) < radii[d] for d in range(2))
               for other in ranked):
            rejected.append({"points": center, "reason": "overlapping_candidate"})
            continue
        # Integer subsampling avoids interpolation-induced line-shape changes.
        points = [np.unique(np.rint(np.linspace(c-r, c+r, min(21, 2*r+1))).astype(int))
                  for c, r in zip(center, radii)]
        sample = z[:, points[0][:, None], points[1][None, :]].reshape(4, -1)
        u, v = np.meshgrid((points[0]-center[0])/radii[0],
                           (points[1]-center[1])/radii[1], indexing="ij")
        background, _ = np.linalg.qr(np.stack([np.ones(u.size), u.ravel(), v.ravel()], axis=1))
        def detrend(values):
            return values - (values @ background) @ background.T
        observed = detrend(sample)
        def local_model(parameters):
            factors = [1/(1+1j*o*(p-c)/w) for o, p, c, w in
                       zip(orientations, points, parameters[::2], parameters[1::2])]
            design = _hypercomplex_design(_tensor_line(*factors))
            design = np.stack([detrend(design[:, :, i]).ravel() for i in range(4)], axis=1)
            coefficient = np.linalg.lstsq(design, observed.ravel(), rcond=None)[0]
            return design @ coefficient-observed.ravel(), coefficient
        initial = [center[0], widths[0], center[1], widths[1]]
        lower = [center[0]-2*widths[0], .3, center[1]-2*widths[1], .3]
        upper = [center[0]+2*widths[0], radii[0], center[1]+2*widths[1], radii[1]]
        fit = optimize.least_squares(lambda p: local_model(p)[0], initial, bounds=(lower, upper),
                                     max_nfev=80, ftol=1e-8, xtol=1e-8, gtol=1e-8)
        residual, coefficient = local_model(fit.x)
        left, singular, right = np.linalg.svd(coefficient.reshape(2, 2, order="F"))
        amplitude = float(singular[0])
        relative_rms = float(np.sqrt(np.mean(residual**2))/max(amplitude, 1e-15))
        if (not fit.success or amplitude < cut or relative_rms > .04 or
                singular[1] > .08*amplitude):
            rejected.append({"points": center, "reason": "nonseparable_or_line_model_residual",
                             "relative_rms": relative_rms})
            continue
        phase0 = float(np.rad2deg(np.arctan2(left[1, 0], left[0, 0])))
        phase1 = float(np.rad2deg(np.arctan2(right[0, 1], right[0, 0])))
        lines.append({"points": points, "parameters": fit.x, "amplitude": amplitude,
                      "phases": [phase0, phase1], "sample": sample, "background": background,
                      "radii": radii, "relative_rms": relative_rms})
        if len(lines) == max_peaks:
            break
    needed = 2 if first_order else 1
    if len(lines) < needed:
        raise NMRNextError("AUTOPHASE_INSUFFICIENT_LINES", f"Need {needed} isolated separable hypercomplex lines; found {len(lines)}",
                           details={"accepted_lines": len(lines), "rejected_candidates": rejected})
    locations = np.array([line["parameters"][::2] for line in lines])
    spans = np.ptp(locations, axis=0)/np.array([a.size for a in axes])
    if first_order and np.any(spans < .15):
        raise NMRNextError("AUTOPHASE_UNDERDETERMINED", "Accepted cross-peaks must span at least 15% of EACH axis to estimate first-order phase",
                           details={"span_fraction": dict(zip(ids, spans.tolist()))})
    gaps = np.max(np.diff(np.sort(locations, axis=0), axis=0), axis=0) / np.array([a.size for a in axes]) if len(lines) > 1 else np.zeros(2)
    if first_order and np.any(gaps*max_p1 >= 90):
        raise NMRNextError("AUTOPHASE_UNDERDETERMINED", "Cross-peak gaps permit multiple 180-degree phase branches within max_p1; reduce max_p1 using prior knowledge or supply more lines",
                           details={"maximum_gap_fraction": dict(zip(ids, gaps.tolist()))})
    # A positive tensor is unchanged by simultaneous 180-degree rotations.
    # Fix the first axis branch, then recover the second with its positive sign.
    phases = np.array([line["phases"] for line in lines])
    order = np.argsort(locations[:, 0])
    unwrapped = np.rad2deg(np.unwrap(np.deg2rad(2*phases[order, 0])))/2
    phases[order, 0] = unwrapped
    for index, line in enumerate(lines):
        phases[index, 1] += 180*round((line["phases"][0]-phases[index, 0])/180)
    order = np.argsort(locations[:, 1])
    phases[order, 1] = np.rad2deg(np.unwrap(np.deg2rad(phases[order, 1])))
    seed = []
    for dim, a in enumerate(axes):
        positions = (locations[:, dim]-pivots[dim])/a.size
        slope, intercept = np.polyfit(positions, phases[:, dim], 1) if first_order else (0., float(np.mean(phases[:, dim])))
        if abs(slope) >= max_p1:
            raise NMRNextError("AUTOPHASE_BOUND_REACHED", "Phase slopes exceed bounds; mixed-sign or unsupported cross-peaks may be present")
        seed.extend([intercept, slope] if first_order else [intercept])
    global_count = len(seed)
    lower, upper = [], []
    for dim in range(2):
        offset = dim*(2 if first_order else 1)
        lower.extend([seed[offset]-90, -max_p1] if first_order else [seed[offset]-90])
        upper.extend([seed[offset]+90, max_p1] if first_order else [seed[offset]+90])
    for line in lines:
        c0, w0, c1, w1 = line["parameters"]
        seed.extend([c0, w0, c1, w1, line["amplitude"]])
        lower.extend([c0-w0, .3, c1-w1, .3, .1*line["amplitude"]])
        upper.extend([c0+w0, line["radii"][0], c1+w1, line["radii"][1], 3*line["amplitude"]])
    def joint_residual(parameters, by_line=False):
        errors = []
        for index, line in enumerate(lines):
            local = parameters[global_count+5*index:global_count+5*(index+1)]
            factors = []
            for dim, a in enumerate(axes):
                offset = dim*(2 if first_order else 1)
                p0, p1 = parameters[offset], parameters[offset+1] if first_order else 0.
                points = line["points"][dim]
                c, w = local[2*dim:2*dim+2]
                factors.append(np.exp(1j*np.deg2rad(p0+p1*(points-pivots[dim])/a.size)) /
                               (1+1j*orientations[dim]*(points-c)/w))
            delta = local[4]*_tensor_line(*factors)-line["sample"]
            background = line["background"]
            delta -= (delta @ background) @ background.T
            errors.append(delta.ravel()/line["amplitude"])
        return errors if by_line else np.concatenate(errors)
    fit = optimize.least_squares(joint_residual, seed, bounds=(lower, upper), max_nfev=120,
                                 ftol=1e-8, xtol=1e-8, gtol=1e-8)
    if not fit.success:
        raise NMRNextError("AUTOPHASE_NOT_CONVERGED", "Shared two-axis phase model did not converge")
    errors = [float(np.sqrt(np.mean(e*e))) for e in joint_residual(fit.x, True)]
    if max(errors) > .04:
        raise NMRNextError("AUTOPHASE_MODEL_MISMATCH", "Positive separable cross-peaks do not share a consistent two-axis phase ramp",
                           details={"per_line_relative_rms": errors,
                                    "line_centers_points": locations.tolist()})
    p0s, p1s = [], []
    for dim in range(2):
        offset = dim*(2 if first_order else 1)
        p0s.append(float(-fit.x[offset]))
        p1s.append(float(-fit.x[offset+1]) if first_order else 0.)
    if first_order and any(abs(p) > .98*max_p1 for p in p1s):
        raise NMRNextError("AUTOPHASE_BOUND_REACHED", "First-order phase reached a fitting bound")
    canonical0 = (p0s[0]+90)%180-90
    p0s[1] += p0s[0]-canonical0
    p0s = [canonical0, (p0s[1]+180)%360-180]
    operations = [{"op": "phase", "axis": a.id, "p0": p0, "p1": p1, "pivot": p}
                  for a, p0, p1, p in zip(axes, p0s, p1s, pivots)]
    result = run_operations(dataset, operations, backend=backend, threads=threads, memory_bytes=memory_bytes)
    report = _report(dataset, "autophase", {"axes": ids, "pivot": dict(zip(ids, pivots)),
                     "pivot_unit": "points", "first_order": first_order, "max_p1": max_p1,
                     "max_peaks": max_peaks, "min_snr": min_snr,
                     "min_snr_semantics": "phase candidate variation multiplier, not amplitude SNR",
                     "method": "isolated_positive_separable_hypercomplex_lorentzian_joint_two_axis_phase",
                     "inclusion_mask_digest": _mask_digest(valid, inclusion_mask)})
    report.update(operations=operations, result_dataset_digest=dataset_digest(result),
                  quadrature_index_orientation=orientation,
                  selection=_selection_record(valid),
                  axis_results={a.id: {"operation": op, "line_span_fraction": float(span),
                                      "maximum_gap_fraction": float(gap)}
                                for a, op, span, gap in zip(axes, operations, spans, gaps)},
                  objective={"name": "full_four_quadrature_relative_least_squares",
                             "normalized_rms": float(np.sqrt(np.mean(joint_residual(fit.x)**2))),
                             "per_line_relative_rms": errors, "acceptance_limit": .04},
                  convergence={"success": True, "evaluations": int(fit.nfev)},
                  lines=[{"centers": dict(zip(ids, fit.x[global_count+5*i:global_count+5*i+4:2].tolist())),
                          "widths": dict(zip(ids, fit.x[global_count+5*i+1:global_count+5*i+4:2].tolist())),
                          "amplitude": float(fit.x[global_count+5*i+4]), "relative_rms": errors[i]}
                         for i in range(len(lines))],
                  rejected_candidates=rejected,
                  candidate_screening={"scale": variation*scale, "not_amplitude_noise_sd": True,
                                       "method": "adjacent_difference_MAD; four_quadrature_norm_seeds_only"},
                  phase_gauge={"ambiguity": "simultaneous 180-degree shifts on both axes leave all four quadratures unchanged",
                               "canonical_branch": "first logical axis correction p0 in [-90,90); second in [-180,180)"},
                  resource_bounds={"candidate_fits": 32, "accepted_lines": 8, "samples_per_axis_per_line": 21,
                                   "local_max_nfev": 80, "joint_max_nfev": 120},
                  fit_units={"center": "zero-based points", "width": "HWHM points", "phase": "correction degrees",
                             "p1": "degrees per N points", "amplitude": "normalized intensity", "intensity_scale": scale})
    report["flags"] = ["inspect_phase", "positive_separable_lorentzian_cross_peak_assumption",
                       "simultaneous_180_degree_axis_ambiguity", "background_fitted_for_estimation_only",
                       "excluded_candidates_not_phase_certified"]
    if not first_order:
        report["flags"].append("first_order_not_estimated")
    if inclusion_mask is not None:
        report["flags"].append("excluded_regions_not_phase_certified")
    return result, report
