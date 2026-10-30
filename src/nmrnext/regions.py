"""Bounded analysis views of a complete frequency-domain spectrum.

The parent is immutable; only the selected rectangular samples are copied.
Reports retain their ordinary local grid and add an explicit parent-grid link.
"""
from dataclasses import replace
import math

from .analysis import MAX_POINTS, dataset_digest, integrate_regions, pick_peaks
from .errors import NMRNextError
from .model import Dataset, finite
from .operations import quadrature_index_orientation


def _selection(parent, bounds, unit):
    if not isinstance(parent, Dataset):
        raise NMRNextError("INVALID_DATASET", "Analysis region requires a Dataset")
    if len(parent.axes) not in (1, 2) or any(a.role != "spectral" or a.domain != "frequency" for a in parent.axes):
        raise NMRNextError("UNSUPPORTED_ANALYSIS_PROFILE", "Analysis regions require frequency-domain spectral 1D/2D data")
    if not isinstance(bounds, dict) or set(bounds) != {a.id for a in parent.axes}:
        raise NMRNextError("INVALID_REGION", "Region must bound every parent axis ID")
    if unit not in ("points", "Hz", "ppm"):
        raise NMRNextError("INVALID_UNIT", "Region unit must be points, Hz or ppm")
    quadrature_index_orientation(parent)
    starts, stops, canonical_bounds = {}, {}, {}
    for axis in parent.axes:
        pair = bounds[axis.id]
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise NMRNextError("INVALID_REGION", "Each region bound must be an inclusive pair")
        left, right = (finite(v, "region bound") for v in pair)
        canonical_bounds[axis.id] = [left, right]
        if unit == "points":
            point_left, point_right = left, right
        elif unit == "Hz":
            point_left, point_right = (left-axis.start)/axis.step, (right-axis.start)/axis.step
        else:
            if axis.observation_mhz is None or axis.reference_ppm is None:
                raise NMRNextError("UNAVAILABLE_COORDINATES", f"Axis {axis.id} has no ppm calibration")
            point_left = (axis.reference_hz + (left-axis.reference_ppm)*axis.observation_mhz-axis.start)/axis.step
            point_right = (axis.reference_hz + (right-axis.reference_ppm)*axis.observation_mhz-axis.start)/axis.step
        lo, hi = sorted((point_left, point_right))
        if not all(math.isfinite(v) for v in (lo, hi)) or lo < -1e-9 or hi > axis.size-1+1e-9:
            raise NMRNextError("INVALID_REGION", "Region bounds must be inside the parent sample centers")
        start, stop = max(0, math.ceil(lo-1e-9)), min(axis.size, math.floor(hi+1e-9)+1)
        if stop-start < 2:
            raise NMRNextError("INSUFFICIENT_DATA", "Each selected axis needs at least two sample centers")
        starts[axis.id], stops[axis.id] = start, stop
    if math.prod(stops[a.id]-starts[a.id] for a in parent.axes) > MAX_POINTS:
        raise NMRNextError("ANALYSIS_SIZE_LIMIT", f"Analysis region exceeds {MAX_POINTS} points per component")
    return starts, stops, canonical_bounds


def select_analysis_region(parent, bounds, *, unit="points"):
    """Return a bounded Dataset and its exact parent-grid map.

    Bounds are inclusive sample-center coordinates, in points, Hz or calibrated
    ppm. Only centers inside the requested interval are selected. ``stop`` in
    the map is exclusive; local index ``i`` means parent index ``start+i``.
    """
    starts, stops, canonical_bounds = _selection(parent, bounds, unit)
    axes = tuple(replace(a, size=stops[a.id]-starts[a.id],
                         start=a.start+starts[a.id]*a.step) for a in parent.axes)
    slices = (slice(None), *(slice(starts[a.id], stops[a.id]) for a in parent.axes))
    # Dataset's immutable bytes owner copies exactly this bounded slice.
    region = Dataset(parent.data[slices], axes, parent.complex_axes,
                     parent.metadata, parent.provenance)
    mapping = {"schema_version": "1", "parent_dataset_digest": dataset_digest(parent),
               "parent_axes": [a.to_dict() for a in parent.axes],
               "parent_start": starts, "parent_stop": stops,
               "region_axes": [a.to_dict() for a in region.axes],
               "region_dataset_digest": dataset_digest(region),
               "bounds": canonical_bounds, "unit": unit}
    return region, mapping


def _coordinates(parent, points):
    result = {}
    for axis in parent.axes:
        hz = float(axis.start + points[axis.id]*axis.step)
        values = {"Hz": hz}
        if axis.observation_mhz is not None and axis.reference_ppm is not None:
            values["ppm"] = float(axis.reference_ppm+(hz-axis.reference_hz)/axis.observation_mhz)
        result[axis.id] = values
    return result


def pick_peaks_in_region(parent, bounds, *, unit="points", **peak_options):
    """Pick on a bounded view; peak points remain local with parent counterparts."""
    region, mapping = select_analysis_region(parent, bounds, unit=unit)
    report = pick_peaks(region, **peak_options)
    for peak in report["peaks"]:
        points = {axis.id: mapping["parent_start"][axis.id]+peak["points"][axis.id]
                  for axis in parent.axes}
        peak["parent_points"] = points
        peak["parent_coordinates"] = _coordinates(parent, points)
    report["parent_region"] = mapping
    report["flags"].append("region_edge_flags_refer_to_selected_view")
    return report


def integrate_regions_in_region(parent, bounds, regions, *, unit="points", **integration_options):
    """Integrate local boxes in a bounded view with parent-grid point bounds."""
    region, mapping = select_analysis_region(parent, bounds, unit=unit)
    report = integrate_regions(region, regions, **integration_options)
    for item in report["regions"]:
        parent_bounds = {axis.id: [mapping["parent_start"][axis.id]+v
                                   for v in item["point_bounds"][axis.id]] for axis in parent.axes}
        item["parent_point_bounds"] = parent_bounds
        item["parent_coordinate_bounds"] = {axis.id: {
            key: [_coordinates(parent, {a.id: parent_bounds[a.id][edge] for a in parent.axes})[axis.id][key]
                  for edge in (0, 1)] for key in item["coordinate_bounds"][axis.id]}
            for axis in parent.axes}
    report["parent_region"] = mapping
    report["flags"].append("region_edge_flags_refer_to_selected_view")
    return report


def verify_region_mapping(parent, mapping):
    """Rebuild a saved selection and reject stale or edited parent linkage."""
    if not isinstance(mapping, dict) or set(mapping) != {"schema_version", "parent_dataset_digest", "parent_axes",
        "parent_start", "parent_stop", "region_axes", "region_dataset_digest", "bounds", "unit"}:
        raise NMRNextError("INVALID_REGION", "Saved region mapping fields are missing or unknown")
    region, expected = select_analysis_region(parent, mapping["bounds"], unit=mapping["unit"])
    if mapping != expected:
        raise NMRNextError("STALE_ANALYSIS", "Saved region mapping differs from the current parent spectrum")
    return region
