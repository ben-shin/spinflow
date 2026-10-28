"""End-to-end CLI contracts for bounded analysis and optional figures."""
import contextlib
import csv
import io
import json
import os
from pathlib import Path
from unittest import mock

import numpy as np

from nmrnext import cli
from nmrnext.analysis import dataset_digest
from nmrnext.errors import NMRNextError
from nmrnext.io import read_dataset, write_dataset
from nmrnext.model import Axis, Dataset


def invoke(*args):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        status = cli.main(list(map(str, args)))
    return status, stdout.getvalue(), stderr.getvalue()


def spectrum(path):
    values = np.zeros((1, 64), dtype=np.float64)
    values[0, 16], values[0, 48] = 4., -3.
    dataset = Dataset(values, [Axis('f', 64, domain='frequency', unit='Hz',
                                    start=100., step=-2., observation_mhz=500.,
                                    reference_ppm=4.7)])
    write_dataset(dataset, path, format='native')
    return dataset


def test_peaks_integral_json_csv(tmp_path):
    source = tmp_path/'spectrum.nmrh5'
    dataset = spectrum(source)
    report_path, csv_path = tmp_path/'analysis.json', tmp_path/'analysis.csv'
    status, out, err = invoke('peaks', source, '--sign', 'both', '--threshold', '1',
                              '--baseline', 'none', '--integrate', '--half-width', '3',
                              '--output', report_path, '--csv', csv_path)
    assert status == 0, err
    metadata = json.loads(out)
    assert metadata['dataset_digest'] == dataset_digest(dataset)
    assert metadata['output'] == str(report_path) and metadata['csv'] == str(csv_path)
    assert metadata['cleanup_warnings'] == []
    report = json.loads(report_path.read_text())
    assert set(report) == {'peaks', 'proposal', 'integrals'}
    assert all(entry['dataset_digest'] == dataset_digest(dataset) for entry in report.values())
    assert [peak['sign'] for peak in report['peaks']['peaks']] == ['positive', 'negative']
    assert all('requires_review' in r['flags'] for r in report['integrals']['regions'])
    with csv_path.open(newline='', encoding='utf-8') as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2
    assert [float(row['integral']) > 0 for row in rows] == [True, False]
    assert rows[0]['integral_unit'] == 'intensity*Hz'
    assert rows[0]['f_start_Hz'] and rows[0]['f_stop_Hz']
    assert rows[0]['f_ppm']

def test_manual_integration_requires_matching_proposal_and_refuses_overlap(tmp_path):
    source = tmp_path/'spectrum.nmrh5'
    dataset = spectrum(source)
    wrong = tmp_path/'wrong.json'
    wrong.write_text(json.dumps({'schema_version': '1', 'kind': 'region_proposal',
                                 'dataset_digest': '0'*64, 'regions': []}))
    output = tmp_path/'regions.json'
    status, _, err = invoke('integrate', source, wrong, '--output', output)
    assert status == 2 and json.loads(err)['error']['code'] == 'STALE_ANALYSIS'
    assert not output.exists()

    regions = [dict(id='a', bounds={'f': [10, 20]}, unit='points'),
               dict(id='b', bounds={'f': [18, 25]}, unit='points')]
    path = tmp_path/'manual.json'
    path.write_text(json.dumps(regions))
    status, _, err = invoke('integrate', source, path, '--output', output)
    assert status == 2 and json.loads(err)['error']['code'] == 'OVERLAPPING_REGIONS'
    assert not output.exists()
    status, _, err = invoke('integrate', source, path, '--output', output,
                            '--allow-overlap', '--baseline', 'edge_linear')
    assert status == 0, err
    report = json.loads(output.read_text())
    assert report['dataset_digest'] == dataset_digest(dataset)
    assert all('overlap_allowed_independent_integral' in r['flags'] for r in report['regions'])
    assert all(r['integral_unit'] == 'intensity*Hz' for r in report['regions'])


def test_2d_automatic_peak_regions_have_signed_hz_squared_volumes(tmp_path):
    data = np.zeros((1, 16, 16))
    data[0, 3, 3], data[0, 12, 12] = 5., -4.
    source = tmp_path/'two-dimensional.nmrh5'
    write_dataset(Dataset(data, [Axis('f1', 16, domain='frequency', unit='Hz', step=2.),
                                 Axis('f2', 16, domain='frequency', unit='Hz', step=-3.)]),
                  source, format='native')
    report_path = tmp_path/'two-dimensional.json'
    status, _, err = invoke('peaks', source, '--output', report_path, '--sign', 'both',
                            '--threshold', '1', '--baseline', 'none',
                            '--integrate', '--half-width', '2')
    assert status == 0, err
    report = json.loads(report_path.read_text())
    assert len(report['peaks']['peaks']) == len(report['integrals']['regions']) == 2
    assert [r['integral_unit'] for r in report['integrals']['regions']] == ['intensity*Hz^2']*2
    assert [r['integral'] > 0 for r in report['integrals']['regions']] == [True, False]


