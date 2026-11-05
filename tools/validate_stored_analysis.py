"""S20 post-failure stored-spectrum engineering check; no raw processing.

See docs/stored-analysis-protocol.md. Preparation hashes but does not decode
acquired samples. Root approval of a prepared lock precedes acquired execution.
"""
import argparse
from collections import Counter
import hashlib
import importlib
import importlib.metadata
from itertools import product
import json
import math
from pathlib import Path, PurePosixPath
import platform
import re
import sys

import numpy as np


PICKER = {"channel": 0, "sign": "both", "threshold": None, "snr": 5.,
          "baseline": "none", "min_separation": 1., "separation_unit": "points",
          "max_peaks": 10000}
GATES = {"coordinate_ppm": 2e-5, "target_strict_ppm": .002, "integral_relative": 1e-10}
COMPONENTS = ("2rr", "2ir", "2ri", "2ii")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def validate_manifest(m):
    keys = {"schema", "id", "classification", "files", "picker", "gates", "targets", "regions"}
    if not isinstance(m, dict) or set(m) != keys or type(m["schema"]) is not int or m["schema"] != 1:
        raise ValueError("Unknown manifest schema or fields")
    if m["id"] != "bmse000002-stored-analysis-s20" or m["classification"] != "post-failure-engineering-not-holdout":
        raise ValueError("Unexpected case identity")
    if m["picker"] != PICKER or m["gates"] != GATES:
        raise ValueError("Fixed analysis contract changed")
    if len(m["files"]) != 19 or len({r["path"] for r in m["files"]}) != 19:
        raise ValueError("Expected all 19 unique source pins")
    if len(m["targets"]) != 18 or [r["row"] for r in m["targets"]] != list(range(1, 19)):
        raise ValueError("Expected all 18 ordered targets")
    if len(m["regions"]) != 4:
        raise ValueError("Expected four frozen regions")
    for r in m["regions"]:
        if len(r["bounds_points"]) != 2 or any(hi-lo != 8 for lo, hi in r["bounds_points"]) or len(r["expected_integrals"]) != 4:
            raise ValueError("Expected original nine-by-nine, four-component regions")
    return m


