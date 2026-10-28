"""Bounded Bruker GRPDLY correction, independent of the vendor-reader codec.

Version 1 implements the fractional (not truncated) nmrglue/Westler-Abildgaard
advance-and-fold policy. For unsigned DFT bin k the multiplier is
exp(+2*pi*i*GRPDLY*k/N). This convention is intentional, including k>N/2;
it is not a signed-frequency interpolation or a general instrument deconvolver.
After advancement, reverse the last max(floor(GRPDLY)+2-6,0) samples into
the beginning, then discard floor(GRPDLY)+2 samples from the tail. No other
first-point weighting, conjugation, gain or chemical-shift referencing occurs.
The recovered FID begins at t=0, with unchanged dwell and acquisition count.

Reference algorithm: nmrglue 0.12 fileio.bruker.rm_dig_filter, fractional mode.
GRPDLY must be recorded and positive; no firmware-dependent defaults are used.
"""
from copy import deepcopy
import math

import numpy as np

from .errors import NMRNextError
from .model import finite


ALGORITHM = "bruker-grpdly-fourier-advance-fold-v1"


def correction_spec(axes, complex_axes, metadata):
    imported = metadata.get("import")
    if not isinstance(imported, dict) or imported.get("format") != "bruker-raw-1d":
        raise NMRNextError("UNSUPPORTED_DIGITAL_FILTER", "Correction requires an imported Bruker raw 1D acquisition")
    state = imported.get("digital_filter")
    if not isinstance(state, dict):
        raise NMRNextError("UNSUPPORTED_DIGITAL_FILTER", "Acquisition digital-filter parameters are missing")
    if state.get("state") == "corrected":
        raise NMRNextError("DIGITAL_FILTER_ALREADY_CORRECTED", "Digital filter is already corrected")
    if state.get("state") != "uncorrected_or_unknown":
        raise NMRNextError("UNSUPPORTED_DIGITAL_FILTER", "Unknown digital-filter state")
    delay = finite(state.get("GRPDLY"), "acquisition GRPDLY")
    if delay <= 0:
        raise NMRNextError("UNSUPPORTED_DIGITAL_FILTER", "A positive recorded GRPDLY is required; DSP lookup/default delays are not supported")
    params = imported.get("acquisition_parameters", {})
    if not isinstance(params, dict) or params.get("AQ_mod") not in (1,3):
        raise NMRNextError("UNSUPPORTED_DIGITAL_FILTER", "Supported acquisition mode must be preserved from import")
    sw = finite(params.get("SW_h"), "acquisition SW_h")
    if len(axes) != 1 or complex_axes != (axes[0].id,):
        raise NMRNextError("UNSUPPORTED_DIGITAL_FILTER", "Correction supports one complex direct dimension only")
    a = axes[0]
    if (a.role != "spectral" or a.domain != "time" or a.encoding != "complex" or
            a.start != 0 or a.time_origin_seconds != 0 or a.step <= 0 or
            a.dwell_seconds != a.step or a.size != a.acquired_size or
            params.get("TD") != 2*a.size or not math.isclose(sw, 1/a.step, rel_tol=1e-14)):
        raise NMRNextError("UNSUPPORTED_DIGITAL_FILTER", "Correction requires the original uncropped, unpadded, forward 1D acquisition grid")
    if delay >= a.size-2:
        raise NMRNextError("INVALID_DIGITAL_FILTER", "Group delay leaves no usable acquired samples")
    trim = math.floor(delay)+2
    fold = max(trim-6,0)
    if fold > a.size-trim:
        raise NMRNextError("INVALID_DIGITAL_FILTER", "Acquisition is too short for the reverse-tail fold")
    return {"algorithm": ALGORITHM, "version": "1", "delay_source": "acqus.GRPDLY",
            "group_delay_complex_points": delay, "fractional_delay": True,
            "phase_bins": "unsigned DFT k=0..N-1; exp(+2*pi*i*delay*k/N)",
            "trim_complex_points": trim, "fold_complex_points": fold,
            "first_point_scale": 1.0, "input_complex_points": a.size,
            "output_complex_points": a.size-trim, "output_time_origin_seconds": 0.0,
            "reference": "nmrglue 0.12 rm_dig_filter(truncate_grpdly=False) policy"}


def corrected_metadata(metadata, spec):
    result = deepcopy(metadata)
    state = result["import"]["digital_filter"]
    state["state"] = "corrected"
    state["correction"] = deepcopy(spec)
    return result


def correct_samples(z, spec, transform):
    """Apply the declared policy using the selected engine's complex FFT."""
    n = z.size
    phase = np.exp(2j*np.pi*spec["group_delay_complex_points"]*np.arange(n)/n)
    advanced = transform(transform(z, False)*phase, True)
    fold = spec["fold_complex_points"]
    if fold:
        advanced[:fold] += advanced[-1:-fold-1:-1]
    return advanced[:-spec["trim_complex_points"]]
