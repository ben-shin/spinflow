"""Small, independent mathematical oracles for synthetic NMR fixtures.

This module uses only the Python standard library. It intentionally does not
import SpinFlow, NumPy, SciPy, or an FFT implementation. The i0/i1 notation for
four-channel arrays denotes two independent quadrature units, each squaring
to -1; this is a synthetic representation, not a vendor layout claim.
"""

from __future__ import annotations

import cmath
import math
from typing import Sequence


def direct_dft(samples: Sequence[complex], *, inverse: bool = False) -> list[complex]:
    """Return an unshifted DFT; inverse has the reciprocal 1/N scaling."""
    length = len(samples)
    if length < 1:
        raise ValueError("a transform requires at least one sample")
    sign = 1 if inverse else -1
    scale = 1 / length if inverse else 1
    return [
        scale
        * sum(
            sample * cmath.exp(sign * 2j * math.pi * bin_index * n / length)
            for n, sample in enumerate(samples)
        )
        for bin_index in range(length)
    ]


def frequency_bins(length: int, dwell_seconds: float) -> list[float]:
    """Unshifted signed bin centres in Hz, including negative Nyquist for even N."""
    if length < 1 or not math.isfinite(dwell_seconds) or dwell_seconds <= 0:
        raise ValueError("length and dwell must be positive and finite")
    split = (length + 1) // 2
    return [
        (k if k < split else k - length) / (length * dwell_seconds)
        for k in range(length)
    ]


def exponential_samples(
    length: int,
    *,
    amplitude: complex = 1,
    frequency_cycles_per_dwell: float = 0,
    decay_per_dwell: float = 0,
    start_dwell: float = 0,
) -> list[complex]:
    """x[n] = A exp[(-decay + 2πi frequency)(n + start)]."""
    if length < 1 or decay_per_dwell < 0:
        raise ValueError("length must be positive and decay nonnegative")
    exponent = complex(-decay_per_dwell, 2 * math.pi * frequency_cycles_per_dwell)
    return [amplitude * cmath.exp(exponent * (n + start_dwell)) for n in range(length)]


def geometric_spectrum(
    length: int,
    bin_index: int,
    *,
    amplitude: complex = 1,
    frequency_cycles_per_dwell: float = 0,
    decay_per_dwell: float = 0,
    start_dwell: float = 0,
) -> complex:
    """Closed-form DFT of exponential_samples, independent of direct_dft.

    At an exact undamped bin, the ratio is 1 and the limit is N. The sum is
    otherwise evaluated as a geometric series, never by iterating samples.
    """
    if length < 1 or not 0 <= bin_index < length or decay_per_dwell < 0:
        raise ValueError("invalid transform length, bin, or decay")
    exponent = complex(-decay_per_dwell, 2 * math.pi * frequency_cycles_per_dwell)
    ratio_exponent = exponent - 2j * math.pi * bin_index / length
    if abs(ratio_exponent) < 1e-14:
        geometric_sum = length
    else:
        ratio = cmath.exp(ratio_exponent)
        geometric_sum = (1 - ratio**length) / (1 - ratio)
    return amplitude * cmath.exp(exponent * start_dwell) * geometric_sum


def _shape(channels: dict[str, Sequence[Sequence[float]]]) -> tuple[int, int]:
    if set(channels) != {"RR", "RI", "IR", "II"}:
        raise ValueError("channels must be RR, RI, IR, II")
    rows = len(channels["RR"])
    cols = len(channels["RR"][0]) if rows else 0
    if rows < 1 or cols < 1 or any(
        len(matrix) != rows or any(len(row) != cols for row in matrix)
        for matrix in channels.values()
    ):
        raise ValueError("all four channels need the same nonempty rectangle")
    return rows, cols


def quadrature_dft(
    channels: dict[str, Sequence[Sequence[float]]], axis: int
) -> dict[str, list[list[float]]]:
    """Direct transform of one of two independent quadrature axes.

    Axis 0 acts on the RR/IR and RI/II pairs; axis 1 acts on the RR/RI
    and IR/II pairs. The result retains all four real-valued components.
    """
    rows, cols = _shape(channels)
    if axis not in (0, 1):
        raise ValueError("axis must be 0 or 1")
    out = {name: [[0.0] * cols for _ in range(rows)] for name in channels}
    size = rows if axis == 0 else cols
    for output_index in range(size):
        for input_index in range(size):
            theta = 2 * math.pi * output_index * input_index / size
            cosine, sine = math.cos(theta), math.sin(theta)
            for other in range(cols if axis == 0 else rows):
                input_pos = (input_index, other) if axis == 0 else (other, input_index)
                output_pos = (output_index, other) if axis == 0 else (other, output_index)
                for real_name, imag_name in (
                    (("RR", "IR"), ("RI", "II"))
                    if axis == 0
                    else (("RR", "RI"), ("IR", "II"))
                ):
                    real = channels[real_name][input_pos[0]][input_pos[1]]
                    imag = channels[imag_name][input_pos[0]][input_pos[1]]
                    out[real_name][output_pos[0]][output_pos[1]] += real * cosine + imag * sine
                    out[imag_name][output_pos[0]][output_pos[1]] += imag * cosine - real * sine
    return out


def transpose_quadrature(
    channels: dict[str, Sequence[Sequence[float]]]
) -> dict[str, list[list[float]]]:
    """Swap axes and their quadrature bit positions: RI and IR exchange."""
    _shape(channels)
    mapping = {"RR": "RR", "RI": "IR", "IR": "RI", "II": "II"}
    return {
        name: [list(row) for row in zip(*channels[source])]
        for name, source in mapping.items()
    }
