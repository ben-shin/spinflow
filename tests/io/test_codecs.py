import h5py
import hashlib
import json
import math
import os
import struct
import subprocess
import sys
from pathlib import Path
import nmrglue as ng
import numpy as np
import pytest

from nmrnext.errors import NMRNextError
from nmrnext.io import capabilities, export_preflight, inspect_dataset, read_dataset, write_dataset
from nmrnext.model import Axis, Dataset


def _startup_timeout_seconds(base_seconds):
    """Bound an opt-in subprocess startup allowance for slow emulated CI."""
    raw = os.environ.get("SPINFLOW_TEST_TIMEOUT_FACTOR")
    if raw is None:
        return base_seconds
    try:
        factor = float(raw)
    except ValueError as exc:
        raise ValueError("SPINFLOW_TEST_TIMEOUT_FACTOR must be finite and between 1 and 5") from exc
    if not math.isfinite(factor) or not 1 <= factor <= 5:
        raise ValueError("SPINFLOW_TEST_TIMEOUT_FACTOR must be finite and between 1 and 5")
    return base_seconds * factor


def spectrum(shape=(8, 16), *, q=False):
    axes = tuple(Axis(id=f"a{i}", size=n, domain="frequency", unit="Hz",
                      start=-n * (20 + i) / 2, step=20 + i,
                      observation_mhz=500 + i * 50, reference_ppm=4.7 + i,
                      label=("N15", "H1")[i], nucleus=("N15", "H1")[i],
                      encoding="complex" if q and i == 1 else "real")
                 for i, n in enumerate(shape))
    data = np.arange(np.prod(shape), dtype="float64").reshape(shape)
    if q:
        return Dataset(np.stack([data, data + 100]), axes, ("a1",))
    return Dataset(data[None], axes)


def test_native_exact_roundtrip_and_header_inspection(tmp_path):
    ds = spectrum(q=True)
    path = tmp_path / "sample.nmrh5"
    report = write_dataset(ds, path)
    assert report["format"] == "nmrnext-hdf5"
    with h5py.File(path) as h:
        assert h["data"].shape == (2, 8, 16)
        assert h.attrs["schema_version"] == "1"
    header = inspect_dataset(path)
    assert header["shape"] == [2, 8, 16]
    json.dumps(header, allow_nan=False)
    result = read_dataset(path)
    np.testing.assert_array_equal(result.data, ds.data)
    assert result.to_metadata() == ds.to_metadata()


def test_no_overwrite_preserves_existing_and_input(tmp_path):
    ds = spectrum()
    path = tmp_path / "x.nmrh5"
    write_dataset(ds, path)
    old = path.read_bytes()
    with pytest.raises(NMRNextError, match="exists"):
        write_dataset(ds, path)
    assert path.read_bytes() == old
    assert ds.data.flags.writeable is False


def test_native_rejects_corrupt_shape_and_external_link(tmp_path):
    path = tmp_path / "bad.nmrh5"
    with h5py.File(path, "w") as h:
        h.attrs["schema_version"] = "1"
        h.attrs["metadata_json"] = "{}"
        h["data"] = np.ones((1, 2), dtype="float32")
    with pytest.raises(NMRNextError):
        read_dataset(path)
    with h5py.File(path, "w") as h:
        h.attrs["schema_version"] = "1"
        h.attrs["metadata_json"] = "{}"
        h["data"] = h5py.ExternalLink("outside.h5", "data")
    with pytest.raises(NMRNextError):
        read_dataset(path)


