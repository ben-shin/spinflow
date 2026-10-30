"""Typed, explicit modern operations; no implicit legacy compatibility."""
from dataclasses import replace
import importlib
import math
import sys

import numpy as np

from .errors import NMRNextError
from .model import Dataset, _has_immutable_storage, finite, positive_int
from .bruker import correction_spec, corrected_metadata, correct_samples


_FIELDS = {
    "digital_filter": {"axis"},
    "reference": {"axis", "reference_ppm", "reference_hz", "observation_mhz", "frequency_scale", "spectral_width_hz"},
    "window": {"axis", "kind", "lb_hz", "power", "first_point_scale", "off", "end"},
    "zero_fill": {"axis", "size"}, "fft": {"axis", "inverse"},
    "phase": {"axis", "p0", "p1", "pivot"},
    "crop": {"axis", "start", "stop"}, "reverse": {"axis"},
    "transpose": {"order"},
    "discard_imaginary": {"axis"},
}


_ORIENTATION_KEY = "quadrature_index_orientation"


def quadrature_index_orientation(dataset):
    """Resolve complex-line handedness in index order, not physical Hz order.

    Canonical negative DFT output has +1 handedness. Sample reversal changes it;
    a coordinate-only reference reflection does not. Older constructed frequency
    datasets retain their historical sign(step) convention. For older operation
    records, undo only coordinate reflections after the most recent FFT so that
    saved pre-field native spectra retain their original quadrature meaning.
    """
    return _quadrature_orientation(dataset.axes, dataset.complex_axes, dataset.metadata, dataset.provenance)


def _quadrature_orientation(axis_records, complex_axes, metadata, history):
    axes = {a.id: a for a in axis_records
            if a.domain == "frequency" and a.id in complex_axes}
    if _ORIENTATION_KEY in metadata:
        declared = metadata[_ORIENTATION_KEY]
        if (not isinstance(declared, dict) or set(declared) != set(axes) or
                any(type(value) is not int or value not in (-1, 1) for value in declared.values())):
            raise NMRNextError("INVALID_QUADRATURE_ORIENTATION",
                               "quadrature_index_orientation must map every current frequency complex axis to integer +1 or -1")
        return dict(declared)
    result = {identity: 1 if axis.step > 0 else -1 for identity, axis in axes.items()}
    for identity in axes:
        for entry in reversed(history):
            if not isinstance(entry, dict):
                continue
            operation = entry.get("operation")
            if not isinstance(operation, dict) or operation.get("axis") != identity:
                continue
            if operation.get("op") == "fft":
                if type(operation.get("inverse", False)) is not bool:
                    raise NMRNextError("INVALID_QUADRATURE_ORIENTATION", "Recorded FFT inverse flag must be boolean")
                break
            if operation.get("op") == "reference":
                scale = operation.get("frequency_scale", 1)
                if type(scale) is not int or scale not in (-1, 1):
                    raise NMRNextError("INVALID_QUADRATURE_ORIENTATION", "Recorded reference frequency_scale must be integer +1 or -1")
                result[identity] *= scale
    return result


def _transition_metadata(axes, complex_axes, metadata, provenance, op):
    """Pure state transition shared by header plans and numeric execution."""
    # Ordinary transitions replace only top-level orientation state. Nested
    # validated vendor records are read-only internally and copied at public
    # Dataset boundaries. Filter correction explicitly copies its nested edits.
    result = dict(metadata)
    orientation = _quadrature_orientation(axes, complex_axes, metadata, provenance)
    if op["op"] == "fft":
        if op["inverse"]:
            orientation.pop(op["axis"], None)
        else:
            orientation[op["axis"]] = 1
    elif op["op"] == "reverse" and op["axis"] in orientation:
        orientation[op["axis"]] *= -1
    elif op["op"] == "discard_imaginary":
        orientation.pop(op["axis"], None)
    if orientation:
        result[_ORIENTATION_KEY] = orientation
    else:
        result.pop(_ORIENTATION_KEY, None)
    if op["op"] == "digital_filter":
        result = corrected_metadata(result, correction_spec(axes, complex_axes, metadata))
    return result


