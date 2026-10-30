"""Portable, versioned GUI sessions with a strict runnable workflow sibling."""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import warnings

from .analysis import dataset_digest
from .errors import NMRNextError
from .io import read_dataset, write_dataset
from .model import Dataset, json_copy, positive_int
from .operations import quadrature_index_orientation, run_operations
from .planning import plan_operations
from .regions import select_analysis_region, verify_region_mapping
from .workflow_bundle import _cleanup_published, _file_identity, _publish_new


MAX_SESSION_JSON = 8 * 1024 * 1024
MAX_WORKFLOW_JSON = 1024 * 1024
_FILES = ("source.nmrh5", "workflow.json", "session.json")


def _directory_identity(path):
    try:
        info = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    return (info.st_dev, info.st_ino) if stat.S_ISDIR(info.st_mode) and info.st_ino else None


def _matches_file(path, identity):
    try:
        return identity is not None and _file_identity(path) == identity
    except (FileNotFoundError, OSError):
        return False


def _require_staging(staging, identity):
    if identity is None or _directory_identity(staging) != identity:
        raise NMRNextError("SESSION_STAGING_CHANGED", "Session staging directory changed before writing")


def _write_owned(path, payload, identities):
    with path.open("xb") as stream:
        info = os.fstat(stream.fileno())
        identities[path.name] = (info.st_dev, info.st_ino) if info.st_ino else None
        stream.write(payload)


def _cleanup_staging_owned(staging, directory_identity, identities, hashes):
    warnings = []
    if _directory_identity(staging) != directory_identity or directory_identity is None:
        return [f"Preserved staging directory after its ownership changed: {staging}"]
    for name in _FILES:
        path = staging/name
        if _matches_file(path, identities.get(name)) and hashes.get(name) is not None and _hash_file(path) == hashes[name]:
            try:
                path.unlink()
            except OSError as exc:
                warnings.append(f"Could not remove owned staged {name}: {exc}")
        elif path.exists() or path.is_symlink():
            warnings.append(f"Preserved unowned staged {name}: {path}")
    try:
        staging.rmdir()
    except OSError as exc:
        warnings.append(f"Preserved nonempty or inaccessible staging directory {staging}: {exc}")
    return warnings


def _hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_bytes(value, limit, label):
    try:
        payload = (json.dumps(value, allow_nan=False, indent=2, ensure_ascii=False)+"\n").encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise NMRNextError("INVALID_SESSION", f"{label} must contain finite JSON values") from exc
    if len(payload) > limit:
        raise NMRNextError("SESSION_SIZE_LIMIT", f"{label} exceeds its JSON size limit")
    return payload


def _read_json(path, limit):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise NMRNextError("INVALID_SESSION", f"Session file is missing, linked, or oversized: {path.name}")
    def unique(pairs):
        record = {}
        for key, value in pairs:
            if key in record:
                raise NMRNextError("INVALID_SESSION", f"Duplicate JSON field: {key}")
            record[key] = value
        return record
    def nonfinite(value):
        raise NMRNextError("INVALID_SESSION", f"Nonfinite JSON number: {value}")
    def finite_float(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise NMRNextError("INVALID_SESSION", f"Nonfinite JSON number: {value}")
        return parsed
    try:
        return json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=nonfinite, parse_float=finite_float)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise NMRNextError("INVALID_SESSION", f"Malformed {path.name}") from exc


def _coordinates(axes, points):
    values = {}
    for axis in axes:
        hz = float(axis.start+points[axis.id]*axis.step)
        values[axis.id] = {"Hz": hz}
        if axis.observation_mhz is not None and axis.reference_ppm is not None:
            values[axis.id]["ppm"] = float(axis.reference_ppm+(hz-axis.reference_hz)/axis.observation_mhz)
    return values


def _reports_hash(reports):
    payload = json.dumps(reports, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(payload) > MAX_SESSION_JSON:
        raise NMRNextError("SESSION_SIZE_LIMIT", "Analysis reports exceed the session size limit")
    return hashlib.sha256(payload).hexdigest()


def _report_error(message):
    raise NMRNextError("INVALID_ANALYSIS_REPORT", message)


def _record(value, required, optional=(), label="record"):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value)-set(required)-set(optional):
        _report_error(f"{label} has missing or unsupported fields")


def _finite_tree(value):
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            _report_error("Analysis report keys must be strings")
        for item in value.values():
            _finite_tree(item)
    elif isinstance(value, list):
        for item in value:
            _finite_tree(item)
    elif isinstance(value, float) and not math.isfinite(value):
        _report_error("Analysis report numbers must be finite")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        _report_error("Analysis report contains an unsupported value")


def _string_list(value, label):
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        _report_error(f"{label} must be a list of strings")


_COMMON_REPORT_FIELDS = {"schema_version", "kind", "dataset_digest", "axes", "parameters",
                         "flags", "implementation", "numpy_version"}
_PEAK_FIELDS = {"noise", "threshold", "baseline", "peaks"}
_INTEGRAL_FIELDS = {"regions"}
_AUTO_COMMON = {"result_dataset_digest", "quadrature_index_orientation", "selection",
                "rejected_candidates", "objective", "convergence", "lines", "candidate_screening", "fit_units"}
_AUTO_1D = {"operation", "noise", "line_span_fraction"}
_AUTO_2D = {"operations", "axis_results", "phase_gauge", "resource_bounds"}


