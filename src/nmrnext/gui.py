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
import threading
import csv
import shutil
from types import MappingProxyType
from types import SimpleNamespace

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from .demo import generate_demo
from .errors import NMRNextError
from .io import export_preflight, read_dataset, write_dataset
from ._execution_cache import RecipeCache
from ._viewport import display_frame, full_extent, contour_frame
from .planning import plan_operations
from .workflow_bundle import export_workflow_bundle


INK = "#e6e7e9"
MUTED = "#a8abb0"
ACCENT = "#b7bcc3"
SURFACE = "#202225"
PAPER = "#111315"
BORDER = "#393c40"
PLOT_POSITIVE = "#f4f0df"
PLOT_NEGATIVE = "#dc777d"
_font_loaded = False


class _BatchProgressBridge(QtCore.QObject):
    """Deliver immutable worker progress snapshots on the GUI thread."""

    progressed = QtCore.Signal(object)


class _ZoomBoxViewBox(pg.ViewBox):
    """Right-drag selects data-coordinate bounds; click menus and left pan remain native."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._zoom_start = None
        self._zoom_start_screen = None
        self._zoom_box = None

    def _clear_zoom_box(self):
        if self._zoom_box is not None:
            self.removeItem(self._zoom_box)
            self._zoom_box = None
        self._zoom_start = None
        self._zoom_start_screen = None

    def mouseDragEvent(self, ev, axis=None):
        if ev.button() != QtCore.Qt.MouseButton.RightButton or axis is not None:
            return super().mouseDragEvent(ev, axis=axis)
        ev.accept()
        if ev.isStart():
            self._clear_zoom_box()
            self._zoom_start = self.mapSceneToView(ev.buttonDownScenePos())
            self._zoom_start_screen = ev.buttonDownScreenPos()
            pen = QtGui.QPen(QtGui.QColor("#fff4d6"))
            pen.setWidth(2)
            pen.setCosmetic(True)
            self._zoom_box = QtWidgets.QGraphicsRectItem()
            self._zoom_box.setPen(pen)
            self._zoom_box.setBrush(QtGui.QBrush(QtGui.QColor(255, 244, 214, 38)))
            self._zoom_box.setZValue(1e6)
            self.addItem(self._zoom_box, ignoreBounds=True)

        if self._zoom_start is None or self._zoom_box is None:
            return
        current = self.mapSceneToView(ev.scenePos())
        rect = QtCore.QRectF(self._zoom_start, current).normalized()
        self._zoom_box.setRect(rect)
        if ev.isFinish():
            start_screen = self._zoom_start_screen
            end_screen = ev.screenPos()
            enough_x = abs(end_screen.x() - start_screen.x()) >= 4
            enough_y = abs(end_screen.y() - start_screen.y()) >= 4
            self._clear_zoom_box()
            if enough_x and enough_y and rect.width() > 0 and rect.height() > 0:
                ranges = {}
                if self.state['mouseEnabled'][0]:
                    ranges['xRange'] = (rect.left(), rect.right())
                if self.state['mouseEnabled'][1]:
                    ranges['yRange'] = (rect.top(), rect.bottom())
                if ranges:
                    self.setRange(padding=0, **ranges)

    def keyPressEvent(self, ev):
        if ev.key() == QtCore.Qt.Key.Key_Escape and self._zoom_box is not None:
            self._clear_zoom_box()
            ev.accept()
            return
        return super().keyPressEvent(ev)


class _PhysicalContourPathItem(QtWidgets.QGraphicsPathItem):
    """Bounded display contour mapped through the true source block centers."""

    def __init__(self, data, level, color, x_centers, x_edges, y_centers, y_edges):
        self.data = data
        self.level = float(level)
        self.axisOrder = "row-major"
        path = QtGui.QPainterPath()
        x_knots = np.concatenate(([0.], np.arange(len(x_centers), dtype=np.float64) + .5,
                                  [float(len(x_centers))]))
        y_knots = np.concatenate(([0.], np.arange(len(y_centers), dtype=np.float64) + .5,
                                  [float(len(y_centers))]))
        x_values = np.concatenate(([x_edges[0]], x_centers, [x_edges[-1]]))
        y_values = np.concatenate(([y_edges[0]], y_centers, [y_edges[-1]]))
        for line in pg.functions.isocurve(data.T, self.level, connected=True, extendToEdge=True):
            if not line:
                continue
            x, y = line[0]
            path.moveTo(float(np.interp(x, x_knots, x_values)),
                        float(np.interp(y, y_knots, y_values)))
            for x, y in line[1:]:
                path.lineTo(float(np.interp(x, x_knots, x_values)),
                            float(np.interp(y, y_knots, y_values)))
        super().__init__(path)
        self.setPen(pg.mkPen(color, width=1.25))


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
        self._viewport_generation = 0
        self._viewport_pending = None
        self._viewport_item = None
        self._viewport_contours = []
        self._viewport_frame = None
        self._viewport_levels = None
        self._viewport_drawing = False
        self._viewport_timer = QtCore.QTimer(self)
        self._viewport_timer.setSingleShot(True)
        self._viewport_timer.setInterval(20)
        self._viewport_timer.timeout.connect(self._refresh_viewport)
        self._state_generation = 0
        self._recipe_cache = RecipeCache()
        self._batch_manifest_path = None
        self._batch_manifest_snapshot = None
        self._batch_template_path = None
        self._batch_template = None
        self._batch_draft_reference = None
        self._batch_draft_digest = None
        self._batch_template_dirty = True
        self._batch_manifest_dirty = False
        self._batch_operations = None
        self._batch_capture_current_applied = False
        self._batch_preserve_loaded_settings = False
        self._batch_backend = None
        self._batch_threads = 1
        self._batch_memory_bytes = None
        self._batch_pipe_script = None
        self._batch_plan_result = None
        self._batch_csv_path = None
        self._batch_stop_event = None
        self._batch_running = False
        self._batch_run_returned = False
        self._batch_progress_ticket = 0
        self._batch_next_item_number = 1
        self._batch_progress_bridge = _BatchProgressBridge(self)
        self._build_ui()
        self._style()
        self._batch_progress_bridge.progressed.connect(
            self._on_batch_progress, QtCore.Qt.ConnectionType.QueuedConnection)
        self._batch_processing_mode_changed()
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
        splitter.setSizes([210, 500, 610])
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
        self.bruker_button = QtWidgets.QPushButton("Open FID folder…")
        self.bruker_button.clicked.connect(self._open_bruker)
        self.bruker_button.setToolTip("Open a supported raw FID/acquisition folder. Format is detected from its contents; accepted profiles open in the time domain.")
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
        self.plot_controls_widget = QtWidgets.QWidget()
        display_controls = QtWidgets.QGridLayout(self.plot_controls_widget)
        display_controls.setContentsMargins(0, 0, 0, 0)
        display_controls.addWidget(QtWidgets.QLabel("2D display"), 0, 0)
        self.plot_mode_combo = QtWidgets.QComboBox()
        self.plot_mode_combo.addItem("Contours", "contours")
        self.plot_mode_combo.addItem("Raster", "raster")
        self.plot_mode_combo.setToolTip("Display only; never changes spectrum samples")
        self.plot_mode_combo.currentIndexChanged.connect(self._render)
        display_controls.addWidget(self.plot_mode_combo, 0, 1)
        display_controls.addWidget(QtWidgets.QLabel("Levels"), 0, 2)
        self.contour_level_count = QtWidgets.QSpinBox()
        self.contour_level_count.setRange(1, 3)
        self.contour_level_count.setValue(2)
        self.contour_level_count.setToolTip("Number of display-only contour levels per selected sign")
        self.contour_level_count.valueChanged.connect(self._render)
        display_controls.addWidget(self.contour_level_count, 0, 3)
        display_controls.addWidget(QtWidgets.QLabel("Spacing"), 1, 0)
        self.contour_spacing_combo = QtWidgets.QComboBox()
        self.contour_spacing_combo.addItem("Linear", "linear")
        self.contour_spacing_combo.addItem("Geometric", "geometric")
        self.contour_spacing_combo.currentIndexChanged.connect(self._render)
        display_controls.addWidget(self.contour_spacing_combo, 1, 1)
        display_controls.addWidget(QtWidgets.QLabel("Sign"), 1, 2)
        self.contour_sign_combo = QtWidgets.QComboBox()
        self.contour_sign_combo.addItem("Both", "both")
        self.contour_sign_combo.addItem("Positive", "positive")
        self.contour_sign_combo.addItem("Negative", "negative")
        self.contour_sign_combo.currentIndexChanged.connect(self._render)
        display_controls.addWidget(self.contour_sign_combo, 1, 3)
        display_controls.setColumnStretch(4, 1)
        layout.addWidget(self.plot_controls_widget)
        pg.setConfigOptions(antialias=True, background=PAPER, foreground=INK)
        self.plot = pg.PlotWidget(viewBox=_ZoomBoxViewBox())
        self.plot.setObjectName("plot")
        for axis_name in ("left", "bottom", "right", "top"):
            axis = self.plot.getPlotItem().getAxis(axis_name)
            axis.setPen("#62666c")
            axis.setTextPen(INK)
        self.plot.showGrid(x=True, y=True, alpha=0.16)
        self.plot.getPlotItem().getViewBox().setMouseEnabled(x=True, y=True)
        self.plot.getPlotItem().getViewBox().sigRangeChanged.connect(self._queue_viewport)
        layout.addWidget(self.plot, 1)
        self.plot_note = QtWidgets.QLabel("Right-drag a box to zoom; left-drag to pan; wheel to zoom. Contours are a display-only block min/max preview capped at 192 × 192; zoom to recalculate from source samples.")
        self.plot_note.setObjectName("caption")
        self.plot_note.setWordWrap(True)
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
        tip = QtWidgets.QLabel("Raw FID: set Window and Zero fill, then Fourier transform. Process applies the recipe; Phase is available on complex frequency data. Save runnable workflow keeps the applied recipe and source.")
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
        self.batch_tab_index = tabs.addTab(self._batch_panel(), 'Batch')
        tabs.setMinimumWidth(590)
        return tabs

    def _batch_panel(self):
        panel = QtWidgets.QWidget()
        self.batch_panel = panel
        layout = QtWidgets.QVBoxLayout(panel)
        intro = QtWidgets.QLabel(
            "Copy spectra with no processing, or reuse an applied recipe. Optional labels measure fixed regions.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        files_box = QtWidgets.QGroupBox("Input files")
        files_layout = QtWidgets.QVBoxLayout(files_box)
        self.batch_items_table = QtWidgets.QTableWidget(0, 5)
        self.batch_items_table.setHorizontalHeaderLabels(
            ["ID", "Input", "Output", "Axis map", "Status"])
        self.batch_items_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.batch_items_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.batch_items_table.setFixedHeight(88)
        self.batch_items_table.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.batch_items_table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((70, 112, 95, 108, 90)):
            self.batch_items_table.setColumnWidth(column, width)
        self.batch_items_table.cellDoubleClicked.connect(self._batch_map_item_dialog)
        files_layout.addWidget(self.batch_items_table)
        file_actions = QtWidgets.QHBoxLayout()
        self.batch_add_files_button = QtWidgets.QPushButton("Files…")
        self.batch_add_files_button.clicked.connect(self._batch_add_files_dialog)
        self.batch_add_folder_button = QtWidgets.QPushButton("Folder…")
        self.batch_add_folder_button.clicked.connect(self._batch_add_folder_dialog)
        self.batch_remove_item_button = QtWidgets.QPushButton("Remove")
        self.batch_remove_item_button.clicked.connect(self._batch_remove_selected_items)
        self.batch_map_axes_button = QtWidgets.QPushButton("Map axes…")
        self.batch_map_axes_button.clicked.connect(self._batch_map_selected_item)
        for button in (self.batch_add_files_button, self.batch_add_folder_button,
                       self.batch_remove_item_button, self.batch_map_axes_button):
            file_actions.addWidget(button)
        files_layout.addLayout(file_actions)
        output_row = QtWidgets.QHBoxLayout()
        output_row.addWidget(QtWidgets.QLabel("New output folder"))
        self.batch_output_directory_edit = QtWidgets.QLineEdit()
        self.batch_output_directory_edit.setToolTip("Choose an existing folder; batch creates unique new native outputs here.")
        self.batch_output_directory_edit.textChanged.connect(self._batch_items_changed)
        output_row.addWidget(self.batch_output_directory_edit, 1)
        self.batch_choose_output_button = QtWidgets.QPushButton("Choose…")
        self.batch_choose_output_button.clicked.connect(self._batch_choose_output_directory)
        output_row.addWidget(self.batch_choose_output_button)
        files_layout.addLayout(output_row)
        layout.addWidget(files_box)

        assignment_box = QtWidgets.QGroupBox("Optional labels and fixed regions")
        assignment_layout = QtWidgets.QVBoxLayout(assignment_box)
        assignment_intro = QtWidgets.QLabel(
            "Label a current 1D/2D spectrum or open a template. Search and integration bounds stay fixed; no automatic alignment is performed.")
        assignment_intro.setWordWrap(True)
        assignment_layout.addWidget(assignment_intro)
        self.batch_include_labels_check = QtWidgets.QCheckBox("Measure these labels for each input")
        self.batch_include_labels_check.setChecked(False)
        self.batch_include_labels_check.toggled.connect(self._batch_labels_toggled)
        assignment_layout.addWidget(self.batch_include_labels_check)
        channel_row = QtWidgets.QHBoxLayout()
        channel_row.addWidget(QtWidgets.QLabel("Analysis component"))
        self.assignment_channel_combo = QtWidgets.QComboBox()
        self.assignment_channel_combo.currentIndexChanged.connect(self._batch_template_edited)
        channel_signals = self.assignment_channel_combo.blockSignals(True)
        self.assignment_channel_combo.addItem("Component 0", 0)
        self.assignment_channel_combo.blockSignals(channel_signals)
        channel_row.addWidget(self.assignment_channel_combo)
        channel_row.addStretch()
        assignment_layout.addLayout(channel_row)
        self.assignment_table = QtWidgets.QTableWidget(0, 6)
        self.assignment_table.setHorizontalHeaderLabels(
            ["ID", "Label", "Sign", "Units", "Fixed regions", "Cutoff"])
        self.assignment_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked |
                                              QtWidgets.QAbstractItemView.EditTrigger.EditKeyPressed)
        self.assignment_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.assignment_table.setFixedHeight(88)
        self.assignment_table.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.assignment_table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((80, 90, 70, 60, 140, 70)):
            self.assignment_table.setColumnWidth(column, width)
        self.assignment_table.cellDoubleClicked.connect(self._batch_assignment_region_dialog)
        self.assignment_table.itemChanged.connect(self._batch_template_edited)
        assignment_layout.addWidget(self.assignment_table)
        assignment_actions = QtWidgets.QHBoxLayout()
        self.batch_label_button = QtWidgets.QPushButton("Label assignment…")
        self.batch_label_button.setText("Label")
        self.batch_label_button.clicked.connect(self._batch_add_assignment)
        self.batch_new_template_button = QtWidgets.QPushButton("New template from current…")
        self.batch_new_template_button.setText("New")
        self.batch_new_template_button.clicked.connect(self._batch_new_template)
        self.batch_remove_assignment_button = QtWidgets.QPushButton("Remove label")
        self.batch_remove_assignment_button.setText("Remove")
        self.batch_remove_assignment_button.clicked.connect(self._batch_remove_assignment)
        self.batch_save_template_button = QtWidgets.QPushButton("Save template…")
        self.batch_save_template_button.setText("Save")
        self.batch_save_template_button.clicked.connect(self._batch_save_template_dialog)
        self.batch_open_template_button = QtWidgets.QPushButton("Open template…")
        self.batch_open_template_button.setText("Open")
        self.batch_open_template_button.clicked.connect(self._batch_open_template_dialog)
        for button in (self.batch_label_button, self.batch_new_template_button,
                       self.batch_remove_assignment_button, self.batch_save_template_button,
                       self.batch_open_template_button):
            assignment_actions.addWidget(button)
        assignment_layout.addLayout(assignment_actions)
        self.batch_template_label = QtWidgets.QLabel("No template loaded")
        self.batch_template_label.setWordWrap(True)
        assignment_layout.addWidget(self.batch_template_label)
        layout.addWidget(assignment_box)

        recipe_box = QtWidgets.QGroupBox("Batch processing")
        recipe_layout = QtWidgets.QVBoxLayout(recipe_box)
        recipe_row = QtWidgets.QHBoxLayout()
        self.batch_processing_mode = QtWidgets.QComboBox()
        self.batch_processing_mode.setMaximumWidth(255)
        self.batch_processing_mode.addItem("No processing · copy input", "copy")
        self.batch_processing_mode.addItem("Use applied processing", "applied")
        self.batch_processing_mode.addItem("Import command file", "script")
        self.batch_processing_mode.currentIndexChanged.connect(self._batch_processing_mode_changed)
        recipe_row.addWidget(self.batch_processing_mode)
        self.batch_use_applied_recipe_button = QtWidgets.QPushButton("Use applied recipe")
        self.batch_use_applied_recipe_button.clicked.connect(self._batch_use_applied_recipe)
        self.batch_import_script_button = QtWidgets.QPushButton("Import…")
        self.batch_import_script_button.setMaximumWidth(110)
        self.batch_import_script_button.clicked.connect(self._batch_import_pipe_script)
        recipe_row.addWidget(self.batch_import_script_button)
        recipe_layout.addLayout(recipe_row)
        self.batch_recipe_label = QtWidgets.QLabel("No processing selected. Current backend and memory limit will be recorded.")
        self.batch_recipe_label.setWordWrap(True)
        recipe_layout.addWidget(self.batch_recipe_label)
        manifest_row = QtWidgets.QHBoxLayout()
        self.batch_open_manifest_button = QtWidgets.QPushButton("Open manifest…")
        self.batch_open_manifest_button.clicked.connect(self._batch_open_manifest_dialog)
        self.batch_save_manifest_button = QtWidgets.QPushButton("Save manifest…")
        self.batch_save_manifest_button.clicked.connect(self._batch_save_manifest_dialog)
        manifest_row.addWidget(self.batch_open_manifest_button)
        manifest_row.addWidget(self.batch_save_manifest_button)
        recipe_layout.addLayout(manifest_row)
        self.batch_manifest_label = QtWidgets.QLabel("Manifest not saved")
        self.batch_manifest_label.setWordWrap(True)
        recipe_layout.addWidget(self.batch_manifest_label)
        run_row = QtWidgets.QHBoxLayout()
        self.batch_plan_button = QtWidgets.QPushButton("Plan")
        self.batch_plan_button.clicked.connect(self._batch_plan_async)
        self.batch_apply_button = QtWidgets.QPushButton("Apply to files")
        self.batch_apply_button.setObjectName("primary")
        self.batch_apply_button.clicked.connect(self._batch_run_async)
        self.batch_stop_button = QtWidgets.QPushButton("Stop after current file")
        self.batch_stop_button.setText("Stop after file")
        self.batch_stop_button.clicked.connect(self._batch_request_stop)
        for button in (self.batch_plan_button, self.batch_apply_button, self.batch_stop_button):
            run_row.addWidget(button)
        recipe_layout.addLayout(run_row)
        self.batch_progress_label = QtWidgets.QLabel("No batch plan or results")
        self.batch_progress_label.setWordWrap(True)
        recipe_layout.addWidget(self.batch_progress_label)
        self.batch_export_csv_button = QtWidgets.QPushButton("Export CSV…")
        self.batch_export_csv_button.clicked.connect(self._batch_export_csv_dialog)
        recipe_layout.addWidget(self.batch_export_csv_button)
        layout.addWidget(recipe_box)

        self.batch_advanced_toggle = QtWidgets.QToolButton()
        self.batch_advanced_toggle.setText("Advanced run options")
        self.batch_advanced_toggle.setCheckable(True)
        self.batch_advanced_toggle.setChecked(False)
        self.batch_advanced_widget = QtWidgets.QWidget()
        advanced_layout = QtWidgets.QVBoxLayout(self.batch_advanced_widget)
        self.batch_axis_mapping_advanced = QtWidgets.QLabel(
            "Axis mappings are edited per item with the Map axes action. Raw JSON is not required.")
        self.batch_axis_mapping_advanced.setWordWrap(True)
        advanced_layout.addWidget(self.batch_axis_mapping_advanced)
        self.batch_resume_check = QtWidgets.QCheckBox("Resume exact matching items")
        self.batch_continue_check = QtWidgets.QCheckBox("Continue after item failures")
        advanced_layout.addWidget(self.batch_resume_check)
        advanced_layout.addWidget(self.batch_continue_check)
        self.batch_advanced_widget.setVisible(False)
        self.batch_advanced_toggle.toggled.connect(self.batch_advanced_widget.setVisible)
        layout.addWidget(self.batch_advanced_toggle)
        layout.addWidget(self.batch_advanced_widget)
        layout.addStretch(1)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(panel)
        self.batch_scroll = scroll
        return scroll

    def _batch_assignment_source(self):
        if self.current is None or self.preview is not None:
            raise NMRNextError("NO_APPLIED_SPECTRUM", "Process or open a current spectrum before labeling assignments")
        if (len(self.current.axes) not in (1, 2) or
                any(axis.role != "spectral" or axis.domain != "frequency" for axis in self.current.axes)):
            raise NMRNextError("UNSUPPORTED_ASSIGNMENT_DIMENSIONS",
                               "Assignments require an applied 1D or 2D frequency spectrum")
        if self._batch_template is None:
            self.assignment_channel_combo.blockSignals(True)
            try:
                self.assignment_channel_combo.clear()
                for channel in range(self.current.data.shape[0]):
                    self.assignment_channel_combo.addItem(f"Component {channel}", channel)
            finally:
                self.assignment_channel_combo.blockSignals(False)
        return self.current

    def _batch_add_assignment(self):
        try:
            dataset = self._batch_assignment_source()
        except NMRNextError as exc:
            self._show_error(exc)
            return
        from .analysis import dataset_digest

        source = dataset
        loaded_reference = deepcopy(self._batch_template["reference"]) if self._batch_template is not None else None
        draft_reference = deepcopy(self._batch_draft_reference)
        draft_digest = self._batch_draft_digest

        def check_reference():
            digest = dataset_digest(source)
            if loaded_reference is not None:
                if digest != loaded_reference["dataset_digest"]:
                    raise NMRNextError("TEMPLATE_REFERENCE_MISMATCH",
                                       "This template belongs to another reference spectrum. Start a new template from the current spectrum to change its origin.")
                return loaded_reference, False
            if draft_reference is not None:
                if digest != draft_digest:
                    raise NMRNextError("TEMPLATE_REFERENCE_MISMATCH",
                                       "The draft belongs to another reference spectrum. Start a new template from the current spectrum to change its origin.")
                return draft_reference, False
            from .operations import quadrature_index_orientation
            reference = {
                "dataset_digest": digest,
                "axes": [axis.to_dict() for axis in source.axes],
                "complex_axes": list(source.complex_axes),
                "quadrature_index_orientation": quadrature_index_orientation(source),
            }
            return reference, True

        def ready(result):
            reference, created = result
            if created:
                self._batch_draft_reference = deepcopy(reference)
                self._batch_draft_digest = reference["dataset_digest"]
            self._batch_insert_assignment(reference)

        self._start_job(check_reference, ready, "Checking the assignment reference…")

    def _batch_new_template(self):
        try:
            dataset = self._batch_assignment_source()
        except NMRNextError as exc:
            self._show_error(exc)
            return
        from .analysis import dataset_digest
        from .operations import quadrature_index_orientation

        def prepare():
            return {
                "dataset_digest": dataset_digest(dataset),
                "axes": [axis.to_dict() for axis in dataset.axes],
                "complex_axes": list(dataset.complex_axes),
                "quadrature_index_orientation": quadrature_index_orientation(dataset),
            }

        def ready(reference):
            self._batch_template = None
            self._batch_template_path = None
            self._batch_draft_reference = reference
            self._batch_draft_digest = reference["dataset_digest"]
            self._batch_template_dirty = True
            self.assignment_table.setRowCount(0)
            self._set_assignment_channels(reference)
            self.batch_template_label.setText("New template draft from this spectrum. Add labels and save a new file.")
            self.batch_include_labels_check.setChecked(True)
            self._batch_template_edited()

        self._start_job(prepare, ready, "Recording the current spectrum as a new template reference…")

    def _batch_insert_assignment(self, reference):
        row = self.assignment_table.rowCount()
        used_ids = {self.assignment_table.item(i, 0).text()
                    for i in range(row) if self.assignment_table.item(i, 0) is not None}
        number = row + 1
        assignment_id = f"assignment-{number:03d}"
        while assignment_id in used_ids:
            number += 1
            assignment_id = f"assignment-{number:03d}"
        axes = [dict(record) for record in reference["axes"]]
        unit = "points"
        search, integration = {}, {}
        selected = self.selected_regions[0] if self.selected_regions else None
        selected_bounds = selected.get("bounds", {}) if isinstance(selected, dict) else {}
        selected_matches = bool(selected and set(selected_bounds) == {axis["id"] for axis in axes})
        if selected_matches:
            unit = selected.get("unit", "points")
        else:
            selected_bounds = {}
        for axis in axes:
            axis_id = axis["id"]
            if axis_id in selected_bounds:
                low, high = map(float, selected_bounds[axis_id])
            else:
                size = int(axis["size"])
                center = (size - 1) / 2
                half = min(2.0, max(0.5, (size - 1) / 20))
                low, high = max(0.0, center - half), min(size - 1.0, center + half)
            integration[axis_id] = [low, high]
            search[axis_id] = [low, high]
        assignment = {"assignment_id": assignment_id, "label": f"Assignment {number}",
                      "sign": "positive", "unit": unit, "search_bounds": search,
                      "integration_bounds": integration, "threshold": 0.0}
        self._batch_add_assignment_row(assignment, axes)
        self.batch_include_labels_check.setChecked(True)
        self._batch_template_edited()

    def _batch_add_assignment_row(self, assignment, axes):
        row = self.assignment_table.rowCount()
        self.assignment_table.blockSignals(True)
        try:
            self.assignment_table.insertRow(row)
            identity = QtWidgets.QTableWidgetItem(assignment["assignment_id"])
            identity.setFlags(identity.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            self.assignment_table.setItem(row, 0, identity)
            label = QtWidgets.QTableWidgetItem(assignment["label"])
            label.setFlags(label.flags() | QtCore.Qt.ItemFlag.ItemIsEditable)
            self.assignment_table.setItem(row, 1, label)
            sign = QtWidgets.QComboBox()
            sign.addItems(["positive", "negative"])
            sign.setCurrentText(assignment["sign"])
            sign.currentTextChanged.connect(self._batch_template_edited)
            self.assignment_table.setCellWidget(row, 2, sign)
            unit = QtWidgets.QComboBox()
            unit.addItems(["points", "Hz", "ppm"])
            unit.setCurrentText(assignment["unit"])
            unit.currentTextChanged.connect(
                lambda new, item=identity: self._batch_unit_changed(self.assignment_table.row(item), new))
            self.assignment_table.setCellWidget(row, 3, unit)
            regions = QtWidgets.QPushButton("Edit fixed regions…")
            regions.setToolTip(
                "Search: " + self._bounds_summary(axes, assignment["search_bounds"], assignment["unit"]) +
                "\nIntegration: " + self._bounds_summary(axes, assignment["integration_bounds"], assignment["unit"]))
            regions.clicked.connect(
                lambda _checked=False, item=identity: self._batch_edit_assignment_bounds(
                    self.assignment_table.row(item)))
            self.assignment_table.setCellWidget(row, 4, regions)
            threshold = QtWidgets.QDoubleSpinBox()
            threshold.setRange(0.0, 1e15)
            threshold.setDecimals(6)
            threshold.setValue(float(assignment["threshold"]))
            threshold.valueChanged.connect(self._batch_template_edited)
            self.assignment_table.setCellWidget(row, 5, threshold)
            identity.setData(QtCore.Qt.ItemDataRole.UserRole, deepcopy(assignment))
            identity.setData(QtCore.Qt.ItemDataRole.UserRole + 1, deepcopy(axes))
        finally:
            self.assignment_table.blockSignals(False)

    @staticmethod
    def _axis_coordinate_values(axis, unit):
        size, start, step = int(axis["size"]), float(axis["start"]), float(axis["step"])
        values = np.arange(size, dtype=np.float64) if unit == "points" else start + np.arange(size) * step
        if unit == "ppm":
            obs, ref_ppm = axis.get("observation_mhz"), axis.get("reference_ppm")
            if obs is None or ref_ppm is None:
                raise NMRNextError("UNAVAILABLE_COORDINATES", f"{axis.get('nucleus') or axis['id']} has no ppm calibration")
            ref_hz = float(axis.get("reference_hz") or 0.0)
            values = float(ref_ppm) + (values - ref_hz) / float(obs)
        return values

    def _bounds_summary(self, axes, bounds, unit):
        parts = []
        for axis in axes:
            pair = bounds[axis["id"]]
            parts.append(f"{axis.get('nucleus') or axis['id']} {pair[0]:.4g}–{pair[1]:.4g} {unit}")
        return "; ".join(parts)

    def _batch_edit_assignment_bounds(self, row):
        identity = self.assignment_table.item(row, 0)
        if identity is None:
            return
        assignment = deepcopy(identity.data(QtCore.Qt.ItemDataRole.UserRole))
        axes = identity.data(QtCore.Qt.ItemDataRole.UserRole + 1)
        unit = self.assignment_table.cellWidget(row, 3).currentText()
        self._batch_bounds_dialog(row, assignment, axes, unit)

    def _batch_unit_changed(self, row, new_unit):
        identity = self.assignment_table.item(row, 0)
        if identity is None:
            return
        assignment = deepcopy(identity.data(QtCore.Qt.ItemDataRole.UserRole))
        old_unit = assignment["unit"]
        axes = identity.data(QtCore.Qt.ItemDataRole.UserRole + 1)
        try:
            for key in ("search_bounds", "integration_bounds"):
                for axis in axes:
                    axis_id = axis["id"]
                    for index, value in enumerate(assignment[key][axis_id]):
                        assignment[key][axis_id][index] = self._convert_axis_value(axis, value, old_unit, new_unit)
        except NMRNextError as exc:
            combo = self.assignment_table.cellWidget(row, 3)
            combo.blockSignals(True)
            combo.setCurrentText(old_unit)
            combo.blockSignals(False)
            self._show_error(exc)
            return
        assignment["unit"] = new_unit
        identity.setData(QtCore.Qt.ItemDataRole.UserRole, assignment)
        self.assignment_table.cellWidget(row, 4).setToolTip(
            "Search: " + self._bounds_summary(axes, assignment["search_bounds"], new_unit) +
            "\nIntegration: " + self._bounds_summary(axes, assignment["integration_bounds"], new_unit))
        self._batch_template_edited()

    @classmethod
    def _convert_axis_value(cls, axis, value, source_unit, target_unit):
        if source_unit == target_unit:
            return value
        def to_hz(number, unit):
            if unit == "Hz":
                return number
            if unit == "points":
                return float(axis["start"]) + number * float(axis["step"])
            obs, reference_ppm = axis.get("observation_mhz"), axis.get("reference_ppm")
            if obs is None or reference_ppm is None:
                raise NMRNextError("UNAVAILABLE_COORDINATES", f"{axis.get('nucleus') or axis['id']} has no ppm calibration")
            return float(axis.get("reference_hz") or 0.0) + (number - float(reference_ppm)) * float(obs)
        hz = to_hz(float(value), source_unit)
        if target_unit == "Hz":
            return hz
        if target_unit == "points":
            return (hz - float(axis["start"])) / float(axis["step"])
        obs, reference_ppm = axis.get("observation_mhz"), axis.get("reference_ppm")
        if obs is None or reference_ppm is None:
            raise NMRNextError("UNAVAILABLE_COORDINATES", f"{axis.get('nucleus') or axis['id']} has no ppm calibration")
        return float(reference_ppm) + (hz - float(axis.get("reference_hz") or 0.0)) / float(obs)

    def _batch_assignment_region_dialog(self, row, column):
        if column == 4:
            self._batch_edit_assignment_bounds(row)

    def _batch_bounds_dialog(self, row, assignment, axes, unit):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Edit fixed search and integration regions")
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(QtWidgets.QLabel("Bounds are inclusive. They are not shifted to follow detected peaks."))
        form = QtWidgets.QFormLayout()
        controls = {}
        for axis in axes:
            title = axis.get("nucleus") or axis["id"]
            group = QtWidgets.QGroupBox(f"{title} ({axis['id']})")
            grid = QtWidgets.QGridLayout(group)
            coordinates = self._axis_coordinate_values(axis, unit)
            maximum = max(float(np.max(np.abs(coordinates))), float(np.ptp(coordinates)), 1.0) * 2
            fields = {}
            for index, (key, name) in enumerate((("search_bounds", "Search"),
                                                   ("integration_bounds", "Integration"))):
                grid.addWidget(QtWidgets.QLabel(name), index, 0)
                values = []
                for side, label in enumerate(("Start", "End")):
                    box = QtWidgets.QDoubleSpinBox()
                    box.setRange(float(np.min(coordinates)) - maximum,
                                 float(np.max(coordinates)) + maximum)
                    box.setDecimals(6)
                    box.setValue(float(assignment[key][axis["id"]][side]))
                    grid.addWidget(box, index, side + 1)
                    values.append(box)
                fields[key] = values
            controls[axis["id"]] = fields
            form.addRow(group)
        layout.addLayout(form)
        actions = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save |
                                             QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        actions.accepted.connect(dialog.accept)
        actions.rejected.connect(dialog.reject)
        layout.addWidget(actions)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        updated = deepcopy(assignment)
        for axis_id, fields in controls.items():
            for key, pair in fields.items():
                low, high = pair[0].value(), pair[1].value()
                if low == high:
                    self._show_error(NMRNextError("INVALID_ASSIGNMENT_TEMPLATE", "Each region needs nonzero width."))
                    return
                updated[key][axis_id] = [low, high]
        identity = self.assignment_table.item(row, 0)
        identity.setData(QtCore.Qt.ItemDataRole.UserRole, updated)
        self.assignment_table.cellWidget(row, 4).setToolTip(
            "Search: " + self._bounds_summary(axes, updated["search_bounds"], unit) +
            "\nIntegration: " + self._bounds_summary(axes, updated["integration_bounds"], unit))
        self._batch_template_edited()

    def _batch_remove_assignment(self):
        rows = sorted({index.row() for index in self.assignment_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.assignment_table.removeRow(row)
        if rows:
            self._batch_template_edited()

    def _batch_template_edited(self, *_args):
        self._batch_template_dirty = True
        self._batch_plan_result = None
        self._batch_csv_path = None
        self._batch_manifest_dirty = True
        if hasattr(self, "batch_template_label"):
            self.batch_template_label.setText("Template edits are not saved. Save a new template before planning.")
        self._refresh_buttons()

    def _batch_template_record(self):
        reference = (deepcopy(self._batch_template["reference"]) if self._batch_template is not None
                     else deepcopy(self._batch_draft_reference))
        if reference is None:
            raise NMRNextError("MISSING_TEMPLATE_REFERENCE", "Label the current spectrum or open a template first")
        assignments = []
        for row in range(self.assignment_table.rowCount()):
            identity = self.assignment_table.item(row, 0)
            label = self.assignment_table.item(row, 1)
            if identity is None or label is None:
                raise NMRNextError("INVALID_ASSIGNMENT_TEMPLATE", f"Assignment row {row + 1} is incomplete")
            assignment = deepcopy(identity.data(QtCore.Qt.ItemDataRole.UserRole))
            assignment["label"] = label.text().strip()
            assignment["sign"] = self.assignment_table.cellWidget(row, 2).currentText()
            assignment["unit"] = self.assignment_table.cellWidget(row, 3).currentText()
            assignment["threshold"] = self.assignment_table.cellWidget(row, 5).value()
            if not assignment["label"]:
                raise NMRNextError("INVALID_ASSIGNMENT_TEMPLATE", f"Assignment row {row + 1} needs a label")
            assignments.append(assignment)
        channel = self.assignment_channel_combo.currentData()
        if type(channel) is not int:
            raise NMRNextError("INVALID_CHANNEL", "Choose an analysis component")
        base = deepcopy(self._batch_template) if self._batch_template is not None else {
            "schema_version": "1", "kind": "assignment_template", "reference": reference,
            "assignments": []}
        base["analysis_channel"] = channel
        base["assignments"] = assignments
        return base

    def _batch_save_template_dialog(self):
        try:
            record = self._batch_template_record()
        except (NMRNextError, ValueError) as exc:
            self._show_error(exc if isinstance(exc, Exception) else NMRNextError("INVALID_ASSIGNMENT_TEMPLATE", str(exc)))
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save assignment template", "", "JSON (*.json)")
        if not path:
            return
        from .assignments import save_assignment_template
        target = Path(path).expanduser().resolve()
        try:
            saved = save_assignment_template(record, target)
        except Exception as exc:
            self._show_error(exc)
            return
        self._batch_accept_template(saved, target)

    def _batch_open_template_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open assignment template", "", "JSON (*.json);;All files (*)")
        if not path:
            return
        from .assignments import load_assignment_template
        target = Path(path)
        self._start_job(lambda: load_assignment_template(target),
                        lambda template: self._batch_accept_template(template, target),
                        "Opening assignment template…")

    def _batch_accept_template(self, template, path):
        self._batch_template = deepcopy(template)
        self._batch_template_path = Path(path).resolve()
        self._batch_draft_reference = None
        self._batch_draft_digest = None
        self._batch_template_dirty = False
        self.batch_include_labels_check.setChecked(True)
        self.assignment_table.blockSignals(True)
        self.assignment_channel_combo.blockSignals(True)
        try:
            self.assignment_table.setRowCount(0)
            axes = template["reference"]["axes"]
            for assignment in template["assignments"]:
                self._batch_add_assignment_row(assignment, axes)
            self._set_assignment_channels(template["reference"])
            self.assignment_channel_combo.setCurrentIndex(template["analysis_channel"])
        finally:
            self.assignment_table.blockSignals(False)
            self.assignment_channel_combo.blockSignals(False)
        self.batch_template_label.setText(
            f"Reference: {self._batch_template_path} · {len(template['assignments'])} labels · component {template['analysis_channel']}")
        self._batch_manifest_dirty = True
        self._batch_plan_result = None
        self._batch_csv_path = None
        self._batch_update_item_axis_maps()
        self._refresh_buttons()

    def _set_assignment_channels(self, reference):
        self.assignment_channel_combo.blockSignals(True)
        try:
            self.assignment_channel_combo.clear()
            count = 2 ** len(reference["complex_axes"])
            for channel in range(count):
                self.assignment_channel_combo.addItem(f"Component {channel}", channel)
        finally:
            self.assignment_channel_combo.blockSignals(False)

    def _batch_update_item_axis_maps(self):
        reference = (self._batch_template["reference"] if self._batch_template is not None
                     else self._batch_draft_reference)
        if reference is None:
            return
        for row in range(self.batch_items_table.rowCount()):
            cell = self.batch_items_table.item(row, 3)
            if cell is not None and self.batch_include_labels_check.isChecked():
                cell.setData(QtCore.Qt.ItemDataRole.UserRole, {})
                cell.setText("Map required")

    def _batch_labels_toggled(self, enabled):
        if enabled:
            self._batch_update_item_axis_maps()
        else:
            for row in range(self.batch_items_table.rowCount()):
                cell = self.batch_items_table.item(row, 3)
                if cell is not None:
                    cell.setData(QtCore.Qt.ItemDataRole.UserRole, {})
                    cell.setText("No labels")
        self._batch_items_changed()

    def _axis_map_summary(self, axis_map):
        return ", ".join(f"{ref} → {target}" for ref, target in sorted(axis_map.items())) or "No labels"

    def _batch_map_selected_item(self):
        rows = sorted({index.row() for index in self.batch_items_table.selectedIndexes()})
        if not rows:
            self._set_status("Select one input row to map its axes.")
            return
        self._batch_map_item_dialog(rows[0], 3)

    def _batch_map_item_dialog(self, row, column):
        if column != 3 or not 0 <= row < self.batch_items_table.rowCount():
            return
        if self._batch_template is None and self._batch_draft_reference is None:
            self._set_status("Open or create an assignment template before mapping axes.")
            return
        path_item = self.batch_items_table.item(row, 1)
        if path_item is None:
            return
        source = Path(path_item.data(QtCore.Qt.ItemDataRole.UserRole) or path_item.text())
        def inspect():
            from .io import describe_dataset
            return describe_dataset(source)
        self._start_job(inspect, lambda header: self._batch_show_axis_map_dialog(row, header),
                        "Reading input headers for axis mapping…")

    def _batch_show_axis_map_dialog(self, row, header):
        if row >= self.batch_items_table.rowCount() or (self._batch_template is None and self._batch_draft_reference is None):
            return
        reference = (self._batch_template["reference"] if self._batch_template is not None
                     else self._batch_draft_reference)
        reference_axes = reference["axes"]
        target_axes = list(header.axes)
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Map assignment axes for this input")
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(QtWidgets.QLabel("Map each template axis to one distinct input axis. No remapping is inferred."))
        form = QtWidgets.QFormLayout()
        choices = {}
        old_map = self.batch_items_table.item(row, 3).data(QtCore.Qt.ItemDataRole.UserRole) or {}
        for reference in reference_axes:
            combo = QtWidgets.QComboBox()
            for axis in target_axes:
                combo.addItem(f"{axis.nucleus or axis.id} · {axis.id}", axis.id)
            if reference["id"] in old_map:
                index = combo.findData(old_map[reference["id"]])
            else:
                index = combo.findData(reference["id"])
            if index >= 0:
                combo.setCurrentIndex(index)
            form.addRow(f"{reference.get('nucleus') or reference['id']} ({reference['id']})", combo)
            choices[reference["id"]] = combo
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save |
                                             QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        mapped = {ref: combo.currentData() for ref, combo in choices.items()}
        if len(set(mapped.values())) != len(mapped):
            self._show_error(NMRNextError("INVALID_AXIS_MAP", "Each template axis must map to a distinct input axis."))
            return
        cell = self.batch_items_table.item(row, 3)
        cell.setData(QtCore.Qt.ItemDataRole.UserRole, mapped)
        cell.setText(self._axis_map_summary(mapped))
        self._batch_items_changed()

    def _batch_choose_output_directory(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose new native output folder")
        if path:
            self.batch_output_directory_edit.setText(path)

    def _batch_add_files_dialog(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Add processed spectra", "",
            "NMR spectra (*.nmrh5 *.h5 *.ft1 *.ft2 *.ucsf);;All files (*)")
        if paths:
            self._batch_add_input_paths(paths)

    def _batch_add_folder_dialog(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Add supported acquisition folder")
        if path:
            self._batch_add_input_paths([path])

    def _batch_add_input_paths(self, paths):
        output_dir = Path(self.batch_output_directory_edit.text()).expanduser() if self.batch_output_directory_edit.text().strip() else None
        self.batch_items_table.blockSignals(True)
        try:
            for raw_path in paths:
                row = self.batch_items_table.rowCount()
                existing = {self.batch_items_table.item(i, 0).text()
                            for i in range(self.batch_items_table.rowCount())
                            if self.batch_items_table.item(i, 0) is not None}
                counter = self._batch_next_item_number
                item_id = f"item-{counter:04d}"
                while item_id in existing:
                    counter += 1
                    item_id = f"item-{counter:04d}"
                self._batch_next_item_number = counter + 1
                source = str(Path(raw_path).expanduser().resolve())
                output = (str((output_dir / f"{item_id}.nmrh5").resolve()) if output_dir else "")
                self.batch_items_table.insertRow(row)
                values = (item_id, Path(raw_path).name, Path(output).name if output else "",
                          "Map required" if self.batch_include_labels_check.isChecked() else "No labels", "Ready")
                for column, value in enumerate(values):
                    cell = QtWidgets.QTableWidgetItem(value)
                    cell.setFlags(cell.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                    if column == 3:
                        cell.setData(QtCore.Qt.ItemDataRole.UserRole, {})
                    elif column == 1:
                        cell.setData(QtCore.Qt.ItemDataRole.UserRole, source)
                        cell.setToolTip(source)
                    elif column == 2:
                        cell.setData(QtCore.Qt.ItemDataRole.UserRole, output)
                        cell.setToolTip(output)
                    self.batch_items_table.setItem(row, column, cell)
        finally:
            self.batch_items_table.blockSignals(False)
        self._batch_items_changed()

    def _batch_remove_selected_items(self):
        rows = sorted({index.row() for index in self.batch_items_table.selectedIndexes()}, reverse=True)
        self.batch_items_table.blockSignals(True)
        try:
            for row in rows:
                self.batch_items_table.removeRow(row)
        finally:
            self.batch_items_table.blockSignals(False)
        if rows:
            self._batch_items_changed()

    def _batch_items_changed(self, *_args):
        self._batch_manifest_dirty = True
        self._batch_plan_result = None
        self._batch_csv_path = None
        if hasattr(self, "batch_manifest_label") and self._batch_manifest_path is not None:
            self.batch_manifest_label.setText("Manifest edits are not saved. Save a new manifest before planning.")
        self._refresh_buttons()

    def _batch_processing_mode_changed(self, *_args):
        mode = self.batch_processing_mode.currentData()
        self._batch_pipe_script = None
        self._batch_capture_current_applied = mode == "applied"
        self._batch_preserve_loaded_settings = False
        if mode == "copy":
            self._batch_operations = []
            self._batch_backend = self.backend_combo.currentData()
            self._batch_threads = 1
            self._batch_memory_bytes = self.memory_spin.value() * 1024**2
            self.batch_recipe_label.setText(
                f"No processing: input values and quadratures are copied. Saved backend/memory: {self._batch_backend}, "
                f"{self._batch_memory_bytes / 1024**2:g} MiB.")
        elif mode == "applied":
            if self.last_plan is None or self.last_backend is None or self.last_memory_bytes is None:
                self._batch_operations = None
                self._batch_backend = None
                self._batch_memory_bytes = None
                self.batch_recipe_label.setText("Apply a recipe in Processing first, then choose this mode.")
            else:
                self._batch_operations = deepcopy(self.last_operations)
                self._batch_backend = self.last_backend
                self._batch_threads = 1
                self._batch_memory_bytes = self.last_memory_bytes
                self.batch_recipe_label.setText(
                    f"Applied recipe selected · {len(self._batch_operations)} operations · {self._batch_backend} · "
                    f"{self._batch_memory_bytes / 1024**2:g} MiB. Unapplied edits and previews are excluded.")
        else:
            self._batch_operations = None
            self._batch_backend = self.backend_combo.currentData()
            self._batch_threads = 1
            self._batch_memory_bytes = self.memory_spin.value() * 1024**2
            self.batch_recipe_label.setText("Import supported command text; it is parsed as data and never executed.")
        self._batch_plan_result = None
        self._batch_manifest_dirty = True
        self._batch_csv_path = None
        self._refresh_buttons()

    def _batch_use_applied_recipe(self):
        index = self.batch_processing_mode.findData("applied")
        self.batch_processing_mode.setCurrentIndex(index)
        if self.batch_processing_mode.currentData() == "applied":
            self._batch_processing_mode_changed()

    def _batch_import_pipe_script(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import NMRPipe command file", "", "Command files (*.com *.csh *.txt);;All files (*)")
        if not path:
            return
        from .pipe_script import parse_pipe_script
        source = Path(path).expanduser().resolve()

        backend = self.backend_combo.currentData()
        memory_bytes = self.memory_spin.value() * 1024**2

        def parse():
            with source.open("rb") as stream:
                raw = stream.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise NMRNextError("PIPE_SCRIPT_TOO_LARGE", "Command text exceeds the 1 MiB input limit")
            text = raw.decode("utf-8", errors="strict")
            return parse_pipe_script(text, source_name=str(source))

        def done(parsed):
            self._batch_pipe_script = source
            self._batch_operations = None
            self._batch_backend = backend
            self._batch_threads = 1
            self._batch_memory_bytes = memory_bytes
            self._batch_manifest_dirty = True
            self._batch_csv_path = None
            stages = ", ".join(f"{stage['name']}@{stage['line']}" for stage in parsed["stages"])
            overwrite = " Script requests overwrite; batch outputs remain exclusive." if parsed.get("overwrite_requested") else ""
            self.batch_recipe_label.setText(
                f"Parsed {len(parsed['stages'])} data-only stages: {stages}. "
                f"Input {parsed['input_literal']!r} and output {parsed['output_literal']!r} bind per item.{overwrite}")
            self._batch_plan_result = None
            self._refresh_buttons()

        self._start_job(parse, done, "Reading command text; no commands are executed…")

    def _batch_manifest_from_widgets(self):
        include_labels = self.batch_include_labels_check.isChecked()
        if include_labels and (self._batch_template_path is None or self._batch_template_dirty):
            raise NMRNextError("MISSING_ASSIGNMENT_TEMPLATE", "Save or open the label template before applying it")
        mode = self.batch_processing_mode.currentData()
        if mode in ("copy", "script") and not self._batch_preserve_loaded_settings:
            self._batch_backend = self.backend_combo.currentData()
            self._batch_memory_bytes = self.memory_spin.value() * 1024**2
            self._batch_threads = 1
        elif mode == "applied" and self._batch_capture_current_applied and not self._batch_preserve_loaded_settings:
            if self.last_plan is None or self.last_backend is None or self.last_memory_bytes is None:
                raise NMRNextError("MISSING_BATCH_RECIPE", "Apply processing first to reuse its exact recipe")
            self._batch_operations = deepcopy(self.last_operations)
            self._batch_backend = self.last_backend
            self._batch_memory_bytes = self.last_memory_bytes
            self._batch_threads = 1
        if self._batch_backend is None or self._batch_memory_bytes is None:
            raise NMRNextError("MISSING_BATCH_RECIPE", "Choose a processing mode with a backend and memory limit")
        items = []
        for row in range(self.batch_items_table.rowCount()):
            cells = [self.batch_items_table.item(row, column) for column in range(4)]
            if any(cell is None or not cell.text().strip() for cell in cells[:3]):
                raise NMRNextError("INVALID_BATCH_MANIFEST", f"Input row {row + 1} is incomplete")
            axis_map = (cells[3].data(QtCore.Qt.ItemDataRole.UserRole) or {}) if include_labels else {}
            if include_labels:
                expected = {axis["id"] for axis in self._batch_template["reference"]["axes"]}
                if set(axis_map) != expected:
                    raise NMRNextError("INVALID_AXIS_MAP", f"Map axes for input row {row + 1}")
            item = {"item_id": cells[0].text(),
                    "input": cells[1].data(QtCore.Qt.ItemDataRole.UserRole) or cells[1].text(),
                    "output": cells[2].data(QtCore.Qt.ItemDataRole.UserRole) or cells[2].text()}
            if include_labels or axis_map:
                item["axis_map"] = axis_map
            items.append(item)
        if not items:
            raise NMRNextError("INVALID_BATCH_MANIFEST", "Add at least one input item")
        record = {"schema_version": "1", "kind": "batch_manifest", "backend": self._batch_backend,
                  "threads": self._batch_threads, "memory_bytes": self._batch_memory_bytes, "items": items}
        if include_labels:
            record["assignment_template"] = str(self._batch_template_path)
        if mode == "script":
            if self._batch_pipe_script is None:
                raise NMRNextError("MISSING_BATCH_RECIPE", "Import a supported command file first")
            record["pipe_script"] = str(self._batch_pipe_script)
        else:
            record["operations"] = deepcopy(self._batch_operations)
        return record

    def _batch_save_manifest_dialog(self):
        try:
            manifest = self._batch_manifest_from_widgets()
        except NMRNextError as exc:
            self._show_error(exc)
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save new batch manifest", "", "JSON (*.json)")
        if not path:
            return
        from .batch import save_batch_manifest
        target = Path(path).expanduser().resolve()
        try:
            saved = save_batch_manifest(manifest, target)
        except Exception as exc:
            self._show_error(exc)
            return
        self._batch_manifest_path = target
        self._batch_manifest_snapshot = deepcopy(saved)
        self._batch_manifest_dirty = False
        self._batch_plan_result = None
        self._batch_csv_path = None
        self.batch_manifest_label.setText(str(target))
        self._refresh_buttons()

    def _batch_open_manifest_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open batch manifest", "", "JSON (*.json);;All files (*)")
        if not path:
            return
        from .batch import load_batch_manifest
        target = Path(path).expanduser().resolve()

        def load():
            manifest = load_batch_manifest(target)
            if not manifest.get("assignment_template"):
                return manifest, None, None
            from .assignments import load_assignment_template
            template_path = Path(manifest["assignment_template"])
            if not template_path.is_absolute():
                template_path = target.parent / template_path
            template_path = template_path.resolve()
            return manifest, load_assignment_template(template_path), template_path

        self._start_job(load, lambda result: self._batch_accept_manifest(target, *result),
                        "Opening batch manifest and template…")

    def _batch_accept_manifest(self, path, manifest, template, template_path):
        self._batch_manifest_path = Path(path)
        self._batch_manifest_snapshot = deepcopy(manifest)
        self._batch_manifest_dirty = False
        self._batch_plan_result = None
        self._batch_csv_path = None
        self._batch_backend = manifest["backend"]
        self._batch_threads = manifest["threads"]
        self._batch_memory_bytes = manifest["memory_bytes"]
        self._batch_operations = deepcopy(manifest.get("operations"))
        self._batch_capture_current_applied = False
        self._batch_pipe_script = None
        if template is not None:
            self._batch_accept_template(template, template_path)
            self.batch_include_labels_check.setChecked(True)
        else:
            self._batch_template = None
            self._batch_template_path = None
            self._batch_draft_reference = None
            self._batch_draft_digest = None
            self._batch_template_dirty = False
            self.batch_include_labels_check.setChecked(False)
            self.assignment_table.setRowCount(0)
            self.batch_template_label.setText("No label template selected. Processing-only batches keep all quadratures.")
        if "pipe_script" in manifest:
            mode = "script"
        elif manifest.get("operations") == []:
            mode = "copy"
        else:
            mode = "applied"
        mode_index = self.batch_processing_mode.findData(mode)
        self.batch_processing_mode.blockSignals(True)
        self.batch_processing_mode.setCurrentIndex(max(0, mode_index))
        self.batch_processing_mode.blockSignals(False)
        self.batch_items_table.blockSignals(True)
        try:
            self.batch_items_table.setRowCount(0)
            for row, item in enumerate(manifest["items"]):
                self.batch_items_table.insertRow(row)
                axis_map = item.get("axis_map", {})
                map_text = self._axis_map_summary(axis_map) if axis_map else (
                    "Map required" if template is not None else "No labels")
                values = (item["item_id"], Path(item["input"]).name, Path(item["output"]).name, map_text, "Ready")
                for column, value in enumerate(values):
                    cell = QtWidgets.QTableWidgetItem(value)
                    cell.setFlags(cell.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                    if column == 3:
                        cell.setData(QtCore.Qt.ItemDataRole.UserRole, deepcopy(axis_map))
                    elif column == 1:
                        cell.setData(QtCore.Qt.ItemDataRole.UserRole, item["input"])
                        cell.setToolTip(item["input"])
                    elif column == 2:
                        cell.setData(QtCore.Qt.ItemDataRole.UserRole, item["output"])
                        cell.setToolTip(item["output"])
                    self.batch_items_table.setItem(row, column, cell)
        finally:
            self.batch_items_table.blockSignals(False)
        outputs = [Path(item["output"]) for item in manifest["items"]]
        if outputs:
            self.batch_output_directory_edit.setText(str(outputs[0].parent))
        self._batch_manifest_dirty = False
        self._batch_manifest_snapshot = deepcopy(manifest)
        if "pipe_script" in manifest:
            script = Path(manifest["pipe_script"])
            if not script.is_absolute():
                script = Path(path).parent / script
            self._batch_pipe_script = script.resolve()
            self._batch_operations = None
            self.batch_recipe_label.setText(f"NMRPipe command text: {self._batch_pipe_script}")
        else:
            count = len(manifest["operations"])
            self.batch_recipe_label.setText(
                ("No processing · all input quadratures are copied. " if not count else "Saved processing · ")
                + f"{count} operation(s) · {self._batch_backend} · {self._batch_memory_bytes / 1024**2:g} MiB")
        self.batch_manifest_label.setText(str(path))
        self._batch_preserve_loaded_settings = True
        self._refresh_buttons()

    def _batch_plan_async(self):
        if self._busy or self._batch_manifest_path is None or self._batch_manifest_dirty:
            return
        from .batch import load_batch_manifest, plan_batch
        path = Path(self._batch_manifest_path)
        snapshot = deepcopy(self._batch_manifest_snapshot)
        ticket = self._batch_progress_ticket + 1
        self._batch_progress_ticket = ticket
        self._batch_plan_result = None

        def work():
            if load_batch_manifest(path) != snapshot:
                raise NMRNextError("BATCH_MANIFEST_CHANGED", "Saved manifest changed after it was opened")
            return plan_batch(path)

        def done(plan):
            if ticket != self._batch_progress_ticket:
                return
            self._batch_plan_result = plan
            self.batch_progress_label.setText(
                f"Plan ready · {len(plan['items'])} items · run {plan['run_sha256'][:12]}")
            self._refresh_buttons()

        self._start_job(work, done, "Planning every batch input…")

    def _batch_run_async(self):
        if (self._busy or self._batch_manifest_path is None or self._batch_manifest_dirty or
                self._batch_plan_result is None or
                (self.batch_include_labels_check.isChecked() and self._batch_template_dirty)):
            return
        from .batch import load_batch_manifest, run_batch
        path = Path(self._batch_manifest_path)
        snapshot = deepcopy(self._batch_manifest_snapshot)
        resume = self.batch_resume_check.isChecked()
        continue_on_error = self.batch_continue_check.isChecked()
        stop_event = threading.Event()
        ticket = self._batch_progress_ticket + 1
        self._batch_progress_ticket = ticket
        self._batch_stop_event = stop_event
        self._batch_running = True
        self._batch_run_returned = False
        self._batch_progress_states = {}
        progress_states = {}
        progress_lock = threading.Lock()
        for row in range(self.batch_items_table.rowCount()):
            self._batch_set_item_status(row, "queued")
        bridge = self._batch_progress_bridge

        def progress(snapshot_value):
            event = MappingProxyType(dict(snapshot_value))
            with progress_lock:
                progress_states[event["item_id"]] = event["state"]
            bridge.progressed.emit((ticket, event))

        def work():
            if load_batch_manifest(path) != snapshot:
                raise NMRNextError("BATCH_MANIFEST_CHANGED", "Saved manifest changed after it was planned")
            result = run_batch(path, resume=resume, continue_on_error=continue_on_error,
                               should_stop=stop_event.is_set, on_progress=progress)
            summaries = self._batch_read_status_csv(Path(result["csv_path"]))
            with progress_lock:
                final_states = dict(progress_states)
            return result, summaries, final_states

        def done(value):
            if ticket != self._batch_progress_ticket:
                return
            result, summaries, final_states = value
            self._batch_run_returned = True
            self._batch_progress_states = final_states
            self._batch_running = False
            self._batch_stop_event = None
            self._batch_csv_path = Path(result["csv_path"])
            for row in range(self.batch_items_table.rowCount()):
                item_id = self.batch_items_table.item(row, 0).text()
                summary = summaries.get(item_id, {})
                counts = summary.get("counts", {})
                status = final_states.get(item_id)
                if status is None:
                    if counts.get("not_run"):
                        status = "not_run"
                    elif counts.get("failed"):
                        status = "failed"
                    else:
                        status = "completed"
                details = ", ".join(f"{key}={counts[key]}" for key in sorted(counts))
                label = status + (f" · {details}" if details else "")
                self._batch_set_item_status(row, label, summary.get("error"))
            completion = (
                f"Batch finished · completed {result['completed']} · skipped {result['skipped']} · "
                f"failed {result['failed']} · not run {result['not_run']}"
                + (f" · {result['stop_reason']}" if result.get("stop_reason") else ""))
            self.batch_progress_label.setText(completion)
            self._set_status(completion)
            self._refresh_buttons()

        self._start_job(work, done, "Applying batch sequentially…")

    @staticmethod
    def _batch_read_status_csv(path):
        summaries = {}
        with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                identity = row.get("item_id", "")
                status = row.get("status", "unknown")
                summary = summaries.setdefault(identity, {"counts": {}, "error": None})
                counts = summary["counts"]
                counts[status] = counts.get(status, 0) + 1
                if row.get("error") and summary["error"] is None:
                    try:
                        error = json.loads(row["error"])
                        if isinstance(error, dict):
                            summary["error"] = f"{error.get('code', 'ERROR')}: {error.get('message', str(error))}"
                        else:
                            summary["error"] = str(error)
                    except ValueError:
                        summary["error"] = row["error"]
        return summaries

    def _batch_set_item_status(self, row, status, error=None):
        self.batch_items_table.blockSignals(True)
        try:
            cell = self.batch_items_table.item(row, 4)
            if cell is None:
                cell = QtWidgets.QTableWidgetItem()
                cell.setFlags(cell.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                self.batch_items_table.setItem(row, 4, cell)
            cell.setText(status)
            cell.setToolTip(error or "")
        finally:
            self.batch_items_table.blockSignals(False)

    @QtCore.Slot(object)
    def _on_batch_progress(self, envelope):
        if not self._batch_running:
            return
        ticket, event = envelope
        if ticket != self._batch_progress_ticket or not isinstance(event, MappingProxyType):
            return
        item_id = event["item_id"]
        self._batch_progress_states[item_id] = event["state"]
        for row in range(self.batch_items_table.rowCount()):
            if self.batch_items_table.item(row, 0).text() == item_id:
                self._batch_set_item_status(row, event["state"])
                break
        self.batch_progress_label.setText(
            f"{event['state']} · {event['item_id']} · {event['completed']}/{event['total']}")

    def _batch_request_stop(self):
        if self._batch_running and self._batch_stop_event is not None:
            self._batch_stop_event.set()
            self.batch_progress_label.setText("Stop requested; the current spectrum will finish first.")

    def _batch_export_csv_dialog(self):
        if self._batch_csv_path is None or not self._batch_csv_path.is_file():
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export batch results CSV", "", "CSV (*.csv)")
        if not path:
            return
        source, target = self._batch_csv_path, Path(path).expanduser().resolve()
        if source.resolve() == target:
            self._set_status("Choose a different CSV destination; batch results are retained.", error=True)
            return
        try:
            with source.open("rb") as incoming, target.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
                outgoing.flush()
                os.fsync(outgoing.fileno())
        except FileExistsError:
            self._set_status("CSV destination already exists; choose a new path.", error=True)
            return
        except OSError as exc:
            self._show_error(exc)
            return
        self._set_status(f"Exported batch results to {target}")

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

    def _figure_request(self):
        """Bind a figure to the applied parent or its committed analysis region."""
        if self.current is None:
            raise NMRNextError('NO_DATASET', 'Open a dataset before saving a figure')
        if self.preview is not None:
            raise NMRNextError('ANALYSIS_STATE', 'Apply processing before saving a figure')
        reports = self._current_analysis()
        if not self.analysis_region_check.isChecked():
            return self.current, reports, self.source_name, False
        if (len(self.selected_regions) != 1 or self._entered_region() != self.selected_regions[0]
                or self.view_combo.currentIndex() != 0 or self._display_dataset() is not self.current
                or self._rendered_dataset is not self.current):
            raise NMRNextError('ANALYSIS_STATE',
                               'Show the applied Current spectrum and select the entered region before saving its figure')
        from .regions import select_analysis_region
        selection = self.selected_regions[0]
        region, mapping = select_analysis_region(self.current, selection['bounds'], unit=selection['unit'])
        annotations = {}
        for kind, report in reports.items():
            if kind == 'autophase':
                continue  # Parent-scale phase evidence belongs in saved analysis JSON.
            if (kind not in ('peaks', 'integrate')
                    or report.get('kind') != {'peaks': 'peak_picking', 'integrate': 'integration'}[kind]
                    or report.get('parent_region') != mapping
                    or report.get('dataset_digest') != mapping['region_dataset_digest']):
                raise NMRNextError('STALE_ANALYSIS',
                                   'Figure analysis belongs to a different selected region; select and analyze this region again')
            annotations[kind] = report
        return region, annotations, f'{self.source_name} · selected applied region', 'autophase' in reports

    def _save_figure_dialog(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Save scientific figure', 'spinflow-spectrum.svg', 'Figures (*.svg *.pdf *.png)')
        if not path:
            return
        try:
            from .figures import export_figure
            dataset, reports, title, phase_omitted = self._figure_request()
        except Exception as exc:
            self._show_error(exc)
            return
        self._start_job(lambda: export_figure(dataset, path, analysis=reports, title=title),
                        lambda report: self._set_status(
                            f"Saved {report['format']} figure to {report['path']}. "
                            + ("Parent-scale automatic-phase diagnostics remain in Save analysis JSON. " if phase_omitted else "")
                            + "Save analysis JSON for complete methods, parent coordinates and flags."),
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
            QFrame#topbar {{ background: #17191c; border-bottom: 2px solid {ACCENT}; }}
            QLabel#brand {{ color: white; font-size: 19pt; font-weight: 700; }}
            QLabel#descriptor {{ color: #b9cfdf; font-size: 10pt; padding-left: 15px; }}
            QLabel#datasetTitle {{ color: #d9e9f2; font-size: 10pt; }}
            QFrame#plotpanel {{ background: {PAPER}; border-left: 1px solid {BORDER}; border-right: 1px solid {BORDER}; }}
            QLabel#heading {{ font-size: 13pt; font-weight: 650; }}
            QLabel#caption {{ color: {MUTED}; font-size: 9pt; }}
            QLabel#recipe {{ color: {INK}; background: #2a2d31; border-left: 3px solid {ACCENT}; padding: 9px; }}
            QLabel#status {{ background: #25282c; border-top: 1px solid {BORDER}; padding: 7px 18px; }}
            QTextBrowser#details {{ background: transparent; color: {INK}; }}
            QPushButton, QComboBox, QDoubleSpinBox, QSpinBox, QListWidget, QTableWidget, QLineEdit {{ background: #2a2d31; color: {INK}; border: 1px solid #4a4e54; border-radius: 4px; min-height: 30px; padding: 2px 8px; selection-background-color: #51565d; }}
            QPushButton:hover {{ border-color: #aeb3b9; background: #35393e; }}
            QPushButton#primary {{ background: #555a61; color: {INK}; border-color: #777c83; font-weight: 650; }}
            QPushButton#primary:hover {{ background: #676c73; }}
            QPushButton:disabled {{ color: #777c82; background: #25282c; }}
            QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus, QPushButton:focus, QCheckBox:focus, QLineEdit:focus, QListWidget:focus, QTableWidget:focus {{ border: 2px solid #d5d8dc; }}
            QToolTip {{ color: {INK}; background: #2a2d31; border: 1px solid #62666c; }}
            QSplitter::handle {{ background: {BORDER}; width: 2px; }}
            QTabWidget::pane {{ background: {SURFACE}; border: 1px solid {BORDER}; }}
            QTabBar::tab {{ background: #292c30; color: #c3c6ca; border: 1px solid {BORDER}; padding: 6px 10px; }}
            QTabBar::tab:selected {{ background: #3a3e43; color: #f0f1f2; border-bottom: 2px solid #e0e2e5; }}
            QTabBar::tab:disabled {{ color: #8c9095; background: #24272a; }}
            QGroupBox {{ background: {SURFACE}; color: {INK}; border: 1px solid {BORDER}; border-radius: 4px; margin-top: 10px; padding-top: 8px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {INK}; }}
            QGroupBox:disabled {{ background: {SURFACE}; color: {MUTED}; }}
            QGroupBox QLabel:disabled {{ color: {MUTED}; }}
            QHeaderView::section {{ background: #363a3f; color: {INK}; border: 1px solid {BORDER}; padding: 3px 5px; }}
            QTableCornerButton::section {{ background: #363a3f; border: 1px solid {BORDER}; }}
            QTableWidget:disabled {{ background: #24272a; color: {MUTED}; }}
            QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled, QSpinBox:disabled {{ background: #24272a; color: {MUTED}; border-color: {BORDER}; }}
        """)

    def _set_status(self, text, *, error=False):
        self.status.setText(text)
        self.status.setStyleSheet("color: #ff9b9e;" if error else "")

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
        if self._batch_running and not self._batch_run_returned:
            self._batch_running = False
            self._batch_stop_event = None
            for row in range(self.batch_items_table.rowCount()):
                if self.batch_items_table.item(row, 4).text() == "queued":
                    self._batch_set_item_status(row, "not processed · batch preflight failed")
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
        self._viewport_timer.stop()
        self._viewport_pending = None
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
        self._queue_viewport()

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
        batch_ready = (has and self.preview is None and len(self.current.axes) in (1, 2)
                       and all(axis.role == "spectral" and axis.domain == "frequency"
                               for axis in self.current.axes))
        batch_idle = not self._busy
        self.batch_open_template_button.setEnabled(batch_idle)
        self.batch_open_manifest_button.setEnabled(batch_idle)
        self.batch_processing_mode.setEnabled(batch_idle)
        self.batch_import_script_button.setEnabled(batch_idle and self.batch_processing_mode.currentData() == "script")
        self.batch_use_applied_recipe_button.setEnabled(False)
        self.batch_add_files_button.setEnabled(batch_idle)
        self.batch_add_folder_button.setEnabled(batch_idle)
        self.batch_choose_output_button.setEnabled(batch_idle)
        self.batch_remove_item_button.setEnabled(batch_idle and self.batch_items_table.rowCount() > 0)
        self.batch_map_axes_button.setEnabled(batch_idle and self.batch_include_labels_check.isChecked()
                                              and self.batch_items_table.rowCount() > 0)
        self.batch_items_table.setEnabled(batch_idle)
        self.batch_output_directory_edit.setEnabled(batch_idle)
        self.batch_include_labels_check.setEnabled(batch_idle)
        self.assignment_channel_combo.setEnabled(batch_idle and batch_ready)
        self.assignment_table.setEnabled(batch_idle)
        self.batch_label_button.setEnabled(batch_idle and batch_ready)
        self.batch_new_template_button.setEnabled(batch_idle and batch_ready)
        self.batch_remove_assignment_button.setEnabled(
            batch_idle and self.assignment_table.rowCount() > 0)
        self.batch_save_template_button.setEnabled(
            batch_idle and self.assignment_table.rowCount() > 0
            and (self._batch_template is not None or self._batch_draft_reference is not None))
        self.batch_template_label.setEnabled(batch_idle)
        recipe_ready = (self._batch_pipe_script is not None if self.batch_processing_mode.currentData() == "script"
                        else self._batch_operations is not None)
        labels_ready = (not self.batch_include_labels_check.isChecked() or
                        (self._batch_template_path is not None and not self._batch_template_dirty))
        self.batch_save_manifest_button.setEnabled(batch_idle and labels_ready and recipe_ready
                                                   and self._batch_backend is not None
                                                   and self._batch_memory_bytes is not None
                                                   and self.batch_items_table.rowCount() > 0)
        can_plan_batch = (batch_idle and self._batch_manifest_path is not None
                          and not self._batch_manifest_dirty)
        self.batch_plan_button.setEnabled(can_plan_batch)
        self.batch_apply_button.setEnabled(can_plan_batch and self._batch_plan_result is not None
                                           and (not self.batch_include_labels_check.isChecked()
                                                or not self._batch_template_dirty))
        self.batch_stop_button.setEnabled(self._batch_running and self._batch_stop_event is not None)
        self.batch_export_csv_button.setEnabled(
            batch_idle and self._batch_csv_path is not None and self._batch_csv_path.is_file())
        for control in (self.batch_resume_check, self.batch_continue_check):
            control.setEnabled(batch_idle)
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
        self._viewport_timer.stop()
        self._viewport_pending = None
        self._viewport_generation += 1
        self._viewport_item = None
        self._viewport_contours = []
        self._viewport_frame = None
        self._viewport_levels = None
        dataset = self._display_dataset()
        self._rendered_dataset = None
        self._rendered_axis_units = {}
        self.plot.clear()
        if dataset is None:
            self.plot_controls_widget.setEnabled(False)
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
        domains = {axis.domain for axis in dataset.axes}
        domain_label = "Time domain" if domains == {"time"} else ("Frequency domain" if domains == {"frequency"} else "Mixed domains")
        self.plot_caption.setText(f"{label} · {domain_label} · {component_name(index, dataset.complex_axes)} · {data.shape}")
        self.plot_controls_widget.setEnabled(data.ndim == 2)
        self.plot.getPlotItem().invertX(False)
        self.plot.getPlotItem().invertY(False)
        if data.ndim == 1:
            axis = dataset.axes[0]
            frame = display_frame(dataset, index, memory_bytes=self.memory_spin.value() * 1024**2)
            unit = frame.units[0]
            self._rendered_axis_units[axis.id] = unit
            self._viewport_item = self.plot.plot([], [], pen=pg.mkPen(ACCENT, width=1.6))
            self.plot.addLine(y=0, pen=pg.mkPen("#8ca0b0", width=1))
            self.plot.setLabel("bottom", f"{axis.nucleus or axis.label or axis.id} ({unit})")
            self.plot.setLabel("left", "Signed intensity")
            self.plot.getPlotItem().invertX(axis.domain == "frequency")
        elif data.ndim == 2:
            ya, xa = dataset.axes
            frame = display_frame(dataset, index, memory_bytes=self.memory_spin.value() * 1024**2)
            yu, xu = frame.units
            self._rendered_axis_units = {xa.id: xu, ya.id: yu}
            item = pg.ImageItem(axisOrder="row-major")
            if self.plot_mode_combo.currentData() == "raster":
                vmax = float(np.percentile(np.abs(frame.values), 99.5)) if frame.values.size else 0.
                vmax = vmax if vmax > 0 else 1.0
                self._viewport_levels = (-vmax, vmax)
                colors = np.array([[30, 33, 37, 255], [115, 119, 124, 255],
                                   [190, 190, 186, 255], [220, 135, 139, 255],
                                   [245, 225, 219, 255]], dtype=np.uint8)
                lut = pg.ColorMap(np.linspace(0, 1, 5), colors).getLookupTable(nPts=256)
                item.setLookupTable(lut)
                item.setLevels(self._viewport_levels)
            self.plot.addItem(item)
            self._viewport_item = item
            self.plot.setLabel("bottom", f"{xa.nucleus or xa.label or xa.id} ({xu})")
            self.plot.setLabel("left", f"{ya.nucleus or ya.label or ya.id} ({yu})")
            self.plot.getPlotItem().invertX(xa.domain == "frequency")
            self.plot.getPlotItem().invertY(ya.domain == "frequency")
        else:
            self.plot_caption.setText("Plot supports 1D and 2D data; metadata and export remain available.")
            self.plot_note.setText('Plot supports one or two axes.')
        self._viewport_drawing = True
        try:
            if data.ndim in (1, 2):
                self._draw_viewport(frame)
            self.plot.autoRange()
            # Retain the complete parent extent, including tail points absent
            # from a strided overview. Later viewport updates never reset it.
            if data.ndim in (1, 2):
                ranges = {'xRange': full_extent(dataset.axes[-1])}
                if data.ndim == 2:
                    ranges['yRange'] = full_extent(dataset.axes[0])
                self.plot.getPlotItem().getViewBox().setRange(**ranges, padding=.02)
            self._rendered_dataset = dataset
        finally:
            self._viewport_drawing = False
        self._refresh_visible_region_button()

    def _viewport_key(self):
        return (self._viewport_generation, self._rendered_dataset, self.component_combo.currentData())

    def _queue_viewport(self, *_args):
        if (self._viewport_drawing or self._viewport_item is None
                or self._rendered_dataset is None
                or self._display_dataset() is not self._rendered_dataset):
            return
        self._viewport_pending = self._viewport_key()
        self._viewport_timer.start()

    def _refresh_viewport(self):
        pending, self._viewport_pending = self._viewport_pending, None
        if (pending is None or pending != self._viewport_key()
                or self._display_dataset() is not self._rendered_dataset):
            return
        dataset = self._rendered_dataset
        visible = self.plot.getPlotItem().getViewBox().viewRange()
        ranges = (visible[0],) if len(dataset.axes) == 1 else (visible[1], visible[0])
        try:
            frame = display_frame(dataset, self.component_combo.currentData(), ranges,
                                  memory_bytes=self.memory_spin.value() * 1024**2)
            self._viewport_drawing = True
            self._draw_viewport(frame)
        except Exception as exc:
            # Keep the last good drawing, but explicitly retract a completeness
            # claim for a range that could not be prepared.
            self.plot_note.setText(f'Visible display update failed: {exc}. Previous drawing retained.')
        finally:
            self._viewport_drawing = False

    def _draw_viewport(self, frame):
        if len(frame.coordinates) == 1:
            self._viewport_item.setData(frame.coordinates[0], frame.values)
        elif not frame.values.size:
            for item in self._viewport_contours:
                self.plot.removeItem(item)
            self._viewport_contours = []
            self._viewport_item.clear()
        elif self.plot_mode_combo.currentData() == "contours":
            self._draw_contours(frame)
        else:
            image = frame.values
            if len(frame.coordinates[1]) > 1 and frame.coordinates[1][0] > frame.coordinates[1][-1]:
                image = np.flip(image, axis=1)
            if len(frame.coordinates[0]) > 1 and frame.coordinates[0][0] > frame.coordinates[0][-1]:
                image = np.flip(image, axis=0)
            self._viewport_item.setImage(image, autoLevels=False, levels=self._viewport_levels,
                                         autoDownsample=False)
            self._viewport_item.setRect(QtCore.QRectF(*frame.rect))
        self._viewport_frame = frame
        if not frame.values.size:
            note = 'No sample centers in the visible area.'
        elif len(frame.coordinates) == 2 and self.plot_mode_combo.currentData() == "contours":
            note = ('Contours aggregate signed min/max over visible source blocks, capped at 192 × 192. '
                    'Nearby features may merge; display only.')
        elif frame.sampled:
            note = 'Display sampled within visible area; zoom for complete samples. Narrow peaks may be omitted.'
        else:
            note = 'Complete visible samples shown.'
        dataset = self._display_dataset()
        unreferenced = dataset and any(a.domain == 'frequency' and a.reference_ppm is None for a in dataset.axes)
        reference_note = (' Chemical-shift reference is unknown: axes show mathematical frequency offsets in Hz. '
                          'Confirm the orientation and enter a known anchor for ppm.' if unreferenced else '')
        self.plot_note.setText(f'{note} Right-drag a box to zoom; left-drag to pan; wheel to zoom. Intensities retain their sign.{reference_note}')

    def _draw_contours(self, frame):
        """Draw a small signed contour set on a bounded display-only frame."""
        dataset = self._display_dataset()
        contours = contour_frame(dataset, self.component_combo.currentData(), frame, max_samples=192)
        positive_values, negative_values = contours.positive, contours.negative
        y_coordinates, x_coordinates = contours.coordinates
        y_edges, x_edges = contours.edges
        if len(x_coordinates) > 1 and x_coordinates[0] > x_coordinates[-1]:
            positive_values = positive_values[:, ::-1]
            negative_values = negative_values[:, ::-1]
            x_coordinates = x_coordinates[::-1]
            x_edges = x_edges[::-1]
        if len(y_coordinates) > 1 and y_coordinates[0] > y_coordinates[-1]:
            positive_values = positive_values[::-1, :]
            negative_values = negative_values[::-1, :]
            y_coordinates = y_coordinates[::-1]
            y_edges = y_edges[::-1]

        rect = contours.rect

        for item in self._viewport_contours:
            self.plot.removeItem(item)
        self._viewport_contours = []
        if self._viewport_item is not None:
            self.plot.removeItem(self._viewport_item)
            self._viewport_item.deleteLater()

        # Transparent image supplies ImageItem's pixel-to-axis transform.
        # Extrema come from bounded visible source blocks, never a full copy.
        anchor = pg.ImageItem(axisOrder="row-major")
        anchor.setImage(np.zeros((*positive_values.shape, 4), dtype=np.uint8), autoLevels=False)
        anchor.setRect(QtCore.QRectF(*rect))
        self.plot.addItem(anchor)
        self._viewport_item = anchor

        count = self.contour_level_count.value()
        if self.contour_spacing_combo.currentData() == "geometric":
            fractions = np.geomspace(0.15, 0.85, count)
        else:
            fractions = np.linspace(1 / (count + 1), count / (count + 1), count)
        signs = self.contour_sign_combo.currentData()
        levels = []
        maximum = float(np.max(positive_values)) if positive_values.size else 0.
        minimum = float(np.min(negative_values)) if negative_values.size else 0.
        if signs in {"both", "positive"} and maximum > 0:
            levels.extend((positive_values, maximum * fraction, PLOT_POSITIVE) for fraction in fractions)
        if signs in {"both", "negative"} and minimum < 0:
            levels.extend((negative_values, minimum * fraction, PLOT_NEGATIVE) for fraction in fractions)
        for level_data, level, color in levels:
            contour = _PhysicalContourPathItem(level_data, level, color,
                                               x_coordinates, x_edges, y_coordinates, y_edges)
            self.plot.addItem(contour)
            self._viewport_contours.append(contour)

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
                         '_state_generation', '_viewport_item', '_viewport_frame', '_viewport_levels')
        reference_names = {'source', 'current', 'preview', '_recipe_cache', '_rendered_dataset',
                           '_viewport_item', '_viewport_frame'}
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
        self._viewport_timer.stop()
        self._viewport_pending = None
        self._viewport_generation += 1
        self._viewport_drawing = True
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
        self._viewport_drawing = False


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
