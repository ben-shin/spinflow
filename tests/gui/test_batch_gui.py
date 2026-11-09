"""The batch workbench keeps assignment authoring in real Qt widgets."""
import os
import csv
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6 import QtCore, QtWidgets
from unittest.mock import patch

from nmrnext.assignments import load_assignment_template
from nmrnext.batch import load_batch_manifest
from nmrnext.gui import Workbench
from nmrnext.io import read_dataset, write_dataset
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


def _frequency_source(axis_id="h", offset=0):
    axis = Axis(axis_id, 16, domain="frequency", unit="Hz", start=15., step=-1.,
                observation_mhz=500., reference_ppm=4.7, encoding="complex")
    data = np.arange(2 * 16, dtype=np.float32).reshape(2, 16) + offset
    return Dataset(data, (axis,), (axis_id,))


def _fill_assignment_row(table):
    table.item(0, 1).setText("H1")
    table.cellWidget(0, 2).setCurrentText("positive")
    table.cellWidget(0, 3).setCurrentText("points")


def _accept_next_dialog(title_fragment, fill=None):
    app = QtWidgets.QApplication.instance()
    def accept():
        for widget in app.topLevelWidgets():
            if isinstance(widget, QtWidgets.QDialog) and title_fragment in widget.windowTitle():
                if fill is not None:
                    fill(widget)
                widget.accept()
                return
        QtCore.QTimer.singleShot(20, accept)
    QtCore.QTimer.singleShot(20, accept)


def _map_rows(workbench):
    for row in range(workbench.batch_items_table.rowCount()):
        workbench.batch_items_table.selectRow(row)
        _accept_next_dialog("Map assignment axes")
        workbench.batch_map_axes_button.click()
        _wait_for_job(workbench)


def _add_input(workbench, path, output_dir):
    workbench.batch_output_directory_edit.setText(str(output_dir))
    with patch("nmrnext.gui.QtWidgets.QFileDialog.getOpenFileNames",
               return_value=([str(path)], "NMR spectra")):
        workbench.batch_add_files_button.click()


def _save_manifest(workbench, path):
    with patch("nmrnext.gui.QtWidgets.QFileDialog.getSaveFileName",
               return_value=(str(path), "JSON (*.json)")):
        workbench.batch_save_manifest_button.click()
    assert path.is_file()


def _wait_for_job(window):
    app = QtWidgets.QApplication.instance()
    deadline = QtCore.QDeadlineTimer(10000)
    while window._busy and not deadline.hasExpired():
        app.processEvents()
        QtCore.QThread.msleep(5)
    app.processEvents()
    assert not window._busy


class _QueuedProgressRecorder(QtCore.QObject):
    def __init__(self):
        super().__init__()
        self.events = []
        self.delivery_threads = []

    @QtCore.Slot(object)
    def record(self, envelope):
        self.events.append(envelope)
        self.delivery_threads.append(QtCore.QThread.currentThread())


class _StopAfterCompleted(QtCore.QObject):
    def __init__(self, stop_event):
        super().__init__()
        self.stop_event = stop_event
        self.completed = []

    @QtCore.Slot(object)
    def on_progress(self, envelope):
        _ticket, event = envelope
        if event["state"] == "completed" and not self.completed:
            self.completed.append(event["item_id"])
            self.stop_event.set()


def test_batch_tab_has_small_native_actions_and_editable_assignment_fields(workbench):
    assert workbench.batch_tab_index >= 0
    assert workbench.styleSheet() == ""
    assert workbench.batch_open_manifest_button.text().startswith("Open")
    assert workbench.batch_label_button.text() == "Label"
    assert workbench.batch_apply_button.text() == "Apply to files"
    assert workbench.batch_export_csv_button.text().startswith("Export CSV")
    assert workbench.assignment_table.editTriggers() & QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked
    assert workbench.assignment_table.columnCount() == 6
    assert workbench.assignment_channel_combo.currentData() == 0
    assert workbench.batch_processing_mode.currentData() == "copy"
    assert not workbench.batch_advanced_widget.isVisible()
    assert "JSON" not in " ".join(workbench.assignment_table.horizontalHeaderItem(i).text()
                                   for i in range(workbench.assignment_table.columnCount()))


