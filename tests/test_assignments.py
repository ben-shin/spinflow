"""Assignment transfer uses fixed boxes and hand-derived signed measurements."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json

import numpy as np
import pytest

from nmrnext.analysis import dataset_digest
from nmrnext.errors import NMRNextError
from nmrnext.model import Axis, Dataset
from nmrnext.operations import quadrature_index_orientation


def spectrum(values=(0, 0, 2, 6, 2, 0, 0, -3, 0), *, calibrated=True):
    a = Axis("h", len(values), domain="frequency", unit="Hz", start=100, step=-2,
             nucleus="1H", observation_mhz=500 if calibrated else None,
             reference_ppm=4.7 if calibrated else None)
    return Dataset(np.asarray([values], dtype=np.float32), [a])


def template(d, *, unit="points", search=(1, 5), integration=(1, 5)):
    return {"schema_version": "1", "kind": "assignment_template",
            "reference": {"dataset_digest": dataset_digest(d),
                          "axes": [a.to_dict() for a in d.axes],
                          "complex_axes": list(d.complex_axes),
                          "quadrature_index_orientation": quadrature_index_orientation(d)},
            "analysis_channel": 0,
            "assignments": [{"assignment_id": "a-stable", "label": "my signal",
                             "sign": "positive", "unit": unit,
                             "search_bounds": {"h": list(search)},
                             "integration_bounds": {"h": list(integration)}, "threshold": 1.}]}


def test_import_origin_is_isolated_checkout():
    import nmrnext
    assert Path(nmrnext.__file__).resolve().parent == Path(__file__).resolve().parents[1] / "src/nmrnext"


def test_valid_fixture_has_hand_expected_existing_python_math():
    from nmrnext.analysis import integrate_regions, pick_peaks
    d = spectrum()
    report = pick_peaks(d, threshold=1., baseline="none")
    assert report["peaks"][0]["points"] == {"h": 3}
    result = integrate_regions(d, [{"id": "check", "bounds": {"h": [1, 5]}}])
    assert result["regions"][0]["integral"] == 20.


def test_default_channel_and_thousand_assignment_cap(tmp_path):
    from nmrnext.assignments import load_assignment_template, save_assignment_template
    t = template(spectrum()); t.pop("analysis_channel")
    t["assignments"] = [dict(t["assignments"][0], assignment_id=str(i)) for i in range(1000)]
    path = tmp_path / "thousand.json"
    normalized = save_assignment_template(t, path)
    assert normalized["analysis_channel"] == 0
    assert load_assignment_template(path) == normalized
    assert len(normalized["assignments"]) == 1000


def test_incompatible_hz_reference_frame_is_not_silently_transferred():
    from nmrnext.assignments import measure_assignments
    d = spectrum()
    target = Dataset(d.data, [replace(d.axes[0], reference_hz=10)])
    row, = measure_assignments(target, template(d, unit="Hz", search=(98, 90), integration=(98, 90)), axis_map={"h": "h"})
    assert row["status"] == "failed" and row["error"]["code"] == "INCOMPATIBLE_ASSIGNMENT_FRAME"


def test_template_roundtrip_is_exact_and_exclusive(tmp_path):
    from nmrnext.assignments import load_assignment_template, save_assignment_template
    t = template(spectrum())
    before = deepcopy(t)
    path = tmp_path / "labels.json"
    assert save_assignment_template(t, path) == t
    assert load_assignment_template(path) == t == before
    with pytest.raises(NMRNextError):
        save_assignment_template(t, path)
    assert json.loads(path.read_text()) == t


@pytest.mark.parametrize("change", ["top", "reference", "axis", "assignment", "duplicate", "nan", "bool_channel", "1001"])
def test_invalid_template_refuses_before_output_creation(tmp_path, change):
    from nmrnext.assignments import save_assignment_template
    t = template(spectrum())
    if change in ("top", "reference", "assignment"):
        target = t if change == "top" else t["reference"] if change == "reference" else t["assignments"][0]
        target["unknown"] = 1
    elif change == "axis":
        t["reference"]["axes"][0]["unknown"] = 1
    elif change == "duplicate":
        t["assignments"] *= 2
    elif change == "nan":
        t["assignments"][0]["threshold"] = float("nan")
    elif change == "bool_channel":
        t["analysis_channel"] = False
    else:
        t["assignments"] = [dict(t["assignments"][0], assignment_id=str(i)) for i in range(1001)]
    path = tmp_path / "bad.json"
    with pytest.raises(NMRNextError):
        save_assignment_template(t, path)
    assert not path.exists()


def test_load_rejects_duplicate_json_keys(tmp_path):
    from nmrnext.assignments import load_assignment_template
    p = tmp_path / "duplicate.json"
    p.write_text('{"schema_version":"1","schema_version":"2"}')
    with pytest.raises(NMRNextError):
        load_assignment_template(p)


@pytest.mark.parametrize("unit,search,integration", [
    ("points", (1, 5), (1, 5)), ("Hz", (98, 90), (98, 90)),
    ("ppm", (4.896, 4.88), (4.896, 4.88))])
def test_measured_positive_height_volume_and_parent_coordinates(unit, search, integration):
    from nmrnext.assignments import measure_assignments
    d = spectrum()
    original = d.data.tobytes()
    rows = measure_assignments(d, template(d, unit=unit, search=search, integration=integration), axis_map={"h": "h"})
    row, = rows
    assert (row["assignment_id"], row["label"], row["status"], row["candidate_count"], row["count_truncated"]) == ("a-stable", "my signal", "measured", 1, False)
    assert row["parent_points"] == {"h": 3}
    assert row["coordinates"]["h"] == {"Hz": 94., "ppm": 4.888}
    assert row["height"] == 6.
    assert row["volume"] == pytest.approx(20.)  # (0+2+6+2+0), trapezoid * |2 Hz|
    assert row["volume_unit"] == "intensity*Hz"
    assert row["integration_bounds"] == {"h": list(integration)}
    assert row["point_bounds"]["h"] == pytest.approx([1., 5.])
    assert "relative_integrals_not_absolute_concentration" in row["warnings"]
    assert d.data.tobytes() == original and d.data.dtype == np.float32


def test_negative_height_volume_fixed_bounds_and_fractional_endpoints():
    from nmrnext.assignments import measure_assignments
    d = spectrum()
    t = template(d, search=(6, 8), integration=(6.5, 7.5))
    t["assignments"][0]["sign"] = "negative"
    row, = measure_assignments(d, t, axis_map={"h": "h"})
    assert row["status"] == "measured" and row["parent_points"] == {"h": 7}
    assert row["height"] == -3. and row["volume"] == -4.5
    assert row["point_bounds"] == {"h": [6.5, 7.5]}


@pytest.mark.parametrize("values,status,count", [
    ((0, 0, 0, 0, 0, 0, 0, 0, 0), "not_found", 0),
    ((0, 0, 2, 0, 20, 0, 0, 0, 0), "ambiguous", 2)])
def test_no_match_or_multiple_never_selects_strongest(values, status, count):
    from nmrnext.assignments import measure_assignments
    d = spectrum(values)
    row, = measure_assignments(d, template(d), axis_map={"h": "h"})
    assert (row["status"], row["candidate_count"]) == (status, count)
    assert row["height"] is None and row["volume"] is None and row["coordinates"] is None


def test_edge_candidate_retains_explicit_warning():
    from nmrnext.assignments import measure_assignments
    d = spectrum((0, 4, 0, 0, 0, 0, 0, 0, 0))
    row, = measure_assignments(d, template(d), axis_map={"h": "h"})
    assert row["status"] == "measured" and row["parent_points"] == {"h": 1}
    assert "search_region_edge" in row["warnings"]


def test_uncalibrated_ppm_and_changed_points_grid_are_failed_rows():
    from nmrnext.assignments import measure_assignments
    ref = spectrum()
    t = template(ref, unit="ppm", search=(4.896, 4.88), integration=(4.896, 4.88))
    row, = measure_assignments(spectrum(calibrated=False), t, axis_map={"h": "h"})
    assert row["status"] == "failed" and row["error"]["code"] == "UNAVAILABLE_COORDINATES"
    target = Dataset(ref.data, [replace(ref.axes[0], step=-1)])
    row, = measure_assignments(target, template(ref), axis_map={"h": "h"})
    assert row["status"] == "failed" and row["error"]["code"] == "INCOMPATIBLE_ASSIGNMENT_GRID"


def test_hz_transfer_uses_new_grid_and_reference_digest_is_not_item_identity():
    from nmrnext.assignments import measure_assignments
    ref = spectrum()
    target = Dataset(np.array([[0, 0, 0, 4, 0, 0, 0]], dtype=np.float64),
                     [replace(ref.axes[0], size=7, start=98, step=-1)])
    t = template(ref, unit="Hz", search=(97, 93), integration=(97, 93))
    row, = measure_assignments(target, t, axis_map={"h": "h"})
    assert row["status"] == "measured" and row["parent_points"] == {"h": 3}
    assert row["coordinates"]["h"]["Hz"] == 95. and row["volume"] == 4.


def test_ppm_transfer_allows_different_observation_frequency_and_grid():
    from nmrnext.assignments import measure_assignments
    ref = spectrum()
    target = Dataset(np.array([[0, 0, 0, 4, 0, 0, 0]], dtype=np.float64),
                     [replace(ref.axes[0], size=7, start=49, step=-.5, observation_mhz=250)])
    t = template(ref, unit="ppm", search=(4.894, 4.886), integration=(4.894, 4.886))
    row, = measure_assignments(target, t, axis_map={"h": "h"})
    assert row["status"] == "measured" and row["height"] == 4.
    assert row["coordinates"]["h"]["ppm"] == pytest.approx(4.89)
    assert row["volume"] == pytest.approx(2.)


@pytest.mark.parametrize("channel", [-1, 1, True])
def test_invalid_channel_refuses_instead_of_switching(channel):
    from nmrnext.assignments import measure_assignments
    d = spectrum(); t = template(d); t["analysis_channel"] = channel
    with pytest.raises(NMRNextError):
        measure_assignments(d, t, axis_map={"h": "h"})


def test_quadrature_channel_is_explicit_and_all_input_channels_unchanged():
    from nmrnext.assignments import measure_assignments
    d0 = spectrum()
    d = Dataset(np.vstack([d0.data, -d0.data]), d0.axes, ["h"])
    t = template(d); t["analysis_channel"] = 1; t["assignments"][0]["sign"] = "negative"
    raw = d.data.tobytes()
    row, = measure_assignments(d, t, axis_map={"h": "h"})
    assert row["height"] == -6. and row["volume"] == -20.
    assert d.data.tobytes() == raw and d.complex_axes == ("h",)
    altered = Dataset(d.data, d.axes, ["h"], {"quadrature_index_orientation": {"h": 1}})
    with pytest.raises(NMRNextError):
        measure_assignments(altered, t, axis_map={"h": "h"})


@pytest.mark.parametrize("mapping", [{}, {"h": "missing"}, {"h": "n", "n": "h"}])
def test_missing_or_swapped_axis_mapping_refuses(mapping):
    from nmrnext.assignments import measure_assignments
    d = Dataset(np.zeros((1, 5, 5), dtype=np.float64),
                [Axis("h", 5, domain="frequency", unit="Hz", nucleus="1H"),
                 Axis("n", 5, domain="frequency", unit="Hz", nucleus="15N")])
    t = template(d)
    t["assignments"][0]["search_bounds"]["n"] = [0, 4]
    t["assignments"][0]["integration_bounds"]["n"] = [0, 4]
    with pytest.raises(NMRNextError):
        measure_assignments(d, t, axis_map=mapping)


def test_overlapping_integration_boxes_fail_without_recentering():
    from nmrnext.assignments import measure_assignments
    d = spectrum(); t = template(d)
    t["assignments"].append(dict(t["assignments"][0], assignment_id="other", label="second"))
    t["assignments"].append(dict(t["assignments"][0], assignment_id="unrelated", sign="negative",
                                 search_bounds={"h": [6, 8]}, integration_bounds={"h": [6, 8]}))
    rows = measure_assignments(d, t, axis_map={"h": "h"})
    assert [r["assignment_id"] for r in rows] == ["a-stable", "other", "unrelated"]
    assert all(r["status"] == "failed" and r["error"]["code"] == "OVERLAPPING_REGIONS" and r["volume"] is None for r in rows[:2])
    assert rows[2]["status"] == "measured" and rows[2]["volume"] == -6.


def test_bounded_roi_precedes_parent_size_cap_and_parent_digest_once(monkeypatch):
    from nmrnext import assignments
    d = Dataset(np.zeros((1, 2_000_002), dtype=np.float32),
                [Axis("h", 2_000_002, domain="frequency", unit="Hz")])
    t = template(d, search=(1, 5), integration=(1, 5))
    t["assignments"].append(dict(t["assignments"][0], assignment_id="far", search_bounds={"h": [10, 15]}, integration_bounds={"h": [10, 15]}))
    calls = []
    real = assignments.dataset_digest
    def observed(ds):
        calls.append(ds is d)
        return real(ds)
    monkeypatch.setattr(assignments, "dataset_digest", observed)
    rows = assignments.measure_assignments(d, t, axis_map={"h": "h"})
    assert [r["status"] for r in rows] == ["not_found", "not_found"]
    assert calls.count(True) == 1
    t["assignments"] = t["assignments"][:1]
    t["assignments"][0]["search_bounds"] = {"h": [0, 2_000_001]}
    row, = assignments.measure_assignments(d, t, axis_map={"h": "h"})
    assert row["status"] == "failed" and row["error"]["code"] == "ANALYSIS_SIZE_LIMIT"
    t["assignments"][0]["search_bounds"] = {"h": [1, 5]}
    t["assignments"][0]["integration_bounds"] = {"h": [0, 2_000_001]}
    row, = assignments.measure_assignments(d, t, axis_map={"h": "h"})
    assert row["status"] == "failed" and row["error"]["code"] == "ANALYSIS_SIZE_LIMIT"


def test_candidate_cap_is_explicit_lower_bound_not_strongest_match():
    from nmrnext.assignments import measure_assignments
    values = np.zeros(2105, dtype=np.float32); values[1::2] = 2.
    d = spectrum(values)
    row, = measure_assignments(d, template(d, search=(0, 2104), integration=(0, 2104)), axis_map={"h": "h"})
    assert row["status"] == "ambiguous" and row["candidate_count"] == 1000
    assert row["count_truncated"] is True and row["height"] is None


def test_signed_2d_volume_and_axis_rename_preserve_parent_mapping():
    from nmrnext.assignments import measure_assignments
    values = np.zeros((1, 5, 5), dtype=np.float32); values[0, 2, 2] = -7.
    axes = [Axis("h", 5, domain="frequency", unit="Hz", step=-2, nucleus="1H"),
            Axis("n", 5, domain="frequency", unit="Hz", step=3, nucleus="15N")]
    d = Dataset(values, axes); t = template(d, search=(0, 4), integration=(0, 4))
    a = t["assignments"][0]; a["sign"] = "negative"
    a["search_bounds"]["n"] = [0, 4]; a["integration_bounds"]["n"] = [0, 4]
    target = Dataset(values, [replace(axes[0], id="x"), replace(axes[1], id="y")])
    row, = measure_assignments(target, t, axis_map={"h": "x", "n": "y"})
    assert row["status"] == "measured" and row["height"] == -7.
    assert row["volume"] == -42. and row["volume_unit"] == "intensity*Hz^2"
    assert row["parent_points"] == {"x": 2, "y": 2}
