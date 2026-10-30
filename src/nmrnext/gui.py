"""PySide6 scientific workbench backed by the public NMRNext API.

Run with ``python -m nmrnext.gui``. Qt is deliberately optional for headless use.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from .demo import generate_demo
from .errors import NMRNextError
from .io import export_preflight, read_dataset, write_dataset
from ._execution_cache import RecipeCache
from .planning import plan_operations
from .workflow_bundle import export_workflow_bundle


INK = "#14263a"
MUTED = "#596b7d"
ACCENT = "#1769a3"
SURFACE = "#f4f8fb"
PAPER = "#ffffff"
BORDER = "#d6e1e9"
_font_loaded = False


def _save_json_report(path, report):
    """Publish complete JSON without replacing a destination; return cleanup advice."""
    payload = json.dumps(report, indent=2, allow_nan=False) + '\n'
    target = Path(path)
    fd, name = tempfile.mkstemp(prefix='.spinflow-json-', suffix='.tmp', dir=target.parent)
    temporary = Path(name)
    published = False
    failure = None
    warning = ''
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            if stream.write(payload) != len(payload):
                raise OSError('Incomplete JSON write')
            stream.flush()
            os.fsync(stream.fileno())
        # Windows rename refuses existing paths; POSIX link provides no-replace.
        if os.name == 'nt':
            os.rename(temporary, target)
        else:
            os.link(temporary, target)
        published = True
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as exc:
            warning = f'Temporary cleanup failed for {temporary}: {exc}'
            if failure is not None:
                failure.add_note(warning)
            elif not published:
                raise
    return warning


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


class StepDialog(QtWidgets.QDialog):
    """Edit common fields directly while retaining the complete operation JSON."""

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit {operation['op']} step")
        self.setMinimumWidth(520)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(f"{operation['op']} · edit its values, or expand the complete operation JSON."))
        form = QtWidgets.QFormLayout()
        self.fields = {}
        for key, value in operation.items():
            if key in ('op', 'version'):
                continue
            shown = (', '.join(value) if key == 'order' and isinstance(value, list) else
                     json.dumps(value, ensure_ascii=False)
                     if isinstance(value, (list, dict, bool)) or value is None else str(value))
            field = QtWidgets.QLineEdit(shown)
            if key == 'order':
                field.setToolTip('Enter axis IDs separated by commas, such as a0, a1. Zero-based positions such as 0, 1 also work.')
            field.setAccessibleName(key)
            form.addRow(key.replace('_', ' ').capitalize(), field)
            self.fields[key] = field
        layout.addLayout(form)
        self.advanced_check = QtWidgets.QCheckBox('Edit complete operation JSON')
        layout.addWidget(self.advanced_check)
        self.advanced = QtWidgets.QPlainTextEdit(json.dumps(operation, indent=2, ensure_ascii=False))
        self.advanced.setAccessibleName('Complete operation JSON')
        self.advanced.setVisible(False)
        self.advanced_check.toggled.connect(self.advanced.setVisible)
        layout.addWidget(self.advanced)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                             QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._original = operation

    def operation(self):
        if self.advanced_check.isChecked():
            value = json.loads(self.advanced.toPlainText())
        else:
            value = deepcopy(self._original)
            for key, field in self.fields.items():
                text = field.text().strip()
                original = self._original[key]
                if key == 'order':
                    parts = [part.strip() for part in text.split(',')]
                    if not parts or any(not part for part in parts):
                        raise NMRNextError('INVALID_AXIS_ORDER', 'Enter every axis once, separated by commas')
                    axes = [axis.id for axis in self.parent().source.axes] if getattr(self.parent(), 'source', None) is not None else []
                    if axes and all(part.isdecimal() and part not in axes for part in parts):
                        indices = [int(part) for part in parts]
                        if any(index >= len(axes) for index in indices):
                            raise NMRNextError('INVALID_AXIS_ORDER', 'Axis position is outside the source axes')
                        parts = [axes[index] for index in indices]
                    value[key] = parts
                elif isinstance(original, str):
                    value[key] = text
                elif isinstance(original, bool):
                    value[key] = json.loads(text.lower())
                else:
                    value[key] = json.loads(text)
        if not isinstance(value, dict):
            raise NMRNextError('INVALID_OPERATION', 'A step must be a JSON object')
        return value


class Workbench(QtWidgets.QMainWindow):
    """Thin GUI: recipes and data flow through the same APIs as the CLI."""

    def __init__(self):
        super().__init__()
        _ensure_font()
        self.setWindowTitle("SpinFlow · Spectrum workbench")
        self.resize(1320, 840)
        self.setMinimumSize(950, 620)
        self.source = None
        self.current = None
        self.preview = None
        self.source_name = "No dataset"
        self.last_operations = []
        self.last_plan = None
        self.last_backend = None
        self.last_memory_bytes = None
        self._thread = None
        self._worker = None
        self._on_success = None
        self._busy = False
        self._suspend_preview_invalidation = False
        self._axis_settings = {}
        self.analysis_reports = {}
        self.selected_regions = []
        self._recipe_mode = 'quick'
        self._ordered_operations = []
        self._pending_memory_bytes = None
        self._rendered_dataset = None
        self._rendered_axis_units = {}
        self._state_generation = 0
        self._recipe_cache = RecipeCache()
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
        title = QtWidgets.QLabel("SpinFlow")
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
        self.bruker_button = QtWidgets.QPushButton("Open acquisition folder…")
        self.bruker_button.clicked.connect(self._open_bruker)
        self.bruker_button.setToolTip("Open a supported acquisition folder; the format is detected from its contents.")
        layout.addWidget(self.bruker_button)
        self.open_session_button = QtWidgets.QPushButton("Open saved session…")
        self.open_session_button.clicked.connect(self._open_session_dialog)
        layout.addWidget(self.open_session_button)
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
        self.component_combo.currentIndexChanged.connect(self._component_changed)
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

        self.load_recipe_button = QtWidgets.QPushButton('Load ordered recipe…')
        self.load_recipe_button.setToolTip('Load a strict schema-1 CLI recipe and its source. Every supported operation and parameter is retained.')
        self.load_recipe_button.clicked.connect(self._load_recipe_dialog)
        layout.addWidget(self.load_recipe_button)
        self.ordered_label = QtWidgets.QLabel('Quick controls build a new recipe from the original data.')
        self.ordered_label.setObjectName('caption')
        self.ordered_label.setWordWrap(True)
        layout.addWidget(self.ordered_label)
        self.ordered_steps = QtWidgets.QListWidget()
        self.ordered_steps.setAccessibleName('Ordered processing steps')
        self.ordered_steps.setMinimumHeight(165)
        self.ordered_steps.itemDoubleClicked.connect(lambda _: self._edit_step_dialog())
        layout.addWidget(self.ordered_steps)
        recipe_row = QtWidgets.QHBoxLayout()
        self.add_step_button = QtWidgets.QPushButton('Add')
        self.edit_step_button = QtWidgets.QPushButton('Edit')
        self.remove_step_button = QtWidgets.QPushButton('Remove')
        self.up_step_button = QtWidgets.QPushButton('↑')
        self.down_step_button = QtWidgets.QPushButton('↓')
        self.add_step_button.clicked.connect(self._add_step_dialog)
        self.edit_step_button.clicked.connect(self._edit_step_dialog)
        self.remove_step_button.clicked.connect(lambda: self._change_step('remove'))
        self.up_step_button.clicked.connect(lambda: self._change_step('up'))
        self.down_step_button.clicked.connect(lambda: self._change_step('down'))
        for button in (self.add_step_button, self.edit_step_button, self.remove_step_button,
                       self.up_step_button, self.down_step_button):
            recipe_row.addWidget(button)
        layout.addLayout(recipe_row)
        self.quick_recipe_button = QtWidgets.QPushButton('Start a new quick recipe')
        self.quick_recipe_button.setToolTip('Explicitly replace the pending ordered recipe with the quick controls. The applied result remains until Process.')
        self.quick_recipe_button.clicked.connect(self._start_quick_recipe)
        layout.addWidget(self.quick_recipe_button)
        layout.addWidget(self._heading('Quick controls'))

        self.filter_check = QtWidgets.QCheckBox('Correct Bruker digital filter')
        self.filter_check.setToolTip('Explicit first operation. Only the supported GRPDLY acquisition profile is accepted; unknown profiles are refused.')
        self.filter_check.stateChanged.connect(self._processing_changed)
        layout.addWidget(self.filter_check)

        layout.addWidget(QtWidgets.QLabel("Axis"))
        self.axis_combo = QtWidgets.QComboBox()
        self.axis_combo.setToolTip("Choose which axis settings to edit. Process always runs the complete recipe for every axis.")
        self.axis_combo.currentIndexChanged.connect(self._axis_changed)
        layout.addWidget(self.axis_combo)
        self.axis_settings_label = QtWidgets.QLabel()
        self.axis_settings_label.setObjectName("caption")
        self.axis_settings_label.setWordWrap(True)
        layout.addWidget(self.axis_settings_label)
        layout.addWidget(QtWidgets.QLabel("Line broadening (Hz)"))
        self.lb_spin = QtWidgets.QDoubleSpinBox()
        self.lb_spin.setRange(0, 100000)
        self.lb_spin.setDecimals(12)
        self.lb_spin.setSingleStep(0.5)
        self.lb_spin.setValue(3.0)
        self.lb_spin.valueChanged.connect(lambda: self._setting_changed('lb'))
        layout.addWidget(self.lb_spin)
        layout.addWidget(QtWidgets.QLabel("Window first-point scale"))
        self.first_point_spin = QtWidgets.QDoubleSpinBox()
        self.first_point_spin.setRange(0.0, 1.0)
        self.first_point_spin.setDecimals(3)
        self.first_point_spin.setSingleStep(0.5)
        self.first_point_spin.setValue(1.0)
        self.first_point_spin.setToolTip("Scale the first acquired point when windowing; public 2D recipes use 0.5 on both axes.")
        self.first_point_spin.valueChanged.connect(lambda: self._setting_changed('first_point'))
        layout.addWidget(self.first_point_spin)
        layout.addWidget(QtWidgets.QLabel("Zero fill"))
        self.zf_combo = QtWidgets.QComboBox()
        self.zf_combo.addItems(["1× original", "2× original", "4× original", "8× original"])
        self.zf_combo.setCurrentIndex(1)
        self.zf_combo.currentIndexChanged.connect(lambda: self._setting_changed('zf'))
        layout.addWidget(self.zf_combo)
        self.fft_check = QtWidgets.QCheckBox("Fourier transform time axes")
        self.fft_check.setChecked(True)
        self.fft_check.stateChanged.connect(lambda: self._setting_changed('fft'))
        layout.addWidget(self.fft_check)
        layout.addWidget(QtWidgets.QLabel("Zero-order phase (°)"))
        self.p0_spin = QtWidgets.QDoubleSpinBox()
        self.p0_spin.setRange(-3600, 3600)
        self.p0_spin.setDecimals(2)
        self.p0_spin.valueChanged.connect(lambda: self._setting_changed('p0'))
        layout.addWidget(self.p0_spin)
        layout.addWidget(QtWidgets.QLabel("First-order phase (°)"))
        self.p1_spin = QtWidgets.QDoubleSpinBox()
        self.p1_spin.setRange(-3600, 3600)
        self.p1_spin.setDecimals(2)
        self.p1_spin.valueChanged.connect(lambda: self._setting_changed('p1'))
        layout.addWidget(self.p1_spin)
        layout.addWidget(QtWidgets.QLabel("Phase pivot (zero-based point)"))
        self.pivot_spin = QtWidgets.QDoubleSpinBox()
        self.pivot_spin.setRange(-1e9, 1e9)
        self.pivot_spin.setDecimals(3)
        self.pivot_spin.setToolTip("Phase = p0 + p1 × (point − pivot) / point count; before cropping.")
        self.pivot_spin.valueChanged.connect(lambda: self._setting_changed('pivot'))
        layout.addWidget(self.pivot_spin)
        self.crop_check = QtWidgets.QCheckBox("Crop frequency region")
        self.crop_check.setToolTip("Available for an already processed frequency axis. Original source is retained.")
        self.crop_check.stateChanged.connect(lambda: self._setting_changed('crop'))
        layout.addWidget(self.crop_check)
        layout.addWidget(QtWidgets.QLabel("Crop start (point, included)"))
        self.crop_start_spin = QtWidgets.QSpinBox()
        self.crop_start_spin.setRange(0, 2147483647)
        self.crop_start_spin.valueChanged.connect(lambda: self._setting_changed('start'))
        layout.addWidget(self.crop_start_spin)
        layout.addWidget(QtWidgets.QLabel("Crop stop (point, excluded)"))
        self.crop_stop_spin = QtWidgets.QSpinBox()
        self.crop_stop_spin.setRange(1, 2147483647)
        self.crop_stop_spin.valueChanged.connect(lambda: self._setting_changed('stop'))
        layout.addWidget(self.crop_stop_spin)
        crop_tip = QtWidgets.QLabel("Zero-based source points; 0 ≤ start < stop ≤ axis size. Calibration is retained.")
        crop_tip.setObjectName("caption")
        crop_tip.setWordWrap(True)
        layout.addWidget(crop_tip)
        self.reference_check = QtWidgets.QCheckBox('Apply explicit frequency reference')
        self.reference_check.setToolTip('Enter a known shift and frequency anchor for each axis. A zero shown in an unknown field is only an editable placeholder, not an inferred reference.')
        self.reference_check.stateChanged.connect(lambda: self._setting_changed('reference'))
        layout.addWidget(self.reference_check)
        self.reference_reverse_check = QtWidgets.QCheckBox('Reverse frequency orientation when referencing')
        self.reference_reverse_check.setToolTip('Use only when your acquisition convention requires reflecting the mathematical frequency grid. Samples are not reversed or conjugated.')
        self.reference_reverse_check.stateChanged.connect(lambda: self._setting_changed('reference_reverse'))
        layout.addWidget(self.reference_reverse_check)
        for key, attr, label in [('reference_ppm', 'reference_ppm_spin', 'Anchor shift (ppm)'),
                                 ('reference_hz', 'reference_hz_spin', 'Anchor frequency (Hz)'),
                                 ('reference_mhz', 'reference_mhz_spin', 'Observation frequency (MHz)')]:
            layout.addWidget(QtWidgets.QLabel(label))
            field = QtWidgets.QDoubleSpinBox()
            field.setRange(-1e9 if key != 'reference_mhz' else 0, 1e9)
            field.setDecimals(12)
            field.valueChanged.connect(lambda value, k=key: self._setting_changed(k))
            setattr(self, attr, field)
            layout.addWidget(field)
        layout.addWidget(QtWidgets.QLabel("Compute with"))
        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.addItem("Native C++ / PocketFFT", "native")
        self.backend_combo.addItem("NumPy (experimental)", "numpy")
        self.backend_combo.currentIndexChanged.connect(self._backend_changed)
        layout.addWidget(self.backend_combo)
        layout.addWidget(QtWidgets.QLabel("Processing working-array budget (MiB)"))
        self.memory_spin = QtWidgets.QSpinBox()
        self.memory_spin.setRange(1, 1048576)
        self.memory_spin.setValue(256)
        self.memory_spin.setToolTip("Budget for estimated working arrays, not a hard process-memory limit. Preview computes the complete recipe. Opening has a separate 1 GiB payload cap.")
        self.memory_spin.valueChanged.connect(self._memory_changed)
        layout.addWidget(self.memory_spin)
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
        self.reset_button = QtWidgets.QPushButton("Reset to original")
        self.reset_button.clicked.connect(self.reset)
        layout.addSpacing(15)
        layout.addWidget(self._heading("Export"))
        export_tip = QtWidgets.QLabel("Exports use the applied current result. Pending control edits are excluded.")
        export_tip.setObjectName("caption")
        export_tip.setWordWrap(True)
        layout.addWidget(export_tip)
        self.export_combo = QtWidgets.QComboBox()
        self.export_combo.addItem("SpinFlow native · all channels", "native")
        self.export_combo.addItem("NMRPipe · component 0", "nmrpipe")
        self.export_combo.addItem("UCSF · component 0 · 2D", "ucsf")
        layout.addWidget(self.export_combo)
        self.export_button = QtWidgets.QPushButton("Save current result…")
        self.export_button.setToolTip("Preview is not exported. NMRPipe and UCSF write component 0 only.")
        self.export_button.clicked.connect(self._export_dialog)
        layout.addWidget(self.export_button)
        self.workflow_button = QtWidgets.QPushButton("Save runnable workflow…")
        self.workflow_button.setToolTip("Save a new folder with the original source snapshot and CLI recipe for the applied current result. Unapplied controls are excluded.")
        self.workflow_button.clicked.connect(self._save_workflow_dialog)
        layout.addWidget(self.workflow_button)
        self.session_button = QtWidgets.QPushButton('Save applied session…')
        self.session_button.setToolTip('Save source, exact applied recipe, selected analysis region and reports in a new folder for reopening here.')
        self.session_button.clicked.connect(self._save_session_dialog)
        layout.addWidget(self.session_button)
        self.recipe_button = QtWidgets.QPushButton("Save audit report…")
        self.recipe_button.setToolTip("Applied operations, plan and provenance for inspection. This report is not a CLI recipe.")
        self.recipe_button.clicked.connect(self._save_recipe_dialog)
        layout.addWidget(self.recipe_button)
        layout.addStretch()
        # Keep actions reachable while the axis controls scroll on small screens.
        container = QtWidgets.QFrame()
        container.setObjectName("controlpanel")
        controls = QtWidgets.QVBoxLayout(container)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(scroll, 1)
        actions = QtWidgets.QVBoxLayout()
        actions.setContentsMargins(19, 8, 19, 12)
        actions.addLayout(row)
        actions.addWidget(self.reset_button)
        controls.addLayout(actions)
        tabs = QtWidgets.QTabWidget()
        tabs.addTab(container, 'Processing')
        tabs.addTab(self._analysis_panel(), 'Analysis / figures')
        return tabs

    def _analysis_panel(self):
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        tip = QtWidgets.QLabel('Analyze the applied current result. Peaks and integrals use component 0; automatic phase fits every quadrature channel. Pending edits and previews are excluded. Inspect all automatic results.')
        tip.setWordWrap(True)
        layout.addWidget(tip)
        self.autophase_button = QtWidgets.QPushButton('Apply automatic phase (1D / 2D)…')
        self.autophase_button.setToolTip('Positive isolated Lorentzian lines; 2D requires all four quadratures and fits both axes together. Mixed-sign, crowded or underdetermined spectra may be refused. Adds explicit phase operations to the applied recipe, using centered point pivots.')
        self.autophase_button.clicked.connect(lambda: self._analysis_job('autophase'))
        layout.addWidget(self.autophase_button)
        self.first_order_check = QtWidgets.QCheckBox('Estimate first-order phase')
        self.first_order_check.setToolTip('Requires sufficiently separated usable peaks; underdetermined fits are refused.')
        layout.addWidget(self.first_order_check)
        self.phase_mask_path = self._mask_control(layout, 'Phase anchor regions (optional JSON)')
        form = QtWidgets.QFormLayout()
        self.sign_combo = QtWidgets.QComboBox()
        self.sign_combo.addItems(['positive', 'negative', 'both'])
        self.sign_combo.setCurrentText('both')
        form.addRow('Peak sign', self.sign_combo)
        self.snr_spin = QtWidgets.QDoubleSpinBox()
        self.snr_spin.setRange(0.1, 10000)
        self.snr_spin.setValue(5)
        form.addRow('Noise threshold (σ)', self.snr_spin)
        self.threshold_check = QtWidgets.QCheckBox('Use absolute threshold')
        form.addRow(self.threshold_check)
        self.threshold_spin = QtWidgets.QDoubleSpinBox()
        self.threshold_spin.setRange(0, 1e15)
        self.threshold_spin.setDecimals(6)
        self.threshold_spin.setValue(1)
        form.addRow('Absolute threshold', self.threshold_spin)
        layout.addLayout(form)
        self.noise_mask_path = self._mask_control(layout, 'Noise reference regions (optional JSON)')
        self.peaks_button = QtWidgets.QPushButton('Pick peaks')
        self.peaks_button.clicked.connect(lambda: self._analysis_job('peaks'))
        layout.addWidget(self.peaks_button)
        self.analysis_region_check = QtWidgets.QCheckBox('Analyze selected region of applied spectrum')
        self.analysis_region_check.setToolTip('Select a bounded region after processing, using complete-spectrum coordinates. Required for spectra above the global analysis limit.')
        layout.addWidget(self.analysis_region_check)
        self.use_plot_region_button = QtWidgets.QPushButton('Use visible plot area as region')
        self.use_plot_region_button.clicked.connect(self._use_plot_region)
        layout.addWidget(self.use_plot_region_button)
        tip = QtWidgets.QLabel('Integration bounds include both endpoints (unlike crop). Enter one region below; no concentration inference or deconvolution.')
        tip.setWordWrap(True)
        layout.addWidget(tip)
        self.region_unit = QtWidgets.QComboBox()
        self.region_unit.addItems(['points', 'Hz', 'ppm'])
        self.region_unit.currentIndexChanged.connect(self._refresh_analysis_bounds)
        layout.addWidget(self.region_unit)
        self.region_form = QtWidgets.QFormLayout()
        self.region_bounds = {}
        layout.addLayout(self.region_form)
        self.select_region_button = QtWidgets.QPushButton('Select entered analysis region')
        self.select_region_button.clicked.connect(self._select_entered_region)
        layout.addWidget(self.select_region_button)
        self.baseline_combo = QtWidgets.QComboBox()
        self.baseline_combo.addItems(['none', 'constant', 'edge_linear', 'reference_linear'])
        self.baseline_combo.setToolTip('Baseline policy; inspect the report for method and flags. Edge-linear is limited to supported profiles.')
        layout.addWidget(self.baseline_combo)
        self.baseline_mask_path = self._mask_control(layout, 'Baseline reference regions (optional JSON)')
        layout.addWidget(QtWidgets.QLabel('Constant baseline intensity (only for constant)'))
        self.baseline_value_spin = QtWidgets.QDoubleSpinBox()
        self.baseline_value_spin.setRange(-1e15, 1e15)
        self.baseline_value_spin.setDecimals(6)
        layout.addWidget(self.baseline_value_spin)
        self.integrate_button = QtWidgets.QPushButton('Integrate entered region')
        self.integrate_button.clicked.connect(lambda: self._analysis_job('integrate'))
        layout.addWidget(self.integrate_button)
        self.analysis_summary = QtWidgets.QLabel('No analysis yet.')
        self.analysis_summary.setWordWrap(True)
        layout.addWidget(self.analysis_summary)
        self.analysis_table = QtWidgets.QTableWidget(0, 6)
        self.analysis_table.setHorizontalHeaderLabels(['Kind', 'ID', 'Coordinates / bounds', 'Value', 'Units', 'Flags'])
        self.analysis_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.analysis_table.setMinimumHeight(150)
        layout.addWidget(self.analysis_table, 1)
        self.analysis_export_button = QtWidgets.QPushButton('Save analysis JSON…')
        self.analysis_export_button.clicked.connect(self._save_analysis_dialog)
        layout.addWidget(self.analysis_export_button)
        self.figure_button = QtWidgets.QPushButton('Save scientific figure…')
        self.figure_button.clicked.connect(self._save_figure_dialog)
        self.figure_button.setToolTip('Optional plot extra. SVG/PDF vector curves and signed contours, or 300 dpi PNG. Source and analysis digest links retained.')
        layout.addWidget(self.figure_button)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        self.analysis_panel = panel
        return scroll

    def _mask_control(self, layout, label):
        layout.addWidget(QtWidgets.QLabel(label))
        row = QtWidgets.QHBoxLayout()
        field = QtWidgets.QLineEdit()
        field.setPlaceholderText('No regions selected')
        field.setToolTip('JSON regions use inclusive point indices keyed by the current axis IDs. Clear the path to remove the selection.')
        button = QtWidgets.QPushButton('Choose…')
        def choose():
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, label, '', 'JSON (*.json);;All files (*)')
            if path:
                field.setText(path)
        button.clicked.connect(choose)
        row.addWidget(field, 1)
        row.addWidget(button)
        layout.addLayout(row)
        return field

    def _refresh_analysis_bounds(self):
        while self.region_form.rowCount():
            self.region_form.removeRow(0)
        self.region_bounds = {}
        if self.current is None:
            return
        for axis in self.current.axes[:2]:
            row = QtWidgets.QWidget()
            box = QtWidgets.QHBoxLayout(row)
            box.setContentsMargins(0, 0, 0, 0)
            fields = [QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox()]
            try:
                values = (np.arange(axis.size) if self.region_unit.currentText() == 'points'
                          else self.current.coordinates(axis.id, self.region_unit.currentText()))
                limits = (float(min(values)), float(max(values)))
            except NMRNextError:
                limits = (0, 0)
            for field, value in zip(fields, limits):
                field.setRange(-1e12, 1e12)
                field.setDecimals(6)
                field.setMinimumWidth(60)
                field.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
                field.setValue(value)
                box.addWidget(field)
            self.region_bounds[axis.id] = fields
            self.region_form.addRow(axis.nucleus or axis.id, row)

    def _clear_analysis(self):
        self.analysis_reports = {}
        self.selected_regions = []
        self.analysis_region_check.setChecked(False)
        self._display_analysis()
        self._refresh_analysis_bounds()

    def _entered_region(self):
        return {'bounds': {axis: [field.value() for field in fields]
                           for axis, fields in self.region_bounds.items()},
                'unit': self.region_unit.currentText()}

    def _select_entered_region(self):
        if self.current is None or self.preview is not None:
            self._show_error(NMRNextError('ANALYSIS_STATE', 'Apply processing before selecting an analysis region'))
            return
        try:
            from .regions import select_analysis_region
            selection = self._entered_region()
            _, mapping = select_analysis_region(self.current, selection['bounds'],
                                                unit=selection['unit'])
            self.selected_regions = [selection]
            self.analysis_region_check.setChecked(True)
            self._state_generation += 1
            self._set_status('Selected calibrated region on the applied spectrum; full-spectrum coordinates are retained.')
        except Exception as exc:
            self._show_error(exc)

    def _restore_region_controls(self):
        if not self.selected_regions:
            self.analysis_region_check.setChecked(False)
            return
        selection = self.selected_regions[0]
        self.region_unit.setCurrentText(selection['unit'])
        for axis, pair in selection['bounds'].items():
            if axis in self.region_bounds:
                for field, value in zip(self.region_bounds[axis], pair):
                    field.setValue(value)
        self.analysis_region_check.setChecked(True)

    def _use_plot_region(self):
        if (self.current is None or self.preview is not None or self.view_combo.currentIndex() != 0
                or self._display_dataset() is not self.current or self._rendered_dataset is not self.current):
            self._show_error(NMRNextError('ANALYSIS_STATE',
                                          'Show the applied Current spectrum before selecting its visible region'))
            return
        dataset = self.current
        ranges = self.plot.getPlotItem().getViewBox().viewRange()
        axes = dataset.axes
        visible = {axes[-1].id: ranges[0]}
        if len(axes) == 2:
            visible[axes[0].id] = ranges[1]
        self.region_unit.setCurrentText('points')
        self._refresh_analysis_bounds()
        for axis in axes:
            if axis.id not in visible:
                continue
            values, _ = self._axis_values(dataset, axis)
            low, high = sorted(visible[axis.id])
            inside = np.flatnonzero((values >= low) & (values <= high))
            if len(inside):
                first, last = int(inside[0]), int(inside[-1])
            else:
                center = (low + high) / 2
                first = last = int(np.argmin(np.abs(values - center)))
            self.region_bounds[axis.id][0].setValue(first)
            self.region_bounds[axis.id][1].setValue(last)
        self._select_entered_region()

    def _display_analysis(self):
        self.analysis_table.setRowCount(0)
        summaries = []
        if 'autophase' in self.analysis_reports:
            summaries.append('Automatic phase is an explicit editable step in the applied and pending recipe.')
        for kind, report in self.analysis_reports.items():
            summaries.append(f"{kind}: " + ', '.join(report.get('flags', [])))
            if kind == 'autophase':
                operations = report.get('operations') or [report['operation']]
                summaries.extend(f"{op['axis']}: p0={op['p0']:.4g}°, p1={op['p1']:.4g}° per N points; pivot={op['pivot']:.4g} points"
                                 for op in operations)
                if 'phase_gauge' in report:
                    summaries.append(report['phase_gauge']['canonical_branch'])
                summaries.append(f"Fit normalized RMS={report['objective']['normalized_rms']:.4g}; inspect excluded candidates and flags in saved JSON.")
            if kind == 'peaks':
                summaries.append(f"Noise σ={report.get('noise', {}).get('sigma')}; threshold={report.get('threshold')}")
            records = report.get('peaks', []) or report.get('regions', [])
            for item in records:
                values = [kind, item['id'], json.dumps(item.get('coordinates', item.get('bounds', {}))),
                          str(item.get('intensity', item.get('integral', ''))),
                          item.get('integral_unit', 'intensity'), ', '.join(item.get('flags', []))]
                row = self.analysis_table.rowCount()
                self.analysis_table.insertRow(row)
                for col, value in enumerate(values):
                    cell = QtWidgets.QTableWidgetItem(value)
                    cell.setToolTip(value)
                    self.analysis_table.setItem(row, col, cell)
        self.analysis_table.resizeColumnsToContents()
        self.analysis_summary.setText('\n'.join(summaries) or 'No analysis yet.')

    def _analysis_request(self, kind):
        from .analysis import autophase, integrate_regions, pick_peaks
        from .cli import _point_mask_selection, _attach_mask_selection
        if self.current is None or self.preview is not None:
            raise NMRNextError('ANALYSIS_STATE', 'Process the current result before analysis; previews are excluded')
        dataset = self.current
        if kind == 'autophase':
            mask, selection = _point_mask_selection(dataset, self.phase_mask_path.text().strip() or None)
            memory = self.last_memory_bytes if self.last_memory_bytes is not None else self.memory_spin.value() * 1024**2
            options = {'backend': self.last_backend or self.backend_combo.currentData(),
                       'first_order': self.first_order_check.isChecked(),
                       'inclusion_mask': mask, 'memory_bytes': memory}
            def work():
                return _attach_mask_selection(autophase(dataset, **options), 'phase', selection)
            work.phase_memory_bytes = memory
            return work
        if self.analysis_region_check.isChecked():
            from .regions import select_analysis_region, pick_peaks_in_region, integrate_regions_in_region
            selection = self._entered_region()
            bounds, unit = selection['bounds'], selection['unit']
            region_dataset, mapping = select_analysis_region(dataset, bounds, unit=unit)
            if kind == 'peaks':
                if self.noise_mask_path.text().strip():
                    raise NMRNextError('REGION_MASK_UNSUPPORTED', 'Clear the full-spectrum noise mask before analyzing a selected region')
                options = {'channel': 0, 'sign': self.sign_combo.currentText(), 'snr': self.snr_spin.value(),
                           'threshold': self.threshold_spin.value() if self.threshold_check.isChecked() else None}
                work = lambda: pick_peaks_in_region(dataset, bounds, unit=unit, **options)
            else:
                if self.baseline_mask_path.text().strip():
                    raise NMRNextError('REGION_MASK_UNSUPPORTED', 'Clear the full-spectrum baseline mask before analyzing a selected region')
                baseline = self.baseline_combo.currentText()
                value = self.baseline_value_spin.value() if baseline == 'constant' else None
                local = {'id': 'region-1', 'unit': 'points',
                         'bounds': {axis.id: [0, region_dataset.axis(axis.id).size - 1]
                                    for axis in region_dataset.axes}}
                work = lambda: integrate_regions_in_region(
                    dataset, bounds, [local], unit=unit, channel=0, baseline=baseline,
                    baseline_value=value)
            work.region_selection = selection
            return work
        if kind == 'peaks':
            mask, selection = _point_mask_selection(dataset, self.noise_mask_path.text().strip() or None)
            options = {'channel': 0, 'sign': self.sign_combo.currentText(), 'snr': self.snr_spin.value(),
                       'threshold': self.threshold_spin.value() if self.threshold_check.isChecked() else None,
                       'noise_mask': mask}
            return lambda: _attach_mask_selection(pick_peaks(dataset, **options), 'noise', selection)
        region = {'id': 'region-1', 'unit': self.region_unit.currentText(),
                  'bounds': {axis: [field.value() for field in fields] for axis, fields in self.region_bounds.items()}}
        baseline = self.baseline_combo.currentText()
        value = self.baseline_value_spin.value() if baseline == 'constant' else None
        mask, selection = _point_mask_selection(dataset, self.baseline_mask_path.text().strip() or None)
        return lambda: _attach_mask_selection(integrate_regions(dataset, [region], channel=0, baseline=baseline, baseline_value=value, baseline_mask=mask), 'baseline', selection)

    def _accept_analysis(self, kind, result, *, phase_memory_bytes=None, region_selection=None):
        if kind == 'autophase':
            dataset, report = result
            corrections = report.get('operations') or [report['operation']]
            operations = self.last_operations + corrections
            backend = self.last_backend or self.backend_combo.currentData()
            plan = plan_operations(self.source, operations, backend=backend, memory_bytes=phase_memory_bytes)
            self.current = dataset
            self.last_operations, self.last_plan, self.last_backend = operations, plan, backend
            self.last_memory_bytes = phase_memory_bytes
            self._clear_analysis()
            self.analysis_reports['autophase'] = report
            self._install_ordered(operations, backend=backend, memory_bytes=phase_memory_bytes)
            self._render()
            self._update_recipe()
        else:
            self.analysis_reports[kind] = result
            if region_selection is not None:
                self.selected_regions = [deepcopy(region_selection)]
        self._display_analysis()
        self._refresh_buttons()
        self._set_status('Analysis completed on the applied current result. Inspect methods and flags before use.')
        self._state_generation += 1

    def analyze_now(self, kind):
        work = self._analysis_request(kind)
        result = work()
        self._accept_analysis(kind, result, phase_memory_bytes=getattr(work, 'phase_memory_bytes', None),
                              region_selection=getattr(work, 'region_selection', None))
        return result

    def _analysis_job(self, kind):
        try:
            work = self._analysis_request(kind)
        except Exception as exc:
            self._show_error(exc)
            return
        phase_memory = getattr(work, 'phase_memory_bytes', None)
        selection = getattr(work, 'region_selection', None)
        self._start_job(work, lambda result: self._accept_analysis(kind, result, phase_memory_bytes=phase_memory,
                                                                  region_selection=selection), 'Analyzing applied spectrum…')

    def _current_analysis(self):
        from .analysis import dataset_digest
        digest = dataset_digest(self.current)
        reports = self.analysis_reports.copy()
        if any((r.get('result_dataset_digest') if k == 'autophase' else
                r.get('parent_region', {}).get('parent_dataset_digest', r.get('dataset_digest'))) != digest
               for k, r in reports.items()):
            raise NMRNextError('STALE_ANALYSIS', 'Analysis belongs to a different current dataset')
        return reports

    def _save_analysis_dialog(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Save applied analysis tables', 'spinflow-analysis.json', 'JSON (*.json)')
        if not path:
            return
        try:
            reports = self._current_analysis()
            warning = _save_json_report(path, reports)
            self._set_status(f'Saved analysis tables to {path}. {warning}')
        except Exception as exc:
            self._show_error(exc)

    def _save_figure_dialog(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Save scientific figure', 'spinflow-spectrum.svg', 'Figures (*.svg *.pdf *.png)')
        if not path:
            return
        try:
            from .figures import export_figure
            dataset, reports = self.current, self._current_analysis()
        except Exception as exc:
            self._show_error(exc)
            return
        self._start_job(lambda: export_figure(dataset, path, analysis=reports, title=self.source_name),
                        lambda report: self._set_status(f"Saved {report['format']} figure to {report['path']}. Save analysis JSON for methods and flags."),
                        'Rendering scientific figure…')

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
            QPushButton, QComboBox, QDoubleSpinBox, QSpinBox {{ background: white; border: 1px solid #bacbd7; border-radius: 4px; min-height: 30px; padding: 2px 8px; }}
            QPushButton:hover {{ border-color: {ACCENT}; background: #eef7fd; }}
            QPushButton#primary {{ background: {ACCENT}; color: white; border-color: {ACCENT}; font-weight: 650; }}
            QPushButton#primary:hover {{ background: #0e568a; }}
            QPushButton:disabled {{ color: #8899a5; background: #edf1f4; }}
            QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus, QPushButton:focus, QCheckBox:focus {{ border: 2px solid {ACCENT}; }}
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

    def _read_with_budget(self, path, memory_bytes, *, format=None):
        from .io import describe_dataset
        header = describe_dataset(path, format=format)
        limit = min(memory_bytes, 1024**3)
        if header.nbytes > limit:
            raise NMRNextError('MEMORY_BUDGET', f'Input requires {header.nbytes} bytes; selected read limit is {limit} bytes. Increase the processing budget or use a smaller input (opening is capped at 1 GiB).', details={'required_bytes': header.nbytes, 'max_bytes': limit})
        return read_dataset(path, format=format, max_bytes=limit, expected_header=header)

    def _open_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open NMR spectrum", "", "NMR spectra (*.nmrh5 *.h5 *.ft1 *.ft2 *.ucsf);;All files (*)")
        if path:
            memory = self.memory_spin.value() * 1024**2
            ticket = self._job_ticket()
            self._start_job(lambda: self._read_with_budget(path, memory),
                            lambda ds: self._accept_if_fresh(ticket, lambda: self.load_dataset_now(ds, Path(path).name)),
                            "Opening spectrum…")

    def _open_bruker(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose acquisition folder")
        if path:
            memory = self.memory_spin.value() * 1024**2
            ticket = self._job_ticket()
            self._start_job(lambda: self._read_with_budget(path, memory),
                            lambda ds: self._accept_if_fresh(ticket, lambda: self.load_dataset_now(ds, Path(path).name)),
                            "Opening acquisition…")

    def _job_ticket(self):
        return (self._state_generation, self.source, self.current, self.preview)

    def _accept_if_fresh(self, ticket, action):
        generation, source, current, preview = ticket
        if (generation != self._state_generation or source is not self.source
                or current is not self.current or preview is not self.preview):
            raise NMRNextError('STALE_JOB', 'The workbench changed while this job was running; its result was discarded')
        return action()

    def _load_recipe_record(self, path):
        from .cli import _load_recipe
        args = SimpleNamespace(recipe=str(path), memory=None, block_memory=None,
                               backend=None, threads=None)
        recipe, source_path, _, memory, backend, threads = _load_recipe(args)
        if threads != 1:
            raise NMRNextError('UNSUPPORTED_THREADS', 'The workbench runs recipes with one processing thread')
        source = self._read_with_budget(source_path, memory, format=recipe.get('input_format'))
        plan_operations(source, recipe['operations'], backend=backend, threads=threads,
                        memory_bytes=memory)
        return source, recipe, source_path, memory, backend

    def load_recipe_now(self, path):
        """Validate source and the whole CLI recipe before changing the workbench."""
        source, recipe, source_path, memory, backend = self._load_recipe_record(path)
        self.load_dataset_now(source, source_path.name)
        self._install_ordered(recipe['operations'], backend=backend, memory_bytes=memory)
        self._set_status(f"Loaded {len(recipe['operations'])} ordered steps from {path}. Process to apply them.")
        return recipe

    def _load_recipe_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Open runnable workflow', '', 'JSON (*.json)')
        if not path:
            return
        ticket = self._job_ticket()
        self._start_job(lambda: self._load_recipe_record(path),
                        lambda value: self._accept_if_fresh(ticket, lambda: self._accept_loaded_recipe(value, path)),
                        'Opening recipe and source…')

    def _accept_loaded_recipe(self, value, path):
        source, recipe, source_path, memory, backend = value
        self.load_dataset_now(source, source_path.name)
        self._install_ordered(recipe['operations'], backend=backend, memory_bytes=memory)
        self._set_status(f"Loaded {len(recipe['operations'])} ordered steps from {path}. Process to apply them.")

    def _install_ordered(self, operations, *, backend=None, memory_bytes=None):
        self._recipe_mode = 'ordered'
        self._ordered_operations = deepcopy(operations)
        self._pending_memory_bytes = memory_bytes
        if backend is not None:
            index = self.backend_combo.findData(backend)
            if index < 0:
                raise NMRNextError('UNSUPPORTED_BACKEND', f'No GUI backend for {backend}')
            self.backend_combo.blockSignals(True)
            self.backend_combo.setCurrentIndex(index)
            self.backend_combo.blockSignals(False)
        if memory_bytes is not None:
            self.memory_spin.blockSignals(True)
            self.memory_spin.setValue(max(1, (memory_bytes + 1024**2 - 1) // 1024**2))
            self.memory_spin.blockSignals(False)
        self._refresh_ordered_steps()
        self._update_recipe()
        self._state_generation += 1

    def set_ordered_recipe_now(self, operations):
        """Replace only the pending recipe after complete metadata and budget validation."""
        if self.source is None:
            raise NMRNextError('NO_DATASET', 'Open a dataset first')
        memory = self._pending_memory_bytes or self.memory_spin.value() * 1024**2
        plan_operations(self.source, operations, backend=self.backend_combo.currentData(),
                        memory_bytes=memory)
        self._install_ordered(operations, memory_bytes=memory)

    def _refresh_ordered_steps(self):
        self.ordered_steps.clear()
        operations = self.build_recipe()
        for index, op in enumerate(operations):
            axis = op.get('axis', ', '.join(op.get('order', [])))
            extras = ', '.join(f'{key}={value}' for key, value in op.items()
                               if key not in ('op', 'axis', 'order'))
            self.ordered_steps.addItem(f"{index + 1}. {op['op']} · {axis}" +
                                       (f' · {extras}' if extras else ''))
        self.ordered_label.setText(
            'Ordered recipe: every step and parameter is retained. Quick controls are paused.'
            if self._recipe_mode == 'ordered' else
            'Quick controls build a new recipe from the original data. Select a step to edit the complete sequence.')

    def _start_quick_recipe(self):
        if self._recipe_mode == 'ordered' and self._ordered_operations:
            choice = QtWidgets.QMessageBox.question(
                self, 'Replace pending recipe?',
                'Start a new quick recipe from the original source? The applied result stays until Process.')
            if choice != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        self._recipe_mode = 'quick'
        self._ordered_operations = []
        self._pending_memory_bytes = None
        self._refresh_ordered_steps()
        self._update_recipe()
        self._state_generation += 1

    def _edit_step_dialog(self):
        if self.source is None:
            return
        index = self.ordered_steps.currentRow()
        ops = self.build_recipe()
        if not 0 <= index < len(ops):
            self._set_status('Select a processing step to edit.')
            return
        dialog = StepDialog(ops[index], self)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        try:
            ops[index] = dialog.operation()
            self.set_ordered_recipe_now(ops)
            self.ordered_steps.setCurrentRow(index)
            self._set_status(f'Updated step {index + 1}; the applied result is unchanged until Process.')
        except Exception as exc:
            self._show_error(exc)

    def _add_step_dialog(self):
        if self.source is None:
            return
        from .operations import _FIELDS
        names = sorted(_FIELDS)
        name, accepted = QtWidgets.QInputDialog.getItem(self, 'Add processing step', 'Operation', names, 0, False)
        if not accepted:
            return
        axis = self.source.axes[0].id
        initial = {'op': name, 'order': [a.id for a in self.source.axes]} if name == 'transpose' else {'op': name, 'axis': axis}
        if name == 'zero_fill':
            initial['size'] = self.source.axis(axis).size
        elif name == 'crop':
            initial.update(start=0, stop=self.source.axis(axis).size)
        elif name == 'reference':
            initial.update(reference_ppm=0.0, reference_hz=0.0,
                           observation_mhz=self.source.axis(axis).observation_mhz or 1.0)
        dialog = StepDialog(initial, self)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        ops = self.build_recipe()
        row = self.ordered_steps.currentRow()
        insert_at = row + 1 if row >= 0 else len(ops)
        try:
            ops.insert(insert_at, dialog.operation())
            self.set_ordered_recipe_now(ops)
            self.ordered_steps.setCurrentRow(insert_at)
            self._set_status(f'Added step {insert_at + 1}; the applied result is unchanged until Process.')
        except Exception as exc:
            self._show_error(exc)

    def _change_step(self, action):
        row = self.ordered_steps.currentRow()
        ops = self.build_recipe()
        if not 0 <= row < len(ops):
            self._set_status('Select a processing step first.')
            return
        if action == 'remove':
            ops.pop(row)
            new_row = min(row, len(ops) - 1)
        else:
            new_row = row + (-1 if action == 'up' else 1)
            if not 0 <= new_row < len(ops):
                return
            ops[row], ops[new_row] = ops[new_row], ops[row]
        try:
            self.set_ordered_recipe_now(ops)
            self.ordered_steps.setCurrentRow(new_row)
            self._set_status('Ordered steps updated; the applied result is unchanged until Process.')
        except Exception as exc:
            self._show_error(exc)

    def load_dataset_now(self, dataset, name="Dataset"):
        """Install a validated source. Used by dialogs, demos and offscreen tests."""
        from .model import Dataset
        if not isinstance(dataset, Dataset):
            raise NMRNextError("INVALID_DATASET", "Open requires a validated Dataset")
        previous_bounds = {axis: [field.value() for field in fields] for axis, fields in self.region_bounds.items()}
        previous = (self.source, self.current, self.preview, self.source_name,
                    self.last_operations, self.last_plan, self.last_backend, self.last_memory_bytes,
                    self.dataset_title.text(), self.view_combo.currentIndex(),
                    self.axis_combo.currentIndex(), self.component_combo.currentIndex(),
                    self.status.text(), self.status.styleSheet(), self._axis_settings,
                    self.analysis_reports, self.filter_check.isChecked(),
                    self._recipe_mode, self._ordered_operations, self._pending_memory_bytes,
                    self.selected_regions, self._state_generation)
        self._suspend_preview_invalidation = True
        try:
            self.source = dataset
            self.current = dataset
            self.preview = None
            self.source_name = name
            self.last_operations = []
            self.last_plan = None
            self.last_backend = None
            self.last_memory_bytes = None
            self._recipe_mode = 'quick'
            self._ordered_operations = []
            self._pending_memory_bytes = None
            self.selected_regions = []
            self._reset_axis_settings()
            self._clear_analysis()
            self.dataset_title.setText(name)
            self.view_combo.blockSignals(True)
            self.view_combo.setCurrentIndex(0)
            self.view_combo.blockSignals(False)
            self._refresh()
            self._set_status(f"Loaded {name}. Original data is preserved.")
            self._recipe_cache.clear()
            self._state_generation += 1
        except Exception:
            (self.source, self.current, self.preview, self.source_name,
             self.last_operations, self.last_plan, self.last_backend, self.last_memory_bytes,
             title, view_index, axis_index, component_index,
             status, status_style, self._axis_settings,
             self.analysis_reports, filter_checked,
             self._recipe_mode, self._ordered_operations, self._pending_memory_bytes,
             self.selected_regions, self._state_generation) = previous
            self.filter_check.blockSignals(True)
            self.filter_check.setChecked(filter_checked)
            self.filter_check.blockSignals(False)
            self._display_analysis()
            self._refresh_analysis_bounds()
            for axis, fields in self.region_bounds.items():
                for field, value in zip(fields, previous_bounds.get(axis, [])):
                    field.setValue(value)
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
        self._recipe_cache.clear()
        self.current = self.source
        self.preview = None
        self.last_operations = []
        self.last_plan = None
        self.last_backend = None
        self.last_memory_bytes = None
        self._recipe_mode = 'quick'
        self._ordered_operations = []
        self._pending_memory_bytes = None
        self.selected_regions = []
        self._reset_axis_settings()
        self._clear_analysis()
        self._refresh()
        self._set_status("Reset to original data. The source has not changed.")
        self._state_generation += 1

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
        if self._recipe_mode == 'ordered':
            return deepcopy(self._ordered_operations)
        ops = []
        if self.filter_check.isChecked():
            ops.append({'op': 'digital_filter', 'axis': self.source.axes[-1].id})
        for axis in self.source.axes:
            if axis.role != 'spectral':
                continue
            settings = self._axis_settings[axis.id]
            factor = (1, 2, 4, 8)[settings['zf']]
            if axis.domain == "time":
                if settings['lb'] > 0 or settings['first_point'] != 1.0:
                    ops.append({"op": "window", "axis": axis.id, "kind": "exponential",
                                "lb_hz": settings['lb'], "first_point_scale": settings['first_point']})
                # Correction trims direct points. "1× original" restores the
                # original acquisition grid before FFT, rather than a shorter grid.
                if factor > 1 or (self.filter_check.isChecked() and axis.id == self.source.axes[-1].id):
                    ops.append({"op": "zero_fill", "axis": axis.id, "size": axis.size * factor})
                if settings['fft']:
                    ops.append({"op": "fft", "axis": axis.id})
            if settings['reference']:
                reference = {'op': 'reference', 'axis': axis.id, 'reference_ppm': settings['reference_ppm'],
                             'reference_hz': settings['reference_hz'], 'observation_mhz': settings['reference_mhz']}
                if settings['reference_reverse']:
                    reference['frequency_scale'] = -1
                ops.append(reference)
            if (settings['p0'] or settings['p1']) and axis.id in self.source.complex_axes:
                ops.append({"op": "phase", "axis": axis.id, "p0": settings['p0'],
                            "p1": settings['p1'], "pivot": settings['pivot']})
            if axis.domain == 'frequency' and settings['crop']:
                ops.append({'op': 'crop', 'axis': axis.id, 'start': settings['start'], 'stop': settings['stop']})
        return ops

    def _reset_axis_settings(self):
        self.filter_check.blockSignals(True)
        self.filter_check.setChecked(False)
        self.filter_check.blockSignals(False)
        self._axis_settings = {
            a.id: dict(lb=3.0, first_point=1.0, zf=1, fft=True, p0=0.0, p1=0.0, pivot=0.0,
                       crop=False, start=0, stop=a.size, reference=False, reference_reverse=False,
                       reference_ppm=a.reference_ppm or 0.0, reference_hz=a.reference_hz,
                       reference_mhz=a.observation_mhz or 0.0)
            for a in self.source.axes if a.role == 'spectral'
        }

    def _setting_controls(self):
        return {'lb': self.lb_spin, 'first_point': self.first_point_spin, 'zf': self.zf_combo, 'fft': self.fft_check,
                'p0': self.p0_spin, 'p1': self.p1_spin, 'pivot': self.pivot_spin,
                'crop': self.crop_check, 'start': self.crop_start_spin, 'stop': self.crop_stop_spin,
                'reference': self.reference_check, 'reference_reverse': self.reference_reverse_check, 'reference_ppm': self.reference_ppm_spin,
                'reference_hz': self.reference_hz_spin, 'reference_mhz': self.reference_mhz_spin}

    def _eligible_setting_axes(self, key, axes):
        if key.startswith('reference'):
            return axes
        if key in ('lb', 'first_point', 'zf', 'fft'):
            return [a for a in axes if a.domain == 'time']
        if key in ('crop', 'start', 'stop'):
            return [a for a in axes if a.domain == 'frequency']
        return [a for a in axes if a.id in self.source.complex_axes]

    def _setting_changed(self, key):
        if self._recipe_mode == 'ordered':
            return
        self._discard_extra_operations()
        widget = self._setting_controls()[key]
        value = (widget.currentIndex() if key == 'zf' else widget.isChecked()
                 if key in ('fft', 'crop', 'reference', 'reference_reverse') else widget.value())
        for axis in self._eligible_setting_axes(key, self._selected_axes()):
            self._axis_settings[axis.id][key] = value
        self._update_recipe()
        self._state_generation += 1

    def _discard_extra_operations(self):
        if 'autophase' in self.analysis_reports:
            self._set_status('Processing edits discard automatic phase from the next recipe. The applied current result remains unchanged until Process.')

    def _processing_changed(self):
        if self._recipe_mode == 'ordered':
            return
        self._discard_extra_operations()
        self._update_recipe()
        self._state_generation += 1

    def _memory_changed(self):
        self._pending_memory_bytes = None
        self._update_recipe()
        self._state_generation += 1

    def _backend_changed(self):
        self._update_recipe()
        self._state_generation += 1

    def _axis_changed(self):
        selected = self._selected_axes()
        if selected:
            for key, widget in self._setting_controls().items():
                # In a mixed-domain selection show the first eligible axis.
                eligible = self._eligible_setting_axes(key, selected)
                settings = self._axis_settings[(eligible or selected)[0].id]
                widget.blockSignals(True)
                if key == 'zf':
                    widget.setCurrentIndex(settings[key])
                elif key in ('fft', 'crop', 'reference', 'reference_reverse'):
                    widget.setChecked(settings[key])
                else:
                    widget.setValue(settings[key])
                widget.blockSignals(False)
        self._update_recipe()

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
        selected = self._selected_axes()
        if self.axis_combo.currentData() is None:
            mixed = any(len({self._axis_settings[a.id][key]
                             for a in self._eligible_setting_axes(key, selected)}) > 1
                        for key in self._setting_controls())
            self.axis_settings_label.setText(
                ("Settings differ; showing the first eligible axis. " if mixed else "") +
                "All axes: each edit applies that field to every eligible axis. Process runs the complete recipe.")
        else:
            self.axis_settings_label.setText("Edits are retained for this axis. Process runs the complete recipe for all axes.")
        if not ops:
            self.recipe_label.setText("No operations · current data can still be inspected and saved.")
        else:
            names = []
            for op in ops:
                name = {"window": "Window", "zero_fill": "Zero fill", "fft": "Fourier transform", "phase": "Phase", "crop": "Crop", 'digital_filter': 'Digital-filter correction', 'reference': 'Reference',
                        'transpose': 'Transpose', 'discard_imaginary': 'Discard imaginary', 'reverse': 'Reverse'} .get(op['op'], op['op'])
                names.append(f"{name} ({op.get('axis', ', '.join(op.get('order', [])))})")
            self.recipe_label.setText(" → ".join(names))
        if self._recipe_mode == 'ordered':
            self.plan_label.setText('The complete ordered recipe runs from the original source. Every step is validated before execution.')
        elif any(a.domain == "time" and a.id not in self.source.complex_axes for a in self._selected_axes()) and self.fft_check.isChecked():
            self.plan_label.setText("Fourier transform requires complex components on each selected time axis.")
        elif any(a.domain == "frequency" and a.id not in self.source.complex_axes for a in self._selected_axes()):
            self.plan_label.setText("Processed real data has no imaginary channel. Phase is unavailable; crop, view and export remain available.")
        elif _digital_filter_info(self.source.metadata)[0] == "uncorrected_or_unknown":
            self.plan_label.setText("Digital-filter correction is unresolved. Select explicit validated correction; unsupported acquisition profiles will be refused.")
        elif _digital_filter_info(self.source.metadata)[1]:
            self.plan_label.setText("Import details are opaque. Check acquisition metadata before transforming; unsupported filter records may be refused.")
        else:
            self.plan_label.setText("All processing uses the original source and the selected backend.")
        self._refresh_ordered_steps()
        self._refresh_buttons()

    def process_now(self, *, preview=False):
        """Run the public engine synchronously, suitable for direct controller tests."""
        if self.source is None:
            raise NMRNextError("NO_DATASET", "Open a dataset first")
        ops = self.build_recipe()
        backend = self.backend_combo.currentData()
        memory = self._pending_memory_bytes or self.memory_spin.value() * 1024**2
        result, plan = self._recipe_cache.run(self.source, ops, backend=backend, memory_bytes=memory)
        if preview:
            self.preview = result
            self._set_status("Complete recipe preview. Process to make it the current result.")
        else:
            self.current = result
            self.preview = None
            self.last_operations = ops
            self.last_plan = plan
            self.last_backend = backend
            self.last_memory_bytes = memory
            self._clear_analysis()
            self._set_status(f"Processed {len(ops)} operation{'s' if len(ops) != 1 else ''} with {backend}." if ops
                             else "No processing operations selected; current data is unchanged.")
        self.view_combo.setCurrentIndex(0)
        self._render()
        self._refresh_buttons()
        self._state_generation += 1
        return result

    def _process_async(self, *, preview):
        if self.source is None or self._busy:
            return
        ops = self.build_recipe()
        backend = self.backend_combo.currentData()
        source = self.source
        memory = self._pending_memory_bytes or self.memory_spin.value() * 1024**2
        def work():
            return self._recipe_cache.run(source, ops, backend=backend, memory_bytes=memory)
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
                self.last_memory_bytes = memory
                self._clear_analysis()
                self._set_status(f"Processed {len(ops)} operation{'s' if len(ops) != 1 else ''} with {backend}." if ops
                                 else "No processing operations selected; current data is unchanged.")
            self.view_combo.setCurrentIndex(0)
            self._render()
            self._refresh_buttons()
            self._state_generation += 1
        self._start_job(work, done, "Computing spectrum…")

    def _display_dataset(self):
        if self.source is None:
            return None
        if self.view_combo.currentIndex() == 1:
            return self.source
        return self.preview or self.current

    def _component_changed(self):
        # Display selection does not change the digest-bound applied result.
        self._render()

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
        self._axis_changed()
        self._refresh_buttons()
        self._render()

    def _refresh_buttons(self):
        has = self.source is not None
        for button in (self.open_button, self.bruker_button, self.demo1_button, self.demo2_button,
                       self.open_session_button, self.load_recipe_button,
                       self.process_button, self.preview_button, self.reset_button,
                       self.export_button, self.recipe_button, self.workflow_button,
                       self.session_button):
            button.setEnabled(not self._busy and (has or button in (
                self.open_button, self.bruker_button, self.open_session_button,
                self.load_recipe_button, self.demo1_button, self.demo2_button)))
        selected = self._selected_axes() if has else []
        has_time = any(a.domain == "time" for a in selected)
        has_complex = any(a.id in self.source.complex_axes for a in selected) if has else False
        quick = self._recipe_mode == 'quick'
        ordered = self._recipe_mode == 'ordered'
        for button in (self.add_step_button, self.edit_step_button, self.remove_step_button,
                       self.up_step_button, self.down_step_button):
            button.setEnabled(has and not self._busy)
        self.quick_recipe_button.setEnabled(has and ordered and not self._busy)
        for control in (self.axis_combo, self.backend_combo, self.export_combo):
            control.setEnabled(has and not self._busy)
        for control in (self.lb_spin, self.first_point_spin, self.zf_combo, self.fft_check):
            control.setEnabled(quick and has_time and not self._busy)
        for control in (self.p0_spin, self.p1_spin, self.pivot_spin):
            control.setEnabled(quick and has_complex and not self._busy)
        has_frequency = any(a.domain == 'frequency' for a in selected)
        for control in (self.crop_check, self.crop_start_spin, self.crop_stop_spin):
            control.setEnabled(quick and has_frequency and not self._busy)
        for control in (self.reference_check, self.reference_reverse_check, self.reference_ppm_spin, self.reference_hz_spin, self.reference_mhz_spin):
            control.setEnabled(quick and bool(selected) and not self._busy)
        self.process_button.setEnabled(has and bool(self.build_recipe()) and not self._busy)
        self.preview_button.setEnabled(has and bool(self.build_recipe()) and not self._busy)
        self.export_button.setEnabled(has and self.preview is None and not self._busy)
        self.recipe_button.setEnabled(has and self.preview is None and not self._busy)
        self.workflow_button.setEnabled(has and self.preview is None and not self._busy)
        self.session_button.setEnabled(has and self.preview is None and not self._busy)
        imported = self.source.metadata.get('import') if has else None
        self.filter_check.setEnabled(quick and not self._busy and isinstance(imported, dict)
                                     and imported.get('format') in ('bruker-raw-1d', 'bruker-raw-2d'))
        can_analyze = (has and self.preview is None and not self._busy
                       and len(self.current.axes) in (1, 2)
                       and all(a.domain == 'frequency' for a in self.current.axes))
        self.analysis_panel.setEnabled(can_analyze)
        phase_ready = (can_analyze and all(a.role == 'spectral' for a in self.current.axes)
                       and set(self.current.complex_axes) == {a.id for a in self.current.axes})
        self.autophase_button.setEnabled(phase_ready)
        self.analysis_export_button.setEnabled(can_analyze and bool(self.analysis_reports))
        self._refresh_visible_region_button()

    def _refresh_visible_region_button(self):
        can_use = (self.current is not None and self.preview is None
                   and self.view_combo.currentIndex() == 0
                   and self._display_dataset() is self.current
                   and self._rendered_dataset is self.current
                   and len(self.current.axes) in (1, 2)
                   and all(axis.domain == 'frequency' for axis in self.current.axes)
                   and not self._busy)
        self.use_plot_region_button.setEnabled(can_use)

    def _update_details(self):
        if self.source is None:
            self.details.setHtml("<p>Open SpinFlow native data, a supported processed spectrum, or a supported acquisition folder.</p><p>Try a synthetic example to explore the workflow.</p>")
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
        self._rendered_dataset = None
        self._rendered_axis_units = {}
        self.plot.clear()
        if dataset is None:
            self.plot_caption.setText("No spectrum loaded")
            self.plot_note.setText("Open a spectrum or create a synthetic example.")
            self._refresh_visible_region_button()
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
            self._rendered_axis_units[axis.id] = unit
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
            self._rendered_axis_units = {xa.id: xu, ya.id: yu}
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
        unreferenced = any(a.domain == 'frequency' and a.reference_ppm is None for a in dataset.axes)
        reference_note = (' Chemical-shift reference is unknown: axes show mathematical frequency offsets in Hz. '
                          'Confirm the orientation and enter a known anchor for ppm.' if unreferenced else '')
        self.plot_note.setText(f"{sampled} Wheel to zoom; drag to pan. Intensities retain their sign.{reference_note}")
        self.plot.autoRange()
        self._rendered_dataset = dataset
        self._refresh_visible_region_button()

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
        return {"schema_version": "1", "report_type": "spinflow-applied-workflow-audit", "source_name": self.source_name,
                "operations": ops, "backend": self.last_backend, "memory_bytes": self.last_memory_bytes,
                "plan": self.last_plan, "result_provenance": self.current.provenance,
                "source_axes": [a.to_dict() for a in self.source.axes],
                "result_axes": [a.to_dict() for a in self.current.axes]}

    def _save_recipe_dialog(self):
        if self.source is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save audit report (not a CLI recipe)", "spinflow-audit.json",
                                                         "JSON (*.json)")
        if not path:
            return
        try:
            warning = _save_json_report(path, self.recipe_record())
            self._set_status(f"Saved audit report to {path}. Use Save runnable workflow for CLI replay. {warning}")
        except Exception as exc:
            self._show_error(exc)

    def export_workflow_now(self, directory):
        """Export the applied current result, excluding preview and pending edits."""
        if self.source is None:
            raise NMRNextError('NO_DATASET', 'Open a dataset first')
        return export_workflow_bundle(self.source, self.last_operations, directory,
                                      backend=self.last_backend or 'native',
                                      memory_bytes=self.last_memory_bytes or self.memory_spin.value() * 1024**2)

    def _save_workflow_dialog(self):
        if self.source is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, 'Save applied workflow to a NEW folder', 'spinflow-workflow',
            options=QtWidgets.QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return
        self._start_job(lambda: self.export_workflow_now(path),
                        lambda recipe: self._set_status(
                            f'Saved applied workflow: {recipe}. Run spinflow run "{recipe}" to reproduce it.'),
                        'Saving applied workflow and source snapshot…')

    def save_session_now(self, directory):
        """Save only the applied result and its digest-linked analysis state."""
        if self.source is None or self.preview is not None:
            raise NMRNextError('SESSION_STATE', 'Open and apply a dataset before saving a session')
        from .session import save_session
        reports = self._current_analysis()
        return save_session(self.source, self.last_operations, directory,
                            backend=self.last_backend or 'native',
                            memory_bytes=self.last_memory_bytes or self.memory_spin.value() * 1024**2,
                            regions=deepcopy(self.selected_regions), reports=reports,
                            result=self.current, source_name=self.source_name)

    def _save_session_dialog(self):
        if self.source is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, 'Save applied session to a NEW folder', 'spinflow-session',
            options=QtWidgets.QFileDialog.Option.DontConfirmOverwrite)
        if path:
            self._start_job(lambda: self.save_session_now(path),
                            lambda saved: self._set_status(f'Saved applied session: {saved}.'),
                            'Saving source, recipe and analysis…')

    def open_session_now(self, directory):
        """Replay and verify the complete saved session before changing the view."""
        from .session import load_session
        record = load_session(directory, max_bytes=min(self.memory_spin.value() * 1024**2, 1024**3))
        self._accept_loaded_session(record)
        return record

    def _open_session_dialog(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, 'Open saved session folder')
        if not path:
            return
        memory = min(self.memory_spin.value() * 1024**2, 1024**3)
        ticket = self._job_ticket()
        from .session import load_session
        self._start_job(lambda: load_session(path, max_bytes=memory),
                        lambda record: self._accept_if_fresh(ticket, lambda: self._accept_loaded_session(record)),
                        'Checking and replaying saved session…')

    def _accept_loaded_session(self, record):
        source, result = record['source'], record['current']
        operations = record['operations']
        backend, memory = record['backend'], record['memory_bytes']
        # These checks precede the first GUI mutation even if an alternate
        # session loader returned an incomplete record.
        if result is None:
            raise NMRNextError('INVALID_SESSION', 'Saved session has no replayed applied result')
        plan = plan_operations(source, operations, backend=backend, memory_bytes=memory)
        checkpoint = self._capture_view_checkpoint()
        try:
            # Loading a source normally clears its cache. Keep the previous
            # cache object untouched until the entire new view succeeds.
            self._recipe_cache = RecipeCache()
            self.load_dataset_now(source, record.get('source_name') or 'Saved source')
            self._install_ordered(operations, backend=backend, memory_bytes=memory)
            self.current = result
            self.last_operations = deepcopy(operations)
            self.last_plan = plan
            self.last_backend = backend
            self.last_memory_bytes = memory
            self.selected_regions = deepcopy(record.get('regions', []))
            self.analysis_reports = deepcopy(record.get('reports', {}))
            self._display_analysis()
            self._refresh_analysis_bounds()
            self._restore_region_controls()
            self._render()
            self._refresh_buttons()
            self._set_status(f"Reopened {len(operations)} applied steps and {len(self.analysis_reports)} analysis report(s).")
        except BaseException as exc:
            try:
                self._restore_view_checkpoint(checkpoint)
            except BaseException as rollback_exc:
                exc.add_note(f'GUI rollback failed: {rollback_exc}')
            raise

    def _capture_view_checkpoint(self):
        """Capture logical and visible state without copying large sample arrays."""
        logical_names = ('source', 'current', 'preview', 'source_name', 'last_operations',
                         'last_plan', 'last_backend', 'last_memory_bytes', '_axis_settings',
                         'analysis_reports', 'selected_regions', '_recipe_mode',
                         '_ordered_operations', '_pending_memory_bytes', '_recipe_cache',
                         '_rendered_dataset', '_rendered_axis_units', '_suspend_preview_invalidation',
                         '_state_generation')
        reference_names = {'source', 'current', 'preview', '_recipe_cache', '_rendered_dataset'}
        logical = {name: (getattr(self, name) if name in reference_names else deepcopy(getattr(self, name)))
                   for name in logical_names}
        combo_names = ('axis_combo', 'component_combo', 'view_combo', 'backend_combo',
                       'export_combo', 'region_unit', 'sign_combo', 'baseline_combo', 'zf_combo')
        combos = {}
        for name in combo_names:
            widget = getattr(self, name)
            combos[name] = ([(widget.itemText(i), widget.itemData(i)) for i in range(widget.count())],
                            widget.currentIndex())
        value_names = ('lb_spin', 'first_point_spin', 'p0_spin', 'p1_spin', 'pivot_spin',
                       'crop_start_spin', 'crop_stop_spin', 'reference_ppm_spin',
                       'reference_hz_spin', 'reference_mhz_spin', 'memory_spin',
                       'snr_spin', 'threshold_spin', 'baseline_value_spin')
        values = {name: getattr(self, name).value() for name in value_names}
        check_names = ('filter_check', 'fft_check', 'crop_check', 'reference_check',
                       'reference_reverse_check', 'first_order_check', 'threshold_check',
                       'analysis_region_check')
        checks = {name: getattr(self, name).isChecked() for name in check_names}
        line_names = ('phase_mask_path', 'noise_mask_path', 'baseline_mask_path')
        lines = {name: getattr(self, name).text() for name in line_names}
        labels = {name: getattr(self, name).text() for name in
                  ('dataset_title', 'status', 'recipe_label', 'plan_label', 'axis_settings_label',
                   'ordered_label', 'analysis_summary', 'plot_caption', 'plot_note')}
        table = [[(self.analysis_table.item(row, col).text(),
                   self.analysis_table.item(row, col).toolTip())
                  if self.analysis_table.item(row, col) is not None else None
                  for col in range(self.analysis_table.columnCount())]
                 for row in range(self.analysis_table.rowCount())]
        plot = self.plot.getPlotItem()
        view = plot.getViewBox()
        plot_state = {
            'items': list(plot.items), 'range': deepcopy(view.viewRange()),
            'x_inverted': bool(view.state.get('xInverted')),
            'y_inverted': bool(view.state.get('yInverted')),
            'bottom': (plot.getAxis('bottom').labelText, plot.getAxis('bottom').labelUnits),
            'left': (plot.getAxis('left').labelText, plot.getAxis('left').labelUnits),
        }
        enabled = {widget: widget.isEnabled() for widget in self.findChildren(QtWidgets.QWidget)}
        return {'logical': logical, 'combos': combos, 'values': values, 'checks': checks,
                'lines': lines, 'labels': labels, 'region_bounds': self._entered_region(),
                'steps': ([self.ordered_steps.item(i).text() for i in range(self.ordered_steps.count())],
                          self.ordered_steps.currentRow()),
                'table': table, 'details': self.details.toHtml(), 'status_style': self.status.styleSheet(),
                'plot': plot_state, 'enabled': enabled}

    def _restore_view_checkpoint(self, checkpoint):
        """Restore after any failed installation without invoking failing render hooks."""
        for name, value in checkpoint['logical'].items():
            setattr(self, name, value)
        for name, (items, index) in checkpoint['combos'].items():
            widget = getattr(self, name)
            blocked = widget.blockSignals(True)
            try:
                widget.clear()
                for label, data in items:
                    widget.addItem(label, data)
                widget.setCurrentIndex(index)
            finally:
                widget.blockSignals(blocked)
        for name, value in checkpoint['values'].items():
            widget = getattr(self, name)
            blocked = widget.blockSignals(True)
            try:
                widget.setValue(value)
            finally:
                widget.blockSignals(blocked)
        for name, value in checkpoint['checks'].items():
            widget = getattr(self, name)
            blocked = widget.blockSignals(True)
            try:
                widget.setChecked(value)
            finally:
                widget.blockSignals(blocked)
        for name, value in checkpoint['lines'].items():
            widget = getattr(self, name)
            blocked = widget.blockSignals(True)
            try:
                widget.setText(value)
            finally:
                widget.blockSignals(blocked)
        # Recreate the prior dynamic region rows from the restored current
        # dataset, then restore their exact user-entered bounds.
        Workbench._refresh_analysis_bounds(self)
        for axis, pair in checkpoint['region_bounds']['bounds'].items():
            if axis in self.region_bounds:
                for field, value in zip(self.region_bounds[axis], pair):
                    field.setValue(value)
        self.ordered_steps.clear()
        for label in checkpoint['steps'][0]:
            self.ordered_steps.addItem(label)
        self.ordered_steps.setCurrentRow(checkpoint['steps'][1])
        self.analysis_table.setRowCount(len(checkpoint['table']))
        self.analysis_table.clearContents()
        for row, cells in enumerate(checkpoint['table']):
            for col, content in enumerate(cells):
                if content is not None:
                    item = QtWidgets.QTableWidgetItem(content[0])
                    item.setToolTip(content[1])
                    self.analysis_table.setItem(row, col, item)
        self.details.setHtml(checkpoint['details'])
        for name, value in checkpoint['labels'].items():
            getattr(self, name).setText(value)
        self.status.setStyleSheet(checkpoint['status_style'])
        plot = self.plot.getPlotItem()
        self.plot.clear()
        for item in checkpoint['plot']['items']:
            plot.addItem(item)
        plot.invertX(checkpoint['plot']['x_inverted'])
        plot.invertY(checkpoint['plot']['y_inverted'])
        self.plot.setLabel('bottom', *checkpoint['plot']['bottom'])
        self.plot.setLabel('left', *checkpoint['plot']['left'])
        x_range, y_range = checkpoint['plot']['range']
        plot.getViewBox().setRange(xRange=x_range, yRange=y_range, padding=0)
        for widget, state in checkpoint['enabled'].items():
            try:
                widget.setEnabled(state)
            except RuntimeError:
                pass  # A dynamic region row may have been replaced.


def main(argv=None):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("SpinFlow")
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
