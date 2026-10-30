"""A loaded ordered recipe and an applied GUI session remain reproducible."""
import json
import os
from pathlib import Path
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6 import QtCore, QtWidgets
from unittest.mock import patch

from nmrnext.demo import generate_demo
from nmrnext.errors import NMRNextError
from nmrnext.gui import StepDialog, Workbench
from nmrnext.operations import run_operations


@pytest.fixture
def workbench():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Workbench()
    yield window
    window.close()
    window.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()


def _complex_2d_source():
    from dataclasses import replace
    from nmrnext.model import Dataset
    old = generate_demo(dimensions=2, points=[8, 16])
    axes = tuple(replace(axis, id=f'a{i}') for i, axis in enumerate(old.axes))
    return Dataset(old.data, axes, ('a0', 'a1'))


def _ordered_recipe(source):
    # All operations in the public laboratory recipe, with small transform grids.
    record = json.loads((Path(__file__).resolve().parents[2] / 'examples' / 'laboratory-workflow.json').read_text())
    ops = record['operations']
    ops[1]['size'] = 32
    ops[8]['size'] = 16
    return ops


def test_step_form_noop_preserves_optional_parameter_types(workbench):
    for operation in ({'op': 'fft', 'axis': 'a1', 'inverse': False, 'version': '1'},
                      {'op': 'reference', 'axis': 'a1', 'reference_ppm': 55.0,
                       'reference_hz': 0, 'observation_mhz': 125.691,
                       'frequency_scale': -1, 'spectral_width_hz': 50000}):
        assert StepDialog(operation, workbench).operation() == operation


def test_transpose_basic_order_accepts_plain_axis_names(workbench):
    source = _complex_2d_source()
    workbench.load_dataset_now(source)
    dialog = StepDialog({'op': 'transpose', 'order': ['a1', 'a0']}, workbench)
    field = dialog.fields['order']
    field.setText('a0, a1')
    assert dialog.operation()['order'] == ['a0', 'a1']
    field.setText('0, 1')
    assert dialog.operation()['order'] == ['a0', 'a1']
    operations = _ordered_recipe(source)
    workbench.set_ordered_recipe_now(operations)
    invalid = json.loads(json.dumps(operations))
    invalid[6]['order'] = ['a0', 'a0']
    with pytest.raises(NMRNextError) as caught:
        workbench.set_ordered_recipe_now(invalid)
    assert caught.value.code == 'INVALID_AXIS_ORDER'
    assert workbench.build_recipe() == operations
    workbench.ordered_steps.setCurrentRow(6)
    with patch.object(StepDialog, 'exec', return_value=QtWidgets.QDialog.DialogCode.Rejected):
        workbench._edit_step_dialog()
    assert workbench.build_recipe() == operations


def test_loaded_full_recipe_retains_order_parameters_and_replays(workbench, tmp_path):
    source = _complex_2d_source()
    ops = _ordered_recipe(source)
    from nmrnext.io import write_dataset, read_dataset
    write_dataset(source, tmp_path / 'source.nmrh5', format='native')
    recipe = {'schema_version': '1', 'input': 'source.nmrh5', 'output': 'result.nmrh5',
              'input_format': 'native', 'output_format': 'native', 'backend': 'native',
              'memory_bytes': 256 * 1024**2, 'operations': ops}
    path = tmp_path / 'workflow.json'
    path.write_text(json.dumps(recipe))
    workbench.load_recipe_now(path)
    assert workbench.build_recipe() == ops
    assert workbench.ordered_steps.count() == 14
    assert 'sine' in workbench.ordered_steps.item(0).text()
    assert 'transpose' in workbench.ordered_steps.item(6).text().lower()
    for index in (0, 3, 5, 6, 11):
        workbench.ordered_steps.setCurrentRow(index)
        with patch.object(StepDialog, 'exec', return_value=QtWidgets.QDialog.DialogCode.Accepted):
            workbench._edit_step_dialog()
        assert workbench.build_recipe() == ops
    result = workbench.process_now()
    expected = run_operations(source, ops, backend='native', memory_bytes=256 * 1024**2)
    np.testing.assert_array_equal(result.data, expected.data)
    assert result.to_metadata() == expected.to_metadata()
    bundle = workbench.export_workflow_now(tmp_path / 'bundle')
    assert json.loads(bundle.read_text())['operations'] == ops
    assert read_dataset(bundle.parent / 'source.nmrh5').to_metadata() == source.to_metadata()


