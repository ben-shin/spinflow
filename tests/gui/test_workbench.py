"""Offscreen UI checks use the real processor and file codec."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
import gc
import json
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch

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

    def test_acquisition_folder_uses_shared_reader(self):
        self.assertIn("acquisition folder", self.window.bruker_button.text().lower())
        with patch("nmrnext.gui.QtWidgets.QFileDialog.getExistingDirectory", return_value="sample.fid") as choose, \
             patch("nmrnext.gui.read_dataset") as reader, \
             patch.object(self.window, "_start_job") as start:
            self.window._open_bruker()
            self.assertEqual(choose.call_args.args[1], "Choose acquisition folder")
            start.call_args.args[0]()
            reader.assert_called_once_with("sample.fid")

    def test_analysis_region_files_pass_masks_to_shared_engine(self):
        w = self.window
        w.load_dataset_now(run_operations(generate_demo(dimensions=1, points=[64]),
                                           [{'op': 'fft', 'axis': 't2'}], backend='native'))
        scratch = Path.cwd() / '.tmp'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            path = Path(directory) / 'mask.json'
            from nmrnext.analysis import dataset_digest
            path.write_text(json.dumps({'dataset_digest': dataset_digest(w.current), 'regions': [{'t2': [0, 10]}]}))
            w.phase_mask_path.setText(str(path))
            w.noise_mask_path.setText(str(path))
            w.baseline_mask_path.setText(str(path))
            w.baseline_combo.setCurrentText('reference_linear')
            for kind, function, keyword in [('autophase', 'autophase', 'inclusion_mask'),
                                            ('peaks', 'pick_peaks', 'noise_mask'),
                                            ('integrate', 'integrate_regions', 'baseline_mask')]:
                with patch('nmrnext.analysis.' + function) as engine:
                    engine.return_value = (w.current, {}) if kind == 'autophase' else {}
                    work = w._analysis_request(kind)
                    selected = json.loads(path.read_text())
                    path.write_text(json.dumps({'dataset_digest': 'changed', 'regions': []}))
                    result = work()
                    self.assertEqual(np.flatnonzero(engine.call_args.kwargs[keyword]).tolist(), list(range(11)))
                    report = result[1] if kind == 'autophase' else result
                    role = {'autophase': 'phase', 'peaks': 'noise', 'integrate': 'baseline'}[kind]
                    self.assertEqual(report['mask_selections'][role], selected)
                    path.write_text(json.dumps(selected))

    def test_unreferenced_frequency_spectrum_uses_hz_and_prompts_for_anchor(self):
        source = run_operations(generate_demo(dimensions=1, points=[64]),
                                [{'op': 'fft', 'axis': 't2'}], backend='native')
        axis = replace(source.axes[0], reference_ppm=None)
        source = Dataset(source.data, (axis,), source.complex_axes)
        self.window.load_dataset_now(source)
        self.assertIsNone(self.window.current.axes[0].reference_ppm)
        self.assertEqual(self.window._axis_values(source, axis)[1], 'Hz')
        self.assertIn('reference is unknown', self.window.plot_note.text())
        self.assertFalse(self.window.reference_check.isChecked())

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

    def test_axis_settings_survive_selection_and_all_axes_edits(self):
        w = self.window
        w.demo2_button.click()
        w.axis_combo.setCurrentIndex(1)
        first_id = w.axis_combo.currentData()
        w.lb_spin.setValue(1.5)
        w.zf_combo.setCurrentIndex(0)
        w.p0_spin.setValue(25)
        w.p1_spin.setValue(-18)
        w.pivot_spin.setValue(7.5)
        w.axis_combo.setCurrentIndex(2)
        second_id = w.axis_combo.currentData()
        w.lb_spin.setValue(6)
        w.p0_spin.setValue(-40)
        w.p1_spin.setValue(32)
        recipe = w.build_recipe()
        w.axis_combo.setCurrentIndex(0)
        self.assertEqual(w.build_recipe(), recipe)
        self.assertIn("differ", w.axis_settings_label.text())
        w.zf_combo.setCurrentIndex(2)
        w.axis_combo.setCurrentIndex(1)
        self.assertEqual(w.lb_spin.value(), 1.5)
        self.assertEqual(w.p0_spin.value(), 25)
        self.assertEqual(w.pivot_spin.value(), 7.5)
        w.axis_combo.setCurrentIndex(2)
        self.assertEqual(w.lb_spin.value(), 6)
        self.assertEqual(w.p0_spin.value(), -40)
        recipe = w.build_recipe()
        phases = {op['axis']: op for op in recipe if op['op'] == 'phase'}
        self.assertEqual(phases[first_id]['p0'], 25)
        self.assertEqual(phases[second_id]['p1'], 32)
        self.assertEqual([op['size'] for op in recipe if op['op'] == 'zero_fill'],
                         [a.size * 4 for a in w.source.axes])
        result = w.process_now()
        # Independent NumPy reference, including the quadrature component pairing.
        expected = w.source.data.copy()
        for i, axis in enumerate(w.source.axes):
            lb, p0, p1, pivot = ((1.5, 25, -18, 7.5) if axis.id == first_id else (6, -40, 32, 0))
            shape = [1] * expected.ndim
            shape[i + 1] = axis.size
            expected *= np.exp(-np.pi * lb * np.arange(axis.size) * axis.step).reshape(shape)
            padding = [(0, 0)] * expected.ndim
            padding[i + 1] = (0, 3 * axis.size)
            expected = np.pad(expected, padding)
            mask = 1 << w.source.complex_axes.index(axis.id)
            for r in range(expected.shape[0]):
                if r & mask:
                    continue
                z = expected[r] + 1j * expected[r | mask]
                z = np.fft.fftshift(np.fft.fft(z, axis=i), axes=i)
                ramp_shape = [1] * z.ndim
                ramp_shape[i] = axis.size * 4
                angle = np.deg2rad(p0 + p1 * (np.arange(axis.size * 4) - pivot) / (axis.size * 4))
                z *= np.exp(1j * angle).reshape(ramp_shape)
                expected[r], expected[r | mask] = z.real, z.imag
        np.testing.assert_allclose(result.data, expected, rtol=1e-11, atol=1e-11)

    def test_reset_and_new_source_clear_axis_controls(self):
        w = self.window
        for reset in (w.reset, lambda: w.demo1_button.click()):
            w.demo2_button.click()
            w.axis_combo.setCurrentIndex(2)
            w.p0_spin.setValue(85)
            w.lb_spin.setValue(27)
            w.pivot_spin.setValue(12)
            reset()
            self.assertEqual(w.p0_spin.value(), 0)
            self.assertEqual(w.pivot_spin.value(), 0)
            self.assertEqual(w.lb_spin.value(), 3)
            self.assertFalse(any(op['op'] == 'phase' for op in w.build_recipe()))

    def test_processed_real_crop_preserves_values_and_calibration(self):
        w = self.window
        raw = generate_demo(dimensions=1)
        axis = replace(raw.axes[0], domain='frequency', unit='Hz', encoding='real', start=900, step=-4)
        source = Dataset(raw.data[:1], (axis,), (), metadata={})
        w.load_dataset_now(source, 'Real frequency')
        self.assertTrue(w.crop_check.isEnabled())
        self.assertIn('crop', w.plan_label.text().lower())
        self.assertFalse(w.pivot_spin.isEnabled())
        w.crop_start_spin.setValue(5)
        w.crop_stop_spin.setValue(23)
        self.assertEqual(w.build_recipe(), [])
        w.crop_check.setChecked(True)
        result = w.process_now()
        np.testing.assert_array_equal(result.data, source.data[:, 5:23])
        np.testing.assert_array_equal(result.coordinates(axis.id), source.coordinates(axis.id)[5:23])
        np.testing.assert_allclose(result.coordinates(axis.id, unit='ppm'), source.coordinates(axis.id, unit='ppm')[5:23])
        w.crop_start_spin.setValue(24)
        with self.assertRaises(NMRNextError) as caught:
            w.process_now()
        self.assertEqual(caught.exception.code, 'INVALID_REGION')

    def test_workflow_export_uses_applied_settings_and_dialog_cancel_errors(self):
        w = self.window
        w.demo1_button.click()
        w.p0_spin.setValue(12)
        w.process_now()
        applied = w.last_operations.copy()
        w.p0_spin.setValue(90)
        temp_root = Path(__file__).resolve().parents[2] / '.artifacts' / 'gui-test-tmp'
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as folder:
            destination = Path(folder) / 'workflow'
            path = w.export_workflow_now(destination)
            recipe = json.loads(path.read_text())
            self.assertEqual(recipe['operations'], applied)
            with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=('', '')):
                w._save_workflow_dialog()
                w._save_recipe_dialog()
            with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(destination), '')):
                w._save_workflow_dialog()
                deadline = time.monotonic() + 10
                while w._busy and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)
            self.assertIn('OUTPUT_EXISTS', w.status.text())
            self.assertEqual(json.loads(path.read_text())['operations'], applied)
            audit = Path(folder) / 'audit.json'
            with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(audit), '')):
                w._save_recipe_dialog()
                original = audit.read_bytes()
                self.assertEqual(json.loads(original)['operations'], applied)
                w._save_recipe_dialog()
                self.assertIn('FileExistsError', w.status.text())
                self.assertEqual(audit.read_bytes(), original)

    def test_complex_frequency_crops_are_retained_per_axis_and_invalidate_preview(self):
        w = self.window
        raw = generate_demo(dimensions=2)
        source = run_operations(raw, [{'op': 'fft', 'axis': a.id} for a in raw.axes], backend='native')
        w.load_dataset_now(source)
        w.axis_combo.setCurrentIndex(1)
        w.crop_start_spin.setValue(3)
        w.crop_stop_spin.setValue(20)
        w.crop_check.setChecked(True)
        w.axis_combo.setCurrentIndex(2)
        w.crop_start_spin.setValue(7)
        w.crop_stop_spin.setValue(40)
        w.crop_check.setChecked(True)
        w.axis_combo.setCurrentIndex(0)
        w.axis_combo.setCurrentIndex(1)
        self.assertEqual(w.crop_start_spin.value(), 3)
        self.assertEqual(w.crop_stop_spin.value(), 20)
        result = w.process_now()
        np.testing.assert_array_equal(result.data, source.data[:, 3:20, 7:40])
        w.process_now(preview=True)
        w.crop_stop_spin.setValue(19)
        self.assertIsNone(w.preview)
        w.process_now(preview=True)
        w.crop_check.setChecked(False)
        self.assertIsNone(w.preview)
        w.reset()
        self.assertFalse(w.crop_check.isChecked())
        self.assertEqual(w.build_recipe(), [])

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

    def test_analysis_uses_applied_result_and_clears_after_processing(self):
        from nmrnext.model import Axis
        w = self.window
        source = Dataset(np.array([[0., 1., 3., 1., 0., -2., 0.]]),
                         (Axis('f', 7, domain='frequency', unit='Hz', step=2, encoding='real'),))
        w.load_dataset_now(source)
        w.threshold_check.setChecked(True)
        w.threshold_spin.setValue(0.5)
        w.sign_combo.setCurrentText('both')
        peaks = w.analyze_now('peaks')
        self.assertEqual({p['sign'] for p in peaks['peaks']}, {'positive', 'negative'})
        w.region_bounds['f'][0].setValue(0)
        w.region_bounds['f'][1].setValue(4)
        report = w.analyze_now('integrate')
        self.assertAlmostEqual(report['regions'][0]['integral'], 10)
        w.baseline_combo.setCurrentText('constant')
        w.baseline_value_spin.setValue(1)
        report = w.analyze_now('integrate')
        self.assertAlmostEqual(report['regions'][0]['integral'], 2)
        self.assertGreater(w.analysis_table.rowCount(), 0)
        w.crop_check.setChecked(True)
        w.crop_stop_spin.setValue(5)
        self.assertTrue(w.analysis_reports)  # Pending controls do not alter applied data.
        w.process_now()
        self.assertEqual(w.analysis_reports, {})
        self.assertEqual(w.analysis_table.rowCount(), 0)

    def test_explicit_bruker_filter_precedes_other_operations_and_unknown_refuses(self):
        from nmrnext.model import Axis
        w = self.window
        z = np.exp(2j * np.pi * np.arange(64) / 8)
        metadata = {'import': {'format': 'bruker-raw-1d', 'acquisition_parameters':
                    {'AQ_mod': 3, 'TD': 128, 'SW_h': 1000}, 'digital_filter':
                    {'state': 'uncorrected_or_unknown', 'GRPDLY': 4.25, 'DECIM': 3080, 'DSPFVS': 20}}}
        source = Dataset(np.stack((z.real, z.imag)), (Axis('a0', 64, step=.001, dwell_seconds=.001),), ('a0',), metadata)
        w.load_dataset_now(source)
        self.assertFalse(w.filter_check.isChecked())
        with self.assertRaises(NMRNextError):
            w.process_now()

        w.filter_check.setChecked(True)
        self.assertEqual(w.build_recipe()[0]['op'], 'digital_filter')
        result = w.process_now()
        self.assertEqual(result.axes[0].domain, 'frequency')
        self.assertEqual(source.metadata['import']['digital_filter']['state'], 'uncorrected_or_unknown')
        w.reset()
        self.assertFalse(w.filter_check.isChecked())
        metadata['import']['digital_filter']['GRPDLY'] = -1
        w.load_dataset_now(Dataset(source.data, source.axes, source.complex_axes, metadata))
        w.filter_check.setChecked(True)
        with self.assertRaises(NMRNextError):
            w.process_now()


    def test_reference_controls_calibrate_without_changing_intensities(self):
        from nmrnext.model import Axis
        w = self.window
        source = Dataset(np.array([[0., 1., 2., 1.]]),
                         (Axis('f', 4, domain='frequency', unit='Hz', start=-100, step=50, encoding='real'),))
        w.load_dataset_now(source)
        w.reference_mhz_spin.setValue(100)
        w.reference_ppm_spin.setValue(4.7)
        w.reference_hz_spin.setValue(0)
        w.reference_check.setChecked(True)
        result = w.process_now()
        np.testing.assert_array_equal(result.data, source.data)
        np.testing.assert_allclose(result.coordinates('f', 'ppm'), [3.7, 4.2, 4.7, 5.2])

    def test_2d_auto_phase_preserves_both_operations_reports_and_native_workflow(self):
        from nmrnext.model import Axis
        from nmrnext.analysis import dataset_digest
        from nmrnext import cli
        data = np.zeros((4, 256, 320))
        for y, x in ((55, 67), (130, 166), (203, 251)):
            fy = np.exp(.6j)/(1+1j*(np.arange(256)-y)/2.5)
            fx = np.exp(-.9j)/(1+1j*(np.arange(320)-x)/3.2)
            for ch, values in enumerate(((fy.real, fx.real), (fy.imag, fx.real),
                                         (fy.real, fx.imag), (fy.imag, fx.imag))):
                data[ch] += np.outer(*values)
        data += np.random.default_rng(76).normal(0, .0001, data.shape)
        source = Dataset(data, [Axis('c', 256, domain='frequency', unit='Hz', step=2),
                                Axis('h', 320, domain='frequency', unit='Hz', step=1.5)], ['c', 'h'])
        w = self.window
        w.load_dataset_now(source)
        self.assertTrue(w.autophase_button.isEnabled())
        w.first_order_check.setChecked(True)
        w.p0_spin.setValue(90)  # Pending processing edit excluded from analysis.
        result, report = w.analyze_now('autophase')
        self.assertEqual(w.last_operations, report['operations'])
        self.assertEqual(set(report['axis_results']), {'c', 'h'})
        self.assertIn('points', w.analysis_summary.text())
        self.assertIn('first logical axis', w.analysis_summary.text())
        replay = run_operations(source, w.last_operations, backend='native')
        np.testing.assert_array_equal(replay.data, result.data)
        self.assertEqual(replay.to_metadata(), result.to_metadata())
        for component in (3, 1, 0):
            w.component_combo.setCurrentIndex(component)
            w.view_combo.setCurrentIndex(1)
            w.view_combo.setCurrentIndex(0)
            w.axis_combo.setCurrentIndex(1)
            self.assertEqual(w._current_analysis()['autophase'], report)
            self.assertEqual(dataset_digest(w.current), report['result_dataset_digest'])
        temp_root = Path(__file__).resolve().parents[2]/'.artifacts/gui-test-tmp'
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as folder:
            recipe_path = w.export_workflow_now(Path(folder)/'workflow')
            self.assertEqual(json.loads(recipe_path.read_text())['operations'], report['operations'])
            self.assertEqual(cli.main(['run', str(recipe_path)]), 0)
            saved = read_dataset(recipe_path.parent/'result.nmrh5')
            np.testing.assert_array_equal(saved.data, result.data)
            self.assertEqual(saved.to_metadata(), result.to_metadata())
        w.process_now()
        self.assertEqual(w.analysis_reports, {})
        self.assertEqual(len(w.last_operations), 2)  # Manual phases, one per axis.
        w.load_dataset_now(Dataset(source.data[:1], source.axes))
        self.assertFalse(w.autophase_button.isEnabled())
        w.load_dataset_now(Dataset(source.data[:2], source.axes, ['c']))
        self.assertFalse(w.autophase_button.isEnabled())

    def test_automatic_phase_preserves_applied_crop_recipe_and_invalidates_on_process(self):
        from nmrnext.model import Axis
        w = self.window
        x = np.arange(1024)
        z = (1 / (1 + 1j * (x-250)/5) + .7 / (1 + 1j * (x-700)/6)) * np.exp(.4j)
        source = Dataset(np.stack((z.real, z.imag)),
                         (Axis('f', 1024, domain='frequency', unit='Hz', step=2),), ('f',))
        w.load_dataset_now(source)
        w.crop_start_spin.setValue(20)
        w.crop_stop_spin.setValue(1000)
        w.crop_check.setChecked(True)
        applied = w.process_now()
        operations = w.last_operations.copy()
        w.p0_spin.setValue(90)  # Pending edit must not leak into automatic analysis.
        result, report = w.analyze_now('autophase')
        self.assertEqual(w.last_operations, operations + [report['operation']])
        expected = run_operations(source, w.last_operations, backend='native')
        np.testing.assert_array_equal(result.data, expected.data)
        self.assertEqual(result.to_metadata(), expected.to_metadata())
        self.assertIsNot(w.current, applied)
        self.assertIn('Next Process', w.analysis_summary.text())
        self.assertIn('autophase', w._current_analysis())
        w.threshold_check.setChecked(True)
        w.threshold_spin.setValue(.1)
        w.analyze_now('peaks')
        w.region_bounds['f'][0].setValue(220)
        w.region_bounds['f'][1].setValue(240)
        w.analyze_now('integrate')
        reports = w._current_analysis()
        rows = w.analysis_table.rowCount()
        summary = w.analysis_summary.text()
        temp_root = Path(__file__).resolve().parents[2] / '.artifacts' / 'gui-test-tmp'
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as folder:
            destination = Path(folder) / 'reports.json'
            for component in (1, 0, 1):
                w.component_combo.setCurrentIndex(component)
                self.assertIs(w.current, result)
                self.assertEqual(w._current_analysis(), reports)
                self.assertEqual(w.analysis_table.rowCount(), rows)
                self.assertEqual(w.analysis_summary.text(), summary)
                # Live plot is the selected signed component, with no analysis overlays.
                curves = w.plot.listDataItems()
                self.assertEqual(len(curves), 1)
                np.testing.assert_array_equal(curves[0].getData()[1], result.data[component])
            with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(destination), '')):
                w._save_analysis_dialog()
            self.assertEqual(json.loads(destination.read_text()), reports)
        w.process_now()
        self.assertEqual(w.analysis_reports, {})

    def test_analysis_preview_refuses_but_component_changes_preserve_reports(self):
        w = self.window
        w.demo1_button.click()
        w.process_now()
        w.analyze_now('peaks')
        reports = w._current_analysis()
        w.component_combo.setCurrentIndex(1)
        self.assertEqual(w._current_analysis(), reports)
        w.process_now(preview=True)
        self.assertFalse(w.analysis_panel.isEnabled())
        with self.assertRaises(NMRNextError):
            w.analyze_now('peaks')

    def test_preview_is_not_savable_until_committed(self):
        self.window.demo1_button.click()
        self.window.process_now(preview=True)
        self.assertFalse(self.window.export_button.isEnabled())
        self.assertFalse(self.window.recipe_button.isEnabled())
        self.assertFalse(self.window.workflow_button.isEnabled())
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
            ("first point", self.window.first_point_spin, lambda w: w.setValue(.5)),
            ("zero fill", self.window.zf_combo, lambda w: w.setCurrentIndex(2)),
            ("transform", self.window.fft_check, lambda w: w.setChecked(False)),
            ("zero-order phase", self.window.p0_spin, lambda w: w.setValue(65)),
            ("first-order phase", self.window.p1_spin, lambda w: w.setValue(30)),
            ("phase pivot", self.window.pivot_spin, lambda w: w.setValue(5)),
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
        self.window.p0_spin.setValue(47)
        old_recipe = self.window.build_recipe()
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
        self.assertEqual(self.window.build_recipe(), old_recipe)
        self.assertEqual(self.window.p0_spin.value(), 47)
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
