import xml.etree.ElementTree as ET

import numpy as np
import pytest

from nmrnext.errors import NMRNextError
from nmrnext.model import Axis, Dataset


def spectrum(dimensions=1):
    x = Axis('x', 5, domain='frequency', unit='Hz', start=-200, step=100,
             encoding='real', observation_mhz=100, reference_ppm=5, nucleus='1H')
    if dimensions == 1:
        return Dataset(np.array([[0., 2., -1., 3., 0.]]), (x,))
    y = Axis('y', 3, domain='frequency', unit='Hz', start=10, step=-5, encoding='real', nucleus='15N')
    return Dataset(np.arange(-7., 8.).reshape(1, 3, 5), (y, x))


def test_figure_1d_preserves_signed_samples_and_calibrated_axes():
    from nmrnext.figures import _make_figure
    source = spectrum()
    fig, report = _make_figure(source)
    ax = fig.axes[0]
    np.testing.assert_array_equal(ax.lines[0].get_xdata(), [3, 4, 5, 6, 7])
    np.testing.assert_array_equal(ax.lines[0].get_ydata(), source.data[0])
    assert ax.get_xlim()[0] > ax.get_xlim()[1]
    assert ax.get_xlabel() == '1H (ppm)'
    assert ax.get_ylabel() == 'Signed intensity'
    assert len(report['dataset_digest']) == 64


def test_figure_2d_contours_keep_sign_and_physical_coordinates():
    from nmrnext.figures import _make_figure
    fig, report = _make_figure(spectrum(2), contour_level=1)
    ax = fig.axes[0]
    assert ax.get_xlabel() == '1H (ppm)'
    assert ax.get_ylabel() == '15N (Hz)'
    assert ax.get_xlim() == (7, 3)
    assert ax.get_ylim() == (10, 0)
    assert len(ax.collections) == 2
    assert all(level < 0 for level in ax.collections[0].levels)
    assert all(level > 0 for level in ax.collections[1].levels)
    assert report['contour_level'] == 1


@pytest.mark.parametrize('suffix,signature', [('svg', b'<?xml'), ('pdf', b'%PDF'), ('png', b'\x89PNG')])
def test_figure_exports_actual_formats_without_overwrite(tmp_path, suffix, signature):
    from nmrnext.figures import export_figure
    path = tmp_path / f'figure.{suffix}'
    report = export_figure(spectrum(), path, title='Example <&>')
    original = path.read_bytes()
    assert original.startswith(signature)
    assert report['format'] == suffix
    if suffix == 'svg':
        text = ''.join(ET.fromstring(original).itertext())
        assert '1H (ppm)' in text
        assert 'Example <&>' in text
        assert report['dataset_digest'] in text
    with pytest.raises(NMRNextError) as caught:
        export_figure(spectrum(), path)
    assert caught.value.code == 'OUTPUT_EXISTS'
    assert path.read_bytes() == original


def test_figures_reject_stale_analysis_and_time_data(tmp_path):
    from nmrnext.figures import export_figure
    with pytest.raises(NMRNextError, match='digest'):
        export_figure(spectrum(), tmp_path / 'stale.svg', analysis={'dataset_digest': 'bad', 'peaks': []})
    raw = Dataset(np.ones((1, 3)), (Axis('t', 3, encoding='real'),))
    with pytest.raises(NMRNextError):
        export_figure(raw, tmp_path / 'raw.svg')
    assert list(tmp_path.iterdir()) == []


def test_annotations_use_exact_calibrated_region_and_peak_positions():
    from nmrnext.analysis import dataset_digest
    from nmrnext.figures import _make_figure
    source = spectrum()
    report = {'dataset_digest': dataset_digest(source), 'parameters': {'channel': 0},
              'peaks': [{'id': 'P1', 'points': {'x': 3}, 'coordinates': {'x': {'Hz': 100, 'ppm': 6}}, 'intensity': 3}],
              'regions': [{'id': 'R1', 'bounds': {'x': [0, 2]}, 'unit': 'points', 'point_bounds': {'x': [0, 2]}}]}
    fig, _ = _make_figure(source, analysis=report)
    ax = fig.axes[0]
    assert ax.texts[0].xy == (6, 3)
    assert ax.patches[0].get_x() == 3
    assert ax.patches[0].get_width() == 2
    report['peaks'].append({**report['peaks'][0], 'id': 'overlap'})
    _, result = _make_figure(source, analysis=report)
    assert result['peak_labels_shown'] == 1
    assert result['peaks_in_report'] == 2