@pytest.mark.parametrize("format,suffix", [("nmrpipe-real", ".ft2"), ("ucsf-real", ".ucsf")])
def test_processed_2d_roundtrip_shared_codec_reader(tmp_path, format, suffix):
    ds = spectrum()
    path = tmp_path / ("x" + suffix)
    report = write_dataset(ds, path, format=format)
    assert report["losses"] == []
    dic, raw = (ng.pipe.read(path) if format == "nmrpipe-real" else ng.sparky.read(path))
    np.testing.assert_array_equal(raw, ds.data[0, ::-1, ::-1].astype("float32"))
    for i, axis in enumerate(ds.axes):
        uc = (ng.pipe.make_uc(dic, raw, dim=i) if format == "nmrpipe-real"
              else ng.sparky.make_uc(dic, raw, dim=i))
        expected = ds.coordinates(axis.id, "ppm")[::-1]
        assert uc.ppm(0) == pytest.approx(expected[0], abs=1e-4)
        assert uc.ppm(axis.size - 1) == pytest.approx(expected[-1], abs=1e-4)
    imported = read_dataset(path)
    json.dumps(inspect_dataset(path), allow_nan=False)
    # Imported files retain their own descending coordinate order. A format
    # reader cannot infer that the source array was ascending before export.
    np.testing.assert_allclose(imported.data[0], raw, atol=0.0)
    for a, b in zip(imported.axes, ds.axes):
        np.testing.assert_allclose(imported.coordinates(a.id, "ppm"), ds.coordinates(b.id, "ppm")[::-1], atol=1e-4)


def test_pipe_real_1d_and_ucsf_refusal(tmp_path):
    ax = Axis("h", 16, domain="frequency", unit="Hz", start=-160, step=20,
              observation_mhz=500, reference_ppm=4.7, label="H1", encoding="real")
    ds = Dataset(np.arange(16, dtype="float32")[None], (ax,))
    path = tmp_path / "x.ft1"
    write_dataset(ds, path)
    np.testing.assert_array_equal(ng.pipe.read(path)[1], ds.data[0, ::-1])
    with pytest.raises(NMRNextError):
        write_dataset(ds, tmp_path / "x.ucsf")


def test_quadrature_refusal_and_explicit_projection(tmp_path):
    ds = spectrum(q=True)
    path = tmp_path / "x.ft2"
    with pytest.raises(NMRNextError) as exc:
        write_dataset(ds, path)
    assert exc.value.code == "UNACKNOWLEDGED_LOSS"
    assert not path.exists()
    report = write_dataset(ds, path, allow_losses=("quadrature_projection",))
    assert "quadrature_projection" in report["losses"]
    np.testing.assert_array_equal(ng.pipe.read(path)[1], ds.data[0, ::-1, ::-1].astype("float32"))
    assert ds.data.shape[0] == 2


def test_capabilities_and_preflight():
    assert {c["format"] for c in capabilities()} >= {"nmrnext-hdf5", "nmrpipe-real", "ucsf-real"}
    report = export_preflight(spectrum(q=True), "ucsf-real")
    assert report["ok"] is False
    assert "quadrature_projection" in report["losses"]


def test_truncated_and_budget_refusal_before_array_read(tmp_path):
    ds = spectrum()
    path = tmp_path / "x.ft2"
    write_dataset(ds, path)
    with pytest.raises(NMRNextError) as exc:
        inspect_dataset(path, max_bytes=100)
    assert exc.value.code == "MEMORY_BUDGET"
    path.write_bytes(path.read_bytes()[:-4])
    with pytest.raises(NMRNextError) as exc:
        read_dataset(path)
    assert exc.value.code == "INVALID_SHAPE"