def verify_inputs(root, records):
    root = Path(root).resolve()
    for record in records:
        raw = record["path"]
        rel = PurePosixPath(raw)
        if not raw or rel.is_absolute() or ".." in rel.parts or "\\" in raw or ":" in raw:
            raise ValueError("Unsafe source path")
        path = (root/Path(*rel.parts)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Source path escapes input root")
        if path.stat().st_size != record["bytes"] or digest(path) != record["sha256"]:
            raise ValueError(f"Input identity changed: {raw}")


def scalars(path):
    result = {}
    for line in Path(path).read_text(encoding="latin1").splitlines():
        match = re.fullmatch(r"##\$([^=]+)=\s*(.*)", line)
        if not match:
            continue
        key, text = match.groups()
        if key in result:
            raise ValueError(f"Duplicate header scalar: {key}")
        if text.startswith("("):
            continue
        try:
            result[key] = int(text)
        except ValueError:
            try:
                result[key] = float(text)
            except ValueError:
                result[key] = text
    return result


def decode_archive(root, case, components):
    directory = Path(root)/case/"pdata/1"
    params = [scalars(directory/name) for name in (("procs",) if case == "1H" else ("proc2s", "procs"))]
    for h in params:
        if (h["BYTORDP"], h["DTYPP"], h["NC_proc"]) != (0, 0, -6):
            raise ValueError("Unsupported archived binary profile")
        if h["SI"] % h["XDIM"] or h["SI"] < 2 or h["SW_p"] <= 0 or h["SF"] <= 0:
            raise ValueError("Unsupported archived dimensions")
    if len(params) == 2 and params[1]["XDIM"] != params[1]["SI"]:
        raise ValueError("Expected full-row direct tiling")
    shape = tuple(h["SI"] for h in params)
    arrays = []
    for name in components:
        raw = (directory/name).read_bytes()
        if len(raw) != 4*math.prod(shape):
            raise ValueError("Wrong archived payload length")
        arrays.append(np.frombuffer(raw, dtype="<i4").reshape(shape).astype(np.float64)*2.**-6)
    return np.stack(arrays), params


def grid(h, unit):
    indices = np.arange(h["SI"], dtype=np.float64)
    if unit == "Hz":
        return h["OFFSET"]*h["SF"]-indices*h["SW_p"]/h["SI"]
    return h["OFFSET"]-indices*h["SW_p"]/(h["SF"]*h["SI"])


def make_dataset(values, params):
    from nmrnext.model import Axis, Dataset
    axes = [Axis(id=f"a{i}", size=h["SI"], domain="frequency", unit="Hz",
                 start=h["OFFSET"]*h["SF"], step=-h["SW_p"]/h["SI"],
                 observation_mhz=h["SF"], reference_ppm=0., reference_hz=0., encoding="real")
            for i, h in enumerate(params)]
    return Dataset(values[None, ...], axes)


def amplitude_sigma(values):
    # Sorted middle values implement the scalar median definition, independent
    # of the product's NumPy median calls. No sample differencing is used.
    def median(v):
        ordered = np.sort(np.asarray(v).ravel())
        n = len(ordered)
        return float(ordered[n//2]) if n % 2 else (float(ordered[n//2-1])+float(ordered[n//2]))/2
    center = median(values)
    return median(np.abs(values-center))/.6744897501960817


def extrema(data, cut):
    """Independent comparisons and set flood fill, no SciPy/product helpers."""
    offsets = list(product((-1, 0, 1), repeat=data.ndim))
    result = []
    for sign in (1, -1):
        signed = data*sign
        possible = (signed >= cut) & (signed > 0)
        lower = np.zeros(data.shape, dtype=bool)
        for offset in offsets:
            here, there = [], []
            for n, d in zip(data.shape, offset):
                here.append(slice(max(0, -d), min(n, n-d)))
                there.append(slice(max(0, d), min(n, n+d)))
            a, b = tuple(here), tuple(there)
            possible[a] &= signed[a] >= signed[b]
            lower[a] |= signed[a] > signed[b]
        remaining = {tuple(int(v) for v in p) for p in np.argwhere(possible)}
        count = 0
        for first in sorted(remaining):
            if first not in remaining:
                continue
            remaining.remove(first)
            group, pending = [first], [first]
            while pending:
                p = pending.pop()
                for offset in offsets:
                    neighbor = tuple(v+d for v, d in zip(p, offset))
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        pending.append(neighbor)
                        group.append(neighbor)
            count += 1
            if count > 50000:
                raise ValueError("More than 50000 candidate plateaus")
            if not any(lower[p] for p in group):
                continue
            totals = tuple(sum(p[i] for p in group) for i in range(data.ndim))
            point = min(group, key=lambda p: (sum((len(group)*v-s)**2 for v, s in zip(p, totals)), p))
            flags = (["plateau"] if len(group) > 1 else [])
            if any(v == 0 or v == n-1 for v, n in zip(point, data.shape)):
                flags.append("spectrum_edge")
            result.append({"points": list(point), "sign": "positive" if sign == 1 else "negative",
                           "intensity": float(data[point]), "flags": flags})
    result.sort(key=lambda p: (-abs(p["intensity"]), tuple(p["points"]), p["sign"] == "negative"))
    if len(result) > 10000:
        raise ValueError("More than 10000 detections; truncation is a failure")
    return result


def compare_peaks(expected, observed, params):
    def wanted_key(p):
        return tuple(p["points"]), p["sign"]
    def actual_key(p):
        if set(p["points"]) != {f"a{i}" for i in range(len(params))}:
            raise ValueError("Malformed detection coordinates")
        point = tuple(p["points"][f"a{i}"] for i in range(len(params)))
        if any(type(v) is not int or not 0 <= v < h["SI"] for v, h in zip(point, params)):
            raise ValueError("Malformed detection point")
        return point, p["sign"]
    ekeys, akeys = [wanted_key(p) for p in expected], [actual_key(p) for p in observed]
    ec, ac = Counter(ekeys), Counter(akeys)
    differences = []
    by_key = dict(zip(ekeys, expected))
    for index, (key, record) in enumerate(zip(akeys, observed)):
        if key not in by_key:
            continue
        want = by_key[key]
        errors = []
        if record["intensity"] != want["intensity"] or record["flags"] != want["flags"]:
            errors.append("intensity-or-flags")
        for i, (point, h) in enumerate(zip(key[0], params)):
            for unit, expected_value, limit in (
                ("Hz", h["OFFSET"]*h["SF"]-point*h["SW_p"]/h["SI"], GATES["coordinate_ppm"]*h["SF"]),
                ("ppm", h["OFFSET"]-point*h["SW_p"]/(h["SF"]*h["SI"]), GATES["coordinate_ppm"])):
                value = record["coordinates"][f"a{i}"][unit]
                if not math.isfinite(value) or abs(value-expected_value) > limit:
                    errors.append(f"a{i}-{unit}")
        if errors:
            differences.append({"detection_index": index, "fields": errors})
    missing = [{"points": list(k[0]), "sign": k[1], "count": n} for k, n in (ec-ac).items()]
    extra = [{"points": list(k[0]), "sign": k[1], "count": n} for k, n in (ac-ec).items()]
    duplicates = [{"points": list(k[0]), "sign": k[1], "count": n} for k, n in ac.items() if n > 1]
    return {"pass": not (missing or extra or duplicates or differences) and ekeys == akeys,
            "expected_count": len(expected), "observed_count": len(observed), "missing": missing,
            "extra": extra, "duplicates": duplicates, "differences": differences,
            "ordered_keys_equal": ekeys == akeys}


def match_targets(targets, detections):
    eligible = []
    for target in targets:
        indices = [i for i, p in enumerate(detections) if p["sign"] == "positive"
                   and abs(p["coordinates"]["a0"]["ppm"]-target["ppm"]) < GATES["target_strict_ppm"]]
        eligible.append(sorted(indices, key=lambda i: (abs(detections[i]["coordinates"]["a0"]["ppm"]-target["ppm"]),
                                                        detections[i]["points"]["a0"])))
    owners = {}
    def assign(row, seen):
        for index in eligible[row]:
            if index in seen:
                continue
            seen.add(index)
            if index not in owners or assign(owners[index], seen):
                owners[index] = row
                return True
        return False
    for row in range(len(targets)):
        assign(row, set())
    by_row = {row: index for index, row in owners.items()}
    rows = []
    for row, target in enumerate(targets):
        index = by_row.get(row)
        ppm = None if index is None else detections[index]["coordinates"]["a0"]["ppm"]
        rows.append({"row": target["row"], "target_ppm": target["ppm"], "detection_index": index,
                     "eligible_detection_indices": eligible[row], "observed_ppm": ppm,
                     "absolute_error_ppm": None if ppm is None else abs(ppm-target["ppm"])})
    return {"classification": "DESCRIPTIVE_NOT_QUALIFICATION", "rows": rows,
            "matched_count": len(owners), "unmatched_target_rows": [r["row"] for r in rows if r["detection_index"] is None],
            "unmatched_detection_indices": [i for i in range(len(detections)) if i not in owners]}


def cell_volume(values, bounds, params):
    (y0, y1), (x0, x1) = bounds
    cells = ((float(values[y, x])+float(values[y+1, x])+float(values[y, x+1])+float(values[y+1, x+1]))/4
             for y in range(y0, y1) for x in range(x0, x1))
    return math.fsum(cells)*math.prod(abs(h["SW_p"]/h["SI"]) for h in params)


def compare_volume(observed, expected, absolute_volume):
    usable = expected != 0 and abs(expected) > GATES["integral_relative"]*absolute_volume
    error = abs(observed-expected)
    relative = error/abs(expected) if usable else None
    return {"pass": usable and math.isfinite(observed) and relative <= GATES["integral_relative"],
            "expected": expected, "observed": observed, "absolute_error": error,
            "relative_error": relative, "absolute_sample_integral": absolute_volume,
            "denominator_status": "usable" if usable else "UNVERIFIED-zero-or-cancellation"}


def distribution_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def runtime():
    import nmrnext.analysis
    import nmrnext.model
    modules = {}
    for name, module in sorted(sys.modules.copy().items()):
        path = getattr(module, "__file__", None)
        if path and name.split(".")[0] in {"nmrnext", "numpy", "scipy"} and Path(path).is_file():
            path = Path(path).resolve()
            modules[name] = {"path": str(path), "sha256": digest(path)}
    return {"python": sys.version, "executable": str(Path(sys.executable).resolve()),
            "python_sha256": digest(sys.executable), "platform": platform.platform(),
            "versions": {name: distribution_version(name) for name in ("spinflow", "numpy", "scipy")},
            "source_package_version": nmrnext.__version__, "modules": modules,
            "cohort": "source-host-engineering; no installed-Linux or native-processing claim"}


def check_runtime(saved):
    if sys.version != saved["python"] or str(Path(sys.executable).resolve()) != saved["executable"] or digest(sys.executable) != saved["python_sha256"]:
        raise ValueError("Frozen Python runtime changed")
    for name, version in saved["versions"].items():
        if distribution_version(name) != version:
            raise ValueError(f"Frozen dependency changed: {name}")
    for name, record in saved["modules"].items():
        actual = Path(importlib.import_module(name).__file__).resolve()
        if str(actual) != record["path"] or digest(actual) != record["sha256"]:
            raise ValueError(f"Frozen runtime module changed: {name}")


def prepare(data, manifest, protocol, output):
    m = validate_manifest(load(manifest))
    verify_inputs(data, m["files"])
    lock = {"schema": 1, "status": "PREDECLARED_AWAITING_ROOT_APPROVAL",
            "manifest_sha256": digest(manifest), "protocol_sha256": digest(protocol),
            "tool_sha256": digest(__file__), "runtime": runtime()}
    write_new(output, lock)
    return {"status": lock["status"], "lock_sha256": digest(output)}


def run(data, manifest, protocol, lock_path, output, *, lock_sha256):
    from nmrnext.analysis import pick_peaks, integrate_regions
    if Path(output).exists():
        raise FileExistsError(output)
    initial_lock_bytes = Path(lock_path).read_bytes()
    initial_lock_sha256 = hashlib.sha256(initial_lock_bytes).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", lock_sha256) or initial_lock_sha256 != lock_sha256:
        raise ValueError("Scientific lock differs from root-approved lock hash")
    lock = json.loads(initial_lock_bytes.decode("utf-8-sig"))
    if set(lock) != {"schema", "status", "manifest_sha256", "protocol_sha256", "tool_sha256", "runtime"} or type(lock["schema"]) is not int or lock["schema"] != 1:
        raise ValueError("Unknown lock schema")
    if lock["status"] != "PREDECLARED_AWAITING_ROOT_APPROVAL":
        raise ValueError("Unknown scientific lock status")
    if (lock["manifest_sha256"], lock["protocol_sha256"], lock["tool_sha256"]) != (digest(manifest), digest(protocol), digest(__file__)):
        raise ValueError("Frozen protocol, manifest or tool changed")
    check_runtime(lock["runtime"])
    m = validate_manifest(load(manifest))
    verify_inputs(data, m["files"])
    checks, pickers = [], []
    def check(identifier, passed, **evidence):
        checks.append({"id": identifier, "pass": bool(passed), **evidence})
    arrays2d, params2d = None, None
    for case, components in (("1H", ["1r"]), ("1H_13C_HSQC", COMPONENTS)):
        arrays, params = decode_archive(data, case, components)
        values = arrays[0]
        dataset = make_dataset(values, params)
        check(case+"-identity", dataset.data.dtype == np.float64 and np.array_equal(dataset.data[0], values),
              shape=list(values.shape), sample_sha256=hashlib.sha256(values.tobytes()).hexdigest())
        for i, h in enumerate(params):
            for unit in ("Hz", "ppm"):
                error = float(np.max(abs(dataset.coordinates(f"a{i}", unit)-grid(h, unit))))
                limit = GATES["coordinate_ppm"]*(h["SF"] if unit == "Hz" else 1)
                check(f"{case}-grid-{i}-{unit}", error <= limit, maximum_absolute_error=error, limit=limit)
        sigma = amplitude_sigma(values)
        threshold = PICKER["snr"]*sigma
        expected = extrema(values, threshold)
        observed = pick_peaks(dataset, **PICKER)
        comparison = compare_peaks(expected, observed["peaks"], params)
        check(case+"-detections", comparison["pass"], **{k: v for k, v in comparison.items() if k != "pass"})
        check(case+"-threshold", observed["noise"]["sigma"] == sigma and observed["threshold"] == threshold,
              expected_sigma=sigma, observed_sigma=observed["noise"]["sigma"], expected_threshold=threshold, observed_threshold=observed["threshold"])
        check(case+"-untruncated", "max_peaks_reached" not in observed["flags"], flags=observed["flags"])
        record = {"case": case, "headers": params, "oracle_peaks": expected, "product_report": observed, "comparison": comparison}
        if case == "1H":
            record["deposited_target_matching"] = match_targets(m["targets"], observed["peaks"])
        else:
            arrays2d, params2d = arrays, params
        pickers.append(record)
    volume_reports = []
    for channel, values in enumerate(arrays2d):
        dataset = make_dataset(values, params2d)
        check(f"volume-{channel}-identity", np.array_equal(dataset.data[0], values))
        regions = [{"id": r["id"], "unit": "ppm", "bounds": {f"a{i}": b for i, b in enumerate(r["bounds_ppm"])}}
                   for r in m["regions"]]
        observed = integrate_regions(dataset, regions, baseline="none")
        volume_reports.append({"channel": channel, "product_report": observed})
        records = observed.get("regions") if isinstance(observed, dict) else None
        ids = [r.get("id") if isinstance(r, dict) else None for r in records] if isinstance(records, list) else []
        expected_ids = [r["id"] for r in m["regions"]]
        complete = (isinstance(observed, dict) and observed.get("kind") == "integration"
                    and observed.get("schema_version") == "1" and ids == expected_ids)
        check(f"volume-{channel}-report-structure", complete, expected_ids=expected_ids, observed_ids=ids)
        for region in m["regions"]:
            expected = cell_volume(values, region["bounds_points"], params2d)
            absolute = cell_volume(abs(values), region["bounds_points"], params2d)
            matching = [r for r in records if isinstance(r, dict) and r.get("id") == region["id"]] if isinstance(records, list) else []
            measured = matching[0] if len(matching) == 1 else None
            if measured is None or type(measured.get("integral")) not in (int, float) or not math.isfinite(measured["integral"]):
                check(f"volume-{channel}-{region['id']}", False, expected=expected,
                      observed=None, status="MISSING_DUPLICATED_OR_INVALID_REGION", matching_count=len(matching))
                continue
            comparison = compare_volume(measured["integral"], expected, absolute)
            anchored = expected == region["expected_integrals"][channel]
            check(f"volume-{channel}-{region['id']}", comparison["pass"] and anchored and complete,
                  **{k: v for k, v in comparison.items() if k != "pass"}, frozen_anchor_equal=anchored,
                  product_region=measured)
    verify_inputs(data, m["files"])
    check_runtime(lock["runtime"])
    if (lock["manifest_sha256"], lock["protocol_sha256"], lock["tool_sha256"]) != (digest(manifest), digest(protocol), digest(__file__)):
        raise ValueError("Frozen analysis inputs changed during execution")
    if Path(lock_path).read_bytes() != initial_lock_bytes:
        raise ValueError("Scientific lock bytes changed during execution")
    report = {"schema": 1, "classification": "POST_FAILURE_ENGINEERING_NOT_HOLDOUT", "pass": all(c["pass"] for c in checks),
              "s19_qualification": "FAIL_UNCHANGED", "human_acceptance": "UNVERIFIED",
              "lock_sha256": initial_lock_sha256, "approved_lock_sha256": lock_sha256,
              "runtime": lock["runtime"], "inputs_unchanged": True,
              "checks": checks, "pickers": pickers, "volume_reports": volume_reports}
    write_new(output, report)
    return {"pass": report["pass"], "checks": len(checks), "failures": [c["id"] for c in checks if not c["pass"]],
            "report_sha256": digest(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run"))
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--lock-sha256", help="Exact root-approved prepared-lock SHA-256 (required for run)")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    if args.output.resolve().is_relative_to(args.data.resolve()):
        raise ValueError("Output must be outside acquisition directory")
    try:
        if args.action == "prepare":
            result = prepare(args.data, args.manifest, args.protocol, args.output)
        else:
            if args.lock is None or args.lock_sha256 is None:
                parser.error("run requires --lock and --lock-sha256")
            result = run(args.data, args.manifest, args.protocol, args.lock, args.output, lock_sha256=args.lock_sha256)
    except Exception as exc:
        if not args.output.exists():
            write_new(args.output, {"schema": 1, "pass": False, "status": "EXECUTION_ERROR",
                                    "approved_lock_sha256": args.lock_sha256,
                                    "exception_type": type(exc).__name__, "error": str(exc)})
        raise
    print(json.dumps(result, indent=2, allow_nan=False))
    if result.get("pass") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
