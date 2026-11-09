"""Reusable labels and bounded fixed-region relative measurements.

Templates record their origin, not reusable digest-bound analysis reports.
Every target is resolved independently; no alignment, fitting or recentering is
performed. Item/source/run identity belongs to the batch service, not this API.
"""
from dataclasses import fields, replace
import json
import math
import os
from pathlib import Path
import re

from .analysis import (_overlaps, _region_bounds, dataset_digest,
                       integrate_regions, pick_peaks)
from .errors import NMRNextError
from .model import Axis, Dataset, finite, json_copy
from .operations import quadrature_index_orientation
from .regions import _coordinates, _selection


MAX_ASSIGNMENTS = 1000
MAX_TEMPLATE_BYTES = 4 * 1024 * 1024


def _invalid(message):
    raise NMRNextError("INVALID_ASSIGNMENT_TEMPLATE", message)


def _keys(value, required, *, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        _invalid("Template fields are missing or unknown")


def _validate_template(template):
    t = json_copy(template, "assignment template")
    _keys(t, {"schema_version", "kind", "reference", "assignments"}, optional={"analysis_channel"})
    if t["schema_version"] != "1" or t["kind"] != "assignment_template":
        _invalid("Expected assignment_template schema 1")
    ref = t["reference"]
    _keys(ref, {"dataset_digest", "axes", "complex_axes", "quadrature_index_orientation"})
    if not isinstance(ref["dataset_digest"], str) or re.fullmatch(r"[0-9a-f]{64}", ref["dataset_digest"]) is None:
        _invalid("Reference dataset_digest must be SHA256")
    if not isinstance(ref["axes"], list) or not 1 <= len(ref["axes"]) <= 2:
        _invalid("Templates require 1D/2D reference axes")
    axes = []
    for record in ref["axes"]:
        _keys(record, {f.name for f in fields(Axis)})
        axis = Axis(**record)
        if axis.role != "spectral" or axis.domain != "frequency":
            _invalid("Reference axes must be frequency-domain spectral axes")
        axes.append(axis)
    bits = ref["complex_axes"]
    if not isinstance(bits, list):
        _invalid("complex_axes must be a list")
    Dataset._layout(axes, bits)  # Layout validation without allocating samples.
    orientation = ref["quadrature_index_orientation"]
    if (not isinstance(orientation, dict) or set(orientation) != set(bits) or
            any(type(v) is not int or v not in (-1, 1) for v in orientation.values())):
        _invalid("Reference quadrature orientation must declare each complex bit")
    channel = t.setdefault("analysis_channel", 0)
    if type(channel) is not int or not 0 <= channel < 2 ** len(bits):
        raise NMRNextError("INVALID_CHANNEL", "analysis_channel must select an existing reference component")
    assignments = t["assignments"]
    if not isinstance(assignments, list) or not 1 <= len(assignments) <= MAX_ASSIGNMENTS:
        _invalid(f"Expected 1..{MAX_ASSIGNMENTS} assignments")
    ids = set()
    for a in assignments:
        _keys(a, {"assignment_id", "label", "sign", "unit", "search_bounds", "integration_bounds", "threshold"})
        identity = a["assignment_id"]
        if not isinstance(identity, str) or not identity.strip() or identity in ids:
            _invalid("Assignment IDs must be nonempty and unique")
        ids.add(identity)
        if not isinstance(a["label"], str) or not a["label"].strip():
            _invalid("Assignment label must be nonempty text")
        if a["sign"] not in ("positive", "negative") or a["unit"] not in ("points", "Hz", "ppm"):
            _invalid("Assignment sign/unit is unsupported")
        if finite(a["threshold"], "threshold") < 0:
            _invalid("Threshold must be nonnegative")
        for name in ("search_bounds", "integration_bounds"):
            bounds = a[name]
            _keys(bounds, {axis.id for axis in axes})
            for pair in bounds.values():
                if not isinstance(pair, list) or len(pair) != 2:
                    _invalid("Bounds must be inclusive pairs")
                if finite(pair[0], name) == finite(pair[1], name):
                    _invalid("Bounds must have positive width")
    return t, axes


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid("Duplicate JSON object field")
        result[key] = value
    return result


def load_assignment_template(path: Path) -> dict:
    """Load a strict finite JSON sidecar; normalize the default channel to 0."""
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_TEMPLATE_BYTES + 1)
        if len(raw) > MAX_TEMPLATE_BYTES:
            _invalid("Template exceeds the 4 MiB sidecar limit")
        t = json.loads(raw, object_pairs_hook=_unique_object)
    except (OSError, ValueError, UnicodeError) as exc:
        if isinstance(exc, NMRNextError):
            raise
        raise NMRNextError("INVALID_ASSIGNMENT_TEMPLATE", "Cannot read assignment template JSON") from exc
    return _validate_template(t)[0]


