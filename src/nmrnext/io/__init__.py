"""Controlled native and processed-spectrum file profiles.

Only declared profiles are accepted. All numeric conversions are explicit and
losses require named acknowledgments before a file is written.
"""
from __future__ import annotations

from dataclasses import fields
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile
import warnings

import h5py
import numpy as np

from nmrnext.errors import NMRNextError
from nmrnext.model import Axis, Dataset


MAX_BYTES = 1 << 30


def _nmrglue():
    """Load the processed/vendor codec only for profiles that use it."""
    import nmrglue
    return nmrglue


_ALIASES = {
    "native": "nmrnext-hdf5", "hdf5": "nmrnext-hdf5", "nmrnext-hdf5": "nmrnext-hdf5",
    "nmrpipe": "nmrpipe-real", "pipe": "nmrpipe-real", "nmrpipe-real": "nmrpipe-real",
    "ucsf": "ucsf-real", "sparky": "ucsf-real", "ucsf-real": "ucsf-real",
    "bruker-raw-1d": "bruker-raw-1d", "bruker1d": "bruker-raw-1d",
    "bruker-raw-2d": "bruker-raw-2d", "bruker2d": "bruker-raw-2d",
    "varian-raw-1d": "varian-raw-1d", "varian1d": "varian-raw-1d", "agilent1d": "varian-raw-1d",
    "varian-raw-2d": "varian-raw-2d", "varian2d": "varian-raw-2d", "agilent2d": "varian-raw-2d",
    "jcamp-real-1d": "jcamp-real-1d", "jcamp": "jcamp-real-1d", "jcampdx": "jcamp-real-1d",
}
_EXTENSIONS = {".nmrh5": "nmrnext-hdf5", ".h5": "nmrnext-hdf5",
               ".ft1": "nmrpipe-real", ".ft2": "nmrpipe-real", ".ucsf": "ucsf-real"}


def capabilities():
    """Exact implemented profiles; a profile is not generic format support."""
    return [
        {"format": "nmrnext-hdf5", "version": "1", "object": "native numeric dataset",
         "dimensions": "1+", "components": "all quadrature channels", "read": True, "write": True,
         "preserved": ["data", "dtype", "axis schema", "metadata", "provenance"], "known_losses": []},
        {"format": "nmrpipe-real", "version": "nmrglue 0.12 writer/header dialect",
         "object": "processed real spectrum", "dimensions": [1, 2], "components": "component 0 (all-real channel)",
         "read": True, "write": True, "preserved": ["float32 intensities", "axis ppm calibration", "axis labels"],
         "known_losses": ["quadrature_projection", "float32_quantization", "metadata", "provenance",
                          "reference_fields", "axis_annotations"], "readback": "nmrglue 0.12 (shared codec family)",
         "external_consumer": "unverified"},
        {"format": "ucsf-real", "version": "UCSF NMR version 2",
         "object": "processed real spectrum", "dimensions": [2], "components": "component 0 (all-real channel)",
         "read": True, "write": True, "preserved": ["float32 intensities", "axis ppm calibration", "axis labels"],
         "known_losses": ["quadrature_projection", "float32_quantization", "metadata", "provenance",
                          "reference_fields", "axis_annotations"], "readback": "nmrglue 0.12 (shared codec family)",
         "external_consumer": "unverified"},
        {"format": "bruker-raw-1d", "version": "controlled acqus/fid profile",
         "object": "raw 1D complex FID", "dimensions": [1], "components": "real and imaginary",
         "read": True, "write": False, "preserved": ["raw acquired points", "raw acqus text", "digital-filter parameters"],
         "known_losses": ["padding outside TD"], "readback": "nmrglue 0.12 Bruker reader",
         "external_consumer": "source acquisition is left untouched"},
        {"format":"bruker-raw-2d", "version":"controlled fully sampled ser profile v1",
         "object":"raw 2D hypercomplex acquisition", "dimensions":[2],
         "components":"RR, IR, RI, II; indirect bit 0, direct bit 1",
         "read":True, "write":False,
         "preserved":["acquisition parameters and text", "source hashes", "quadrature mapping", "digital-filter parameters"],
         "known_losses":["padding outside TD"],
         "supported_modes":["FnMODE=5 States-TPPI", "FnMODE=6 Echo-AntiEcho"],
         "constraints":["AQ_mod=3", "AQSEQ=0", "PARMODE=1", "FnTYPE=0", "DIGMOD=1 when supplied", "SW*SFO1 agrees with SW_h within relative 2e-5 when SW is supplied", "positive recorded GRPDLY", "complete 1024-byte padded traces"],
         "external_consumer":"source acquisition is left untouched"},
        *[{"format":f"varian-raw-{d}d", "version":"1", "read":True, "write":False,
           "object":"raw float32 acquisition", "dimensions":[d],
           "components":"R,I" if d == 1 else "RR,IR,RI,II; indirect bit 0, direct bit 1",
           "constraints":["big endian float32 status 73/201", "one trace/header per block",
                          "unarrayed 1D" if d == 1 else "cpjdcptan2d C13/N15 phase=[1,2] States only"],
           "preserved":["all acquired quadratures", "procpar text and fields", "all block headers", "source hashes"],
           "known_losses":[], "reference_policy":"physical ppm requires explicit reference with frequency_scale=-1 after FFT for this retained-sign profile",
           "external_consumer":"independent archived NMRPipe time-domain reference; no vendor write"}
          for d in (1, 2)],
        {"format":"jcamp-real-1d", "version":"controlled Hz/AFFN profile 1",
         "object":"processed real 1D spectrum", "dimensions":[1], "components":"real only",
         "read":True, "write":False, "preserved":["intensities", "Hz grid", "source text", "source checksum"],
         "known_losses":[], "constraints":["single spectrum block", "Hz units", "AFFN numeric encoding"],
         "external_consumer":"unverified; constructed fixtures only"},
    ]