def test_autophase_native_result_report_and_collision(tmp_path):
    n = 1024
    x = np.linspace(-100, 100, n)
    measured = sum(height/(1+1j*(x-center)/width)
                   for center, width, height in [(-42, 1.7, 1.), (35, 2., .7)])
    measured *= np.exp(1j*np.deg2rad(35))
    source = tmp_path/'input.nmrh5'
    dataset = Dataset(np.stack((measured.real, measured.imag)),
                      [Axis('f', n, domain='frequency', unit='Hz', start=x[0],
                            step=x[1]-x[0])], ['f'])
    write_dataset(dataset, source, format='native')
    output, report_path = tmp_path/'phased.nmrh5', tmp_path/'phase.json'
    status, out, err = invoke('autophase', source, output, '--report', report_path,
                              '--backend', 'numpy')
    assert status == 0, err
    report = json.loads(report_path.read_text())
    assert report['dataset_digest'] == dataset_digest(dataset)
    assert report['result_dataset_digest'] == dataset_digest(read_dataset(output))
    assert abs(report['operation']['p0']+35) < 1
    assert json.loads(out)['result_dataset_digest'] == report['result_dataset_digest']
    original = report_path.read_bytes()
    status, _, err = invoke('autophase', source, tmp_path/'new.nmrh5',
                            '--report', report_path, '--backend', 'numpy')
    assert status == 2 and json.loads(err)['error']['code'] == 'OUTPUT_EXISTS'
    assert not (tmp_path/'new.nmrh5').exists() and report_path.read_bytes() == original


