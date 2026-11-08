"""Failure-first synthetic controls; never reads the acquired ASU archive."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import zipfile

import numpy as np
import pytest

SPEC = importlib.util.spec_from_file_location(
    "asu_validator", Path(__file__).resolve().parents[2] / "tools/validate_asu_filter.py")
asu = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(asu)


@pytest.fixture
def samples():
    from nmrnext import Axis, Dataset
    from nmrnext.operations import run_operations
    pairs = [(100000 + j * 3, -80000 + j * 7) for j in range(32768)]
    data = np.array(pairs, dtype=np.float64).T.copy()
    axis = Axis("a0", 32768, label="1H", nucleus="1H", step=.0001284,
                dwell_seconds=.0001284, observation_mhz=400.13311072)
    hashes = {"fid": "synthetic-fid", "acqus": "synthetic-acqus"}
    metadata = {"import": {"format": "bruker-raw-1d", "raw_acqus_text": "synthetic",
        "acquisition_parameters": {k: v for k, v in asu.PROFILE.items() if k != "PARMODE"}, "source_sha256": hashes,
        "padding_complex_points": 0, "digital_filter": {"state": "uncorrected_or_unknown",
        "GRPDLY": 76, "DECIM": 2568, "DSPFVS": 21}}}
    source = Dataset(data, (axis,), ("a0",), metadata)
    corrected = run_operations(source, asu.RECIPE, backend="native", threads=1)
    reference = np.array(asu.integer_oracle(pairs), dtype=np.float64).T
    return source, corrected, pairs, hashes, reference


def checks(samples, source=None, corrected=None):
    s, c, pairs, hashes, reference = samples
    return {v["id"]: v for v in asu.numerical_checks(
        source or s, corrected or c, pairs, asu.PROFILE, "synthetic", hashes,
        reference[0] + 1j * reference[1])}


def fake(dataset, **changes):
    values = {name: getattr(dataset, name) for name in ("data", "axes", "complex_axes", "metadata", "provenance")}
    values["coordinates"] = dataset.coordinates
    values.update(changes)
    return SimpleNamespace(**values)


def test_literal_decoder_checks_every_signed_pair_and_length():
    pairs = [(-2147483648, 2147483647), (-1, 2), (123456, -765432)]
    payload = b"".join(struct.pack("<ii", *p) for p in pairs)
    assert asu.literal_pairs(payload, 3) == pairs
    assert asu.literal_pairs(b"".join(struct.pack(">ii", *p) for p in pairs), 3) != pairs
    for bad in (payload[:-1], payload[:-8], payload + bytes(8)):
        with pytest.raises(ValueError):
            asu.literal_pairs(bad, 3)


def test_integer_oracle_avoids_int32_overflow():
    pairs = [(2147483647, -2147483648)] * 256
    oracle = asu.integer_oracle(pairs)
    assert oracle[0] == (4294967294, -4294967296)
    assert oracle[71] == oracle[0]
    assert oracle[72] == pairs[0]
    assert len(oracle) == 178


def test_complete_synthetic_policy_passes(samples):
    result = list(checks(samples).values())
    assert all(v["pass"] for v in result), result
    assert asu.complete_pass([{"id": "ASU-01", "pass": True}] + result)


@pytest.mark.parametrize("mutation", ["swapped", "wrong_byteorder", "missing", "wrong_shape"])
def test_import_corruptions_fail(samples, mutation):
    source = samples[0]
    data = source.data.copy()
    if mutation == "swapped":
        data = data[::-1]
    elif mutation == "wrong_byteorder":
        payload = b"".join(struct.pack(">ii", *p) for p in samples[2])
        data = np.array(asu.literal_pairs(payload, 32768), dtype=np.float64).T
    elif mutation == "missing":
        data = data[:, :-1]
    else:
        data = data.reshape(1, -1)
    assert checks(samples, source=fake(source, data=data))["ASU-02"]["pass"] is False


@pytest.mark.parametrize("mutation", ["trim", "fold", "channel", "nan"])
def test_filter_corruptions_fail(samples, mutation):
    corrected = samples[1]
    data = corrected.data.copy()
    if mutation == "trim":
        data = data[:, :-1]
    elif mutation == "fold":
        data[0, 71] += 1e5
    elif mutation == "channel":
        data = data[::-1]
    else:
        data[0, 10] = np.nan
    result = checks(samples, corrected=fake(corrected, data=data))
    assert not result["ASU-06"]["pass"]
    assert not result["ASU-05"]["pass"]


def test_metadata_and_quadrature_changes_fail(samples):
    source, corrected = samples[:2]
    metadata = copy.deepcopy(source.metadata)
    metadata["import"]["acquisition_parameters"]["GRPDLY"] = 75
    assert not checks(samples, source=fake(source, metadata=metadata))["ASU-03"]["pass"]
    assert not checks(samples, source=fake(source, complex_axes=()))["ASU-03"]["pass"]
    metadata = copy.deepcopy(corrected.metadata)
    metadata["import"]["digital_filter"]["correction"]["fold_complex_points"] = 71
    assert not checks(samples, corrected=fake(corrected, metadata=metadata))["ASU-04"]["pass"]
    metadata = copy.deepcopy(source.metadata)
    metadata["import"]["raw_acqus_text"] = "synthetic\r\n"
    assert not checks(samples, source=fake(source, metadata=metadata))["ASU-03"]["pass"]


def test_missing_duplicate_extra_and_nonboolean_checks_never_pass():
    good = [{"id": n, "pass": True} for n in asu.REQUIRED]
    assert asu.complete_pass(good)
    assert not asu.complete_pass(good[:-1])
    assert not asu.complete_pass(good + [good[0]])
    assert not asu.complete_pass(good + [{"id": "extra", "pass": True}])
    assert not asu.complete_pass([{**x, "pass": 1} for x in good])
    assert not asu.complete_pass([{**x, "pass": False} if x["id"] == "ASU-01" else x for x in good])


def test_file_lock_and_source_mutations_are_refused(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"original")
    lock = tmp_path / "lock.json"
    asu.write_json_new(lock, {"binding": asu.sha256(source)})
    frozen = asu.sha256(lock)
    assert asu.load_locked(lock, frozen)["binding"] == asu.sha256(source)
    bindings = asu.bindings({"source": source})
    source.write_bytes(b"mutated")
    with pytest.raises(ValueError):
        asu.require_bindings(bindings, {"source": source})
    lock.write_text('{"binding":"replacement"}')
    with pytest.raises(ValueError):
        asu.load_locked(lock, frozen)
    with pytest.raises(FileExistsError):
        asu.write_json_new(lock, {})


def test_runtime_mutation_and_missing_runtime_fields_refused():
    expected = {"schema": 1, "versions": {"nmrglue": "0.12"}, "files": {"native": "abc"}}
    assert asu.require_runtime(expected, copy.deepcopy(expected)) is None
    for changed in ({**expected, "files": {"native": "changed"}}, {"schema": 1},
                    {**expected, "versions": {"nmrglue": "0.13"}}):
        with pytest.raises(ValueError):
            asu.require_runtime(expected, changed)


def zip_case(names):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        for name in names:
            info = zipfile.ZipInfo("placeholder")
            info.filename = info.orig_filename = name
            z.writestr(info, b"synthetic")
    stream.seek(0)
    with zipfile.ZipFile(stream) as z:
        case = {"directory": [asu.zip_record(i) for i in z.infolist()], "known_metadata": []}
    stream.seek(0)
    return stream, case


@pytest.mark.parametrize("name", ["../fid", "/fid", "C:/fid", "a\\fid"])
def test_zip_path_refusals(name):
    stream, case = zip_case([name])
    with zipfile.ZipFile(stream) as z, pytest.raises(ValueError):
        asu.verify_zip(z, case)


def test_zip_duplicates_symlinks_and_size_mutation_refused():
    with pytest.warns(UserWarning, match="Duplicate name"):
        stream, case = zip_case(["fid", "fid"])
    with zipfile.ZipFile(stream) as z, pytest.raises(ValueError):
        asu.verify_zip(z, case)
    stream, case = zip_case(["fid"])
    case["directory"][0]["uncompressed_bytes"] += 1
    with zipfile.ZipFile(stream) as z, pytest.raises(ValueError):
        asu.verify_zip(z, case)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        info = zipfile.ZipInfo("fid")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        z.writestr(info, "target")
    stream.seek(0)
    with zipfile.ZipFile(stream) as z, pytest.raises(ValueError):
        asu.verify_zip(z, {"directory": [asu.zip_record(z.infolist()[0])], "known_metadata": []})


def test_hash_only_extraction_never_calls_numeric_decoder_and_refuses_overwrite(tmp_path, monkeypatch):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr(asu.PREFIX + "acqus", b"synthetic-header".ljust(8861, b" "))
        z.writestr(asu.PREFIX + "fid", b"\x01\x02\x03\x04" * 65536)
        z.writestr(asu.PREFIX + "pulseprogram", b"must not be extracted")
    monkeypatch.setattr(asu, "literal_pairs", lambda *a: pytest.fail("Hash-only prepare decoded numbers"))
    directory = tmp_path / "data"
    records = asu.extract_hash_only(stream.getvalue(), directory)
    assert set(records) == {"fid", "acqus"}
    assert {p.name for p in directory.iterdir()} == {"fid", "acqus"}
    assert records["fid"]["bytes"] == 262144
    with pytest.raises(FileExistsError):
        asu.extract_hash_only(stream.getvalue(), directory)
    asu.verify_data(directory, records)
    (directory / "fid").write_bytes(b"changed")
    with pytest.raises(ValueError):
        asu.verify_data(directory, records)


def test_known_metadata_hash_and_crc_corruption_refused():
    stream, case = zip_case(["acqus"])
    case["known_metadata"] = [{"source_member": "acqus", "bytes": 9, "sha256": "0" * 64}]
    with zipfile.ZipFile(stream) as z, pytest.raises(ValueError):
        asu.verify_zip(z, case)
    stream, case = zip_case(["acqus"])
    payload = bytearray(stream.getvalue())
    payload[30 + len("acqus")] ^= 1
    case["known_metadata"] = [{"source_member": "acqus", "bytes": 9, "sha256": "0" * 64}]
    with zipfile.ZipFile(io.BytesIO(payload)) as z, pytest.raises(zipfile.BadZipFile):
        asu.verify_zip(z, case)


def test_cli_failure_receipt_and_no_overwrite(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    archive = tmp_path / "bad.zip"
    archive.write_bytes(b"not an archive")
    output = tmp_path / "receipt"
    argv = ["prepare", "--case", str(repo / "benchmarks/cases/asu-meod25-import-filter.json"),
            "--protocol", str(repo / "docs/asu-import-filter-protocol.md"),
            "--archive", str(archive), "--output", str(output)]
    assert asu.main(argv) == 1
    receipt = json.loads((output / "receipt.json").read_text())
    assert receipt["pass"] is False and receipt["checks_complete"] is False
    assert not (output / "data").exists()
    assert (output / "exit-code.txt").read_text().strip() == "1"
    assert "Archive file/size mismatch" in (output / "stderr.txt").read_text()
    before = (output / "receipt.json").read_bytes()
    with pytest.raises(FileExistsError):
        asu.main(argv)
    assert (output / "receipt.json").read_bytes() == before


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'])
def test_noncanonical_json_refused(tmp_path, text):
    path = tmp_path / "bad.json"
    path.write_text(text)
    with pytest.raises(ValueError):
        asu.load_json(path)
