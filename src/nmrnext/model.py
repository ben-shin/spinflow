"""Schema 1: independent quadrature bits and stable physical axes."""
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import math
from numbers import Real

import numpy as np

from .errors import NMRNextError


def finite(value, field):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise NMRNextError("INVALID_NUMBER", f"{field} must be a finite number")
    return float(value)


def positive_int(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise NMRNextError("INVALID_INTEGER", f"{field} must be a positive integer")
    return value


def json_copy(value, field):
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError, OverflowError) as exc:
        raise NMRNextError("INVALID_METADATA", f"{field} must contain finite JSON data") from exc


@dataclass(frozen=True, slots=True)
class Axis:
    id: str
    size: int
    role: str = "spectral"
    domain: str = "time"
    label: str = ""
    nucleus: str | None = None
    unit: str = "s"
    start: float = 0.0
    step: float = 1.0
    observation_mhz: float | None = None
    reference_ppm: float | None = None
    reference_hz: float = 0.0
    encoding: str = "complex"
    time_origin_seconds: float | None = None
    dwell_seconds: float | None = None
    acquired_size: int | None = None
    transform_time_origin_seconds: float | None = None

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip():
            raise NMRNextError("INVALID_AXIS", "Axis ID must be a nonempty string")
        positive_int(self.size, "axis size")
        if self.role not in ("spectral", "series") or self.domain not in ("time", "frequency", "index"):
            raise NMRNextError("INVALID_AXIS", "Unsupported axis role or domain")
        if not isinstance(self.label, str) or (self.nucleus is not None and not isinstance(self.nucleus, str)):
            raise NMRNextError("INVALID_AXIS", "Axis labels and nuclei must be strings")
        if not isinstance(self.unit, str) or not self.unit:
            raise NMRNextError("INVALID_AXIS", "Axis unit must be declared")
        if self.domain == "time" and self.unit != "s":
            raise NMRNextError("INVALID_UNIT", "Time axes require seconds (s)")
        if self.domain == "frequency" and self.unit != "Hz":
            raise NMRNextError("INVALID_UNIT", "Frequency axes require Hz")
        if self.encoding not in ("complex", "states", "real", "none"):
            raise NMRNextError("UNSUPPORTED_ENCODING", f"Unsupported encoding {self.encoding!r}")
        if self.time_origin_seconds is None:
            object.__setattr__(self, "time_origin_seconds", self.start if self.domain == "time" else 0.)
        for name in ("start", "step", "reference_hz", "time_origin_seconds"):
            object.__setattr__(self, name, finite(getattr(self, name), name))
        if self.step == 0:
            raise NMRNextError("INVALID_AXIS", "Axis step cannot be zero")
        for name in ("observation_mhz", "reference_ppm", "dwell_seconds", "transform_time_origin_seconds"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, finite(value, name))
        if self.observation_mhz is not None and self.observation_mhz <= 0:
            raise NMRNextError("INVALID_AXIS", "Observation frequency must be positive")
        if self.dwell_seconds is not None and self.dwell_seconds <= 0:
            raise NMRNextError("INVALID_AXIS", "Acquisition dwell must be positive")
        if self.acquired_size is None:
            object.__setattr__(self, "acquired_size", self.size)
        positive_int(self.acquired_size, "acquired_size")

    def to_dict(self):
        return asdict(self)


