"""Independent checks for the small synthetic reference corpus.

Run with: python -m unittest discover -s tests/reference -v
No production code or third-party numerical library is imported here.
"""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

from reference_math import (
    direct_dft,
    exponential_samples,
    frequency_bins,
    geometric_spectrum,
    quadrature_dft,
    transpose_quadrature,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic"


def read_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


class ReferenceMathTests(unittest.TestCase):
    def assert_complex_close(self, actual: complex, expected: complex, atol: float = 2e-12):
        self.assertLessEqual(abs(actual - expected), atol, f"{actual!r} != {expected!r}")

    def test_fixture_tones_have_exact_peak_and_quiet_other_bins(self):
        for case in read_fixture("one_dimensional.json")["cases"]:
            with self.subTest(case=case["id"]):
                amplitude = complex(*case["value"])
                if case["kind"] == "constant":
                    samples = [amplitude] * case["length"]
                else:
                    samples = exponential_samples(
                        case["length"],
                        amplitude=amplitude,
                        frequency_cycles_per_dwell=case["cycles_per_dwell"],
                    )
                spectrum = direct_dft(samples)
                peak_bin = case["peak_bin"]
                self.assert_complex_close(spectrum[peak_bin], complex(*case["peak_value"]))
                for k, value in enumerate(spectrum):
                    if k != peak_bin:
                        self.assertLessEqual(abs(value), 2e-12, f"{case['id']} bin {k}")

    def test_impulse_has_flat_magnitude_and_known_phase(self):
        samples = [0j, 0j, 2 - 1j, 0j, 0j]
        spectrum = direct_dft(samples)
        self.assert_complex_close(spectrum[0], 2 - 1j)
        for k, value in enumerate(spectrum):
            expected = (2 - 1j) * complex(
                math.cos(-2 * math.pi * k * 2 / 5),
                math.sin(-2 * math.pi * k * 2 / 5),
            )
            self.assert_complex_close(value, expected)
            self.assertAlmostEqual(abs(value), math.sqrt(5), delta=2e-12)

    def test_inverse_uses_reciprocal_scaling(self):
        samples = [2 - 1j, -3 + 0.25j, 4j, 0.5 - 2j, -1 + 0j]
        for actual, expected in zip(direct_dft(direct_dft(samples), inverse=True), samples):
            self.assert_complex_close(actual, expected)
        self.assertEqual(direct_dft([3 - 2j]), [3 - 2j])
        with self.assertRaises(ValueError):
            direct_dft([])

    def test_signed_unshifted_frequency_bins(self):
        self.assertEqual(frequency_bins(8, 0.25), [0, 0.5, 1, 1.5, -2, -1.5, -1, -0.5])
        self.assertEqual(frequency_bins(5, 0.2), [0, 1, 2, -2, -1])
        self.assertEqual(frequency_bins(1, 0.5), [0])
        for bad_length, bad_dwell in ((0, 1), (4, 0), (4, float("nan"))):
            with self.assertRaises(ValueError):
                frequency_bins(bad_length, bad_dwell)

    def test_off_bin_tone_matches_geometric_closed_form(self):
        length, frequency = 11, 2.375 / 11
        amplitude = 0.75 + 0.5j
        spectrum = direct_dft(
            exponential_samples(length, amplitude=amplitude, frequency_cycles_per_dwell=frequency)
        )
        for k, observed in enumerate(spectrum):
            expected = geometric_spectrum(
                length, k, amplitude=amplitude, frequency_cycles_per_dwell=frequency
            )
            self.assert_complex_close(observed, expected, 3e-13)
        # Distinct phase and leakage in neighboring bins are part of the oracle.
        self.assertGreater(abs(spectrum[3]), 0.1)
        self.assertGreater(abs(spectrum[1]), 0.1)
        self.assertGreater(abs(spectrum[2].imag), 0.1)

    def test_damped_tone_matches_geometric_closed_form(self):
        length = 13
        parameters = dict(
            amplitude=0.6 - 0.8j,
            frequency_cycles_per_dwell=-2.25 / length,
            decay_per_dwell=0.18,
            start_dwell=0.5,
        )
        spectrum = direct_dft(exponential_samples(length, **parameters))
        for k, observed in enumerate(spectrum):
            self.assert_complex_close(observed, geometric_spectrum(length, k, **parameters), 8e-14)
        self.assertGreater(abs(spectrum[11]), abs(spectrum[3]))

    def test_time_origin_has_analytical_linear_phase(self):
        length, bin_index = 8, 2
        expected = {0: 8 + 0j, 0.5: 4 * math.sqrt(2) * (1 + 1j), 1: 8j}
        for start, peak in expected.items():
            with self.subTest(start=start):
                spectrum = direct_dft(
                    exponential_samples(
                        length,
                        frequency_cycles_per_dwell=bin_index / length,
                        start_dwell=start,
                    )
                )
                self.assert_complex_close(spectrum[bin_index], peak)
                for k, value in enumerate(spectrum):
                    if k != bin_index:
                        self.assertLessEqual(abs(value), 2e-12)

    def test_weak_peak_checked_at_its_own_scale(self):
        length = 32
        strong = exponential_samples(length, amplitude=1e3, frequency_cycles_per_dwell=3 / length)
        weak = exponential_samples(length, amplitude=1e-3, frequency_cycles_per_dwell=9 / length)
        spectrum = direct_dft([a + b for a, b in zip(strong, weak)])
        self.assertLessEqual(abs(spectrum[3] - 32e3), 1e-9)
        self.assertLessEqual(abs(spectrum[9] - 0.032), 1e-9)
        self.assertGreater(abs(spectrum[9]), 0.031999999)

    def test_four_channel_two_axis_transform_has_closed_form_peak(self):
        fixture = read_fixture("four_channel_2d.json")
        transformed = quadrature_dft(quadrature_dft(fixture["tone_channels"], 0), 1)
        for channel, matrix in transformed.items():
            for row_index, row in enumerate(matrix):
                for col_index, actual in enumerate(row):
                    expected = 16 if (channel, row_index, col_index) == ("RR", 1, 1) else 0
                    self.assertAlmostEqual(actual, expected, delta=2e-14)

    def test_four_channel_axis_transforms_commute(self):
        marker = read_fixture("four_channel_2d.json")["transpose_marker"]
        first = quadrature_dft(quadrature_dft(marker, 0), 1)
        second = quadrature_dft(quadrature_dft(marker, 1), 0)
        for channel in first:
            for first_row, second_row in zip(first[channel], second[channel]):
                for a, b in zip(first_row, second_row):
                    self.assertAlmostEqual(a, b, delta=1e-11)

    def test_transpose_exchanges_axis_roles_and_quadrature_bits(self):
        marker = read_fixture("four_channel_2d.json")["transpose_marker"]
        moved = transpose_quadrature(marker)
        self.assertEqual(moved["RR"], [[1, 4], [2, 5], [3, 6]])
        self.assertEqual(moved["RI"], [[100, 400], [200, 500], [300, 600]])
        self.assertEqual(moved["IR"], [[10, 40], [20, 50], [30, 60]])
        self.assertEqual(moved["II"], [[1000, 4000], [2000, 5000], [3000, 6000]])
        self.assertEqual(transpose_quadrature(moved), marker)


if __name__ == "__main__":
    unittest.main()