def save_assignment_template(template: dict, path: Path) -> dict:
    """Validate before exclusively creating a new sidecar; never overwrite."""
    t, _ = _validate_template(template)
    raw = (json.dumps(t, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
    if len(raw) > MAX_TEMPLATE_BYTES:
        _invalid("Template exceeds the 4 MiB sidecar limit")
    try:
        with Path(path).open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise NMRNextError("OUTPUT_EXISTS", "Assignment template destination already exists") from exc
    except OSError as exc:
        raise NMRNextError("ASSIGNMENT_WRITE_FAILED", "Cannot write assignment template") from exc
    return t


def _validate_target(dataset, template, axes, axis_map):
    if not isinstance(dataset, Dataset):
        raise NMRNextError("INVALID_DATASET", "Assignment measurements require a Dataset")
    if (not isinstance(axis_map, dict) or set(axis_map) != {a.id for a in axes} or
            not all(isinstance(v, str) for v in axis_map.values()) or
            len(set(axis_map.values())) != len(axes) or set(axis_map.values()) != {a.id for a in dataset.axes}):
        raise NMRNextError("INVALID_AXIS_MAP", "Map every reference axis to one distinct target axis")
    for ref in axes:
        target = dataset.axis(axis_map[ref.id])
        if any(getattr(ref, key) != getattr(target, key) for key in ("role", "domain", "unit", "nucleus", "encoding")):
            raise NMRNextError("INCOMPATIBLE_ASSIGNMENT_AXIS", "Mapped axes have incompatible physical semantics")
    ref = template["reference"]
    if tuple(axis_map[b] for b in ref["complex_axes"]) != dataset.complex_axes:
        raise NMRNextError("INCOMPATIBLE_ASSIGNMENT_QUADRATURE", "Mapped quadrature-bit order differs")
    if {axis_map[b]: v for b, v in ref["quadrature_index_orientation"].items()} != quadrature_index_orientation(dataset):
        raise NMRNextError("INCOMPATIBLE_ASSIGNMENT_QUADRATURE", "Mapped quadrature orientation differs")
    if not 0 <= template["analysis_channel"] < dataset.data.shape[0]:
        raise NMRNextError("INVALID_CHANNEL", "analysis_channel does not exist in target")


def _unit_compatibility(dataset, axes, axis_map, unit):
    for ref in axes:
        target = dataset.axis(axis_map[ref.id])
        if unit == "points" and replace(ref, id=target.id) != target:
            raise NMRNextError("INCOMPATIBLE_ASSIGNMENT_GRID", "Points transfer requires the identical explicit mapped grid")
        if unit == "Hz" and any(getattr(ref, k) != getattr(target, k) for k in
                                ("observation_mhz", "reference_ppm", "reference_hz")):
            raise NMRNextError("INCOMPATIBLE_ASSIGNMENT_FRAME", "Hz transfer requires the same observation/reference frame")
        if unit == "ppm" and any(getattr(a, k) is None for a in (ref, target) for k in ("observation_mhz", "reference_ppm")):
            raise NMRNextError("UNAVAILABLE_COORDINATES", "ppm transfer requires calibrated reference and target axes")


def _view(parent, selection):
    starts, stops, _ = selection
    axes = tuple(replace(a, size=stops[a.id] - starts[a.id], start=a.start + starts[a.id] * a.step) for a in parent.axes)
    slices = (slice(None), *(slice(starts[a.id], stops[a.id]) for a in parent.axes))
    return Dataset(parent.data[slices], axes, parent.complex_axes, parent.metadata, parent.provenance)


def measure_assignments(dataset: Dataset, template: dict, *, axis_map: dict[str, str]) -> list[dict]:
    """Measure unique signed matches in fixed boxes, returning one row per label.

    Invalid template/map/channel refuses the call. Target-specific coordinate,
    bounds, overlap or numerical failures become explicit assignment rows.
    Picker/integrator defaults remain unchanged: this policy explicitly uses
    baseline none, min separation one point and a 1000-candidate reporting cap.
    Only bounded regions are copied, including all original quadratures.
    """
    t, axes = _validate_template(template)
    _validate_target(dataset, t, axes, axis_map)
    parent_digest = dataset_digest(dataset)  # Exactly once for this parent/call.
    prepared, integration_regions = [], []
    rows = []
    for a in t["assignments"]:
        search = {axis_map[k]: list(v) for k, v in a["search_bounds"].items()}
        fixed = {axis_map[k]: list(v) for k, v in a["integration_bounds"].items()}
        row = {"assignment_id": a["assignment_id"], "label": a["label"], "sign": a["sign"],
               "status": "failed", "candidate_count": None, "count_truncated": False,
               "coordinates": None, "parent_points": None, "height": None,
               "volume": None, "volume_unit": None, "unit": a["unit"],
               "search_bounds": search, "integration_bounds": fixed,
               "point_bounds": None, "coordinate_bounds": None,
               "policy": {"baseline": "none", "integration_bounds": "fixed",
                          "match": "unique_signed_local_extremum", "min_separation_points": 1.,
                          "max_candidates": 1000, "allow_overlap": False,
                          "analysis_channel": t["analysis_channel"], "axis_map": dict(axis_map)},
               "parent_dataset_digest": parent_digest,
               "warnings": ["relative_integrals_not_absolute_concentration", "no_deconvolution"], "error": None}
        rows.append(row)
        try:
            _unit_compatibility(dataset, axes, axis_map, a["unit"])
            search_selection = _selection(dataset, search, a["unit"])
            region, = _region_bounds(dataset, [{"id": a["assignment_id"], "bounds": fixed, "unit": a["unit"]}])
            # Include both interpolation-support endpoints, before allocation.
            support = {k: [math.floor(lo), math.ceil(hi)] for k, (lo, hi) in region["point_bounds"].items()}
            integration_selection = _selection(dataset, support, "points")
            row["point_bounds"] = region["point_bounds"]
            prepared.append((a, row, search_selection, integration_selection, region))
            integration_regions.append(region)
        except NMRNextError as exc:
            row["error"] = exc.to_dict()
    overlaps = _overlaps(integration_regions)
    for a, row, selection, support, region in prepared:
        try:
            if overlaps[a["assignment_id"]]:
                raise NMRNextError("OVERLAPPING_REGIONS", "Fixed assignment integration bounds overlap", details={"overlaps": overlaps[a["assignment_id"]]})
            view = _view(dataset, selection)
            picked = pick_peaks(view, channel=t["analysis_channel"], sign=a["sign"],
                                threshold=a["threshold"], baseline="none", max_peaks=1000)
            peaks = picked["peaks"]
            truncated = "max_peaks_reached" in picked["flags"]
            row.update(candidate_count=len(peaks), count_truncated=truncated)
            row["warnings"].extend(picked["flags"])
            if not peaks:
                row["status"] = "not_found"
                continue
            if len(peaks) != 1 or truncated:
                row["status"] = "ambiguous"
                continue
            peak = peaks[0]
            points = {axis.id: selection[0][axis.id] + peak["points"][axis.id] for axis in dataset.axes}
            integral_view = _view(dataset, support)
            local = {k: [v - support[0][k] for v in pair] for k, pair in region["point_bounds"].items()}
            result = integrate_regions(integral_view, [{"id": a["assignment_id"], "bounds": local, "unit": "points"}], channel=t["analysis_channel"])
            integral, = result["regions"]
            row.update(status="measured", coordinates=_coordinates(dataset, points), parent_points=points,
                       height=peak["intensity"], volume=integral["integral"], volume_unit=integral["integral_unit"],
                       coordinate_bounds=integral["coordinate_bounds"])
            row["warnings"].extend(peak["flags"] + integral["flags"])
            if "spectrum_edge" in peak["flags"]:
                row["warnings"].append("search_region_edge")
        except NMRNextError as exc:
            row.update(status="failed", error=exc.to_dict())
        row["warnings"] = sorted(set(row["warnings"]))
    return rows
