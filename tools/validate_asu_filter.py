"""Staged, source-only ASU integer import/filter validation; see fixed protocol.

download never extracts; prepare extracts/hashes bytes without numerical decode;
runtime freezes an installed environment; run requires both externally frozen locks.
Each stage requires its separate root authorization. No source content is executed.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import importlib
import importlib.metadata
import io
import json
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import struct
import sys
import traceback
import urllib.request
import zipfile
import zlib

REQUIRED = tuple(f"ASU-{i:02d}" for i in range(1, 7))
RECIPE = [{"op": "digital_filter", "axis": "a0"}]
PROFILE = {"AQ_mod": 3, "PARMODE": 0, "BYTORDA": 0, "DTYPA": 0,
           "TD": 65536, "GRPDLY": 76, "DECIM": 2568, "DSPFVS": 21,
           "DIGMOD": 3, "SW_h": 7788.16199376947, "SFO1": 400.13311072,
           "BF1": 400.13, "O1": 3110.71999999513, "NUC1": "1H"}
ARCHIVE_SHA = "38c8d260169d42f0d15ab5c7b9ce7084735419688c9ea66e76dc34fa6b356094"
COMMIT = "af3b9c2c4642e63e379bc349cb40e1fe8c5730e6"
BLOB = "6b4d3850d0167a48d9301030bcbe458e5eb1b29e"
URL = f"https://raw.githubusercontent.com/CHM343/NMR/{COMMIT}/Data/MeOD_25C_Temp_Cal_Bruker400.zip"
PREFIX = "2023_10_04_AcAc_Solvents_Temp_Bruker400/232/"
RTOL, ATOL = 1e-11, 1e-7
UNVERIFIED = ["Full raw-to-TopSpin processing/phase/baseline/peak/volume parity UNVERIFIED",
              "S19 six of eighteen comparisons FAIL unchanged", "Human acceptance UNVERIFIED",
              "NMRPipe parity UNVERIFIED", "No stable promotion"]


def sha256(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Regular nonsymlink file required: {path}")
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 32 << 20:
        raise ValueError("Bounded regular JSON file required")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def write_json_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def load_locked(path, expected_sha256):
    if not re.fullmatch("[0-9a-f]{64}", expected_sha256 or "") or sha256(path) != expected_sha256:
        raise ValueError("Externally frozen lock SHA-256 mismatch")
    return load_json(path)


def bindings(paths):
    return {name: {"bytes": Path(path).stat().st_size, "sha256": sha256(path)}
            for name, path in sorted(paths.items())}


def require_bindings(expected, paths):
    if expected != bindings(paths):
        raise ValueError("Source/input binding changed or is incomplete")


def require_runtime(expected, actual):
    if expected != actual:
        raise ValueError("Installed runtime changed or is incomplete")


def complete_pass(checks):
    ids = [item.get("id") for item in checks]
    return (len(ids) == len(REQUIRED) and set(ids) == set(REQUIRED)
            and all(type(c.get("pass")) is bool and c["pass"] for c in checks))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        raise ValueError("Source redirects are refused")


def validate_case(case):
    wanted = {"schema": 1, "id": "asu-meod25-import-filter-v1", "commit": COMMIT,
              "archive": {"url": URL, "bytes": 1060855, "git_blob_sha1": BLOB, "sha256": ARCHIVE_SHA},
              "member_prefix": PREFIX, "extract_allowlist": ["acqus", "fid"],
              "profile": PROFILE, "required_checks": list(REQUIRED), "recipe": RECIPE,
              "backend": "native", "threads": 1, "filter_rtol": RTOL, "filter_atol": ATOL,
              "candidate_version": "0.3.0b3", "nmrglue_version": "0.12"}
    if any(case.get(key) != value for key, value in wanted.items()):
        raise ValueError("Case differs from the fixed preregistration")
    names = [e["name"] for e in case["directory"]]
    if len(names) != 24 or len(set(names)) != 24:
        raise ValueError("Incomplete pinned archive directory")


def zip_record(info):
    return {"name": info.filename, "flags": info.flag_bits, "method": info.compress_type,
            "crc32": info.CRC, "compressed_bytes": info.compress_size,
            "uncompressed_bytes": info.file_size, "local_offset": info.header_offset}


def verify_zip(archive, case):
    infos = archive.infolist()
    names = [i.orig_filename for i in infos]
    expected = {e["name"]: e for e in case["directory"]}
    if len(names) != len(set(names)) or len(expected) != len(case["directory"]) or set(names) != set(expected):
        raise ValueError("ZIP duplicate, missing or unexpected member")
    for info in infos:
        name = info.orig_filename
        parts = PurePosixPath(name)
        mode = info.external_attr >> 16
        if (name != info.filename or parts.is_absolute() or ".." in parts.parts or "\\" in name or ":" in name
                or str(parts) != name or info.is_dir() or info.flag_bits & 1
                or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                or zip_record(info) != expected[name]):
            raise ValueError(f"Unsafe or changed ZIP member: {name}")
    # Intentionally read only known, bounded text metadata here; never FID/arrays.
    for item in case["known_metadata"]:
        if PurePosixPath(item["source_member"]).name not in {"acqus", "procs", "title", "auditp.txt"}:
            raise ValueError("Unexpected metadata member")
        if item["bytes"] > 65536:
            raise ValueError("Oversized text metadata")
        data = archive.read(item["source_member"])
        if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("Known metadata SHA-256 mismatch")


def read_archive(path, case):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size != 1060855:
        raise ValueError("Archive file/size mismatch")
    data = path.read_bytes()
    if (hashlib.sha256(data).hexdigest() != ARCHIVE_SHA
            or hashlib.sha1(b"blob 1060855\0" + data).hexdigest() != BLOB):
        raise ValueError("Archive SHA-256 or Git blob mismatch")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        verify_zip(archive, case)
    return data


def literal_pairs(payload, count=32768):
    """No NumPy/vendor/product decoder, and no int32 arithmetic."""
    if type(count) is not int or count < 1 or len(payload) != count * 8:
        raise ValueError("Incomplete or unexpected signed int32 complex pairs")
    return list(struct.iter_unpack("<ii", payload))


def integer_oracle(pairs):
    n = len(pairs)
    if n <= 150:
        raise ValueError("Insufficient points for fixed GRPDLY=76 policy")
    advanced = [pairs[(j + 76) % n] for j in range(n)]
    output = []
    for j in range(n - 78):
        r, i = (int(v) for v in advanced[j])
        if j < 72:
            tr, ti = advanced[n - 1 - j]
            r, i = r + int(tr), i + int(ti)
        output.append((r, i))
    return output


def scalars(text):
    result = {}
    for line in text.splitlines():
        match = re.fullmatch(r"##\$([^=]+)=\s*(.*)", line)
        if not match or match[2].startswith("("):
            continue
        key, value = match.groups()
        if key in result:
            raise ValueError(f"Duplicate scalar: {key}")
        if value.startswith("<") and value.endswith(">"):
            result[key] = value[1:-1]
        else:
            try:
                result[key] = int(value)
            except ValueError:
                try:
                    result[key] = float(value)
                except ValueError:
                    result[key] = value
    return result


def _axis(axis, size):
    wanted = {"id": "a0", "size": size, "role": "spectral", "domain": "time",
              "label": "1H", "nucleus": "1H", "unit": "s", "start": 0., "step": .0001284,
              "observation_mhz": 400.13311072, "reference_ppm": None, "reference_hz": 0.,
              "encoding": "complex", "time_origin_seconds": 0., "dwell_seconds": .0001284,
              "acquired_size": 32768, "transform_time_origin_seconds": None}
    return all(getattr(axis, key, object()) == value for key, value in wanted.items())


def numerical_checks(source, corrected, pairs, profile, raw_text, hashes, reference):
    import numpy as np
    expected = np.array(pairs, dtype=np.float64).T
    policy = np.array(integer_oracle(pairs), dtype=np.float64).T
    def numeric(actual, wanted, exact=False):
        shape_ok = actual.shape == wanted.shape and actual.dtype == np.float64
        finite = bool(np.isfinite(actual).all())
        error = float(np.max(np.abs(actual - wanted))) if shape_ok and finite else None
        passed = shape_ok and finite and (np.array_equal(actual, wanted) if exact else np.allclose(actual, wanted, rtol=RTOL, atol=ATOL))
        return bool(passed), {"shape": list(actual.shape), "dtype": str(actual.dtype), "maximum_absolute_error": error}
    checks = []
    passed, evidence = numeric(source.data, expected, exact=True)
    checks.append({"id": "ASU-02", "pass": passed, **evidence, "pairs_checked": len(pairs)})
    retained = {k: v for k, v in profile.items() if k != "PARMODE"}
    imported = {"format": "bruker-raw-1d", "raw_acqus_text": raw_text,
                "acquisition_parameters": retained, "source_sha256": hashes,
                "padding_complex_points": 0, "digital_filter": {"state": "uncorrected_or_unknown",
                "GRPDLY": 76, "DECIM": 2568, "DSPFVS": 21}}
    axes_ok = len(source.axes) == 1 and _axis(source.axes[0], 32768)
    coordinates = np.arange(32768, dtype=np.float64) * .0001284
    grid_ok = axes_ok and np.array_equal(source.coordinates("a0"), coordinates)
    metadata_ok = source.metadata == {"import": imported}
    checks.append({"id": "ASU-03", "pass": bool(axes_ok and grid_ok and metadata_ok and source.complex_axes == ("a0",)),
                   "axis_ok": axes_ok, "grid_ok": grid_ok, "metadata_ok": metadata_ok})
    spec = {"algorithm": "bruker-grpdly-fourier-advance-fold-v1", "version": "1", "axis": "a0",
            "delay_source": "acqus.GRPDLY", "group_delay_complex_points": 76., "fractional_delay": True,
            "phase_bins": "unsigned DFT k=0..N-1; exp(+2*pi*i*delay*k/N)",
            "trim_complex_points": 78, "fold_complex_points": 72, "first_point_scale": 1.,
            "input_complex_points": 32768, "output_complex_points": 32690,
            "output_time_origin_seconds": 0., "reference": "nmrglue 0.12 rm_dig_filter(truncate_grpdly=False) policy"}
    corrected_import = copy.deepcopy(imported)
    corrected_import["digital_filter"].update(state="corrected", correction=spec)
    structural = (corrected.data.shape == (2, 32690) and corrected.data.dtype == np.float64
                  and np.isfinite(corrected.data).all() and len(corrected.axes) == 1
                  and _axis(corrected.axes[0], 32690) and corrected.complex_axes == ("a0",)
                  and corrected.metadata == {"import": corrected_import}
                  and np.array_equal(corrected.coordinates("a0"), coordinates[:32690]))
    history = getattr(corrected, "provenance", [])
    history_ok = (len(history) == 1 and history[0].get("operation") == {**RECIPE[0], "version": "1"}
                  and history[0].get("backend", {}).get("name") == "native"
                  and history[0].get("backend", {}).get("threads") == 1
                  and history[0].get("digital_filter") == spec)
    checks.append({"id": "ASU-04", "pass": bool(structural and history_ok), "structure_ok": bool(structural),
                   "history_ok": history_ok, "trim": 78, "fold": 72, "output_points": 32690})
    observed = corrected.data[0] + 1j * corrected.data[1] if corrected.data.ndim == 2 and corrected.data.shape[0] == 2 else np.array([])
    for name, wanted in (("ASU-05", reference), ("ASU-06", policy[0] + 1j * policy[1])):
        valid = observed.shape == wanted.shape and np.isfinite(observed).all() and np.isfinite(wanted).all()
        error = float(np.max(abs(observed - wanted))) if valid else None
        checks.append({"id": name, "pass": bool(valid and np.allclose(observed, wanted, rtol=RTOL, atol=ATOL)),
                       "maximum_absolute_error": error, "rtol": RTOL, "atol": ATOL,
                       "points_checked": int(observed.size) if valid else 0})
    return checks


def capture_runtime(source_paths):
    import nmrnext
    import nmrnext._native
    import nmrglue
    prefix = Path(sys.prefix).resolve()
    if not Path(nmrnext.__file__).resolve().is_relative_to(prefix):
        raise ValueError("Acquired runtime must import an installed package under sys.prefix")
    versions = {name: importlib.metadata.version(name) for name in ("spinflow", "numpy", "scipy", "h5py", "nmrglue")}
    if versions["spinflow"] != "0.3.0b3" or nmrnext.__version__ != "0.3.0b3" or versions["nmrglue"] != "0.12" or nmrglue.__version__ != "0.12":
        raise ValueError("Installed candidate/nmrglue version mismatch")
    paths = set()
    for dist_name, module_name in (("spinflow", "nmrnext"), ("numpy", "numpy"), ("scipy", "scipy"), ("h5py", "h5py"), ("nmrglue", "nmrglue")):
        root = Path(importlib.import_module(module_name).__file__).resolve().parent
        paths.update(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
        distribution = importlib.metadata.distribution(dist_name)
        if distribution.files is None:
            raise ValueError("Installed distribution has no file inventory")
        for item in distribution.files:
            path = Path(distribution.locate_file(item))
            if "__pycache__" not in path.parts and path.suffix != ".pyc" and path.is_file():
                paths.add(path)
    files = {}
    for path in sorted(paths, key=str):
        if path.is_symlink() or not path.resolve().is_relative_to(prefix):
            raise ValueError(f"Runtime file outside installed prefix or symlink: {path}")
        relative = str(path.resolve().relative_to(prefix))
        files[relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    return {"schema": 1, "backend": "native", "threads": 1, "versions": versions,
            "python": sys.version, "platform": platform.platform(), "prefix": str(prefix),
            "executable": str(Path(sys.executable).resolve()), "executable_sha256": sha256(Path(sys.executable).resolve()),
            "source_bindings": bindings(source_paths), "files": files}


def verify_data(data_directory, expected):
    directory = Path(data_directory)
    if directory.is_symlink() or not directory.is_dir() or {p.name for p in directory.iterdir()} != {"acqus", "fid"}:
        raise ValueError("Prepared input directory must contain exactly acqus and fid")
    require_bindings(expected, {name: directory / name for name in ("acqus", "fid")})


def extract_hash_only(archive_bytes, directory):
    """Called only after archive verification and separate preparation GO."""
    directory = Path(directory)
    directory.mkdir(exist_ok=False)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for name in ("acqus", "fid"):
            info = archive.getinfo(PREFIX + name)
            if info.file_size != (8861 if name == "acqus" else 262144):
                raise ValueError("Allowlisted member size mismatch")
            value = archive.read(info)  # CRC validated by zipfile; no numeric decode.
            if zlib.crc32(value) != info.CRC:
                raise ValueError("Allowlisted member CRC mismatch")
            with (directory / name).open("xb") as stream:
                stream.write(value)
    return bindings({name: directory / name for name in ("acqus", "fid")})


def perform(args, out):
    case = load_json(args.case)
    validate_case(case)
    source_paths = {"tool": Path(__file__), "protocol": args.protocol, "case": args.case}
    if args.action == "runtime":
        runtime = capture_runtime(source_paths)
        write_json_new(out / "runtime-lock.json", runtime)
        return {"pass": True, "status": "RUNTIME_ONLY", "runtime_sha256": sha256(out / "runtime-lock.json")}
    if args.action == "download":
        request = urllib.request.Request(URL, headers={"User-Agent": "SpinFlow-public-calibration-download"})
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            if response.status != 200 or response.url != URL:
                raise ValueError("Unexpected response or redirect")
            data = response.read(1060856)
        if len(data) != 1060855 or hashlib.sha256(data).hexdigest() != ARCHIVE_SHA or hashlib.sha1(b"blob 1060855\0" + data).hexdigest() != BLOB:
            raise ValueError("Downloaded source identity mismatch")
        archive_path = out / "source.zip"
        with archive_path.open("xb") as stream:
            stream.write(data)
        read_archive(archive_path, case)
        return {"pass": True, "status": "DOWNLOAD_ONLY", "archive_sha256": ARCHIVE_SHA,
                "archive_bytes": len(data), "git_blob_sha1": BLOB,
                "numeric_decoded": False, "members_extracted": False, "source_bindings": bindings(source_paths)}
    if args.archive is None:
        raise ValueError("This stage requires --archive")
    archive_bytes = read_archive(args.archive, case)
    if args.action == "prepare":
        directory = out / "data"
        inputs = extract_hash_only(archive_bytes, directory)
        lock = {"schema": 1, "status": "PREPARED_HASH_ONLY_AWAITING_ROOT_GO", "case_id": case["id"],
                "source_bindings": bindings(source_paths), "archive_sha256": ARCHIVE_SHA, "data_files": inputs,
                "required_checks": list(REQUIRED), "recipe": RECIPE, "backend": "native", "threads": 1,
                "filter_rtol": RTOL, "filter_atol": ATOL, "profile": PROFILE, "numeric_decoded": False}
        write_json_new(out / "prepared-lock.json", lock)
        return {"pass": True, "status": "PREPARED_HASH_ONLY", "prepared_sha256": sha256(out / "prepared-lock.json"),
                "data_files": inputs, "numeric_decoded": False}
    prepared = load_locked(args.prepared_lock, args.prepared_sha256)
    runtime = load_locked(args.runtime_lock, args.runtime_sha256)
    required_lock = {"schema": 1, "status": "PREPARED_HASH_ONLY_AWAITING_ROOT_GO", "case_id": case["id"],
                     "archive_sha256": ARCHIVE_SHA, "required_checks": list(REQUIRED), "recipe": RECIPE,
                     "backend": "native", "threads": 1, "filter_rtol": RTOL, "filter_atol": ATOL,
                     "profile": PROFILE, "numeric_decoded": False}
    if any(prepared.get(key) != value for key, value in required_lock.items()):
        raise ValueError("Prepared lock incomplete or changed")
    require_bindings(prepared["source_bindings"], source_paths)
    require_runtime(runtime, capture_runtime(source_paths))
    verify_data(args.data, prepared["data_files"])
    initial_locks = bindings({"prepared": args.prepared_lock, "runtime": args.runtime_lock})
    # This line is the first acquired numeric decoding boundary, after all locks.
    pairs = literal_pairs((args.data / "fid").read_bytes())
    # Preserve original CRLF bytes in the retained acquisition text.
    raw_text = (args.data / "acqus").read_bytes().decode("latin-1")
    header = scalars(raw_text)
    if any(header.get(key) != value for key, value in PROFILE.items()):
        raise ValueError("Acquired scalar profile mismatch")
    import numpy as np
    import nmrglue as ng
    from nmrnext.io import read_dataset
    from nmrnext.operations import run_operations
    source = read_dataset(args.data, format="bruker-raw-1d")
    snapshot = (source.data.copy(), source.axes, source.complex_axes, source.metadata, source.provenance)
    corrected = run_operations(source, RECIPE, backend="native", threads=1)
    expected = np.array(pairs, dtype=np.float64).T
    reference = ng.bruker.rm_dig_filter(expected[0] + 1j * expected[1], 2568, 21, 76, truncate_grpdly=False)
    hashes = {name: record["sha256"] for name, record in prepared["data_files"].items()}
    checks = numerical_checks(source, corrected, pairs, PROFILE, raw_text, hashes, reference)
    unchanged = (np.array_equal(source.data, snapshot[0]) and source.axes == snapshot[1]
                 and source.complex_axes == snapshot[2] and source.metadata == snapshot[3]
                 and source.provenance == snapshot[4])
    verify_data(args.data, prepared["data_files"])
    read_archive(args.archive, case)
    require_bindings(prepared["source_bindings"], source_paths)
    require_bindings(initial_locks, {"prepared": args.prepared_lock, "runtime": args.runtime_lock})
    require_runtime(runtime, capture_runtime(source_paths))
    checks.insert(0, {"id": "ASU-01", "pass": bool(unchanged), "all_hashes_reverified": True,
                      "input_dataset_unchanged": bool(unchanged)})
    ids_complete = len(checks) == len(REQUIRED) and {c.get("id") for c in checks} == set(REQUIRED)
    return {"pass": complete_pass(checks), "status": "COMPLETE", "checks_complete": ids_complete, "checks": checks,
            "prepared_sha256": args.prepared_sha256, "runtime_sha256": args.runtime_sha256,
            "source_bindings": bindings(source_paths), "backend": "native", "threads": 1,
            "numeric_decoded": True, "unverified": UNVERIFIED}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("download", "prepare", "runtime", "run"))
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New receipt directory; never overwritten")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--prepared-lock", type=Path)
    parser.add_argument("--prepared-sha256")
    parser.add_argument("--runtime-lock", type=Path)
    parser.add_argument("--runtime-sha256")
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    stdout, stderr = io.StringIO(), io.StringIO()
    code = 1
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            result = perform(args, args.output)
            code = 0 if result["pass"] is True else 1
            print(json.dumps(result, indent=2, allow_nan=False))
        except Exception as exc:
            traceback.print_exc()
            result = {"pass": False, "status": "ERROR", "checks_complete": False,
                      "error_type": type(exc).__name__, "error": str(exc),
                      "required_checks": list(REQUIRED), "unverified": UNVERIFIED}
    receipt = {"schema": 1, "action": args.action, "exit_code": code, **result}
    write_json_new(args.output / "receipt.json", receipt)
    for name, content in (("stdout.txt", stdout.getvalue()), ("stderr.txt", stderr.getvalue()), ("exit-code.txt", f"{code}\n")):
        with (args.output / name).open("x", encoding="utf-8") as stream:
            stream.write(content)
    print(json.dumps(receipt, indent=2, allow_nan=False))
    if stderr.getvalue():
        print(stderr.getvalue(), file=sys.stderr, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
