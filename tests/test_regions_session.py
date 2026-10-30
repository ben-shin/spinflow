"""Full-grid region identities and portable session failure checks."""
import json
import hashlib
import os
from unittest.mock import patch

import numpy as np
import pytest

from nmrnext.analysis import autophase, dataset_digest, integrate_regions, pick_peaks
from nmrnext.errors import NMRNextError
from nmrnext.model import Axis, Dataset
from nmrnext.operations import run_operations
from nmrnext.regions import (integrate_regions_in_region, pick_peaks_in_region,
                             select_analysis_region)
from nmrnext.session import load_session, save_session


def _big_processed():
    values = np.zeros((1, 2048, 4096), dtype=np.float32)
    values[0, 1007, 1998] = 9
    axes = [Axis("n", 2048, domain="frequency", unit="Hz", start=1000, step=-.5,
                 observation_mhz=50, reference_ppm=120, reference_hz=0),
            Axis("h", 4096, domain="frequency", unit="Hz", start=-2000, step=1.25,
                 observation_mhz=500, reference_ppm=4.7, reference_hz=0)]
    return Dataset(values, axes)


def test_full_size_parent_region_peaks_integrals_and_calibration():
    parent = _big_processed()
    with pytest.raises(NMRNextError, match="crop first"):
        pick_peaks(parent, threshold=1)
    bounds = {"n": [1000, 1015], "h": [1980, 2010]}
    region, mapping = select_analysis_region(parent, bounds)
    assert region.data.shape == (1, 16, 31)
    assert len(region._data) == 16*31*4
    assert mapping["parent_start"] == {"n": 1000, "h": 1980}
    assert mapping["parent_stop"] == {"n": 1016, "h": 2011}
    report = pick_peaks_in_region(parent, bounds, threshold=1, baseline="none")
    peak = report["peaks"][0]
    assert peak["points"] == {"n": 7, "h": 18}
    assert peak["parent_points"] == {"n": 1007, "h": 1998}
    assert peak["parent_coordinates"]["n"]["Hz"] == pytest.approx(496.5)
    assert peak["parent_coordinates"]["h"]["ppm"] == pytest.approx(5.695)
    assert report["parent_region"]["parent_dataset_digest"] == dataset_digest(parent)
    integral = integrate_regions_in_region(parent, bounds, [{"id": "box", "bounds": {
        "n": [6, 8], "h": [17, 19]}, "unit": "points"}])
    item = integral["regions"][0]
    assert item["parent_point_bounds"] == {"n": [1006, 1008], "h": [1997, 1999]}
    assert item["integral"] == pytest.approx(9*.5*1.25)
    assert item["integral_unit"] == "intensity*Hz^2"
    assert parent.data[0, 1007, 1998] == 9


def test_region_rejects_outside_uncalibrated_and_oversize():
    parent = _big_processed()
    with pytest.raises(NMRNextError) as caught:
        select_analysis_region(parent, {"n": [0, 2047], "h": [0, 4095]})
    assert caught.value.code == "ANALYSIS_SIZE_LIMIT"
    with pytest.raises(NMRNextError) as caught:
        select_analysis_region(parent, {"n": [-1, 10], "h": [0, 10]})
    assert caught.value.code == "INVALID_REGION"
    uncalibrated = Dataset(np.zeros((1, 8)), [Axis("h", 8, domain="frequency", unit="Hz")])
    with pytest.raises(NMRNextError) as caught:
        select_analysis_region(uncalibrated, {"h": [0, 2]}, unit="ppm")
    assert caught.value.code == "UNAVAILABLE_COORDINATES"


