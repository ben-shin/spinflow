"""Private complete-axis slabs for the bounded native-HDF5 execution profile.

File ownership, source authentication and atomic publication belong to blocks.
This module owns only scalar slab planning and bounded sample selections.
"""
import math

import numpy as np

from .errors import NMRNextError
from .model import DatasetHeader, positive_int
from .operations import (_ExecutionBuffers, _ExecutionState, _execute,
                         _operation_entry, _transition)


_SUPPORTED = frozenset({"reference", "window", "zero_fill", "fft", "phase",
                        "crop", "reverse", "transpose", "discard_imaginary"})
_POCKETFFT_COMMIT = "4d865cba24359b05b37da4e75aee24498c3b78e7"
_HEADROOM = 64*1024


def _validate_stage(before, op, after, backend):
    if not isinstance(before, DatasetHeader) or not isinstance(after, DatasetHeader):
        raise NMRNextError("INVALID_BLOCK_PLAN", "Block stages require validated DatasetHeader records")
    if not 1 <= len(before.axes) <= 4 or len(after.axes) != len(before.axes):
        raise NMRNextError("UNSUPPORTED_BLOCK_DIMENSIONS", "Bounded native processing supports one through four physical axes")
    if (not isinstance(backend, dict) or backend.get("name") != "native" or
            type(backend.get("threads")) is not int or backend["threads"] != 1 or
            backend.get("pocketfft_commit") != _POCKETFFT_COMMIT):
        raise NMRNextError("UNSUPPORTED_BLOCK_BACKEND", "Bounded processing requires the pinned native PocketFFT backend with threads=1")
    if not isinstance(op, dict) or op.get("op") not in _SUPPORTED:
        raise NMRNextError("UNSUPPORTED_BLOCK_OPERATION", "Operation is outside the bounded complete-axis profile")
    sampling = before.metadata.get("sampling", "fully_sampled")
    if sampling != "fully_sampled" and sampling != {"mode": "fully_sampled"}:
        raise NMRNextError("UNSUPPORTED_SAMPLING", "Bounded processing requires fully sampled data")
    axes, bits, metadata, resolved = _transition(before.axes, before.complex_axes,
                                                before.metadata, before.provenance, op)
    shape = (2**len(bits), *(axis.size for axis in axes))
    entry = _operation_entry(before.axes, before.complex_axes, before.shape, shape,
                             before.dtype, resolved, backend, output_axes=axes,
                             output_complex_axes=bits)
    if (resolved != op or after.axes != axes or after.complex_axes != bits or
            after.dtype != before.dtype or after.metadata != metadata or
            after.provenance != before.provenance+[entry]):
        raise NMRNextError("INVALID_BLOCK_PLAN", "Output header does not match the complete normalized operation and scientific history")


def _native_fft_reserve(length, itemsize):
    """Conservative one-thread reserve for the pinned PocketFFT implementation.

    cfftp twiddles occupy <=2N complex scalars; its work occupies N vector
    complex values. Bluestein uses a smooth convolution length <=P, where P
    is the next power of two above 2N-1. General-nd input work plus Bluestein
    and cfftp work need <=3*VLEN*P complex values, plus <8P scalar complex
    plan/chirp work. The header supports VLEN<=16 for float (<=8 double),
    so 64P complex values cover construction and execution, with allocator
    and factor-vector headroom separate. The Python-owned FFT output and
    shift/packing arrays are accounted outside this reserve.
    """
    convolution_bound = 1 << (max(1, 2*length-1)-1).bit_length()
    return 64*convolution_bound*(2*itemsize) + _HEADROOM


