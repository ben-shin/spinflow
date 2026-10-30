"""Bounded bmse000002 qualification. Preparation never imports SpinFlow.

Inputs are fetched separately; all commands refuse overwriting their reports.
See docs/bruker-qualification-protocol.md for claims and fixed gates.
"""
import argparse
import dataclasses
import hashlib
import importlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import re
import sys
import xml.etree.ElementTree as ET

import numpy as np

GATES = {"filter_rtol": 1e-11, "filter_atol": 1e-7,
         "native_rtol": 1e-11, "native_atol": 1e-5,
         "peak_strict_ppm": .002, "peak_search_strict_ppm": .004,
         "archive_integral_relative": 1e-10, "coordinate_ppm": 2e-5}
BASE = "https://bmrb.io/ftp/pub/bmrb/metabolomics/entry_directories/bmse000002/nmr/set01/"
CASE_2D = "1H_13C_HSQC"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def scalars(path):
    """Independent scalar-only JCAMP reader for these pinned headers."""
    result = {}
    for line in Path(path).read_text(encoding="latin1").splitlines():
        match = re.fullmatch(r"##\$([^=]+)=\s*(.*)", line)
        if not match:
            continue
        key, value = match.groups()
        if key in result:
            raise ValueError(f"Duplicate scalar {key}")
        if value.startswith("("):
            continue
        if value.startswith("<") and value.endswith(">"):
            result[key] = value[1:-1]
        else:
            try:
                result[key] = int(value)
            except ValueError:
                try:
                    result[key] = float(value)
                except ValueError:
                    result[key] = value
    return result


def verify_inputs(root, manifest):
    for item in manifest["files"]:
        path = root / item["path"]
        if path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
            raise ValueError(f"Input identity mismatch: {item['path']}")


def raw_decode(directory, dimensions):
    """Decode exact acquired integer channels without vendor/product readers."""
    a = scalars(directory / "acqus")
    if (a["DTYPA"], a["BYTORDA"], a["AQ_mod"]) != (0, 0, 3):
        raise ValueError("Expected pinned little-endian int32 complex profile")
    td = a["TD"]
    padded = math.ceil(td * 4 / 1024) * 256
    if dimensions == 1:
        raw = np.frombuffer((directory / "fid").read_bytes(), dtype="<i4")
        if raw.size != padded or td % 2:
            raise ValueError("Incomplete 1D payload")
        return np.stack((raw[:td:2], raw[1:td:2])).astype(np.float64)
    b = scalars(directory / "acqu2s")
    if (b["FnMODE"], a["AQSEQ"], a["PARMODE"], a["FnTYPE"]) != (6, 0, 1, 0):
        raise ValueError("Expected pinned fully sampled Echo/AntiEcho profile")
    raw = np.frombuffer((directory / "ser").read_bytes(), dtype="<i4")
    if raw.size != b["TD"] * padded or b["TD"] % 2 or td % 2:
        raise ValueError("Incomplete 2D payload")
    rows = raw.reshape(b["TD"], padded)[:, :td].astype(np.float64)
    er, ei = rows[::2, ::2], rows[::2, 1::2]
    ar, ai = rows[1::2, ::2], rows[1::2, 1::2]
    # C=E-A; S=i(E+A). No averaging; indirect bit first.
    return np.stack((er-ar, -ei-ai, ei-ai, er+ar))


def archive_decode(directory):
    """Independent int32 full-row-tile decoder, retaining all four files."""
    p = [scalars(directory / name) for name in ("proc2s", "procs")]
    shape = tuple(v["SI"] for v in p)
    if p[1]["XDIM"] != shape[1] or shape[0] % p[0]["XDIM"]:
        raise ValueError("Unsupported archived tiling")
    if any((v["BYTORDP"], v["DTYPP"], v["NC_proc"]) != (0, 0, -6) for v in p):
        raise ValueError("Unexpected archived binary profile")
    arrays = []
    for name in ("2rr", "2ir", "2ri", "2ii"):
        values = np.frombuffer((directory / name).read_bytes(), dtype="<i4")
        if values.size != math.prod(shape):
            raise ValueError("Wrong archived array length")
        arrays.append(values.reshape(shape).astype(np.float64) * 2.**-6)
    ppm = [v["OFFSET"] - np.arange(v["SI"]) * v["SW_p"] / (v["SF"]*v["SI"]) for v in p]
    return np.stack(arrays), p, ppm


