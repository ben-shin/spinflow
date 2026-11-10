"""Zoom uses current immutable data, not magnified overview samples."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from unittest.mock import patch
import numpy as np
import pytest
from PySide6 import QtCore, QtWidgets
import pyqtgraph as pg

from nmrnext.gui import Workbench
from nmrnext.model import Axis, Dataset


@pytest.fixture
def window():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    workbench = Workbench()
    # These tests assert ImageItem raster sampling and coordinate transforms.
    workbench.plot_mode_combo.setCurrentIndex(workbench.plot_mode_combo.findData('raster'))
    yield workbench
    workbench.close()
    workbench.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()


def image(window):
    return next(item for item in window.plot.getPlotItem().items if isinstance(item, pg.ImageItem))


def drain(window):
    app = QtWidgets.QApplication.instance()
    deadline = QtCore.QDeadlineTimer(2000)
    while window._viewport_timer.isActive() and not deadline.hasExpired():
        app.processEvents()
    app.processEvents()
    assert not window._viewport_timer.isActive()


@pytest.mark.parametrize('step', [1., -1.])
def test_zoom_reveals_both_signed_peaks_and_preserves_physical_centers(window, step):
    data = np.zeros((1, 2048, 4096), dtype=np.float64)
    data[0, 701, 1601] = 7.
    data[0, 703, 1603] = -5.
    axes = [Axis('n', 2048, domain='frequency', unit='Hz', step=step,
                 observation_mhz=60., reference_ppm=120.),
            Axis('h', 4096, domain='frequency', unit='Hz', step=-step,
                 observation_mhz=600., reference_ppm=8.)]
    source = Dataset(data, axes)
    window.load_dataset_now(source)
    assert not np.any(image(window).image)
    levels = tuple(image(window).levels)
    x = source.coordinates('h', 'ppm')
    y = source.coordinates('n', 'ppm')
    bounds = [sorted((x[1599], x[1605])), sorted((y[699], y[705]))]
    view = window.plot.getPlotItem().getViewBox()
    view.setRange(xRange=bounds[0], yRange=bounds[1], padding=0)
    requested = view.viewRange()
    drain(window)
    item = image(window)
    assert item.image.shape == (7, 7)
    assert item.image.max() == 7. and item.image.min() == -5.
    assert tuple(item.levels) == levels
    assert view.viewRange() == requested
    assert 'Complete visible' in window.plot_note.text()
    for row, col, value in ((701, 1601, 7.), (703, 1603, -5.)):
        pixel = np.argwhere(item.image == value)[0]
        physical = item.mapToParent(QtCore.QPointF(float(pixel[1]) + .5, float(pixel[0]) + .5))
        assert physical.x() == pytest.approx(x[col], abs=1e-12)
        assert physical.y() == pytest.approx(y[row], abs=1e-12)
    assert window.current is source and window.analysis_reports == {}
    np.testing.assert_array_equal(source.data, data)


def test_range_updates_coalesce_and_stale_source_cannot_replace_new_curve(window):
    first = Dataset(np.arange(60001, dtype=np.float64)[None],
                    [Axis('a', 60001, domain='frequency', unit='Hz')])
    window.load_dataset_now(first)
    view = window.plot.getPlotItem().getViewBox()
    with patch.object(window, '_draw_viewport', wraps=window._draw_viewport) as draw:
        for start in (100., 200., 300.):
            view.setXRange(start, start + 10., padding=0)
        drain(window)
        assert draw.call_count == 1
    np.testing.assert_array_equal(window.plot.listDataItems()[0].getData()[1], np.arange(300., 311.))
    # A distinct small dataset proves queued old updates are canceled.
    view.setXRange(400., 410., padding=0)
    second = Dataset(np.array([[2., -3., 4.]]), [Axis('b', 3, domain='frequency', unit='Hz')])
    window.load_dataset_now(second)
    drain(window)
    np.testing.assert_array_equal(window.plot.listDataItems()[0].getData()[1], second.data[0])
    assert window._rendered_dataset is second


def test_pan_outside_blanks_frame_then_recovers_without_range_reset(window):
    source = Dataset(np.array([[[1., -2., 3.], [4., -5., 6.]]]), [Axis('y', 2), Axis('x', 3)])
    window.load_dataset_now(source)
    view = window.plot.getPlotItem().getViewBox()
    view.setXRange(1e20, 2e20, padding=0)
    drain(window)
    assert image(window).image is None
    assert 'No sample centers' in window.plot_note.text()
    view.setRange(xRange=[-1, 2001], yRange=[-1, 1001], padding=0)
    drain(window)
    np.testing.assert_array_equal(image(window).image, source.data[0])


def test_curve_zoom_uses_original_samples_without_full_coordinate_scan(window):
    data = np.zeros((1, 50001), dtype=np.float64)
    data[0, 25000:25002] = [9., -6.]
    source = Dataset(data, [Axis('x', 50001, domain='frequency', unit='Hz')])
    window.load_dataset_now(source)
    assert not np.any(window.plot.listDataItems()[0].getData()[1])
    view = window.plot.getPlotItem().getViewBox()
    with patch.object(Dataset, 'coordinates', side_effect=AssertionError('full coordinates scan')):
        view.setXRange(24999., 25003., padding=0)
        drain(window)
    x, y = window.plot.listDataItems()[0].getData()
    np.testing.assert_array_equal(x, np.arange(24999., 25004.))
    np.testing.assert_array_equal(y, data[0, 24999:25004])
    window.memory_spin.setValue(1)
    drain(window)
    assert window._viewport_frame.estimated_bytes <= 1024**2
    assert window.current is source


def test_pending_component_preview_and_original_switch_use_the_latest_frame(window):
    axes = [Axis('x', 60001, domain='frequency', unit='Hz')]
    source = Dataset(np.stack([np.arange(60001.), -np.arange(60001.)]), axes, ('x',))
    window.load_dataset_now(source)
    view = window.plot.getPlotItem().getViewBox()
    view.setXRange(100., 110., padding=0)
    window.component_combo.setCurrentIndex(1)
    drain(window)
    assert window._viewport_frame.values.max() <= 0
    assert window.component_combo.currentData() == 1
    preview = Dataset(np.stack([np.ones(60001), -np.ones(60001)]), axes, ('x',))
    window.preview = preview
    window._render()
    view.setXRange(100., 110., padding=0)
    window.view_combo.setCurrentIndex(1)
    drain(window)
    assert window._rendered_dataset is source
    assert not window.use_plot_region_button.isEnabled()
    window.view_combo.setCurrentIndex(0)
    drain(window)
    assert window._rendered_dataset is preview
    assert not window.use_plot_region_button.isEnabled()
    np.testing.assert_array_equal(window._viewport_frame.values, -np.ones(window._viewport_frame.values.shape))


def test_failed_view_refresh_retains_last_good_drawing_with_explicit_warning(window):
    source = Dataset(np.arange(100., dtype=np.float64)[None],
                     [Axis('x', 100, domain='frequency', unit='Hz')])
    window.load_dataset_now(source)
    before = window.plot.listDataItems()[0].getData()[1].copy()
    with patch('nmrnext.gui.display_frame', side_effect=RuntimeError('injected frame failure')):
        window.plot.getPlotItem().getViewBox().setXRange(10., 20., padding=0)
        drain(window)
    np.testing.assert_array_equal(window.plot.listDataItems()[0].getData()[1], before)
    assert 'Previous drawing retained' in window.plot_note.text()
    window.plot.getPlotItem().getViewBox().setXRange(20., 30., padding=0)
    drain(window)
    np.testing.assert_array_equal(window.plot.listDataItems()[0].getData()[1], np.arange(20., 31.))


def test_session_rollback_restores_display_frame_and_cancels_incoming_timer(window, tmp_path):
    incoming = Dataset(np.arange(20., dtype=np.float64)[None],
                       [Axis('incoming', 20, domain='frequency', unit='Hz')])
    window.load_dataset_now(incoming)
    saved = window.save_session_now(tmp_path / 'incoming')
    prior = Dataset(np.arange(50001., dtype=np.float64)[None],
                    [Axis('prior', 50001, domain='frequency', unit='Hz')])
    window.load_dataset_now(prior)
    view = window.plot.getPlotItem().getViewBox()
    view.setXRange(100., 110., padding=0)
    drain(window)
    old_frame, old_item = window._viewport_frame, window._viewport_item
    old_range = view.viewRange()
    render = window._render
    calls = 0

    def fail_final():
        nonlocal calls
        calls += 1
        if calls == 2:
            window.plot.clear()
            raise RuntimeError('injected session render failure')
        return render()

    with patch.object(window, '_render', side_effect=fail_final):
        with pytest.raises(RuntimeError, match='injected session render failure'):
            window.open_session_now(saved)
    drain(window)
    assert window._viewport_frame is old_frame
    assert window._viewport_item is old_item
    assert window.current is prior and window._rendered_dataset is prior
    assert view.viewRange() == old_range
    view.setXRange(200., 210., padding=0)
    drain(window)
    np.testing.assert_array_equal(window.plot.listDataItems()[0].getData()[1], np.arange(200., 211.))


@pytest.mark.parametrize('size', [(950, 620), (1320, 840)])
@pytest.mark.parametrize('referenced', [False, True])
@pytest.mark.parametrize('state', ['sampled', 'complete', 'empty'])
def test_display_notes_fit_small_and_normal_windows(window, size, referenced, state):
    axes = [Axis('n', 129, domain='frequency', unit='Hz', step=-1.,
                 observation_mhz=60. if referenced else None,
                 reference_ppm=120. if referenced else None),
            Axis('h', 1201, domain='frequency', unit='Hz', step=-1.,
                 observation_mhz=600. if referenced else None,
                 reference_ppm=8. if referenced else None)]
    source = Dataset(np.zeros((1, 129, 1201), dtype=np.float32), axes)
    window.load_dataset_now(source)
    window.resize(*size)
    window.show()
    QtWidgets.QApplication.instance().processEvents()
    view = window.plot.getPlotItem().getViewBox()
    if state == 'complete':
        x, _ = window._axis_values(source, axes[1])
        y, _ = window._axis_values(source, axes[0])
        view.setRange(xRange=sorted((x[100], x[120])),
                      yRange=sorted((y[20], y[40])), padding=0)
        drain(window)
    elif state == 'empty':
        view.setXRange(1e10, 2e10, padding=0)
        drain(window)
    QtWidgets.QApplication.instance().processEvents()
    label = window.plot_note
    expected = {'sampled': 'Narrow peaks may be omitted.',
                'complete': 'Complete visible samples shown.',
                'empty': 'No sample centers in the visible area.'}[state]
    assert expected in label.text()
    if not referenced:
        assert 'reference is unknown' in label.text()
        assert 'enter a known anchor for ppm.' in label.text()
    assert label.wordWrap()
    required = label.fontMetrics().boundingRect(
        QtCore.QRect(0, 0, label.contentsRect().width(), 10000),
        QtCore.Qt.TextFlag.TextWordWrap, label.text()).height()
    assert required <= label.contentsRect().height() + 1
    assert window.plot.height() >= 180
