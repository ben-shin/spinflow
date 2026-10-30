"""Private, byte-bounded identities for immutable numeric Dataset owners."""
from collections import OrderedDict
import hashlib
import json
import sys
import threading
import weakref

import numpy as np

from .errors import NMRNextError
from .model import Axis, Dataset


def _cache_eligible(dataset):
    # Subclasses may override samples/axes or expose mutable axis properties.
    # Only the built-in validated types guarantee the immutable snapshot used
    # by both numeric digest memoization and recipe-prefix reuse.
    return type(dataset) is Dataset and all(type(axis) is Axis for axis in dataset.axes)


def _retained_size(value, seen=None):
    """Conservative Python owner-graph size, counting shared owners once.

    Weak references never count their referents. ndarray views include their
    base owner; owning arrays already include their payload in getsizeof.
    This is retained object storage, not allocator/process RSS.
    """
    if seen is None:
        seen = set()
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    total = sys.getsizeof(value)
    if isinstance(value, dict):
        total += sum(_retained_size(key, seen) + _retained_size(item, seen)
                     for key, item in value.items())
    elif isinstance(value, (tuple, list, set, frozenset)):
        total += sum(_retained_size(item, seen) for item in value)
    elif isinstance(value, np.ndarray):
        if value.base is not None:
            total += _retained_size(value.base, seen)
    elif type(value).__module__.startswith('nmrnext.'):
        for cls in type(value).__mro__:
            for slot in getattr(cls, '__slots__', ()):
                if slot not in ('__weakref__', '__dict__') and hasattr(value, slot):
                    total += _retained_size(getattr(value, slot), seen)
        if hasattr(value, '__dict__'):
            total += _retained_size(vars(value), seen)
    return total


def _capacity(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NMRNextError('INVALID_CACHE_CAPACITY', 'Cache capacity must be a nonnegative integer byte count')
    return value


class NumericIdentityCache:
    """Exact digest memoization with weak identity keys and bounded overhead."""

    def __init__(self, capacity_bytes=32 * 1024):
        self._capacity_bytes = _capacity(capacity_bytes)
        self._entries = OrderedDict()
        self._lock = threading.RLock()

    def _prune(self):
        for reference in list(self._entries):
            if reference() is None:
                del self._entries[reference]

    def _bytes(self):
        return _retained_size(self) if self._entries else 0

    def _trim(self):
        while self._entries and self._bytes() > self._capacity_bytes:
            self._entries.popitem(last=False)

    @property
    def capacity_bytes(self):
        return self._capacity_bytes

    @capacity_bytes.setter
    def capacity_bytes(self, value):
        with self._lock:
            self._capacity_bytes = _capacity(value)
            self._prune()
            self._trim()

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

    def digest(self, dataset):
        if not isinstance(dataset, Dataset):
            raise NMRNextError('INVALID_DATASET', 'Digest requires a Dataset')
        if not _cache_eligible(dataset):
            return _compute_digest(dataset)
        with self._lock:
            self._prune()
            reference = weakref.ref(dataset)
            if reference in self._entries:
                self._entries.move_to_end(reference)
                return self._entries[reference]
            result = _compute_digest(dataset)
            if self._capacity_bytes:
                self._entries[reference] = result
                self._trim()
            return result


def _compute_digest(dataset):
    from .operations import quadrature_index_orientation
    header = {'axes': [a.to_dict() for a in dataset.axes], 'complex_axes': list(dataset.complex_axes),
              'dtype': dataset.data.dtype.str, 'shape': list(dataset.data.shape)}
    orientation = quadrature_index_orientation(dataset)
    overrides = {a.id: orientation[a.id] for a in dataset.axes if a.id in orientation
                 and orientation[a.id] != (1 if a.step > 0 else -1)}
    if overrides:
        header['quadrature_index_orientation'] = overrides
    digest = hashlib.sha256(json.dumps(header, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
    digest.update(memoryview(dataset.data).cast('B'))
    return digest.hexdigest()


_NUMERIC_IDENTITIES = NumericIdentityCache()


def numeric_digest(dataset):
    return _NUMERIC_IDENTITIES.digest(dataset)
