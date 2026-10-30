"""Explicit bounded native-HDF5 file processing; Dataset APIs remain eager."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import time
import uuid

import h5py
import numpy as np

from . import _block_engine as _engine
from ._identity import _retained_size
from .errors import NMRNextError
from .io import _format, describe_dataset
from .io.headers import check_source_stat
from .model import DatasetHeader, positive_int
from .operations import _operation_entry, _transition, quadrature_index_orientation, validate_backend
from .planning import plan_operations


_CACHE_BYTES = 64 * 1024
_HASH_BYTES = 256 * 1024
_FILE_HASH_BYTES = 1024 * 1024
_METADATA_LIMIT = 1024 * 1024
_HDF5_ALLOWANCE = 2 * 1024 * 1024
_MEMORY_SCOPE = ('Conservative bounded slab/complex/native workspace, metadata, hash buffers, source chunk '
                 'decompression and explicit HDF5 caches; excludes interpreter/OS overhead; not a hard RSS cap')


def _record(header):
    return {'schema_version': '1', 'axes': [a.to_dict() for a in header.axes],
            'complex_axes': list(header.complex_axes), 'metadata': header.metadata,
            'provenance': header.provenance}


def _serialized(header):
    value = json.dumps(_record(header), sort_keys=True, allow_nan=False)
    if len(value.encode('utf-8')) > _METADATA_LIMIT:
        raise NMRNextError('INVALID_METADATA', 'Block output metadata exceeds the native 1 MiB declaration limit')
    return value


def _open(path, mode='r'):
    return h5py.File(path, mode, rdcc_nbytes=_CACHE_BYTES, rdcc_nslots=17, rdcc_w0=0.)


def _next_header(before, operation, backend):
    axes, bits, metadata, op = _transition(before.axes, before.complex_axes, before.metadata,
                                          before.provenance, operation)
    shape = (2**len(bits), *(a.size for a in axes))
    entry = _operation_entry(before.axes, before.complex_axes, before.shape, shape, before.dtype,
                             op, backend, output_axes=axes, output_complex_axes=bits)
    after = DatasetHeader(axes, bits, before.dtype, metadata, before.provenance + [entry])
    _serialized(after)
    return after, op


def _same_source(expected, path):
    check_source_stat(expected)
    current = describe_dataset(path, format='native')
    if (current.source_identity != expected.source_identity or current.axes != expected.axes or
            current.complex_axes != expected.complex_axes or current.dtype != expected.dtype or
            current.metadata != expected.metadata or current.provenance != expected.provenance):
        raise NMRNextError('INPUT_CHANGED', 'Native input changed after block preflight')


def _check_store(handle, header):
    data = handle['data']
    if (data.shape != header.shape or data.dtype != header.dtype or data.is_virtual or data.external or
            handle.attrs.get('schema_version') != '1' or json.loads(handle.attrs['metadata_json']) != _record(header)):
        raise NMRNextError('INPUT_CHANGED', 'Opened native storage differs from the validated block header')
    return data


def _target(source, output, overwrite):
    output = Path(output).absolute()
    if output.resolve() == source.resolve() or (output.exists() and os.path.samefile(source, output)):
        raise NMRNextError('INPUT_OVERWRITE', 'Block input and output must be different files, including aliases')
    if (output.exists() or output.is_symlink()) and not overwrite:
        raise NMRNextError('OUTPUT_EXISTS', f'Output already exists: {output}')
    if not output.parent.is_dir():
        raise NMRNextError('OUTPUT_DIRECTORY', f'Output parent does not exist: {output.parent}')
    return output


def _disk_check(output, required):
    free = shutil.disk_usage(output.parent).free
    if free < required:
        raise NMRNextError('TEMPORARY_DISK_BUDGET_EXCEEDED', 'Insufficient free space for current and next block stores',
                           details={'temporary_disk_bytes': required, 'available_disk_bytes': free})
    return free


def plan_file_operations(input_path, operations, *, backend='native', threads=1,
                         memory_bytes=256*1024**2, input_format=None, output_format='native', output_path=None):
    """Validate headers/recipe and scalar resource geometry without reading samples."""
    positive_int(memory_bytes, 'memory_bytes')
    source = Path(input_path).resolve()
    if not source.exists():
        raise NMRNextError('INPUT_NOT_FOUND', f'Input does not exist: {source}')
    if _format(output_format, Path('output.nmrh5')) != 'nmrnext-hdf5':
        raise NMRNextError('UNSUPPORTED_BLOCK_FORMAT', 'Bounded output supports native schema-1 HDF5 only')
    if source.is_dir() or _format(input_format, source, reading=True, header_only=True) != 'nmrnext-hdf5':
        raise NMRNextError('UNSUPPORTED_BLOCK_FORMAT', 'Bounded input supports native schema-1 HDF5 only')
    header = describe_dataset(source, format='native')
    if not 1 <= len(header.axes) <= 4:
        raise NMRNextError('UNSUPPORTED_BLOCK_DIMENSIONS', 'Bounded processing supports one through four physical axes')
    backend_info = validate_backend(backend, threads)
    if backend != 'native' or threads != 1:
        raise NMRNextError('UNSUPPORTED_BLOCK_BACKEND', 'Bounded processing requires native backend and threads=1')
    sampling = header.metadata.get('sampling', 'fully_sampled')
    if sampling != 'fully_sampled' and sampling != {'mode': 'fully_sampled'}:
        raise NMRNextError('UNSUPPORTED_SAMPLING', 'Bounded processing requires fully sampled input')
    eager = plan_operations(header, operations, backend=backend, threads=threads)
    _serialized(header)
    with _open(source) as handle:
        data = _check_store(handle, header)
        if data.id.get_create_plist().get_nfilters():
            raise NMRNextError('UNSUPPORTED_BLOCK_STORAGE',
                               'Bounded processing supports uncompressed, unfiltered native HDF5 only')
        chunk_bytes = math.prod(data.chunks)*header.dtype.itemsize if data.chunks else 0
    # Only the current header is retained; full metadata histories are not
    # duplicated into every stage. Reserve defensive copies and serialization.
    control_bytes = 4 * (_retained_size(header) + _retained_size(eager)) + 4*_METADATA_LIMIT
    external = {'hdf5_raw_chunk_caches_bytes': 2*_CACHE_BYTES,
                'source_chunk_decompression_bytes': chunk_bytes,
                'metadata_control_bytes': control_bytes,
                'identity_and_file_hash_buffers_bytes': _FILE_HASH_BYTES+2*_HASH_BYTES,
                'hdf5_fixed_allowance_bytes': _HDF5_ALLOWANCE}
    reserve = sum(external.values())
    if memory_bytes < reserve+header.dtype.itemsize+1:
        raise NMRNextError('MEMORY_BUDGET_EXCEEDED', 'Block metadata/cache/hash reserves cannot fit the working budget',
                           details={'minimum_working_bytes': reserve+header.dtype.itemsize+1,
                                    'memory_bytes': memory_bytes, 'external_reserves': external})
    current, steps, sizes = header, [], []
    for step in eager['steps']:
        after, op = _next_header(current, step['operation'], backend_info)
        geometry = _engine.plan_stage(current, op, after, backend_info, memory_bytes, reserve)
        steps.append({**step, 'block': geometry})
        sizes.append(after.nbytes)
        current = after
    # Empty recipes still copy/validate in bounded canonical-order selections.
    estimate = max((step['block']['estimated_working_bytes'] for step in steps), default=reserve+2*_HASH_BYTES)
    minimum = max((step['block']['minimum_working_bytes'] for step in steps), default=estimate)
    if estimate > memory_bytes:
        raise NMRNextError('MEMORY_BUDGET_EXCEEDED', 'Bounded copy buffers cannot fit the working budget',
                           details={'minimum_working_bytes': estimate, 'memory_bytes': memory_bytes})
    pairs = [sizes[0]] if sizes else [header.nbytes]
    pairs.extend(a+b for a, b in zip(sizes, sizes[1:]))
    temporary = max(pairs) + 4*_METADATA_LIMIT
    free = None
    if output_path is not None:
        output = _target(source, output_path, overwrite=True)
        free = _disk_check(output, temporary)
    _same_source(header, source)
    return {'schema_version': '1', 'execution_mode': 'bounded_native_hdf5', 'streaming': True,
            'input': str(source), 'input_format': 'nmrnext-hdf5', 'output_format': 'nmrnext-hdf5',
            'backend': backend_info, 'memory_bytes': memory_bytes, 'memory_scope': _MEMORY_SCOPE,
            'estimated_working_bytes': estimate, 'minimum_working_bytes': minimum,
            'external_reserves': external, 'hdf5_cache_bytes_per_store': _CACHE_BYTES,
            'temporary_disk_bytes': temporary, 'available_disk_bytes_at_plan': free,
            'input_validation': 'header_only', 'input_identity': header.source_identity,
            'input_record': _record(header), 'input_dtype': header.dtype.name, 'input_shape': list(header.shape),
            'steps': steps, 'output_record': _record(current), 'output_shape': list(current.shape)}


def _hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(_FILE_HASH_BYTES), b''):
            digest.update(block)
    return digest.hexdigest()


def _selections(header):
    tile, count = [1]*len(header.axes), 1
    capacity = max(1, _HASH_BYTES//header.dtype.itemsize)
    for i in reversed(range(len(tile))):
        tile[i] = min(header.axes[i].size, capacity//count)
        count *= tile[i]
    for channel in range(header.shape[0]):
        start = [0]*len(tile)
        while True:
            yield (slice(channel, channel+1), *(slice(lo, min(lo+n, axis.size))
                   for lo, n, axis in zip(start, tile, header.axes)))
            for index in reversed(range(len(tile))):
                start[index] += tile[index]
                if start[index] < header.axes[index].size:
                    break
                start[index] = 0
            else:
                break


def _numeric_digest(data, header, target=None):
    orientation = quadrature_index_orientation(header)
    record = {'axes': [a.to_dict() for a in header.axes], 'complex_axes': list(header.complex_axes),
              'dtype': header.dtype.str, 'shape': list(header.shape)}
    overrides = {a.id: orientation[a.id] for a in header.axes if a.id in orientation and
                 orientation[a.id] != (1 if a.step > 0 else -1)}
    if overrides:
        record['quadrature_index_orientation'] = overrides
    digest = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
    for selection in _selections(header):
        values = data[selection]
        if not np.isfinite(values).all():
            raise NMRNextError('NONFINITE_DATA', 'Native source/result contains NaN or infinity')
        digest.update(memoryview(values).cast('B'))
        if target is not None:
            target[selection] = values
    return digest.hexdigest()


def _stat_identity(info):
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


def _identity(path):
    return _stat_identity(Path(path).lstat())


class _Transaction:
    """Own exact newly created file identities; cleanup never recurses."""
    def __init__(self, directory):
        self.directory = Path(directory).absolute()
        self.directory.mkdir(mode=0o700)
        self.directory_identity = _identity(self.directory)
        self.owned = {}
        self.handles = {}

    def create_path(self, name):
        self._check_directory()
        path = self.directory/name
        if path.parent != self.directory or Path(name).name != name:
            raise NMRNextError('INVALID_TEMPORARY_PATH', 'Temporary store must be an immediate transaction member')
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, 'O_BINARY', 0), 0o600)
        try:
            # Acquire identity from the exclusive object, never a later pathname lookup.
            self.owned[path] = _stat_identity(os.fstat(fd))
            self.handles[path] = os.fdopen(fd, 'r+b', buffering=0)
        except BaseException:
            os.close(fd)
            raise
        return path

    def refresh(self, path):
        self._check_directory()
        handle = self.handles.get(path)
        if handle is not None and _stat_identity(os.fstat(handle.fileno())) != self.owned[path]:
            raise NMRNextError('TEMPORARY_FILE_CHANGED', 'Owned temporary handle identity changed')
        if _identity(path) != self.owned[path]:
            raise NMRNextError('TEMPORARY_FILE_CHANGED', 'Owned temporary file was replaced')

    @contextmanager
    def writer(self, path):
        """HDF5 can truncate only the retained owned object, never a pathname replacement."""
        stream = self.handles[path]
        try:
            self.refresh(path)
            # FileIO is unbuffered; the fileobj driver adds no Python file buffer.
            with _open(stream, 'w') as handle:
                yield handle
        finally:
            self._close(path)

    def _close(self, path):
        handle = self.handles.pop(path, None)
        if handle is not None:
            handle.close()

    def _check_directory(self):
        if _identity(self.directory) != self.directory_identity:
            raise NMRNextError('TEMPORARY_DIRECTORY_CHANGED', 'Owned temporary directory was replaced')

    def remove(self, path):
        self._close(path)
        if not path.exists() and not path.is_symlink():
            self.owned.pop(path, None)
            return
        self.refresh(path)
        path.unlink()
        self.owned.pop(path)

    def cleanup(self):
        warnings = []
        for path in list(self.owned):
            try:
                self.remove(path)
            except (OSError, NMRNextError) as error:
                warnings.append(f'Preserved temporary path {path}: {error}')
        try:
            if _identity(self.directory) != self.directory_identity:
                warnings.append(f'Preserved replaced transaction directory {self.directory}')
            else:
                self.directory.rmdir()
        except OSError as error:
            warnings.append(f'Could not remove transaction directory {self.directory}: {error}')
        return warnings


def _write_header(handle, header):
    handle.attrs['schema_version'] = '1'
    handle.attrs['metadata_json'] = _serialized(header)


def _publish(transaction, temporary, target, overwrite, initial_target):
    transaction.refresh(temporary)
    if target.exists() or target.is_symlink():
        if not overwrite or initial_target is None:
            raise NMRNextError('OUTPUT_EXISTS', f'Output appeared before atomic publication: {target}')
        if _identity(target) != initial_target:
            raise NMRNextError('OUTPUT_CHANGED', 'Existing output was replaced during processing')
    elif initial_target is not None:
        raise NMRNextError('OUTPUT_CHANGED', 'Existing output disappeared during processing')
    if overwrite and initial_target is not None:
        os.replace(temporary, target)
    elif os.name == 'nt':
        try:
            os.rename(temporary, target)
        except FileExistsError as error:
            raise NMRNextError('OUTPUT_EXISTS', f'Output appeared before publication: {target}') from error
    else:
        try:
            os.link(temporary, target)
        except FileExistsError as error:
            raise NMRNextError('OUTPUT_EXISTS', f'Output appeared before publication: {target}') from error
        transaction.remove(temporary)
    transaction.owned.pop(temporary, None)


def process_to_file(input_path, output_path, operations, *, backend='native', threads=1,
                    memory_bytes=256*1024**2, input_format=None, output_format='native', overwrite=False):
    """Process bounded complete-axis slabs into one atomic native output file."""
    started = time.perf_counter()
    source = Path(input_path).resolve()
    if not source.exists():
        raise NMRNextError('INPUT_NOT_FOUND', f'Input does not exist: {source}')
    target = _target(source, output_path, overwrite)
    initial_target = _identity(target) if target.exists() or target.is_symlink() else None
    plan = plan_file_operations(source, operations, backend=backend, threads=threads, memory_bytes=memory_bytes,
                                input_format=input_format, output_format=output_format, output_path=target)
    header = describe_dataset(source, format='native')
    if header.source_identity != plan['input_identity']:
        raise NMRNextError('INPUT_CHANGED', 'Input changed after the complete block plan')
    source_hash = _hash_file(source)
    _same_source(header, source)
    with _open(source) as handle:
        input_digest = _numeric_digest(_check_store(handle, header), header)
    _same_source(header, source)
    transaction = _Transaction(target.parent/f'.spinflow-blocks-{uuid.uuid4().hex}')
    warnings, observations = [], []
    current_path, current_header = source, header
    try:
        for index, step in enumerate(plan['steps']):
            after, op = _next_header(current_header, step['operation'], plan['backend'])
            destination = transaction.create_path(f'stage-{index:06d}.nmrh5')
            with _open(current_path) as incoming, transaction.writer(destination) as outgoing:
                data = _check_store(incoming, current_header)
                _write_header(outgoing, after)
                result = outgoing.create_dataset('data', shape=after.shape, dtype=after.dtype, chunks=None)
                observations.append(_engine.execute_stage(data, result, current_header, after, op,
                                                           step['block'], plan['backend']))
                outgoing.flush()
            transaction.refresh(destination)
            if current_path != source:
                transaction.remove(current_path)
            current_path, current_header = destination, after
        if not plan['steps']:
            destination = transaction.create_path('copy.nmrh5')
            with _open(source) as incoming, transaction.writer(destination) as outgoing:
                _write_header(outgoing, header)
                result = outgoing.create_dataset('data', shape=header.shape, dtype=header.dtype, chunks=None)
                _numeric_digest(_check_store(incoming, header), header, target=result)
                outgoing.flush()
            transaction.refresh(destination)
            current_path = destination
        with _open(current_path) as handle:
            output_digest = _numeric_digest(_check_store(handle, current_header), current_header)
        output_hash = _hash_file(current_path)
        _same_source(header, source)
        if _hash_file(source) != source_hash:
            raise NMRNextError('INPUT_CHANGED', 'Input bytes changed during block processing')
        _same_source(header, source)
        _publish(transaction, current_path, target, overwrite, initial_target)
        report = {'execution_mode': 'bounded_native_hdf5', 'streaming': True, 'backend': plan['backend'],
                'operations': len(plan['steps']), 'memory_bytes': memory_bytes,
                'estimated_working_bytes': plan['estimated_working_bytes'], 'memory_scope': _MEMORY_SCOPE,
                'temporary_disk_bytes': plan['temporary_disk_bytes'], 'plan': plan, 'stages': observations,
                'input': {'path': str(source), 'sha256': source_hash, 'dataset_digest': input_digest,
                          'shape': list(header.shape), 'dtype': header.dtype.name,
                          'finite_validation': 'every sample before recipe execution'},
                'result': {'path': str(target), 'format': 'nmrnext-hdf5', 'sha256': output_hash,
                           'dataset_digest': output_digest, 'shape': list(current_header.shape),
                           'dtype': current_header.dtype.name},
                'elapsed_scope': 'complete API call including planning, validation, hashes, output close, publication and cleanup',
                'cleanup_warnings': warnings}
    except NMRNextError as error:
        error.details['cleanup_warnings'] = warnings
        raise
    except (OSError, MemoryError) as error:
        raise NMRNextError('BLOCK_IO_FAILURE', f'Bounded native transaction failed: {error}',
                           details={'cleanup_warnings': warnings}) from error
    finally:
        warnings.extend(transaction.cleanup())
    report['elapsed_seconds'] = time.perf_counter()-started
    return report