def test_atomic_cleanup_when_codec_fails(tmp_path, monkeypatch):
    ds = spectrum()
    path = tmp_path / "fail.ft2"
    def fail(*args, **kwargs):
        raise RuntimeError("simulated codec failure")
    monkeypatch.setattr(ng.pipe, "write", fail)
    with pytest.raises(RuntimeError, match="simulated"):
        write_dataset(ds, path)
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_pipe_binary_header_and_payload_without_nmrglue_decoder(tmp_path):
    """Check exact float positions in the pinned NMRPipe header dialect."""
    ds = spectrum()
    path = tmp_path / "bytes.ft2"
    write_dataset(ds, path)
    payload = path.read_bytes()
    assert len(payload) == 2048 + 8*16*4
    fields = struct.unpack("<512f", payload[:2048])
    # Indices from pinned nmrglue 0.12 fdata_nums; decoding uses struct only.
    assert fields[2] == pytest.approx(2.345, abs=1e-6)
    assert fields[9] == 2
    assert fields[99] == 16
    assert fields[219] == 8
    for axis, sw_idx, obs_idx, orig_idx in ((ds.axes[0], 229, 218, 249),
                                            (ds.axes[1], 100, 119, 101)):
        n = axis.size
        assert fields[sw_idx] == pytest.approx(n*axis.step)
        assert fields[obs_idx] == pytest.approx(axis.observation_mhz)
        # The pinned unit formula gives first Hz = ORIG + SW - SW/N.
        first_hz = fields[orig_idx] + fields[sw_idx] - fields[sw_idx]/n
        high_native_hz = axis.start + (n-1)*axis.step
        high_absolute_hz = axis.reference_ppm*axis.observation_mhz + high_native_hz
        assert first_hz == pytest.approx(high_absolute_hz, abs=1e-3)
    binary_array = np.frombuffer(payload, dtype="<f4", offset=2048).reshape(8, 16)
    np.testing.assert_array_equal(binary_array, ds.data[0, ::-1, ::-1])


def test_ucsf_binary_header_and_payload_without_nmrglue_decoder(tmp_path):
    ds = spectrum()
    path = tmp_path / "bytes.ucsf"
    write_dataset(ds, path)
    payload = path.read_bytes()
    assert payload[:10] == b"UCSF NMR\x00\x00"
    assert payload[10] == 2 and payload[11] == 1 and payload[13] == 2
    for i, axis in enumerate(ds.axes):
        raw = struct.unpack_from(">6sh3I6f84s", payload, 180 + i*128)
        npoints, tile, obs, sw, car_ppm = raw[2], raw[4], raw[5], raw[6], raw[7]
        assert npoints == axis.size
        assert tile == axis.size  # 8x16 fixture occupies one full tile.
        assert obs == pytest.approx(axis.observation_mhz)
        assert sw == pytest.approx(axis.step*axis.size)
        first_hz = car_ppm*obs + sw/2
        high_absolute_hz = axis.reference_ppm*axis.observation_mhz + axis.start + (axis.size-1)*axis.step
        assert first_hz == pytest.approx(high_absolute_hz, abs=1e-3)
    binary_array = np.frombuffer(payload, dtype=">f4", offset=436).reshape(8, 16)
    np.testing.assert_array_equal(binary_array, ds.data[0, ::-1, ::-1])


def test_controlled_bruker_1d_raw_import_synthetic(tmp_path):
    """Synthetic file layout exercises the declared 1D Bruker import boundary."""
    acq = tmp_path / "acq"
    acq.mkdir()
    (acq / "acqus").write_text(
        "##TITLE= synthetic fixture\n##JCAMPDX= 5.00\n"
        "##$TD= 256\n##$AQ_mod= 3\n##$BYTORDA= 0\n##$DTYPA= 0\n"
        "##$SW_h= 8000\n##$SFO1= 500.13\n##$O1= 1200\n"
        "##$NUC1= <1H>\n##$GRPDLY= 23.5\n##END=\n",
        encoding="ascii")
    interleaved = np.arange(256, dtype="<i4")
    (acq / "fid").write_bytes(interleaved.tobytes())
    head = inspect_dataset(acq)
    assert head["format"] == "bruker-raw-1d"
    assert head["shape"] == [2, 128]
    ds = read_dataset(acq)
    assert ds.complex_axes == ("a0",)
    assert ds.axes[0].domain == "time"
    assert ds.axes[0].step == pytest.approx(1/8000)
    assert ds.axes[0].reference_ppm is None
    assert ds.metadata["import"]["digital_filter"]["GRPDLY"] == 23.5
    np.testing.assert_array_equal(ds.data[0], interleaved[::2])
    np.testing.assert_array_equal(ds.data[1], interleaved[1::2])
    assert "source_sha256" in ds.metadata["import"]
    with pytest.raises(NMRNextError):
        write_dataset(ds, tmp_path / "unsupported.bruker", format="bruker-raw-1d")


