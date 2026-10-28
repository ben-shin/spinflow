"""Optional figure CLI contracts, exercised with [plot] and without Qt."""
import contextlib
import io
import json

import numpy as np

from nmrnext import cli
from nmrnext.io import write_dataset
from nmrnext.model import Axis, Dataset


def invoke(*args):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        status = cli.main(list(map(str, args)))
    return status, stdout.getvalue(), stderr.getvalue()


def spectrum(path):
    values = np.zeros((1, 64), dtype=np.float64)
    values[0, 16], values[0, 48] = 4., -3.
    write_dataset(Dataset(values, [Axis('f', 64, domain='frequency', unit='Hz',
                                        start=100., step=-2., observation_mhz=500.,
                                        reference_ppm=4.7)]), path, format='native')


def test_combined_peak_integral_report_renders_actual_svg(tmp_path):
    source = tmp_path/'spectrum.nmrh5'
    spectrum(source)
    analysis, output = tmp_path/'analysis.json', tmp_path/'figure.svg'
    status, _, err = invoke('peaks', source, '--output', analysis, '--sign', 'both',
                            '--threshold', '1', '--baseline', 'none', '--integrate',
                            '--half-width', '3')
    assert status == 0, err
    status, out, err = invoke('figure', source, output, '--analysis', analysis)
    assert status == 0, err
    assert output.read_bytes().startswith(b'<?xml')
    rendered = json.loads(out)
    assert rendered['path'] == str(output)
    assert rendered['peaks_in_report'] == 2
    assert rendered['analysis_digest']
    original = output.read_bytes()
    status, _, err = invoke('figure', source, output, '--analysis', analysis)
    assert status == 2 and json.loads(err)['error']['code'] == 'OUTPUT_EXISTS'
    assert output.read_bytes() == original


def test_figure_rejects_stale_member_of_combined_report(tmp_path):
    source = tmp_path/'spectrum.nmrh5'
    spectrum(source)
    analysis, output = tmp_path/'analysis.json', tmp_path/'figure.png'
    status, _, err = invoke('peaks', source, '--output', analysis, '--threshold', '1',
                            '--baseline', 'none', '--integrate', '--half-width', '3')
    assert status == 0, err
    payload = json.loads(analysis.read_text())
    payload['integrals']['dataset_digest'] = '0'*64
    analysis.write_text(json.dumps(payload))
    status, _, err = invoke('figure', source, output, '--analysis', analysis)
    assert status == 2 and json.loads(err)['error']['code'] == 'STALE_ANALYSIS'
    assert not output.exists()


def test_supported_thousand_peak_report_feeds_figure_and_proposal_integration(tmp_path):
    n = 128
    values = np.zeros((1, n, n), dtype=np.float64)
    values[0, 2:n-1:3, 2:n-1:3] = 2.
    source = tmp_path/'dense.nmrh5'
    write_dataset(Dataset(values, [Axis('a', n, domain='frequency', unit='Hz'),
                                   Axis('b', n, domain='frequency', unit='Hz')]),
                  source, format='native')
    report, figure = tmp_path/'analysis.json', tmp_path/'figure.svg'
    status, _, err = invoke('peaks', source, '--output', report, '--threshold', '1',
                            '--baseline', 'none', '--max-peaks', '1000',
                            '--integrate', '--half-width', '.4')
    assert status == 0, err
    assert report.stat().st_size > 1024*1024
    assert len(json.loads(report.read_text())['peaks']['peaks']) == 1000
    status, _, err = invoke('figure', source, figure, '--analysis', report)
    assert status == 0, err
    assert figure.read_bytes().startswith(b'<?xml')
    status, _, err = invoke('integrate', source, report, '--output', tmp_path/'reintegrated.json')
    assert status == 0, err
    assert len(json.loads((tmp_path/'reintegrated.json').read_text())['regions']) == 1000


def test_figure_rejects_stale_report_with_plot_installed(tmp_path):
    source = tmp_path/'spectrum.nmrh5'
    spectrum(source)
    report = tmp_path/'stale.json'
    report.write_text(json.dumps({'schema_version': '1', 'kind': 'peak_picking',
                                  'dataset_digest': '0'*64, 'peaks': []}))
    output = tmp_path/'stale.png'
    status, _, err = invoke('figure', source, output, '--analysis', report)
    assert status == 2 and json.loads(err)['error']['code'] == 'STALE_ANALYSIS'
    assert not output.exists()