def test_assignment_template_editor_saves_and_reopens_real_sidecar(workbench, tmp_path):
    workbench.load_dataset_now(_frequency_source(), "Reference spectrum")
    workbench.batch_label_button.click()
    _wait_for_job(workbench)
    assert workbench.assignment_table.rowCount() == 1
    _fill_assignment_row(workbench.assignment_table)

    path = tmp_path / "labels.assignment.json"
    with patch("nmrnext.gui.QtWidgets.QFileDialog.getSaveFileName",
               return_value=(str(path), "JSON (*.json)")):
        workbench.batch_save_template_button.click()
    _wait_for_job(workbench)
    assert path.is_file()
    saved = load_assignment_template(path)
    assert saved["analysis_channel"] == 0
    assert saved["reference"]["axes"][0]["id"] == "h"
    assert saved["assignments"][0]["assignment_id"] == "assignment-001"
    assert saved["assignments"][0]["integration_bounds"]["h"][0] < saved["assignments"][0]["integration_bounds"]["h"][1]

    def set_regions(dialog):
        boxes = dialog.findChildren(QtWidgets.QDoubleSpinBox)
        assert len(boxes) == 4
        boxes[0].setValue(3.0)
        boxes[1].setValue(8.0)
    _accept_next_dialog("fixed search", set_regions)
    workbench.assignment_table.cellWidget(0, 4).click()
    updated = workbench.assignment_table.item(0, 0).data(QtCore.Qt.ItemDataRole.UserRole)
    assert updated["search_bounds"]["h"] == [3.0, 8.0]

    workbench.assignment_table.item(0, 1).setText("edited but not saved")
    with patch("nmrnext.gui.QtWidgets.QFileDialog.getOpenFileName",
               return_value=(str(path), "JSON (*.json)")):
        workbench.batch_open_template_button.click()
    _wait_for_job(workbench)
    assert workbench.assignment_table.item(0, 1).text() == "H1"

    original_digest = saved["reference"]["dataset_digest"]
    workbench.load_dataset_now(_frequency_source(offset=100), "Different current spectrum")
    workbench.assignment_table.item(0, 1).setText("renamed on original reference")
    edited_path = tmp_path / "edited.assignment.json"
    with patch("nmrnext.gui.QtWidgets.QFileDialog.getSaveFileName",
               return_value=(str(edited_path), "JSON (*.json)")):
        workbench.batch_save_template_button.click()
    edited = load_assignment_template(edited_path)
    assert edited["reference"]["dataset_digest"] == original_digest
    assert edited["assignments"][0]["label"] == "renamed on original reference"
    workbench.batch_label_button.click()
    _wait_for_job(workbench)
    assert workbench.assignment_table.rowCount() == 1
    assert "belongs to another reference" in workbench.status.text()


def test_assignment_controls_follow_surviving_row_after_deleting_a(monkeypatch, workbench):
    axes = [{"id": "h", "size": 32, "start": 10.0, "step": 2.0,
             "observation_mhz": 500.0, "reference_ppm": 4.7, "reference_hz": 0.0}]
    for letter, low in (("A", 1.0), ("B", 2.0), ("C", 3.0)):
        assignment = {"assignment_id": letter, "label": letter, "sign": "positive", "unit": "points",
                      "threshold": 0.0, "search_bounds": {"h": [low, low + 1]},
                      "integration_bounds": {"h": [low, low + 1]}}
        workbench._batch_add_assignment_row(assignment, axes)
    table = workbench.assignment_table
    table.selectRow(0)
    workbench._batch_remove_assignment()
    routed = []
    monkeypatch.setattr(workbench, "_batch_edit_assignment_bounds", lambda row: routed.append(
        table.item(row, 0).data(QtCore.Qt.ItemDataRole.UserRole)["assignment_id"]))
    table.cellWidget(0, 4).click()
    assert routed == ["B"]

    before_c = table.item(1, 0).data(QtCore.Qt.ItemDataRole.UserRole)
    table.cellWidget(0, 3).setCurrentText("Hz")
    edited_b = table.item(0, 0).data(QtCore.Qt.ItemDataRole.UserRole)
    after_c = table.item(1, 0).data(QtCore.Qt.ItemDataRole.UserRole)
    assert edited_b["unit"] == "Hz"
    assert edited_b["integration_bounds"]["h"] == [14.0, 16.0]
    assert after_c == before_c