def _number(value, label, *, minimum=None, maximum=None, integer=False):
    # bool is a subclass of int, but never a generated scientific scalar.
    if (isinstance(value, bool) or not isinstance(value, int if integer else (int, float))
            or (minimum is not None and value < minimum)
            or (maximum is not None and value > maximum)):
        _report_error(f"{label} has an invalid numeric value")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        _report_error(f"{label} must be finite in the supported numeric range")


def _text(value, label):
    if not isinstance(value, str) or not value:
        _report_error(f"{label} must be nonempty text")


def _numbers(value, count, label, **limits):
    if not isinstance(value, list) or len(value) != count:
        _report_error(f"{label} has an invalid length")
    for item in value:
        _number(item, label, **limits)


def _digest(value, label, *, optional=False):
    if optional and value is None:
        return
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        _report_error(f"{label} must be a SHA256 hex digest")


def _baseline(record, parameters, dimension, *, peak=False):
    method = parameters["baseline"]
    supported = ("none", "constant", "median") if peak else ("none", "constant", "edge_linear", "reference_linear")
    if method not in supported:
        _report_error("Unsupported baseline method")
    fields = {"method", "value"}
    if method == "edge_linear":
        fields.add("coefficients")
    elif method == "reference_linear":
        fields = {"method", "coefficients", "sample_count", "coordinate_convention",
                  "mask_digest", "reference_rms", "assumption"}
    _record(record, fields, label="baseline")
    if record["method"] != method:
        _report_error("Baseline method differs from its parameters")
    if method == "constant":
        _number(parameters["baseline_value"], "Constant baseline parameter")
        _number(record["value"], "Constant baseline value")
        if record["value"] != parameters["baseline_value"]:
            _report_error("Constant baseline differs from its parameter")
    elif parameters["baseline_value"] is not None:
        _report_error("This baseline method has no constant parameter")
    if peak:
        _number(record["value"], "Peak baseline value")
        if method == "none" and record["value"] != 0:
            _report_error("Uncorrected peak baseline must be zero")
    elif method in ("none", "edge_linear") and record["value"] is not None:
        _report_error("This baseline method has no constant value")
    if method in ("edge_linear", "reference_linear"):
        _numbers(record["coefficients"], dimension+1, "Affine baseline coefficients")
    if method == "reference_linear":
        _number(record["sample_count"], "Baseline sample count", integer=True, minimum=dimension+2)
        _number(record["reference_rms"], "Baseline residual", minimum=0)
        for field in ("coordinate_convention", "assumption"):
            _text(record[field], f"Baseline {field}")
        _digest(record["mask_digest"], "Baseline mask digest")
        if record["mask_digest"] != parameters["baseline_mask_digest"]:
            _report_error("Baseline mask differs from its parameter")


