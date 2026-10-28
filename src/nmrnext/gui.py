"""PySide6 scientific workbench backed by the public NMRNext API.

Run with ``python -m nmrnext.gui``. Qt is deliberately optional for headless use.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from .demo import generate_demo
from .errors import NMRNextError
from .io import export_preflight, read_dataset, write_dataset
from .operations import run_operations
from .planning import plan_operations


INK = "#14263a"
MUTED = "#596b7d"
ACCENT = "#1769a3"
SURFACE = "#f4f8fb"
PAPER = "#ffffff"
BORDER = "#d6e1e9"
_font_loaded = False


def _ensure_font():
    """Qt's offscreen Windows platform may expose no system font families."""
    global _font_loaded
    if _font_loaded:
        return
    if sys.platform == "win32":
        for name in ("segoeui.ttf", "segoeuib.ttf"):
            path = Path("C:/Windows/Fonts") / name
            if path.is_file():
                QtGui.QFontDatabase.addApplicationFont(str(path))
    _font_loaded = True


def component_name(index: int, complex_axes: tuple[str, ...]) -> str:
    """Name a channel from its actual quadrature-bit order."""
    if not complex_axes:
        return "Real"
    terms = [f"{axis}: {'imaginary' if index & (1 << bit) else 'real'}"
             for bit, axis in enumerate(complex_axes)]
    return "  /  ".join(terms)


def _digital_filter_info(metadata: dict) -> tuple[object, bool]:
    """Interpret only known import fields; preserve all opaque JSON unchanged."""
    imported = metadata.get("import")
    if not isinstance(imported, dict):
        return None, imported is not None
    filter_record = imported.get("digital_filter")
    if isinstance(filter_record, dict):
        return filter_record.get("state"), False
    return None, filter_record is not None


def human_error(exc: Exception) -> str:
    if isinstance(exc, NMRNextError):
        remedy = {
            "DIGITAL_FILTER_UNRESOLVED": "The acquisition needs a validated digital-filter correction before Fourier transform.",
            "QUADRATURE_REQUIRED": "Choose an axis that still has real and imaginary components.",
            "INVALID_DOMAIN": "Choose a time-domain axis for window, zero fill or forward transform.",
            "OUTPUT_EXISTS": "Choose a new output name. Existing files are never replaced here.",
            "UNACKNOWLEDGED_LOSS": "Review the export losses and acknowledge each one before saving.",
            "MEMORY_BUDGET_EXCEEDED": "Reduce the zero-fill factor or use a smaller dataset.",
        }.get(exc.code, "Check the selected axis, processing values and file profile.")
        detail = ""
        if exc.details.get("operation_index") is not None:
            detail = f"\nOperation {exc.details['operation_index'] + 1}."
        if exc.details.get("errors"):
            detail += "\nProfile limits: " + ", ".join(str(code) for code in exc.details["errors"])
        if exc.details.get("unacknowledged"):
            detail += "\nUnacknowledged losses: " + ", ".join(exc.details["unacknowledged"])
        return f"{exc.code}: {exc}{detail}\n{remedy}"
    return f"{type(exc).__name__}: {exc}"


class _Worker(QtCore.QObject):
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(object)
    finished = QtCore.Signal()

    def __init__(self, func):
        super().__init__()
        self.func = func

    @QtCore.Slot()
    def run(self):
        try:
            self.completed.emit(self.func())
        except Exception as exc:  # relay to the GUI thread; keep Qt alive
            self.failed.emit(exc)
        finally:
            self.finished.emit()