def test_invalid_recipe_load_and_edit_preserve_applied_result(workbench, tmp_path):
    source = _complex_2d_source()
    workbench.load_dataset_now(source)
    good = workbench.process_now()
    before = (workbench.source, workbench.current, workbench.build_recipe(), workbench.last_operations)
    bad = {'schema_version': '1', 'input': 'absent.nmrh5', 'output': 'out.nmrh5',
           'operations': [{'op': 'transpose', 'order': ['a0', 'missing']}]}
    path = tmp_path / 'bad.json'
    path.write_text(json.dumps(bad))
    with pytest.raises(Exception):
        workbench.load_recipe_now(path)
    assert (workbench.source, workbench.current, workbench.build_recipe(), workbench.last_operations) == before
    assert workbench.current is good
    with pytest.raises(NMRNextError):
        workbench.set_ordered_recipe_now([{'op': 'transpose', 'order': ['a0', 'missing']}])
    assert workbench.current is good


def test_session_close_reopen_restores_applied_recipe_and_reports(workbench, tmp_path):
    source = _complex_2d_source()
    workbench.load_dataset_now(source, 'Selected source')
    ops = _ordered_recipe(source)
    workbench.set_ordered_recipe_now(ops)
    result = workbench.process_now()
    path = workbench.save_session_now(tmp_path / 'session')
    assert path.exists()
    replacement = generate_demo(dimensions=1, points=[64])
    workbench.load_dataset_now(replacement)
    workbench.open_session_now(path.parent)
    assert workbench.source_name == 'Selected source'
    assert workbench.build_recipe() == ops
    assert workbench.last_operations == ops
    np.testing.assert_array_equal(workbench.current.data, result.data)
    assert workbench.current.to_metadata() == result.to_metadata()


def test_large_applied_spectrum_analyzes_bounded_region_and_restores_it(workbench, tmp_path):
    from nmrnext.model import Axis, Dataset
    values = np.zeros((1, 2048, 4096), dtype=np.float32)
    values[0, 1007, 1998] = 9
    parent = Dataset(values, [
        Axis('n', 2048, domain='frequency', unit='Hz', start=1000, step=-.5,
             observation_mhz=50, reference_ppm=120, reference_hz=0),
        Axis('h', 4096, domain='frequency', unit='Hz', start=-2000, step=1.25,
             observation_mhz=500, reference_ppm=4.7, reference_hz=0)])
    workbench.load_dataset_now(parent, 'Full-size result')
    workbench.memory_spin.setValue(512)
    workbench.region_bounds['n'][0].setValue(1000)
    workbench.region_bounds['n'][1].setValue(1015)
    workbench.region_bounds['h'][0].setValue(1980)
    workbench.region_bounds['h'][1].setValue(2010)
    workbench.analysis_region_check.setChecked(True)
    workbench.threshold_check.setChecked(True)
    workbench.threshold_spin.setValue(1)
    peaks = workbench.analyze_now('peaks')
    assert peaks['peaks'][0]['parent_points'] == {'n': 1007, 'h': 1998}
    assert peaks['parent_region']['parent_start'] == {'n': 1000, 'h': 1980}
    workbench.baseline_combo.setCurrentText('none')
    integral = workbench.analyze_now('integrate')
    assert integral['regions'][0]['parent_point_bounds'] == {'n': [1000, 1015], 'h': [1980, 2010]}
    saved = workbench.save_session_now(tmp_path / 'large-session')
    workbench.load_dataset_now(generate_demo(dimensions=1, points=[64]))
    workbench.memory_spin.setValue(512)
    workbench.open_session_now(saved)
    assert workbench.analysis_region_check.isChecked()
    assert workbench.selected_regions == [{'bounds': {'n': [1000.0, 1015.0], 'h': [1980.0, 2010.0]}, 'unit': 'points'}]
    assert workbench._current_analysis()['peaks'] == peaks
    assert workbench._current_analysis()['integrate'] == integral