def _format(name, path, *, reading=False):
    if name is not None:
        result = _ALIASES.get(str(name).lower())
        if result is None:
            raise NMRNextError("UNSUPPORTED_FORMAT", f"Unsupported format {name!r}")
        return result
    if reading:
        if Path(path).is_dir():
            if (Path(path)/"procpar").is_file() and (Path(path)/"fid").is_file():
                if (Path(path)/"acqus").exists():
                    raise NMRNextError("AMBIGUOUS_FORMAT", "Directory contains both Varian and Bruker parameters")
                # Only use the small binary header to select dimensionality;
                # the selected profile still validates every acquisition field.
                with (Path(path)/"fid").open("rb") as handle:
                    raw = handle.read(4)
                if len(raw) != 4:
                    raise NMRNextError("INVALID_HEADER", "Truncated Varian header")
                return "varian-raw-1d" if struct.unpack(">i",raw)[0] == 1 else "varian-raw-2d"
            if (Path(path)/"acqus").is_file() and (Path(path)/"ser").is_file() and (Path(path)/"acqu2s").is_file():
                return "bruker-raw-2d"
            if (Path(path)/"acqus").is_file() and (Path(path)/"fid").is_file():
                return "bruker-raw-1d"
            raise NMRNextError("AMBIGUOUS_FORMAT", "Directory does not contain a declared acquisition profile")
        with open(path, "rb") as handle:
            head = handle.read(2048)
        if head.startswith(b"\x89HDF\r\n\x1a\n"):
            return "nmrnext-hdf5"
        if head.startswith(b"UCSF NMR"):
            return "ucsf-real"
        if head.lstrip().startswith(b"##TITLE=") and b"##JCAMP-DX=" in head:
            return "jcamp-real-1d"
        if len(head) >= 2048:
            marker = np.frombuffer(head[8:12], dtype="<f4")[0]
            swapped = np.frombuffer(head[8:12], dtype=">f4")[0]
            if min(abs(float(marker)-2.345), abs(float(swapped)-2.345)) < 1e-5:
                return "nmrpipe-real"
        raise NMRNextError("AMBIGUOUS_FORMAT", "No supported file signature found; specify a verified format")
    result = _EXTENSIONS.get(Path(path).suffix.lower())
    if result is None:
        raise NMRNextError("AMBIGUOUS_FORMAT", "Output suffix does not identify a supported profile; specify format")
    return result


def _hash_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_budget(size, max_bytes):
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise NMRNextError("INVALID_BUDGET", "max_bytes must be a positive integer")
    if size > max_bytes:
        raise NMRNextError("MEMORY_BUDGET", f"Required {size} bytes exceeds {max_bytes}-byte read budget",
                           details={"required_bytes": int(size), "max_bytes": max_bytes})


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value):
    raise ValueError(f"Nonfinite JSON constant {value!r}")


def _finite_json_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"Nonfinite JSON float {value!r}")
    return parsed


def _validate_native_metadata(meta, shape):
    required = {"schema_version", "axes", "complex_axes", "metadata", "provenance"}
    if not isinstance(meta, dict) or set(meta) != required:
        raise NMRNextError("INVALID_METADATA", "Native numeric schema fields are missing or unknown")
    if meta["schema_version"] != "1":
        raise NMRNextError("UNSUPPORTED_SCHEMA", "Expected native numeric schema version 1")
    axes, bits = meta["axes"], meta["complex_axes"]
    if (not isinstance(axes, list) or not axes or
            not isinstance(bits, list) or
            not isinstance(meta["metadata"], dict) or
            not isinstance(meta["provenance"], list)):
        raise NMRNextError("INVALID_METADATA", "Native axis, component, metadata or provenance types are invalid")
    axis_fields = {field.name for field in fields(Axis)}
    parsed_axes = []
    for item in axes:
        if not isinstance(item, dict) or set(item) != axis_fields:
            raise NMRNextError("INVALID_METADATA", "Native axis fields are missing or unknown")
        try:
            parsed_axes.append(Axis(**item))
        except (NMRNextError, TypeError, ValueError) as exc:
            raise NMRNextError("INVALID_METADATA", f"Invalid native axis: {exc}") from exc
    ids = [axis.id for axis in parsed_axes]
    if len(set(ids)) != len(ids) or any(not isinstance(bit, str) for bit in bits) or len(set(bits)) != len(bits):
        raise NMRNextError("INVALID_METADATA", "Native axis IDs or quadrature bits are invalid")
    if not set(bits) <= set(ids) or any(axis.id in bits and axis.encoding in {"real", "none"} for axis in parsed_axes):
        raise NMRNextError("INVALID_METADATA", "Quadrature mapping contradicts axis schema")
    if shape[0] != 2 ** len(bits) or len(parsed_axes) != len(shape)-1 or tuple(a.size for a in parsed_axes) != shape[1:]:
        raise NMRNextError("INVALID_SHAPE", "Data shape contradicts native numeric schema")