def validate_backend(backend, threads):
    positive_int(threads, "threads")
    # The native boundary accepts py::ssize_t, whose positive bound is the
    # interpreter's sys.maxsize. Reject overflow before any numerical work.
    if threads > sys.maxsize:
        raise NMRNextError("INVALID_THREADS", f"threads must fit the platform signed size type (maximum {sys.maxsize})")
    if backend == "numpy":
        if threads != 1:
            raise NMRNextError("UNSUPPORTED_THREADS", "NumPy bootstrap accepts threads=1 only; select native for controlled FFT threads")
        return {"name": "numpy", "version": np.__version__, "status": "experimental", "threads": 1,
                "thread_policy": "no engine parallelism; external runtime pools are not controlled"}
    if backend == "native":
        try:
            module = importlib.import_module("nmrnext._native")
        except ImportError as exc:
            raise NMRNextError("BACKEND_UNAVAILABLE", "Native FFT module unavailable; build the C++ backend or explicitly select experimental numpy") from exc
        return {"name": "native", "version": str(getattr(module, "__version__", "unreported")),
                "pocketfft_commit": str(getattr(module, "pocketfft_commit", "unreported")),
                "status": "experimental", "threads": threads}
    raise NMRNextError("UNSUPPORTED_BACKEND", f"Unknown backend {backend!r}")