def select_reference_regions(real, ppm):
    """At most two extrema of each sign; deterministic disjoint 9x9 boxes.

    These are mathematical test rectangles, not assigned or resolved peaks.
    Selection uses only the archived RR array, never candidate outputs.
    """
    unavailable = np.zeros(real.shape, dtype=bool)
    unavailable[:4, :] = unavailable[-4:, :] = True
    unavailable[:, :4] = unavailable[:, -4:] = True
    regions = []
    for sign in (1, -1):
        for rank in (1, 2):
            scores = np.where(unavailable, -np.inf, sign * real)
            index = np.unravel_index(np.argmax(scores), scores.shape)
            if scores[index] <= 0:
                continue
            y, x = (int(v) for v in index)
            bounds = [[y-4, y+4], [x-4, x+4]]
            regions.append({"id": f"{'positive' if sign == 1 else 'negative'}-{rank}",
                            "center_points": [y, x], "reference_center_value": float(real[index]),
                            "bounds_points": bounds,
                            "bounds_ppm": [sorted([float(grid[lo]), float(grid[hi])]) for grid, (lo, hi) in zip(ppm, bounds)]})
            # Inclusive boxes are disjoint only if at least one center distance >8.
            unavailable[max(0,y-8):y+9, max(0,x-8):x+9] = True
    return regions


def archive_oracle(array, bounds, params):
    """Independent scalar cell-area sum: four corners per acquired grid cell."""
    (y0, y1), (x0, x1) = bounds
    terms = []
    for y in range(y0, y1):
        for x in range(x0, x1):
            terms.append(float(array[y,x]+array[y+1,x]+array[y,x+1]+array[y+1,x+1]) / 4)
    area = math.prod(abs(p["SW_p"] / p["SI"]) for p in params)
    return math.fsum(terms) * area


def prepare(root, manifest_path, protocol_path, output):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    verify_inputs(root, manifest)
    targets = [{"row": i+1, "ppm": float(node.attrib["F1"]), "deposited_attributes": dict(node.attrib)}
               for i, node in enumerate(ET.parse(root/"1H/pdata/1/peaklist.xml").iter("Peak1D"))]
    if not targets:
        raise ValueError("No deposited targets")
    a, p = scalars(root/"1H/acqus"), scalars(root/"1H/pdata/1/procs")
    recipe = [{"op":"digital_filter", "axis":"a0"},
              {"op":"zero_fill", "axis":"a0", "size":32768},
              {"op":"fft", "axis":"a0"},
              {"op":"reference", "axis":"a0", "reference_ppm":(a["SFO1"]-p["SF"])*1e6/p["SF"],
               "reference_hz":0., "observation_mhz":p["SF"]}]
    arrays, params, ppm = archive_decode(root/CASE_2D/"pdata/1")
    regions = select_reference_regions(arrays[0], ppm)
    for region in regions:
        region["expected_integrals"] = [archive_oracle(channel, region["bounds_points"], params) for channel in arrays]
    lock = {"schema":1, "status":"PREDECLARED_AWAITING_ROOT_AND_INSTALLED_CANDIDATE",
            "source_entry":"bmse000002", "manifest_sha256":digest(manifest_path),
            "protocol_sha256":digest(protocol_path), "harness_sha256":digest(__file__),
            "gates":GATES, "recipe":recipe, "targets":targets, "archive_regions":regions,
            "weak_strong_claim":False, "weak_strong_reason":"At most four mathematical extrema boxes; not five independent assigned regions",
            "archive_shape":list(arrays.shape), "archive_dtype":str(arrays.dtype),
            "archive_parameters":params,
            "exposure":"Metadata, deposited peak list and reference-only arrays seen; no candidate acquired outputs",
            "preparation_runtime":{"python":platform.python_version(), "numpy":np.__version__},
            "unverified":["raw-to-TopSpin processing parity", "NMRPipe parity", "raw phase/sign/volume accuracy", "sensitivity", "biological assignments", "concentrations", "human acceptance"]}
    write_new(output, lock)
    return {"lock_sha256":digest(output), "target_count":len(targets), "archive_regions":regions}


def environment(output):
    names = ("nmrnext", "nmrnext._native", "nmrnext.analysis", "nmrnext.model", "nmrnext.io",
             "nmrnext.io.bruker2d", "nmrnext.bruker", "nmrnext.operations", "nmrnext.planning",
             "nmrglue", "nmrglue.fileio.bruker", "numpy")
    records = {}
    for name in names:
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        records[name] = {"path":str(path), "sha256":digest(path)}
    report = {"python":sys.version, "executable":sys.executable, "platform":platform.platform(),
              "versions":{name:importlib.metadata.version(name) for name in ("spinflow","numpy","nmrglue")},
              "modules":records}
    write_new(output, report)
    return report


