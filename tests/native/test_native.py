"""Analytical and ownership checks for the separately built native module."""

import math
import subprocess
import sys

import numpy as np
import pytest

from nmrnext import Axis, Dataset, NMRNextError, _native, __version__


def test_native_version_matches_python_package():
    assert _native.__version__ == __version__


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
@pytest.mark.parametrize("rows,columns", [(2, 3), (31, 33), (32, 32), (33, 65)])
def test_transposed_pack_preserves_every_bit(dtype, rows, columns):
    base = np.arange(rows * columns, dtype=dtype)
    words = base.view(np.uint32 if dtype == np.float32 else np.uint64)
    sign = 1 << (8 * base.itemsize - 1)
    largest = int(np.array(np.finfo(dtype).max, dtype=dtype).view(words.dtype))
    words[:6] = [0, sign, 1, sign | 1, largest, sign | largest]
    raw = base.reshape(1, columns, rows).transpose(0, 2, 1)
    expected = raw.tobytes(order="C")
    raw.setflags(write=False)
    packed = _native._try_pack_transposed(raw)
    assert type(packed) is bytes and packed == expected
    assert raw.tobytes(order="C") == expected
    base.fill(42)
    assert packed == expected


def test_private_packer_declines_other_layouts_without_coercion():
    raw = np.arange(24, dtype=np.float32).reshape(1, 6, 4).transpose(0, 2, 1)
    unaligned = np.ndarray((1, 6, 4), dtype=raw.dtype,
                           buffer=bytearray(raw.nbytes + 1), offset=1).transpose(0, 2, 1)
    for source in (object(), raw.copy(), raw[:, ::-1], raw[:, ::2],
                   np.broadcast_to(raw[:, :, :1], raw.shape), raw[0][None],
                   np.empty((2, 6, 4), dtype=raw.dtype).transpose(0, 2, 1),
                   raw[0], raw[:, :1], raw[:, :0], unaligned,
                   raw.astype("i4"), raw.astype(raw.dtype.newbyteorder("S"))):
        assert _native._try_pack_transposed(source) is None


def test_private_packer_accepts_measured_extent_and_declines_larger_input():
    at_cap = np.zeros((1, 4096, 2048), dtype=np.float32).transpose(0, 2, 1)
    assert _native._try_pack_transposed(at_cap) == at_cap.tobytes(order="C")
    above_cap = np.empty((1, 4097, 2048), dtype=np.float32).transpose(0, 2, 1)
    assert _native._try_pack_transposed(above_cap) is None


def test_dataset_uses_native_bytes_and_preserves_public_ownership(monkeypatch):
    class DerivedDataset(Dataset):
        pass
    class DerivedAxis(Axis):
        pass
    class Samples(np.ndarray):
        pass
    raw = np.arange(35, dtype=np.float64).reshape(1, 5, 7).transpose(0, 2, 1).view(Samples)
    expected = np.asarray(raw).tobytes(order="C")
    axes = (DerivedAxis("a", 7), Axis("b", 5))
    packed = []
    native_pack = _native._try_pack_transposed
    def observe(array):
        result = native_pack(array)
        packed.append(result)
        return result
    monkeypatch.setattr(_native, "_try_pack_transposed", observe)
    metadata = {"opaque": [17]}
    provenance = [{"source": "pack test"}]
    result = DerivedDataset(raw, axes, metadata=metadata, provenance=provenance)
    assert packed == [expected]
    exposed = result.data
    while isinstance(exposed, np.ndarray):
        with pytest.raises(ValueError):
            exposed.setflags(write=True)
        exposed = exposed.base
    assert exposed is packed[0] and type(exposed) is bytes
    result.data.base.dtype = np.uint8
    result.data.shape = (35,)
    raw.fill(-5)
    metadata["opaque"].append(99)
    provenance.clear()
    assert result.data.shape == raw.shape and result.data.dtype == raw.dtype
    assert result.data.tobytes() == expected and result.data.flags.c_contiguous
    assert result.axes == axes and result.complex_axes == ()
    assert result.metadata == {"opaque": [17]} and result.provenance == [{"source": "pack test"}]


@pytest.mark.parametrize("failure", [MemoryError, KeyboardInterrupt])
def test_failed_pack_does_not_publish_step_snapshot(monkeypatch, failure):
    from nmrnext.operations import _run_plan
    from nmrnext.planning import plan_operations
    raw = np.arange(24, dtype=np.float32).reshape(1, 6, 4)
    source = Dataset(raw, (Axis("a", 6), Axis("b", 4)))
    plan = plan_operations(source, [{"op": "transpose", "order": ["b", "a"]}])
    def fail(array):
        raise failure()
    monkeypatch.setattr(_native, "_try_pack_transposed", fail)
    snapshots = []
    with pytest.raises(NMRNextError if failure is MemoryError else KeyboardInterrupt) as error:
        _run_plan(source, plan, on_step=lambda index, data: snapshots.append(data))
    if failure is MemoryError:
        assert error.value.code == "MEMORY_ALLOCATION_FAILED"
    assert snapshots == []
    assert source.data.tobytes() == raw.tobytes()


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