@pytest.mark.parametrize("unit,bounds,expected", [
    ("Hz", {"n": [499.25, 497.25], "h": [495.9, 499.0]},
     {"n": (1002, 1006), "h": (1997, 2000)}),
    ("ppm", {"n": [129.981, 129.949], "h": [5.691, 5.699]},
     {"n": (1002, 1006), "h": (1997, 2000)}),
    ("points", {"n": [1002, 1005], "h": [1997, 1999]},
     {"n": (1002, 1006), "h": (1997, 2000)}),
])
def test_fractional_physical_bounds_select_inside_centers_on_descending_axis(unit, bounds, expected):
    parent = _big_processed()
    region, mapping = select_analysis_region(parent, bounds, unit=unit)
    assert mapping["parent_start"] == {key: pair[0] for key, pair in expected.items()}
    assert mapping["parent_stop"] == {key: pair[1] for key, pair in expected.items()}
    assert region.data.shape == (1, 4, 3)
    assert region.axes[0].start == pytest.approx(parent.axes[0].start+1002*parent.axes[0].step)
    assert region.axes[1].start == pytest.approx(parent.axes[1].start+1997*parent.axes[1].step)


def _time_source():
    a = Axis("h", 64, domain="time", unit="s", start=0, step=.001,
             observation_mhz=500, reference_ppm=4.7)
    values = np.zeros((2, 64), dtype=np.float64)
    values[0, 2] = 1
    return Dataset(values, [a], ["h"])


def test_session_replay_exact_and_integrity_failures(tmp_path):
    source = _time_source()
    operations = [{"op": "fft", "axis": "h"},
                  {"op": "phase", "axis": "h", "p0": 0, "p1": 15, "pivot": 20}]
    current = run_operations(source, operations, backend="numpy")
    bounds = {"h": [12, 50]}
    report = pick_peaks_in_region(current, bounds, threshold=0, baseline="none")
    saved = save_session(source, operations, tmp_path/"session", backend="numpy",
                         regions=[{"bounds": bounds, "unit": "points"}],
                         reports={"peaks": report}, result=current, source_name="Example acquisition")
    loaded = load_session(saved)
    assert loaded["source_name"] == "Example acquisition"
    assert loaded["operations"] == operations
    assert loaded["reports"] == {"peaks": report}
    np.testing.assert_array_equal(loaded["current"].data, current.data)
    assert loaded["current"].to_metadata() == current.to_metadata()
    with pytest.raises(NMRNextError) as caught:
        save_session(source, operations, tmp_path/"session", backend="numpy")
    assert caught.value.code == "OUTPUT_EXISTS"
    manifest_path = saved
    manifest = json.loads(manifest_path.read_text())
    manifest["result_dataset_digest"] = "0"*64
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(NMRNextError) as caught:
        load_session(saved)
    assert caught.value.code == "SESSION_RESULT_MISMATCH"


def test_session_rejects_modified_snapshot_workflow_and_unknown_manifest(tmp_path):
    source = _time_source()
    saved = save_session(source, [], tmp_path/"session", backend="numpy")
    workflow = saved.parent/"workflow.json"
    workflow.write_text(workflow.read_text().replace('"operations": []', '"operations": [{"op":"reverse","axis":"h"}]'))
    with pytest.raises(NMRNextError) as caught:
        load_session(saved)
    assert caught.value.code == "SESSION_INTEGRITY_FAILURE"
    manifest = json.loads(saved.read_text())
    manifest["unexpected"] = True
    saved.write_text(json.dumps(manifest))
    with pytest.raises(NMRNextError) as caught:
        load_session(saved)
    assert caught.value.code == "UNSUPPORTED_SESSION_SCHEMA"