def execute(root, manifest_path, protocol_path, lock_path, environment_path, output):
    from nmrnext.analysis import integrate_regions
    from nmrnext.io import read_dataset
    from nmrnext.model import Axis, Dataset
    from nmrnext.operations import run_operations
    import nmrglue as ng

    lock = json.loads(lock_path.read_text())
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    env = json.loads(environment_path.read_text())
    if lock["gates"] != GATES or lock["manifest_sha256"] != digest(manifest_path) or lock["protocol_sha256"] != digest(protocol_path) or lock["harness_sha256"] != digest(__file__):
        raise ValueError("Frozen scientific inputs changed")
    if env["versions"]["spinflow"] != "0.3.0b2":
        raise ValueError("Installed candidate must be 0.3.0b2")
    for name, record in env["modules"].items():
        actual = Path(importlib.import_module(name).__file__).resolve()
        if str(actual) != record["path"] or digest(actual) != record["sha256"]:
            raise ValueError(f"Frozen runtime changed: {name}")
    verify_inputs(root, manifest)
    checks = []
    def check(name, passed, **evidence):
        checks.append({"id":name, "pass":bool(passed), **evidence})
    sources = {}
    for case, dimensions in (("1H",1),(CASE_2D,2)):
        expected = raw_decode(root/case, dimensions)
        actual = read_dataset(root/case)
        sources[case] = actual
        check(case+"-raw", actual.data.dtype == np.float64 and actual.data.shape == expected.shape and np.array_equal(actual.data, expected),
              shape=list(actual.data.shape), dtype=str(actual.data.dtype), maximum_absolute_error=float(np.max(abs(actual.data-expected))),
              source_digest=hashlib.sha256(expected.tobytes()).hexdigest())
        headers = [scalars(root/case/name) for name in (("acqus",) if dimensions==1 else ("acqu2s","acqus"))]
        bit_ids = tuple(f"a{i}" for i in range(dimensions))
        check(case+"-bits", actual.complex_axes == bit_ids, actual=list(actual.complex_axes), expected=list(bit_ids))
        for i, h in enumerate(headers):
            width = h["SW"]*h["SFO1"] if dimensions==2 and i==0 else h["SW_h"]
            ax = actual.axes[i]
            wanted = np.arange(expected.shape[i+1])*(1/width)
            check(case+f"-axis-{i}", ax.id==f"a{i}" and ax.nucleus==h["NUC1"] and ax.domain=="time" and ax.unit=="s" and ax.reference_ppm is None and ax.observation_mhz==h["SFO1"] and ax.dwell_seconds==1/width and ax.encoding==("states" if dimensions==2 and i==0 else "complex") and np.array_equal(actual.coordinates(ax.id), wanted),
                  maximum_time_error=float(np.max(abs(actual.coordinates(ax.id)-wanted))), points=ax.size, nucleus=ax.nucleus,
                  observation_mhz=ax.observation_mhz, dwell_seconds=ax.dwell_seconds, axis=dataclasses.asdict(ax))
    source = sources["1H"]
    recipe = lock["recipe"]
    a = scalars(root/"1H/acqus")
    corrected = run_operations(source,recipe[:1],backend="numpy")
    independent_filter = ng.bruker.rm_dig_filter(source.data[0]+1j*source.data[1], a["DECIM"],a["DSPFVS"],a["GRPDLY"],truncate_grpdly=False)
    observed_filter = corrected.data[0]+1j*corrected.data[1]
    check("digital-filter", corrected.data.dtype==np.float64 and np.allclose(observed_filter,independent_filter,rtol=GATES["filter_rtol"],atol=GATES["filter_atol"]),
          maximum_absolute_error=float(np.max(abs(observed_filter-independent_filter))), shape=list(corrected.data.shape), dtype=str(corrected.data.dtype))
    numpy_result = run_operations(source,recipe,backend="numpy")
    native_result = run_operations(source,recipe,backend="native",threads=1)
    check("native-numpy", native_result.data.dtype==numpy_result.data.dtype==np.float64 and native_result.complex_axes==numpy_result.complex_axes and native_result.axes==numpy_result.axes and np.allclose(native_result.data,numpy_result.data,rtol=GATES["native_rtol"],atol=GATES["native_atol"]),
          maximum_absolute_error=float(np.max(abs(native_result.data-numpy_result.data))), shape=list(native_result.data.shape), dtype=str(native_result.data.dtype))
    ppm = native_result.coordinates("a0","ppm")
    ref = recipe[-1]
    # Fixed negative-exponent centered FFT grid; no fitted shift/reversal.
    expected_ppm = ref["reference_ppm"] + (np.arange(32768)-16384)*a["SW_h"]/32768/ref["observation_mhz"]
    coordinate_error = float(np.max(abs(ppm-expected_ppm)))
    check("1d-absolute-grid", coordinate_error<=GATES["coordinate_ppm"], maximum_error_ppm=coordinate_error)
    magnitude = np.hypot(*native_result.data)
    for target in lock["targets"]:
        indices = np.flatnonzero(abs(ppm-target["ppm"])<GATES["peak_search_strict_ppm"])
        observed = float(ppm[indices[np.argmax(magnitude[indices])]]) if len(indices) else None
        error = abs(observed-target["ppm"]) if observed is not None else None
        check(f"peak-{target['row']}", error is not None and error<GATES["peak_strict_ppm"], target=target, observed_ppm=observed, error_ppm=error)
    arrays, params, grids = archive_decode(root/CASE_2D/"pdata/1")
    axes = [Axis(id=f"a{i}",size=p["SI"],domain="frequency",unit="Hz",start=p["OFFSET"]*p["SF"],step=-p["SW_p"]/p["SI"],observation_mhz=p["SF"],reference_ppm=0.,reference_hz=0.,encoding="real") for i,p in enumerate(params)]
    for channel, values in enumerate(arrays):
        dataset = Dataset(values[None,...],axes)
        check(f"archive-{channel}-identity", dataset.data.dtype==np.float64 and not dataset.complex_axes and np.array_equal(dataset.data[0],values), shape=list(dataset.data.shape), dtype=str(dataset.data.dtype))
        for i, grid in enumerate(grids):
            error = float(np.max(abs(dataset.coordinates(f"a{i}","ppm")-grid)))
            check(f"archive-{channel}-grid-{i}", error<=GATES["coordinate_ppm"], maximum_error_ppm=error)
        regions = [{"id":r["id"],"unit":"ppm","bounds":{f"a{i}":b for i,b in enumerate(r["bounds_ppm"])}} for r in lock["archive_regions"]]
        results = integrate_regions(dataset,regions,baseline="none")["regions"]
        for measured, r in zip(results,lock["archive_regions"]):
            expected = r["expected_integrals"][channel]
            fresh_expected = archive_oracle(values,r["bounds_points"],params)
            absolute_volume = archive_oracle(abs(values),r["bounds_points"],params)
            usable = abs(expected)>absolute_volume*GATES["archive_integral_relative"] and expected!=0
            relative = abs(measured["integral"]-expected)/abs(expected) if usable else None
            check(f"archive-{channel}-volume-{r['id']}", fresh_expected==expected and relative is not None and relative<=GATES["archive_integral_relative"],
                  expected=expected, observed=measured["integral"], absolute_error=abs(measured["integral"]-expected), relative_error=relative, flags=measured.get("flags",[]), denominator_status="nonzero" if usable else "zero-or-cancellation-dominated-unverified", absolute_volume=absolute_volume)
    verify_inputs(root,manifest)
    report = {"schema":1,"lock_sha256":digest(lock_path),"environment_sha256":digest(environment_path),
              "checks":checks,"pass":all(c["pass"] for c in checks),"unverified":lock["unverified"],
              "weak_strong_claim":False,"inputs_unchanged":True}
    write_new(output,report)
    return {"pass":report["pass"],"checks":len(checks),"failures":[c for c in checks if not c["pass"]],"report_sha256":digest(output)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("prepare","environment","run"))
    p.add_argument("--data",type=Path)
    p.add_argument("--manifest",type=Path)
    p.add_argument("--protocol",type=Path)
    p.add_argument("--lock",type=Path)
    p.add_argument("--environment-lock",type=Path)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    if a.action=="environment":
        result=environment(a.output)
    elif a.action=="prepare":
        result=prepare(a.data,a.manifest,a.protocol,a.output)
    else:
        try:
            result=execute(a.data,a.manifest,a.protocol,a.lock,a.environment_lock,a.output)
        except Exception as exc:
            # Preserve a failed execution receipt; never infer a pass or omit
            # the fact that downstream target checks could not all complete.
            if not a.output.exists():
                write_new(a.output,{"pass":False,"status":"EXECUTION_ERROR",
                                   "exception_type":type(exc).__name__,"error":str(exc),
                                   "target_checks_complete":False})
            raise
    print(json.dumps(result,indent=2,allow_nan=False))
    if result.get("pass") is False:
        raise SystemExit(1)


if __name__=="__main__":
    main()
