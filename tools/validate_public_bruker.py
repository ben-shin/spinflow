"""Reproduce the bounded public Bruker validation without network access.

First run: python tests/fixtures/fetch_bruker_public.py
Then: python tools/validate_public_bruker.py > local-report.json
The JSON includes the exact recipe, source hashes, versions and limitations.
"""
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import nmrglue as ng
import numpy as np

from nmrnext.io import read_dataset
from nmrnext.operations import run_operations


def main():
    repo = Path(__file__).resolve().parents[1]
    root = repo/".artifacts/science11-realdata/bmse000001"
    manifest = json.loads((repo/"tests/fixtures/bruker_public_manifest.json").read_text())
    for item in manifest["files"]:
        if hashlib.sha256((root/item["path"]).read_bytes()).hexdigest() != item["sha256"]:
            raise RuntimeError(f"Public acquisition checksum mismatch: {item['path']}")
    acq = root/"1H"
    a = ng.bruker.read_jcamp(str(acq/"acqus"))
    reference = ng.bruker.read_jcamp(str(acq/"pdata/1/procs"))
    sf = reference["SF"]
    carrier = (a["SFO1"]-sf)*1e6/sf
    recipe = [{"op":"digital_filter","axis":"a0"},
              {"op":"zero_fill","axis":"a0","size":32768},
              {"op":"fft","axis":"a0"},
              {"op":"reference","axis":"a0","reference_ppm":carrier,
               "reference_hz":0.,"observation_mhz":sf}]
    source = read_dataset(acq)
    before = time.perf_counter()
    result = run_operations(source,recipe)
    elapsed = time.perf_counter()-before
    z = result.data[0]+1j*result.data[1]
    ppm = result.coordinates("a0","ppm")
    peaks = []
    for node in ET.parse(acq/"pdata/1/peaklist.xml").iter("Peak1D"):
        expected = float(node.attrib["F1"])
        region = np.flatnonzero(abs(ppm-expected) < .004)
        observed = float(ppm[region[np.argmax(abs(z[region]))]])
        peaks.append({"reference_ppm":expected, "magnitude_max_ppm":observed,
                      "difference_ppm":observed-expected})
    native_error = None
    if importlib.util.find_spec("nmrnext._native") is not None:
        native = run_operations(source,recipe,backend="native")
        native_error = float(abs(native.data-result.data).max())
        np.testing.assert_allclose(native.data,result.data,rtol=1e-11,atol=1e-5)
    corrected = run_operations(source,[recipe[0]])
    baseline = ng.bruker.rm_dig_filter(source.data[0]+1j*source.data[1],
        a["DECIM"],a["DSPFVS"],a["GRPDLY"],truncate_grpdly=False)
    differential_error = float(abs(corrected.data[0]+1j*corrected.data[1]-baseline).max())
    np.testing.assert_allclose(corrected.data[0]+1j*corrected.data[1],baseline,rtol=1e-11,atol=1e-7)
    files = ["src/nmrnext/bruker.py","src/nmrnext/operations.py","src/nmrnext/planning.py",
             "src/nmrnext/io/__init__.py","tests/unit/test_bruker_filter.py",
             "tests/io/test_bruker_public.py","tests/fixtures/bruker_public_manifest.json",
             "tests/fixtures/fetch_bruker_public.py","tools/validate_public_bruker.py"]
    report = {"id":manifest["id"], "recipe":recipe, "data_files":manifest["files"],
        "numpy_version":np.__version__, "nmrglue_version":ng.__version__,
        "nmrglue_bruker_source_sha256":hashlib.sha256(Path(inspect.getfile(ng.bruker)).read_bytes()).hexdigest(),
        "numpy_pipeline_seconds_single_run":elapsed, "native_numpy_max_abs":native_error,
        "native_checked":native_error is not None,
        "nmrglue_fractional_differential_max_abs":differential_error,
        "archived_topspin_peak_comparison":peaks,
        "maximum_peak_difference_ppm":max(abs(p["difference_ppm"]) for p in peaks),
        "peak_tolerance_ppm":.002,
        "limitations":["Independent TopSpin check is physical peak location only; no amplitude, phase, baseline or integral equivalence.",
            "Analytical unit tests establish the filter policy; nmrglue comparison is differential corroboration.",
            "No NMRPipe executable comparison; only positive recorded GRPDLY and raw 1D complex acquisition supported.",
            "Timing is one local run, not a benchmark or speedup claim."],
        "source_freeze_sha256":{f:hashlib.sha256((repo/f).read_bytes()).hexdigest() for f in files}}
    report["source_untouched"] = all(hashlib.sha256((root/it["path"]).read_bytes()).hexdigest()==it["sha256"] for it in manifest["files"])
    report["pass"] = report["source_untouched"] and report["maximum_peak_difference_ppm"] < .002
    print(json.dumps(report,indent=2))
    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
