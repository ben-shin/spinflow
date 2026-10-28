"""Portable workflow exports exercise the actual CLI and native codec."""
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import numpy as np
import pytest

from nmrnext.demo import generate_demo
from nmrnext.errors import NMRNextError
from nmrnext.io import read_dataset
from nmrnext.operations import run_operations


def export(*args, **kwargs):
    from nmrnext.workflow_bundle import export_workflow_bundle
    return export_workflow_bundle(*args, **kwargs)


def test_bundle_replays_after_move_without_original_file(tmp_path):
    source = generate_demo(dimensions=2)
    ops = [{'op': 'fft', 'axis': a.id} for a in source.axes]
    ops += [{'op': 'phase', 'axis': source.axes[0].id, 'p0': 23, 'p1': 71, 'pivot': 3.5},
            {'op': 'crop', 'axis': source.axes[1].id, 'start': 8, 'stop': 40}]
    expected = run_operations(source, ops, backend='native')
    path = export(source, ops, tmp_path / 'bundle', backend='native')
    assert path.name == 'workflow.json'
    assert sorted(p.name for p in path.parent.iterdir()) == ['source.nmrh5', 'workflow.json']
    moved = tmp_path / 'moved'
    path.parent.rename(moved)
    for command in ('plan', 'run'):
        result = subprocess.run([sys.executable, '-m', 'nmrnext.cli', command, str(moved / path.name)],
                                cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    replay = read_dataset(moved / 'result.nmrh5')
    np.testing.assert_array_equal(replay.data, expected.data)
    assert replay.to_metadata() == expected.to_metadata()
    snapshot = read_dataset(moved / 'source.nmrh5')
    np.testing.assert_array_equal(snapshot.data, source.data)
    assert snapshot.to_metadata() == source.to_metadata()


@pytest.mark.parametrize('kind', ['empty_directory', 'populated_directory', 'file'])
def test_bundle_collision_preserves_existing_destination(tmp_path, kind):
    destination = tmp_path / 'bundle'
    if kind == 'file':
        destination.write_bytes(b'existing')
        preserved = destination
    else:
        destination.mkdir()
        preserved = destination / 'source.nmrh5'
        if kind == 'populated_directory':
            preserved.write_bytes(b'existing')
    with pytest.raises(NMRNextError) as caught:
        export(generate_demo(dimensions=1), [], destination)
    assert caught.value.code == 'OUTPUT_EXISTS'
    if kind != 'empty_directory':
        assert preserved.read_bytes() == b'existing'
    else:
        assert list(destination.iterdir()) == []


def test_bundle_failure_removes_only_owned_outputs(tmp_path):
    from nmrnext import workflow_bundle
    destination = tmp_path / 'bundle'
    original_open = Path.open
    def fail_recipe(path, *args, **kwargs):
        if path.name == 'workflow.json':
            (destination / 'unrelated.txt').write_text('preserve me')
            raise OSError('simulated disk failure')
        return original_open(path, *args, **kwargs)
    with patch.object(Path, 'open', fail_recipe):
        with pytest.raises(OSError, match='disk failure'):
            workflow_bundle.export_workflow_bundle(generate_demo(dimensions=1), [], destination)
    assert sorted(p.name for p in destination.iterdir()) == ['unrelated.txt']
    assert (destination / 'unrelated.txt').read_text() == 'preserve me'


def test_invalid_recipe_leaves_no_destination(tmp_path):
    with pytest.raises(NMRNextError):
        export(generate_demo(dimensions=1), [{'op': 'unsupported'}], tmp_path / 'bundle')
    assert not (tmp_path / 'bundle').exists()


def test_snapshot_hash_failure_after_write_leaves_no_partial_bundle(tmp_path):
    from nmrnext import io
    destination = tmp_path / 'bundle'
    failure = OSError('snapshot hash failed after publication')
    def fail_hash(path):
        # The real native writer has published a valid HDF5 file by this point.
        assert Path(path).is_file()
        raise failure
    with patch.object(io, '_hash_file', side_effect=fail_hash):
        with pytest.raises(OSError) as caught:
            export(generate_demo(dimensions=1), [], destination)
    assert caught.value is failure
    assert not destination.exists()


@pytest.mark.parametrize('filename', ['source.nmrh5', 'workflow.json'])
def test_concurrent_file_collision_preserves_other_actor_bytes(tmp_path, filename):
    from nmrnext import workflow_bundle
    destination = tmp_path / 'bundle'
    write = workflow_bundle.write_dataset
    def concurrent_write(*args, **kwargs):
        result = write(*args, **kwargs)
        # Another process creates a public output after our private snapshot was
        # written, but before the bundle publishes its final files.
        (destination / filename).write_bytes(b'created by another process')
        (destination / 'unrelated.txt').write_bytes(b'also preserve')
        return result
    with patch.object(workflow_bundle, 'write_dataset', side_effect=concurrent_write):
        with pytest.raises(NMRNextError) as caught:
            export(generate_demo(dimensions=1), [], destination)
    assert caught.value.code == 'OUTPUT_EXISTS'
    assert (destination / filename).read_bytes() == b'created by another process'
    assert (destination / 'unrelated.txt').read_bytes() == b'also preserve'
    assert sorted(p.name for p in destination.iterdir()) == sorted([filename, 'unrelated.txt'])
