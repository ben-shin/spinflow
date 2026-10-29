"""Fresh-process shared-codec readback; external application reopening remains unverified."""
import json
import os
import subprocess
import sys

import numpy as np
import pytest

from nmrnext.io import write_dataset
from nmrnext.model import Axis, Dataset


@pytest.mark.parametrize('suffix,reader', [('.ft2', 'pipe'), ('.ucsf', 'sparky')])
def test_cli_export_reopened_in_fresh_nmrglue_process(tmp_path, suffix, reader):
    axes = (Axis('n', 4, domain='frequency', unit='Hz', start=50, step=-5,
                 observation_mhz=50, reference_ppm=100, label='N15', encoding='real'),
            Axis('h', 6, domain='frequency', unit='Hz', start=100, step=-10,
                 observation_mhz=500, reference_ppm=4, label='H1', encoding='real'))
    data = np.arange(24, dtype=np.float32).reshape(1, 4, 6)
    source = tmp_path/'input.nmrh5'
    output = tmp_path/('export' + suffix)
    write_dataset(Dataset(data, axes), source)
    env = os.environ.copy()
    export = subprocess.run([sys.executable, '-m', 'nmrnext', 'convert', str(source), str(output)],
                            capture_output=True, text=True, env=env, timeout=30)
    assert export.returncode == 0, export.stderr
    script = """import json, sys, nmrglue as ng
codec = getattr(ng, sys.argv[2])
header, values = codec.read(sys.argv[1])
print(json.dumps({'data': values.tolist(), 'axes': [codec.make_uc(header, values, dim=i).ppm_scale().tolist() for i in (0, 1)]}))
"""
    consumer = subprocess.run([sys.executable, '-I', '-c', script, str(output), reader],
                              capture_output=True, text=True, timeout=30)
    assert consumer.returncode == 0, consumer.stderr
    decoded = json.loads(consumer.stdout)
    np.testing.assert_array_equal(decoded['data'], data[0])
    np.testing.assert_allclose(decoded['axes'][0], [101, 100.9, 100.8, 100.7], rtol=0, atol=1e-4)
    np.testing.assert_allclose(decoded['axes'][1], [4.2, 4.18, 4.16, 4.14, 4.12, 4.1], rtol=0, atol=1e-4)