def _phase_report(report, current, is_1d):
    """Validate schema-1 diagnostics, without re-fitting historical analyses."""
    axes = current.axes
    ids = [a.id for a in axes]
    if len(axes) != (1 if is_1d else 2):
        _report_error("Automatic phase report dimension is inconsistent")
    parameters = report["parameters"]
    if parameters["axis" if is_1d else "axes"] != (ids[0] if is_1d else ids):
        _report_error("Automatic phase parameter axes are inconsistent")
    corrections = [report["operation"]] if is_1d else report["operations"]
    if not isinstance(corrections, list) or len(corrections) != len(axes):
        _report_error("Automatic phase correction count is invalid")
    for axis, correction in zip(axes, corrections):
        _record(correction, {"op", "axis", "p0", "p1", "pivot"}, label="phase correction")
        if correction["op"] != "phase" or correction["axis"] != axis.id:
            _report_error("Automatic phase correction axis or order is invalid")
        for field in ("p0", "p1", "pivot"):
            _number(correction[field], f"Phase correction {field}")
        _number(correction["pivot"], "Phase pivot", minimum=0, maximum=axis.size-1)
    # The public planner checks axis role/domain/quadrature without executing data.
    plan_operations(current, corrections, backend="numpy")
    if is_1d:
        _number(parameters["pivot"], "Phase parameter pivot")
        pivots = [parameters["pivot"]]
    else:
        _record(parameters["pivot"], ids, label="Phase parameter pivots")
        pivots = [parameters["pivot"][axis] for axis in ids]
        for pivot in pivots:
            _number(pivot, "Phase parameter pivot")
    if pivots != [op["pivot"] for op in corrections] or parameters["pivot_unit"] != "points":
        _report_error("Phase correction pivots differ from their parameters")
    if not isinstance(parameters["first_order"], bool):
        _report_error("Phase first_order must be boolean")
    _number(parameters["max_p1"], "Phase maximum ramp", minimum=0, maximum=180)
    _number(parameters["min_snr"], "Phase screening multiplier", minimum=0)
    if parameters["max_p1"] == 0 or parameters["min_snr"] == 0:
        _report_error("Phase bounds and screening multiplier must be positive")
    _number(parameters["max_peaks"], "Phase maximum peaks", integer=True, minimum=1, maximum=8)
    for field in ("method", "min_snr_semantics"):
        _text(parameters[field], f"Phase {field}")
    _digest(parameters["inclusion_mask_digest"], "Phase mask digest", optional=True)
    _digest(report["result_dataset_digest"], "Phase result digest")
    orientation = report["quadrature_index_orientation"]
    _record(orientation, ids, label="Quadrature orientation")
    for value in orientation.values():
        _number(value, "Quadrature orientation", integer=True, minimum=-1, maximum=1)
    if orientation != quadrature_index_orientation(current):
        _report_error("Phase quadrature orientation differs from the applied spectrum")
    selection = report["selection"]
    _record(selection, {"included_points", "total_points", "scope"}, label="Phase selection")
    total = math.prod(a.size for a in axes)
    _number(selection["total_points"], "Phase total points", integer=True, minimum=total, maximum=total)
    _number(selection["included_points"], "Phase included points", integer=True, minimum=1, maximum=total)
    _text(selection["scope"], "Phase selection scope")
    lines = report["lines"]
    if not isinstance(lines, list) or not 1 <= len(lines) <= parameters["max_peaks"]:
        _report_error("Automatic phase line count is invalid")
    objective = report["objective"]
    _record(objective, {"name", "normalized_rms", "per_line_relative_rms", "acceptance_limit"}
            | ({"initial_rms"} if is_1d else set()), label="Phase objective")
    name = "complex_least_squares" if is_1d else "full_four_quadrature_relative_least_squares"
    limit = .05 if is_1d else .04
    if objective["name"] != name:
        _report_error("Unsupported automatic phase objective")
    for field in ("normalized_rms", "acceptance_limit", *(("initial_rms",) if is_1d else ())):
        _number(objective[field], f"Phase objective {field}", minimum=0)
    if objective["acceptance_limit"] != limit:
        _report_error("Phase objective acceptance limit is inconsistent")
    _numbers(objective["per_line_relative_rms"], len(lines), "Phase per-line residuals", minimum=0, maximum=limit)
    convergence = report["convergence"]
    _record(convergence, {"success", "evaluations"}, label="Phase convergence")
    if convergence["success"] is not True:
        _report_error("Saved phase fit must have converged")
    _number(convergence["evaluations"], "Phase fit evaluations", integer=True, minimum=1)
    for index, line in enumerate(lines):
        _record(line, {"center", "width", "amplitude", "phase", "relative_residual"} if is_1d
                else {"centers", "widths", "amplitude", "relative_rms"}, label="Phase line")
        _number(line["amplitude"], "Phase line amplitude", minimum=0)
        if is_1d:
            _number(line["phase"], "Phase line angle")
            _number(line["relative_residual"], "Phase line residual", minimum=0, maximum=limit)
            centers, widths = [line["center"]], [line["width"]]
        else:
            for field in ("centers", "widths"):
                _record(line[field], ids, label=f"Phase line {field}")
            centers, widths = [line["centers"][a] for a in ids], [line["widths"][a] for a in ids]
            _number(line["relative_rms"], "Phase line residual", minimum=0, maximum=limit)
            if line["relative_rms"] != objective["per_line_relative_rms"][index]:
                _report_error("Phase line residual differs from its objective entry")
        for axis, center, width in zip(axes, centers, widths):
            _number(center, "Phase line center", minimum=0, maximum=axis.size-1)
            _number(width, "Phase line width", minimum=0)
            if width == 0 or line["amplitude"] == 0:
                _report_error("Phase line width and amplitude must be positive")
    rejected = report["rejected_candidates"]
    if not isinstance(rejected, list) or len(rejected) > 32:
        _report_error("Phase rejected candidates must be a bounded list")
    for candidate in rejected:
        _record(candidate, {"points", "reason"}, {"relative_rms"} if not is_1d else (), "Rejected phase candidate")
        _numbers(candidate["points"], len(axes), "Rejected phase points", minimum=0, integer=True)
        if any(p >= a.size for p, a in zip(candidate["points"], axes)):
            _report_error("Rejected candidate is outside the spectrum")
        _text(candidate["reason"], "Rejected candidate reason")
        if "relative_rms" in candidate:
            _number(candidate["relative_rms"], "Rejected candidate residual", minimum=0)
    screening = report["candidate_screening"]
    _record(screening, {"scale", "method", "not_amplitude_noise_sd"} | ({"assumption"} if is_1d else set()), label="Phase screening")
    _number(screening["scale"], "Phase screening scale", minimum=0)
    _text(screening["method"], "Phase screening method")
    if screening["not_amplitude_noise_sd"] is not True:
        _report_error("Phase screening is not an amplitude noise estimate")
    units = report["fit_units"]
    _record(units, {"center", "width", "phase", "amplitude", "intensity_scale", "reported_lines" if is_1d else "p1"}, label="Phase fit units")
    for field, value in units.items():
        if field != "intensity_scale":
            _text(value, f"Phase fit {field} units")
    _number(units["intensity_scale"], "Phase fit intensity scale", minimum=0)
    if units["intensity_scale"] == 0:
        _report_error("Phase intensity scale must be positive")
    if is_1d:
        _text(screening["assumption"], "Phase screening assumption")
        noise = report["noise"]
        _record(noise, {"sigma", "method", "assumption"}, label="Phase noise")
        _number(noise["sigma"], "Phase noise sigma", minimum=0)
        for field in ("method", "assumption"):
            _text(noise[field], f"Phase noise {field}")
        _number(report["line_span_fraction"], "Phase line span", minimum=0, maximum=1)
    else:
        gauge = {"ambiguity": "simultaneous 180-degree shifts on both axes leave all four quadratures unchanged",
                 "canonical_branch": "first logical axis correction p0 in [-90,90); second in [-180,180)"}
        if report["phase_gauge"] != gauge:
            _report_error("Unsupported two-axis phase gauge")
        _record(report["axis_results"], ids, label="Phase axis results")
        for axis, correction in zip(ids, corrections):
            item = report["axis_results"][axis]
            _record(item, {"operation", "line_span_fraction", "maximum_gap_fraction"}, label="Phase axis result")
            _record(item["operation"], {"op", "axis", "p0", "p1", "pivot"}, label="Phase axis correction")
            for field in ("p0", "p1", "pivot"):
                _number(item["operation"][field], f"Phase axis correction {field}")
            if item["operation"] != correction:
                _report_error("Phase axis result correction is inconsistent")
            for field in ("line_span_fraction", "maximum_gap_fraction"):
                _number(item[field], f"Phase axis {field}", minimum=0, maximum=1)
        bounds = report["resource_bounds"]
        _record(bounds, {"candidate_fits", "accepted_lines", "samples_per_axis_per_line", "local_max_nfev", "joint_max_nfev"}, label="Phase resource bounds")
        for field, value in bounds.items():
            _number(value, f"Phase resource {field}", integer=True, minimum=1)


