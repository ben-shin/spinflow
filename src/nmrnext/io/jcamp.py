"""Read one bounded JCAMP-DX 5.00 real 1D NMR spectrum in AFFN form.

NMR protocol: https://iupac.org/wp-content/uploads/2021/08/JCAMP-DX_NMR_1993.pdf
Only XYDATA (X++(Y..Y)), Hz and arbitrary ordinate units are accepted.
Chemical-shift reference remains unknown; no reference is inferred from Hz.
"""
import hashlib
import math
from pathlib import Path
import re

import numpy as np

from nmrnext.errors import NMRNextError
from nmrnext.model import Axis, Dataset


_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?\Z")
_MAX_SOURCE = 16 * 1024 * 1024


def _number(value, name):
    if not _NUMBER.fullmatch(value):
        raise NMRNextError("INVALID_HEADER", f"JCAMP {name} must be a finite AFFN number")
    result = float(value)
    if not math.isfinite(result):
        raise NMRNextError("INVALID_HEADER", f"JCAMP {name} must be finite")
    return result


def inspect_jcamp(path, max_bytes):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise NMRNextError("UNSUPPORTED_PROFILE", "JCAMP input must be a regular file")
    with path.open("rb") as stream:
        raw = stream.read(_MAX_SOURCE + 1)
    if len(raw) > _MAX_SOURCE:
        raise NMRNextError("MEMORY_LIMIT", "JCAMP source exceeds the 16 MiB profile limit")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise NMRNextError("INVALID_HEADER", "JCAMP profile requires ASCII text") from exc
    fields, rows = {}, []
    table = False
    for line in text.splitlines():
        line = line.split("$$", 1)[0].strip()
        if not line:
            continue
        if line.startswith("##"):
            if "END" in fields:
                raise NMRNextError("INVALID_HEADER", "Content follows JCAMP END")
            if "=" not in line:
                raise NMRNextError("INVALID_HEADER", "Malformed JCAMP label")
            key, value = line[2:].split("=", 1)
            key = re.sub(r"[ _-]", "", key).upper()
            if not key or key in fields:
                raise NMRNextError("INVALID_HEADER", "Duplicate or empty JCAMP label")
            fields[key] = value.strip()
            table = key == "XYDATA"
        elif table:
            rows.append(line)
        else:
            raise NMRNextError("INVALID_HEADER", "Unexpected JCAMP continuation")
    required = {"TITLE", "JCAMPDX", "DATATYPE", "DATACLASS", ".OBSERVEFREQUENCY",
                ".OBSERVENUCLEUS", "XUNITS", "YUNITS", "XFACTOR", "YFACTOR",
                "FIRSTX", "LASTX", "NPOINTS", "XYDATA", "END"}
    if not required <= fields.keys():
        raise NMRNextError("INVALID_HEADER", "Missing required JCAMP spectrum labels")
    normalized = lambda s: re.sub(r"\s+", "", s).upper()
    if (fields["JCAMPDX"] != "5.00" or normalized(fields["DATATYPE"]) != "NMRSPECTRUM"
            or normalized(fields["DATACLASS"]) != "XYDATA" or normalized(fields["XUNITS"]) != "HZ"
            or normalized(fields["YUNITS"]) != "ARBITRARYUNITS"
            or normalized(fields["XYDATA"]) != "(X++(Y..Y))"
            or not fields[".OBSERVENUCLEUS"].strip()
            or any(key in fields for key in ("BLOCKS", "NTUPLES", "DATATABLE", "XYPOINTS", "PEAKTABLE", "PEAKASSIGNMENTS"))):
        raise NMRNextError("UNSUPPORTED_PROFILE", "Expected one real 1D JCAMP 5.00 NMR spectrum with AFFN XYDATA and Hz axes")
    first = _number(fields["FIRSTX"], "FIRSTX")
    last = _number(fields["LASTX"], "LASTX")
    xf = _number(fields["XFACTOR"], "XFACTOR")
    yf = _number(fields["YFACTOR"], "YFACTOR")
    obs = _number(fields[".OBSERVEFREQUENCY"], ".OBSERVE FREQUENCY")
    count_value = _number(fields["NPOINTS"], "NPOINTS")
    if not count_value.is_integer() or not 2 <= count_value <= max_bytes // 8:
        raise NMRNextError("MEMORY_LIMIT", "JCAMP point count is invalid or exceeds the numeric budget")
    count = int(count_value)
    step = (last - first) / (count - 1)
    if not math.isfinite(step) or step == 0 or xf == 0 or yf == 0 or obs <= 0:
        raise NMRNextError("INVALID_HEADER", "JCAMP axis, scale and observation frequency must be finite and nonzero")
    # The permitted discrepancy is relative to one bin, never the absolute
    # axis origin. Reject grids whose floating precision cannot resolve it.
    rounding = 4 * max(math.ulp(first), math.ulp(last), math.ulp(step))
    if rounding > abs(step) * 0.01:
        raise NMRNextError("INVALID_HEADER", "JCAMP grid cannot be resolved reliably in float64")
    values = []
    for row in rows:
        tokens = row.replace(",", " ").split()
        if len(tokens) < 2 or len(values) + len(tokens) - 1 > count:
            raise NMRNextError("INVALID_HEADER", "Invalid JCAMP XYDATA row length")
        x = _number(tokens[0], "row X") * xf
        expected = first + len(values) * step
        if not math.isfinite(x):
            raise NMRNextError("INVALID_HEADER", "JCAMP row X exceeds the finite range")
        tolerance = max(abs(step) * 1e-6, rounding, 4 * math.ulp(x), 4 * math.ulp(expected))
        if tolerance > abs(step) * 0.01:
            raise NMRNextError("INVALID_HEADER", "JCAMP grid cannot be resolved reliably in float64")
        if abs(x - expected) > tolerance:
            raise NMRNextError("INVALID_HEADER", "JCAMP row X is inconsistent with the declared grid")
        values.extend(_number(token, "row Y") * yf for token in tokens[1:])
    if len(values) != count or not all(math.isfinite(v) for v in values):
        raise NMRNextError("INVALID_HEADER", "JCAMP ordinate count or scale is invalid")
    if "FIRSTY" in fields and not math.isclose(_number(fields["FIRSTY"], "FIRSTY"), values[0], rel_tol=1e-8, abs_tol=1e-6):
        raise NMRNextError("INVALID_HEADER", "JCAMP FIRSTY disagrees with XYDATA")
    return {"format": "jcamp-real-1d", "version": "5.00 AFFN XYDATA", "shape": [1, count],
            "dtype": "float64", "header": {"first_hz": first, "step_hz": step, "observation_mhz": obs,
            "nucleus": fields[".OBSERVENUCLEUS"], "source_text": text,
            "source_sha256": hashlib.sha256(raw).hexdigest(), "values": values}}


def read_jcamp(path, head):
    with Path(path).open("rb") as stream:
        raw = stream.read(_MAX_SOURCE + 1)
    h = head["header"]
    if hashlib.sha256(raw).hexdigest() != h["source_sha256"]:
        raise NMRNextError("INPUT_CHANGED", "JCAMP input changed between inspection and import")
    axis = Axis(id="a0", size=head["shape"][1], domain="frequency", unit="Hz", start=h["first_hz"],
                step=h["step_hz"], observation_mhz=h["observation_mhz"], reference_ppm=None,
                nucleus=h["nucleus"], label=h["nucleus"], encoding="real")
    return Dataset(np.asarray([h["values"]], dtype=np.float64), (axis,), (),
                   {"import": {"format": "jcamp-real-1d", "source_text": h["source_text"],
                               "source_sha256": h["source_sha256"]}})
