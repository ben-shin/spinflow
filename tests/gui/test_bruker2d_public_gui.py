"""Opt-in acquired-data controller checks; ordinary GUI tests stay offline."""
import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest
from PySide6 import QtWidgets

from nmrnext.gui import Workbench
from nmrnext.io import read_dataset
from nmrnext.operations import run_operations

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('case_index', [0, 1])
def test_public_raw_directory_controls_canonical_preprocessing_and_both_axis_phase(tmp_path, case_index):
    cache = os.environ.get('SPINFLOW_BRUKER2D_ROOT')
    if not cache:
        pytest.skip('Set SPINFLOW_BRUKER2D_ROOT after explicit public-data fetch')
    spec = importlib.util.spec_from_file_location('public_2d_gui_validator', REPO/'tools/validate_public_bruker2d.py')
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    manifest = json.loads(validator.MANIFEST.read_text())
    root = Path(cache)
    validator.verify_sources(root, manifest)
    case = manifest['cases'][case_index]
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Workbench()
    try:
        # Exercise the actual Bruker dialog dispatch and directory auto-detection.
        with patch.object(QtWidgets.QFileDialog, 'getExistingDirectory', return_value=str(root/case['path'])), \
             patch.object(window, '_start_job', side_effect=lambda work, accept, label: accept(work())):
            window._open_bruker()
        source = window.source
        assert source.data.shape[0:2] == (4, 128)
        assert window.filter_check.isEnabled()
        window.filter_check.setChecked(True)
        canonical = validator.raw_recipe(root, case)
        for index, axis in enumerate(source.axes):
            window.axis_combo.setCurrentIndex(index+1)
            by_op = {op['op']: op for op in canonical if op['axis'] == axis.id}
            window.lb_spin.setValue(by_op['window']['lb_hz'])
            window.first_point_spin.setValue(.5)
            factor = 1024//axis.size
            window.zf_combo.setCurrentIndex((1, 2, 4, 8).index(factor))
            reference = by_op['reference']
            window.reference_ppm_spin.setValue(reference['reference_ppm'])
            window.reference_mhz_spin.setValue(reference['observation_mhz'])
            window.reference_check.setChecked(True)
        recipe = window.build_recipe()
        assert len([op for op in recipe if op['op'] == 'digital_filter']) == 1
        assert recipe[0] == {'op': 'digital_filter', 'axis': 'a1'}
        assert [op['first_point_scale'] for op in recipe if op['op'] == 'window'] == [.5, .5]
        window.memory_spin.setValue(512)
        applied = window.process_now()
        assert window.last_memory_bytes == 512 * 1024**2
        expected = run_operations(source, canonical, backend='native')
        np.testing.assert_allclose(applied.data, expected.data, rtol=1e-10, atol=1e-6)
        assert window.autophase_button.isEnabled()
        window.first_order_check.setChecked(case['autophase_first_order'])
        result, report = window.analyze_now('autophase')
        assert {op['axis'] for op in report['operations']} == {'a0', 'a1'}
        assert report['parameters']['first_order'] == case['autophase_first_order']
        assert max(report['objective']['per_line_relative_rms']) <= .04
        replay = run_operations(source, window.last_operations, backend='native')
        np.testing.assert_array_equal(replay.data, result.data)
        window.component_combo.setCurrentIndex(3)
        window.view_combo.setCurrentIndex(1)
        window.view_combo.setCurrentIndex(0)
        assert window._current_analysis()['autophase'] == report
        bundle = window.export_workflow_now(tmp_path/'workflow')
        saved = json.loads(bundle.read_text())
        assert saved['operations'] == recipe+report['operations']
        assert saved['memory_bytes'] == 512 * 1024**2
        assert read_dataset(bundle.parent/'source.nmrh5').to_metadata() == source.to_metadata()
        validator.verify_sources(root, manifest)
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
