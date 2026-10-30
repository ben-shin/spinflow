"""Independent bounded HDF5 output agreement, outside product timing/RSS."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path

import h5py
import numpy as np


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 ** 2), b""):
            h.update(chunk)
    return h.hexdigest()


def selections(shape, maximum=65536):
    tile = [1] * len(shape)
    count = 1
    for i in reversed(range(len(shape))):
        tile[i] = min(shape[i], max(1, maximum // count))
        count *= tile[i]
    for start in itertools.product(*(range(0, n, extent) for n, extent in zip(shape, tile))):
        yield tuple(slice(lo, min(lo + extent, n)) for lo, n, extent in zip(start, shape, tile))


def bounds_roi(data, centers):
    return data[(slice(None), *(slice(max(0, c - 1), min(n, c + 2)) for c, n in zip(centers, data.shape[1:])))]


def audit(reference, candidate, tolerance):
    with h5py.File(reference, "r", rdcc_nbytes=65536) as left, h5py.File(candidate, "r", rdcc_nbytes=65536) as right:
        a, b = left["data"], right["data"]
        assert a.shape == b.shape and a.dtype == b.dtype
        ma, mb = json.loads(left.attrs["metadata_json"]), json.loads(right.attrs["metadata_json"])
        assert ma == mb, "complete axes/metadata/global provenance differ"
        assert len(ma["axes"]) == a.ndim - 1
        reference_hash, candidate_hash = hashlib.sha256(), hashlib.sha256()
        maximum, error, sum_error, count = 0., 0., 0., 0
        signed_disagreements = 0
        largest = []
        largest_candidate = []
        component_maxima = [0.] * a.shape[0]
        component_errors = [0.] * a.shape[0]
        for channel in range(a.shape[0]):
            for selection in selections(a.shape[1:]):
                index = (slice(channel, channel + 1), *selection)
                x, y = a[index], b[index]
                assert np.isfinite(x).all() and np.isfinite(y).all()
                reference_hash.update(memoryview(x).cast("B"))
                candidate_hash.update(memoryview(y).cast("B"))
                values = x.astype("float64")
                difference = values - y.astype("float64")
                xmax = float(np.max(np.abs(values)))
                dmax = float(np.max(np.abs(difference)))
                maximum, error = max(maximum, xmax), max(error, dmax)
                component_maxima[channel] = max(component_maxima[channel], xmax)
                component_errors[channel] = max(component_errors[channel], dmax)
                sum_error += float(np.sum(difference * difference))
                count += x.size
                signed_disagreements += int(np.count_nonzero(np.signbit(x) != np.signbit(y)))
                if channel == 0:
                    flat = np.abs(values).reshape(-1)
                    take = np.argpartition(flat, -min(3, len(flat)))[-3:]
                    for local in take:
                        coordinates = np.unravel_index(int(local), x.shape)[1:]
                        global_index = tuple(s.start + int(i) for s, i in zip(selection, coordinates))
                        largest.append((float(flat[local]), global_index))
                    largest = sorted(largest, reverse=True)[:3]
                    flat_y = np.abs(y).reshape(-1)
                    take_y = np.argpartition(flat_y, -min(3, len(flat_y)))[-3:]
                    for local in take_y:
                        coordinates_y = np.unravel_index(int(local), y.shape)[1:]
                        index_y = tuple(s.start + int(i) for s, i in zip(selection, coordinates_y))
                        largest_candidate.append((float(flat_y[local]), index_y))
                    largest_candidate = sorted(largest_candidate, reverse=True)[:3]
        relative = error / max(maximum, np.finfo(float).tiny)
        assert relative <= tolerance, (relative, tolerance)
        component_relative = [e / max(m, np.finfo(float).tiny) for e, m in zip(component_errors, component_maxima)]
        assert max(component_relative) <= tolerance, (component_relative, tolerance)
        assert [index for _, index in largest] == [index for _, index in largest_candidate], "strongest signed peak bins changed"
        centers = [tuple(1 for _ in a.shape[1:]), tuple(max(0, n - 2) for n in a.shape[1:]),
                   tuple(n // 2 for n in a.shape[1:]), *(index for _, index in largest)]
        rois = []
        for center in centers:
            x, y = bounds_roi(a, center), bounds_roi(b, center)
            scale = max(float(np.max(np.abs(x))), np.finfo(float).tiny)
            delta = float(np.max(np.abs(x.astype("float64") - y.astype("float64"))))
            assert delta / scale <= tolerance
            rois.append({"center_indices": list(center), "all_channel_shape": list(x.shape),
                         "reference_sha256": hashlib.sha256(x.tobytes()).hexdigest(),
                         "candidate_sha256": hashlib.sha256(y.tobytes()).hexdigest(),
                         "relative_max_error": delta / scale,
                         "reference_component0_center_value": float(a[(0, *center)]),
                         "candidate_component0_center_value": float(b[(0, *center)])})
        for _, center in largest:
            assert np.signbit(a[(0, *center)]) == np.signbit(b[(0, *center)]), "strong peak sign changed"
        coordinates = []
        for axis in ma["axes"]:
            assert axis["size"] == a.shape[1 + ma["axes"].index(axis)]
            hz = axis["start"] + np.arange(axis["size"], dtype="float64") * axis["step"]
            assert np.isfinite(hz).all()
            coordinates.append({"axis": axis["id"], "domain": axis["domain"], "unit": axis["unit"],
                                "first": float(hz[0]), "last": float(hz[-1]),
                                "all_coordinate_sha256": hashlib.sha256(hz.tobytes()).hexdigest(),
                                "absolute_ppm_reference": axis["reference_ppm"]})
        metadata_bytes = json.dumps(ma, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return {"status": "PASS", "shape": list(a.shape), "dtype": str(a.dtype), "all_values_compared": count,
                "reference_numeric_sha256": reference_hash.hexdigest(), "candidate_numeric_sha256": candidate_hash.hexdigest(),
                "numeric_bytes_exact": reference_hash.hexdigest() == candidate_hash.hexdigest(),
                "full_metadata_history_exact": True, "full_metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
                "full_metadata": ma, "complex_axes": ma["complex_axes"], "coordinates": coordinates,
                "maximum_absolute_error": error, "relative_maximum_error": relative,
                "rms_error": (sum_error / count) ** .5, "signbit_disagreements": signed_disagreements,
                "component_relative_maximum_errors": component_relative,
                "strongest_reference_component0_bins": [list(index) for _, index in largest],
                "strongest_candidate_component0_bins": [list(index) for _, index in largest_candidate], "rois": rois,
                "reference_file": {"path": str(reference), "bytes": Path(reference).stat().st_size, "sha256": sha(reference)},
                "candidate_file": {"path": str(candidate), "bytes": Path(candidate).stat().st_size, "sha256": sha(candidate)},
                "scope": "Independent literal HDF5 full-array/all-channel and full metadata/axis/provenance check outside both timed processes; 65536-value numeric tiles; no alignment/normalization/fitting"}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--reference", type=Path, required=True)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--tolerance", type=float, default=2e-5)
    args = p.parse_args()
    report = audit(args.reference, args.candidate, args.tolerance)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    print(json.dumps({"status": "PASS", "all_values_compared": report["all_values_compared"],
                      "relative_maximum_error": report["relative_maximum_error"],
                      "numeric_bytes_exact": report["numeric_bytes_exact"]}))
