"""Private bounded recipe-prefix reuse for interactive controllers.

    Full recipe validation always precedes lookup. Numeric source identity is
    a weak object identity, never a path/stat heuristic or a collision-prone
    hash; canonical operations/backend versions/memory policy are exact keys.
"""
from collections import OrderedDict
import json
import threading
import weakref

from ._identity import _cache_eligible, _capacity, _retained_size
from .model import Dataset
from .operations import _run_plan
from .planning import plan_operations


class RecipeCache:
    """Sparse immutable checkpoints within a shared processing/cache budget."""

    def __init__(self, capacity_bytes=128 * 1024**2):
        self._capacity_bytes = _capacity(capacity_bytes)
        self._entries = OrderedDict()
        self._source_ref = None
        self._policy = None
        self._last_reused_steps = 0
        self._lock = threading.RLock()

    def _prune(self):
        if self._source_ref is not None and self._source_ref() is None:
            self.clear()

    def _bytes(self, pending=None):
        if not self._entries and not pending:
            return 0
        seen = set()
        total = _retained_size(self, seen)
        if pending is not None:
            total += _retained_size(pending, seen)
        return total

    def _trim(self, limit):
        while self._entries and self._bytes() > limit:
            self._entries.popitem(last=False)

    @property
    def capacity_bytes(self):
        return self._capacity_bytes

    @capacity_bytes.setter
    def capacity_bytes(self, value):
        with self._lock:
            self._capacity_bytes = _capacity(value)
            self._prune()
            self._trim(self._capacity_bytes)

    @property
    def retained_bytes(self):
        with self._lock:
            self._prune()
            return self._bytes()

    @property
    def entry_count(self):
        with self._lock:
            self._prune()
            return len(self._entries)

    @property
    def last_reused_steps(self):
        return self._last_reused_steps

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._source_ref = None
            self._policy = None
            self._last_reused_steps = 0

    def run(self, dataset, operations, *, backend='numpy', threads=1, memory_bytes=None):
        with self._lock:
            self._last_reused_steps = 0
            # Includes original-source orientation, complete suffix validation,
            # backend/thread bounds and the selected full-recipe memory limit.
            plan = plan_operations(dataset, operations, backend=backend, threads=threads, memory_bytes=memory_bytes)
            if not isinstance(dataset, Dataset):
                from .errors import NMRNextError
                raise NMRNextError('INVALID_DATASET', 'Execution requires numeric sample data')
            if not _cache_eligible(dataset):
                self.clear()
                return _run_plan(dataset, plan), plan
            policy = json.dumps({'cache_schema': 1, 'backend': plan['backend'], 'memory_bytes': memory_bytes},
                                sort_keys=True, separators=(',', ':'), allow_nan=False)
            if self._source_ref is None or self._source_ref() is not dataset or self._policy != policy:
                self.clear()
                self._source_ref = weakref.ref(dataset)
                self._policy = policy
            limit = self._capacity_bytes
            if memory_bytes is not None:
                limit = min(limit, max(0, memory_bytes - plan['estimated_working_bytes']))
            self._trim(limit)
            normalized = tuple(json.dumps(step['operation'], sort_keys=True, separators=(',', ':'), allow_nan=False)
                               for step in plan['steps'])
            start = 0
            current = dataset
            for count in range(len(normalized), 0, -1):
                key = normalized[:count]
                if key in self._entries:
                    current = self._entries[key]
                    self._entries.move_to_end(key)
                    start = count
                    break
            self._last_reused_steps = start
            if start == len(normalized):
                return current, plan
            if not limit:
                return _run_plan(current, plan, start=start), plan
            # The final result handles exact repeats; its predecessor handles
            # final-parameter edits. Prefer the predecessor if both do not fit.
            snapshots = {index for index in (len(normalized) - 2, len(normalized) - 1)
                         if index >= start and plan['steps'][index]['output_bytes'] < limit}
            preferred = normalized[:-1] if len(normalized) > 1 else None
            pending = OrderedDict()
            def checkpoint(index, result):
                key = normalized[:index + 1]
                pending[key] = result
                # Eviction is safe even if a later step fails. Unsuccessful new
                # snapshots are never published. Protect the same predecessor
                # whether it was staged now or retained from an earlier run.
                while self._entries and self._bytes(pending) > limit:
                    victim = next((key for key in self._entries if key != preferred), None)
                    if victim is None:
                        break
                    del self._entries[victim]
                while pending and self._bytes(pending) > limit:
                    victim = next((key for key in reversed(pending) if key != preferred), None)
                    if victim is None:
                        break
                    del pending[victim]
                if self._bytes(pending) > limit:
                    pending.pop(preferred, None)
                    self._entries.pop(preferred, None)
            result = _run_plan(current, plan, start=start, on_step=checkpoint,
                               snapshot_steps=snapshots)
            for key, snapshot in pending.items():
                self._entries[key] = snapshot
                self._entries.move_to_end(key)
            self._trim(limit)
            return result, plan