def _transition_axes(axes, complex_axes, metadata, operation):
    """Validate parameters and derive metadata without allocating sample arrays."""
    if not isinstance(operation, dict) or not isinstance(operation.get("op"), str):
        raise NMRNextError("INVALID_OPERATION", "Each operation must be an object with an op identifier")
    name = operation["op"]
    if name not in _FIELDS:
        raise NMRNextError("UNSUPPORTED_OPERATION", f"Unsupported operation {name!r}")
    if set(operation) - (_FIELDS[name] | {"op", "version"}):
        raise NMRNextError("UNKNOWN_PARAMETER", f"Unknown parameter for {name}")
    if operation.get("version", "1") != "1":
        raise NMRNextError("UNSUPPORTED_OPERATION_VERSION", "Operation version must be '1'")
    op = dict(operation)
    op["version"] = "1"
    imported = metadata.get("import", {})
    filter_state = imported.get("digital_filter") if isinstance(imported, dict) else None
    if name != "digital_filter" and filter_state is not None and (
            not isinstance(filter_state, dict) or filter_state.get("state") != "corrected"):
        raise NMRNextError("DIGITAL_FILTER_UNRESOLVED", "Imported digital filter is unresolved; apply explicit digital_filter correction to the original acquisition before other processing")
    ids = [a.id for a in axes]
    if name == "transpose":
        order = op.get("order")
        if not isinstance(order, (list, tuple)) or len(order) != len(ids) or not all(isinstance(i, str) for i in order) or set(order) != set(ids):
            raise NMRNextError("INVALID_AXIS_ORDER", "Transpose order must list every physical axis exactly once")
        op["order"] = list(order)
        return tuple(axes[ids.index(i)] for i in order), op
    if not isinstance(op.get("axis"), str) or op["axis"] not in ids:
        raise NMRNextError("UNKNOWN_AXIS", f"Operation {name} requires an existing named axis; available {ids}")
    i = ids.index(op["axis"])
    a = axes[i]
    new = a
    if name in {"window", "zero_fill", "fft", "phase"} and a.role != "spectral":
        raise NMRNextError("INVALID_AXIS_ROLE", f"{name} requires a spectral axis; {a.id} is {a.role}")
    if name in {"window", "zero_fill"} and a.domain != "time":
        raise NMRNextError("INVALID_DOMAIN", f"{name} requires a time-domain axis")
    if name in {"fft", "phase"} and a.id not in complex_axes:
        raise NMRNextError("QUADRATURE_REQUIRED", f"{name} needs explicit complex components for {a.id}")
    if name == "reference":
        if a.role != "spectral" or a.domain != "frequency":
            raise NMRNextError("INVALID_DOMAIN", "Reference requires a frequency-domain spectral axis")
        op["reference_ppm"] = finite(op.get("reference_ppm"), "reference_ppm")
        op["reference_hz"] = finite(op.get("reference_hz"), "reference_hz")
        op["observation_mhz"] = finite(op.get("observation_mhz", a.observation_mhz), "observation_mhz")
        if op["observation_mhz"] <= 0:
            raise NMRNextError("INVALID_PARAMETER", "Reference observation_mhz must be positive")
        op["frequency_scale"] = op.get("frequency_scale", 1)
        if type(op["frequency_scale"]) is not int or op["frequency_scale"] not in (-1, 1):
            raise NMRNextError("INVALID_PARAMETER", "Reference frequency_scale must be integer +1 or -1")
        start, step = a.start, a.step
        if "spectral_width_hz" in op:
            op["spectral_width_hz"] = finite(op["spectral_width_hz"], "spectral_width_hz")
            if op["spectral_width_hz"] <= 0:
                raise NMRNextError("INVALID_PARAMETER", "Reference spectral_width_hz must be positive")
            finite(a.size*abs(a.step), "prior_spectral_width_hz")
            # Width means N bins of this current grid, including after a crop;
            # it is neither endpoint span nor the original acquisition width.
            # Preserve index positions relative to zero Hz. Acquisition dwell
            # remains source history, so inverse FFT keeps its canonical guard.
            step = math.copysign(op["spectral_width_hz"] / a.size, a.step)
            if step != a.step:
                start = (a.start / a.step) * step
        new = replace(a, start=start*op["frequency_scale"], step=step*op["frequency_scale"],
                      reference_ppm=op["reference_ppm"], reference_hz=op["reference_hz"], observation_mhz=op["observation_mhz"])
    elif name == "digital_filter":
        spec = correction_spec(axes, complex_axes, metadata)
        if a.id != spec["axis"]:
            raise NMRNextError("INVALID_DIGITAL_FILTER_AXIS", "Digital filter correction requires the acquired direct axis")
        new = replace(a, size=spec["output_complex_points"])
    elif name == "discard_imaginary":
        if a.role != "spectral" or a.domain != "frequency":
            raise NMRNextError("INVALID_DOMAIN", "Imaginary discard requires a frequency-domain spectral axis")
        if a.id not in complex_axes:
            raise NMRNextError("QUADRATURE_REQUIRED", "Imaginary discard requires a currently complex axis")
        new = replace(a, encoding="real")
    elif name == "window":
        kind = op.get("kind", "exponential")
        if a.step <= 0:
            raise NMRNextError("INVALID_TIME_GRID", "Window requires a forward time grid")
        op["kind"] = kind
        op["first_point_scale"] = finite(op.get("first_point_scale", 1), "first_point_scale")
        if kind == "exponential":
            if any(key in op for key in ("power", "off", "end")):
                raise NMRNextError("INVALID_PARAMETER", "power, off and end are only valid for sine window")
            op["lb_hz"] = finite(op.get("lb_hz", 0), "lb_hz")
            if op["lb_hz"] < 0:
                raise NMRNextError("INVALID_PARAMETER", "lb_hz must be nonnegative")
        elif kind == "sine":
            if "lb_hz" in op:
                raise NMRNextError("INVALID_PARAMETER", "lb_hz is only valid for exponential window")
            op["power"] = finite(op.get("power", 1), "power")
            op["off"] = finite(op.get("off", 0), "off")
            op["end"] = finite(op.get("end", 1), "end")
            if not 0 <= op["off"] <= op["end"] <= 1:
                raise NMRNextError("INVALID_PARAMETER", "Sine window requires 0 <= off <= end <= 1")
            if op["power"] <= 0 or a.size < 2:
                raise NMRNextError("INVALID_PARAMETER", "Sine window needs positive power and at least two points")
        else:
            raise NMRNextError("UNSUPPORTED_WINDOW", f"Unsupported window {kind!r}")
    elif name == "zero_fill":
        n = positive_int(op.get("size"), "zero_fill.size")
        if n < a.size:
            raise NMRNextError("INVALID_SIZE", "Zero fill cannot crop acquired samples")
        new = replace(a, size=n, acquired_size=a.acquired_size or a.size)
    elif name == "fft":
        inv = op.get("inverse", False)
        if not isinstance(inv, bool):
            raise NMRNextError("INVALID_PARAMETER", "inverse must be a Boolean")
        op["inverse"] = inv
        sampling = metadata.get("sampling", "fully_sampled")
        if sampling != "fully_sampled" and sampling != {"mode": "fully_sampled"}:
            raise NMRNextError("UNSUPPORTED_SAMPLING", "FFT requires fully sampled data; NUS reconstruction is not implemented")
        if inv:
            dt = a.dwell_seconds
            if a.domain != "frequency" or dt is None:
                raise NMRNextError("INVALID_DOMAIN", "Inverse FFT requires frequency data with recorded acquisition dwell")
            step = 1 / (a.size * dt)
            start = -(a.size // 2) * step
            if not math.isclose(a.step, step, rel_tol=1e-12, abs_tol=1e-14) or not math.isclose(a.start, start, rel_tol=1e-12, abs_tol=1e-14):
                raise NMRNextError("INVALID_FREQUENCY_GRID", "Inverse requires a complete canonical FFT frequency grid; crop/reverse cannot be guessed")
            origin = a.transform_time_origin_seconds if a.transform_time_origin_seconds is not None else a.time_origin_seconds
            new = replace(a, domain="time", unit="s", start=origin, step=dt)
        else:
            if a.domain != "time" or a.step <= 0:
                raise NMRNextError("INVALID_DOMAIN", "Forward FFT requires time data with positive dwell")
            step = 1 / (a.size * a.step)
            new = replace(a, domain="frequency", unit="Hz", start=-(a.size // 2)*step,
                          step=step, dwell_seconds=a.step, transform_time_origin_seconds=a.start,
                          acquired_size=a.acquired_size or a.size)
    elif name == "phase":
        if a.domain not in {"time", "frequency"}:
            raise NMRNextError("INVALID_DOMAIN", "Phase requires a time or frequency axis")
        for key in ("p0", "p1", "pivot"):
            op[key] = finite(op.get(key, 0), key)
    elif name == "crop":
        lo, hi = op.get("start"), op.get("stop")
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (lo, hi)) or not (0 <= lo < hi <= a.size):
            raise NMRNextError("INVALID_REGION", "Crop requires integer 0 <= start < stop <= axis size")
        new = replace(a, size=hi-lo, start=a.start+lo*a.step)
    elif name == "reverse":
        new = replace(a, start=a.start+(a.size-1)*a.step, step=-a.step)
    updated = list(axes)
    updated[i] = new
    return tuple(updated), op


def _transition(axes, complex_axes, metadata, provenance, operation):
    """One pure axes, quadrature and metadata transition for plan and execution."""
    new_axes, op = _transition_axes(axes, complex_axes, metadata, operation)
    new_bits = tuple(a for a in complex_axes if a != op["axis"]) if op["op"] == "discard_imaginary" else complex_axes
    new_metadata = _transition_metadata(axes, complex_axes, metadata, provenance, op)
    return new_axes, new_bits, new_metadata, op


def _pairs(dataset, axis_id):
    mask = 1 << dataset.complex_axes.index(axis_id)
    for real in range(dataset.data.shape[0]):
        if not real & mask:
            yield real, real | mask


class _ExecutionState:
    """Private recipe state; writable samples never cross a public boundary."""
    __slots__ = ("data", "axes", "complex_axes", "metadata", "provenance")

    def __init__(self, data, axes, complex_axes, metadata, provenance):
        self.data, self.axes, self.complex_axes = data, axes, complex_axes
        self.metadata, self.provenance = metadata, provenance

    axis_index = Dataset.axis_index
    axis = Dataset.axis


class _ExecutionBuffers:
    """One complex packing buffer, reused within a single synchronous run."""
    def __init__(self):
        self.complex = None

    def complex_array(self, shape, dtype):
        dtype = np.dtype(np.complex64 if dtype == np.float32 else np.complex128)
        if self.complex is None or self.complex.shape != shape or self.complex.dtype != dtype:
            self.complex = np.empty(shape, dtype=dtype)
        return self.complex


def _execution_records(dataset):
    if _has_immutable_storage(dataset):
        return dataset._metadata, dataset._provenance
    return dataset.metadata, dataset.provenance


def _operation_entry(axes, complex_axes, input_shape, output_shape, dtype, op, backend_info,
                     *, output_axes, output_complex_axes, digital_filter_spec=None):
    """Build the unchanged scientific history using scalar global array facts."""
    name = op["op"]
    entry = {"operation": op, "backend": backend_info, "input_shape": list(input_shape),
             "output_shape": list(output_shape), "dtype": str(dtype)}
    if digital_filter_spec is not None:
        entry["digital_filter"] = digital_filter_spec
    if name == "reference" and "spectral_width_hz" in op:
        prior = next(axis for axis in axes if axis.id == op["axis"])
        calibrated = next(axis for axis in output_axes if axis.id == op["axis"])
        def grid(axis):
            return {"size":axis.size, "start_hz":axis.start, "step_hz":axis.step,
                    "spectral_width_hz":axis.size*abs(axis.step)}
        entry["reference_calibration"] = {
            "requested_spectral_width_hz":op["spectral_width_hz"],
            "width_definition":"current grid size times absolute bin spacing; scale about zero Hz",
            "prior_grid":grid(prior), "new_grid":grid(calibrated),
            "acquisition_dwell_seconds":prior.dwell_seconds}
    if name == "discard_imaginary":
        mask = 1 << complex_axes.index(op["axis"])
        selected = [c for c in range(input_shape[0]) if not c & mask]
        entry["losses"] = ["discarded_imaginary_quadrature"]
        entry["discard_imaginary"] = {"axis":op["axis"], "input_complex_axes":list(complex_axes),
                                      "output_complex_axes":list(output_complex_axes), "retained_input_components":selected,
                                      "input_axis_encoding":next(axis.encoding for axis in axes if axis.id == op["axis"])}
    return entry


def _execute(dataset, op, new_axes, new_bits, new_metadata, backend, threads, backend_info,
             *, public=True, buffers=None):
    name = op["op"]
    data = dataset.data
    metadata, provenance = _execution_records(dataset)
    reusable = isinstance(dataset, _ExecutionState) and data.flags.writeable
    spec = None
    if name == "reference":
        result = data
    elif name == "discard_imaginary":
        mask = 1 << dataset.complex_axes.index(op["axis"])
        selected = [c for c in range(data.shape[0]) if not c & mask]
        result = data[selected]
    elif name == "digital_filter":
        spec = correction_spec(dataset.axes, dataset.complex_axes, metadata)
        i = dataset.axis_index(spec["axis"])
        shape = list(data.shape)
        shape[i+1] = spec["output_complex_points"]
        result = np.empty(shape, dtype=data.dtype)
        def transform(values, inverse):
            if backend == "native":
                return importlib.import_module("nmrnext._native").fft_complex(np.ascontiguousarray(values), values.ndim-1, inverse=inverse, threads=threads)
            return (np.fft.ifft if inverse else np.fft.fft)(values, axis=-1)
        for real, imag in _pairs(dataset, spec["axis"]):
            z = np.moveaxis(data[real].astype(np.complex128) + 1j*data[imag], i, -1)
            corrected = np.moveaxis(correct_samples(z, spec, transform), -1, i)
            result[real], result[imag] = corrected.real, corrected.imag
    elif name == "transpose":
        order = [dataset.axis_index(i) + 1 for i in op["order"]]
        result = np.transpose(data, (0, *order))
    else:
        i = dataset.axis_index(op["axis"])
        a = dataset.axes[i]
        if name == "zero_fill":
            shape = list(data.shape); shape[i+1] = op["size"]
            result = np.zeros(shape, dtype=data.dtype)
            region = [slice(None)]*data.ndim; region[i+1] = slice(0,a.size)
            result[tuple(region)] = data
        elif name == "crop":
            region = [slice(None)]*data.ndim; region[i+1] = slice(op["start"],op["stop"])
            result = data[tuple(region)]
        elif name == "reverse":
            result = np.flip(data, axis=i+1)
        elif name == "window":
            k = np.arange(a.size, dtype=np.float64)
            if op["kind"] == "exponential":
                window = np.exp(-np.pi*op["lb_hz"]*k*a.step)
            else:
                if op["off"] == 0 and op["end"] == 1:
                    # Keep the original default expression bit-for-bit.
                    window = np.sin(np.pi*k/(a.size-1))**op["power"]
                else:
                    sine = np.sin(np.pi*op["off"]+np.pi*(op["end"]-op["off"])*k/(a.size-1))
                    # The validated angle interval is [0, pi]. Arithmetic
                    # can put its endpoint just above pi, producing a tiny
                    # negative sine that is invalid for fractional powers.
                    # Clip only that roundoff scale, not arbitrary negatives.
                    rounded_negative = (sine < 0) & (sine >= -4*np.finfo(np.float64).eps)
                    window = np.where(rounded_negative, 0., sine)**op["power"]
            window[0] *= op["first_point_scale"]
            shape = [1]*data.ndim; shape[i+1] = a.size
            weights = window.astype(data.dtype).reshape(shape)
            result = np.multiply(data, weights, out=data if reusable else None)
        else:
            result = data if reusable else np.empty_like(data)
            buffers = _ExecutionBuffers() if buffers is None else buffers
            packed = buffers.complex_array(data.shape[1:], data.dtype)
            if name == "phase":
                phase = np.deg2rad(op["p0"]+op["p1"]*(np.arange(a.size)-op["pivot"])/a.size)
                shape = [1]*packed.ndim; shape[i] = a.size
                phase_factor = np.exp(1j*phase).reshape(shape)
            for real, imag in _pairs(dataset, a.id):
                z = packed
                z.real, z.imag = data[real], data[imag]
                if name == "fft":
                    inv = op["inverse"]
                    if inv:
                        z = np.fft.ifftshift(z, axes=i)
                    if backend == "native":
                        transformed = importlib.import_module("nmrnext._native").fft_complex(z, i, inverse=inv, threads=threads)
                    else:
                        transformed = (np.fft.ifft if inv else np.fft.fft)(z, axis=i)
                    if not inv:
                        transformed = np.fft.fftshift(transformed, axes=i)
                else:
                    transformed = z*phase_factor
                result[real], result[imag] = transformed.real, transformed.imag
    # Public construction checks once while freezing. Private arithmetic needs
    # its own check before another operation can hide a nonfinite result.
    legacy_boundary = isinstance(dataset, Dataset) and not _has_immutable_storage(dataset)
    if (legacy_boundary or (not public and name in {"window", "phase", "fft", "digital_filter"})) and not np.isfinite(result).all():
        raise NMRNextError("NUMERICAL_OVERFLOW", f"Operation {name} produced nonfinite values")
    metadata = new_metadata
    entry_axes = dataset.axes
    if legacy_boundary and (name == "discard_imaginary" or (name == "reference" and "spectral_width_hz" in op)):
        # Preserve the pre-extraction public axis() annotation hook too.
        entry_axes = tuple(dataset.axis(axis.id) if axis.id == op["axis"] else axis for axis in entry_axes)
    entry = _operation_entry(entry_axes, dataset.complex_axes, data.shape, result.shape,
                             result.dtype, op, backend_info, output_axes=new_axes,
                             output_complex_axes=new_bits, digital_filter_spec=spec)
    history = provenance+[entry]
    if name == "reference" and _has_immutable_storage(dataset):
        return dataset._share_samples(new_axes, new_bits, metadata, history)
    if public:
        try:
            return Dataset(result, new_axes, new_bits, metadata, history)
        except NMRNextError as exc:
            if exc.code == "NONFINITE_DATA":
                raise NMRNextError("NUMERICAL_OVERFLOW", f"Operation {name} produced nonfinite values") from exc
            raise
    return _ExecutionState(result, new_axes, new_bits, metadata, history)


def run_operations(dataset, operations, *, backend="numpy", threads=1, memory_bytes=None):
    from .planning import plan_operations
    if not isinstance(dataset, Dataset):
        raise NMRNextError("INVALID_DATASET", "Execution requires numeric sample data; a DatasetHeader is planning-only")
    quadrature_index_orientation(dataset)
    plan = plan_operations(dataset, operations, backend=backend, threads=threads, memory_bytes=memory_bytes)
    return _run_plan(dataset, plan)


def _run_plan(dataset, plan, *, start=0, on_step=None, snapshot_steps=None):
    """Execute a preflighted plan; selected callbacks receive immutable snapshots.

    Private callers must preflight the complete recipe against its original
    source, even when resuming from a previously frozen matching prefix.
    """
    current = dataset
    legacy_boundaries = not _has_immutable_storage(dataset)
    buffers = _ExecutionBuffers()
    steps = plan["steps"]
    last_numeric = max((i for i in range(start, len(steps))
                        if steps[i]["operation"]["op"] != "reference"), default=-1)
    for index in range(start, len(steps)):
        step = steps[index]
        metadata, provenance = _execution_records(current)
        new_axes, new_bits, new_metadata, op = _transition(current.axes, current.complex_axes, metadata, provenance, step["operation"])
        snapshot = on_step is not None and (snapshot_steps is None or index in snapshot_steps)
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                current = _execute(current, op, new_axes, new_bits, new_metadata,
                                   plan["backend"]["name"], plan["backend"]["threads"], plan["backend"],
                                   public=legacy_boundaries or snapshot or index >= last_numeric, buffers=buffers)
        except (FloatingPointError, OverflowError) as exc:
            raise NMRNextError("NUMERICAL_OVERFLOW", f"Operation {op['op']} overflowed; use float64 or revise parameters") from exc
        except MemoryError as exc:
            raise NMRNextError("MEMORY_ALLOCATION_FAILED", "Working arrays could not be allocated; use a smaller dataset") from exc
        if snapshot:
            on_step(index, current)
    return current


def apply_operation(dataset, operation, *, backend="numpy", threads=1):
    return run_operations(dataset, [operation], backend=backend, threads=threads)
