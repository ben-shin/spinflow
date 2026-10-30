"""Controller replay reuse must preserve source, previews and applied state."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import gc
import time

import numpy as np
import pytest
from PySide6 import QtCore, QtWidgets

from nmrnext.demo import generate_demo
from nmrnext.errors import NMRNextError
from nmrnext.gui import Workbench
from nmrnext.operations import run_operations


@pytest.fixture
def workbench():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Workbench()
    window.show()
    app.processEvents()
    yield window, app
    window.close()
    window.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()


def test_preview_then_process_reuses_full_original_recipe_and_reset_clears(workbench):
    window, app = workbench
    original = generate_demo(dimensions=1, points=[64])
    window.load_dataset_now(original)
    window.backend_combo.setCurrentIndex(window.backend_combo.findData('numpy'))
    preview = window.process_now(preview=True)
    assert window.current is original
    result = window.process_now()
    assert result is preview
    assert window.source is original
    expected = run_operations(original, window.build_recipe())
    np.testing.assert_array_equal(result.data, expected.data)
    assert result.to_metadata() == expected.to_metadata()
    assert window._recipe_cache.entry_count > 0
    window.reset()
    assert window._recipe_cache.entry_count == 0
    assert window.current is original


def test_async_recipe_uses_same_cache_and_source_reload_never_reuses_old_result(workbench):
    window, app = workbench
    window.load_dataset_now(generate_demo(dimensions=1, points=[64]))
    first = window.process_now()
    window._process_async(preview=False)
    deadline = time.monotonic() + 10
    while window._busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert not window._busy
    assert window.current is first
    replacement = generate_demo(dimensions=1, points=[64])
    window.load_dataset_now(replacement)
    assert window._recipe_cache.entry_count == 0
    second = window.process_now()
    assert second is not first
    assert window.source is replacement


def test_reduced_budget_cannot_publish_cached_result_or_replace_applied_state(workbench):
    window, app = workbench
    window.load_dataset_now(generate_demo(dimensions=1, points=[64]))
    window.memory_spin.setValue(16)
    applied = window.process_now()
    window.memory_spin.setValue(1)
    with pytest.raises(NMRNextError) as error:
        window.process_now()
    assert error.value.code == 'MEMORY_BUDGET_EXCEEDED'
    assert window.current is applied
    assert window.last_memory_bytes == 16 * 1024**2
