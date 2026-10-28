"""Real acquired data, hash-pinned; skipped only when explicitly not fetched.

The independent TopSpin archive checks physical peak recovery, not equivalence
of digital-filter/phase/amplitude policies. Analytical unit tests establish the
exact filter operation. nmrglue comparison is differential corroboration only.
"""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import nmrglue as ng
import numpy as np
import pytest

from nmrnext.io import read_dataset
from nmrnext.operations import run_operations


def test_public_bmrb_acquisition_peak_recovery_and_native_numpy_agreement():
    repo = Path(__file__).resolve().parents[2]
    root = repo/".artifacts/science11-realdata/bmse000001"
    manifest = json.loads((repo/"tests/fixtures/bruker_public_manifest.json").read_text())
    if not (root/"1H/fid").exists():
        pytest.skip("Public acquisition not fetched; run python tests/fixtures/fetch_bruker_public.py")
    for item in manifest["files"]:
        assert hashlib.sha256((root/item["path"]).read_bytes()).hexdigest() == item["sha256"]
    source = read_dataset(root/"1H")
    acquisition = ng.bruker.read_jcamp(str(root/"1H/acqus"))
    reference = ng.bruker.read_jcamp(str(root/"1H/pdata/1/procs"))
    sf = reference["SF"]
    carrier = (acquisition["SFO1"]-sf)*1e6/sf
    recipe = [{"op":"digital_filter","axis":"a0"},
              {"op":"zero_fill","axis":"a0","size":32768},
              {"op":"fft","axis":"a0"},
              {"op":"reference","axis":"a0","reference_ppm":carrier,
               "reference_hz":0.,"observation_mhz":sf}]
    result = run_operations(source,recipe)
    actual = result.data[0]+1j*result.data[1]
    ppm = result.coordinates("a0","ppm")
    # Reference parameters and peak list were recorded in 2011, independently
    # of this implementation. No shifts, reversals or fitted alignment allowed.
    peaks = [float(node.attrib["F1"]) for node in ET.parse(root/"1H/pdata/1/peaklist.xml").iter("Peak1D")]
    for peak in peaks:
        region = np.flatnonzero(abs(ppm-peak) < .004)
        observed = ppm[region[np.argmax(abs(actual[region]))]]
        assert abs(observed-peak) < .002
    # Native backend availability is independent of fetching the public corpus.
    # Always exercise NumPy above; native parity runs in installations with it.
    try:
        import nmrnext._native  # noqa: F401
    except ImportError:
        return
    native = run_operations(source,recipe,backend="native")
    np.testing.assert_allclose(native.data,result.data,rtol=1e-11,atol=1e-5)
    assert source.metadata["import"]["digital_filter"]["state"] == "uncorrected_or_unknown"
