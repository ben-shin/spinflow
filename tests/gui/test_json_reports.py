"""JSON publication faults exercise actual GUI saves and filesystem bytes."""
import builtins
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6 import QtCore, QtWidgets
from nmrnext.gui import Workbench


@pytest.fixture
def window():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Workbench()
    w.demo1_button.click()
    w.process_now()
    w.analyze_now('peaks')
    yield w
    w.close()
    w.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()


class PartialWriter:
    def __init__(self, stream, short):
        self.stream, self.short = stream, short
    def __enter__(self):
        self.stream.__enter__()
        return self
    def __exit__(self, *args):
        return self.stream.__exit__(*args)
    def __getattr__(self, name):
        return getattr(self.stream, name)
    def write(self, payload):
        self.stream.write(payload[:27])
        self.stream.flush()
        if self.short:
            return 27
        raise OSError('injected disk full')


@pytest.mark.parametrize('kind', ['analysis', 'recipe'])
@pytest.mark.parametrize('short', [False, True])
def test_json_partial_write_never_publishes(window, tmp_path, kind, short):
    target = tmp_path / 'report.json'
    original_open, original_fdopen = builtins.open, os.fdopen
    def wrapped_open(*args, **kwargs):
        return PartialWriter(original_open(*args, **kwargs), short)
    def wrapped_fdopen(*args, **kwargs):
        return PartialWriter(original_fdopen(*args, **kwargs), short)
    with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(target), '')), \
         patch('builtins.open', side_effect=wrapped_open), patch('os.fdopen', side_effect=wrapped_fdopen):
        getattr(window, f'_save_{kind}_dialog')()
    assert not target.exists()
    assert not list(tmp_path.iterdir())
    assert 'OSError' in window.status.text()


@pytest.mark.parametrize('kind', ['analysis', 'recipe'])
def test_json_success_collision_and_cancel(window, tmp_path, kind):
    target = tmp_path / 'report.json'
    expected = window._current_analysis() if kind == 'analysis' else window.recipe_record()
    with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(target), '')):
        getattr(window, f'_save_{kind}_dialog')()
        original = target.read_bytes()
        assert json.loads(original) == expected
        getattr(window, f'_save_{kind}_dialog')()
    assert target.read_bytes() == original
    assert 'FileExistsError' in window.status.text()
    assert list(tmp_path.iterdir()) == [target]
    with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=('', '')):
        getattr(window, f'_save_{kind}_dialog')()
    assert list(tmp_path.iterdir()) == [target]


def test_json_concurrent_destination_preserved(tmp_path, monkeypatch):
    from nmrnext.gui import _save_json_report
    target = tmp_path / 'report.json'
    method = 'rename' if os.name == 'nt' else 'link'
    publish = getattr(os, method)
    def race(src, dst):
        Path(dst).write_bytes(b'concurrent writer')
        publish(src, dst)
    monkeypatch.setattr(os, method, race)
    with pytest.raises(FileExistsError):
        _save_json_report(target, {'complete': True})
    assert target.read_bytes() == b'concurrent writer'
    assert list(tmp_path.iterdir()) == [target]


def test_json_cleanup_failure_preserves_success_and_original_failure(tmp_path, monkeypatch):
    from nmrnext.gui import _save_json_report
    target = tmp_path / 'report.json'
    # Keep a temporary link after publication to exercise the POSIX cleanup path.
    if os.name == 'nt':
        def link_simulation(src, dst):
            with open(dst, 'xb') as stream:
                stream.write(Path(src).read_bytes())
        monkeypatch.setattr(os, 'rename', link_simulation)
    unlink = Path.unlink
    def denied(path, *args, **kwargs):
        if path.name.startswith('.spinflow-json-'):
            raise OSError('injected cleanup failure')
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', denied)
    warning = _save_json_report(target, {'complete': True})
    assert json.loads(target.read_text()) == {'complete': True}
    assert 'cleanup' in warning.lower()
    residue = list(tmp_path.glob('.spinflow-json-*'))
    assert len(residue) == 1
    assert str(residue[0]) in warning
    with pytest.raises(FileExistsError) as caught:
        _save_json_report(target, {'complete': False})
    assert json.loads(target.read_text()) == {'complete': True}
    assert 'cleanup' in ' '.join(caught.value.__notes__).lower()
    assert len(list(tmp_path.glob('.spinflow-json-*'))) == 2
    for path in tmp_path.glob('.spinflow-json-*'):
        unlink(path)