def _validate_analysis(current, regions, reports):
    if not isinstance(regions, list) or len(regions) > 1000:
        raise NMRNextError("INVALID_SESSION", "Session regions must be a bounded list")
    if not isinstance(reports, dict) or len(reports) > 32 or not all(isinstance(k, str) and k for k in reports):
        raise NMRNextError("INVALID_SESSION", "Session reports must be a bounded keyed object")
    kinds = {"peaks": "peak_picking", "integrate": "integration", "autophase": "autophase"}
    if set(reports)-set(kinds):
        raise NMRNextError("INVALID_ANALYSIS_REPORT", "Unknown saved analysis report key")
    digest = dataset_digest(current)
    for item in regions:
        if not isinstance(item, dict) or set(item) != {"bounds", "unit"}:
            raise NMRNextError("INVALID_REGION", "Saved region requires bounds and unit")
        select_analysis_region(current, item["bounds"], unit=item["unit"])
    for key, report in reports.items():
        _finite_tree(report)
        if (not isinstance(report, dict) or report.get("schema_version") != "1"
                or report.get("kind") != kinds[key] or not isinstance(report.get("parameters"), dict)
                or not isinstance(report.get("flags"), list)):
            raise NMRNextError("INVALID_ANALYSIS_REPORT", f"Malformed saved report: {key}")
        _string_list(report["flags"], "Report flags")
        if (not isinstance(report.get("implementation"), str)
                or not isinstance(report.get("numpy_version"), str)
                or not isinstance(report.get("dataset_digest"), str)
                or not isinstance(report.get("axes"), list)):
            _report_error("Report identity fields are malformed")
        if "mask_selections" in report:
            masks = report["mask_selections"]
            expected_role = {"peaks": "noise", "integrate": "baseline", "autophase": "phase"}[key]
            if not isinstance(masks, dict) or set(masks) != {expected_role}:
                _report_error("Mask selections have unsupported roles")
            for selection in masks.values():
                _record(selection, {"dataset_digest", "regions"}, label="mask selection")
                if (selection["dataset_digest"] != report["dataset_digest"]
                        or not isinstance(selection["regions"], list)
                        or not 1 <= len(selection["regions"]) <= 1000):
                    _report_error("Mask selection fields are malformed")
                axes = report["axes"]
                if not all(isinstance(axis, dict) and isinstance(axis.get("id"), str)
                           and isinstance(axis.get("size"), int) for axis in axes):
                    _report_error("Mask selection axes are malformed")
                for box in selection["regions"]:
                    if not isinstance(box, dict) or set(box) != {axis["id"] for axis in axes}:
                        _report_error("Mask selection axis IDs are malformed")
                    for axis in axes:
                        pair = box[axis["id"]]
                        if (not isinstance(pair, list) or len(pair) != 2
                                or any(isinstance(v, bool) or not isinstance(v, int) for v in pair)
                                or not 0 <= pair[0] <= pair[1] < axis["size"]):
                            _report_error("Mask selection point bounds are invalid")
        if key == "peaks":
            _record(report, _COMMON_REPORT_FIELDS | _PEAK_FIELDS,
                    {"parent_region", "mask_selections"}, "peak report")
            _record(report["parameters"], {"channel", "sign", "threshold", "snr", "min_separation",
                "separation_unit", "separation_rule", "baseline", "baseline_value", "max_peaks",
                "mask_digest", "noise_mask_digest"}, label="peak parameters")
            if not isinstance(report["peaks"], list) or len(report["peaks"]) > 10000:
                _report_error("Peak records must be a bounded list")
            if not isinstance(report["noise"], dict) or not isinstance(report["baseline"], dict):
                _report_error("Peak noise and baseline records are required")
            _record(report["noise"], {"sigma", "method", "sample_count",
                "sample_count_is_effective_count", "assumption", "selection"}, label="peak noise")
            noise = report["noise"]
            _number(noise["sigma"], "Peak noise sigma", minimum=0)
            _number(noise["sample_count"], "Peak noise sample count", integer=True, minimum=3)
            if (noise["method"] != "amplitude_MAD_normal" or noise["sample_count_is_effective_count"] is not False
                    or noise["selection"] not in ("detection_mask", "explicit_noise_mask")):
                _report_error("Peak noise method or selection is unsupported")
            _text(noise["assumption"], "Peak noise assumption")
            _number(report["threshold"], "Peak threshold", minimum=0)
            parameters = report["parameters"]
            if parameters["sign"] not in ("positive", "negative", "both") or parameters["separation_unit"] not in ("points", "Hz", "ppm"):
                _report_error("Peak sign or separation unit is unsupported")
            _number(parameters["max_peaks"], "Maximum peak count", integer=True, minimum=1, maximum=10000)
            _number(parameters["snr"], "Peak SNR", minimum=0)
            if parameters["snr"] == 0:
                _report_error("Peak SNR must be positive")
            if parameters["threshold"] is not None:
                _number(parameters["threshold"], "Peak threshold parameter", minimum=0)
            separation = parameters["min_separation"]
            if isinstance(separation, dict):
                _record(separation, [a.id for a in current.axes], label="Peak separation")
                separation = list(separation.values())
            else:
                separation = [separation]
            for value in separation:
                _number(value, "Peak separation", minimum=0)
                if value == 0:
                    _report_error("Peak separation must be positive")
            _text(parameters["separation_rule"], "Peak separation rule")
            for field in ("mask_digest", "noise_mask_digest"):
                _digest(parameters[field], field, optional=True)
            _baseline(report["baseline"], parameters, len(current.axes), peak=True)
        elif key == "integrate":
            _record(report, _COMMON_REPORT_FIELDS | _INTEGRAL_FIELDS,
                    {"parent_region", "mask_selections"}, "integration report")
            _record(report["parameters"], {"channel", "baseline", "baseline_value",
                "allow_overlap", "baseline_mask_digest", "quadrature", "coordinate_measure",
                "bounds_convention"}, label="integration parameters")
            if not isinstance(report["regions"], list) or not 1 <= len(report["regions"]) <= 1000:
                _report_error("Integration records must be a bounded nonempty list")
            parameters = report["parameters"]
            _number(parameters["channel"], "Integration channel", integer=True, minimum=0, maximum=current.data.shape[0]-1)
            if not isinstance(parameters["allow_overlap"], bool):
                _report_error("Integration overlap parameter must be boolean")
            _digest(parameters["baseline_mask_digest"], "Integration baseline mask", optional=True)
            for field in ("quadrature", "coordinate_measure", "bounds_convention"):
                _text(parameters[field], f"Integration {field}")
        else:
            is_1d = "operation" in report
            _record(report, _COMMON_REPORT_FIELDS | _AUTO_COMMON | (_AUTO_1D if is_1d else _AUTO_2D),
                    {"mask_selections"}, "automatic phase report")
            _record(report["parameters"], {"axis" if is_1d else "axes", "pivot", "pivot_unit",
                "first_order", "max_p1", "max_peaks", "min_snr", "min_snr_semantics",
                "method", "inclusion_mask_digest"}, label="automatic phase parameters")
            _phase_report(report, current, is_1d)
        mapping = report.get("parent_region")
        if mapping is None:
            if report.get("dataset_digest") != digest and report.get("result_dataset_digest") != digest:
                raise NMRNextError("STALE_ANALYSIS", f"Saved report does not belong to the applied spectrum: {key}")
            if key != "autophase" and report["axes"] != [a.to_dict() for a in current.axes]:
                _report_error("Whole-spectrum report axes differ from the applied spectrum")
            analysis_dataset = current
        else:
            if key == "autophase":
                raise NMRNextError("INVALID_ANALYSIS_REPORT", "Automatic phase reports cannot use analysis-region linkage")
            region = verify_region_mapping(current, mapping)
            if report.get("dataset_digest") != dataset_digest(region):
                raise NMRNextError("STALE_ANALYSIS", f"Saved region report is stale: {key}")
            if report["axes"] != [a.to_dict() for a in region.axes]:
                _report_error("Region report axes differ from the selected view")
            analysis_dataset = region
            if report["kind"] == "peak_picking":
                for peak in report.get("peaks", []):
                    points = peak.get("points") if isinstance(peak, dict) else None
                    if not isinstance(points, dict) or set(points) != {a.id for a in region.axes}:
                        raise NMRNextError("INVALID_ANALYSIS_REPORT", "Saved peak points are invalid")
                    expected = {a.id: mapping["parent_start"][a.id]+points[a.id] for a in region.axes}
                    if (any(isinstance(points[a.id], bool) or not isinstance(points[a.id], int)
                            or not 0 <= points[a.id] < a.size for a in region.axes)
                            or peak.get("parent_points") != expected
                            or peak.get("coordinates") != _coordinates(region.axes, points)
                            or peak.get("parent_coordinates") != _coordinates(current.axes, expected)):
                        raise NMRNextError("INVALID_ANALYSIS_REPORT", "Saved parent peak indices are invalid")
                    channel = report.get("parameters", {}).get("channel")
                    if (isinstance(channel, bool) or not isinstance(channel, int)
                            or not 0 <= channel < region.data.shape[0]
                            or peak.get("intensity") != float(region.data[(channel, *(points[a.id] for a in region.axes))])):
                        raise NMRNextError("INVALID_ANALYSIS_REPORT", "Saved peak intensity differs from its region sample")
            elif report["kind"] == "integration":
                for item in report.get("regions", []):
                    local = item.get("point_bounds") if isinstance(item, dict) else None
                    if not isinstance(local, dict) or set(local) != {a.id for a in region.axes}:
                        raise NMRNextError("INVALID_ANALYSIS_REPORT", "Saved integral bounds are invalid")
                    expected = {}
                    for axis in region.axes:
                        pair = local[axis.id]
                        if (not isinstance(pair, list) or len(pair) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in pair)
                                or not 0 <= pair[0] < pair[1] <= axis.size-1):
                            raise NMRNextError("INVALID_ANALYSIS_REPORT", "Saved integral bounds are out of range")
                        expected[axis.id] = [mapping["parent_start"][axis.id]+v for v in pair]
                    if item.get("parent_point_bounds") != expected:
                        raise NMRNextError("INVALID_ANALYSIS_REPORT", "Saved parent integral bounds are invalid")
                    parent_ends = [_coordinates(current.axes, {a.id: expected[a.id][edge]
                                        for a in current.axes}) for edge in (0, 1)]
                    expected_coordinates = {a.id: {unit: [parent_ends[edge][a.id][unit] for edge in (0, 1)]
                                                for unit in parent_ends[0][a.id]} for a in current.axes}
                    if item.get("parent_coordinate_bounds") != expected_coordinates:
                        raise NMRNextError("INVALID_ANALYSIS_REPORT", "Saved parent integral coordinates are invalid")
        if key == "peaks":
            ids = set()
            channel = report["parameters"].get("channel")
            if isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel < analysis_dataset.data.shape[0]:
                _report_error("Peak channel is invalid")
            for peak in report["peaks"]:
                fields = {"id", "points", "coordinates", "intensity", "height_above_baseline", "sign", "flags"}
                if mapping is not None:
                    fields |= {"parent_points", "parent_coordinates"}
                _record(peak, fields, label="peak")
                if not isinstance(peak["id"], str) or not peak["id"] or peak["id"] in ids:
                    _report_error("Peak IDs must be nonempty and unique")
                ids.add(peak["id"])
                _string_list(peak["flags"], "Peak flags")
                if peak["sign"] not in ("positive", "negative"):
                    _report_error("Peak sign is invalid")
                points = peak["points"]
                if (not isinstance(points, dict) or set(points) != {a.id for a in analysis_dataset.axes}
                        or any(isinstance(points[a.id], bool) or not isinstance(points[a.id], int)
                                   or not 0 <= points[a.id] < a.size for a in analysis_dataset.axes)):
                    _report_error("Peak sample indices are invalid")
                if peak["coordinates"] != _coordinates(analysis_dataset.axes, points):
                    _report_error("Peak coordinates differ from the selected grid")
                _number(peak["intensity"], "Peak intensity")
                if peak["intensity"] != float(analysis_dataset.data[(channel, *(points[a.id] for a in analysis_dataset.axes))]):
                    _report_error("Peak intensity differs from the selected sample")
                if (isinstance(peak["height_above_baseline"], bool)
                        or not isinstance(peak["height_above_baseline"], (int, float))):
                    _report_error("Peak baseline height is invalid")
        elif key == "integrate":
            ids = set()
            for item in report["regions"]:
                fields = {"id", "bounds", "unit", "point_bounds", "flags", "integral",
                          "integral_unit", "baseline", "overlaps", "coordinate_bounds"}
                if mapping is not None:
                    fields |= {"parent_point_bounds", "parent_coordinate_bounds"}
                _record(item, fields, label="integral")
                if not isinstance(item["id"], str) or not item["id"] or item["id"] in ids:
                    _report_error("Integral IDs must be nonempty and unique")
                ids.add(item["id"])
                _string_list(item["flags"], "Integral flags")
                _string_list(item["overlaps"], "Integral overlaps")
                if (isinstance(item["integral"], bool) or not isinstance(item["integral"], (int, float))
                        or item["integral_unit"] != ("intensity*Hz" if len(analysis_dataset.axes) == 1 else "intensity*Hz^2")
                        or not isinstance(item["baseline"], dict) or not isinstance(item["baseline"].get("method"), str)):
                    _report_error("Integral value, unit or baseline is malformed")
                _baseline(item["baseline"], report["parameters"], len(analysis_dataset.axes))
                if (not isinstance(item["bounds"], dict) or set(item["bounds"]) != {a.id for a in analysis_dataset.axes}
                        or item["unit"] not in ("points", "Hz", "ppm")):
                    _report_error("Integral requested bounds are malformed")
                local = item["point_bounds"]
                if not isinstance(local, dict) or set(local) != {a.id for a in analysis_dataset.axes}:
                    _report_error("Integral point bounds are malformed")
                for axis in analysis_dataset.axes:
                    _numbers(item["bounds"][axis.id], 2, "Integral requested bounds")
                    pair = local[axis.id]
                    if (not isinstance(pair, list) or len(pair) != 2
                            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in pair)
                            or not 0 <= pair[0] < pair[1] <= axis.size-1):
                        _report_error("Integral point bounds are out of range")
                ends = [_coordinates(analysis_dataset.axes, {a.id: local[a.id][edge]
                          for a in analysis_dataset.axes}) for edge in (0, 1)]
                expected = {a.id: {unit: [ends[edge][a.id][unit] for edge in (0, 1)]
                                    for unit in ends[0][a.id]} for a in analysis_dataset.axes}
                if item["coordinate_bounds"] != expected:
                    _report_error("Integral coordinates differ from the selected grid")
        else:
            if report["result_dataset_digest"] != digest or report["axes"] != [a.to_dict() for a in current.axes]:
                raise NMRNextError("STALE_ANALYSIS", "Automatic phase result or axes differ from applied spectrum")
            corrections = [report["operation"]] if "operation" in report else report["operations"]
            actual = [entry.get("operation") for entry in current.provenance[-len(corrections):]]
            if (len(current.provenance) < len(corrections)
                    or actual != [dict(op, version="1") for op in corrections]):
                _report_error("Automatic phase corrections differ from applied provenance")
    return digest


