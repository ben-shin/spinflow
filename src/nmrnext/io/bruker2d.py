"""Controlled fully sampled Bruker ser profile (States-TPPI / Echo-AntiEcho).

Physical axes are (indirect a0, direct a1); quadrature bits follow that order.
The canonical channels are RR, IR, RI, II, with the indirect letter first.
States-TPPI row pairs are demodulated by (-1)**increment. Echo-AntiEcho uses
the rotated Rance-Kay mapping C=even-odd, S=i*(even+odd), without averaging.
The latter fixes a convention, not a phase estimate; phase remains explicit.
DIGMOD may be omitted; when supplied it must be integer 1 (digital mode).
Optional direct SW (ppm) must agree with SW_h / SFO1. The relative 2e-5
consistency allowance for both dimensions is an engineering allowance for
rounded acquisition headers, not a claim about instrument calibration error.

Sources: nmrglue 0.12 fileio.bruker (FnMODE and binary layout),
process.nmrtxt.rance_kay.bruk_ranceY (Echo-AntiEcho mapping), and NMRPipe
bruk2pipe -yMODE States-TPPI (complex sign alternation).
https://github.com/jjhelmus/nmrglue/blob/v0.12/nmrglue/fileio/bruker.py
https://github.com/jjhelmus/nmrglue/blob/v0.12/nmrglue/process/nmrtxt/rance_kay.py
https://spin.niddk.nih.gov/bax-apps/NMRPipe/doc/nmrProg/bruk2pipe.html
"""
import hashlib
import math
from pathlib import Path
import warnings

import numpy as np

from nmrnext.errors import NMRNextError
from nmrnext.model import Axis, Dataset


def _integer(dic, key, *, allowed=None, minimum=None):
    value = dic.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise NMRNextError("INVALID_HEADER", f"Bruker {key} must be an integer")
    if (allowed is not None and value not in allowed) or (minimum is not None and value < minimum):
        raise NMRNextError("UNSUPPORTED_PROFILE", f"Bruker {key}={value} is outside the raw 2D profile")
    return value


def _positive(dic, key):
    value = dic.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NMRNextError("INVALID_HEADER", f"Bruker {key} must be finite and positive")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise NMRNextError("INVALID_HEADER", f"Bruker {key} is outside the numeric range") from exc
    if not math.isfinite(number) or number <= 0:
        raise NMRNextError("INVALID_HEADER", f"Bruker {key} must be finite and positive")
    return number


def _read_parameters(path):
    from . import _validated_jcamp_text, _BoundedJcampStream, _nmrglue
    if not path.is_file() or path.is_symlink():
        raise NMRNextError("UNSUPPORTED_PROFILE", f"Bruker import requires a regular {path.name} file")
    with path.open("rb") as handle:
        raw = handle.read((1 << 20)+1)
    if len(raw) > 1 << 20:
        raise NMRNextError("INVALID_HEADER", f"{path.name} exceeds the 1 MiB header limit")
    text = _validated_jcamp_text(raw)
    # Duplicate acquisition parameters are ambiguous, even when a permissive
    # JCAMP parser would choose the final occurrence.
    names = [line[3:].split("=",1)[0] for line in text.splitlines() if line.startswith("##$")]
    if len(names) != len(set(names)):
        raise NMRNextError("INVALID_HEADER", f"Duplicate parameters in {path.name}")
    stream = _BoundedJcampStream(text)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            dic = _nmrglue().bruker.parse_jcamp_file(stream, {"_coreheader":[],"_comments":[]})
        except (ValueError, TypeError, IndexError, KeyError, OverflowError) as exc:
            raise NMRNextError("INVALID_HEADER", f"Malformed {path.name}: {exc}") from exc
    if caught or stream.failed:
        raise NMRNextError("INVALID_HEADER", f"Cannot parse bounded {path.name}")
    return dic, text, hashlib.sha256(raw).hexdigest()


