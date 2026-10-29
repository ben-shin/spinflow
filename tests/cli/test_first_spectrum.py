import contextlib
import io
import json
from pathlib import Path

import numpy as np

from nmrnext import cli
from nmrnext.io import read_dataset


def invoke(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main([str(arg) for arg in args])
    return code, out.getvalue(), err.getvalue()


def test_first_spectrum_replays_and_corrects_known_phase(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out, err = invoke('first-spectrum', 'first')
    assert code == 0, err
    assert json.loads(out)['synthetic'] is True
    folder = Path('first')
    raw = read_dataset(folder / 'synthetic-raw.nmrh5')
    assert raw.metadata['synthetic'] is True
    before = read_dataset(folder / 'processed.nmrh5').data
    after = read_dataset(folder / 'phased.nmrh5').data
    angle = np.deg2rad(35)
    # Check both quadratures independently against the documented rotation.
    np.testing.assert_allclose(after[0], before[0]*np.cos(angle) + before[1]*np.sin(angle), atol=1e-12)
    np.testing.assert_allclose(after[1], before[1]*np.cos(angle) - before[0]*np.sin(angle), atol=1e-12)
    report = json.loads((folder / 'analysis.json').read_text())
    assert sorted((p['points']['t2'], p['sign']) for p in report['peaks']['peaks']) == [(224, 'negative'), (320, 'positive')]
    assert len(report['integrals']['regions']) == 2
    source_bytes = (folder / 'synthetic-raw.nmrh5').read_bytes()
    for name, result in [('process', 'processed'), ('phase', 'phased')]:
        recipe = json.loads((folder / f'{name}.json').read_text())
        recipe['output'] = f'{result}-replay.nmrh5'
        (folder / f'{name}-replay.json').write_text(json.dumps(recipe))
        code, _, err = invoke('run', folder / f'{name}-replay.json')
        assert code == 0, err
        np.testing.assert_array_equal(read_dataset(folder / recipe['output']).data, read_dataset(folder / f'{result}.nmrh5').data)
    assert (folder / 'synthetic-raw.nmrh5').read_bytes() == source_bytes
    assert (folder / 'analysis.csv').is_file()


def test_first_spectrum_refuses_existing_directory_without_changes(tmp_path):
    folder = tmp_path / 'existing'
    folder.mkdir()
    sentinel = folder / 'sentinel'
    sentinel.write_bytes(b'keep')
    code, _, err = invoke('first-spectrum', folder)
    assert code == 2
    assert json.loads(err)['error']['code'] == 'OUTPUT_EXISTS'
    assert sentinel.read_bytes() == b'keep'
    assert list(folder.iterdir()) == [sentinel]