def test_reopened_manifest_save_preserves_its_recipe_without_last_plan(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    recipe = [{"op": "phase", "axis": "h", "p0": 20.0, "p1": 0.0, "pivot": 0.0}]
    manifest = {"schema_version": "1", "kind": "batch_manifest", "backend": "numpy", "threads": 1,
                "memory_bytes": 128 * 1024**2, "operations": recipe,
                "items": [{"item_id": "one", "input": "in.nmrh5", "output": "out.nmrh5"}]}
    from nmrnext.batch import save_batch_manifest
    source = tmp_path / "saved.json"
    save_batch_manifest(manifest, source)
    window = Workbench()
    try:
        loaded = load_batch_manifest(source)
        window._batch_accept_manifest(source, loaded, None, None)
        assert window.last_plan is None
        saved = window._batch_manifest_from_widgets()
        assert saved["operations"] == recipe
        assert saved["backend"] == "numpy"
        assert saved["memory_bytes"] == 128 * 1024**2
    finally:
        window.close()
        window.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        app.processEvents()


def test_reopened_manifest_save_does_not_replace_recipe_with_current_applied(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    saved_operations = [{"op": "phase", "axis": "h", "p0": 20.0, "p1": 0.0, "pivot": 0.0}]
    manifest = {"schema_version": "1", "kind": "batch_manifest", "backend": "numpy", "threads": 1,
                "memory_bytes": 128 * 1024**2, "operations": saved_operations,
                "items": [{"item_id": "one", "input": "in.nmrh5", "output": "out.nmrh5"}]}
    from nmrnext.batch import save_batch_manifest
    source = tmp_path / "saved-again.json"
    save_batch_manifest(manifest, source)
    window = Workbench()
    try:
        window.load_dataset_now(_frequency_source(), "Applied p0=90")
        window.backend_combo.setCurrentText("NumPy (experimental)")
        window.set_ordered_recipe_now(
            [{"op": "phase", "axis": "h", "p0": 90.0, "p1": 0.0, "pivot": 0.0}])
        window.process_now()
        assert window.last_operations[0]["p0"] == 90.0
        loaded = load_batch_manifest(source)
        window._batch_accept_manifest(source, loaded, None, None)
        saved = window._batch_manifest_from_widgets()
        assert saved["operations"] == saved_operations
        assert saved["backend"] == "numpy"
        assert saved["memory_bytes"] == 128 * 1024**2
    finally:
        window.close()
        window.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        app.processEvents()


def test_input_list_keeps_duplicate_basenames_as_distinct_items(workbench, tmp_path):
    source = _frequency_source()
    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    first, second = first_dir / "sample.nmrh5", second_dir / "sample.nmrh5"
    write_dataset(source, first)
    write_dataset(source, second)
    workbench.batch_output_directory_edit.setText(str(output_dir))
    with patch("nmrnext.gui.QtWidgets.QFileDialog.getOpenFileNames",
               return_value=([str(first), str(second)], "NMR spectra")):
        workbench.batch_add_files_button.click()
    table = workbench.batch_items_table
    assert table.rowCount() == 2
    ids = [table.item(row, 0).text() for row in range(2)]
    paths = [table.item(row, 1).data(QtCore.Qt.ItemDataRole.UserRole) for row in range(2)]
    outputs = [table.item(row, 2).text() for row in range(2)]
    assert ids[0] != ids[1]
    assert Path(paths[0]).parent != Path(paths[1]).parent
    assert outputs[0] != outputs[1]


def test_gui_plans_runs_and_exports_with_the_real_batch_service(workbench, tmp_path):
    source = _frequency_source()
    workbench.load_dataset_now(source, "Frequency reference")
    workbench.backend_combo.setCurrentText("NumPy (experimental)")
    workbench.process_now()  # Empty applied recipe; tests explicitly select NumPy.
    workbench.batch_label_button.click()
    _wait_for_job(workbench)
    _fill_assignment_row(workbench.assignment_table)

    template_path = tmp_path / "assignment.json"
    with patch("nmrnext.gui.QtWidgets.QFileDialog.getSaveFileName",
               return_value=(str(template_path), "JSON (*.json)")):
        workbench.batch_save_template_button.click()
    _wait_for_job(workbench)
    assert template_path.is_file()

    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    input_path = input_dir / "sample.nmrh5"
    write_dataset(source, input_path)
    workbench.batch_output_directory_edit.setText(str(output_dir))
    with patch("nmrnext.gui.QtWidgets.QFileDialog.getOpenFileNames",
               return_value=([str(input_path)], "NMR spectra")):
        workbench.batch_add_files_button.click()
    workbench.batch_processing_mode.setCurrentIndex(workbench.batch_processing_mode.findData("applied"))
    _map_rows(workbench)
    assert workbench._batch_operations == workbench.last_operations
    assert workbench._batch_backend == "numpy"

    manifest_path = tmp_path / "batch.json"
    with patch("nmrnext.gui.QtWidgets.QFileDialog.getSaveFileName",
               return_value=(str(manifest_path), "JSON (*.json)")):
        workbench.batch_save_manifest_button.click()
    assert manifest_path.is_file()
    workbench.batch_plan_button.click()
    _wait_for_job(workbench)
    assert workbench._batch_plan_result["kind"] == "batch_plan"
    recorder = _QueuedProgressRecorder()
    workbench._batch_progress_bridge.progressed.connect(
        recorder.record, QtCore.Qt.ConnectionType.QueuedConnection)
    workbench.batch_apply_button.click()
    assert workbench._busy
    assert not workbench.batch_add_files_button.isEnabled()
    assert not workbench.process_button.isEnabled()
    assert workbench.batch_stop_button.isEnabled()
    workbench.backend_combo.setCurrentText("Native C++ / PocketFFT")
    _wait_for_job(workbench)
    workbench._batch_progress_bridge.progressed.disconnect(recorder.record)
    assert recorder.events
    assert {entry[1]["state"] for entry in recorder.events} >= {"started", "completed"}
    assert all(thread == workbench.thread() for thread in recorder.delivery_threads)
    assert workbench._batch_csv_path.is_file()
    assert workbench.batch_items_table.item(0, 4).text().startswith("completed")
    assert (output_dir / "item-0001.nmrh5").is_file()
    assert (output_dir / "item-0001.nmrh5").is_file()
    with workbench._batch_csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    assert csv_rows[0]["source_locator"] == str(input_path.resolve())

    exported = tmp_path / "exported.csv"
    with patch("nmrnext.gui.QtWidgets.QFileDialog.getSaveFileName",
               return_value=(str(exported), "CSV (*.csv)")):
        workbench.batch_export_csv_button.click()
    assert exported.read_bytes() == workbench._batch_csv_path.read_bytes()


def _prepare_batch(workbench, tmp_path, source, inputs):
    workbench.load_dataset_now(source, "Frequency reference")
    workbench.backend_combo.setCurrentText("NumPy (experimental)")
    workbench.process_now()
    workbench.batch_label_button.click()
    _wait_for_job(workbench)
    _fill_assignment_row(workbench.assignment_table)
    template_path = tmp_path / "assignment.json"
    with patch("nmrnext.gui.QtWidgets.QFileDialog.getSaveFileName",
               return_value=(str(template_path), "JSON (*.json)")):
        workbench.batch_save_template_button.click()
    _wait_for_job(workbench)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    workbench.batch_output_directory_edit.setText(str(output_dir))
    workbench._batch_add_input_paths([str(path) for path in inputs])
    workbench.batch_processing_mode.setCurrentIndex(workbench.batch_processing_mode.findData("applied"))
    _map_rows(workbench)
    manifest_path = tmp_path / "batch.json"
    with patch("nmrnext.gui.QtWidgets.QFileDialog.getSaveFileName",
               return_value=(str(manifest_path), "JSON (*.json)")):
        workbench.batch_save_manifest_button.click()
    assert manifest_path.is_file()
    return output_dir


def test_stop_after_current_item_keeps_completed_output_and_not_run_rows(workbench, tmp_path):
    source = _frequency_source()
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    input_path = input_dir / "sample.nmrh5"
    write_dataset(source, input_path)
    output_dir = _prepare_batch(workbench, tmp_path, source,
                                [input_path, input_path, input_path])
    workbench.batch_plan_button.click()
    _wait_for_job(workbench)

    original_start = workbench._start_job
    stop_receiver = None

    def connect_stop_before_worker(func, on_success, label):
        nonlocal stop_receiver
        if label == "Applying batch sequentially…":
            stop_receiver = _StopAfterCompleted(workbench._batch_stop_event)
            workbench._batch_progress_bridge.progressed.connect(
                stop_receiver.on_progress, QtCore.Qt.ConnectionType.DirectConnection)
        return original_start(func, on_success, label)

    workbench._start_job = connect_stop_before_worker
    workbench.batch_apply_button.click()
    try:
        _wait_for_job(workbench)
    finally:
        if stop_receiver is not None:
            workbench._batch_progress_bridge.progressed.disconnect(stop_receiver.on_progress)
        workbench._start_job = original_start

    assert stop_receiver is not None and len(stop_receiver.completed) == 1
    statuses = [workbench.batch_items_table.item(row, 4).text()
                for row in range(workbench.batch_items_table.rowCount())]
    assert statuses[0].startswith("completed")
    assert statuses[1].startswith("not_run")
    assert statuses[2].startswith("not_run")
    assert (output_dir / "item-0001.nmrh5").is_file()
    assert not (output_dir / "item-0002.nmrh5").exists()
    assert not (output_dir / "item-0003.nmrh5").exists()


def test_item_assignment_failure_is_visible_and_continue_preserves_other_item(workbench, tmp_path):
    source = _frequency_source("h")
    mismatched = _frequency_source("x")
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    valid_path, invalid_path = input_dir / "valid.nmrh5", input_dir / "invalid.nmrh5"
    write_dataset(source, valid_path)
    write_dataset(mismatched, invalid_path)
    output_dir = _prepare_batch(workbench, tmp_path, source, [valid_path, invalid_path])
    workbench.batch_items_table.item(1, 3).setData(
        QtCore.Qt.ItemDataRole.UserRole, {"h": "missing-axis"})
    workbench.batch_items_table.item(1, 3).setText("h → missing-axis")
    with patch("nmrnext.gui.QtWidgets.QFileDialog.getSaveFileName",
               return_value=(str(tmp_path / "batch-invalid-map.json"), "JSON (*.json)")):
        workbench.batch_save_manifest_button.click()
    assert (tmp_path / "batch-invalid-map.json").is_file()
    workbench.batch_continue_check.setChecked(True)
    workbench.batch_plan_button.click()
    _wait_for_job(workbench)
    workbench.batch_apply_button.click()
    _wait_for_job(workbench)
    assert workbench.batch_items_table.item(0, 4).text().startswith("completed")
    assert workbench.batch_items_table.item(1, 4).text().startswith("failed")
    assert "INVALID_AXIS_MAP" in workbench.batch_items_table.item(1, 4).toolTip()
    assert (output_dir / "item-0001.nmrh5").is_file()
    assert not (output_dir / "item-0002.nmrh5").exists()


def test_processing_only_manifest_copies_all_quadratures_with_real_service(workbench, tmp_path):
    axes = (Axis("h", 8, domain="frequency", unit="Hz", start=7., step=-1., encoding="complex"),
            Axis("n", 6, domain="frequency", unit="Hz", start=5., step=-1., encoding="complex"))
    samples = np.arange(4 * 8 * 6, dtype=np.float32).reshape(4, 8, 6)
    source = Dataset(samples, axes, ("h", "n"))
    input_path = tmp_path / "all-quadratures.nmrh5"
    output = tmp_path / "out"; output.mkdir()
    write_dataset(source, input_path)
    workbench.backend_combo.setCurrentText("NumPy (experimental)")
    _add_input(workbench, input_path, output)
    manifest_path = tmp_path / "processing-only.json"
    _save_manifest(workbench, manifest_path)
    manifest = load_batch_manifest(manifest_path)
    assert manifest["operations"] == []
    assert "assignment_template" not in manifest
    assert manifest["items"][0].get("axis_map", {}) == {}
    workbench.batch_plan_button.click(); _wait_for_job(workbench)
    assert not workbench._batch_plan_result["refusals"]
    workbench.batch_apply_button.click(); _wait_for_job(workbench)
    result = read_dataset(output / "item-0001.nmrh5")
    np.testing.assert_array_equal(result.data, samples)
    assert result.complex_axes == ("h", "n")
    assert workbench.batch_items_table.item(0, 4).text().startswith("completed")
    with workbench._batch_csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        assert list(csv.DictReader(stream)) == []

