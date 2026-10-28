"""Production operations against independent analytic and direct-DFT oracles.

These tests intentionally never call a production helper to form an expected
numeric result. The sole documented adapter is a fixed FFT storage shift.
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from nmrnext.errors import NMRNextError
from nmrnext.model import Axis, Dataset
from nmrnext.operations import apply_operation, run_operations
from reference_math import (
    direct_dft,
    exponential_samples,
    geometric_spectrum,
    quadrature_dft,
)
from test_reference_math import read_fixture


def shifted(values):
    """Fixed documented centering: output[j] = unshifted[(j-floor(N/2)) mod N]."""
    n = len(values)
    return [values[(j - n // 2) % n] for j in range(n)]


def fid_dataset(samples, *, dtype=np.float64, dwell=0.001, start=0.0, metadata=None):
    values = np.array([[z.real for z in samples], [z.imag for z in samples]], dtype=dtype)
    axis = Axis("F1", len(samples), step=dwell, start=start, encoding="complex")
    return Dataset(values, (axis,), ("F1",), metadata=metadata)


def complex_values(dataset):
    return [complex(r, i) for r, i in zip(dataset.data[0], dataset.data[1])]


class ProductionReferenceTests(unittest.TestCase):
    def assert_vectors_close(self, actual, expected, *, atol, label):
        self.assertEqual(len(actual), len(expected), label)
        for index, (a, e) in enumerate(zip(actual, expected)):
            self.assertLessEqual(abs(a - e), atol, f"{label} bin {index}: {a!r} != {e!r}")

    def test_fixture_integer_tones_and_constants_both_backends_precisions(self):
        fixture = read_fixture("one_dimensional.json")
        for case in fixture["cases"]:
            amp = complex(*case["value"])
            samples = (
                [amp] * case["length"]
                if case["kind"] == "constant"
                else exponential_samples(
                    case["length"], amplitude=amp,
                    frequency_cycles_per_dwell=case["cycles_per_dwell"],
                )
            )
            literal = complex(*case["peak_value"])
            expected = [literal if k == case["peak_bin"] else 0j for k in range(case["length"])]
            for dtype, backend in ((np.float64, "numpy"), (np.float64, "native"),
                                   (np.float32, "numpy"), (np.float32, "native")):
                with self.subTest(case=case["id"], dtype=dtype.__name__, backend=backend):
                    result = apply_operation(fid_dataset(samples, dtype=dtype),
                                             {"op": "fft", "axis": "F1"}, backend=backend)
                    atol = 2e-12 if dtype is np.float64 else 1e-5 * case["length"] * abs(amp)
                    self.assert_vectors_close(complex_values(result), shifted(expected),
                                              atol=atol, label=case["id"])
                    self.assertEqual(result.data.dtype, dtype)
                    self.assertEqual(result.axis("F1").domain, "frequency")

    def test_impulse_phase_and_shifted_odd_even_bins(self):
        for length, index in ((5, 2), (8, 3)):
            samples = [0j] * length
            samples[index] = 2 - 1j
            expected = shifted(direct_dft(samples))
            for backend in ("numpy", "native"):
                with self.subTest(length=length, backend=backend):
                    result = apply_operation(fid_dataset(samples), {"op": "fft", "axis": "F1"}, backend=backend)
                    self.assert_vectors_close(complex_values(result), expected, atol=2e-12,
                                              label=f"impulse-{length}")
                    self.assertAlmostEqual(result.axis("F1").start,
                                           -(length // 2) / (length * 0.001), delta=1e-12)
                    self.assertAlmostEqual(result.axis("F1").step, 1 / (length * 0.001), delta=1e-12)

    def test_off_bin_damped_and_time_origins_against_closed_form(self):
        cases = (
            (11, dict(amplitude=0.75 + 0.5j, frequency_cycles_per_dwell=2.375 / 11,
                      decay_per_dwell=0.0, start_dwell=0.0)),
            (13, dict(amplitude=0.6 - 0.8j, frequency_cycles_per_dwell=-2.25 / 13,
                      decay_per_dwell=0.18, start_dwell=0.5)),
            (8, dict(amplitude=1, frequency_cycles_per_dwell=2 / 8,
                     decay_per_dwell=0, start_dwell=0)),
            (8, dict(amplitude=1, frequency_cycles_per_dwell=2 / 8,
                     decay_per_dwell=0, start_dwell=0.5)),
            (8, dict(amplitude=1, frequency_cycles_per_dwell=2 / 8,
                     decay_per_dwell=0, start_dwell=1)),
        )
        for length, parameters in cases:
            samples = exponential_samples(length, **parameters)
            expected = shifted([geometric_spectrum(length, k, **parameters) for k in range(length)])
            for dtype, backend in ((np.float64, "numpy"), (np.float64, "native"),
                                   (np.float32, "numpy"), (np.float32, "native")):
                with self.subTest(length=length, start=parameters["start_dwell"],
                                  damping=parameters["decay_per_dwell"], dtype=dtype.__name__, backend=backend):
                    origin = parameters["start_dwell"] * 0.001
                    result = apply_operation(fid_dataset(samples, dtype=dtype, start=origin),
                                             {"op": "fft", "axis": "F1"}, backend=backend)
                    atol = 3e-12 if dtype is np.float64 else 2e-5
                    self.assert_vectors_close(complex_values(result), expected, atol=atol,
                                              label="geometric")
                    self.assertEqual(result.axis("F1").time_origin_seconds, origin)
                    self.assertEqual(result.axis("F1").transform_time_origin_seconds, origin)

    def test_weak_peak_checked_locally_both_backends_and_threads(self):
        length = 32
        strong = exponential_samples(length, amplitude=1e3, frequency_cycles_per_dwell=3 / length)
        weak = exponential_samples(length, amplitude=1e-3, frequency_cycles_per_dwell=9 / length)
        samples = [a + b for a, b in zip(strong, weak)]
        weak_storage_index = (9 + length // 2) % length
        strong_storage_index = (3 + length // 2) % length
        native_double = {}
        for backend, threads in (("numpy", 1), ("native", 1), ("native", 2)):
            with self.subTest(backend=backend, threads=threads):
                result = apply_operation(fid_dataset(samples), {"op": "fft", "axis": "F1"},
                                         backend=backend, threads=threads)
                values = complex_values(result)
                self.assertLessEqual(abs(values[strong_storage_index] - 32000), 1e-9)
                self.assertLessEqual(abs(values[weak_storage_index] - 0.032), 1e-9)
                self.assertGreater(abs(values[weak_storage_index]), 0.031999999)
                if backend == "native":
                    native_double[threads] = values
        self.assert_vectors_close(native_double[1], native_double[2], atol=1e-10,
                                  label="native-double-thread-agreement")
        # At float32 precision use a smaller, still 1000:1, amplitude ratio.
        # The weak bin has its own absolute bound; a global normalization by
        # the strong bin would conceal its error.
        weak32 = exponential_samples(length, amplitude=0.01,
                                     frequency_cycles_per_dwell=9 / length)
        strong32 = exponential_samples(length, amplitude=10,
                                       frequency_cycles_per_dwell=3 / length)
        samples32 = [a + b for a, b in zip(strong32, weak32)]
        native_single = {}
        for backend, threads in (("numpy", 1), ("native", 1), ("native", 2)):
            with self.subTest(dtype="float32", backend=backend, threads=threads):
                result = apply_operation(fid_dataset(samples32, dtype=np.float32),
                                         {"op": "fft", "axis": "F1"},
                                         backend=backend, threads=threads)
                values = complex_values(result)
                self.assertLessEqual(abs(values[strong_storage_index] - 320), 1e-4)
                self.assertLessEqual(abs(values[weak_storage_index] - 0.32), 1e-4)
                if backend == "native":
                    native_single[threads] = values
        self.assert_vectors_close(native_single[1], native_single[2], atol=1e-5,
                                  label="native-single-thread-agreement")

    def test_zero_fill_origin_metadata_and_inverse_restores_time_grid(self):
        samples = [1 + 0.25j, 2 - 1j, -0.5 + 3j, 0.75 - 0.5j, 1j]
        original = fid_dataset(samples, dwell=0.002, start=0.001)
        expected = shifted(direct_dft(samples + [0j] * 3))
        for backend in ("numpy", "native"):
            with self.subTest(backend=backend):
                padded = apply_operation(original, {"op": "zero_fill", "axis": "F1", "size": 8}, backend=backend)
                self.assertEqual(padded.axis("F1").acquired_size, 5)
                self.assertEqual(padded.axis("F1").time_origin_seconds, 0.001)
                self.assertEqual(complex_values(padded), samples + [0j] * 3)
                transformed = apply_operation(padded, {"op": "fft", "axis": "F1"}, backend=backend)
                self.assert_vectors_close(complex_values(transformed), expected, atol=2e-12,
                                          label="zero-fill")
                self.assertAlmostEqual(transformed.axis("F1").start, -250, delta=1e-12)
                self.assertAlmostEqual(transformed.axis("F1").step, 62.5, delta=1e-12)
                recovered = apply_operation(transformed, {"op": "fft", "axis": "F1", "inverse": True},
                                            backend=backend)
                self.assert_vectors_close(complex_values(recovered), samples + [0j] * 3,
                                          atol=2e-12, label="inverse")
                self.assertEqual(recovered.axis("F1").start, 0.001)
                self.assertEqual(recovered.axis("F1").step, 0.002)
                self.assertEqual(recovered.axis("F1").time_origin_seconds, 0.001)
                self.assertEqual(recovered.axis("F1").acquired_size, 5)

    def test_phase_pivot_and_first_point_scale_are_explicit(self):
        samples = [1 + 0j] * 5
        for backend in ("numpy", "native"):
            with self.subTest(backend=backend):
                windowed = apply_operation(fid_dataset(samples),
                    {"op": "window", "axis": "F1", "kind": "exponential",
                     "lb_hz": 0, "first_point_scale": 0.5}, backend=backend)
                self.assertEqual(complex_values(windowed), [0.5 + 0j] + samples[1:])
                phased = apply_operation(windowed,
                    {"op": "phase", "axis": "F1", "p0": 30, "p1": 90, "pivot": 1}, backend=backend)
                expected = [
                    (0.5 if j == 0 else 1) * complex(math.cos(math.radians(30 + 90 * (j-1) / 5)),
                                                     math.sin(math.radians(30 + 90 * (j-1) / 5)))
                    for j in range(5)
                ]
                self.assert_vectors_close(complex_values(phased), expected, atol=3e-15,
                                          label="phase-pivot")

    def test_crop_reverse_coordinates_and_inverse_grid_guard(self):
        samples = [complex(j, -j) for j in range(6)]
        original = fid_dataset(samples, dwell=0.002, start=0.001)
        for backend in ("numpy", "native"):
            with self.subTest(backend=backend):
                changed = run_operations(original,
                    [{"op": "crop", "axis": "F1", "start": 1, "stop": 5},
                     {"op": "reverse", "axis": "F1"}], backend=backend)
                self.assertEqual(complex_values(changed), list(reversed(samples[1:5])))
                self.assertAlmostEqual(changed.axis("F1").start, 0.009, delta=1e-15)
                self.assertAlmostEqual(changed.axis("F1").step, -0.002, delta=1e-15)
                self.assertEqual(changed.axis("F1").time_origin_seconds, 0.001)
                self.assertEqual(changed.axis("F1").acquired_size, 6)
                transformed = apply_operation(original, {"op": "fft", "axis": "F1"}, backend=backend)
                cut = apply_operation(transformed,
                    {"op": "crop", "axis": "F1", "start": 1, "stop": 5}, backend=backend)
                with self.assertRaises(NMRNextError) as caught:
                    apply_operation(cut, {"op": "fft", "axis": "F1", "inverse": True}, backend=backend)
                self.assertEqual(caught.exception.code, "INVALID_FREQUENCY_GRID")

    def test_four_channels_two_axes_mixed_domain_and_named_transpose(self):
        fixture = read_fixture("four_channel_2d.json")
        channels = fixture["tone_channels"]
        # Production bit order (F1,F2) is RR,IR,RI,II; fixture names are
        # explicit algebraic labels, independent of any vendor layout.
        names = ("RR", "IR", "RI", "II")
        data = np.array([channels[name] for name in names], dtype=np.float64)
        axes = (Axis("F1", 4, step=0.001, encoding="states"),
                Axis("F2", 4, step=0.002, encoding="states"))
        original = Dataset(data, axes, ("F1", "F2"))
        expected_first = quadrature_dft(channels, 0)
        expected_both = quadrature_dft(expected_first, 1)
        for backend in ("numpy", "native"):
            with self.subTest(backend=backend):
                first = apply_operation(original, {"op": "fft", "axis": "F1"}, backend=backend)
                self.assertEqual([a.domain for a in first.axes], ["frequency", "time"])
                for bit, name in enumerate(names):
                    for row in range(4):
                        for col in range(4):
                            self.assertAlmostEqual(first.data[bit, row, col],
                                expected_first[name][(row-2) % 4][col], delta=2e-14)
                transposed = apply_operation(first,
                    {"op": "transpose", "order": ["F2", "F1"]}, backend=backend)
                self.assertEqual([a.id for a in transposed.axes], ["F2", "F1"])
                self.assertEqual(transposed.complex_axes, ("F1", "F2"))
                self.assertEqual([a.domain for a in transposed.axes], ["time", "frequency"])
                second = apply_operation(transposed, {"op": "fft", "axis": "F2"}, backend=backend)
                self.assertEqual([a.domain for a in second.axes], ["frequency", "frequency"])
                for bit, name in enumerate(names):
                    for f2 in range(4):
                        for f1 in range(4):
                            self.assertAlmostEqual(second.data[bit, f2, f1],
                                expected_both[name][(f1-2) % 4][(f2-2) % 4], delta=5e-14)
                self.assertAlmostEqual(second.data[0, 3, 3], 16, delta=5e-14)

    def test_three_axis_quadrature_tensor_has_single_analytical_peak(self):
        sizes = (3, 3, 4)
        data = np.zeros((8, *sizes), dtype=np.float64)
        for channel in range(8):
            for n0 in range(3):
                for n1 in range(3):
                    for n2 in range(4):
                        value = 1.0
                        for bit, (n, size) in enumerate(zip((n0, n1, n2), sizes)):
                            angle = 2 * math.pi * n / size
                            value *= math.sin(angle) if channel & (1 << bit) else math.cos(angle)
                        data[channel, n0, n1, n2] = value
        axes = tuple(Axis(f"F{i+1}", size, step=0.001, encoding="states")
                     for i, size in enumerate(sizes))
        original = Dataset(data, axes, ("F1", "F2", "F3"))
        steps = [{"op": "fft", "axis": axis.id} for axis in axes]
        for backend in ("numpy", "native"):
            with self.subTest(backend=backend):
                result = run_operations(original, steps, backend=backend)
                for channel in range(8):
                    for j0 in range(3):
                        for j1 in range(3):
                            for j2 in range(4):
                                expected = 36 if (channel, j0, j1, j2) == (0, 2, 2, 3) else 0
                                self.assertAlmostEqual(result.data[channel, j0, j1, j2],
                                                       expected, delta=5e-13)

    def test_series_nus_and_unresolved_filter_are_structured_rejections(self):
        series = Dataset(np.ones((1, 3), dtype=np.float64),
                         (Axis("delay", 3, role="series", domain="index",
                               unit="ms", encoding="none"),))
        with self.assertRaises(NMRNextError) as caught:
            apply_operation(series, {"op": "fft", "axis": "delay"})
        self.assertEqual(caught.exception.code, "INVALID_AXIS_ROLE")
        for metadata, code in (({"sampling": {"mode": "nus", "measured": [0, 2]}},
                                "UNSUPPORTED_SAMPLING"),
                               ({"import": {"digital_filter": {"state": "unknown"}}},
                                "DIGITAL_FILTER_UNRESOLVED")):
            with self.subTest(code=code):
                with self.assertRaises(NMRNextError) as caught:
                    apply_operation(fid_dataset([1, 0, 0, 0], metadata=metadata),
                                    {"op": "fft", "axis": "F1"})
                self.assertEqual(caught.exception.code, code)

    def test_unknown_parameter_and_invalid_axis_fail_by_code(self):
        original = fid_dataset([1, 0, 0, 0])
        for operation, code in (
            ({"op": "fft", "axis": "F1", "hidden_fix": True}, "UNKNOWN_PARAMETER"),
            ({"op": "fft", "axis": "not-an-axis"}, "UNKNOWN_AXIS"),
            ({"op": "zero_fill", "axis": "F1", "size": 2}, "INVALID_SIZE"),
        ):
            with self.subTest(code=code):
                with self.assertRaises(NMRNextError) as caught:
                    apply_operation(original, operation)
                self.assertEqual(caught.exception.code, code)


if __name__ == "__main__":
    unittest.main()