def _workflow_record(operations, backend, memory_bytes):
    return {"schema_version": "1", "input": "source.nmrh5", "output": "result.nmrh5",
            "input_format": "native", "output_format": "native", "operations": operations,
            "backend": backend, "memory_bytes": memory_bytes}


def save_session(source, operations, directory, *, backend="native", memory_bytes=256*1024**2,
                 regions=(), reports=None, result=None, source_name=None):
    """Publish a new portable folder; session.json is the last commit marker.

    The three files are published sequentially, so the folder is not an atomic
    whole-directory snapshot. Readers require the manifest before returning.
    """
    if not isinstance(source, Dataset):
        raise NMRNextError("INVALID_DATASET", "Session source must be a Dataset")
    positive_int(memory_bytes, "memory_bytes")
    if source_name is not None and (not isinstance(source_name, str) or len(source_name) > 256):
        raise NMRNextError("INVALID_SESSION", "source_name must be short display text")
    operations = json_copy(operations, "operations")
    if not isinstance(operations, list):
        raise NMRNextError("INVALID_RECIPE", "Session operations must be a list")
    regions = json_copy(list(regions), "regions")
    reports = json_copy({} if reports is None else reports, "reports")
    plan_operations(source, operations, backend=backend, memory_bytes=memory_bytes)
    replayed = run_operations(source, operations, backend=backend, memory_bytes=memory_bytes)
    if result is not None:
        if not isinstance(result, Dataset) or dataset_digest(result) != dataset_digest(replayed) or result.to_metadata() != replayed.to_metadata():
            raise NMRNextError("SESSION_RESULT_MISMATCH", "Applied result does not match the saved recipe")
    result_digest = _validate_analysis(replayed, regions, reports)
    workflow = _workflow_record(operations, backend, memory_bytes)
    workflow_bytes = _json_bytes(workflow, MAX_WORKFLOW_JSON, "Workflow")
    destination = Path(directory)
    if destination.exists():
        raise NMRNextError("OUTPUT_EXISTS", f"Session destination already exists: {destination}")
    parent = destination.parent
    if not parent.is_dir():
        raise NMRNextError("INVALID_PATH", "Session destination parent must exist")
    staging = Path(tempfile.mkdtemp(prefix=".spinflow-session-", dir=parent))
    staging_identity = _directory_identity(staging)
    if staging_identity is None:
        raise NMRNextError("INVALID_STORAGE", "Cannot establish ownership of session staging directory")
    staged_identities = {}
    staged_hashes = {}
    owned = []
    created_destination = False
    destination_identity = None
    primary_error = None
    cleanup_warnings = []
    try:
        _require_staging(staging, staging_identity)
        snapshot = staging / "source.nmrh5"
        write_dataset(source, snapshot, format="native", overwrite=False)
        staged_identities["source.nmrh5"] = _file_identity(snapshot)
        if staged_identities["source.nmrh5"] is None:
            raise NMRNextError("INVALID_STORAGE", "Cannot establish source snapshot ownership")
        source_hash = _hash_file(snapshot)
        staged_hashes["source.nmrh5"] = source_hash
        _require_staging(staging, staging_identity)
        _write_owned(staging / "workflow.json", workflow_bytes, staged_identities)
        staged_hashes["workflow.json"] = hashlib.sha256(workflow_bytes).hexdigest()
        manifest = {"schema_version": "1", "kind": "spinflow-session", "source_file": "source.nmrh5",
                    "workflow_file": "workflow.json", "source_sha256": source_hash,
                    "source_dataset_digest": dataset_digest(source),
                    "workflow_sha256": hashlib.sha256(workflow_bytes).hexdigest(),
                    "result_dataset_digest": result_digest, "source_name": source_name,
                    "regions": regions, "reports": reports, "reports_sha256": _reports_hash(reports)}
        session_bytes = _json_bytes(manifest, MAX_SESSION_JSON, "Session")
        _require_staging(staging, staging_identity)
        _write_owned(staging / "session.json", session_bytes, staged_identities)
        staged_hashes["session.json"] = hashlib.sha256(session_bytes).hexdigest()
        try:
            destination.mkdir()
        except FileExistsError as exc:
            raise NMRNextError("OUTPUT_EXISTS", f"Session destination already exists: {destination}") from exc
        created_destination = True
        destination_identity = _directory_identity(destination)
        if destination_identity is None:
            raise NMRNextError("INVALID_STORAGE", "Cannot establish session destination ownership")
        for name in _FILES:
            staged = staging / name
            target = destination / name
            identity = staged_identities[name]
            if (_directory_identity(staging) != staging_identity
                    or _directory_identity(destination) != destination_identity
                    or not _matches_file(staged, identity)
                    or _hash_file(staged) != staged_hashes[name]):
                raise NMRNextError("SESSION_STAGING_CHANGED", f"Session staging changed before publishing {name}")
            try:
                _publish_new(staged, target)
            except BaseException:
                if _matches_file(target, identity) and _hash_file(target) == staged_hashes[name]:
                    owned.append((target, identity, staged_hashes[name]))
                raise
            if not _matches_file(target, identity) or _hash_file(target) != staged_hashes[name]:
                raise NMRNextError("SESSION_STAGING_CHANGED", f"Published {name} no longer matches its staged owner")
            owned.append((target, identity, staged_hashes[name]))
    except BaseException as exc:
        primary_error = exc
        for path, identity, digest in reversed(owned):
            try:
                if _matches_file(path, identity) and _hash_file(path) == digest:
                    _cleanup_published(path, identity)
                elif path.exists() or path.is_symlink():
                    cleanup_warnings.append(f"Preserved changed published file {path}")
            except OSError as cleanup_exc:
                cleanup_warnings.append(f"Could not remove owned published {path.name}: {cleanup_exc}")
        if created_destination and destination_identity is not None and _directory_identity(destination) == destination_identity:
            try:
                destination.rmdir()
            except OSError as cleanup_exc:
                cleanup_warnings.append(f"Preserved nonempty destination {destination}: {cleanup_exc}")
        raise
    finally:
        try:
            cleanup_warnings.extend(_cleanup_staging_owned(staging, staging_identity,
                                                           staged_identities, staged_hashes))
        except OSError as cleanup_exc:
            cleanup_warnings.append(f"Could not inspect staging cleanup: {cleanup_exc}")
        if cleanup_warnings:
            if primary_error is not None:
                primary_error.add_note("Session cleanup preserved uncertain files: " + " | ".join(cleanup_warnings))
                if isinstance(primary_error, NMRNextError):
                    primary_error.details["cleanup_warnings"] = cleanup_warnings
            else:
                warnings.warn("Session committed, but cleanup preserved uncertain files: " +
                              " | ".join(cleanup_warnings), RuntimeWarning, stacklevel=2)
    return destination / "session.json"