def test_ucsf_uses_nucleus_code_for_header_not_display_label(tmp_path):
    ds = spectrum()
    axes = tuple(Axis(**{**a.to_dict(), "label": "Long display label", "nucleus": "15N" if i == 0 else "1H"})
                 for i, a in enumerate(ds.axes))
    renamed = Dataset(ds.data, axes)
    preflight = export_preflight(renamed, "ucsf")
    assert preflight["errors"] == []
    assert preflight["losses"] == ["axis_annotations"]
    path = tmp_path / "renamed.ucsf"
    write_dataset(renamed, path, allow_losses=("axis_annotations",))
    dic, _ = ng.sparky.read(path)
    assert dic["w1"]["nucleus"] == "15N"
    assert dic["w2"]["nucleus"] == "1H"


def test_bruker_big_endian_float64_padding_and_truncation(tmp_path):
    acq = tmp_path / "acq"
    acq.mkdir()
    (acq / "acqus").write_text(
        "##TITLE= synthetic fixture\n##JCAMPDX= 5.00\n"
        "##$TD= 100\n##$AQ_mod= 1\n##$BYTORDA= 1\n##$DTYPA= 2\n"
        "##$SW_h= 4000\n##$SFO1= 600.1\n##$NUC1= <1H>\n##END=\n",
        encoding="ascii")
    # Float64 uses 128 real points per 1024-byte block; 28 are padding.
    points = np.arange(128, dtype=">f8")
    (acq / "fid").write_bytes(points.tobytes())
    ds = read_dataset(acq)
    assert ds.data.shape == (2, 50)
    assert ds.metadata["import"]["padding_complex_points"] == 14
    np.testing.assert_array_equal(ds.data[0], points[:100:2])
    np.testing.assert_array_equal(ds.data[1], points[1:100:2])
    (acq / "fid").write_bytes(points.tobytes()[:-8])
    with pytest.raises(NMRNextError) as exc:
        read_dataset(acq)
    assert exc.value.code == "INVALID_SHAPE"


@pytest.mark.parametrize("storage", ["virtual", "external"])
def test_native_rejects_indirect_dataset_storage_without_reading(tmp_path, storage):
    ds = spectrum(shape=(8, 16))
    path = tmp_path / "indirect.nmrh5"
    with h5py.File(path, "w") as h:
        h.attrs["schema_version"] = "1"
        h.attrs["metadata_json"] = json.dumps(ds.to_metadata())
        if storage == "virtual":
            layout = h5py.VirtualLayout(shape=ds.data.shape, dtype="float64")
            layout[:] = h5py.VirtualSource("other.h5", "data", shape=ds.data.shape)
            h.create_virtual_dataset("data", layout)
        else:
            h.create_dataset("data", shape=ds.data.shape, dtype="float64",
                             external=[("other.bin", 0, ds.data.nbytes)])
    with pytest.raises(NMRNextError) as exc:
        inspect_dataset(path)
    assert exc.value.code == "INVALID_STORAGE"


@pytest.mark.parametrize("metadata", [
    '{"schema_version":"1","axes":[],"complex_axes":null,"metadata":{},"provenance":[]}',
    '{"schema_version":"1","axes":[0],"complex_axes":[],"metadata":{},"provenance":[]}',
    '{"schema_version":"1","axes":null,"complex_axes":[],"metadata":{},"provenance":[]}',
    '{"schema_version":"1","axes":{},"complex_axes":"a0","metadata":{},"provenance":[]}',
    '{"schema_version":"1","schema_version":"1","axes":[],"complex_axes":[],"metadata":{},"provenance":[]}',
    '{"schema_version":"1","axes":[],"complex_axes":[],"metadata":{"bad":NaN},"provenance":[]}',
    '{"schema_version":"1","axes":[],"complex_axes":[],"metadata":{"bad":1e999},"provenance":[]}',
])
def test_native_malformed_metadata_is_structured(tmp_path, metadata):
    path = tmp_path / "malformed.nmrh5"
    with h5py.File(path, "w") as h:
        h.attrs["schema_version"] = "1"
        h.attrs["metadata_json"] = metadata
        h.create_dataset("data", data=np.ones((1, 2), dtype="float32"))
    with pytest.raises(NMRNextError) as exc:
        inspect_dataset(path)
    assert exc.value.code == "INVALID_METADATA"


