"""Selected figures use the exact applied analysis region and local reports."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6 import QtCore, QtWidgets
from unittest.mock import patch

from nmrnext.analysis import dataset_digest
from nmrnext.errors import NMRNextError
from nmrnext.figures import export_figure
from nmrnext.gui import Workbench
from nmrnext.model import Axis, Dataset


@pytest.fixture
def workbench():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Workbench()
    yield window
    window.close()
    window.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()


def spectrum(*, xstep=-5.0, ystep=10.0, referenced=True):
    y = np.arange(64)[:, None]
    x = np.arange(128)[None, :]
    z = (np.exp(-((y - 25) / 3) ** 2 - ((x - 70) / 4) ** 2) -
         0.4 * np.exp(-((y - 44) / 4) ** 2 - ((x - 27) / 6) ** 2)).astype(np.float32)
    axes = (Axis("a0", 64, domain="frequency", unit="Hz", start=2000, step=ystep,
                 observation_mhz=50 if referenced else None,
                 reference_ppm=100 if referenced else None),
            Axis("a1", 128, domain="frequency", unit="Hz", start=1000, step=xstep,
                 observation_mhz=100 if referenced else None,
                 reference_ppm=10 if referenced else None))
    return Dataset(z[None], axes)


def select_points(window, bounds=((10, 49), (20, 99))):
    window.region_unit.setCurrentText("points")
    for axis, (lo, hi) in zip(window.current.axes, bounds):
        window.region_bounds[axis.id][0].setValue(lo)
        window.region_bounds[axis.id][1].setValue(hi)
    window._select_entered_region()
    assert window.selected_regions


@pytest.mark.parametrize("xstep,ystep,referenced", [(-5.0, 10.0, True), (5.0, -10.0, False)])
def test_selected_figure_uses_exact_local_grid_and_compatible_reports(workbench, tmp_path, xstep, ystep, referenced):
    workbench.load_dataset_now(spectrum(xstep=xstep, ystep=ystep, referenced=referenced))
    select_points(workbench)
    integration = workbench.analyze_now("integrate")
    workbench.threshold_check.setChecked(True)
    workbench.threshold_spin.setValue(0.1)
    peaks = workbench.analyze_now("peaks")
    assert peaks["peaks"]
    workbench.analysis_reports["autophase"] = {
        "kind": "autophase", "result_dataset_digest": dataset_digest(workbench.current)}

    region, reports, title, phase_omitted = workbench._figure_request()
    assert region.data.shape == (1, 40, 80)
    assert set(reports) == {"integrate", "peaks"}
    assert phase_omitted is True
    assert all(report["dataset_digest"] == dataset_digest(region) for report in reports.values())
    assert all(report["parent_region"]["parent_dataset_digest"] == dataset_digest(workbench.current)
               for report in reports.values())
    assert "selected" in title.lower()
    path = tmp_path / "region.svg"
    result = export_figure(region, path, analysis=reports, title=title)
    assert path.is_file() and result["dataset_digest"] == dataset_digest(region)
    assert result["axis_units"] == {a.id: "ppm" if referenced else "Hz" for a in region.axes}
    assert result["peaks_in_report"] == len(peaks["peaks"])
    assert result["analysis_digest"]


def test_selected_figure_refuses_original_pending_and_stale_region(workbench):
    workbench.load_dataset_now(spectrum())
    select_points(workbench)
    workbench.analyze_now("integrate")
    workbench.view_combo.setCurrentIndex(1)
    with pytest.raises(NMRNextError) as original:
        workbench._figure_request()
    assert original.value.code == "ANALYSIS_STATE"
    workbench.view_combo.setCurrentIndex(0)
    workbench.region_bounds["a1"][0].setValue(21)
    with pytest.raises(NMRNextError) as pending:
        workbench._figure_request()
    assert pending.value.code == "ANALYSIS_STATE"
    workbench.region_bounds["a1"][0].setValue(20)
    workbench.analysis_reports["integrate"]["parent_region"]["bounds"]["a1"] = [21, 99]
    with pytest.raises(NMRNextError) as stale:
        workbench._figure_request()
    assert stale.value.code in {"STALE_ANALYSIS", "INVALID_REGION"}


def test_unselected_figure_keeps_parent_dataset(workbench):
    dataset = spectrum()
    workbench.load_dataset_now(dataset)
    figure_data, reports, title, phase_omitted = workbench._figure_request()
    assert figure_data is dataset and reports == {} and phase_omitted is False
    assert title == workbench.source_name


def test_gui_save_action_writes_selected_figure_and_refuses_mixed_reports(workbench, tmp_path):
    workbench.load_dataset_now(spectrum())
    select_points(workbench)
    workbench.analyze_now("integrate")
    output = tmp_path / "selected.png"
    def run_in_place(work, on_success, _status):
        on_success(work())
    with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(output), "")), \
         patch.object(workbench, "_start_job", side_effect=run_in_place):
        workbench._save_figure_dialog()
    assert output.read_bytes().startswith(b"\x89PNG")
    assert "selected" in workbench.status.text().lower() or "saved" in workbench.status.text().lower()

    # A later selection does not silently pair an earlier integral with new peaks.
    select_points(workbench, ((12, 45), (30, 90)))
    workbench.analyze_now("peaks")
    with pytest.raises(NMRNextError) as mixed:
        workbench._figure_request()
    assert mixed.value.code == "STALE_ANALYSIS"
    refused = tmp_path / "refused.svg"
    with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(refused), "")), \
         patch.object(workbench, "_start_job") as launch, \
         patch.object(workbench, "_show_error") as error:
        workbench._save_figure_dialog()
    launch.assert_not_called()
    error.assert_called_once()
    assert not refused.exists()


def test_selected_figure_refuses_preview_before_export(workbench):
    workbench.load_dataset_now(spectrum())
    select_points(workbench)
    workbench.preview = spectrum()
    with pytest.raises(NMRNextError) as preview:
        workbench._figure_request()
    assert preview.value.code == "ANALYSIS_STATE"


def test_selected_figure_refuses_mislabeled_report_kind(workbench):
    workbench.load_dataset_now(spectrum())
    select_points(workbench)
    workbench.analyze_now("integrate")
    workbench.analysis_reports["integrate"]["kind"] = "peaks"
    with pytest.raises(NMRNextError) as mislabeled:
        workbench._figure_request()
    assert mislabeled.value.code == "STALE_ANALYSIS"
    report = workbench.analysis_reports["integrate"]
    report["kind"] = None
    workbench.analysis_reports = {"unknown": report}
    with pytest.raises(NMRNextError) as unknown:
        workbench._figure_request()
    assert unknown.value.code == "STALE_ANALYSIS"


def test_unselected_large_figure_keeps_existing_size_refusal(workbench, tmp_path):
    length = 4_000_001
    parent = Dataset(np.zeros((1, length), dtype=np.float32),
                     (Axis("f", length, domain="frequency", unit="Hz", step=1),))
    workbench.load_dataset_now(parent)
    figure_data, reports, _, _ = workbench._figure_request()
    assert figure_data is parent and reports == {}
    destination = tmp_path / "too-large.svg"
    with pytest.raises(NMRNextError) as too_large:
        export_figure(figure_data, destination, analysis=reports)
    assert too_large.value.code == "FIGURE_SIZE"
    assert not destination.exists()
