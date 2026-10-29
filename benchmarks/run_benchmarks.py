"""Startup-inclusive native/NumPy processing benchmark with output checks.

Run from an editable project install. Only generated synthetic HDF5 fixtures
are used; this is not an NMRPipe comparison or a real acquisition benchmark.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import time
import uuid

import numpy as np

from nmrnext.demo import generate_demo
from nmrnext.io import read_dataset, write_dataset


ROOT = Path(__file__).resolve().parents[1]
TMP_ROOT = ROOT / ".tmp"
CHILD = ROOT / "benchmarks" / "bench_child.py"
RTOL_L2 = 1e-11
ATOL_MAX = 1e-9

WORKLOADS = {
    "small-1d": {
        "points": [1024], "output_points": [2048], "dtype": "float64",
        "operations": [
            {"op": "window", "axis": "t2", "kind": "exponential", "lb_hz": 3.0,
             "first_point_scale": 0.5},
            {"op": "zero_fill", "axis": "t2", "size": 2048},
            {"op": "fft", "axis": "t2"},
            {"op": "phase", "axis": "t2", "p0": 12.5, "p1": -20.0, "pivot": 200},
        ],
    },
    "routine-2d": {
        "points": [128, 512], "output_points": [256, 1024], "dtype": "float64",
        "operations": [
            {"op": "window", "axis": "t2", "kind": "exponential", "lb_hz": 4.0},
            {"op": "zero_fill", "axis": "t2", "size": 1024},
            {"op": "fft", "axis": "t2"},
            {"op": "phase", "axis": "t2", "p0": 15.0, "p1": -10.0, "pivot": 256},
            {"op": "window", "axis": "t1", "kind": "exponential", "lb_hz": 3.0},
            {"op": "zero_fill", "axis": "t1", "size": 256},
            {"op": "fft", "axis": "t1"},
            {"op": "phase", "axis": "t1", "p0": -7.0, "p1": 5.0, "pivot": 64},
        ],
    },
    "larger-2d": {
        "points": [256, 1024], "output_points": [512, 2048], "dtype": "float64",
        "operations": [
            {"op": "window", "axis": "t2", "kind": "exponential", "lb_hz": 4.0},
            {"op": "zero_fill", "axis": "t2", "size": 2048},
            {"op": "fft", "axis": "t2"},
            {"op": "phase", "axis": "t2", "p0": 15.0, "p1": -10.0, "pivot": 512},
            {"op": "window", "axis": "t1", "kind": "exponential", "lb_hz": 3.0},
            {"op": "zero_fill", "axis": "t1", "size": 512},
            {"op": "fft", "axis": "t1"},
            {"op": "phase", "axis": "t1", "p0": -7.0, "p1": 5.0, "pivot": 128},
        ],
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summary(values: list[float]) -> dict:
    return {"n": len(values), "median": statistics.median(values),
            "minimum": min(values), "maximum": max(values),
            "q1": percentile(values, 0.25), "q3": percentile(values, 0.75),
            "iqr": percentile(values, 0.75) - percentile(values, 0.25)}


def compare_output(result, reference) -> dict:
    if result.data.shape != reference.data.shape or result.data.dtype != reference.data.dtype:
        raise ValueError("output shape or dtype differs between benchmark runs")
    if result.complex_axes != reference.complex_axes or result.axes != reference.axes:
        raise ValueError("quadrature or axis metadata differs between benchmark runs")
    if result.metadata != reference.metadata:
        raise ValueError("dataset metadata differs between benchmark runs")
    left = result.data.astype(np.float64, copy=False)
    right = reference.data.astype(np.float64, copy=False)
    delta = left - right
    maximum = float(np.max(np.abs(delta)))
    norm = float(np.linalg.norm(right.ravel()))
    relative_l2 = float(np.linalg.norm(delta.ravel()) / max(norm, 1e-30))
    if maximum > ATOL_MAX or relative_l2 > RTOL_L2:
        raise ValueError(f"backend outputs differ: max_abs={maximum}, relative_l2={relative_l2}")
    return {"max_absolute_error": maximum, "relative_l2": relative_l2}


def run_child(recipe: Path, backend: str, threads: int, cwd: Path) -> dict:
    env = os.environ.copy()
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[key] = "1"
    env["PYTHONHASHSEED"] = "0"
    env.pop("PYTHONPATH", None)
    command = [sys.executable, "-I", str(CHILD), "run", str(recipe), backend, str(threads)]
    started = time.perf_counter_ns()
    completed = subprocess.run(command, cwd=cwd, env=env, text=True,
                               capture_output=True, timeout=180)
    elapsed = (time.perf_counter_ns() - started) / 1e9
    lines = completed.stderr.splitlines()
    metric_lines = [line.removeprefix("NMRNEXT_BENCH_METRIC=") for line in lines
                    if line.startswith("NMRNEXT_BENCH_METRIC=")]
    if completed.returncode or len(metric_lines) != 1:
        raise RuntimeError(f"{backend} run failed (exit {completed.returncode}): "
                           f"stdout={completed.stdout[-1500:]} stderr={completed.stderr[-1500:]}")
    report = json.loads(completed.stdout)
    metrics = json.loads(metric_lines[0])
    return {"backend": backend, "threads": threads, "wall_seconds": elapsed,
            "peak_rss_bytes": metrics["peak_rss_bytes"],
            "rss_source": metrics["rss_source"],
            "processing_and_write_seconds": report["elapsed_processing_and_write_seconds"],
            "estimated_working_bytes": report["estimated_working_bytes"],
            "output_format": report["result"].get("format")}


def system_ram_bytes() -> int | None:
    if sys.platform == "win32":
        import ctypes
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        value = MemoryStatusEx()
        value.dwLength = ctypes.sizeof(value)
        return int(value.ullTotalPhys) if ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(value)) else None
    try:
        return int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        return None


def environment() -> dict:
    from nmrnext import __version__
    from nmrnext import _native
    native_binary = Path(_native.__file__).resolve()
    zig = ROOT / ".tools" / "zig-x86_64-windows-0.17.0" / "zig.exe"
    tool_version = subprocess.run([str(zig), "version"], text=True, capture_output=True,
                                  timeout=10).stdout.strip() if zig.is_file() else None
    disk = shutil.disk_usage(ROOT)
    return {"utc": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
            "cpu": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER") or "unreported",
            "logical_cpu_count": os.cpu_count(), "physical_ram_bytes": system_ram_bytes(),
            "workspace_drive": ROOT.anchor, "workspace_disk_total_bytes": disk.total,
            "workspace_disk_free_bytes_at_start": disk.free, "storage_medium": "unreported",
            "python": sys.version, "python_executable": sys.executable,
            "nmrnext_version": __version__, "numpy_version": np.__version__,
            "h5py_version": importlib.metadata.version("h5py"),
            "nmrglue_version": importlib.metadata.version("nmrglue"),
            "native_module_version": _native.__version__,
            "pocketfft_commit": _native.pocketfft_commit,
            "pocketfft_header_sha256": sha256(ROOT / "cpp/vendor/pocketfft/pocketfft_hdronly.h"),
            "native_binary_sha256": sha256(native_binary),
            "runner_sha256": sha256(ROOT / "benchmarks/run_benchmarks.py"),
            "child_wrapper_sha256": sha256(CHILD),
            "source_sha256": current_source_hashes(),
            "compiler": {"local_zig_version": tool_version,
                         "windows_build_command_flags": "zig c++ -std=c++20 -shared -O2 (tools/build_native.py)"},
            "legacy_reference": {"nmrPipe_on_path": shutil.which("nmrPipe"),
                                 "status": "not measured"},
            "timing_scope": "parent wall-clock from child spawn to exit: Python startup, import, input read, full processing, HDF5 write, CLI JSON and RSS report",
            "rss_scope": "child process high-water RSS including Python imports; no process-tree aggregation",
            "cache_state": "uncontrolled; repeated runs may use OS file cache"}


def current_source_hashes() -> dict[str, str]:
    return {name: sha256(ROOT / "src/nmrnext" / name)
            for name in ("__init__.py", "errors.py", "cli.py", "operations.py",
                         "io/__init__.py", "demo.py", "model.py", "planning.py")}


def benchmark_one(name: str, config: dict, repetitions: int, native_threads: int,
                  directory: Path, created: list[Path]) -> dict:
    points = config["points"]
    dataset = generate_demo(dimensions=len(points), points=points, dtype=config["dtype"])
    input_path = directory / f"{name}-input.h5"
    write_dataset(dataset, input_path)
    created.append(input_path)
    input_sha = sha256(input_path)
    reference = None
    runs = []
    max_abs = 0.0
    max_l2 = 0.0
    for repetition in range(repetitions):
        order = ["native", "numpy"] if repetition % 2 == 0 else ["numpy", "native"]
        for backend in order:
            output = directory / f"{name}-{repetition}-{backend}.h5"
            recipe_path = directory / f"{name}-{repetition}-{backend}.json"
            recipe = {"schema_version": "1", "input": str(input_path), "output": str(output),
                      "backend": backend, "threads": native_threads if backend == "native" else 1,
                      "memory_bytes": 512 * 1024**2, "operations": config["operations"]}
            recipe_path.write_text(json.dumps(recipe, sort_keys=True), encoding="utf-8")
            created.extend([recipe_path, output])
            sample = run_child(recipe_path, backend, recipe["threads"], directory)
            sample["output_sha256"] = sha256(output)
            result = read_dataset(output)
            sample["output_shape"] = list(result.data.shape)
            sample["output_dtype"] = str(result.data.dtype)
            expected_shape = (2 ** len(points), *config["output_points"])
            if result.data.shape != expected_shape or str(result.data.dtype) != config["dtype"]:
                raise ValueError(f"unexpected processed shape/dtype for {name}: {result.data.shape}/{result.data.dtype}")
            if reference is None:
                reference = result
                difference = {"max_absolute_error": 0.0, "relative_l2": 0.0}
            else:
                difference = compare_output(result, reference)
            max_abs = max(max_abs, difference["max_absolute_error"])
            max_l2 = max(max_l2, difference["relative_l2"])
            sample.update({"repetition": repetition + 1, "order_position": order.index(backend) + 1,
                           "difference_to_first_output": difference})
            runs.append(sample)
            output.unlink()
            recipe_path.unlink()
    if sha256(input_path) != input_sha:
        raise ValueError("benchmark input changed during processing")
    input_path.unlink()
    by_backend = {}
    for backend in ("native", "numpy"):
        samples = [run for run in runs if run["backend"] == backend]
        by_backend[backend] = {
            "wall_seconds": summary([r["wall_seconds"] for r in samples]),
            "peak_rss_bytes": summary([r["peak_rss_bytes"] for r in samples])
                if all(r["peak_rss_bytes"] is not None for r in samples) else None,
            "processing_and_write_seconds": summary([r["processing_and_write_seconds"] for r in samples]),
        }
    return {"workload": name, "fixture": {"generator": "nmrnext.demo.v1", "points": points,
                                           "dtype": config["dtype"], "input_sha256": input_sha,
                                           "operations": config["operations"], "output_shape": expected_shape},
            "checks": {"same_input_after_runs": True, "same_axis_channels_dtype_metadata": True,
                       "max_absolute_error_limit": ATOL_MAX, "relative_l2_limit": RTOL_L2,
                       "observed_max_absolute_error": max_abs, "observed_max_relative_l2": max_l2},
            "runs": runs, "summary": by_backend,
            "median_native_over_numpy_wall_ratio": by_backend["native"]["wall_seconds"]["median"] /
                                                   by_backend["numpy"]["wall_seconds"]["median"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--workload", choices=["both", *WORKLOADS], default="both")
    parser.add_argument("--native-threads", type=int, choices=[1, 2], default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    TMP_ROOT.mkdir(exist_ok=True)
    if not TMP_ROOT.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError("benchmark temp root escaped workspace")
    result_dir = ROOT / "benchmarks" / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    output = (args.output or result_dir /
              f"benchmark-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}.json").resolve()
    if not output.is_relative_to(ROOT.resolve()):
        raise ValueError("result JSON must remain inside this workspace")
    if output.exists():
        raise FileExistsError(f"result JSON already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    run_dir = (TMP_ROOT / f"bench-{uuid.uuid4().hex}").resolve()
    if not run_dir.is_relative_to(TMP_ROOT.resolve()):
        raise RuntimeError("temporary run directory escaped benchmark root")
    run_dir.mkdir()
    created: list[Path] = []
    try:
        record = {"schema_version": "1", "environment": environment(),
                  "repetitions_per_backend_per_workload": args.repetitions,
                  "primary_thread_policy": "NumPy 1; native 1" if args.native_threads == 1
                                           else "NumPy 1; native 2 (separate optional profile)",
                  "workloads": []}
        selected = WORKLOADS.items() if args.workload == "both" else [(args.workload, WORKLOADS[args.workload])]
        for name, config in selected:
            record["workloads"].append(benchmark_one(name, config, args.repetitions,
                                                       args.native_threads, run_dir, created))
        initial = record["environment"]
        from nmrnext import _native
        if (initial["source_sha256"] != current_source_hashes() or
                initial["native_binary_sha256"] != sha256(Path(_native.__file__).resolve()) or
                initial["runner_sha256"] != sha256(ROOT / "benchmarks/run_benchmarks.py") or
                initial["child_wrapper_sha256"] != sha256(CHILD)):
            raise RuntimeError("production source, native binary, or benchmark code changed during the run; rerun against a stable revision")
        output.write_text(json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        for case in record["workloads"]:
            native = case["summary"]["native"]["wall_seconds"]["median"]
            numpy = case["summary"]["numpy"]["wall_seconds"]["median"]
            print(f"{case['workload']}: native {native:.4f}s, NumPy {numpy:.4f}s "
                  f"(median of {args.repetitions}, startup + load + processing + HDF5 write)")
        print(f"Results: {output}")
        return 0
    finally:
        for path in created:
            if path.exists():
                if path.resolve().parent != run_dir or not path.is_file():
                    raise RuntimeError(f"refusing to clean unexpected benchmark path: {path}")
                path.unlink()
        if run_dir.exists():
            run_dir.rmdir()  # only succeeds when the bounded directory is empty


if __name__ == "__main__":
    raise SystemExit(main())
