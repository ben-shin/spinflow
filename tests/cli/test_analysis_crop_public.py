"""Opt-in crop, digest, mask and analysis workflow on the public spectrum."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_public_large_spectrum_crop_then_mask_workflow(tmp_path):
    root = os.environ.get('SPINFLOW_VARIAN_ROOT')
    if not root:
        pytest.skip('Set SPINFLOW_VARIAN_ROOT after fetching public data')
    source = Path(root)/'processed_2d/plot_2d/nmrpipe_2d/test.ft2'
    sha = lambda: hashlib.sha256(source.read_bytes()).hexdigest()
    before = sha()
    assert before == '7baaaf03137561a39a362ee35dfff0c5a7e480c3c2e0e9fdfe9befc7f6c503b8'
    example = Path(__file__).resolve().parents[2]/'examples/hard-mask-crop.json'
    recipe = json.loads(example.read_text())
    crop = tmp_path/'crop.nmrh5'
    recipe.update(input=str(source), output=str(crop))
    recipe_file = tmp_path/'crop.json'
    recipe_file.write_text(json.dumps(recipe))
    def run(*args):
        return subprocess.run([sys.executable, '-m', 'nmrnext', *map(str, args)],
                              text=True, capture_output=True, timeout=60)
    completed = run('run', recipe_file)
    assert completed.returncode == 0, completed.stderr
    initial = tmp_path/'initial.json'
    completed = run('peaks', crop, '--output', initial, '--threshold', 30000, '--memory', '512MiB')
    assert completed.returncode == 0, completed.stderr
    digest = json.loads(initial.read_text())['dataset_digest']
    mask = {'dataset_digest': digest, 'regions': [
        {'a0': [0, 255], 'a1': [1264, 1535]},
        {'a0': [768, 1023], 'a1': [1264, 1535]}]}
    mask_file = tmp_path/'mask.json'
    mask_file.write_text(json.dumps(mask))
    output = tmp_path/'peaks.json'
    completed = run('peaks', crop, '--output', output, '--noise-mask', mask_file,
                    '--snr', 6, '--sign', 'positive', '--baseline', 'none',
                    '--min-separation', 4, '--memory', '512MiB')
    assert completed.returncode == 0, completed.stderr
    result = json.loads(output.read_text())
    assert result['mask_selections']['noise'] == mask
    assert result['noise']['sample_count'] == 139264
    saved = output.read_bytes()
    assert run('peaks', crop, '--output', output, '--noise-mask', mask_file,
               '--memory', '512MiB').returncode != 0
    assert output.read_bytes() == saved and sha() == before