def test_native_header_validates_axis_semantics_before_array_read(tmp_path):
    ds = spectrum()
    metadata = ds.to_metadata()
    metadata["axes"][0]["step"] = 0
    path = tmp_path / "bad-axis.nmrh5"
    with h5py.File(path, "w") as h:
        h.attrs["schema_version"] = "1"
        h.attrs["metadata_json"] = json.dumps(metadata)
        h.create_dataset("data", data=ds.data)
    with pytest.raises(NMRNextError) as exc:
        inspect_dataset(path)
    assert exc.value.code == "INVALID_METADATA"


def test_ucsf_budget_accounts_for_padded_tiles(tmp_path):
    path = tmp_path / "tile.ucsf"
    write_dataset(spectrum(), path)
    payload = bytearray(path.read_bytes())
    # Keep 8x16 tile allocation and file length, but declare 2x2 logical data.
    for offset in (180, 308):
        struct.pack_into(">I", payload, offset + 8, 2)   # npoints
        struct.pack_into(">I", payload, offset + 12, 2)  # size
    path.write_bytes(payload)
    with pytest.raises(NMRNextError) as exc:
        inspect_dataset(path, max_bytes=100)  # logical 16 B, padded 512 B
    assert exc.value.code == "MEMORY_BUDGET"


def test_projection_report_names_fixed_all_real_channel():
    report = export_preflight(spectrum(q=True), "nmrpipe", allow_losses=("quadrature_projection",))
    assert report["projection"] == {"component_index": 0,
                                    "meaning": "all-real quadrature channel",
                                    "complex_axes": ["a1"]}
    assert "selected real component" not in report["preserved"]


def test_native_import_and_inspection_do_not_load_processed_codec_stack(tmp_path):
    path = tmp_path / "native.nmrh5"
    write_dataset(spectrum(), path)
    source = Path(__file__).resolve().parents[2] / "src"
    script = (
        f"import sys; sys.path.insert(0, {str(source)!r}); "
        "from nmrnext.io import inspect_dataset; "
        f"inspect_dataset({str(path)!r}); "
        "assert not any(k.startswith(('nmrglue', 'scipy')) for k in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-I", "-c", script],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("n", [1, 3, 5, 6])
@pytest.mark.parametrize("step", [-20.0, 20.0])
def test_pipe_odd_and_even_point_calibration_is_exactly_declared(tmp_path, n, step):
    axis = Axis("h", n, domain="frequency", unit="Hz", start=100., step=step,
                observation_mhz=500., reference_ppm=4.7, reference_hz=13.,
                label="1H", encoding="real")
    ds = Dataset(np.arange(n, dtype="float32")[None], (axis,))
    path = tmp_path / f"{n}-{int(step)}.ft1"
    report = write_dataset(ds, path, allow_losses=("reference_fields",))
    assert report["format"] == "nmrpipe-real"
    dic, raw = ng.pipe.read(path)
    expected = ds.coordinates("h", "ppm")
    if step > 0:
        expected = expected[::-1]
    np.testing.assert_allclose(ng.pipe.make_uc(dic, raw).ppm_scale(), expected, atol=1e-6, rtol=0)
    binary = path.read_bytes()
    fields = struct.unpack("<512f", binary[:2048])
    actual_first_hz = fields[101] + fields[100] - fields[100]/n
    assert actual_first_hz == pytest.approx(expected[0]*500., abs=5e-4)