def test_invalid_session_preserves_prior_applied_view(workbench, tmp_path):
    source = _complex_2d_source()
    workbench.load_dataset_now(source)
    workbench.set_ordered_recipe_now(_ordered_recipe(source))
    workbench.process_now()
    applied = workbench.current
    before = workbench.build_recipe()
    saved = workbench.save_session_now(tmp_path / 'session')
    manifest = json.loads(saved.read_text())
    manifest['result_dataset_digest'] = '0' * 64
    saved.write_text(json.dumps(manifest))
    with pytest.raises(NMRNextError):
        workbench.open_session_now(saved)
    assert workbench.current is applied
    assert workbench.build_recipe() == before


def test_malformed_report_with_updated_checksum_cannot_replace_prior_view(workbench, tmp_path):
    from nmrnext.session import _reports_hash
    workbench.load_dataset_now(generate_demo(dimensions=1, points=[64]))
    workbench.process_now()
    workbench.analyze_now('peaks')
    saved = workbench.save_session_now(tmp_path / 'reported')
    manifest = json.loads(saved.read_text())
    manifest['reports']['peaks']['flags'] = [7]
    manifest['reports_sha256'] = _reports_hash(manifest['reports'])
    saved.write_text(json.dumps(manifest))
    prior = _complex_2d_source()
    workbench.load_dataset_now(prior, 'Prior good source')
    applied = workbench.current
    with pytest.raises(NMRNextError):
        workbench.open_session_now(saved)
    assert workbench.source is prior
    assert workbench.current is applied
    assert workbench.source_name == 'Prior good source'


def test_valid_session_final_render_failure_rolls_back_complete_view(workbench, tmp_path):
    from nmrnext.model import Axis, Dataset
    incoming = _complex_2d_source()
    workbench.load_dataset_now(incoming, 'Incoming')
    workbench.set_ordered_recipe_now(_ordered_recipe(incoming))
    workbench.process_now()
    saved = workbench.save_session_now(tmp_path / 'incoming-session')
    prior = Dataset(np.array([[0., 1., 9., 3., 0., 0., 0., 0.]]),
                    [Axis('prior', 8, domain='frequency', unit='Hz')])
    workbench.load_dataset_now(prior, 'Prior good source')
    workbench.analyze_now('peaks')
    workbench.memory_spin.setValue(24)
    workbench.view_combo.setCurrentIndex(1)
    workbench.plot.getPlotItem().getViewBox().setXRange(1, 5, padding=0)
    old_range = workbench.plot.getPlotItem().getViewBox().viewRange()
    before = (workbench.source, workbench.current, workbench.preview,
              workbench.source_name, workbench.build_recipe(), workbench.last_operations,
              workbench.analysis_reports.copy(), workbench.memory_spin.value(),
              workbench.view_combo.currentIndex(), workbench.plot_caption.text(),
              workbench.status.text(), workbench._recipe_cache)
    curve = workbench.plot.listDataItems()[0].getData()[1].copy()
    original_render = workbench._render
    calls = 0
    def fail_final():
        nonlocal calls
        calls += 1
        if calls == 2:
            workbench.plot.clear()
            raise RuntimeError('injected final render failure')
        return original_render()
    with patch.object(workbench, '_render', side_effect=fail_final):
        with pytest.raises(RuntimeError, match='final render failure'):
            workbench.open_session_now(saved)
    after = (workbench.source, workbench.current, workbench.preview,
             workbench.source_name, workbench.build_recipe(), workbench.last_operations,
             workbench.analysis_reports.copy(), workbench.memory_spin.value(),
             workbench.view_combo.currentIndex(), workbench.plot_caption.text(),
             workbench.status.text(), workbench._recipe_cache)
    assert after == before
    np.testing.assert_array_equal(workbench.plot.listDataItems()[0].getData()[1], curve)
    assert workbench.plot.getPlotItem().getViewBox().viewRange() == old_range


