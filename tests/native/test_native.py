"""Analytical and ownership checks for the separately built native module."""

import math
import subprocess
import sys

import numpy as np
import pytest

from nmrnext import _native


@pytest.mark.parametrize("dtype", [np.complex64, np.complex128])
@pytest.mark.parametrize("axis", [0, 1, -1])
def test_matches_direct_dft(dtype, axis):
    source = np.array([[1 + 2j, -2 + 0.5j, 3 - 1j, 0],
                       [0.25 - 1j, 5 + 0j, -0.5 + 2j, 1 - 2j]], dtype=dtype)
    original = source.copy()
    normalized_axis = axis % source.ndim
    count = source.shape[normalized_axis]
    expected = np.zeros(source.shape, dtype=np.complex128)
    for index in np.ndindex(source.shape):
        k = index[normalized_axis]
        terms = []
        for n in range(count):
            source_index = list(index)
            source_index[normalized_axis] = n
            terms.append(source[tuple(source_index)] *
                         complex(math.cos(-2 * math.pi * k * n / count),
                                 math.sin(-2 * math.pi * k * n / count)))
        expected[index] = sum(terms)
    result = _native.fft_complex(source, axis, threads=2)
    assert result.dtype == dtype
    assert result.flags.c_contiguous
    assert not np.shares_memory(source, result)
    assert np.array_equal(source, original)
    np.testing.assert_allclose(result, expected, rtol=3e-6 if dtype == np.complex64 else 1e-13,
                               atol=3e-6 if dtype == np.complex64 else 1e-13)
    inverse = _native.fft_complex(result, axis, inverse=True, threads=2)
    np.testing.assert_allclose(inverse, source, rtol=3e-6 if dtype == np.complex64 else 1e-13,
                               atol=3e-6 if dtype == np.complex64 else 1e-13)


@pytest.mark.parametrize("source,axis,threads,expected", [
    ("np.array([1, 2], dtype=np.float64)", 0, 1, "TypeError"),
    ("np.ones(4, dtype=np.complex128)", 1, 1, "ValueError"),
    ("np.ones(4, dtype=np.complex128)", -2, 1, "ValueError"),
    ("np.ones(4, dtype=np.complex128)", 0, 0, "ValueError"),
    ("np.ones((2, 3), dtype=np.complex128).T", 0, 1, "ValueError"),
    ("np.ones((0,), dtype=np.complex128)", 0, 1, "ValueError"),
    ("[1 + 2j]", 0, 1, "TypeError"),
])
def test_invalid_inputs_raise_without_crashing_process(source, axis, threads, expected):
    script = f"""import numpy as np
from nmrnext import _native
source = {source}
try:
    _native.fft_complex(source, {axis}, threads={threads})
except {expected}:
    pass
else:
    raise AssertionError('expected {expected}')
"""
    completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
