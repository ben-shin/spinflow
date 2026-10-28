"""Metadata-only complete-recipe validation and conservative working-array accounting."""
import math

from .errors import NMRNextError
from .model import Dataset, positive_int
from .operations import _transition, validate_backend
from .bruker import correction_spec, corrected_metadata


def plan_operations(dataset, operations, *, backend="numpy", threads=1, memory_bytes=None):
    if not isinstance(dataset, Dataset):
        raise NMRNextError("INVALID_DATASET", "Planner requires a validated Dataset")
    if not isinstance(operations, (list, tuple)):
        raise NMRNextError("INVALID_RECIPE", "operations must be a sequence")
    backend_info = validate_backend(backend, threads)
    if memory_bytes is not None:
        positive_int(memory_bytes, "memory_bytes")
    axes = dataset.axes
    metadata = dataset.metadata
    sizes = [dataset.data.nbytes]
    steps = []
    for index, operation in enumerate(operations):
        try:
            previous_axes = axes
            axes, resolved = _transition(axes, dataset.complex_axes, metadata, operation)
            if resolved["op"] == "digital_filter":
                metadata = corrected_metadata(metadata, correction_spec(previous_axes, dataset.complex_axes, metadata))
        except NMRNextError as exc:
            raise NMRNextError(exc.code, f"Operation {index+1}: {exc}", details={"operation_index":index, **exc.details}) from exc
        shape = [2**len(dataset.complex_axes), *(a.size for a in axes)]
        nbytes = math.prod(shape)*dataset.data.dtype.itemsize
        sizes.append(nbytes)
        steps.append({"operation": resolved, "output_shape": shape, "output_bytes": nbytes,
                      "output_axes": [a.to_dict() for a in axes]})
    # Includes original input, current immutable array, output and conservative
    # transform/copy temporaries. OS/runtime/backend overhead is not an RSS limit.
    estimate = dataset.data.nbytes + 12*max(sizes) + 4*1024*1024
    if memory_bytes is not None and estimate > memory_bytes:
        raise NMRNextError("MEMORY_BUDGET_EXCEEDED", "In-memory processing estimate exceeds the working-array budget; reduce sizes or raise the budget (streaming is not implemented)",
                           details={"estimated_working_bytes":estimate,"memory_bytes":memory_bytes})
    return {"schema_version":"1", "backend":backend_info, "steps":steps,
            "estimated_working_bytes":estimate, "memory_bytes":memory_bytes,
            "memory_scope":"conservative working-array estimate plus 4 MiB allowance; excludes process/runtime and backend-private overhead; not a hard RSS limit",
            "streaming":False, "output_axes":[a.to_dict() for a in axes],
            "output_shape":[2**len(dataset.complex_axes),*(a.size for a in axes)]}