def plan_stage(input_header, resolved_op, output_header, backend_info, memory_bytes, cache_bytes):
    """Plan one complete-axis slab using scalar arithmetic only.

    cache_bytes is the caller's total fixed external reserve, including all
    open HDF caches, source chunk/decompression allowance, hashing and metadata.
    The estimate bounds application working arrays; it is not an OS RSS cap.
    """
    positive_int(memory_bytes, "memory_bytes")
    if type(cache_bytes) is not int or cache_bytes < 0:
        raise NMRNextError("INVALID_BUDGET", "External block reserve must be a nonnegative integer")
    _validate_stage(input_header, resolved_op, output_header, backend_info)
    name = resolved_op["op"]
    active = None if name == "transpose" else input_header.axis_index(resolved_op["axis"])
    itemsize = input_header.dtype.itemsize
    input_length = 1 if active is None else input_header.axes[active].size
    output_length = 1 if active is None else output_header.axes[active].size
    input_values = input_header.shape[0]*input_length
    output_values = output_header.shape[0]*output_length
    # Includes source ownership, possibly separate result ownership and one
    # explicit C-order write copy for reversed/transposed/noncontiguous views.
    real_per_batch = (input_values+2*output_values)*itemsize
    finite_per_batch = input_values+output_values
    # Packing replacement overlap, transform/shift overlap and previous-pair
    # output lifetimes fit four per-pair spatial complex arrays. Phase keeps
    # its original complex128 multiplication even for float32 components.
    complex_per_batch = (4*input_length*(16 if name == "phase" else 2*itemsize)
                         if name in {"fft", "phase"} else 0)
    vector_bytes = 64*input_length if name in {"window", "phase"} else 0
    native_bytes = _native_fft_reserve(input_length, itemsize) if name == "fft" else 0
    per_batch = real_per_batch+finite_per_batch+complex_per_batch
    fixed = cache_bytes+vector_bytes+native_bytes+_HEADROOM
    minimum = fixed+per_batch
    if memory_bytes < minimum:
        raise NMRNextError("MEMORY_BUDGET_EXCEEDED", "One complete transform line with all quadrature channels and scratch cannot fit the block memory budget",
                           details={"minimum_working_bytes": minimum, "memory_bytes": memory_bytes,
                                    "active_axis": active, "full_axis_points": input_length,
                                    "components": input_header.shape[0]})
    capacity = (memory_bytes-fixed)//per_batch
    tile = [1]*len(input_header.axes)
    if active is not None:
        tile[active] = input_length
    batches = 1
    for index in reversed(range(len(tile))):
        if index == active:
            continue
        tile[index] = min(input_header.axes[index].size, capacity//batches)
        batches *= tile[index]
    output_tile = list(tile)
    if active is None:
        output_tile = [tile[input_header.axis_index(identity)] for identity in resolved_op["order"]]
    else:
        output_tile[active] = output_length
    read_shape = [input_header.shape[0], *tile]
    write_shape = [output_header.shape[0], *output_tile]
    reserves = {"real_buffers_bytes": batches*real_per_batch,
                "finite_masks_bytes": batches*finite_per_batch,
                "complex_buffers_bytes": batches*complex_per_batch,
                "axis_vectors_bytes": vector_bytes, "native_fft_workspace_bytes": native_bytes,
                "fixed_headroom_bytes": _HEADROOM, "external_reserve_bytes": cache_bytes}
    return {"memory_bytes": memory_bytes, "minimum_working_bytes": minimum,
            "estimated_working_bytes": sum(reserves.values()),
            "per_batch_working_bytes": per_batch, "external_reserve_bytes": cache_bytes,
            "active_axis": active, "read_block_shape": read_shape, "write_block_shape": write_shape,
            "max_read_bytes": math.prod(read_shape)*itemsize,
            "max_write_bytes": math.prod(write_shape)*itemsize,
            "orthogonal_batch_size": batches,
            "block_count": math.prod((axis.size+extent-1)//extent
                                     for axis, extent in zip(input_header.axes, tile)),
            "reserves": reserves}


def _selections(header, tile):
    # itertools.product caches each input iterable; a large range would make
    # iterator memory grow with the number of slabs. Keep only an odometer.
    point = [0]*len(header.axes)
    while True:
        yield (slice(0, header.shape[0]), *(slice(start, min(start+extent, axis.size))
               for start, extent, axis in zip(point, tile, header.axes)))
        for index in reversed(range(len(point))):
            point[index] += tile[index]
            if point[index] < header.axes[index].size:
                break
            point[index] = 0
        else:
            return


def execute_stage(source_h5_dataset, target_h5_dataset, input_header, output_header,
                  resolved_op, stage_plan, backend_info):
    """Execute bounded selections only; return statistics, never sample arrays."""
    fresh = plan_stage(input_header, resolved_op, output_header, backend_info,
                        stage_plan["memory_bytes"], stage_plan["external_reserve_bytes"])
    if fresh != stage_plan:
        raise NMRNextError("INVALID_BLOCK_PLAN", "Block geometry or accounting changed after preflight")
    for store, header in ((source_h5_dataset, input_header), (target_h5_dataset, output_header)):
        if tuple(store.shape) != header.shape or np.dtype(store.dtype) != header.dtype:
            raise NMRNextError("INVALID_SHAPE", "Block store shape/dtype disagrees with its validated header")
    metadata, history = input_header.metadata, input_header.provenance
    output_metadata = output_header.metadata
    buffers = _ExecutionBuffers()
    active = stage_plan["active_axis"]
    name = resolved_op["op"]
    result = {"processed_blocks": 0, "read_values": 0, "written_values": 0,
              "max_observed_read_bytes": 0, "max_observed_write_bytes": 0,
              "max_observed_packing_bytes": 0}
    try:
        for selection in _selections(input_header, stage_plan["read_block_shape"][1:]):
            shape = tuple(region.stop-region.start for region in selection)
            block = np.empty(shape, dtype=input_header.dtype)
            source_h5_dataset.read_direct(block, source_sel=selection)
            if not np.isfinite(block).all():
                raise NMRNextError("NONFINITE_DATA", "Block source contains NaN or infinity")
            state = _ExecutionState(block, input_header.axes, input_header.complex_axes, metadata, history)
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                transformed = _execute(state, resolved_op, output_header.axes, output_header.complex_axes,
                                       output_metadata, "native", 1, backend_info, public=False, buffers=buffers)
            values = transformed.data
            if not np.isfinite(values).all():
                raise NMRNextError("NUMERICAL_OVERFLOW", f"Operation {name} produced nonfinite values")
            if active is None:
                destination = (slice(0, output_header.shape[0]),
                               *(selection[input_header.axis_index(identity)+1] for identity in resolved_op["order"]))
            else:
                destination = list(selection)
                destination[0] = slice(0, output_header.shape[0])
                destination[active+1] = slice(0, output_header.axes[active].size)
                destination = tuple(destination)
            writable_values = np.ascontiguousarray(values)
            target_h5_dataset[destination] = writable_values
            result["processed_blocks"] += 1
            result["read_values"] += block.size
            result["written_values"] += values.size
            result["max_observed_read_bytes"] = max(result["max_observed_read_bytes"], block.nbytes)
            result["max_observed_write_bytes"] = max(result["max_observed_write_bytes"], writable_values.nbytes)
            if buffers.complex is not None:
                result["max_observed_packing_bytes"] = max(result["max_observed_packing_bytes"], buffers.complex.nbytes)
            # Do not carry a previous slab's views/owners into the next read.
            del writable_values, values, transformed, state, block
    except (FloatingPointError, OverflowError) as exc:
        raise NMRNextError("NUMERICAL_OVERFLOW", f"Operation {name} overflowed; use float64 or revise parameters") from exc
    except MemoryError as exc:
        raise NMRNextError("MEMORY_ALLOCATION_FAILED", "A bounded working buffer could not be allocated") from exc
    return result