class Dataset:
    """Owned immutable array with copied JSON metadata at public boundaries."""
    __slots__ = ("_data", "_axes", "_complex_axes", "_metadata", "_provenance")

    def __init__(self, data, axes, complex_axes=(), metadata=None, provenance=None):
        try:
            axes = tuple(axes)
            bits = tuple(complex_axes)
        except TypeError as exc:
            raise NMRNextError("INVALID_AXIS", "axes and complex_axes must be sequences") from exc
        if not axes or not all(isinstance(a, Axis) for a in axes):
            raise NMRNextError("INVALID_AXIS", "Dataset requires at least one valid Axis")
        ids = tuple(a.id for a in axes)
        if len(set(ids)) != len(ids):
            raise NMRNextError("DUPLICATE_AXIS", "Logical axis IDs must be unique")
        if not all(isinstance(b, str) for b in bits) or len(set(bits)) != len(bits) or not set(bits).issubset(ids):
            raise NMRNextError("INVALID_COMPONENTS", "Quadrature axes must be unique existing axis IDs")
        for a in axes:
            if a.id in bits and a.encoding in {"real", "none"}:
                raise NMRNextError("INVALID_COMPONENTS", f"Axis {a.id} encoding contradicts quadrature data")
        arr = np.asarray(data)
        if arr.dtype not in (np.dtype("float32"), np.dtype("float64")):
            raise NMRNextError("INVALID_DTYPE", "Component arrays require real float32 or float64")
        expected = (2 ** len(bits), *(a.size for a in axes))
        if arr.shape != expected:
            raise NMRNextError("INVALID_SHAPE", f"Expected {expected}, received {arr.shape}")
        if not np.isfinite(arr).all():
            raise NMRNextError("NONFINITE_DATA", "Dataset contains NaN or infinity")
        # A bytes owner prevents callers from simply turning writeability back on.
        self._data = np.frombuffer(arr.tobytes(order="C"), dtype=arr.dtype).reshape(expected)
        self._axes, self._complex_axes = axes, bits
        self._metadata = json_copy({} if metadata is None else metadata, "metadata")
        self._provenance = json_copy([] if provenance is None else provenance, "provenance")
        if not isinstance(self._metadata, dict) or not isinstance(self._provenance, list):
            raise NMRNextError("INVALID_METADATA", "Metadata is an object and provenance is a list")

    @property
    def data(self):
        return self._data

    @property
    def axes(self):
        return self._axes

    @property
    def complex_axes(self):
        return self._complex_axes

    @property
    def metadata(self):
        return deepcopy(self._metadata)

    @property
    def provenance(self):
        return deepcopy(self._provenance)

    def axis_index(self, axis_id):
        for i, axis in enumerate(self.axes):
            if axis.id == axis_id:
                return i
        raise NMRNextError("UNKNOWN_AXIS", f"Unknown axis {axis_id!r}; available: {[a.id for a in self.axes]}")

    def axis(self, axis_id):
        return self.axes[self.axis_index(axis_id)]

    def coordinates(self, axis_id, unit=None):
        a = self.axis(axis_id)
        values = a.start + np.arange(a.size, dtype=np.float64) * a.step
        if unit is None or unit == a.unit:
            return values
        if unit == "ppm" and a.domain == "frequency" and a.observation_mhz is not None and a.reference_ppm is not None:
            return a.reference_ppm + (values - a.reference_hz) / a.observation_mhz
        raise NMRNextError("UNAVAILABLE_COORDINATES", f"Axis {axis_id} cannot be expressed in {unit}")

    def to_metadata(self):
        return {"schema_version": "1", "axes": [a.to_dict() for a in self.axes],
                "complex_axes": list(self.complex_axes), "metadata": self.metadata,
                "provenance": self.provenance}

    @classmethod
    def from_metadata(cls, data, record):
        if not isinstance(record, dict) or record.get("schema_version") != "1":
            raise NMRNextError("UNSUPPORTED_SCHEMA", "Expected numeric schema version 1")
        allowed = {"schema_version", "axes", "complex_axes", "metadata", "provenance"}
        if set(record) != allowed:
            raise NMRNextError("INVALID_METADATA", "Numeric schema fields are missing or unknown")
        try:
            return cls(data, tuple(Axis(**a) for a in record["axes"]), record["complex_axes"],
                       record["metadata"], record["provenance"])
        except (KeyError, TypeError) as exc:
            raise NMRNextError("INVALID_METADATA", "Malformed numeric axis schema") from exc
