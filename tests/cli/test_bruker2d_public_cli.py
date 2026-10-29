"""Explicitly fetched public 2D data through the headless user commands."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path

import numpy as np
import pytest

from nmrnext import cli
from nmrnext.io import read_dataset
from nmrnext.operations import run_operations

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('experiment', ['hsqc', 'tocsy'])
def test_public_recipe_cli_auto_phase_and_native_replay(tmp_path, experiment):
    cache = os.environ.get('SPINFLOW_BRUKER2D_ROOT')
    if not cache:
        pytest.skip('Set SPINFLOW_BRUKER2D_ROOT after explicit public-data fetch')
    spec = importlib.util.spec_from_file_location('public_2d_cli_validator', REPO/'tools/validate_public_bruker2d.py')
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    manifest = json.loads(validator.MANIFEST.read_text())
    case = next(c for c in manifest['cases'] if c['experiment'].lower() == experiment)
    root = Path(cache)
    validator.verify_sources(root, manifest)
    recipe = json.loads((REPO/f'examples/bruker-2d-{experiment}-public.json').read_text())
    assert recipe['operations'] == validator.raw_recipe(root, case)
    recipe['input'] = str(root/case['path'])
    recipe['output'] = str(tmp_path/'transformed.nmrh5')
    workflow = tmp_path/'workflow.json'
    workflow.write_text(json.dumps(recipe), encoding='utf-8')
    output, report_path = tmp_path/'phased.nmrh5', tmp_path/'phase.json'
    with contextlib.redirect_stdout(io.StringIO()):
        assert cli.main(['run', str(workflow)]) == 0
        args = ['autophase', recipe['output'], str(output), '--report', str(report_path), '--memory', '512MiB']
        if case['autophase_first_order']:
            args += ['--first-order']
        assert cli.main(args) == 0
    report = json.loads(report_path.read_text())
    assert set(report['axis_results']) == {'a0', 'a1'}
    assert report['parameters']['first_order'] == case['autophase_first_order']
    assert max(report['objective']['per_line_relative_rms']) <= .04
    source = read_dataset(root/case['path'])
    expected = run_operations(source, recipe['operations']+report['operations'], backend='native')
    actual = read_dataset(output)
    np.testing.assert_array_equal(actual.data, expected.data)
    assert actual.to_metadata() == expected.to_metadata()
    validator.verify_sources(root, manifest)
