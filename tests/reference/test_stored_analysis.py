"""Analytical/falsification controls; no acquired sample is opened."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location(
    "stored_analysis", Path(__file__).resolve().parents[2]/"tools/validate_stored_analysis.py")
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)


def headers(shape):
    return [{"SI": n, "SW_p": n*2., "SF": 100.+i, "OFFSET": 10.}
            for i, n in enumerate(shape)]


def reported(expected, params):
    return [{"points": {f"a{i}": v for i, v in enumerate(p["points"])},
             "sign": p["sign"], "intensity": p["intensity"], "flags": list(p["flags"]),
             "coordinates": {f"a{i}": {"Hz": h["OFFSET"]*h["SF"]-v*h["SW_p"]/h["SI"],
                                              "ppm": h["OFFSET"]-v*h["SW_p"]/(h["SF"]*h["SI"])}
                             for i, (v, h) in enumerate(zip(p["points"], params))}}
            for p in expected]


def test_extrema_plateau_centroid_ties_edges_signs_and_threshold():
    data = np.array([6., 0., 4., 4., 0., -5., 0., 2., 0.])
    expected = q.extrema(data, 2.)
    assert [p["points"] for p in expected] == [[0], [5], [2], [7]]
    assert [p["sign"] for p in expected] == ["positive", "negative", "positive", "positive"]
    assert expected[0]["flags"] == ["spectrum_edge"]
    assert expected[2]["flags"] == ["plateau"]
    assert q.extrema(np.ones((3, 3)), 0.) == []
    assert q.extrema(np.zeros((3, 3)), 0.) == []


def test_diagonally_connected_plateau_and_adjacent_unlike_heights():
    data = np.zeros((6, 6))
    data[1, 1] = data[2, 2] = 4.
    data[4, 4] = -3.
    expected = q.extrema(data, 3.)
    assert [p["points"] for p in expected] == [[1, 1], [4, 4]]
    assert expected[0]["flags"] == ["plateau"]
    data[2, 2] = 5.
    assert q.extrema(data, 3.)[0]["points"] == [2, 2]


@pytest.mark.parametrize("shape", [(17,), (7, 8)])
def test_independent_oracle_agrees_with_product_on_analytical_data(shape):
    from nmrnext.analysis import pick_peaks
    rng = np.random.default_rng(438)
    values = rng.normal(size=shape)
    params = headers(shape)
    data = q.make_dataset(values, params)
    actual = pick_peaks(data, sign="both", threshold=.7, baseline="none",
                        min_separation=1., max_peaks=10000)
    expected = q.extrema(values, .7)
    assert q.compare_peaks(expected, actual["peaks"], params)["pass"]


@pytest.mark.parametrize("fault", ["drop", "extra", "duplicate", "sign", "height", "ppm", "Hz", "flags"])
def test_detection_comparator_rejects_corruption(fault):
    values = np.array([0., 5., 0., -4., 0.])
    expected = q.extrema(values, 1.)
    params = headers(values.shape)
    observed = reported(expected, params)
    assert q.compare_peaks(expected, observed, params)["pass"]
    if fault == "drop":
        observed.pop()
    elif fault == "extra":
        extra = deepcopy(observed[0])
        extra["points"]["a0"] = 2
        observed.append(extra)
    elif fault == "duplicate":
        observed.append(deepcopy(observed[0]))
    elif fault == "sign":
        observed[0]["sign"] = "negative"
    elif fault == "height":
        observed[0]["intensity"] *= 1.01
    elif fault in ("ppm", "Hz"):
        observed[0]["coordinates"]["a0"][fault] += .01
    else:
        observed[0]["flags"].append("plateau")
    assert not q.compare_peaks(expected, observed, params)["pass"]


def test_maximum_cardinality_target_matching_no_duplicate_or_omitted_rows():
    targets = [{"row": 1, "ppm": 1.}, {"row": 2, "ppm": 1.0025},
               {"row": 3, "ppm": 9.}]
    peaks = [{"points": {"a0": i}, "sign": "positive", "coordinates": {"a0": {"ppm": v}}}
             for i, v in enumerate((1.001, .999))]
    result = q.match_targets(targets, peaks)
    assert [r["row"] for r in result["rows"]] == [1, 2, 3]
    assert [r["detection_index"] for r in result["rows"]] == [1, 0, None]
    assert result["unmatched_detection_indices"] == []
    boundary = q.match_targets([{"row": 1, "ppm": 0.}],
                              [{"points": {"a0": 1}, "sign": "positive", "coordinates": {"a0": {"ppm": .002}}}])
    assert boundary["rows"][0]["detection_index"] is None


def test_cell_volume_and_denominator_falsification():
    y, x = np.mgrid[:9, :9]
    values = 2.+y-3*x+2*y*x
    params = [{"SW_p": 18., "SI": 9}, {"SW_p": 27., "SI": 9}]
    expected = 18.5*4*5*6
    assert q.cell_volume(values, [[1, 5], [2, 7]], params) == expected
    assert q.cell_volume(-values, [[1, 5], [2, 7]], params) == -expected
    assert q.compare_volume(expected, expected, expected)["pass"]
    assert not q.compare_volume(expected*1.001, expected, expected)["pass"]
    assert not q.compare_volume(0., 0., 1.)["pass"]
    assert not q.compare_volume(1e-15, 1e-15, 1.)["pass"]


def test_mad_normal_scale():
    assert q.amplitude_sigma(np.array([0., 1., 2., 3., 4.])) == 1./.6744897501960817


def test_missing_source_distribution_metadata_is_explicit(monkeypatch):
    def absent(name):
        raise q.importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(q.importlib.metadata, "version", absent)
    assert q.distribution_version("spinflow") is None


def test_input_identity_path_and_output_refusals(tmp_path):
    path = tmp_path/"source"
    path.write_bytes(b"pinned")
    records = [{"path": "source", "bytes": 6, "sha256": q.digest(path)}]
    q.verify_inputs(tmp_path, records)
    path.write_bytes(b"edited")
    with pytest.raises(ValueError, match="identity"):
        q.verify_inputs(tmp_path, records)
    records[0]["path"] = "../source"
    with pytest.raises(ValueError, match="path"):
        q.verify_inputs(tmp_path, records)
    with pytest.raises(FileExistsError):
        q.write_new(path, {})
    with pytest.raises(ValueError, match="schema"):
        q.validate_manifest({"schema": 9})


def test_archive_decoder_retains_scaled_signed_int32_and_grid(tmp_path):
    folder = tmp_path/"1H/pdata/1"
    folder.mkdir(parents=True)
    (folder/"procs").write_text("\n".join(f"##${k}= {v}" for k, v in
        dict(BYTORDP=0, DTYPP=0, NC_proc=-6, SI=4, XDIM=2, SF=100,
             SW_p=400, OFFSET=10).items()))
    (folder/"1r").write_bytes(np.array([64, -128, 0, 192], dtype="<i4").tobytes())
    values, params = q.decode_archive(tmp_path, "1H", ["1r"])
    np.testing.assert_array_equal(values, [[1., -2., 0., 3.]])
    assert q.grid(params[0], "ppm").tolist() == [10., 9., 8., 7.]
    (folder/"1r").write_bytes(b"wrong")
    with pytest.raises(ValueError, match="length"):
        q.decode_archive(tmp_path, "1H", ["1r"])


@pytest.mark.parametrize("changed", ["manifest", "protocol", "tool", "schema"])
def test_execution_refuses_changed_frozen_inputs_before_decoder(tmp_path, monkeypatch, changed):
    source = Path(__file__).resolve().parents[2]/"benchmarks/cases/bmse000002-stored-analysis.json"
    manifest = json.loads(source.read_text())
    data = tmp_path/"data"
    for record in manifest["files"]:
        path = data/record["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"pinned")
        record.update(bytes=6, sha256=q.digest(path))
    mp, pp, lp = tmp_path/"manifest.json", tmp_path/"protocol.md", tmp_path/"lock.json"
    mp.write_text(json.dumps(manifest))
    pp.write_text("fixed scientific protocol")
    monkeypatch.setattr(q, "runtime", lambda: {"test": "no-acquired-execution"})
    monkeypatch.setattr(q, "check_runtime", lambda saved: None)
    q.prepare(data, mp, pp, lp)
    lock = json.loads(lp.read_text())
    if changed == "manifest":
        mp.write_text(mp.read_text()+"\n")
    elif changed == "protocol":
        pp.write_text(pp.read_text()+"\n")
    elif changed == "tool":
        lock["tool_sha256"] = "0"*64
    else:
        lock["schema"] = 2
    lp.write_text(json.dumps(lock))
    def forbidden(*args):
        pytest.fail("Decoder called after frozen-input mismatch")
    monkeypatch.setattr(q, "decode_archive", forbidden)
    with pytest.raises(ValueError, match="changed|schema"):
        q.run(data, mp, pp, lp, tmp_path/"report.json", lock_sha256=q.digest(lp))


@pytest.fixture
def synthetic_run(tmp_path, monkeypatch):
    """Full synthetic execution seam, never opens the acquired corpus."""
    from nmrnext import analysis
    source = Path(__file__).resolve().parents[2]/"benchmarks/cases/bmse000002-stored-analysis.json"
    manifest = json.loads(source.read_text())
    data = tmp_path/"data"
    for record in manifest["files"]:
        path = data/record["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"pinned")
        record.update(bytes=6, sha256=q.digest(path))
    for region in manifest["regions"]:
        region["bounds_points"] = [[0, 8], [0, 8]]
        region["expected_integrals"] = [256., 512., 768., 1024.]
    mp, pp, lp = tmp_path/"manifest.json", tmp_path/"protocol.md", tmp_path/"lock.json"
    mp.write_text(json.dumps(manifest))
    pp.write_text("fixed synthetic scientific protocol")
    monkeypatch.setattr(q, "runtime", lambda: {"test": "no-acquired-execution"})
    monkeypatch.setattr(q, "check_runtime", lambda saved: None)
    q.prepare(data, mp, pp, lp)
    def decode(root, case, components):
        shape = (9,) if case == "1H" else (9, 9)
        return np.stack([np.full(shape, i+1.) for i in range(len(components))]), headers(shape)
    monkeypatch.setattr(q, "decode_archive", decode)
    def integral(dataset, regions, **kwargs):
        return {"kind": "integration", "schema_version": "1", "regions":
                [{"id": r["id"], "integral": float(dataset.data[0, 0, 0])*256.} for r in regions]}
    monkeypatch.setattr(analysis, "integrate_regions", integral)
    return dict(data=data, manifest=mp, protocol=pp, lock_path=lp,
                output=tmp_path/"report.json", lock_sha256=q.digest(lp)), analysis, integral


@pytest.mark.parametrize("fault", [None, "empty", "truncated", "extra", "duplicate", "reordered", "misidentified", "kind", "schema"])
def test_volume_report_requires_all_ordered_ids_and_all_16_gates(synthetic_run, monkeypatch, fault):
    args, analysis, integral = synthetic_run
    def changed(*a, **kw):
        report = integral(*a, **kw)
        if fault == "empty":
            report["regions"] = []
        elif fault == "truncated":
            report["regions"].pop()
        elif fault == "extra":
            extra = deepcopy(report["regions"][0])
            extra["id"] = "unrequested"
            report["regions"].append(extra)
        elif fault == "duplicate":
            report["regions"][-1] = deepcopy(report["regions"][0])
        elif fault == "reordered":
            report["regions"].reverse()
        elif fault == "misidentified":
            report["regions"][0]["id"] = "wrong"
        elif fault == "kind":
            report["kind"] = "other"
        elif fault == "schema":
            report["schema_version"] = "9"
        return report
    monkeypatch.setattr(analysis, "integrate_regions", changed)
    result = q.run(**args)
    report = json.loads(args["output"].read_text())
    volumes = [c for c in report["checks"] if c["id"].startswith("volume-") and c["id"].split("-")[2] in {"positive", "negative"}]
    assert len(volumes) == 16
    assert result["pass"] == (fault is None)
    assert len(report["volume_reports"]) == 4
    assert len(report["volume_reports"][0]["product_report"]["regions"]) == (0 if fault == "empty" else 3 if fault == "truncated" else 5 if fault == "extra" else 4)


@pytest.mark.parametrize("moment", ["before", "during", "status"])
def test_lock_identity_is_bound_to_approval_and_rechecked(synthetic_run, monkeypatch, moment):
    args, analysis, integral = synthetic_run
    if moment == "before":
        args["lock_path"].write_text(args["lock_path"].read_text()+"\n")
    elif moment == "status":
        lock = json.loads(args["lock_path"].read_text())
        lock["status"] = "UNAPPROVED_OTHER_STATUS"
        args["lock_path"].write_text(json.dumps(lock))
        args["lock_sha256"] = q.digest(args["lock_path"])
    else:
        def mutate(*a, **kw):
            args["lock_path"].write_text(args["lock_path"].read_text()+"\n")
            return integral(*a, **kw)
        monkeypatch.setattr(analysis, "integrate_regions", mutate)
    with pytest.raises(ValueError, match="lock|status"):
        q.run(**args)
    assert not args["output"].exists()
