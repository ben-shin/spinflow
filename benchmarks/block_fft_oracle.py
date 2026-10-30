"""Independent NumPy complete-axis FFT oracle over bounded acquired-data tiles."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path

import h5py
import numpy as np


def verify(source_path, result_path, axis_id, tolerance=2e-6):
    maximum, error, count = 0., 0., 0
    scan = hashlib.sha256()
    with h5py.File(source_path, "r", rdcc_nbytes=65536) as source, h5py.File(result_path, "r", rdcc_nbytes=65536) as result:
        x, y = source["data"], result["data"]
        before, after = json.loads(source.attrs["metadata_json"]), json.loads(result.attrs["metadata_json"])
        assert x.shape == y.shape and x.dtype == y.dtype
        ids = [a["id"] for a in before["axes"]]
        active = ids.index(axis_id)
        bit = before["complex_axes"].index(axis_id)
        assert before["complex_axes"] == after["complex_axes"]
        assert before["axes"][active]["domain"] == "time" and before["axes"][active]["step"] > 0
        size = x.shape[active + 1]
        expected_step = 1 / (size * before["axes"][active]["step"])
        assert after["axes"][active]["domain"] == "frequency"
        assert after["axes"][active]["step"] == expected_step
        assert after["axes"][active]["start"] == -(size // 2) * expected_step
        sizes = x.shape[1:]
        batch = max((i for i in range(len(sizes)) if i != active), key=lambda i: sizes[i])
        remaining = [i for i in range(len(sizes)) if i not in (active, batch)]
        maximum_tile_bytes = 0
        for channel in range(x.shape[0]):
            if channel & (1 << bit):
                continue
            other = channel | (1 << bit)
            for coordinates in itertools.product(*(range(sizes[i]) for i in remaining)):
                for first in range(0, sizes[batch], 128):
                    selection = [slice(0, 1)] * len(sizes)
                    selection[active] = slice(0, size)
                    selection[batch] = slice(first, min(first + 128, sizes[batch]))
                    for i, value in zip(remaining, coordinates):
                        selection[i] = slice(value, value + 1)
                    real = x[(channel, *selection)]
                    imaginary = x[(other, *selection)]
                    z = real.astype("complex64" if x.dtype.itemsize == 4 else "complex128")
                    z.imag = imaginary
                    expected = np.fft.fftshift(np.fft.fft(z, axis=active), axes=active)
                    for c, values in [(channel, expected.real), (other, expected.imag)]:
                        observed = y[(c, *selection)]
                        assert np.isfinite(values).all() and np.isfinite(observed).all()
                        difference = values.astype("float64") - observed.astype("float64")
                        maximum = max(maximum, float(np.max(np.abs(values))))
                        error = max(error, float(np.max(np.abs(difference))))
                        count += observed.size
                        scan.update(np.asarray([c, *(s.start for s in selection)], dtype="<i8").tobytes())
                        scan.update(observed.tobytes())
                    maximum_tile_bytes = max(maximum_tile_bytes, real.nbytes + imaginary.nbytes + z.nbytes + expected.nbytes + difference.nbytes)
        assert count == int(np.prod(x.shape))
        relative = error / max(maximum, np.finfo(float).tiny)
        assert relative <= tolerance, (axis_id, relative, tolerance)
        return {"status": "PASS", "axis": axis_id, "all_channel_scalar_count": count, "maximum_absolute_error": error,
                "relative_maximum_error": relative, "fixed_tolerance": tolerance, "max_observed_oracle_tile_array_bytes": maximum_tile_bytes,
                "oracle_scan_sha256": scan.hexdigest(), "source": str(source_path), "result": str(result_path),
                "input_shape": list(x.shape), "dtype": str(x.dtype), "fft_length": size,
                "frequency_start_hz": after["axes"][active]["start"], "frequency_step_hz": expected_step,
                "scope": "Independent NumPyFFT+fftshift of every acquired full-axis line/all quadrature pairs, <=128orthogonal linesper tile; no productmath called; no alignment/rescaling/phasefit; outside performanceprocesses"}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--result", type=Path, required=True)
    p.add_argument("--axis", required=True)
    p.add_argument("--report", type=Path, required=True)
    args = p.parse_args()
    with args.report.open("x") as f:
        json.dump(verify(args.source, args.result, args.axis), f, indent=2)