def test_session_rejects_stale_region_mapping_and_unknown_report_key(tmp_path):
    source = _time_source()
    current = run_operations(source, [{"op": "fft", "axis": "h"}], backend="numpy")
    report = pick_peaks_in_region(current, {"h": [8, 48]}, threshold=0, baseline="none")
    saved = save_session(source, [{"op": "fft", "axis": "h"}], tmp_path/"session",
                         backend="numpy", reports={"peaks": report}, result=current)
    manifest = json.loads(saved.read_text())
    manifest["reports"]["peaks"]["parent_region"]["parent_start"]["h"] += 1
    manifest["reports_sha256"] = hashlib.sha256(json.dumps(manifest["reports"], sort_keys=True,
        separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    saved.write_text(json.dumps(manifest))
    with pytest.raises(NMRNextError) as caught:
        load_session(saved)
    assert caught.value.code == "STALE_ANALYSIS"
    manifest["reports"]["peaks"] = report
    manifest["reports"]["peaks"]["peaks"][0]["intensity"] += 1
    manifest["reports_sha256"] = hashlib.sha256(json.dumps(manifest["reports"], sort_keys=True,
        separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    saved.write_text(json.dumps(manifest))
    with pytest.raises(NMRNextError) as caught:
        load_session(saved)
    assert caught.value.code == "INVALID_ANALYSIS_REPORT"
    manifest["reports"] = {"unrecognized": report}
    manifest["reports_sha256"] = hashlib.sha256(json.dumps(manifest["reports"], sort_keys=True,
        separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    saved.write_text(json.dumps(manifest))
    with pytest.raises(NMRNextError) as caught:
        load_session(saved)
    assert caught.value.code == "INVALID_ANALYSIS_REPORT"


def test_session_missing_source_and_concurrent_collision_preserve_foreign_bytes(tmp_path):
    source = _time_source()
    destination = tmp_path/"session"
    from nmrnext import session
    original_publish = session._publish_new
    def collide(staged, target):
        if target.name == "workflow.json":
            target.write_bytes(b"foreign workflow")
        return original_publish(staged, target)
    with patch.object(session, "_publish_new", side_effect=collide):
        with pytest.raises(NMRNextError) as caught:
            save_session(source, [], destination, backend="numpy")
    assert caught.value.code == "OUTPUT_EXISTS"
    assert (destination/"workflow.json").read_bytes() == b"foreign workflow"
    assert sorted(p.name for p in destination.iterdir()) == ["workflow.json"]
    saved = save_session(source, [], tmp_path/"complete", backend="numpy")
    (saved.parent/"source.nmrh5").unlink()
    with pytest.raises(NMRNextError) as caught:
        load_session(saved)
    assert caught.value.code == "INVALID_SESSION"


@pytest.mark.parametrize("mutation", ["roi_integral", "whole_intensity", "missing_peaks", "flags_type"])
def test_session_cached_report_edits_fail_before_return(tmp_path, mutation):
    source = Dataset(np.asarray([[0., 0., 1., 4., 8., 3., 1., 0., 0., 0., 0., 0.]]),
                     [Axis("h", 12, domain="frequency", unit="Hz", start=100, step=-2)])
    roi = integrate_regions_in_region(source, {"h": [1, 9]},
        [{"id": "one", "bounds": {"h": [1., 6.]}, "unit": "points"}])
    whole = pick_peaks(source, threshold=2, baseline="none")
    saved = save_session(source, [], tmp_path/"session", backend="numpy",
                         reports={"integrate": roi, "peaks": whole})
    manifest = json.loads(saved.read_text())
    if mutation == "roi_integral":
        manifest["reports"]["integrate"]["regions"][0]["integral"] = 1e99
    elif mutation == "whole_intensity":
        manifest["reports"]["peaks"]["peaks"][0]["intensity"] = 1e99
    elif mutation == "missing_peaks":
        del manifest["reports"]["peaks"]["peaks"]
    else:
        manifest["reports"]["peaks"]["flags"] = [7]
    saved.write_text(json.dumps(manifest))
    with pytest.raises(NMRNextError):
        load_session(saved)
    # A regenerated checksum does not make structurally malformed content valid.
    if mutation in ("missing_peaks", "flags_type"):
        manifest["reports_sha256"] = hashlib.sha256(json.dumps(
            manifest["reports"], sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        saved.write_text(json.dumps(manifest))
        with pytest.raises(NMRNextError):
            load_session(saved)


@pytest.mark.parametrize("member", ["source.nmrh5", "workflow.json", "session.json"])
def test_session_staged_foreign_replacement_survives_failure(tmp_path, member):
    source = _time_source()
    marker = b"foreign replacement must survive cleanup"
    touched = []
    def replace_then_fail(staged, target):
        victim = staged.parent/member
        other = tmp_path/"foreign"
        other.write_bytes(marker)
        os.replace(other, victim)
        touched.append(victim)
        raise OSError("injected publication failure")
    from nmrnext import session
    with patch.object(session, "_publish_new", side_effect=replace_then_fail):
        with pytest.raises(OSError, match="injected publication failure"):
            save_session(source, [], tmp_path/"session", backend="numpy")
    assert touched[0].read_bytes() == marker


def test_session_preserves_autophase_and_masked_report_variants(tmp_path):
    n = 1024
    x = np.arange(n, dtype=float)
    signal = 1/(1+1j*(x-300)/5) + .8/(1+1j*(x-700)/7)
    measured = signal*np.exp(.35j)
    source = Dataset(np.stack((measured.real, measured.imag)),
                     [Axis("h", n, domain="frequency", unit="Hz")], ["h"])
    inclusion = np.ones(n, dtype=bool)
    corrected, phase = autophase(source, inclusion_mask=inclusion, backend="numpy")
    phase["mask_selections"] = {"phase": {"dataset_digest": dataset_digest(source),
                                         "regions": [{"h": [0, n-1]}]}}
    saved = save_session(source, [phase["operation"]], tmp_path/"phase", backend="numpy",
                         reports={"autophase": phase}, result=corrected)
    reopened = load_session(saved)
    assert reopened["reports"]["autophase"] == phase
    assert reopened["current"].to_metadata() == corrected.to_metadata()
    np.testing.assert_array_equal(reopened["current"].data, corrected.data)

    simple = Dataset(np.asarray([[2., 2., 2., 2., 3., 6., 3., 2., 2., 2., 2.]]),
                     [Axis("h", 11, domain="frequency", unit="Hz")])
    mask = np.ones(11, dtype=bool); mask[4:7] = False
    integral = integrate_regions(simple, [{"id": "one", "unit": "points", "bounds": {"h": [4, 6]}}],
                                 baseline="reference_linear", baseline_mask=mask)
    integral["mask_selections"] = {"baseline": {"dataset_digest": dataset_digest(simple),
                                                "regions": [{"h": [0, 3]}, {"h": [7, 10]}]}}
    peaks = pick_peaks(simple, threshold=1, baseline="none", noise_mask=mask)
    peaks["mask_selections"] = {"noise": {"dataset_digest": dataset_digest(simple),
                                          "regions": [{"h": [0, 3]}, {"h": [7, 10]}]}}
    saved = save_session(simple, [], tmp_path/"masked", backend="numpy",
                         reports={"integrate": integral, "peaks": peaks})
    reopened = load_session(saved)
    assert reopened["reports"] == {"integrate": integral, "peaks": peaks}


def test_session_recomputed_checksum_does_not_bypass_report_structure(tmp_path):
    source = Dataset(np.asarray([[0., 0., 5., 0., 0., 0., 0., 0.]]),
                     [Axis("h", 8, domain="frequency", unit="Hz")])
    report = pick_peaks(source, threshold=1, baseline="none")
    saved = save_session(source, [], tmp_path/"session", backend="numpy", reports={"peaks": report})
    gold = json.loads(saved.read_text())
    for mutation in ("missing_id", "unknown_field", "wrong_coordinates", "bad_mask_bounds"):
        item = json.loads(json.dumps(gold))
        peak = item["reports"]["peaks"]["peaks"][0]
        if mutation == "missing_id":
            del peak["id"]
        elif mutation == "unknown_field":
            peak["unsupported"] = 1
        elif mutation == "wrong_coordinates":
            peak["coordinates"]["h"]["Hz"] += 1
        else:
            item["reports"]["peaks"]["mask_selections"] = {"noise": {
                "dataset_digest": report["dataset_digest"], "regions": [{"h": [0, 9]}]}}
        item["reports_sha256"] = hashlib.sha256(json.dumps(item["reports"], sort_keys=True,
            separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        saved.write_text(json.dumps(item))
        with pytest.raises(NMRNextError) as caught:
            load_session(saved)
        assert caught.value.code == "INVALID_ANALYSIS_REPORT", mutation


def test_session_staging_directory_replacement_is_preserved(tmp_path):
    source = _time_source()
    marker = b"foreign directory marker"
    paths = []
    def replace_directory_then_fail(staged, target):
        staging = staged.parent
        moved = tmp_path/"moved-original-staging"
        staging.rename(moved)
        staging.mkdir()
        (staging/"foreign.txt").write_bytes(marker)
        paths.append(staging)
        raise OSError("directory changed")
    from nmrnext import session
    with patch.object(session, "_publish_new", side_effect=replace_directory_then_fail):
        with pytest.raises(OSError, match="directory changed") as caught:
            save_session(source, [], tmp_path/"session", backend="numpy")
    assert (paths[0]/"foreign.txt").read_bytes() == marker
    assert any("ownership changed" in note for note in caught.value.__notes__)


def test_session_source_write_failure_cleans_owned_empty_staging(tmp_path):
    from nmrnext import session
    with patch.object(session, "write_dataset", side_effect=OSError("source write failed")):
        with pytest.raises(OSError, match="source write failed"):
            save_session(_time_source(), [], tmp_path/"session", backend="numpy")
    assert not (tmp_path/"session").exists()
    assert not list(tmp_path.glob(".spinflow-session-*"))


def test_session_rejects_staged_source_content_mutation_without_deleting_it(tmp_path):
    from nmrnext import session
    original = session._write_owned
    altered = []
    def mutate_after_workflow(path, payload, identities):
        original(path, payload, identities)
        if path.name == "workflow.json":
            source_path = path.parent/"source.nmrh5"
            with source_path.open("r+b") as stream:
                stream.seek(0)
                stream.write(b"X")
            altered.append(source_path)
    with patch.object(session, "_write_owned", side_effect=mutate_after_workflow):
        with pytest.raises(NMRNextError) as caught:
            save_session(_time_source(), [], tmp_path/"session", backend="numpy")
    assert caught.value.code == "SESSION_STAGING_CHANGED"
    assert altered[0].read_bytes().startswith(b"X")
    assert not (tmp_path/"session").exists()
    assert any("Preserved unowned staged source.nmrh5" in note for note in caught.value.__notes__)


def test_session_staging_directory_alias_is_not_owned(tmp_path):
    from nmrnext import session
    real = tmp_path/"real"
    real.mkdir()
    alias = tmp_path/"alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this host")
    with pytest.raises(NMRNextError) as caught:
        session._require_staging(alias, session._directory_identity(real))
    assert caught.value.code == "SESSION_STAGING_CHANGED"


@pytest.fixture(scope="module", params=[1, 2], ids=["1d", "2d"])
def phase_session_case(request):
    if request.param == 2:
        from test_autophase_2d import manufactured
        source, _ = manufactured(reverse=True, bit_reverse=True)
    else:
        x = np.arange(1024, dtype=float)
        signal = (1/(1+1j*(x-300)/5) + .8/(1+1j*(x-700)/7))*np.exp(.35j)
        source = Dataset(np.stack((signal.real, signal.imag)),
                         [Axis("h", 1024, domain="frequency", unit="Hz")], ["h"])
    corrected, report = autophase(source, backend="numpy")
    operations = [report["operation"]] if request.param == 1 else report["operations"]
    return source, corrected, operations, report


def _assert_report_rejected_both_boundaries(tmp_path, source, corrected, operations, key, report, mutations):
    """Load attacks regenerate the checksum: shape validation must stand alone."""
    saved = save_session(source, operations, tmp_path/"valid", backend="numpy",
                         reports={key: report}, result=corrected)
    loaded = load_session(saved)
    assert loaded["reports"] == {key: report}
    assert loaded["current"].data.tobytes() == corrected.data.tobytes()
    assert loaded["current"].to_metadata() == corrected.to_metadata()
    gold = json.loads(saved.read_text())
    for index, mutate in enumerate(mutations):
        bad = json.loads(json.dumps(report))
        mutate(bad)
        destination = tmp_path/f"rejected-{index}"
        with pytest.raises(NMRNextError):
            save_session(source, operations, destination, backend="numpy",
                         reports={key: bad}, result=corrected)
        assert not destination.exists()
        manifest = json.loads(json.dumps(gold))
        manifest["reports"][key] = bad
        # Nonfinite JSON is rejected by JSON parsing rather than schema validation.
        manifest["reports_sha256"] = hashlib.sha256(json.dumps(manifest["reports"],
            sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        saved.write_text(json.dumps(manifest))
        with pytest.raises(NMRNextError):
            load_session(saved)


def test_session_phase_objective_and_diagnostic_boundaries(tmp_path, phase_session_case):
    source, corrected, operations, report = phase_session_case
    mutations = [
        lambda r: r.__setitem__("objective", {}),
        lambda r: r["objective"].__setitem__("extra", 1),
        lambda r: r["objective"].__setitem__("name", "invented"),
        lambda r: r["objective"].__setitem__("per_line_relative_rms", []),
        lambda r: r["objective"].__setitem__("per_line_relative_rms", "invalid"),
        lambda r: r["objective"]["per_line_relative_rms"].append(0.),
        lambda r: r["objective"].__setitem__("acceptance_limit", .9),
        lambda r: r.__setitem__("convergence", {}),
        lambda r: r["convergence"].__setitem__("success", 1),
        lambda r: r["convergence"].__setitem__("evaluations", True),
        lambda r: r["selection"].__setitem__("included_points", True),
        lambda r: r["candidate_screening"].__setitem__("scale", "invalid"),
        lambda r: r["fit_units"].__setitem__("intensity_scale", True),
        lambda r: r["lines"][0].__setitem__("amplitude", "invalid"),
        lambda r: r["parameters"].__setitem__("first_order", "invalid"),
        lambda r: r["parameters"].__setitem__("pivot", True),
        lambda r: r.__setitem__("rejected_candidates", [{"points": [], "reason": 7}]),
    ]
    scalars = ["normalized_rms", "acceptance_limit"] + (["initial_rms"] if len(source.axes) == 1 else [])
    for field in scalars:
        mutations.append(lambda r, f=field: r["objective"].pop(f))
        for value in ("invalid", True, -1., float("nan"), float("inf"), 10**1000):
            mutations.append(lambda r, f=field, v=value: r["objective"].__setitem__(f, v))
    for value in ("invalid", True, -1., float("inf")):
        mutations.append(lambda r, v=value: r["objective"]["per_line_relative_rms"].__setitem__(0, v))
    if len(source.axes) == 1:
        mutations.extend([
            lambda r: r["noise"].__setitem__("sigma", "invalid"),
            lambda r: r.__setitem__("line_span_fraction", True),
            lambda r: r["lines"][0].__setitem__("width", False),
            lambda r: r.__setitem__("phase_gauge", {}),
        ])
    else:
        mutations.extend([
            lambda r: r.__setitem__("phase_gauge", {}),
            lambda r: r["phase_gauge"].__setitem__("canonical_branch", 7),
            lambda r: r["phase_gauge"].__setitem__("ambiguity", "invented"),
            lambda r: r["phase_gauge"].__setitem__("extra", "invalid"),
            lambda r: r["axis_results"]["c"].__setitem__("operation", {}),
            lambda r: r["axis_results"]["c"].__setitem__("maximum_gap_fraction", True),
            lambda r: r["lines"][0]["widths"].__setitem__("c", "invalid"),
            lambda r: r["resource_bounds"].__setitem__("local_max_nfev", False),
        ])
    _assert_report_rejected_both_boundaries(tmp_path, source, corrected, operations, "autophase", report, mutations)


def test_session_phase_correction_boundaries(tmp_path, phase_session_case):
    source, corrected, operations, report = phase_session_case
    def correction(r):
        return r["operation"] if "operation" in r else r["operations"][0]
    mutations = [
        lambda r: correction(r).pop("axis"),
        lambda r: correction(r).__setitem__("unsupported", 1),
        lambda r: correction(r).__setitem__("axis", "unknown"),
        lambda r: correction(r).__setitem__("axis", 7),
        lambda r: correction(r).__setitem__("op", "reverse"),
        lambda r: correction(r).__setitem__("p0", correction(r)["p0"]+1),
    ]
    for field in ("p0", "p1", "pivot"):
        mutations.append(lambda r, f=field: correction(r).pop(f))
        for value in (True, "invalid", float("inf")):
            mutations.append(lambda r, f=field, v=value: correction(r).__setitem__(f, v))
    if len(source.axes) == 2:
        mutations.extend([
            lambda r: r.__setitem__("operations", [{"op": "phase"}, {"op": "phase"}]),
            lambda r: r["operations"].reverse(),
            lambda r: r["operations"].pop(),
        ])
        # Keep the duplicated axis diagnostic consistent to reach provenance check.
        def wrong_provenance(r):
            r["operations"][0]["p0"] += 1
            r["axis_results"]["c"]["operation"]["p0"] += 1
        mutations.append(wrong_provenance)
    _assert_report_rejected_both_boundaries(tmp_path, source, corrected, operations, "autophase", report, mutations)


@pytest.mark.parametrize("reverse,bit_reverse,masked", [
    (False, False, False), (True, False, True), (False, True, False), (True, True, True)])
def test_session_two_axis_phase_all_quadratures_exact(tmp_path, reverse, bit_reverse, masked):
    from test_autophase_2d import manufactured
    source, _ = manufactured(reverse=reverse, bit_reverse=bit_reverse)
    mask = np.ones(source.data.shape[1:], dtype=bool) if masked else None
    corrected, report = autophase(source, inclusion_mask=mask, backend="numpy")
    if masked:
        report["mask_selections"] = {"phase": {"dataset_digest": dataset_digest(source),
            "regions": [{a.id: [0, a.size-1] for a in source.axes}]}}
    saved = save_session(source, report["operations"], tmp_path/"session", backend="numpy",
                         reports={"autophase": report}, result=corrected)
    loaded = load_session(saved)
    assert loaded["reports"] == {"autophase": report}
    assert loaded["current"].data.tobytes() == corrected.data.tobytes()
    assert loaded["current"].to_metadata() == corrected.to_metadata()


@pytest.mark.parametrize("dimension", [1, 2])
def test_session_signed_baseline_variants_and_numeric_boundaries(tmp_path, dimension):
    shape = (12,) if dimension == 1 else (12, 13)
    values = np.full(shape, -2.)
    slices = (slice(4, 7),)*dimension
    values[slices] -= 3
    axes = [Axis(axis, size, domain="frequency", unit="Hz", start=100., step=-2.)
            for axis, size in zip(("h", "c"), shape)]
    source = Dataset(values[None], axes)
    mask = np.ones(shape, dtype=bool); mask[slices] = False
    regions = [{"id": "one", "unit": "points", "bounds": {a.id: [4, 6] for a in axes}}]
    for method in ("none", "constant", "edge_linear", "reference_linear"):
        kwargs = {"baseline": method}
        if method == "constant":
            kwargs["baseline_value"] = -2.
        if method == "reference_linear":
            kwargs["baseline_mask"] = mask
        report = integrate_regions(source, regions, **kwargs)
        folder = tmp_path/method; folder.mkdir()
        saved = save_session(source, [], folder/"roundtrip", backend="numpy", reports={"integrate": report})
        loaded = load_session(saved)
        assert loaded["reports"] == {"integrate": report}
        assert loaded["current"].data.tobytes() == source.data.tobytes()
        assert loaded["current"].to_metadata() == source.to_metadata()
        assert report["regions"][0]["integral"] < 0 or method == "edge_linear"
        field = "reference_rms" if method == "reference_linear" else "value"
        mutations = [lambda r, f=field, v=value: r["regions"][0]["baseline"].__setitem__(f, v)
                     for value in ("invalid", True, float("inf"))]
        if method in ("edge_linear", "reference_linear"):
            mutations.extend([
                lambda r: r["regions"][0]["baseline"].__setitem__("coefficients", []),
                lambda r: r["regions"][0]["baseline"]["coefficients"].__setitem__(0, True)])
        _assert_report_rejected_both_boundaries(folder, source, source, [], "integrate", report, mutations)
    for method in ("none", "constant", "median"):
        report = pick_peaks(source, sign="both", threshold=1., baseline=method,
                            baseline_value=-2. if method == "constant" else None, noise_mask=mask)
        folder = tmp_path/("peaks-"+method); folder.mkdir()
        mutations = [lambda r, v=value: r["noise"].__setitem__("sigma", v)
                     for value in ("invalid", True, -1., float("inf"))]
        mutations.extend([
            lambda r: r["noise"].__setitem__("sample_count", True),
            lambda r: r["baseline"].__setitem__("value", "invalid")])
        _assert_report_rejected_both_boundaries(folder, source, source, [], "peaks", report, mutations)
