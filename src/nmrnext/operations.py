"""Typed, explicit modern operations; no implicit legacy compatibility."""
from dataclasses import replace
import importlib
import math
import sys

import numpy as np

from .errors import NMRNextError
from .model import Dataset, finite, positive_int
from .bruker import correction_spec, corrected_metadata, correct_samples


_FIELDS = {
    "digital_filter": {"axis"},
    "reference": {"axis", "reference_ppm", "reference_hz", "observation_mhz"},
    "window": {"axis", "kind", "lb_hz", "power", "first_point_scale"},
    "zero_fill": {"axis", "size"}, "fft": {"axis", "inverse"},
    "phase": {"axis", "p0", "p1", "pivot"},
    "crop": {"axis", "start", "stop"}, "reverse": {"axis"},
    "transpose": {"order"},
}


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


def _transition(axes, complex_axes, metadata, operation):
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
        new = replace(a, reference_ppm=op["reference_ppm"], reference_hz=op["reference_hz"], observation_mhz=op["observation_mhz"])
    elif name == "digital_filter":
        spec = correction_spec(axes, complex_axes, metadata)
        if a.id != spec["axis"]:
            raise NMRNextError("INVALID_DIGITAL_FILTER_AXIS", "Digital filter correction requires the acquired direct axis")
        new = replace(a, size=spec["output_complex_points"])
    elif name == "window":
        kind = op.get("kind", "exponential")
        if a.step <= 0:
            raise NMRNextError("INVALID_TIME_GRID", "Window requires a forward time grid")
        op["kind"] = kind
        op["first_point_scale"] = finite(op.get("first_point_scale", 1), "first_point_scale")
        if kind == "exponential":
            if "power" in op:
                raise NMRNextError("INVALID_PARAMETER", "power is only valid for sine window")
            op["lb_hz"] = finite(op.get("lb_hz", 0), "lb_hz")
            if op["lb_hz"] < 0:
                raise NMRNextError("INVALID_PARAMETER", "lb_hz must be nonnegative")
        elif kind == "sine":
            if "lb_hz" in op:
                raise NMRNextError("INVALID_PARAMETER", "lb_hz is only valid for exponential window")
            op["power"] = finite(op.get("power", 1), "power")
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


def _pairs(dataset, axis_id):
    mask = 1 << dataset.complex_axes.index(axis_id)
    for real in range(dataset.data.shape[0]):
        if not real & mask:
            yield real, real | mask


def _execute(dataset, op, new_axes, backend, threads, backend_info):
    name = op["op"]
    data = dataset.data
    metadata = dataset.metadata
    spec = None
    if name == "reference":
        result = data
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
                window = np.sin(np.pi*k/(a.size-1))**op["power"]
            window[0] *= op["first_point_scale"]
            shape = [1]*data.ndim; shape[i+1] = a.size
            result = data*window.astype(data.dtype).reshape(shape)
        else:
            result = np.empty_like(data)
            for real, imag in _pairs(dataset, a.id):
                z = np.empty(data.shape[1:], dtype=np.complex64 if data.dtype == np.float32 else np.complex128)
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
                    phase = np.deg2rad(op["p0"]+op["p1"]*(np.arange(a.size)-op["pivot"])/a.size)
                    shape = [1]*z.ndim; shape[i] = a.size
                    transformed = z*np.exp(1j*phase).reshape(shape)
                result[real], result[imag] = transformed.real, transformed.imag
    if not np.isfinite(result).all():
        raise NMRNextError("NUMERICAL_OVERFLOW", f"Operation {name} produced nonfinite values")
    entry = {"operation": op, "backend": backend_info, "input_shape": list(data.shape),
             "output_shape": list(result.shape), "dtype": str(result.dtype)}
    if spec is not None:
        metadata = corrected_metadata(metadata, spec)
        entry["digital_filter"] = spec
    return Dataset(result, new_axes, dataset.complex_axes, metadata, dataset.provenance+[entry])


def run_operations(dataset, operations, *, backend="numpy", threads=1, memory_bytes=None):
    from .planning import plan_operations
    plan = plan_operations(dataset, operations, backend=backend, threads=threads, memory_bytes=memory_bytes)
    current = dataset
    for step in plan["steps"]:
        new_axes, op = _transition(current.axes, current.complex_axes, current.metadata, step["operation"])
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                current = _execute(current, op, new_axes, backend, threads, plan["backend"])
        except (FloatingPointError, OverflowError) as exc:
            raise NMRNextError("NUMERICAL_OVERFLOW", f"Operation {op['op']} overflowed; use float64 or revise parameters") from exc
        except MemoryError as exc:
            raise NMRNextError("MEMORY_ALLOCATION_FAILED", "Working arrays could not be allocated; use a smaller dataset") from exc
    return current


def apply_operation(dataset, operation, *, backend="numpy", threads=1):
    return run_operations(dataset, [operation], backend=backend, threads=threads)
