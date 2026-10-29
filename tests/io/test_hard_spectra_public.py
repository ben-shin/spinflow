"""Public hard-spectrum checks require an explicitly populated offline cache."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO=Path(__file__).resolve().parents[2]


def test_public_protein_picker_and_reference_baseline_volume():
    root=os.environ.get('SPINFLOW_VARIAN_ROOT')
    if not root:
        pytest.skip('Set SPINFLOW_VARIAN_ROOT to an explicitly fetched public Varian cache')
    result=subprocess.run([sys.executable,str(REPO/'tools/validate_public_hard_spectra.py'),
                           '--root',str(Path(root).resolve())],cwd=REPO,capture_output=True,text=True,check=True)
    report=json.loads(result.stdout)
    assert report['pass'] and report['source_untouched']
    assert report['phase']['code']=='UNSUPPORTED_AUTOPHASE'
    assert len(report['cases'])==5
    assert all(case['integration_pass'] for case in report['cases'])
    assert report['noise']['sample_count']==139264
    assert report['protocol']['snr']==6 and report['protocol']['source_threshold']==30000
    # Actual production candidates (not ROI maxima) are retained for every externally authored box.
    assert all(len(case['six_mad_candidates'])==1 for case in report['cases'])
    assert all(len(case['source_threshold_candidates'])==1 for case in report['cases'])
