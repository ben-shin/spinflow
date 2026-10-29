"""Opt-in actual acquisition and GUI-to-CLI replay; public source stays untouched."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import pytest
import nmrglue as ng
from PySide6 import QtWidgets

from nmrnext.gui import Workbench
from nmrnext.io import read_dataset


@pytest.mark.parametrize('index', [0, 1])
def test_public_acquisition_dialog_process_reference_and_cli_replay(tmp_path, index):
    root = os.environ.get('SPINFLOW_VARIAN_ROOT')
    if not root:
        pytest.skip('Set SPINFLOW_VARIAN_ROOT after explicit public-data fetch')
    manifest = json.loads((Path(__file__).resolve().parents[1]/'fixtures/varian_public_manifest.json').read_text())
    case = manifest['cases'][index]
    folder = Path(root)/case['raw']
    def hashes():
        return {name: hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in ('fid', 'procpar')}
    before = hashes()
    entries = {entry['path']: entry['sha256'] for archive in manifest['archives'] for entry in archive['members']}
    assert before == {name: entries[f"{case['raw']}/{name}"] for name in ('fid', 'procpar')}
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Workbench()
    try:
        with patch.object(QtWidgets.QFileDialog, 'getExistingDirectory', return_value=str(folder)), \
             patch.object(window, '_start_job', side_effect=lambda work, accept, label: accept(work())):
            window._open_bruker()
        source = window.source
        assert list(source.data.shape) == case['shape']
        assert all(axis.reference_ppm is None for axis in source.axes)
        assert not window.filter_check.isEnabled()
        preview = window.process_now(preview=True)
        assert all(axis.reference_ppm is None for axis in preview.axes)
        assert 'reference is unknown' in window.plot_note.text()
        for axis_index, (reference, frequency) in enumerate(zip(case['reference_ppm'], case['reference_observation_mhz'])):
            window.axis_combo.setCurrentIndex(axis_index + 1)
            window.reference_ppm_spin.setValue(reference)
            window.reference_mhz_spin.setValue(frequency)
            window.reference_check.setChecked(True)
            window.reference_reverse_check.setChecked(True)
        result = window.process_now()
        assert len(result.complex_axes) == len(source.complex_axes)
        assert [axis.reference_ppm for axis in result.axes] == case['reference_ppm']
        assert all(op.get('frequency_scale') == -1 for op in window.build_recipe() if op['op'] == 'reference')
        reference_header, reference_data = ng.pipe.read(str(Path(root)/case['processed_reference']))
        for dim, axis in enumerate(result.axes):
            reference_grid = ng.pipe.make_uc(reference_header, reference_data, dim=dim).ppm_scale()
            reference_size = reference_data.shape[dim]
            indices = ((np.arange(axis.size) - axis.size//2) * (reference_size / axis.size)
                       + reference_size//2)
            expected = np.interp(indices, np.arange(reference_size), reference_grid)
            np.testing.assert_allclose(result.coordinates(axis.id, 'ppm'), expected, rtol=0, atol=1e-4)
        workflow = window.export_workflow_now(tmp_path/'workflow')
        completed = subprocess.run([sys.executable, '-m', 'nmrnext', 'run', str(workflow)],
                                   text=True, capture_output=True, timeout=60)
        assert completed.returncode == 0, completed.stderr
        recipe = json.loads(workflow.read_text())
        replay = read_dataset(workflow.parent/recipe['output'])
        np.testing.assert_array_equal(replay.data, result.data)
        assert replay.to_metadata() == result.to_metadata()
        assert hashes() == before
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