def load_session(directory, *, max_bytes=512*1024**2, replay=True):
    """Verify snapshot, workflow, replay and report linkage before returning."""
    positive_int(max_bytes, "max_bytes")
    path = Path(directory)
    if path.is_file() and path.name == "session.json":
        path = path.parent
    if not path.is_dir() or path.is_symlink():
        raise NMRNextError("INVALID_SESSION", "Session directory is missing or linked")
    manifest = _read_json(path/"session.json", MAX_SESSION_JSON)
    fields = {"schema_version", "kind", "source_file", "workflow_file", "source_sha256",
              "source_dataset_digest", "workflow_sha256", "result_dataset_digest", "source_name",
              "regions", "reports", "reports_sha256"}
    if (not isinstance(manifest, dict) or set(manifest) != fields or manifest["schema_version"] != "1"
            or manifest["kind"] != "spinflow-session" or manifest["source_file"] != "source.nmrh5"
            or manifest["workflow_file"] != "workflow.json"):
        raise NMRNextError("UNSUPPORTED_SESSION_SCHEMA", "Session fields or version are unsupported")
    if (not isinstance(manifest["reports_sha256"], str)
            or _reports_hash(manifest["reports"]) != manifest["reports_sha256"]):
        raise NMRNextError("SESSION_INTEGRITY_FAILURE", "Saved analysis content changed")
    source_path, workflow_path = path/"source.nmrh5", path/"workflow.json"
    for item in (source_path, workflow_path):
        if item.is_symlink() or not item.is_file():
            raise NMRNextError("INVALID_SESSION", "Session source or workflow is missing or linked")
    if _hash_file(source_path) != manifest["source_sha256"] or _hash_file(workflow_path) != manifest["workflow_sha256"]:
        raise NMRNextError("SESSION_INTEGRITY_FAILURE", "Session source or workflow bytes changed")
    workflow = _read_json(workflow_path, MAX_WORKFLOW_JSON)
    expected = {"schema_version", "input", "output", "input_format", "output_format", "operations", "backend", "memory_bytes"}
    if (not isinstance(workflow, dict) or set(workflow) != expected or workflow["schema_version"] != "1"
            or workflow["input"] != "source.nmrh5" or workflow["output"] != "result.nmrh5"
            or workflow["input_format"] != "native" or workflow["output_format"] != "native"
            or not isinstance(workflow["operations"], list)):
        raise NMRNextError("INVALID_RECIPE", "Saved workflow fields are invalid")
    source = read_dataset(source_path, format="native", max_bytes=max_bytes)
    if dataset_digest(source) != manifest["source_dataset_digest"]:
        raise NMRNextError("SESSION_INTEGRITY_FAILURE", "Session source numeric identity changed")
    plan_operations(source, workflow["operations"], backend=workflow["backend"], memory_bytes=workflow["memory_bytes"])
    current = run_operations(source, workflow["operations"], backend=workflow["backend"],
                             memory_bytes=workflow["memory_bytes"])
    digest = _validate_analysis(current, manifest["regions"], manifest["reports"])
    if digest != manifest["result_dataset_digest"]:
        raise NMRNextError("SESSION_RESULT_MISMATCH", "Session result differs from saved applied result")
    if manifest["source_name"] is not None and (not isinstance(manifest["source_name"], str) or len(manifest["source_name"]) > 256):
        raise NMRNextError("INVALID_SESSION", "Session display name is invalid")
    return {"source": source, "current": current if replay else None,
            "operations": deepcopy(workflow["operations"]), "backend": workflow["backend"],
            "memory_bytes": workflow["memory_bytes"], "regions": deepcopy(manifest["regions"]),
            "reports": deepcopy(manifest["reports"]), "source_identity": {
                "snapshot_sha256": manifest["source_sha256"], "numeric_digest": manifest["source_dataset_digest"]},
            "source_name": manifest["source_name"], "result_digest": digest,
            "workflow_path": workflow_path}