def _native_header(path, max_bytes):
    try:
        with h5py.File(path, "r") as h:
            if h.attrs.get("schema_version") != "1":
                raise NMRNextError("UNSUPPORTED_SCHEMA", "Expected native HDF5 schema version 1")
            if "data" not in h or not isinstance(h.get("data", getlink=True), h5py.HardLink):
                raise NMRNextError("INVALID_STORAGE", "Native data must be an internal hard-linked dataset")
            data = h["data"]
            if not isinstance(data, h5py.Dataset) or data.dtype not in (np.dtype("float32"), np.dtype("float64")):
                raise NMRNextError("INVALID_STORAGE", "Native data must have float32 or float64 dtype")
            if data.is_virtual or data.external:
                raise NMRNextError("INVALID_STORAGE", "Native data cannot use virtual or external storage")
            if data.ndim < 2 or any(n < 1 for n in data.shape):
                raise NMRNextError("INVALID_SHAPE", "Native data shape is invalid")
            _check_budget(data.size * data.dtype.itemsize, max_bytes)
            raw = h.attrs.get("metadata_json")
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > (1 << 20):
                raise NMRNextError("INVALID_METADATA", "Native metadata JSON is missing or too large")
            try:
                meta = json.loads(raw, object_pairs_hook=_unique_json_object,
                                  parse_constant=_reject_json_constant, parse_float=_finite_json_float)
            except (ValueError, TypeError) as exc:
                raise NMRNextError("INVALID_METADATA", "Malformed native metadata JSON") from exc
            _validate_native_metadata(meta, data.shape)
            return {"format": "nmrnext-hdf5", "version": "1", "shape": list(data.shape),
                    "dtype": data.dtype.name, "metadata": meta}
    except OSError as exc:
        raise NMRNextError("INVALID_STORAGE", f"Cannot inspect native HDF5: {exc}") from exc


def _pipe_header(path, max_bytes):
    size = Path(path).stat().st_size
    if size < 2048:
        raise NMRNextError("TRUNCATED_FILE", "NMRPipe header is shorter than 2048 bytes")
    try:
        ng = _nmrglue()
        dic = ng.pipe.fdata2dic(ng.pipe.get_fdata(str(path)))
        ndim = int(dic["FDDIMCOUNT"])
        if ndim not in (1, 2) or dic["FDDIMCOUNT"] != ndim:
            raise NMRNextError("UNSUPPORTED_PROFILE", "Only processed real 1D/2D NMRPipe files are supported")
        if ([int(x) for x in dic["FDDIMORDER"][:2]] != [2, 1] or dic["FDPIPEFLAG"] != 0):
            raise NMRNextError("UNSUPPORTED_PROFILE", "NMRPipe dimension order or stream layout is outside this profile")
        dims = (["FDF2"] if ndim == 1 else ["FDF1", "FDF2"])
        shape = ([int(dic["FDSIZE"])] if ndim == 1 else [int(dic["FDSPECNUM"]), int(dic["FDSIZE"])])
        if any(n < 1 for n in shape):
            raise NMRNextError("INVALID_SHAPE", "NMRPipe dimensions must be positive")
        required = math.prod(shape) * 4
        _check_budget(required, max_bytes)
        if size != 2048 + required:
            raise NMRNextError("INVALID_SHAPE", "NMRPipe file length differs from declared real array")
        for dim in dims:
            if dic[dim+"FTFLAG"] != 1 or dic[dim+"QUADFLAG"] != 1:
                raise NMRNextError("UNSUPPORTED_PROFILE", "NMRPipe file is not a processed real spectrum")
            if not all(math.isfinite(float(dic[dim+field])) for field in ("SW", "OBS", "ORIG")):
                raise NMRNextError("INVALID_AXIS", "Nonfinite NMRPipe calibration")
            if dic[dim+"SW"] <= 0 or dic[dim+"OBS"] <= 0:
                raise NMRNextError("INVALID_AXIS", "NMRPipe spectral width and observation frequency must be positive")
        return {"format": "nmrpipe-real", "version": "nmrglue 0.12 header dialect", "shape": [1, *shape],
                "dtype": "float32", "header": _jsonable_header(dic)}
    except (KeyError, ValueError, TypeError, IndexError, OverflowError, UnicodeError, struct.error) as exc:
        if isinstance(exc, NMRNextError):
            raise
        raise NMRNextError("INVALID_HEADER", f"Malformed NMRPipe header: {exc}") from exc


