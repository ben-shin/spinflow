"""Offscreen UI checks use the real processor and file codec."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
import gc
import tempfile
import time
from pathlib import Path
import unittest

import numpy as np
from PySide6 import QtCore, QtWidgets

from nmrnext.demo import generate_demo
from nmrnext.errors import NMRNextError
from nmrnext.gui import Workbench, component_name
from nmrnext.io import read_dataset, write_dataset
from nmrnext.model import Dataset
from nmrnext.operations import run_operations


class WorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.window = Workbench()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.window = None
        gc.collect()

    def test_2d_demo_recipe_preview_process_and_reset_use_same_engine(self):
        self.window.demo2_button.click()
        source = self.window.source
        recipe = self.window.build_recipe()
        self.assertEqual(len(recipe), 6)
        self.assertEqual([s["op"] for s in recipe],
                         ["window", "zero_fill", "fft"] * 2)
        preview = self.window.process_now(preview=True)
        self.assertIs(self.window.current, source)
        self.assertIs(self.window.preview, preview)
        expected = run_operations(source, recipe, backend="native")
        np.testing.assert_array_equal(preview.data, expected.data)
        result = self.window.process_now()
        np.testing.assert_array_equal(result.data, expected.data)
        self.assertEqual([a.to_dict() for a in result.axes],
                         [a.to_dict() for a in expected.axes])
        self.assertEqual(result.provenance, expected.provenance)
        self.assertIs(self.window.source, source)
        self.assertIsNot(self.window.current, source)
        self.window.reset()
        self.assertIs(self.window.current, source)
        np.testing.assert_array_equal(source.data, generate_demo(dimensions=2).data)

    def test_phase_recipe_is_recomputed_from_original_and_saved_provenance(self):
        self.window.demo1_button.click()
        self.window.p0_spin.setValue(22.5)
        first = self.window.process_now()
        self.assertEqual(self.window.last_operations[-1]["op"], "phase")
        self.assertEqual(self.window.recipe_record()["result_provenance"], first.provenance)
        self.window.p0_spin.setValue(-17.0)
        second = self.window.process_now()
        expected = run_operations(self.window.source, self.window.build_recipe(), backend="native")
        np.testing.assert_array_equal(second.data, expected.data)
        self.assertFalse(np.array_equal(first.data, second.data))

    def test_save_roundtrip_and_loss_refusal(self):
        self.window.demo2_button.click()
        self.window.process_now()
        temp_root = Path(__file__).resolve().parents[2] / ".artifacts" / "gui-test-tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as folder:
            native = Path(folder) / "spectrum.nmrh5"
            report = self.window.export_now(native, "native")
            self.assertEqual(report["losses"], [])
            np.testing.assert_array_equal(read_dataset(native).data, self.window.current.data)
            with self.assertRaises(NMRNextError) as caught:
                self.window.export_now(native, "native")
            self.assertEqual(caught.exception.code, "OUTPUT_EXISTS")
            pipe = Path(folder) / "spectrum.ft2"
            profile = self.window.preflight_export("nmrpipe")
            self.assertIn("quadrature_projection", profile["losses"])
            with self.assertRaises(NMRNextError) as caught:
                self.window.export_now(pipe, "nmrpipe")
            self.assertEqual(caught.exception.code, "UNACKNOWLEDGED_LOSS")
            self.assertFalse(pipe.exists())
            if not profile["errors"]:
                report = self.window.export_now(pipe, "nmrpipe", allow_losses=profile["losses"])
                self.assertTrue(pipe.exists())
                self.assertEqual(report["format"], "nmrpipe-real")

    def test_processed_real_has_no_phase_recipe_and_error_is_visible(self):
        raw = generate_demo(dimensions=1)
        real_axis = replace(raw.axes[0], domain="frequency", unit="Hz", encoding="real")
        real = Dataset(raw.data[:1], (real_axis,), (), metadata={})
        self.window.load_dataset_now(real, "Processed real")
        self.window.p0_spin.setValue(90)
        self.assertEqual(self.window.build_recipe(), [])
        self.assertIn("no imaginary channel", self.window.plan_label.text())
        self.assertFalse(self.window.p0_spin.isEnabled())
        self.assertFalse(self.window.p1_spin.isEnabled())
        self.assertFalse(self.window.process_button.isEnabled())
        self.assertTrue(self.window.export_button.isEnabled())
        imported = Dataset(raw.data, raw.axes, raw.complex_axes,
                           metadata={"import": {"digital_filter": {"state": "uncorrected_or_unknown"}}})
        self.window.load_dataset_now(imported, "Bruker FID")
        self.assertIn("Digital-filter correction", self.window.plan_label.text())
        with self.assertRaises(NMRNextError) as caught:
            self.window.process_now()
        self.window._show_error(caught.exception)
        self.assertIn("DIGITAL_FILTER_UNRESOLVED", self.window.status.text())
        self.assertIn("validated digital-filter correction", self.window.status.text())

    def test_component_labels_follow_complex_axes_bit_order(self):
        self.assertEqual(component_name(2, ("direct", "indirect")),
                         "direct: real  /  indirect: imaginary")
        self.assertEqual(component_name(1, ("indirect", "direct")),
                         "indirect: imaginary  /  direct: real")

    def test_preview_is_not_savable_until_committed(self):
        self.window.demo1_button.click()
        self.window.process_now(preview=True)
        self.assertFalse(self.window.export_button.isEnabled())
        self.assertFalse(self.window.recipe_button.isEnabled())
        self.assertIs(self.window.current, self.window.source)
        self.window.process_now()
        self.assertTrue(self.window.export_button.isEnabled())
        self.assertTrue(self.window.recipe_button.isEnabled())
        self.assertIsNone(self.window.preview)

    def test_any_recipe_control_change_invalidates_previous_preview(self):
        self.window.demo1_button.click()
        controls = [
            ("axis", self.window.axis_combo, lambda w: w.setCurrentIndex(1)),
            ("line broadening", self.window.lb_spin, lambda w: w.setValue(6)),
            ("zero fill", self.window.zf_combo, lambda w: w.setCurrentIndex(2)),
            ("transform", self.window.fft_check, lambda w: w.setChecked(False)),
            ("zero-order phase", self.window.p0_spin, lambda w: w.setValue(65)),
            ("first-order phase", self.window.p1_spin, lambda w: w.setValue(30)),
            ("backend", self.window.backend_combo, lambda w: w.setCurrentIndex(1)),
        ]
        for name, widget, change in controls:
            with self.subTest(name=name):
                preview = self.window.process_now(preview=True)
                self.assertIs(self.window.preview, preview)
                if hasattr(widget, "currentIndex"):
                    old = widget.currentIndex()
                    restore = lambda: widget.setCurrentIndex(old)
                elif hasattr(widget, "isChecked"):
                    old = widget.isChecked()
                    restore = lambda: widget.setChecked(old)
                else:
                    old = widget.value()
                    restore = lambda: widget.setValue(old)
                change(widget)
                self.assertIsNone(self.window.preview)
                self.assertNotIn("Preview", self.window.plot_caption.text())
                self.assertIn("changed", self.window.status.text().lower())
                restore()
        self.window.p0_spin.setValue(10)
        stale = self.window.process_now(preview=True)
        self.window.p0_spin.setValue(65)
        self.assertIsNone(self.window.preview)
        result = self.window.process_now()
        expected = run_operations(self.window.source, self.window.build_recipe(), backend="native")
        np.testing.assert_array_equal(result.data, expected.data)
        self.assertFalse(np.array_equal(stale.data, result.data))

    def test_async_native_load_accepts_opaque_import_metadata_without_stale_plot(self):
        self.window.demo2_button.click()
        temp_root = Path(__file__).resolve().parents[2] / ".artifacts" / "gui-test-tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        variants = ["opaque", ["opaque"], None,
                    {"digital_filter": "opaque"}, {"digital_filter": None}]
        with tempfile.TemporaryDirectory(dir=temp_root) as folder:
            for i, imported in enumerate(variants):
                with self.subTest(imported=imported):
                    original = generate_demo(dimensions=1)
                    opaque = Dataset(original.data, original.axes, original.complex_axes,
                                     metadata={"import": imported}, provenance=original.provenance)
                    path = Path(folder) / f"opaque-{i}.nmrh5"
                    write_dataset(opaque, path, format="native")
                    self.window._start_job(lambda p=path: read_dataset(p),
                                           lambda ds: self.window.load_dataset_now(ds, path.name),
                                           "Opening spectrum…")
                    deadline = time.monotonic() + 10
                    while self.window._busy and time.monotonic() < deadline:
                        self.app.processEvents()
                        time.sleep(0.01)
                    self.assertFalse(self.window._busy)
                    self.assertEqual(self.window.source.metadata, {"import": imported})
                    self.assertIs(self.window.current, self.window.source)
                    self.assertIn("(256,)", self.window.plot_caption.text())
                    curves = [item for item in self.window.plot.getPlotItem().items
                              if hasattr(item, "getData")]
                    self.assertEqual(len(curves), 1)
                    np.testing.assert_array_equal(curves[0].getData()[1], opaque.data[0])
                    self.assertTrue(self.window.export_button.isEnabled())
                    self.assertIn("Loaded", self.window.status.text())
                    saved_path = Path(folder) / f"saved-{i}.nmrh5"
                    self.window.export_now(saved_path, "native")
                    saved = read_dataset(saved_path)
                    np.testing.assert_array_equal(saved.data[0], curves[0].getData()[1])
                    self.assertEqual(saved.metadata, {"import": imported})

    def test_failed_presentation_rolls_back_entire_previous_view(self):
        self.window.demo1_button.click()
        self.window.process_now(preview=True)
        old_source = self.window.source
        old_current = self.window.current
        old_preview = self.window.preview
        old_caption = self.window.plot_caption.text()
        old_title = self.window.dataset_title.text()
        old_curve = self.window.plot.getPlotItem().items[0].getData()[1].copy()
        original_render = self.window._render
        failed = False
        def fail_once():
            nonlocal failed
            if not failed:
                failed = True
                raise RuntimeError("presentation failed")
            return original_render()
        self.window._render = fail_once
        with self.assertRaisesRegex(RuntimeError, "presentation failed"):
            self.window.load_dataset_now(generate_demo(dimensions=2), "Replacement")
        self.assertIs(self.window.source, old_source)
        self.assertIs(self.window.current, old_current)
        self.assertIs(self.window.preview, old_preview)
        self.assertEqual(self.window.dataset_title.text(), old_title)
        self.assertEqual(self.window.plot_caption.text(), old_caption)
        np.testing.assert_array_equal(self.window.plot.getPlotItem().items[0].getData()[1], old_curve)
        self.assertFalse(self.window.export_button.isEnabled())

    def test_process_button_runs_on_worker_thread(self):
        self.window.demo1_button.click()
        self.window.process_button.click()
        self.assertTrue(self.window._busy)
        deadline = time.monotonic() + 10
        while self.window._busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(self.window._busy)
        self.assertEqual(self.window.current.axis("t2").domain, "frequency")
        self.assertIn("Processed", self.window.status.text())

    def test_close_during_real_processing_waits_for_worker(self):
        self.window.demo2_button.click()
        self.window.process_button.click()
        self.assertTrue(self.window._busy)
        self.window.close()
        self.assertTrue(self.window.isVisible())
        self.assertIn("still running", self.window.status.text())
        deadline = time.monotonic() + 10
        while self.window._busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(self.window._busy)
        self.assertEqual(self.window.current.axis("t2").domain, "frequency")
        self.window.close()
        self.assertFalse(self.window.isVisible())


if __name__ == "__main__":
    unittest.main()