def inspect_bruker_2d(path, max_bytes):
    from . import _check_budget, _jsonable_header
    path = Path(path)
    if not path.is_dir():
        raise NMRNextError("UNSUPPORTED_PROFILE", "Bruker raw 2D input must be an acquisition directory")
    if any((path/name).exists() for name in ("fid","acqu3s","acqu4s")):
        raise NMRNextError("UNSUPPORTED_PROFILE", "Ambiguous or higher-dimensional acquisition")
    if any(child.name.lower() in {"nuslist","nus.list","sampling.sch","sampling_schedule"} for child in path.iterdir()):
        raise NMRNextError("UNSUPPORTED_SAMPLING", "NUS schedules are outside the fully sampled raw 2D profile")
    ser = path/"ser"
    if not ser.is_file() or ser.is_symlink():
        raise NMRNextError("UNSUPPORTED_PROFILE", "Bruker raw 2D requires a regular ser file")
    direct, raw_direct, hash_direct = _read_parameters(path/"acqus")
    indirect, raw_indirect, hash_indirect = _read_parameters(path/"acqu2s")
    _integer(direct,"PARMODE",allowed={1})
    _integer(direct,"AQSEQ",allowed={0})
    _integer(direct,"AQ_mod",allowed={3})
    _integer(direct,"FnTYPE",allowed={0})
    if "DIGMOD" in direct:
        _integer(direct,"DIGMOD",allowed={1})
    # Reject active NUS metadata even if acquisition mode or schedule disagrees.
    for dic in (direct,indirect):
        if dic.get("NusTD",0) not in (0,None) or dic.get("NUS_TD",0) not in (0,None):
            raise NMRNextError("UNSUPPORTED_SAMPLING", "NUS acquisition metadata is unsupported")
    mode = _integer(indirect,"FnMODE",allowed={5,6})
    td = _integer(direct,"TD",minimum=2)
    rows = _integer(indirect,"TD",minimum=2)
    if td % 2 or rows % 2:
        raise NMRNextError("INVALID_SHAPE", "Direct TD and indirect TD must both contain complete quadrature pairs")
    byteorder = _integer(direct,"BYTORDA",allowed={0,1})
    dtype = _integer(direct,"DTYPA",allowed={0,2})
    sw = _positive(direct,"SW_h")
    obs = _positive(direct,"SFO1")
    if "SW" in direct:
        direct_sw_h = _positive(direct,"SW")*obs
        if not math.isfinite(direct_sw_h) or not math.isclose(direct_sw_h,sw,rel_tol=2e-5):
            raise NMRNextError("INVALID_HEADER", "Direct SW * SFO1 and SW_h disagree or overflow")
    obs_indirect = _positive(indirect,"SFO1")
    # Bruker indirect SW is in ppm and SFO1 in MHz. SW_h in older acqu2s
    # files can have a different rounding precision; preserve both originals.
    sw_indirect = _positive(indirect,"SW")*obs_indirect
    if not math.isfinite(sw_indirect):
        raise NMRNextError("INVALID_HEADER", "Indirect spectral width overflows")
    if "SW_h" in indirect and not math.isclose(_positive(indirect,"SW_h"),sw_indirect,rel_tol=2e-5):
        raise NMRNextError("INVALID_HEADER", "Indirect SW and SW_h disagree")
    delay = _positive(direct,"GRPDLY")
    trim = math.floor(delay)+2
    if trim >= td//2 or max(trim-6,0) > td//2-trim:
        raise NMRNextError("INVALID_DIGITAL_FILTER", "Acquisition is too short for the declared GRPDLY correction")
    for dic in (direct,indirect):
        if not isinstance(dic.get("NUC1"),str) or not dic["NUC1"] or not dic["NUC1"].isascii():
            raise NMRNextError("INVALID_HEADER", "NUC1 must be a nonempty ASCII nucleus code")
    itemsize = 4 if dtype == 0 else 8
    padded = ((td*itemsize+1023)//1024)*1024//itemsize
    expected = rows*padded*itemsize
    # Include padding expanded to float64; bound before reading numeric bytes.
    _check_budget(rows*padded*8,max_bytes)
    if ser.stat().st_size != expected:
        raise NMRNextError("INVALID_SHAPE", "ser length differs from indirect TD times the 1024-byte padded direct TD")
    keys = ("TD","AQ_mod","AQSEQ","PARMODE","FnTYPE","DTYPA","BYTORDA","SW_h","SW","SFO1","BF1","O1","NUC1","DIGMOD","DECIM","DSPFVS","GRPDLY")
    ind_keys = ("TD","FnMODE","SW","SW_h","SFO1","BF1","O1","NUC1")
    # Omission differs from an explicitly invalid value; retain DIGMOD only
    # when present so a native roundtrip preserves that distinction.
    params = _jsonable_header({k:direct.get(k) for k in keys if k != "DIGMOD" or k in direct})
    ind_params = _jsonable_header({k:indirect.get(k) for k in ind_keys})
    ind_params["source_SW_h"] = ind_params["SW_h"]
    ind_params["SW_h"] = sw_indirect
    ind_params["SW_h_policy"] = "acqu2s.SW * acqu2s.SFO1"
    return {"format":"bruker-raw-2d","version":"controlled fully sampled ser profile v1",
        "shape":[4,rows//2,td//2],"dtype":"float64","header":{
            "TD":td,"rows":rows,"padded_real_points":padded,"BYTORDA":byteorder,"DTYPA":dtype,
            "FnMODE":mode,"SW_h":sw,"SFO1":obs,"indirect_SW_h":sw_indirect,
            "indirect_SFO1":obs_indirect,"NUC1":direct["NUC1"],"indirect_NUC1":indirect["NUC1"],
            "acquisition_parameters":params,"indirect_acquisition_parameters":ind_params,
            "raw_acqus_text":raw_direct,"raw_acqu2s_text":raw_indirect,
            "parameter_sha256":{"acqus":hash_direct,"acqu2s":hash_indirect},
            "digital_filter":{**{k:direct.get(k) for k in ("GRPDLY","DECIM","DSPFVS")},
                              "state":"uncorrected_or_unknown"}}}


def read_bruker_2d(path, head):
    from . import _hash_file
    path = Path(path)
    h = head["header"]
    for name, digest in h["parameter_sha256"].items():
        if _hash_file(path/name) != digest:
            raise NMRNextError("INPUT_CHANGED", f"{name} changed between inspection and import")
    dtype = np.dtype((">" if h["BYTORDA"] else "<")+("i4" if h["DTYPA"] == 0 else "f8"))
    count = h["rows"]*h["padded_real_points"]
    # The same bounded bytes supply both the samples and their provenance hash.
    with (path/"ser").open("rb") as handle:
        payload = handle.read(count*dtype.itemsize+1)
    if len(payload) != count*dtype.itemsize:
        raise NMRNextError("INPUT_CHANGED", "ser length changed between inspection and import")
    rows = np.frombuffer(payload,dtype=dtype).reshape(h["rows"],h["padded_real_points"])[:,:h["TD"]]
    if not np.isfinite(rows).all():
        raise NMRNextError("NONFINITE_DATA", "ser contains nonfinite acquired samples")
    even = rows[0::2,0::2].astype(float)+1j*rows[0::2,1::2]
    odd = rows[1::2,0::2].astype(float)+1j*rows[1::2,1::2]
    if h["FnMODE"] == 5:
        signs = np.where(np.arange(even.shape[0]) % 2,-1.,1.)[:,None]
        c, s = even*signs, odd*signs
        mapping = "C=even*(-1)^increment; S=odd*(-1)^increment"
    else:
        c, s = even-odd, 1j*(even+odd)
        mapping = "C=even-odd; S=i*(even+odd); scale=1 (no averaging)"
    from .headers import normalized_description
    axes,bits,metadata,provenance=normalized_description(head,{**h['parameter_sha256'],'ser':hashlib.sha256(payload).hexdigest()})
    return Dataset(np.stack((c.real,s.real,c.imag,s.imag)),axes,bits,metadata,provenance)