def _ucsf_header(path, max_bytes):
    size = Path(path).stat().st_size
    if size < 180 + 2*128:
        raise NMRNextError("TRUNCATED_FILE", "UCSF 2D header is incomplete")
    try:
        ng = _nmrglue()
        with open(path, "rb") as f:
            dic = ng.sparky.fileheader2dic(ng.sparky.get_fileheader(f))
            if (dic["ident"] != "UCSF NMR" or dic["version"] != 2 or dic["naxis"] != 2 or
                    dic["ncomponents"] != 1 or dic["encoding"] != 0):
                raise NMRNextError("UNSUPPORTED_PROFILE", "Only UCSF NMR version 2 real 2D files are supported")
            dic["w1"] = ng.sparky.axisheader2dic(ng.sparky.get_axisheader(f))
            dic["w2"] = ng.sparky.axisheader2dic(ng.sparky.get_axisheader(f))
        shape = [int(dic[f"w{i}"]["npoints"]) for i in (1, 2)]
        tiles = [int(dic[f"w{i}"]["bsize"]) for i in (1, 2)]
        if any(n < 1 for n in shape+tiles):
            raise NMRNextError("INVALID_SHAPE", "UCSF dimensions and tile sizes must be positive")
        required = math.prod(shape) * 4
        _check_budget(required, max_bytes)
        tile_bytes = math.prod(((n+t-1)//t) * t for n, t in zip(shape, tiles)) * 4
        _check_budget(tile_bytes, max_bytes)
        if size != 180 + 2*128 + tile_bytes:
            raise NMRNextError("INVALID_SHAPE", "UCSF file length differs from declared tile layout")
        if dic["seek_pos"] != size or any(dic[f"w{i}"]["size"] != shape[i-1] for i in (1, 2)):
            raise NMRNextError("INVALID_SHAPE", "UCSF header size or end-of-file pointer is inconsistent")
        for i in (1, 2):
            axis = dic[f"w{i}"]
            if not all(math.isfinite(float(axis[field])) for field in ("spectral_width", "spectrometer_freq", "xmtr_freq")):
                raise NMRNextError("INVALID_AXIS", "Nonfinite UCSF calibration")
            if axis["spectral_width"] <= 0 or axis["spectrometer_freq"] <= 0:
                raise NMRNextError("INVALID_AXIS", "UCSF spectral width and observation frequency must be positive")
        return {"format": "ucsf-real", "version": "UCSF NMR version 2", "shape": [1, *shape],
                "dtype": "float32", "header": _jsonable_header(dic)}
    except (KeyError, ValueError, TypeError, IndexError, OverflowError, UnicodeError, struct.error) as exc:
        if isinstance(exc, NMRNextError):
            raise
        raise NMRNextError("INVALID_HEADER", f"Malformed UCSF header: {exc}") from exc


def _validated_jcamp_text(raw):
    """Bound continuation loops before calling the pinned Bruker text parser.

    Acquisition text is at most 1 MiB. A continuation may consume only lines
    present in this buffer and may not run into a new JCAMP parameter.
    """
    text = raw.decode("latin-1")
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("##END="):
            break
        if line.startswith("##$"):
            if "=" not in line[3:]:
                raise NMRNextError("INVALID_HEADER", "Bruker parameter lacks '='")
            key = line[3:line.index("=")]
            if re.fullmatch(r"[A-Za-z0-9_]+", key) is None:
                raise NMRNextError("INVALID_HEADER", "Bruker parameter name is malformed")
            value = line.split("=", 1)[1].lstrip()
            if "<" in value:
                while ">" not in value:
                    index += 1
                    if index >= len(lines) or lines[index].startswith("##"):
                        raise NMRNextError("INVALID_HEADER", "Unterminated Bruker string parameter")
                    value += "\n" + lines[index]
            elif "(" in value:
                match = re.match(r"^\(\s*0\.\.([0-9]+)\s*\)", value)
                if match is None:
                    raise NMRNextError("INVALID_HEADER", "Malformed Bruker array declaration")
                count = int(match.group(1)) + 1
                if count > len(raw):
                    raise NMRNextError("INVALID_HEADER", "Bruker array count exceeds acquisition header length")
                found = len(value[match.end():].split())
                if found > count:
                    raise NMRNextError("INVALID_HEADER", "Bruker array has excess values")
                while found < count:
                    index += 1
                    if index >= len(lines) or lines[index].startswith("##"):
                        raise NMRNextError("INVALID_HEADER", "Truncated Bruker array parameter")
                    found += len(lines[index].split())
                    if found > count:
                        raise NMRNextError("INVALID_HEADER", "Bruker array has excess values")
        index += 1
    return text


class _BoundedJcampStream(io.StringIO):
    """Fail on repeated EOF even if the dependency catches one read error."""
    def __init__(self, text):
        super().__init__(text)
        self.max_reads = len(text.splitlines()) + 1
        self.reads = 0
        self.eof_seen = False
        self.failed = False

    def readline(self, *args):
        self.reads += 1
        if self.reads > self.max_reads:
            self.failed = True
            raise NMRNextError("INVALID_HEADER", "Bruker parser exceeded finite read budget")
        line = super().readline(*args)
        if line == "":
            if self.eof_seen:
                self.failed = True
                raise NMRNextError("INVALID_HEADER", "Bruker parser read beyond end of acquisition text")
            self.eof_seen = True
        return line


def _bruker_header(path, max_bytes):
    path = Path(path)
    if not path.is_dir():
        raise NMRNextError("UNSUPPORTED_PROFILE", "Bruker raw 1D input must be an acquisition directory")
    for name in ("ser", "acqu2s", "acqu3s", "acqu4s"):
        if (path/name).exists():
            raise NMRNextError("UNSUPPORTED_PROFILE", "Only a 1D fid/acqus acquisition is declared")
    acqus, fid = path/"acqus", path/"fid"
    if not acqus.is_file() or not fid.is_file() or acqus.is_symlink() or fid.is_symlink():
        raise NMRNextError("UNSUPPORTED_PROFILE", "Bruker 1D import needs regular acqus and fid files")
    if acqus.stat().st_size > 1 << 20:
        raise NMRNextError("INVALID_HEADER", "acqus exceeds 1 MiB header limit")
    try:
        with acqus.open("rb") as handle:
            acqus_bytes = handle.read((1 << 20) + 1)
        if len(acqus_bytes) > 1 << 20:
            raise NMRNextError("INVALID_HEADER", "acqus exceeds 1 MiB header limit")
        text = _validated_jcamp_text(acqus_bytes)
        ng = _nmrglue()
        stream = _BoundedJcampStream(text)
        with warnings.catch_warnings(record=True) as parser_warnings:
            warnings.simplefilter("always")
            dic = ng.bruker.parse_jcamp_file(stream, {"_coreheader": [], "_comments": []})
        if stream.failed:
            raise NMRNextError("INVALID_HEADER", "Bruker parser exceeded finite acquisition text")
        if parser_warnings:
            raise NMRNextError("INVALID_HEADER", "Bruker acquisition text contains unparsable lines")
        td = int(dic["TD"])
        mode = int(dic["AQ_mod"])
        endian = int(dic["BYTORDA"])
        dtype = int(dic["DTYPA"])
        sw, obs = float(dic["SW_h"]), float(dic["SFO1"])
        nucleus = str(dic["NUC1"])
        if mode not in (1, 3) or endian not in (0, 1) or dtype not in (0, 2):
            raise NMRNextError("UNSUPPORTED_PROFILE", "Acquisition mode, byte order, or numeric type is outside 1D complex profile")
        if td < 2 or td % 2 or not math.isfinite(sw) or sw <= 0 or not math.isfinite(obs) or obs <= 0:
            raise NMRNextError("INVALID_HEADER", "Invalid TD, spectral width, or observation frequency")
        if not nucleus or not nucleus.isascii():
            raise NMRNextError("INVALID_HEADER", "NUC1 must be a nonempty ASCII nucleus code")
        bytes_per = 4 if dtype == 0 else 8
        padded_points = math.ceil(td*bytes_per/1024)*1024//bytes_per
        expected_bytes = padded_points*bytes_per
        _check_budget(padded_points*8, max_bytes)
        if fid.stat().st_size != expected_bytes:
            raise NMRNextError("INVALID_SHAPE", "fid file length differs from TD and 1024-byte padding")
        digital_filter = {name: dic.get(name) for name in ("GRPDLY", "DECIM", "DSPFVS")}
        digital_filter["state"] = "uncorrected_or_unknown"
        return {"format": "bruker-raw-1d", "version": "controlled acqus/fid profile",
                "shape": [2, td//2], "dtype": "float64", "header": {
                    "TD": td, "AQ_mod": mode, "BYTORDA": endian, "DTYPA": dtype,
                    "SW_h": sw, "SFO1": obs, "NUC1": nucleus,
                    "acquisition_parameters": _jsonable_header({name: dic.get(name) for name in
                        ("TD", "AQ_mod", "DTYPA", "BYTORDA", "SW_h", "SFO1", "BF1", "O1", "NUC1", "DIGMOD", "DECIM", "DSPFVS", "GRPDLY")}),
                    "padded_real_points": padded_points, "digital_filter": digital_filter,
                    "raw_acqus_text": text, "acqus_sha256": hashlib.sha256(acqus_bytes).hexdigest()}}
    except (OSError, KeyError, TypeError, ValueError, IndexError, OverflowError, UnicodeError, struct.error) as exc:
        if isinstance(exc, NMRNextError):
            raise
        raise NMRNextError("INVALID_HEADER", f"Cannot parse controlled Bruker acqus: {exc}") from exc


def inspect_dataset(path, *, format=None, max_bytes=MAX_BYTES):
    """Inspect headers without reading the numeric array."""
    path = Path(path)
    if not path.exists():
        raise NMRNextError("INPUT_NOT_FOUND", f"Input does not exist: {path}")
    try:
        fmt = _format(format, path, reading=True)
        if fmt == "nmrnext-hdf5":
            return _native_header(path, max_bytes)
        if fmt == "nmrpipe-real":
            return _pipe_header(path, max_bytes)
        if fmt == "bruker-raw-1d":
            return _bruker_header(path, max_bytes)
        if fmt == "bruker-raw-2d":
            from .bruker2d import inspect_bruker_2d
            return inspect_bruker_2d(path, max_bytes)
        if fmt in {"varian-raw-1d", "varian-raw-2d"}:
            from .varian import inspect_varian
            return inspect_varian(path, max_bytes, dimensions=1 if fmt.endswith("1d") else 2)
        if fmt == "jcamp-real-1d":
            from .jcamp import inspect_jcamp
            return inspect_jcamp(path, max_bytes)
        return _ucsf_header(path, max_bytes)
    except OSError as exc:
        raise NMRNextError("INVALID_FILE", f"Cannot inspect input: {exc}") from exc
    except (TypeError, AttributeError, struct.error) as exc:
        raise NMRNextError("INVALID_HEADER", f"Malformed input header: {exc}") from exc


def _axis_from_uc(index, size, uc, label, *, obs, sw, car):
    start = float(uc.hz(0))
    step = -float(sw) / size
    return Axis(id=f"a{index}", size=size, domain="frequency", unit="Hz", start=start,
                step=step, observation_mhz=float(obs), reference_ppm=0.0,
                reference_hz=0.0, label=str(label), nucleus=str(label), encoding="real")


def read_dataset(path, *, format=None, max_bytes=MAX_BYTES):
    """Read one supported exact profile into the canonical component layout."""
    path = Path(path)
    head = inspect_dataset(path, format=format, max_bytes=max_bytes)
    fmt = head["format"]
    try:
        if fmt == "nmrnext-hdf5":
            with h5py.File(path, "r") as h:
                data = h["data"][:]
            return Dataset.from_metadata(data, head["metadata"])
        if fmt == "bruker-raw-2d":
            from .bruker2d import read_bruker_2d
            return read_bruker_2d(path, head)
        if fmt in {"varian-raw-1d", "varian-raw-2d"}:
            from .varian import read_varian
            return read_varian(path, head)
        if fmt == "jcamp-real-1d":
            from .jcamp import read_jcamp
            return read_jcamp(path, head)
        ng = _nmrglue()
        if fmt == "bruker-raw-1d":
            header = head["header"]
            # Explicit shape and parameters avoid nmrglue's permissive profile
            # guessing and prevent pulse program parsing or execution.
            _, raw = ng.bruker.read_binary(str(path/"fid"),
                                           shape=(header["padded_real_points"]//2,), cplex=True,
                                           big=bool(header["BYTORDA"]), isfloat=header["DTYPA"] == 2)
            if raw.ndim != 1 or raw.size != header["padded_real_points"]//2 or not np.iscomplexobj(raw):
                raise NMRNextError("INVALID_SHAPE", "Bruker reader disagrees with declared 1D complex shape")
            if _hash_file(path/"acqus") != header["acqus_sha256"]:
                raise NMRNextError("INPUT_CHANGED", "acqus changed between inspection and import")
            n = head["shape"][1]
            axis = Axis(id="a0", size=n, domain="time", unit="s", start=0., step=1/header["SW_h"],
                        observation_mhz=header["SFO1"], reference_ppm=None,
                        label=header["NUC1"], nucleus=header["NUC1"], encoding="complex",
                        dwell_seconds=1/header["SW_h"], acquired_size=n)
            data = np.stack((raw[:n].real.astype("float64"), raw[:n].imag.astype("float64")))
            return Dataset(data, (axis,), ("a0",), metadata={"import": {
                "format": fmt, "source_sha256": {"fid": _hash_file(path/"fid"),
                                                  "acqus": header["acqus_sha256"]},
                "raw_acqus_text": header["raw_acqus_text"],
                "acquisition_parameters": header["acquisition_parameters"],
                "padding_complex_points": raw.size-n,
                "digital_filter": header["digital_filter"]}})
        if fmt == "nmrpipe-real":
            dic, data = ng.pipe.read(str(path))
            if tuple(data.shape) != tuple(head["shape"][1:]) or np.iscomplexobj(data):
                raise NMRNextError("INVALID_SHAPE", "NMRPipe reader disagrees with inspected real shape")
            axes = []
            for i, n in enumerate(data.shape):
                dim = "FDF2" if data.ndim == 1 else ("FDF1" if i == 0 else "FDF2")
                uc = ng.pipe.make_uc(dic, data, dim=i)
                axes.append(_axis_from_uc(i, n, uc, dic[dim+"LABEL"],
                                          obs=dic[dim+"OBS"], sw=dic[dim+"SW"], car=0))
        else:
            dic, data = ng.sparky.read(str(path))
            if tuple(data.shape) != tuple(head["shape"][1:]):
                raise NMRNextError("INVALID_SHAPE", "UCSF reader disagrees with inspected real shape")
            axes = []
            for i, n in enumerate(data.shape):
                ax = dic[f"w{i+1}"]
                uc = ng.sparky.make_uc(dic, data, dim=i)
                axes.append(_axis_from_uc(i, n, uc, ax["nucleus"],
                                          obs=ax["spectrometer_freq"], sw=ax["spectral_width"], car=0))
        return Dataset(data[None], tuple(axes), metadata={"import": {"format": fmt,
                   "source_sha256": _hash_file(path), "source_header": _jsonable_header(head["header"])}})
    except (OSError, EOFError, IndexError, TypeError, AttributeError, struct.error, ValueError) as exc:
        if isinstance(exc, NMRNextError):
            raise
        raise NMRNextError("INVALID_FILE", f"Could not read {fmt} file: {exc}") from exc


def _jsonable_header(header):
    """Retain opaque source header values without pretending to understand them."""
    def convert(value):
        if isinstance(value, dict):
            return {str(k): convert(v) for k, v in value.items()}
        if isinstance(value, np.ndarray):
            return convert(value.tolist())
        if isinstance(value, np.generic):
            return convert(value.item())
        if isinstance(value, (list, tuple)):
            return [convert(v) for v in value]
        if isinstance(value, bytes):
            return {"opaque_bytes_hex": value.hex()}
        if isinstance(value, float):
            return value if math.isfinite(value) else str(value)
        if isinstance(value, (str, int, bool)) or value is None:
            return value
        return {"opaque_repr": repr(value)}
    return convert(header)


def _target_label(axis):
    """The external axis field is a nucleus code when one is available."""
    return axis.nucleus or axis.label


def export_preflight(dataset, format, *, allow_losses=()):
    if not isinstance(dataset, Dataset):
        raise NMRNextError("INVALID_DATASET", "Export requires a Dataset")
    fmt = _format(format, "")
    accepted = set(allow_losses)
    known = {"quadrature_projection", "float32_quantization", "metadata", "provenance",
             "reference_fields", "axis_annotations"}
    if not accepted <= known:
        raise NMRNextError("UNKNOWN_LOSS", f"Unknown loss acknowledgments: {sorted(accepted-known)}")
    if fmt == "nmrnext-hdf5":
        return {"format": fmt, "ok": True, "losses": [], "errors": [], "unacknowledged": [],
                "conversions": [], "preserved": ["all components", "dtype", "axis schema", "metadata", "provenance"]}
    if fmt in {"bruker-raw-1d", "bruker-raw-2d", "varian-raw-1d", "varian-raw-2d", "jcamp-real-1d"}:
        return {"format": fmt, "ok": False, "losses": [], "errors": ["read_only_profile"],
                "unacknowledged": [], "conversions": [], "preserved": []}
    errors, losses, conversions = [], [], []
    ndim = len(dataset.axes)
    if ndim not in ((1, 2) if fmt == "nmrpipe-real" else (2,)):
        errors.append("unsupported_dimensions")
    if dataset.complex_axes:
        losses.append("quadrature_projection")
    selected_component = dataset.data[0]
    if np.any(np.abs(selected_component) > np.finfo(np.float32).max):
        errors.append("float32_overflow")
    elif not np.array_equal(selected_component, selected_component.astype("float32").astype(dataset.data.dtype)):
        losses.append("float32_quantization")
    if dataset.metadata:
        losses.append("metadata")
    if dataset.provenance:
        losses.append("provenance")
    for axis in dataset.axes:
        if axis.role != "spectral" or axis.domain != "frequency" or axis.unit != "Hz":
            errors.append(f"axis_{axis.id}_not_frequency_spectral")
        if axis.observation_mhz is None or axis.reference_ppm is None:
            errors.append(f"axis_{axis.id}_missing_ppm_calibration")
        if axis.reference_hz != 0 or axis.reference_ppm != 0:
            # NMRPipe/UCSF store one carrier; the native reference decomposition is lost.
            if axis.reference_hz != 0:
                losses.append("reference_fields")
        if (axis.time_origin_seconds != 0 or axis.dwell_seconds is not None or
                axis.acquired_size != axis.size or axis.transform_time_origin_seconds is not None):
            losses.append("axis_annotations")
        if axis.nucleus not in (None, axis.label):
            losses.append("axis_annotations")
        if axis.encoding not in ("real", "none") and axis.id not in dataset.complex_axes:
            errors.append(f"axis_{axis.id}_encoding_unsupported")
        maxlen = 6 if fmt == "ucsf-real" else 8
        target_label = _target_label(axis)
        if not target_label or len(target_label.encode("ascii", errors="ignore")) > maxlen or not target_label.isascii():
            errors.append(f"axis_{axis.id}_label_unsupported")
        if axis.step > 0:
            conversions.append({"axis": axis.id, "operation": "reverse_points_for_descending_target_axis"})
        if fmt == "nmrpipe-real" and axis.size % 2:
            conversions.append({"axis": axis.id, "operation": "odd_point_nmrpipe_carrier_half_bin_adjustment",
                                "hz": abs(axis.step)/2})
    target_header_fields = _target_header_issues(dataset, fmt) if not errors else []
    if target_header_fields:
        errors.append("target_header_unrepresentable")
    losses = sorted(set(losses))
    unack = sorted(set(losses) - accepted)
    return {"format": fmt, "ok": not errors and not unack, "losses": losses,
            "errors": errors, "unacknowledged": unack, "conversions": conversions,
            "target_header_fields": target_header_fields,
            "projection": {"component_index": 0, "meaning": "all-real quadrature channel",
                           "complex_axes": list(dataset.complex_axes)},
            "preserved": ["component 0 (all-real quadrature channel)", "ppm coordinates", "axis labels"]}


def _udic(dataset, fmt):
    ng = _nmrglue()
    ndim = len(dataset.axes)
    udic = ng.fileio.fileiobase.create_blank_udic(ndim)
    for i, axis in enumerate(dataset.axes):
        sw = abs(axis.step) * axis.size
        left_hz = axis.reference_ppm * axis.observation_mhz + (axis.start - axis.reference_hz)
        if axis.step > 0:
            left_hz += axis.step * (axis.size - 1)
        car = left_hz - sw/2
        if fmt == "nmrpipe-real" and axis.size % 2:
            # Pinned nmrglue pipe.create_dic rounds CENTER to floor(N/2)+1.
            # Its ORIG-derived index-zero coordinate would be half a bin low
            # for odd N; this format-specific carrier correction restores it.
            car += abs(axis.step)/2
        udic[i].update({"size": axis.size, "complex": False, "encoding": "magnitude",
                        "time": False, "freq": True, "sw": sw, "obs": axis.observation_mhz,
                        "car": car, "label": _target_label(axis)})
    return udic


def _fits_target_float32(value, *, positive=False):
    try:
        number = float(value)
        if not math.isfinite(number):
            return False
        with np.errstate(over="ignore", under="ignore", invalid="ignore"):
            serialized = np.float32(number)
        return bool(np.isfinite(serialized) and (not positive or serialized > 0))
    except (ValueError, TypeError, OverflowError):
        return False


def _target_header_issues(dataset, fmt):
    """Validate the pinned writer's actual numeric header serialization."""
    ng = _nmrglue()
    issues = []
    try:
        udic = _udic(dataset, fmt)
        if fmt == "nmrpipe-real":
            dic = ng.pipe.create_dic(udic)
            with np.errstate(over="ignore", under="ignore", invalid="ignore"):
                serialized = ng.pipe.dic2fdata(dic)
            for key in ng.pipe.fdata_nums:
                index = int(ng.pipe.fdata_dic[key])
                if not _fits_target_float32(dic[key]) or not math.isfinite(float(serialized[index])):
                    issues.append(key)
            for index, axis in enumerate(dataset.axes):
                prefix = "FDF2" if index == len(dataset.axes)-1 else "FDF1"
                for suffix in ("SW", "OBS"):
                    key = prefix+suffix
                    slot = int(ng.pipe.fdata_dic[key])
                    if not _fits_target_float32(dic[key], positive=True) or serialized[slot] <= 0:
                        issues.append(key)
        else:
            dic = ng.sparky.create_dic(udic)
            file_header = ng.sparky.dic2fileheader(dic)
            struct.pack(">10s 4c 9s 26s 80s 3x l 40s 4x", *file_header)
            names = ("spectrometer_freq", "spectral_width", "xmtr_freq",
                     "zero_order", "first_order", "first_pt_scale")
            for index in range(len(dataset.axes)):
                axis_header = ng.sparky.dic2axisheader(dic[f"w{index+1}"])
                for field, value in zip(names, axis_header[5:11]):
                    if not _fits_target_float32(value, positive=field in {"spectrometer_freq", "spectral_width"}):
                        issues.append(f"w{index+1}.{field}")
                packed = struct.pack(">6s h 3I 6f 84s", *axis_header)
                decoded = struct.unpack(">6s h 3I 6f 84s", packed)
                for field, value in zip(names, decoded[5:11]):
                    if not math.isfinite(value) or (field in {"spectrometer_freq", "spectral_width"} and value <= 0):
                        issues.append(f"w{index+1}.{field}")
    except (ValueError, TypeError, KeyError, OverflowError, ArithmeticError, struct.error) as exc:
        issues.append(f"header_serialization:{type(exc).__name__}")
    return sorted(set(issues))


def _atomic(path, overwrite, writer):
    path = Path(path)
    if path.exists() and not overwrite:
        raise NMRNextError("OUTPUT_EXISTS", f"Output already exists: {path}")
    if not path.parent.is_dir():
        raise NMRNextError("OUTPUT_DIRECTORY", f"Output parent directory does not exist: {path.parent}")
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        writer(tmp)
        if overwrite:
            os.replace(tmp, path)
        else:
            try:
                if os.name == "nt":
                    # Windows rename fails if the destination exists and is atomic
                    # within this directory; hard links need privileges in sandboxes.
                    os.rename(tmp, path)
                else:
                    os.link(tmp, path)
                    os.unlink(tmp)
            except FileExistsError as exc:
                raise NMRNextError("OUTPUT_EXISTS", f"Output already exists: {path}") from exc
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_dataset(dataset, path, *, format=None, allow_losses=(), overwrite=False):
    fmt = _format(format, path)
    report = export_preflight(dataset, fmt, allow_losses=allow_losses)
    if report["errors"]:
        if "float32_overflow" in report["errors"]:
            raise NMRNextError("FLOAT32_OVERFLOW", "Selected component exceeds finite float32 target range",
                               details=report)
        if "target_header_unrepresentable" in report["errors"]:
            raise NMRNextError("TARGET_HEADER_UNREPRESENTABLE", "Calibration cannot be serialized in the target header",
                               details=report)
        raise NMRNextError("UNSUPPORTED_PROFILE", "Dataset cannot be represented by target profile",
                           details=report)
    if report["unacknowledged"]:
        raise NMRNextError("UNACKNOWLEDGED_LOSS", "Export has unacknowledged losses",
                           details=report)
    path = Path(path)
    if fmt == "nmrnext-hdf5":
        def writer(tmp):
            with h5py.File(tmp, "w") as h:
                h.attrs["schema_version"] = "1"
                h.attrs["metadata_json"] = json.dumps(dataset.to_metadata(), sort_keys=True, allow_nan=False)
                h.create_dataset("data", data=dataset.data, chunks=True)
    else:
        ng = _nmrglue()
        data = dataset.data[0]
        for i, axis in enumerate(dataset.axes):
            if axis.step > 0:
                data = np.flip(data, axis=i)
        data = np.asarray(data, dtype="float32", order="C")
        if not np.isfinite(data).all():
            raise NMRNextError("FLOAT32_OVERFLOW", "Selected component became nonfinite during float32 conversion",
                               details=report)
        udic = _udic(dataset, fmt)
        dic = ng.pipe.create_dic(udic) if fmt == "nmrpipe-real" else ng.sparky.create_dic(udic)
        def writer(tmp):
            if fmt == "nmrpipe-real":
                ng.pipe.write(tmp, dic, data, overwrite=True)
            else:
                ng.sparky.write(tmp, dic, data, overwrite=True)
    _atomic(path, overwrite, writer)
    return {**report, "path": str(path), "source_shape": list(dataset.data.shape),
            "target_shape": ([*dataset.data.shape] if fmt == "nmrnext-hdf5" else list(data.shape)),
            "source_dtype": dataset.data.dtype.name,
            "target_dtype": dataset.data.dtype.name if fmt == "nmrnext-hdf5" else "float32",
            "output_sha256": _hash_file(path)}