def test_pipe_mixed_odd_even_2d_axis_calibration(tmp_path):
    axes = (Axis("n", 3, domain="frequency", unit="Hz", start=100., step=-20.,
                 observation_mhz=50., reference_ppm=118., label="15N", encoding="real"),
            Axis("h", 6, domain="frequency", unit="Hz", start=-60., step=20.,
                 observation_mhz=500., reference_ppm=4.7, label="1H", encoding="real"))
    ds = Dataset(np.arange(18, dtype="float32").reshape(1, 3, 6), axes)
    path = tmp_path / "mixed.ft2"
    write_dataset(ds, path)
    dic, raw = ng.pipe.read(path)
    for i, axis in enumerate(axes):
        expected = ds.coordinates(axis.id, "ppm")
        if axis.step > 0:
            expected = expected[::-1]
        np.testing.assert_allclose(ng.pipe.make_uc(dic, raw, dim=i).ppm_scale(), expected,
                                   atol=1e-6, rtol=0)


@pytest.mark.parametrize("fmt,suffix", [("nmrpipe", ".ft2"), ("ucsf", ".ucsf")])
@pytest.mark.parametrize("sign", [-1., 1.])
def test_float32_overflow_refused_before_publication(tmp_path, fmt, suffix, sign):
    ds = spectrum()
    source = ds.data.copy()
    source[0, 0, 0] = sign*1e40
    ds = Dataset(source, ds.axes)
    report = export_preflight(ds, fmt, allow_losses=("float32_quantization",))
    assert report["ok"] is False
    assert "float32_overflow" in report["errors"]
    path = tmp_path / ("overflow" + suffix)
    with pytest.raises(NMRNextError) as exc:
        write_dataset(ds, path, format=fmt, allow_losses=("float32_quantization",))
    assert exc.value.code == "FLOAT32_OVERFLOW"
    assert not path.exists()
    path.write_bytes(b"existing")
    with pytest.raises(NMRNextError):
        write_dataset(ds, path, format=fmt, allow_losses=("float32_quantization",), overwrite=True)
    assert path.read_bytes() == b"existing"


@pytest.mark.parametrize("fmt,suffix", [("nmrpipe", ".ft1"), ("ucsf", ".ucsf")])
def test_finite_float32_boundary_and_quantization_remain_exportable(tmp_path, fmt, suffix):
    peak = float(np.finfo(np.float32).max)
    if fmt == "nmrpipe":
        ax = Axis("h", 2, domain="frequency", unit="Hz", start=-10, step=10,
                  observation_mhz=500, reference_ppm=4.7, label="1H", encoding="real")
        ds = Dataset(np.array([[peak, np.nextafter(peak, 0.)]], dtype="float64"), (ax,))
    else:
        base = spectrum(shape=(2, 2))
        data = base.data.copy()
        data[0, 0, 0], data[0, 1, 1] = peak, np.nextafter(peak, 0.)
        ds = Dataset(data, base.axes)
    report = export_preflight(ds, fmt, allow_losses=("float32_quantization",))
    assert report["ok"] and "float32_overflow" not in report["errors"]
    path = tmp_path / ("boundary" + suffix)
    write_dataset(ds, path, format=fmt, allow_losses=("float32_quantization",))
    assert np.isfinite(read_dataset(path).data).all()


@pytest.mark.parametrize("bad_line", ["##$D= (0..2)\n1\n", "##$D= <unterminated\n",
                                     "##$BAD..9)= (0..0) 1\n"])
@pytest.mark.parametrize("mode", ["inspect", "read", "cli"])
def test_truncated_jcamp_continuations_terminate_with_structured_error(tmp_path, bad_line, mode):
    acq = tmp_path / "acq"
    acq.mkdir()
    (acq / "acqus").write_text(
        "##$TD= 256\n##$AQ_mod= 3\n##$BYTORDA= 0\n##$DTYPA= 0\n"
        "##$SW_h= 8000\n##$SFO1= 500\n##$NUC1= <1H>\n" + bad_line, encoding="ascii")
    (acq / "fid").write_bytes(np.arange(256, dtype="<i4").tobytes())
    source = Path(__file__).resolve().parents[2] / "src"
    if mode == "cli":
        script = (f"import sys; sys.path.insert(0, {str(source)!r}); "
                  f"from nmrnext.cli import main; raise SystemExit(main(['inspect', {str(acq)!r}, '--json']))")
    else:
        function = "inspect_dataset" if mode == "inspect" else "read_dataset"
        script = (f"import sys,json; sys.path.insert(0, {str(source)!r}); "
                  f"from nmrnext.io import {function}; from nmrnext.errors import NMRNextError; "
                  f"\ntry: {function}({str(acq)!r})\n"
                  "except NMRNextError as e: print(json.dumps(e.to_dict())); sys.exit(2)\n")
    result = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True,
                            text=True, timeout=3, check=False)
    assert result.returncode == 2, result.stderr
    error = json.loads(result.stderr if mode == "cli" else result.stdout)
    assert (error["error"] if mode == "cli" else error)["code"] == "INVALID_HEADER"