def test_visible_region_refuses_original_and_preview_frames(workbench):
    source = generate_demo(dimensions=1, points=[128])
    workbench.load_dataset_now(source)
    workbench.zf_combo.setCurrentIndex(1)
    workbench.process_now()
    workbench.view_combo.setCurrentIndex(1)
    workbench.plot.getPlotItem().getViewBox().setXRange(2, 8, padding=0)
    before = workbench.selected_regions.copy()
    workbench._use_plot_region()
    assert workbench.selected_regions == before
    assert not workbench.use_plot_region_button.isEnabled()
    workbench.view_combo.setCurrentIndex(0)
    values, _ = workbench._axis_values(workbench.current, workbench.current.axes[0])
    lo, hi = sorted((float(values[20]), float(values[40])))
    epsilon = abs(float(values[1] - values[0])) * .01
    workbench.plot.getPlotItem().getViewBox().setXRange(lo - epsilon, hi + epsilon, padding=0)
    workbench._use_plot_region()
    assert workbench.selected_regions == [{'bounds': {'t2': [20.0, 40.0]}, 'unit': 'points'}]
    selected = workbench.selected_regions.copy()
    workbench.process_now(preview=True)
    workbench._use_plot_region()
    assert workbench.selected_regions == selected


def test_visible_region_maps_actual_current_display_with_mixed_axis_units(workbench):
    from nmrnext.model import Axis, Dataset
    source = Dataset(np.zeros((1, 16, 32), dtype=np.float32), [
        Axis('n', 16, domain='frequency', unit='Hz', start=100, step=-1,
             observation_mhz=50, reference_ppm=120, reference_hz=0),
        Axis('h', 32, domain='frequency', unit='Hz', start=-20, step=2)])
    workbench.load_dataset_now(source)
    assert workbench.use_plot_region_button.isEnabled()
    view = workbench.plot.getPlotItem().getViewBox()
    x, _ = workbench._axis_values(source, source.axes[1])
    y, _ = workbench._axis_values(source, source.axes[0])
    view.setXRange(x[8] - .01, x[12] + .01, padding=0)
    view.setYRange(y[6] - .0001, y[3] + .0001, padding=0)
    workbench._use_plot_region()
    assert workbench.selected_regions == [{'bounds': {'n': [3.0, 6.0], 'h': [8.0, 12.0]},
                                           'unit': 'points'}]
    assert workbench.analysis_region_check.isChecked()
    workbench.current = Dataset(np.zeros((1, 16, 32), dtype=np.float32), source.axes)
    before = workbench.selected_regions.copy()
    workbench._use_plot_region()
    assert workbench.selected_regions == before


@pytest.mark.parametrize('stage', ['ordered_install', 'report_display'])
def test_session_install_stage_failure_restores_pending_preview_and_cache(workbench, tmp_path, stage):
    incoming = _complex_2d_source()
    workbench.load_dataset_now(incoming)
    workbench.set_ordered_recipe_now(_ordered_recipe(incoming))
    workbench.process_now()
    saved = workbench.save_session_now(tmp_path / 'incoming')
    prior = generate_demo(dimensions=1, points=[64])
    workbench.load_dataset_now(prior, 'Prior')
    workbench.lb_spin.setValue(4)
    workbench.process_now()
    workbench.analyze_now('peaks')
    workbench.region_bounds['t2'][0].setValue(5)
    workbench.region_bounds['t2'][1].setValue(25)
    workbench._select_entered_region()
    workbench.process_now(preview=True)
    old_source, old_current, old_preview = workbench.source, workbench.current, workbench.preview
    old_cache = workbench._recipe_cache
    old_ops = workbench.build_recipe()
    old_report = workbench.analysis_reports.copy()
    old_caption = workbench.plot_caption.text()
    old_region = workbench.selected_regions.copy()
    old_bounds = workbench._entered_region()
    if stage == 'ordered_install':
        original = workbench._install_ordered
        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('ordered install failure')
        patcher = patch.object(workbench, '_install_ordered', side_effect=fail)
    else:
        original = workbench._display_analysis
        calls = 0
        def fail():
            nonlocal calls
            calls += 1
            if calls == 2:
                workbench.analysis_table.setRowCount(0)
                raise RuntimeError('report display failure')
            return original()
        patcher = patch.object(workbench, '_display_analysis', side_effect=fail)
    with patcher:
        with pytest.raises(RuntimeError):
            workbench.open_session_now(saved)
    assert workbench.source is old_source
    assert workbench.current is old_current
    assert workbench.preview is old_preview
    assert workbench._recipe_cache is old_cache
    assert workbench.build_recipe() == old_ops
    assert workbench.analysis_reports == old_report
    assert workbench.selected_regions == old_region
    assert workbench._entered_region() == old_bounds
    assert workbench.plot_caption.text() == old_caption


