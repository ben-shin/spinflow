"""UNEXECUTED exact check of an already-created CLI output; no processing."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import traceback


def identity(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Regular existing file required: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--applied", type=Path, required=True)
    parser.add_argument("--cli-result", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.receipt.exists() or args.receipt.is_symlink():
        raise FileExistsError("Exact-check receipt must be new; preserve previous evidence")
    record = {"schema": 1, "kind": "exact-cli-replay-check", "pass": False,
              "checks_complete": False, "applied": str(args.applied),
              "cli_result": str(args.cli_result), "processing_performed": False,
              "phase_reestimated": False, "scientific_acceptance": False}
    try:
        if sys.flags.isolated != 1 or not sys.dont_write_bytecode:
            raise ValueError("Invoke with installed interpreter -I -B")
        import numpy as np
        import nmrnext
        import nmrnext.analysis
        import nmrnext.io
        from nmrnext.analysis import dataset_digest
        from nmrnext.io import read_dataset
        prefix = Path(sys.prefix).resolve()
        modules = (np, nmrnext, nmrnext.analysis, nmrnext.io)
        origins = {}
        for module in modules:
            path = Path(module.__file__).resolve()
            if not path.is_relative_to(prefix):
                raise ValueError(f"Module outside installed prefix: {module.__name__}")
            origins[module.__name__] = identity(path)
        if nmrnext.__version__ != "0.3.0b3" or importlib.metadata.version("spinflow") != "0.3.0b3":
            raise ValueError("Expected installed public b3")
        record["runtime"] = {"prefix": str(prefix), "python": sys.executable,
            "python_version": sys.version, "isolated": True, "bytecode_disabled": True,
            "versions": {name: importlib.metadata.version(name) for name in
                         ("spinflow", "numpy", "scipy", "h5py", "nmrglue")},
            "module_origins": origins}
        before = {"applied": identity(args.applied), "cli_result": identity(args.cli_result)}
        # These are existing native numeric files. No processing/replay is called.
        applied = read_dataset(args.applied, max_bytes=67108864)
        actual_cli = read_dataset(args.cli_result, max_bytes=67108864)
        expected_digest, observed_digest = dataset_digest(applied), dataset_digest(actual_cli)
        expected_axes = json.dumps([a.to_dict() for a in applied.axes], sort_keys=True, separators=(",", ":"), allow_nan=False)
        actual_axes = json.dumps([a.to_dict() for a in actual_cli.axes], sort_keys=True, separators=(",", ":"), allow_nan=False)
        checks = {
            "dtype": actual_cli.data.dtype == applied.data.dtype == np.dtype("float64"),
            "shape": actual_cli.data.shape == applied.data.shape == (2, 65536),
            "quadratures": actual_cli.complex_axes == applied.complex_axes == ("a0",),
            "samples": bool(np.array_equal(actual_cli.data, applied.data)),
            "dataset_digest": observed_digest == expected_digest,
            "serialized_axes": actual_axes == expected_axes,
            "metadata": actual_cli.metadata == applied.metadata,
            "provenance": actual_cli.provenance == applied.provenance,
            "complete_metadata": actual_cli.to_metadata() == applied.to_metadata(),
            "input_files_unchanged": before == {"applied": identity(args.applied), "cli_result": identity(args.cli_result)},
            "installed_origins_unchanged": origins == {m.__name__: identity(Path(m.__file__).resolve()) for m in modules},
        }
        record.update(checks=checks, checks_complete=True,
                      inputs=before, expected_digest=expected_digest, actual_cli_digest=observed_digest,
                      dtype=str(actual_cli.data.dtype), shape=list(actual_cli.data.shape),
                      quadratures=list(actual_cli.complex_axes))
        record["pass"] = all(type(value) is bool and value for value in checks.values())
    except Exception as exc:
        traceback.print_exc()
        record.update(error_type=type(exc).__name__, error=str(exc), checks_complete=False)
        record["pass"] = False
    code = 0 if record["pass"] is True and record["checks_complete"] is True else 1
    record["exit_code"] = code
    with args.receipt.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(record, indent=2, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
