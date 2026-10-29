"""Optional offline real-acquisition validation plus always-on provenance checks.

Set SPINFLOW_BRUKER2D_ROOT to the downloaded bmse000001 cache to enable
scientific integration checks. CI never fetches data implicitly.
"""
import hashlib
import importlib.util
import io
import json
import math
from types import SimpleNamespace
import os
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetcher = module_at("fetch_bruker2d_public", REPO/"tests/fixtures/fetch_bruker2d_public.py")
validator = module_at("validate_public_bruker2d", REPO/"tools/validate_public_bruker2d.py")
MANIFEST = json.loads(validator.MANIFEST.read_text())


def test_public_2d_manifest_is_bounded_distinct_and_provenanced():
    items = fetcher.validate_manifest(MANIFEST)
    assert len(items) == 25
    assert sum(i["bytes"] for i in items) == 36803045
    assert {c["FnMODE"] for c in MANIFEST["cases"]} == {5, 6}
    assert MANIFEST["access_policy"].startswith("https://bmrb.io/")
    assert "No explicit redistribution license" in MANIFEST["access_terms"]
    assert MANIFEST["protocol"]["declared_before_raw_comparison"]


@pytest.mark.parametrize("path", ["../escape", "/escape", "C:/escape", "a\\escape"])
def test_manifest_rejects_unsafe_paths(path):
    item = dict(MANIFEST["files"][0], path=path)
    with pytest.raises(ValueError, match="path"):
        fetcher.validate_manifest({"files":[item]})


def test_2d_fetch_is_bounded_atomic_preserving_and_offline_reusable(tmp_path, monkeypatch):
    payload = b"raw two dimensional data"
    item = {"path":"HSQC/ser", "url":"https://bmrb.io/fixture", "bytes":len(payload),
            "sha256":hashlib.sha256(payload).hexdigest()}
    response = io.BytesIO(payload)
    response.url = item["url"]
    monkeypatch.setattr(fetcher, "urlopen", lambda *a, **k: response)
    fetcher.fetch_file(tmp_path, item)
    monkeypatch.setattr(fetcher, "urlopen", lambda *a, **k: pytest.fail("Unexpected download"))
    fetcher.fetch_file(tmp_path, item)
    target = tmp_path/item["path"]
    target.write_bytes(b"preserve this user file")
    with pytest.raises(RuntimeError, match="mismatch"):
        fetcher.fetch_file(tmp_path, item)
    assert target.read_bytes() == b"preserve this user file"
    assert not list(tmp_path.rglob(".fetch-*"))


def test_2d_fetch_bad_hash_or_oversize_never_publishes(tmp_path, monkeypatch):
    payload = b"extra bytes"
    item = {"path":"TOCSY/ser", "url":"https://bmrb.io/fixture", "bytes":4, "sha256":"0"*64}
    response = io.BytesIO(payload)
    response.url = item["url"]
    monkeypatch.setattr(fetcher, "urlopen", lambda *a, **k: response)
    with pytest.raises(RuntimeError, match="checksum"):
        fetcher.fetch_file(tmp_path, item)
    assert not (tmp_path/item["path"]).exists()
    assert not list(tmp_path.rglob(".fetch-*"))


def test_public_report_fingerprints_include_actual_raw_2d_decoder():
    fingerprints = validator.source_fingerprints()
    identity = "src/nmrnext/io/bruker2d.py"
    assert fingerprints[identity] == hashlib.sha256((REPO/identity).read_bytes()).hexdigest()


@pytest.mark.parametrize("original_size, retained_size, expected", [(512,443,.018623389), (1024,955,.013642607)])
def test_original_grid_endpoint_policy_distinguishes_trim_and_first_point(original_size, retained_size, expected):
    original = SimpleNamespace(axis=lambda _:SimpleNamespace(size=original_size,step=.001))
    retained = SimpleNamespace(axis=lambda _:SimpleNamespace(size=retained_size,step=.001))
    recipe = [{"op":"window", "axis":"a1", "lb_hz":math.log(100)/(math.pi*(original_size-1)*.001), "first_point_scale":.5}]
    policy = validator.window_endpoint_policy(original,retained,recipe)["a1"]
    assert policy["original_endpoint_exponential_factor"] == pytest.approx(.01)
    assert policy["retained_endpoint_exponential_factor"] == pytest.approx(expected,abs=5e-10)
    assert policy["retained_endpoint_to_weighted_first_point_ratio"] == pytest.approx(2*expected,abs=1e-9)


def real_root():
    root = os.environ.get("SPINFLOW_BRUKER2D_ROOT")
    if not root:
        pytest.skip("Set SPINFLOW_BRUKER2D_ROOT after explicit public-data fetch")
    path = Path(root)
    validator.verify_sources(path, MANIFEST)
    return path


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda c:c["id"])
def test_public_2d_independent_reference_signed_physical_volumes(case):
    root = real_root()
    ref, report = validator.validate_reference(root, case)
    assert report["reference_integration_pass"]
    assert all(v["integral"] > 0 for v in report["reference_integration"])
    arrays, _, _ = validator.reference_arrays(root, case)
    # Differential corroboration of our direct integer decoder, independent of
    # the application. This is not the primary physical-volume oracle.
    import nmrglue as ng
    _, decoded = ng.bruker.read_pdata(str(root/case["path"]/"pdata/1"), read_acqus=False)
    np.testing.assert_array_equal(arrays[0], decoded)
    assert ref.data.shape == (1,1024,1024)


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda c:c["id"])
def test_public_2d_raw_auto_phase_and_fixed_roi_reference_ratios(case):
    report = validator.validate_case(real_root(), case)
    assert report["pass"], report
