"""Export portable, strict CLI recipes with a lossless original source snapshot."""
import json
import os
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


def export_workflow_bundle(source, operations, directory, *, backend='native'):
    """Create a new directory; return its runnable ``workflow.json`` path.

    Files are completed in private staging before publication, recipe last.
    Failed exports remove only files created by this
    call, never recursively delete a directory, and never claim completion.
    The original source (including provenance) travels with the recipe.
    """
    destination = Path(directory)
    plan = plan_operations(source, operations, backend=backend)
    record = {
        'schema_version': '1', 'input': 'source.nmrh5', 'output': 'result.nmrh5',
        'input_format': 'native', 'output_format': 'native',
        'operations': operations, 'backend': backend,
        # Match the applied GUI recipe even if it exceeds the CLI default budget.
        'memory_bytes': max(256 * 1024**2, plan['estimated_working_bytes']),
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
        _publish_new(staged_snapshot, snapshot)
        owned.append(snapshot)
        _publish_new(staged_recipe, recipe)
        owned.append(recipe)
    except BaseException:
        for path in reversed(owned):
            try:
                path.unlink()
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