def test_valid_unknown_bruker_array_preserved_and_input_unchanged(tmp_path):
    acq = tmp_path / "acq"
    acq.mkdir()
    acqus = acq / "acqus"
    acqus.write_text(
        "##$TD= 256\n##$AQ_mod= 3\n##$BYTORDA= 0\n##$DTYPA= 0\n"
        "##$SW_h= 8000\n##$SFO1= 500\n##$NUC1= <1H>\n"
        "##$D= (0..2)\n1 2 3\n##END=\n", encoding="ascii")
    fid = acq / "fid"
    fid.write_bytes(np.arange(256, dtype="<i4").tobytes())
    original = (acqus.read_bytes(), fid.read_bytes())
    assert inspect_dataset(acq)["shape"] == [2, 128]
    ds = read_dataset(acq)
    assert "##$D= (0..2)" in ds.metadata["import"]["raw_acqus_text"]
    assert (acqus.read_bytes(), fid.read_bytes()) == original
    assert ds.metadata["import"]["source_sha256"]["acqus"] == hashlib.sha256(original[0]).hexdigest()


def test_bruker_parser_error_cannot_be_swallowed_into_success(tmp_path, monkeypatch):
    acq = tmp_path / "acq"
    acq.mkdir()
    (acq / "acqus").write_text(
        "##$TD= 256\n##$AQ_mod= 3\n##$BYTORDA= 0\n##$DTYPA= 0\n"
        "##$SW_h= 8000\n##$SFO1= 500\n##$NUC1= <1H>\n##END=\n", encoding="ascii")
    (acq / "fid").write_bytes(np.arange(256, dtype="<i4").tobytes())
    def swallowing_parser(stream, dictionary):
        # Model the dependency's bare `except` around a malformed parameter.
        try:
            for _ in range(30):
                stream.readline()
        except NMRNextError:
            pass
        return {"TD": 256, "AQ_mod": 3, "BYTORDA": 0, "DTYPA": 0,
                "SW_h": 8000, "SFO1": 500, "NUC1": "1H"}
    monkeypatch.setattr(ng.bruker, "parse_jcamp_file", swallowing_parser)
    with pytest.raises(NMRNextError) as exc:
        inspect_dataset(acq)
    assert exc.value.code == "INVALID_HEADER"


def test_latin1_unknown_bruker_string_preserved(tmp_path):
    acq = tmp_path / "acq"
    acq.mkdir()
    raw = ("##$TD= 256\n##$AQ_mod= 3\n##$BYTORDA= 0\n##$DTYPA= 0\n"
           "##$SW_h= 8000\n##$SFO1= 500\n##$NUC1= <1H>\n"
           "##$NOTE= <caf\xe9>\n##END=\n").encode("latin-1")
    (acq / "acqus").write_bytes(raw)
    fid = np.arange(256, dtype="<i4").tobytes()
    (acq / "fid").write_bytes(fid)
    ds = read_dataset(acq)
    assert "caf\xe9" in ds.metadata["import"]["raw_acqus_text"]
    assert (acq / "acqus").read_bytes() == raw and (acq / "fid").read_bytes() == fid


