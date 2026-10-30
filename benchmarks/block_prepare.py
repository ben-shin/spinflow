"""Prepare authentic WR41 native cubes with independently audited raw indexing.

This benchmark fixture converter is deliberately separate from supported product
vendor IO. No absolute ppm reference is inferred from the old VNMR parameter set.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import struct

import h5py
import nmrglue as ng
import numpy as np

from nmrnext.model import Axis


CASES = {
    "3d": {"folder": "wr41_hnco_8_9_01.fid", "sizes": [35, 40, 512],
           "nuclei": ["15N", "13C", "1H"], "sw": [1600., 1960.7213093, 8000.],
           "obs": [60.7760837, 150.8247161, 599.719531], "phase_order": ["phase", "phase2"],
           "procpar_dimensions": ["2", "1", ""], "torder": "o", "rance_axis": 0},
    "4d": {"folder": "wr41_4d_cnoesy_8_22_01.fid", "sizes": [16, 18, 60, 320],
           "nuclei": ["13C", "13C", "1H", "1H"], "sw": [3125., 3125., 4199.91600168, 8000.],
           "obs": [150.8082741, 150.8082741, 599.7184665, 599.7184665],
           "phase_order": ["phase3", "phase2", "phase"],
           "procpar_dimensions": ["3", "2", "1", ""], "torder": "r", "rance_axis": None},
}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 ** 2), b""):
            value.update(chunk)
    return value.hexdigest()


def record(path):
    path = Path(path)
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": digest(path)}


def raw_cube(path, spec):
    """Map every raw word by explicit mixed-radix time and phase coordinates."""
    with Path(path).open("rb") as stream:
        head = struct.unpack(">6i2hi", stream.read(32))
    nblocks, ntraces, points, ebytes, tbytes, bbytes, version, status, nbheaders = head
    sizes = spec["sizes"]
    ndim = len(sizes)
    assert ntraces == 1 and nbheaders == 1 and ebytes == 4
    assert status == 69 and points == 2 * sizes[-1]
    assert tbytes == points * 4 and bbytes == tbytes + 28
    assert nblocks == int(np.prod(sizes[:-1])) * 2 ** (ndim - 1)
    assert Path(path).stat().st_size == 32 + nblocks * bbytes
    dtype = np.dtype([("scale", ">i2"), ("status", ">i2"), ("index", ">i2"),
                      ("mode", ">i2"), ("ctcount", ">i4"), ("phase", ">f4", (4,)),
                      ("samples", ">i4", (points,))])
    blocks = np.memmap(path, mode="r", dtype=dtype, offset=32, shape=(nblocks,))
    assert np.all(blocks["scale"] == 0) and np.all(blocks["status"] == 69)
    result = np.empty((2 ** ndim, *sizes), dtype="float32")
    exact_words = 0
    for component in range(2 ** ndim):
        bits = [(component >> axis) & 1 for axis in range(ndim)]
        # VNMR arrays list outermost phase first; d3/d2 or d4/d3/d2 time
        # increments remain outermost-to-innermost. These are independent.
        phase_bits = bits[:-1] if spec["torder"] == "r" else bits[:-1][::-1]
        phase_offset = 0
        for value in phase_bits:
            phase_offset = phase_offset * 2 + value
        traces = blocks["samples"][phase_offset::2 ** (ndim - 1), bits[-1]::2]
        converted = traces.astype("float32")
        assert np.array_equal(converted.astype("int64"), traces), "lossy raw scalar conversion"
        exact_words += traces.size
        result[component] = converted.reshape(sizes)
    # A second implementation: nmrglue low-memory trace indexing. Probe every
    # indirect coordinate and every direct word, not only shape or random rows.
    expanded = tuple(2 * n for n in sizes[:-1]) + (sizes[-1],)
    _, other = ng.varian.read_fid_lowmem(str(path), shape=expanded, torder=spec["torder"])
    checked = 0
    for component in range(2 ** ndim):
        bits = [(component >> axis) & 1 for axis in range(ndim)]
        index = tuple(slice(bits[i], None, 2) for i in range(ndim - 1)) + (slice(None),)
        values = other[index]
        values = values.imag if bits[-1] else values.real
        assert np.array_equal(values, result[component]), (component, "independent trace-index mismatch")
        checked += values.size
    assert checked == exact_words == result.size
    return result, {"file_header": list(head), "all_raw_words_exact": exact_words,
                    "all_nmrglue_independent_words_equal": checked,
                    "block_scale": np.unique(blocks["scale"]).tolist(),
                    "block_ctcount": np.unique(blocks["ctcount"]).tolist(),
                    "raw_minimum": int(blocks["samples"].min()),
                    "raw_maximum": int(blocks["samples"].max())}


def pipe_components(path, sizes):
    """Independent literal NMRPipe stream layout, no product reader involved."""
    ndim = len(sizes)
    with Path(path).open("rb") as stream:
        header = np.frombuffer(stream.read(2048), dtype="<f4")
    assert header[2] == np.float32(2.345) and int(header[9]) == ndim
    shape = tuple(2 * value for value in sizes[:-1]) + (2 * sizes[-1],)
    assert Path(path).stat().st_size == 2048 + 4 * int(np.prod(shape))
    data = np.memmap(path, mode="r", offset=2048, dtype="<f4", shape=shape)
    out = np.empty((2 ** ndim, *sizes), dtype="float32")
    for component in range(2 ** ndim):
        bits = [(component >> axis) & 1 for axis in range(ndim)]
        index = tuple(slice(bits[i], None, 2) for i in range(ndim - 1))
        index += (slice(bits[-1] * sizes[-1], (bits[-1] + 1) * sizes[-1]),)
        out[component] = data[index]
    return out


def rance_normalize(raw):
    """Varian Z echo/antiecho shuffle, explicitly invertible for every channel."""
    out = np.empty_like(raw)
    for c in range(raw.shape[0]):
        if c & 1 or c & 4:
            continue
        er, ei, ar, ai = raw[c], raw[c | 4], raw[c | 1], raw[c | 5]
        out[c], out[c | 4] = er + ar, ei + ai
        out[c | 1], out[c | 5] = ei - ai, ar - er
        # No normalization scalar is fitted; this is the defined sum/difference
        # basis change. Reconstruction proves no raw value or channel was lost.
        assert np.array_equal((out[c] - out[c | 5]) / 2, er)
        assert np.array_equal((out[c] + out[c | 5]) / 2, ar)
        assert np.array_equal((out[c | 4] + out[c | 1]) / 2, ei)
        assert np.array_equal((out[c | 4] - out[c | 1]) / 2, ai)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir()
    receipts = {"scope": "Benchmark-only audited native cubes; no production raw ND vendor import claim", "cases": {}}
    for name, spec in CASES.items():
        source = args.input_root / spec["folder"]
        procpar = ng.varian.read_procpar(str(source / "procpar"))
        assert procpar["array"]["values"][0].split(",") == spec["phase_order"]
        raw, audit = raw_cube(source / "fid", spec)
        external = pipe_components(args.reference_root / f"{name}-unshuffled.pipe", spec["sizes"])
        assert np.array_equal(raw, external), (name, "external converter raw layout mismatch")
        del external
        if spec["rance_axis"] is not None:
            normalized = rance_normalize(raw)
            external = pipe_components(args.reference_root / "3d-rance.pipe", spec["sizes"])
            assert np.array_equal(normalized, external), "external Rance-Kay basis mismatch"
            del external, raw
        else:
            normalized = raw
        before_parity = hashlib.sha256(normalized.tobytes()).hexdigest()
        # Undo the explicit States-TPPI receiver sign encoding in all indirect
        # time coordinates. Pulse programs increment receiver phase by180deg at
        # odd increments; all components receive the same sign.
        for axis, size in enumerate(spec["sizes"][:-1]):
            index = [slice(None)] * normalized.ndim
            index[axis + 1] = slice(1, None, 2)
            normalized[tuple(index)] *= -1
        recovered_hash = hashlib.sha256()
        for component in normalized:
            recovered = component.copy()
            for axis in range(len(spec["sizes"]) - 1):
                index = [slice(None)] * recovered.ndim
                index[axis] = slice(1, None, 2)
                recovered[tuple(index)] *= -1
            recovered_hash.update(recovered.tobytes())
        assert recovered_hash.hexdigest() == before_parity
        audit["all_tppi_inverse_words_verified"] = normalized.size
        axes = []
        phases = []
        for i, (size, nucleus, sw, obs, suffix) in enumerate(zip(spec["sizes"], spec["nuclei"], spec["sw"], spec["obs"], spec["procpar_dimensions"])):
            flag = procpar.get(f"f{suffix}180", {}).get("values", ["n"])[0] if suffix else "n"
            delay = float(procpar.get("d" + str(int(suffix) + 1), {}).get("values", [0])[0]) if suffix else 0.
            origin = delay + (0.5 / sw if flag == "y" else 0.)
            axes.append(Axis(f"a{i}", size, label=nucleus, nucleus=nucleus, start=origin,
                             step=1. / sw, observation_mhz=obs, reference_ppm=None,
                             reference_hz=0., encoding="states" if suffix else "complex",
                             time_origin_seconds=origin, dwell_seconds=1. / sw, acquired_size=size))
            phases.append({"op": "phase", "axis": f"a{i}", "p0": float(procpar.get("rp" + suffix, {}).get("values", [0])[0]),
                           "p1": float(procpar.get("lp" + suffix, {}).get("values", [0])[0]), "pivot": 0.})
        operations = []
        for axis in reversed(range(len(axes))):
            operations.extend([{"op": "window", "axis": f"a{axis}", "kind": "sine", "off": 0.5, "end": 1., "power": 1.},
                               {"op": "fft", "axis": f"a{axis}"}, phases[axis]])
        inputs = [record(source / filename) for filename in ["fid", "procpar", "log", "text"]]
        confidence = {"raw_scalars": "exact audited integer-to-float32; scale0; no ctcount division/drift correction",
                      "axis_order": "raw mixed-radix decoder + nmrglue all-words + literal external converter output agree",
                      "quadrature": "all 2^n channels retained; explicit lossless Rance basis if needed; TPPI parity removed from pulseprogram",
                      "frequency_reference": "relative Hz about recorded transmitter only; no calibrated absolute ppm anchor",
                      "phase": "fixed original procpar rp/lp values; no fit; absorptive biological assignment accuracy not claimed",
                      "window": "predeclared cosine/sine processing choice, not an unavailable original depositor script"}
        metadata = {"schema_version": "1", "axes": [asdict(axis) for axis in axes],
                    "complex_axes": [axis.id for axis in axes],
                    "metadata": {"sampling": "fully_sampled", "source": "BMRB5329 WR41 " + name,
                                 "source_records": inputs, "confidence": confidence,
                                 "original_procpar": {k: v["values"] for k, v in procpar.items()}},
                    "provenance": [{"operation": "benchmark_acquisition_normalization", "source_records": inputs,
                                    "rance_kay_axis": spec["rance_axis"], "states_tppi_axes": [a.id for a in axes[:-1]],
                                    "scalar_divisor": 1, "lossless_raw_reconstruction_verified": True}]}
        path = args.output / f"wr41-{name}.nmrh5"
        with h5py.File(path, "x") as h:
            h.attrs["schema_version"] = "1"
            h.attrs["metadata_json"] = json.dumps(metadata, allow_nan=False, sort_keys=True)
            h.create_dataset("data", data=normalized, chunks=(1, *(min(n, 8) for n in spec["sizes"][:-1]), spec["sizes"][-1]))
        recipe = args.output / f"recipe-{name}.json"
        recipe.write_text(json.dumps({"operations": operations}, indent=2) + "\n", encoding="utf-8")
        receipts["cases"][name] = {"native": record(path), "recipe": record(recipe), "inputs": inputs,
                                     "numeric_sha256": hashlib.sha256(normalized.tobytes()).hexdigest(),
                                     "metadata_sha256": hashlib.sha256(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                                     "shape": list(normalized.shape), "dtype": str(normalized.dtype),
                                     "audit": audit, "confidence": confidence}
        del normalized
    (args.output / "preparation-receipt.json").write_text(json.dumps(receipts, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "cases": {k: {"shape": v["shape"], "numeric_sha256": v["numeric_sha256"]} for k, v in receipts["cases"].items()}}))


if __name__ == "__main__":
    main()
