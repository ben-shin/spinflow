"""Export portable, strict CLI recipes with a lossless original source snapshot."""
import json
import os
import stat
from pathlib import Path
import tempfile

from .errors import NMRNextError
from .io import write_dataset
from .planning import plan_operations


def _publish_new(staged, target):
    """Publish a completed file without replacing a concurrently created one."""
    try:
        if os.name == 'nt':
            os.rename(staged, target)  # Windows rename refuses existing targets.
        else:
            os.link(staged, target)  # Same filesystem; atomic, no replacement.
    except FileExistsError as exc:
        raise NMRNextError('OUTPUT_EXISTS', f'Workflow output already exists: {target}') from exc


def _file_identity(path):
    """Use file identity, not a filename or its contents, for best-effort cleanup."""
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or not info.st_ino:
        return None
    return (info.st_dev, info.st_ino)


def _cleanup_published(path, identity):
    # A replacement between this check and unlink remains a filesystem race;
    # uncertain identity is preserved, never guessed from matching bytes.
    if identity is not None and _file_identity(path) == identity:
        path.unlink()


def _cleanup_staging(staging):
    if staging is None:
        return
    # Only these names belong to this invocation's unique private directory.
    # Do not recursively delete anything an unrelated process may have added.
    for name in ('source.nmrh5', 'workflow.json'):
        try:
            (staging / name).unlink(missing_ok=True)
        except OSError:
            pass
    try:
        staging.rmdir()
    except OSError:
        pass


def export_workflow_bundle(source, operations, directory, *, backend='native', memory_bytes=256 * 1024**2):
    """Create a new directory; return its runnable ``workflow.json`` path.

    Files are completed in private staging before publication, recipe last.
    Failed exports attempt to remove only files whose identity still matches
    the staged file; a concurrent replacement between check and unlink is a
    filesystem race. Never recursively delete a directory or claim completion.
    The original source (including provenance) travels with the recipe.
    """
    destination = Path(directory)
    plan = plan_operations(source, operations, backend=backend, memory_bytes=memory_bytes)
    record = {
        'schema_version': '1', 'input': 'source.nmrh5', 'output': 'result.nmrh5',
        'input_format': 'native', 'output_format': 'native',
        'operations': operations, 'backend': backend,
        'memory_bytes': memory_bytes,
    }
    payload = json.dumps(record, indent=2, allow_nan=False) + '\n'
    try:
        destination.mkdir()
    except FileExistsError as exc:
        raise NMRNextError('OUTPUT_EXISTS', f'Workflow destination already exists: {destination}') from exc
    owned = []
    staging = None
    recipe = destination / 'workflow.json'
    try:
        staging = Path(tempfile.mkdtemp(prefix='.spinflow-', dir=destination))
        # The native writer can fail after publishing its file (e.g. hashing).
        # That file is still ours to clean in this unique private namespace.
        staged_snapshot = staging / 'source.nmrh5'
        staged_recipe = staging / 'workflow.json'
        write_dataset(source, staged_snapshot, format='native', overwrite=False)
        with staged_recipe.open('x', encoding='utf-8') as stream:
            stream.write(payload)
        snapshot = destination / 'source.nmrh5'
        snapshot_identity = _file_identity(staged_snapshot)
        _publish_new(staged_snapshot, snapshot)
        owned.append((snapshot, snapshot_identity))
        recipe_identity = _file_identity(staged_recipe)
        _publish_new(staged_recipe, recipe)
        owned.append((recipe, recipe_identity))
    except BaseException:
        for path, identity in reversed(owned):
            try:
                _cleanup_published(path, identity)
            except OSError:
                pass  # Preserve the original error, including a failed cleanup.
        _cleanup_staging(staging)
        try:
            destination.rmdir()  # Only succeeds if empty; preserve unrelated files.
        except OSError:
            pass
        raise
    _cleanup_staging(staging)
    return recipe