def test_multifile_publish_rolls_back_only_its_own_first_output(tmp_path):
    source = tmp_path/'input.nmrh5'
    dataset = spectrum(source)
    output, report = tmp_path/'phased.nmrh5', tmp_path/'phase.json'
    fake = {'schema_version': '1', 'kind': 'autophase',
            'dataset_digest': dataset_digest(dataset),
            'result_dataset_digest': dataset_digest(dataset)}
    original = cli._publish_staged_file
    calls = 0

    def fail_second(staged, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise NMRNextError('OUTPUT_EXISTS', 'Simulated competing report publication')
        return original(staged, target)

    with mock.patch('nmrnext.analysis.autophase', return_value=(dataset, fake)), \
         mock.patch('nmrnext.cli._publish_staged_file', side_effect=fail_second):
        status, _, err = invoke('autophase', source, output, '--report', report)
    assert status == 2 and json.loads(err)['error']['code'] == 'OUTPUT_EXISTS'
    assert not output.exists() and not report.exists()
    assert dataset_digest(read_dataset(source)) == dataset_digest(dataset)
    assert not list(tmp_path.glob('.spinflow-stage-*'))


def test_figure_rejects_stale_report_before_optional_plot_import(tmp_path):
    source = tmp_path/'spectrum.nmrh5'
    spectrum(source)
    report = tmp_path/'stale.json'
    report.write_text(json.dumps({'schema_version': '1', 'kind': 'peak_picking',
                                  'dataset_digest': '0'*64, 'peaks': []}))
    output = tmp_path/'stale.png'
    status, _, err = invoke('figure', source, output, '--analysis', report)
    assert status == 2 and json.loads(err)['error']['code'] == 'STALE_ANALYSIS'
    assert not output.exists()


def test_optional_plot_missing_returns_structured_error_without_qt(tmp_path):
    source = tmp_path/'spectrum.nmrh5'
    spectrum(source)
    output = tmp_path/'spectrum.png'
    with mock.patch('nmrnext.figures._plotting', side_effect=NMRNextError('PLOT_UNAVAILABLE', 'Install optional plot extra')):
        status, _, err = invoke('figure', source, output)
    assert status == 2 and json.loads(err)['error']['code'] == 'PLOT_UNAVAILABLE'
    assert not output.exists()


def test_analysis_reader_has_separate_bounded_strict_json_contract(tmp_path):
    source = tmp_path/'spectrum.nmrh5'
    spectrum(source)
    report = tmp_path/'analysis.json'
    output = tmp_path/'figure.svg'
    report.write_bytes(b' '*(8*1024*1024+1))
    status, _, err = invoke('figure', source, output, '--analysis', report)
    assert status == 2 and json.loads(err)['error']['code'] == 'ANALYSIS_TOO_LARGE'
    assert not output.exists()
    for body in ('{"schema_version":"1","schema_version":"1"}',
                 '{"schema_version":"1","dataset_digest":NaN}',
                 '{"schema_version":"1","unused":{"overflow":1e999}}'):
        report.write_text(body)
        status, _, err = invoke('figure', source, output, '--analysis', report)
        assert status == 2 and json.loads(err)['error']['code'] == 'INVALID_ANALYSIS_REPORT'
    recipe = tmp_path/'recipe.json'
    recipe.write_bytes(b' '*(1024*1024+1))
    status, _, err = invoke('plan', recipe)
    assert status == 2 and json.loads(err)['error']['code'] == 'RECIPE_TOO_LARGE'
    recipe.write_text('{"schema_version":"1","unused":1e999}')
    status, _, err = invoke('plan', recipe)
    assert status == 2 and json.loads(err)['error']['code'] == 'INVALID_RECIPE'


def test_ignored_nested_overflow_number_in_valid_proposal_is_rejected(tmp_path):
    source = tmp_path/'spectrum.nmrh5'
    dataset = spectrum(source)
    proposal = tmp_path/'proposal.json'
    output = tmp_path/'integrals.json'
    body = {'schema_version': '1', 'kind': 'region_proposal',
            'dataset_digest': dataset_digest(dataset),
            'regions': [{'id': 'r', 'unit': 'points', 'bounds': {'f': [10, 20]}}],
            'parameters': {'unused': 0}}
    proposal.write_text(json.dumps(body).replace('"unused": 0', '"unused": 1e999'))
    status, _, err = invoke('integrate', source, proposal, '--output', output)
    assert status == 2 and json.loads(err)['error']['code'] == 'INVALID_ANALYSIS_REPORT'
    assert not output.exists()


def test_postpublication_stage_cleanup_warning_is_success(tmp_path):
    output, table = tmp_path/'report.json', tmp_path/'table.csv'
    real_unlink = Path.unlink

    def fail_payload_cleanup(path, *args, **kwargs):
        if path.name == 'payload':
            raise OSError('simulated private cleanup failure')
        return real_unlink(path, *args, **kwargs)

    with mock.patch.object(Path, 'unlink', fail_payload_cleanup):
        warnings = cli._save_new_files([(output, lambda path: path.write_text('{}')),
                                        (table, lambda path: path.write_text('x\n1\n'))])
    assert output.read_text() == '{}' and table.read_text() == 'x\n1\n'
    assert len(warnings) == 2
    assert all(w['code'] == 'TEMPORARY_CLEANUP_FAILED' for w in warnings)
    assert {w['output'] for w in warnings} == {str(output), str(table)}


def test_cli_reports_success_with_final_paths_after_private_cleanup_warning(tmp_path):
    source = tmp_path/'spectrum.nmrh5'
    spectrum(source)
    output, table = tmp_path/'analysis.json', tmp_path/'analysis.csv'
    real_unlink = Path.unlink

    def fail_payload_cleanup(path, *args, **kwargs):
        if path.name == 'payload':
            raise OSError('simulated private cleanup failure')
        return real_unlink(path, *args, **kwargs)

    with mock.patch.object(Path, 'unlink', fail_payload_cleanup):
        status, out, err = invoke('peaks', source, '--output', output, '--csv', table,
                                  '--threshold', '1', '--baseline', 'none')
    assert status == 0, err
    metadata = json.loads(out)
    assert metadata['output'] == str(output) and metadata['csv'] == str(table)
    assert len(metadata['cleanup_warnings']) == 2
    assert {w['output'] for w in metadata['cleanup_warnings']} == {str(output), str(table)}
    assert output.exists() and table.exists()


def test_writer_failure_and_cleanup_failure_preserve_primary_error(tmp_path):
    output = tmp_path/'report.json'
    real_unlink = Path.unlink

    def fail_payload_cleanup(path, *args, **kwargs):
        if path.name == 'payload':
            raise OSError('simulated cleanup failure')
        return real_unlink(path, *args, **kwargs)

    def broken_writer(path):
        path.write_text('partial private bytes')
        raise NMRNextError('WRITER_FAILED', 'simulated writer failure')

    with mock.patch.object(Path, 'unlink', fail_payload_cleanup):
        with np.testing.assert_raises(NMRNextError) as caught:
            cli._save_new_files([(output, broken_writer)])
    assert caught.exception.code == 'WRITER_FAILED'
    assert not output.exists()


def test_concurrent_later_target_and_replaced_first_target_are_preserved(tmp_path):
    first, second = tmp_path/'first.json', tmp_path/'second.json'
    real_unlink = Path.unlink
    original_publish = cli._publish_staged_file
    calls = 0

    def fail_payload_cleanup(path, *args, **kwargs):
        if path.name == 'payload':
            raise OSError('simulated cleanup failure')
        return real_unlink(path, *args, **kwargs)

    def competing_publish(staged, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            replacement = tmp_path/'competitor.json'
            replacement.write_text('competitor first')
            os.replace(replacement, first)
            second.write_text('competitor second')
        if os.name == 'nt':
            return original_publish(staged, target)
        try:
            os.link(staged, target)  # Exercise real POSIX publication in Linux CI.
        except FileExistsError as exc:
            raise NMRNextError('OUTPUT_EXISTS', 'Simulated concurrent target') from exc

    with mock.patch.object(cli, '_publish_staged_file', side_effect=competing_publish), \
         mock.patch.object(Path, 'unlink', fail_payload_cleanup):
        with np.testing.assert_raises(NMRNextError) as caught:
            cli._save_new_files([(first, lambda path: path.write_text('ours first')),
                                 (second, lambda path: path.write_text('ours second'))])
    assert caught.exception.code == 'OUTPUT_EXISTS'
    assert first.read_text() == 'competitor first'
    assert second.read_text() == 'competitor second'