def test_cancelled_open_dialogs_preserve_applied_view(workbench):
    source = _complex_2d_source()
    workbench.load_dataset_now(source)
    workbench.set_ordered_recipe_now(_ordered_recipe(source))
    applied = workbench.process_now()
    before = workbench.build_recipe()
    with patch.object(QtWidgets.QFileDialog, 'getOpenFileName', return_value=('', '')):
        workbench._load_recipe_dialog()
    with patch.object(QtWidgets.QFileDialog, 'getExistingDirectory', return_value=''):
        workbench._open_session_dialog()
    assert workbench.current is applied
    assert workbench.build_recipe() == before


def test_stale_async_session_completion_cannot_replace_newer_source(workbench, tmp_path):
    workbench.load_dataset_now(generate_demo(dimensions=1, points=[64]))
    workbench.process_now()
    saved = workbench.save_session_now(tmp_path / 'session')
    with patch.object(QtWidgets.QFileDialog, 'getExistingDirectory', return_value=str(saved.parent)), \
         patch.object(workbench, '_start_job') as start:
        workbench._open_session_dialog()
    work, done, _ = start.call_args.args
    newer = generate_demo(dimensions=1, points=[32])
    workbench.load_dataset_now(newer, 'Newer source')
    with pytest.raises(NMRNextError) as caught:
        done(work())
    assert caught.value.code == 'STALE_JOB'
    assert workbench.current is newer
    assert workbench.source_name == 'Newer source'


def test_cached_public_acquisition_full_lab_recipe_gui_session(workbench, tmp_path):
    project = Path(__file__).resolve().parents[2]
    acquisition = project / '.artifacts' / 'coverage-formats' / '2d' / 'agilent2pipe_2d' / 'agilent_2d'
    if not (acquisition / 'fid').exists():
        pytest.skip('Cached public acquisition is not part of the source distribution')
    recipe = json.loads((project / 'examples' / 'laboratory-workflow.json').read_text())
    recipe['input'] = str(acquisition)
    path = tmp_path / 'lab.json'
    path.write_text(json.dumps(recipe))
    workbench.load_recipe_now(path)
    assert workbench.build_recipe() == recipe['operations']
    result = workbench.process_now()
    assert result.data.shape == (1, 2048, 4096)
    saved = workbench.save_session_now(tmp_path / 'session')
    workbench.load_dataset_now(generate_demo(dimensions=1, points=[64]))
    workbench.memory_spin.setValue(1024)
    workbench.open_session_now(saved)
    np.testing.assert_array_equal(workbench.current.data, result.data)
    assert workbench.current.to_metadata() == result.to_metadata()
    assert workbench.build_recipe() == recipe['operations']
    bundle = workbench.export_workflow_now(tmp_path / 'cli-replay')
    command = subprocess.run([sys.executable, '-m', 'nmrnext.cli', 'run', str(bundle)],
                             capture_output=True, text=True)
    assert command.returncode == 0, command.stderr
    from nmrnext.io import export_preflight, read_dataset
    replay = read_dataset(bundle.parent / 'result.nmrh5')
    np.testing.assert_array_equal(replay.data, result.data)
    assert replay.to_metadata() == result.to_metadata()
    assert export_preflight(replay, 'nmrpipe')['losses'] == workbench.preflight_export('nmrpipe')['losses']