@pytest.mark.parametrize("fmt,suffix", [("nmrpipe", ".ft2"), ("ucsf", ".ucsf")])
@pytest.mark.parametrize("field,value", [
    ("step", -1e40), ("observation_mhz", 1e40), ("reference_ppm", 1e40),
    ("step", -1e-50), ("observation_mhz", 1e-50),
])
def test_unrepresentable_target_header_refused_without_staging(tmp_path, fmt, suffix, field, value, monkeypatch):
    ds = spectrum(shape=(2, 2))
    axes = list(ds.axes)
    axes[0] = Axis(**{**axes[0].to_dict(), field: value})
    ds = Dataset(ds.data, tuple(axes))
    report = export_preflight(ds, fmt)
    assert report["ok"] is False
    assert "target_header_unrepresentable" in report["errors"]
    def writer_must_not_run(*args, **kwargs):
        raise AssertionError("codec writer was invoked before fatal preflight")
    monkeypatch.setattr(ng.pipe if fmt == "nmrpipe" else ng.sparky, "write", writer_must_not_run)
    path = tmp_path / ("bad" + suffix)
    with pytest.raises(NMRNextError) as exc:
        write_dataset(ds, path, format=fmt)
    assert exc.value.code == "TARGET_HEADER_UNREPRESENTABLE"
    assert list(tmp_path.iterdir()) == []
    path.write_bytes(b"existing")
    with pytest.raises(NMRNextError):
        write_dataset(ds, path, format=fmt, overwrite=True)
    assert path.read_bytes() == b"existing"
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]


@pytest.mark.parametrize("fmt", ["nmrpipe", "ucsf"])
def test_derived_carrier_overflow_rejected_even_when_inputs_fit_float32(fmt):
    ds = spectrum(shape=(2, 2))
    axes = list(ds.axes)
    axes[0] = Axis(**{**axes[0].to_dict(), "observation_mhz": 1e-20,
                      "start": 1e30, "reference_ppm": 0.})
    report = export_preflight(Dataset(ds.data, tuple(axes)), fmt)
    assert "target_header_unrepresentable" in report["errors"]


def test_bruker_cli_warning_is_structured_json_only(tmp_path):
    acq = tmp_path / "acq"
    acq.mkdir()
    (acq / "acqus").write_text(
        "##$TD= 256\n##$AQ_mod= 3\n##$BYTORDA= 0\n##$DTYPA= 0\n"
        "##$SW_h= 8000\n##$SFO1= 500\n##$NUC1= <1H>\n"
        "extraneous line\n##END=\n", encoding="ascii")
    (acq / "fid").write_bytes(np.arange(256, dtype="<i4").tobytes())
    source = Path(__file__).resolve().parents[2] / "src"
    script = (f"import sys; sys.path.insert(0, {str(source)!r}); "
              f"from nmrnext.cli import main; raise SystemExit(main(['inspect', {str(acq)!r}, '--json']))")
    result = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True,
                            text=True, timeout=_startup_timeout_seconds(3), check=False)
    assert result.returncode == 2
    assert json.loads(result.stderr)["error"]["code"] == "INVALID_HEADER"


@pytest.mark.parametrize("fmt,suffix", [("nmrpipe", ".ft2"), ("ucsf", ".ucsf")])
def test_cli_target_header_refusal_is_structured_and_leaves_no_output(tmp_path, fmt, suffix):
    ds = spectrum(shape=(2, 2))
    axes = list(ds.axes)
    axes[0] = Axis(**{**axes[0].to_dict(), "step": -1e40})
    source = tmp_path / "source.nmrh5"
    target = tmp_path / ("target" + suffix)
    write_dataset(Dataset(ds.data, tuple(axes)), source)
    script = ("from nmrnext.cli import main; "
              f"raise SystemExit(main(['convert', {str(source)!r}, {str(target)!r}, '--format', {fmt!r}]))")
    result = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True,
                            text=True, timeout=_startup_timeout_seconds(4), check=False)
    assert result.returncode == 2
    assert json.loads(result.stderr)["error"]["code"] == "TARGET_HEADER_UNREPRESENTABLE"
    assert not target.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == [source.name]