class ExportLossDialog(QtWidgets.QDialog):
    """Require a separate, explicit acknowledgment for every reported loss."""

    def __init__(self, report: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review export changes")
        self.setMinimumWidth(530)
        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel("This format changes or omits the following information. Select every change you accept.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.checks = []
        explanations = {
            "quadrature_projection": "Keep only component 0 (all-real); discard other quadrature channels",
            "float32_quantization": "Round intensities to 32-bit floating point",
            "metadata": "Omit NMRNext dataset metadata",
            "provenance": "Omit processing provenance",
            "reference_fields": "Collapse separate frequency reference fields",
            "axis_annotations": "Omit acquisition and axis annotations",
        }
        for loss in report["losses"]:
            check = QtWidgets.QCheckBox(explanations.get(loss, loss))
            check.setAccessibleName(f"Acknowledge {loss}")
            check.setProperty("loss_code", loss)
            layout.addWidget(check)
            self.checks.append(check)
        for conversion in report.get("conversions", []):
            layout.addWidget(QtWidgets.QLabel(
                f"Axis {conversion['axis']}: reverse point order for descending target coordinates"))
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save |
                                             QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept_if_complete)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_if_complete(self):
        if all(c.isChecked() for c in self.checks):
            self.accept()
        else:
            QtWidgets.QMessageBox.information(self, "Review every change",
                                              "Select every reported information loss to save this format.")

    def accepted_losses(self):
        return tuple(c.property("loss_code") for c in self.checks if c.isChecked())


class Workbench(QtWidgets.QMainWindow):
    """Thin GUI: recipes and data flow through the same APIs as the CLI."""

    def __init__(self):
        super().__init__()
        _ensure_font()
        self.setWindowTitle("NMRNext · Spectrum workbench")
        self.resize(1320, 840)
        self.setMinimumSize(950, 620)
        self.source = None
        self.current = None
        self.preview = None
        self.source_name = "No dataset"
        self.last_operations = []
        self.last_plan = None
        self.last_backend = None
        self._thread = None
        self._worker = None
        self._on_success = None
        self._busy = False
        self._suspend_preview_invalidation = False
        self._build_ui()
        self._style()
        self._refresh()

    def _build_ui(self):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        top = QtWidgets.QFrame()
        top.setObjectName("topbar")
        bar = QtWidgets.QHBoxLayout(top)
        bar.setContentsMargins(22, 13, 22, 13)
        title = QtWidgets.QLabel("NMRNext")
        title.setObjectName("brand")
        bar.addWidget(title)
        descriptor = QtWidgets.QLabel("Spectrum workbench")
        descriptor.setObjectName("descriptor")
        bar.addWidget(descriptor)
        bar.addStretch()
        self.dataset_title = QtWidgets.QLabel("No dataset")
        self.dataset_title.setObjectName("datasetTitle")
        bar.addWidget(self.dataset_title)
        outer.addWidget(top)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        outer.addWidget(splitter, 1)
        left = self._left_panel()
        splitter.addWidget(left)
        center = self._center_panel()
        splitter.addWidget(center)
        right = self._right_panel()
        splitter.addWidget(right)
        splitter.setSizes([240, 760, 320])
        splitter.setStretchFactor(1, 1)

        self.status = QtWidgets.QLabel("Open a dataset or create a synthetic example.")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(35)
        outer.addWidget(self.status)

    def _left_panel(self):
        panel = QtWidgets.QFrame()
        panel.setObjectName("sidepanel")
        panel.setMinimumWidth(205)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(18, 20, 17, 18)
        layout.setSpacing(10)
        layout.addWidget(self._heading("Data"))
        self.open_button = QtWidgets.QPushButton("Open spectrum…")
        self.open_button.clicked.connect(self._open_file)
        layout.addWidget(self.open_button)
        self.bruker_button = QtWidgets.QPushButton("Open Bruker FID…")
        self.bruker_button.clicked.connect(self._open_bruker)
        self.bruker_button.setToolTip("Controlled 1D acqus/fid profile; digital-filter correction may be required.")
        layout.addWidget(self.bruker_button)
        demo_row = QtWidgets.QHBoxLayout()
        self.demo1_button = QtWidgets.QPushButton("1D demo")
        self.demo2_button = QtWidgets.QPushButton("2D demo")
        self.demo1_button.clicked.connect(lambda: self.load_dataset_now(generate_demo(dimensions=1), "Synthetic 1D"))
        self.demo2_button.clicked.connect(lambda: self.load_dataset_now(generate_demo(dimensions=2), "Synthetic 2D"))
        demo_row.addWidget(self.demo1_button)
        demo_row.addWidget(self.demo2_button)
        layout.addLayout(demo_row)
        layout.addSpacing(16)
        layout.addWidget(self._heading("Acquisition / axes"))
        self.details = QtWidgets.QTextBrowser()
        self.details.setObjectName("details")
        self.details.setOpenExternalLinks(False)
        self.details.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        layout.addWidget(self.details, 1)
        return panel

    def _center_panel(self):
        panel = QtWidgets.QFrame()
        panel.setObjectName("plotpanel")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(20, 17, 20, 18)
        layout.setSpacing(10)
        heading = QtWidgets.QHBoxLayout()
        self.view_title = self._heading("Spectrum")
        heading.addWidget(self.view_title)
        heading.addStretch()
        heading.addWidget(QtWidgets.QLabel("View"))
        self.view_combo = QtWidgets.QComboBox()
        self.view_combo.addItems(["Current", "Original"])
        self.view_combo.currentIndexChanged.connect(self._render)
        heading.addWidget(self.view_combo)
        layout.addLayout(heading)
        sub = QtWidgets.QHBoxLayout()
        self.plot_caption = QtWidgets.QLabel("No spectrum loaded")
        self.plot_caption.setObjectName("caption")
        sub.addWidget(self.plot_caption, 1)
        self.component_combo = QtWidgets.QComboBox()
        self.component_combo.setMinimumWidth(150)
        self.component_combo.setToolTip("Choose a signed quadrature component to display")
        self.component_combo.currentIndexChanged.connect(self._render)
        sub.addWidget(self.component_combo)
        layout.addLayout(sub)
        pg.setConfigOptions(antialias=True, background=PAPER, foreground=INK)
        self.plot = pg.PlotWidget()
        self.plot.setObjectName("plot")
        self.plot.showGrid(x=True, y=True, alpha=0.16)
        self.plot.getPlotItem().getViewBox().setMouseEnabled(x=True, y=True)
        layout.addWidget(self.plot, 1)
        self.plot_note = QtWidgets.QLabel("Wheel to zoom; drag to pan. Intensities retain their sign.")
        self.plot_note.setObjectName("caption")
        layout.addWidget(self.plot_note)
        return panel

    def _right_panel(self):
        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("controlscroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setMinimumWidth(270)
        panel = QtWidgets.QFrame()
        panel.setObjectName("controlpanel")
        scroll.setWidget(panel)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(19, 20, 19, 18)
        layout.setSpacing(10)
        layout.addWidget(self._heading("Process"))
        tip = QtWidgets.QLabel("Every run starts from the original data. The recipe never accumulates on a previous result.")
        tip.setObjectName("caption")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        layout.addWidget(QtWidgets.QLabel("Axis"))
        self.axis_combo = QtWidgets.QComboBox()
        self.axis_combo.setToolTip("Select one spectral axis or all eligible spectral axes")
        self.axis_combo.currentIndexChanged.connect(self._update_recipe)
        layout.addWidget(self.axis_combo)
        layout.addWidget(QtWidgets.QLabel("Line broadening (Hz)"))
        self.lb_spin = QtWidgets.QDoubleSpinBox()
        self.lb_spin.setRange(0, 100000)
        self.lb_spin.setDecimals(3)
        self.lb_spin.setSingleStep(0.5)
        self.lb_spin.setValue(3.0)
        self.lb_spin.valueChanged.connect(self._update_recipe)
        layout.addWidget(self.lb_spin)
        layout.addWidget(QtWidgets.QLabel("Zero fill"))
        self.zf_combo = QtWidgets.QComboBox()
        self.zf_combo.addItems(["1× original", "2× original", "4× original"])
        self.zf_combo.setCurrentIndex(1)
        self.zf_combo.currentIndexChanged.connect(self._update_recipe)
        layout.addWidget(self.zf_combo)
        self.fft_check = QtWidgets.QCheckBox("Fourier transform time axes")
        self.fft_check.setChecked(True)
        self.fft_check.stateChanged.connect(self._update_recipe)
        layout.addWidget(self.fft_check)
        layout.addWidget(QtWidgets.QLabel("Zero-order phase (°)"))
        self.p0_spin = QtWidgets.QDoubleSpinBox()
        self.p0_spin.setRange(-3600, 3600)
        self.p0_spin.setDecimals(2)
        self.p0_spin.valueChanged.connect(self._update_recipe)
        layout.addWidget(self.p0_spin)
        layout.addWidget(QtWidgets.QLabel("First-order phase (°)"))
        self.p1_spin = QtWidgets.QDoubleSpinBox()
        self.p1_spin.setRange(-3600, 3600)
        self.p1_spin.setDecimals(2)
        self.p1_spin.valueChanged.connect(self._update_recipe)
        layout.addWidget(self.p1_spin)
        layout.addWidget(QtWidgets.QLabel("Compute with"))
        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.addItem("Native C++ / PocketFFT", "native")
        self.backend_combo.addItem("NumPy (experimental)", "numpy")
        self.backend_combo.currentIndexChanged.connect(self._update_recipe)
        layout.addWidget(self.backend_combo)
        self.recipe_label = QtWidgets.QLabel("No recipe")
        self.recipe_label.setObjectName("recipe")
        self.recipe_label.setWordWrap(True)
        layout.addWidget(self.recipe_label)
        self.plan_label = QtWidgets.QLabel("")
        self.plan_label.setObjectName("caption")
        self.plan_label.setWordWrap(True)
        layout.addWidget(self.plan_label)
        row = QtWidgets.QHBoxLayout()
        self.preview_button = QtWidgets.QPushButton("Preview")
        self.preview_button.clicked.connect(lambda: self._process_async(preview=True))
        self.process_button = QtWidgets.QPushButton("Process")
        self.process_button.setObjectName("primary")
        self.process_button.clicked.connect(lambda: self._process_async(preview=False))
        row.addWidget(self.preview_button)
        row.addWidget(self.process_button)
        layout.addLayout(row)
        self.reset_button = QtWidgets.QPushButton("Reset to original")
        self.reset_button.clicked.connect(self.reset)
        layout.addWidget(self.reset_button)
        layout.addSpacing(15)
        layout.addWidget(self._heading("Export"))
        self.export_combo = QtWidgets.QComboBox()
        self.export_combo.addItem("NMRNext native · all channels", "native")
        self.export_combo.addItem("NMRPipe · component 0", "nmrpipe")
        self.export_combo.addItem("UCSF · component 0 · 2D", "ucsf")
        layout.addWidget(self.export_combo)
        self.export_button = QtWidgets.QPushButton("Save current result…")
        self.export_button.setToolTip("Preview is not exported. NMRPipe and UCSF write component 0 only.")
        self.export_button.clicked.connect(self._export_dialog)
        layout.addWidget(self.export_button)
        self.recipe_button = QtWidgets.QPushButton("Save recipe and provenance…")
        self.recipe_button.clicked.connect(self._save_recipe_dialog)
        layout.addWidget(self.recipe_button)
        layout.addStretch()
        return scroll

    @staticmethod
    def _heading(text):
        label = QtWidgets.QLabel(text)
        label.setObjectName("heading")
        return label

    def _style(self):
        self.setStyleSheet(f"""
            QWidget {{ color: {INK}; font-family: 'Segoe UI', 'Arial'; font-size: 10pt; }}
            QMainWindow, QFrame#sidepanel, QFrame#controlpanel, QScrollArea#controlscroll {{ background: {SURFACE}; }}
            QFrame#topbar {{ background: {INK}; border-bottom: 2px solid {ACCENT}; }}
            QLabel#brand {{ color: white; font-size: 19pt; font-weight: 700; }}
            QLabel#descriptor {{ color: #b9cfdf; font-size: 10pt; padding-left: 15px; }}
            QLabel#datasetTitle {{ color: #d9e9f2; font-size: 10pt; }}
            QFrame#plotpanel {{ background: {PAPER}; border-left: 1px solid {BORDER}; border-right: 1px solid {BORDER}; }}
            QLabel#heading {{ font-size: 13pt; font-weight: 650; }}
            QLabel#caption {{ color: {MUTED}; font-size: 9pt; }}
            QLabel#recipe {{ color: {INK}; background: #e8f0f6; border-left: 3px solid {ACCENT}; padding: 9px; }}
            QLabel#status {{ background: #e8f0f6; border-top: 1px solid {BORDER}; padding: 7px 18px; }}
            QTextBrowser#details {{ background: transparent; color: {INK}; }}
            QPushButton, QComboBox, QDoubleSpinBox {{ background: white; border: 1px solid #bacbd7; border-radius: 4px; min-height: 30px; padding: 2px 8px; }}
            QPushButton:hover {{ border-color: {ACCENT}; background: #eef7fd; }}
            QPushButton#primary {{ background: {ACCENT}; color: white; border-color: {ACCENT}; font-weight: 650; }}
            QPushButton#primary:hover {{ background: #0e568a; }}
            QPushButton:disabled {{ color: #8899a5; background: #edf1f4; }}
            QComboBox:focus, QDoubleSpinBox:focus, QPushButton:focus, QCheckBox:focus {{ border: 2px solid {ACCENT}; }}
            QSplitter::handle {{ background: {BORDER}; width: 2px; }}
        """)

    def _set_status(self, text, *, error=False):
        self.status.setText(text)
        self.status.setStyleSheet("color: #92353b;" if error else "")

    @QtCore.Slot(object)
    def _show_error(self, exc):
        self._set_status(human_error(exc), error=True)

    @QtCore.Slot(object)
    def _handle_completed(self, value):
        try:
            if self._on_success is not None:
                self._on_success(value)
        except Exception as exc:
            self._show_error(exc)

    def _start_job(self, func, on_success, label):
        if self._busy:
            return
        self._busy = True
        self._refresh_buttons()
        self._set_status(label)
        thread = QtCore.QThread(self)
        worker = _Worker(func)
        self._on_success = on_success
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._handle_completed)
        worker.failed.connect(self._show_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._job_finished)
        self._thread, self._worker = thread, worker
        thread.start()

    @QtCore.Slot()
    def _job_finished(self):
        if self._thread is not None:
            self._thread.wait()
            self._thread.deleteLater()
        self._thread = None
        self._worker = None
        self._on_success = None
        self._busy = False
        self._refresh_buttons()

    def closeEvent(self, event):
        if self._busy:
            self._set_status("A file or processing job is still running. Wait for it to finish before closing.")
            event.ignore()
            return
        super().closeEvent(event)

    def _open_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open NMR spectrum", "", "NMR spectra (*.nmrh5 *.h5 *.ft1 *.ft2 *.ucsf);;All files (*)")
        if path:
            self._start_job(lambda: read_dataset(path),
                            lambda ds: self.load_dataset_now(ds, Path(path).name), "Opening spectrum…")

    def _open_bruker(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose Bruker acquisition directory")
        if path:
            self._start_job(lambda: read_dataset(path, format="bruker-raw-1d"),
                            lambda ds: self.load_dataset_now(ds, Path(path).name), "Opening Bruker FID…")

    def load_dataset_now(self, dataset, name="Dataset"):
        """Install a validated source. Used by dialogs, demos and offscreen tests."""
        from .model import Dataset
        if not isinstance(dataset, Dataset):
            raise NMRNextError("INVALID_DATASET", "Open requires a validated Dataset")
        previous = (self.source, self.current, self.preview, self.source_name,
                    self.last_operations, self.last_plan, self.last_backend,
                    self.dataset_title.text(), self.view_combo.currentIndex(),
                    self.axis_combo.currentIndex(), self.component_combo.currentIndex(),
                    self.status.text(), self.status.styleSheet())
        self._suspend_preview_invalidation = True
        try:
            self.source = dataset
            self.current = dataset
            self.preview = None
            self.source_name = name
            self.last_operations = []
            self.last_plan = None
            self.last_backend = None
            self.dataset_title.setText(name)
            self.view_combo.blockSignals(True)
            self.view_combo.setCurrentIndex(0)
            self.view_combo.blockSignals(False)
            self._refresh()
            self._set_status(f"Loaded {name}. Original data is preserved.")
        except Exception:
            (self.source, self.current, self.preview, self.source_name,
             self.last_operations, self.last_plan, self.last_backend,
             title, view_index, axis_index, component_index,
             status, status_style) = previous
            self.dataset_title.setText(title)
            self.view_combo.blockSignals(True)
            self.view_combo.setCurrentIndex(view_index)
            self.view_combo.blockSignals(False)
            self._refresh()
            self.axis_combo.setCurrentIndex(axis_index)
            self.component_combo.setCurrentIndex(component_index)
            self.status.setText(status)
            self.status.setStyleSheet(status_style)
            raise
        finally:
            self._suspend_preview_invalidation = False

    def reset(self):
        if self.source is None:
            return
        self.current = self.source
        self.preview = None
        self.last_operations = []
        self.last_plan = None
        self.last_backend = None
        self._render()
        self._refresh_buttons()
        self._set_status("Reset to original data. The source has not changed.")

    def _selected_axes(self):
        if self.source is None:
            return []
        axis_id = self.axis_combo.currentData()
        if axis_id is None:
            return [a for a in self.source.axes if a.role == "spectral"]
        return [self.source.axis(axis_id)]

    def build_recipe(self):
        if self.source is None:
            return []
        ops = []
        factor = (1, 2, 4)[self.zf_combo.currentIndex()]
        for axis in self._selected_axes():
            if axis.domain == "time":
                if self.lb_spin.value() > 0:
                    ops.append({"op": "window", "axis": axis.id, "kind": "exponential",
                                "lb_hz": self.lb_spin.value(), "first_point_scale": 1.0})
                if factor > 1:
                    ops.append({"op": "zero_fill", "axis": axis.id, "size": axis.size * factor})
                if self.fft_check.isChecked():
                    ops.append({"op": "fft", "axis": axis.id})
            if (self.p0_spin.value() or self.p1_spin.value()) and axis.id in self.source.complex_axes:
                ops.append({"op": "phase", "axis": axis.id, "p0": self.p0_spin.value(),
                            "p1": self.p1_spin.value(), "pivot": 0.0})
        return ops

    def _update_recipe(self):
        if self.source is None:
            self.recipe_label.setText("Open data to assemble a recipe.")
            self.plan_label.setText("")
            return
        if self.preview is not None and not self._suspend_preview_invalidation:
            self.preview = None
            self._set_status("Processing settings changed. Preview cleared; run Preview again to see these settings.")
            self._render()
        ops = self.build_recipe()
        if not ops:
            self.recipe_label.setText("No operations · current data can still be inspected and saved.")
        else:
            names = []
            for op in ops:
                name = {"window": "Window", "zero_fill": "Zero fill", "fft": "Fourier transform", "phase": "Phase"}[op["op"]]
                names.append(f"{name} ({op['axis']})")
            self.recipe_label.setText(" → ".join(names))
        if any(a.domain == "time" and a.id not in self.source.complex_axes for a in self._selected_axes()) and self.fft_check.isChecked():
            self.plan_label.setText("Fourier transform requires complex components on each selected time axis.")
        elif any(a.domain == "frequency" and a.id not in self.source.complex_axes for a in self._selected_axes()):
            self.plan_label.setText("Processed real data has no imaginary channel. Phase is unavailable; view and export remain available.")
        elif _digital_filter_info(self.source.metadata)[0] == "uncorrected_or_unknown":
            self.plan_label.setText("Digital-filter correction is unresolved. Fourier transform will be refused until a validated correction workflow is available.")
        elif _digital_filter_info(self.source.metadata)[1]:
            self.plan_label.setText("Import details are opaque. Check acquisition metadata before transforming; unsupported filter records may be refused.")
        else:
            self.plan_label.setText("All processing uses the original source and the selected backend.")
        self._refresh_buttons()

    def process_now(self, *, preview=False):
        """Run the public engine synchronously, suitable for direct controller tests."""
        if self.source is None:
            raise NMRNextError("NO_DATASET", "Open a dataset first")
        ops = self.build_recipe()
        backend = self.backend_combo.currentData()
        plan = plan_operations(self.source, ops, backend=backend)
        result = run_operations(self.source, ops, backend=backend)
        if preview:
            self.preview = result
            self._set_status("Complete recipe preview. Process to make it the current result.")
        else:
            self.current = result
            self.preview = None
            self.last_operations = ops
            self.last_plan = plan
            self.last_backend = backend
            self._set_status(f"Processed {len(ops)} operation{'s' if len(ops) != 1 else ''} with {backend}." if ops
                             else "No processing operations selected; current data is unchanged.")
        self.view_combo.setCurrentIndex(0)
        self._render()
        self._refresh_buttons()
        return result

    def _process_async(self, *, preview):
        if self.source is None or self._busy:
            return
        ops = self.build_recipe()
        backend = self.backend_combo.currentData()
        source = self.source
        def work():
            plan = plan_operations(source, ops, backend=backend)
            return run_operations(source, ops, backend=backend), plan
        def done(value):
            result, plan = value
            if preview:
                self.preview = result
                self._set_status("Complete recipe preview. Process to make it the current result.")
            else:
                self.current = result
                self.preview = None
                self.last_operations = ops
                self.last_plan = plan
                self.last_backend = backend
                self._set_status(f"Processed {len(ops)} operation{'s' if len(ops) != 1 else ''} with {backend}." if ops
                                 else "No processing operations selected; current data is unchanged.")
            self.view_combo.setCurrentIndex(0)
            self._render()
            self._refresh_buttons()
        self._start_job(work, done, "Computing spectrum…")

    def _display_dataset(self):
        if self.source is None:
            return None
        if self.view_combo.currentIndex() == 1:
            return self.source
        return self.preview or self.current

    def _refresh(self):
        self.axis_combo.blockSignals(True)
        self.axis_combo.clear()
        self.axis_combo.addItem("All spectral axes", None)
        if self.source is not None:
            for axis in self.source.axes:
                if axis.role == "spectral":
                    self.axis_combo.addItem(f"{axis.label or axis.id} ({axis.id})", axis.id)
        self.axis_combo.blockSignals(False)
        self._update_details()
        self._update_recipe()
        self._refresh_buttons()
        self._render()

    def _refresh_buttons(self):
        has = self.source is not None
        for button in (self.open_button, self.bruker_button, self.demo1_button, self.demo2_button,
                       self.process_button, self.preview_button, self.reset_button,
                       self.export_button, self.recipe_button):
            button.setEnabled(not self._busy and (has or button in (
                self.open_button, self.bruker_button, self.demo1_button, self.demo2_button)))
        selected = self._selected_axes() if has else []
        has_time = any(a.domain == "time" for a in selected)
        has_complex = any(a.id in self.source.complex_axes for a in selected) if has else False
        for control in (self.axis_combo, self.backend_combo, self.export_combo):
            control.setEnabled(has and not self._busy)
        for control in (self.lb_spin, self.zf_combo, self.fft_check):
            control.setEnabled(has_time and not self._busy)
        for control in (self.p0_spin, self.p1_spin):
            control.setEnabled(has_complex and not self._busy)
        self.process_button.setEnabled(has and bool(self.build_recipe()) and not self._busy)
        self.preview_button.setEnabled(has and bool(self.build_recipe()) and not self._busy)
        self.export_button.setEnabled(has and self.preview is None and not self._busy)
        self.recipe_button.setEnabled(has and self.preview is None and not self._busy)

    def _update_details(self):
        if self.source is None:
            self.details.setHtml("<p>Open NMRNext native data, a supported processed spectrum, or a Bruker 1D FID.</p><p>Try a synthetic example to explore the workflow.</p>")
            return
        d = self.source
        rows = [f"<p><b>{len(d.axes)}D · {d.data.dtype.name} · {d.data.shape[0]} component(s)</b></p>"]
        for axis in d.axes:
            nucleus = axis.nucleus or axis.label or axis.id
            rows.append(f"<p><b>{nucleus} ({axis.id})</b><br>{axis.size:,} points · {axis.domain} · {axis.encoding}<br>"
                        f"{axis.start:.5g} {axis.unit} + {axis.step:.5g} {axis.unit}/point</p>")
        state, opaque_filter = _digital_filter_info(d.metadata)
        if state:
            rows.append(f"<p><b>Digital filter</b><br>{state}</p>")
        elif opaque_filter:
            rows.append("<p><b>Import details</b><br>Opaque record retained</p>")
        rows.append(f"<p><b>History</b><br>{len(d.provenance)} source entries</p>")
        self.details.setHtml("".join(rows))

    def _axis_values(self, dataset, axis):
        if axis.domain == "frequency" and axis.observation_mhz and axis.reference_ppm is not None:
            return dataset.coordinates(axis.id, unit="ppm"), "ppm"
        if axis.domain == "time":
            return dataset.coordinates(axis.id) * 1000.0, "ms"
        return dataset.coordinates(axis.id), axis.unit

    def _render(self):
        dataset = self._display_dataset()
        self.plot.clear()
        if dataset is None:
            self.plot_caption.setText("No spectrum loaded")
            self.plot_note.setText("Open a spectrum or create a synthetic example.")
            return
        old = self.component_combo.currentIndex()
        self.component_combo.blockSignals(True)
        self.component_combo.clear()
        for i in range(dataset.data.shape[0]):
            self.component_combo.addItem(component_name(i, dataset.complex_axes), i)
        self.component_combo.setCurrentIndex(min(max(old, 0), dataset.data.shape[0]-1))
        self.component_combo.blockSignals(False)
        index = self.component_combo.currentData()
        data = dataset.data[index]
        label = "Original" if self.view_combo.currentIndex() == 1 else ("Preview" if self.preview is not None else "Current")
        self.plot_caption.setText(f"{label} · {component_name(index, dataset.complex_axes)} · {data.shape}")
        self.plot.getPlotItem().invertX(False)
        self.plot.getPlotItem().invertY(False)
        if data.ndim == 1:
            axis = dataset.axes[0]
            x, unit = self._axis_values(dataset, axis)
            stride = max(1, int(np.ceil(len(x) / 20000)))
            self.plot.plot(x[::stride], data[::stride], pen=pg.mkPen(ACCENT, width=1.6))
            self.plot.addLine(y=0, pen=pg.mkPen("#8ca0b0", width=1))
            self.plot.setLabel("bottom", f"{axis.nucleus or axis.label or axis.id} ({unit})")
            self.plot.setLabel("left", "Signed intensity")
            self.plot.getPlotItem().invertX(axis.domain == "frequency")
            sampled = "Display sampled; full data retained." if stride > 1 else "Complete data shown."
        elif data.ndim == 2:
            ya, xa = dataset.axes
            x, xu = self._axis_values(dataset, xa)
            y, yu = self._axis_values(dataset, ya)
            sy = max(1, int(np.ceil(len(y) / 1024)))
            sx = max(1, int(np.ceil(len(x) / 1024)))
            image = data[::sy, ::sx]
            xv, yv = x[::sx], y[::sy]
            if xv[0] > xv[-1]:
                image = np.flip(image, axis=1)
            if yv[0] > yv[-1]:
                image = np.flip(image, axis=0)
            vmax = float(np.percentile(np.abs(image), 99.5))
            vmax = vmax if vmax > 0 else 1.0
            colors = np.array([[34, 76, 144, 255], [136, 177, 220, 255],
                               [255, 255, 255, 255], [241, 159, 135, 255],
                               [159, 46, 55, 255]], dtype=np.uint8)
            lut = pg.ColorMap(np.linspace(0, 1, 5), colors).getLookupTable(nPts=256)
            item = pg.ImageItem(image, axisOrder="row-major")
            item.setLookupTable(lut)
            item.setLevels((-vmax, vmax))
            xstep = abs(float(x[1]-x[0])) if len(x)>1 else 1.0
            ystep = abs(float(y[1]-y[0])) if len(y)>1 else 1.0
            item.setRect(QtCore.QRectF(min(xv[0],xv[-1])-xstep/2,
                                       min(yv[0],yv[-1])-ystep/2,
                                       abs(float(xv[-1]-xv[0]))+xstep,
                                       abs(float(yv[-1]-yv[0]))+ystep))
            self.plot.addItem(item)
            self.plot.setLabel("bottom", f"{xa.nucleus or xa.label or xa.id} ({xu})")
            self.plot.setLabel("left", f"{ya.nucleus or ya.label or ya.id} ({yu})")
            self.plot.getPlotItem().invertX(xa.domain == "frequency")
            self.plot.getPlotItem().invertY(ya.domain == "frequency")
            sampled = "Display sampled; full data retained." if sx > 1 or sy > 1 else "Complete data shown."
        else:
            self.plot_caption.setText("Plot supports 1D and 2D data; metadata and export remain available.")
            sampled = ""
        self.plot_note.setText(f"{sampled} Wheel to zoom; drag to pan. Intensities retain their sign.")
        self.plot.autoRange()

    def preflight_export(self, fmt):
        if self.current is None:
            raise NMRNextError("NO_DATASET", "Open a dataset first")
        return export_preflight(self.current, fmt)

    def export_now(self, path, fmt, *, allow_losses=()):
        """Save with the actual codec, retaining its no-overwrite default."""
        if self.current is None:
            raise NMRNextError("NO_DATASET", "Open a dataset first")
        return write_dataset(self.current, path, format=fmt, allow_losses=allow_losses, overwrite=False)

    def _export_dialog(self):
        if self.current is None:
            return
        fmt = self.export_combo.currentData()
        suffix = {"native": ".nmrh5", "nmrpipe": ".ft1" if len(self.current.axes)==1 else ".ft2",
                  "ucsf": ".ucsf"}[fmt]
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save spectrum", f"spectrum{suffix}",
                                                         f"Spectrum (*{suffix})")
        if not path:
            return
        try:
            report = self.preflight_export(fmt)
            if report["errors"]:
                raise NMRNextError("UNSUPPORTED_PROFILE", "This dataset cannot be represented in that format",
                                   details=report)
            if Path(path).exists():
                raise NMRNextError("OUTPUT_EXISTS", f"Output already exists: {path}")
        except Exception as exc:
            self._show_error(exc)
            return
        losses = ()
        if report["losses"]:
            dialog = ExportLossDialog(report, self)
            if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return
            losses = dialog.accepted_losses()
        self._start_job(lambda: self.export_now(path, fmt, allow_losses=losses),
                        lambda result: self._set_status(f"Saved {result['path']} · {result['format']}."),
                        "Saving spectrum…")

    def recipe_record(self):
        if self.source is None:
            raise NMRNextError("NO_DATASET", "Open a dataset first")
        ops = self.last_operations if self.current is not self.source else []
        return {"schema_version": "1", "source_name": self.source_name,
                "operations": ops, "backend": self.last_backend,
                "plan": self.last_plan, "result_provenance": self.current.provenance,
                "source_axes": [a.to_dict() for a in self.source.axes],
                "result_axes": [a.to_dict() for a in self.current.axes]}

    def _save_recipe_dialog(self):
        if self.source is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save recipe and provenance", "nmrnext-recipe.json",
                                                         "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "x", encoding="utf-8") as file:
                json.dump(self.recipe_record(), file, indent=2, allow_nan=False)
                file.write("\n")
            self._set_status(f"Saved recipe and provenance to {path}.")
        except Exception as exc:
            self._show_error(exc)


def main(argv=None):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("NMRNext")
    app.setStyle("Fusion")
    window = Workbench()
    window.show()
    try:
        return app.exec()
    finally:
        # An explicit app.quit() can bypass the window's busy close guard.
        # Finish its worker before destroying the QThread's parent widget.
        if window._thread is not None:
            window._thread.quit()
            window._thread.wait()
        # Dispose this window while its Python graph and QApplication are
        # still alive; PySide's interpreter-shutdown order is not safe here.
        # The event loop has stopped, so deliver this deferred delete directly.
        window.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(window, QtCore.QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    raise SystemExit(main())