def test_render_failure_leaves_no_partial_file(tmp_path, monkeypatch):
    from matplotlib.figure import Figure
    from nmrnext.figures import export_figure
    def fail(*args, **kwargs):
        raise OSError('render failure')
    monkeypatch.setattr(Figure, 'savefig', fail)
    with pytest.raises(OSError, match='render failure'):
        export_figure(spectrum(), tmp_path / 'failed.svg')
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('mutation', ['outside', 'nan', 'reversed', 'inconsistent', 'missing_axis', 'coordinate_bounds', 'channel'])
def test_region_tampering_is_rejected_before_publication(tmp_path, mutation):
    from nmrnext.analysis import integrate_regions
    from nmrnext.figures import export_figure
    source = spectrum()
    report = integrate_regions(source, [{'id': 'R1', 'unit': 'points', 'bounds': {'x': [1, 3]}}])
    region = report['regions'][0]
    if mutation == 'channel':
        report['parameters']['channel'] = 1
    elif mutation == 'coordinate_bounds':
        region['coordinate_bounds']['x']['Hz'] = [-200, 200]
    else:
        region['point_bounds'] = {'x': {'outside': [-100, 100], 'nan': [0, float('nan')],
                                     'reversed': [3, 1], 'inconsistent': [0, 4], 'missing_axis': [1, 3]}[mutation]}
        if mutation == 'missing_axis':
            region['point_bounds']['unknown'] = region['point_bounds'].pop('x')
    with pytest.raises(NMRNextError):
        export_figure(source, tmp_path / 'rejected.svg', analysis=report)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('mutation', ['outside', 'nan', 'fractional', 'coordinates', 'intensity', 'missing_axis'])
def test_peak_tampering_is_rejected_before_publication(tmp_path, mutation):
    from nmrnext.analysis import pick_peaks
    from nmrnext.figures import export_figure
    source = spectrum()
    report = pick_peaks(source, threshold=.5)
    peak = report['peaks'][0]
    if mutation == 'coordinates':
        peak['coordinates']['x']['ppm'] += 1
    elif mutation == 'intensity':
        peak['intensity'] += 100
    else:
        peak['points'] = {'x': {'outside': 100, 'nan': float('nan'), 'fractional': 1.5, 'missing_axis': 3}[mutation]}
        if mutation == 'missing_axis':
            peak['points']['unknown'] = peak['points'].pop('x')
    with pytest.raises(NMRNextError):
        export_figure(source, tmp_path / 'rejected.svg', analysis=report)
    assert list(tmp_path.iterdir()) == []


def test_publication_success_with_cleanup_warning_and_failure_preserves_original(tmp_path, monkeypatch):
    import os
    import shutil
    from pathlib import Path
    from types import SimpleNamespace
    from nmrnext import figures
    from matplotlib.figure import Figure
    real_unlink = Path.unlink
    def fail_cleanup(path, *args, **kwargs):
        if path.name.startswith('.'):
            raise OSError('temporary cleanup refused')
        return real_unlink(path, *args, **kwargs)
    def publish(staged, target):
        with open(target, 'xb') as out, open(staged, 'rb') as source:
            shutil.copyfileobj(source, out)
    monkeypatch.setattr(figures, 'os', SimpleNamespace(name='posix', link=os.link if os.name != 'nt' else publish, close=os.close))
    monkeypatch.setattr(Path, 'unlink', fail_cleanup)
    result = figures.export_figure(spectrum(), tmp_path / 'success.svg')
    assert (tmp_path / 'success.svg').exists()
    assert result['cleanup_warning']['code'] == 'TEMPORARY_CLEANUP_FAILED'
    failure = OSError('original render failure')
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(Figure, 'savefig', fail)
    with pytest.raises(OSError) as caught:
        figures.export_figure(spectrum(), tmp_path / 'failure.svg')
    assert caught.value is failure
    assert not (tmp_path / 'failure.svg').exists()


def test_compound_peak_proposal_and_integral_reports_remain_supported():
    from nmrnext.analysis import pick_peaks, propose_regions, integrate_regions
    from nmrnext.figures import _make_figure
    source = spectrum()
    peaks = pick_peaks(source, threshold=.5)
    proposal = propose_regions(source, peaks, half_width=.4)
    integrals = integrate_regions(source, proposal['regions'])
    fig, _ = _make_figure(source, analysis={'peaks': peaks, 'proposal': proposal, 'integrals': integrals})
    assert len(fig.axes[0].patches) == len(integrals['regions'])


def test_concurrent_publication_collision_preserves_existing_bytes(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    from types import SimpleNamespace
    from nmrnext import figures
    destination = tmp_path / 'collision.svg'
    def collide(staged, target):
        Path(target).write_bytes(b'other process')
        raise FileExistsError('concurrent file')
    monkeypatch.setattr(figures, 'os', SimpleNamespace(name='posix', link=collide, close=os.close))
    with pytest.raises(NMRNextError) as caught:
        figures.export_figure(spectrum(), destination)
    assert caught.value.code == 'OUTPUT_EXISTS'
    assert destination.read_bytes() == b'other process'
    assert list(tmp_path.iterdir()) == [destination]
import builtins
import sys
from unittest.mock import patch


def test_missing_plot_reports_active_environment_and_source_install():
    from nmrnext.figures import _plotting
    original = builtins.__import__
    def unavailable(name, *args, **kwargs):
        if name.startswith('matplotlib'):
            raise ImportError('simulated missing optional dependency')
        return original(name, *args, **kwargs)
    with patch('builtins.__import__', side_effect=unavailable):
        with pytest.raises(NMRNextError) as caught:
            _plotting()
    assert caught.value.code == 'PLOT_UNAVAILABLE'
    assert sys.executable in str(caught.value)
    assert '.[plot]' in str(caught.value)
    assert 'bootstrap' in str(caught.value)
    assert 'spinflow[plot]' not in str(caught.value)
