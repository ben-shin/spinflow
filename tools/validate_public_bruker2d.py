"""Offline public 2D validation; fetch fixtures with fetch_bruker2d_public.py.

Protocol, fixed physical regions, tolerances, source URLs and SHA256 hashes are
in tests/fixtures/bruker2d_public_manifest.json. Report JSON is private evidence.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import time

import nmrglue as ng
import numpy as np
from scipy.interpolate import RegularGridInterpolator

from nmrnext.analysis import integrate_regions, autophase
from nmrnext.io import read_dataset
from nmrnext.model import Axis, Dataset
from nmrnext.operations import run_operations

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "tests/fixtures/bruker2d_public_manifest.json"
DEFAULT_ROOT = REPO / ".artifacts/bruker2d-realdata/bmse000001"
SOURCE_FILES = (MANIFEST, Path(__file__), REPO/"src/nmrnext/analysis.py",
                REPO/"src/nmrnext/io/__init__.py", REPO/"src/nmrnext/io/bruker2d.py",
                REPO/"src/nmrnext/bruker.py", REPO/"src/nmrnext/operations.py")


def source_fingerprints():
    return {p.relative_to(REPO).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCE_FILES}


def window_endpoint_policy(source, retained, recipe):
    """Report exponential factors separately from additional first-point weights."""
    result = {}
    for operation in recipe:
        if operation["op"] != "window":
            continue
        identity = operation["axis"]
        original, actual = source.axis(identity), retained.axis(identity)
        original_time = (original.size-1)*original.step
        retained_time = (actual.size-1)*actual.step
        original_factor = math.exp(-math.pi*operation["lb_hz"]*original_time)
        retained_factor = math.exp(-math.pi*operation["lb_hz"]*retained_time)
        result[identity] = {"lb_hz_basis":"original acquisition grid before digital-filter trimming",
                            "original_points":original.size, "retained_points":actual.size,
                            "original_last_time_seconds":original_time, "retained_last_time_seconds":retained_time,
                            "original_endpoint_exponential_factor":original_factor,
                            "retained_endpoint_exponential_factor":retained_factor,
                            "separate_first_point_scale":operation["first_point_scale"],
                            "retained_endpoint_to_weighted_first_point_ratio":retained_factor/operation["first_point_scale"]}
    return result


def verify_sources(root, manifest):
    for item in manifest["files"]:
        path = root/item["path"]
        if path.stat().st_size != item["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise RuntimeError(f"Public acquisition checksum mismatch: {item['path']}")


def reference_arrays(root, case):
    """Independent bounded decode of these exact archived contiguous rows.

    Both acquisitions have direct XDIM=SI=1024, so indirect submatrix blocks
    concatenate as complete rows. Reject other layouts instead of guessing.
    """
    directory = root/case["path"]/"pdata/1"
    params = [ng.bruker.read_jcamp(str(directory/name)) for name in ("proc2s", "procs")]
    shape = tuple(int(p["SI"]) for p in params)
    if params[1]["XDIM"] != shape[1] or shape[0] % params[0]["XDIM"]:
        raise ValueError("Reference is outside declared full-row tile layout")
    arrays = []
    for name in ("2rr", "2ir", "2ri", "2ii"):
        dtype = np.dtype("<i4" if params[1]["BYTORDP"] == 0 else ">i4")
        if params[1]["DTYPP"] != 0:
            raise ValueError("Expected recorded int32 processed reference")
        data = np.fromfile(directory/name, dtype=dtype)
        if data.size != np.prod(shape):
            raise ValueError("Unexpected processed reference length")
        arrays.append(data.reshape(shape).astype(float)*2.**params[1]["NC_proc"])
    ppm = [p["OFFSET"]-np.arange(p["SI"])*p["SW_p"]/(p["SF"]*p["SI"]) for p in params]
    return np.stack(arrays), ppm, params


def reference_dataset(arrays, params):
    # Real channel only: this part tests signed integration on independently
    # acquired, immutable reference data, not raw processing or codec parity.
    axes = [Axis(id=f"a{i}", size=int(p["SI"]), domain="frequency", unit="Hz",
                 start=p["OFFSET"]*p["SF"], step=-p["SW_p"]/p["SI"],
                 observation_mhz=p["SF"], reference_ppm=0., reference_hz=0., encoding="real")
            for i, p in enumerate(params)]
    return Dataset(arrays[:1], axes)


def regions_for(case):
    return [{"id":r["id"], "unit":"ppm", "bounds":{f"a{i}":b for i,b in enumerate(r["bounds_ppm"])}}
            for r in case["regions"]]


def independent_volume(array, ppm, params, bounds):
    """Separate interpolation and tensor-product trapezoidal-weight oracle."""
    grids, weights = [], []
    for coordinates, p, (lo, hi) in zip(ppm, params, bounds):
        inner = coordinates[(coordinates > lo) & (coordinates < hi)]
        nodes = np.sort(np.concatenate(([lo, hi], inner)))
        hz = nodes*p["SF"]
        w = np.empty(nodes.size)
        w[0], w[-1] = (hz[1]-hz[0])/2, (hz[-1]-hz[-2])/2
        w[1:-1] = (hz[2:]-hz[:-2])/2
        grids.append(nodes)
        weights.append(w)
    mesh = np.meshgrid(*grids, indexing="ij")
    interp = RegularGridInterpolator(tuple(p[::-1] for p in ppm), array[::-1, ::-1])
    samples = interp(np.stack(mesh, axis=-1))
    return float(weights[0] @ samples @ weights[1])


def raw_recipe(root, case):
    directory = root/case["path"]
    # Calibration is fixed from acquisition and archived SF, before comparing
    # observed peaks. No reference peak is used by the phase estimator.
    recipe = [{"op":"digital_filter", "axis":"a1"}]
    source = read_dataset(directory)
    for i, (acq, proc) in enumerate((("acqu2s", "proc2s"), ("acqus", "procs"))):
        a = ng.bruker.read_jcamp(str(directory/acq))
        p = ng.bruker.read_jcamp(str(directory/"pdata/1"/proc))
        recipe += [{"op":"window", "axis":f"a{i}", "kind":"exponential", "lb_hz":math.log(100)/(math.pi*(source.axis(f"a{i}").size-1)*source.axis(f"a{i}").step), "first_point_scale":.5},
                   {"op":"zero_fill", "axis":f"a{i}", "size":1024},
                   {"op":"fft", "axis":f"a{i}"},
                   {"op":"reference", "axis":f"a{i}", "reference_ppm":(a["SFO1"]-p["SF"])*1e6/p["SF"],
                    "reference_hz":0., "observation_mhz":p["SF"]}]
    return recipe


def maxima(dataset, case):
    ppm = [dataset.coordinates(f"a{i}", "ppm") for i in range(2)]
    output = []
    for region in case["regions"]:
        indices = [np.flatnonzero((p >= b[0]) & (p <= b[1])) for p,b in zip(ppm,region["bounds_ppm"])]
        box = dataset.data[0][np.ix_(*indices)]
        ij = np.unravel_index(np.argmax(box), box.shape)
        output.append({"id":region["id"], "ppm_indirect_direct":[float(p[ix[j]]) for p,ix,j in zip(ppm,indices,ij)],
                       "positive_height":float(box[ij])})
    return output


def validate_reference(root, case):
    arrays, ppm, params = reference_arrays(root, case)
    ref = reference_dataset(arrays, params)
    volumes = integrate_regions(ref, regions_for(case), baseline="none")
    checks = []
    for measured, region in zip(volumes["regions"], case["regions"]):
        expected = independent_volume(arrays[0], ppm, params, region["bounds_ppm"])
        error = abs(measured["integral"]-expected)/max(abs(expected), 1.)
        checks.append({"id":region["id"], "integral":measured["integral"], "independent_integral":expected, "relative_error":error})
    return ref, {"reference_integration":checks, "reference_maxima":maxima(ref, case),
                 "reference_integration_pass":all(c["relative_error"] <= 1e-10 for c in checks)}


def validate_case(root, case, backend="numpy"):
    ref, report = validate_reference(root, case)
    source = read_dataset(root/case["path"])
    recipe = raw_recipe(root, case)
    before = time.perf_counter()
    retained = run_operations(source, recipe[:1], backend=backend)
    transformed = run_operations(source, recipe, backend=backend)
    phased, phase = autophase(transformed, first_order=case["autophase_first_order"], backend=backend)
    replayed = run_operations(transformed, phase["operations"], backend=backend)
    np.testing.assert_array_equal(phased.data, replayed.data)
    raw_volumes = integrate_regions(phased, regions_for(case), baseline="none")["regions"]
    ref_volumes = report["reference_integration"]
    raw_anchor, ref_anchor = raw_volumes[0]["integral"], ref_volumes[0]["integral"]
    comparisons = []
    for raw, archived in zip(raw_volumes, ref_volumes):
        ratio, expected = raw["integral"]/raw_anchor, archived["integral"]/ref_anchor
        comparisons.append({"id":raw["id"], "raw_integral":raw["integral"], "raw_ratio":ratio,
                            "reference_ratio":expected, "ratio_relative_error":abs(ratio-expected)/abs(expected),
                            "same_sign":bool(np.sign(raw["integral"]) == np.sign(archived["integral"]))})
    report.update(id=case["id"], shape=list(source.data.shape), recipe=recipe, phase=phase,
                  raw_maxima=maxima(phased, case), volume_ratios=comparisons,
                  window_endpoint_policy=window_endpoint_policy(source, retained, recipe),
                  seconds=time.perf_counter()-before, backend=backend)
    position_checks = []
    for actual, expected in zip(report["raw_maxima"], report["reference_maxima"]):
        differences = [abs(a-b) for a,b in zip(actual["ppm_indirect_direct"], expected["ppm_indirect_direct"])]
        tolerances = [2*case["processed"][dim]["SW_p"]/(case["processed"][dim]["SI"]*case["processed"][dim]["SF"]) for dim in ("indirect","direct")]
        position_checks.append({"id":actual["id"], "absolute_difference_ppm":differences,
                                "tolerance_ppm":tolerances, "pass":all(a <= b for a,b in zip(differences,tolerances))})
    positive_peak_checks = []
    for expected, actual in zip(case["positive_peaks_ppm_indirect_direct"], report["raw_maxima"]):
        differences = [abs(a-b) for a,b in zip(actual["ppm_indirect_direct"], expected)]
        tolerances = [2*case["processed"][dim]["SW_p"]/(case["processed"][dim]["SI"]*case["processed"][dim]["SF"]) for dim in ("indirect","direct")]
        positive_peak_checks.append({"curated_ppm_indirect_direct":expected, "observed_ppm_indirect_direct":actual["ppm_indirect_direct"],
                                     "absolute_difference_ppm":differences, "tolerance_ppm":tolerances,
                                     "pass":all(a <= b for a,b in zip(differences,tolerances))})
    supplemental = []
    for entry in case.get("supplemental_curated_peaks", []):
        tolerances = [2*case["processed"][dim]["SW_p"]/(case["processed"][dim]["SI"]*case["processed"][dim]["SF"]) for dim in ("indirect","direct")]
        bounds = [[center-tolerance,center+tolerance] for center,tolerance in zip(entry["ppm_indirect_direct"],tolerances)]
        diagnostic = {"regions":[{"id":"supplemental_LR", "bounds_ppm":bounds}]}
        observed = maxima(phased,diagnostic)[0]
        archived = maxima(ref,diagnostic)[0]
        supplemental.append(dict(entry, neighborhood_halfwidth_ppm=tolerances, raw_neighborhood=observed,
                                 reference_neighborhood=archived,
                                 raw_height_fraction_of_strongest=float(observed["positive_height"]/phased.data[0].max()),
                                 reference_height_fraction_of_strongest=float(archived["positive_height"]/ref.data[0].max()),
                                 interpretation="Positive neighborhood signal only, not an assertion of separately resolved peak recovery"))
    report["supplemental_curated_peaks"] = supplemental
    report["positive_peak_checks"] = positive_peak_checks
    report["position_checks"] = position_checks
    report["pass"] = (report["reference_integration_pass"] and all(p["pass"] for p in position_checks)
                      and all(c["same_sign"] and c["ratio_relative_error"] <= .30 for c in comparisons)
                      and all(c["pass"] for c in positive_peak_checks))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--backend", choices=("numpy", "native"), default="numpy")
    parser.add_argument("--reference-only", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    verify_sources(args.root, manifest)
    cases = [validate_reference(args.root, c)[1] if args.reference_only else validate_case(args.root,c,args.backend) for c in manifest["cases"]]
    report = {"manifest":manifest["id"], "protocol":manifest["protocol"], "cases":cases,
              "source_sha256":source_fingerprints(),
              "numpy_version":np.__version__, "nmrglue_version":ng.__version__}
    verify_sources(args.root, manifest)
    report["source_untouched"] = True
    report["pass"] = all(c["reference_integration_pass"] if args.reference_only else c["pass"] for c in cases)
    print(json.dumps(report, indent=2))
    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
