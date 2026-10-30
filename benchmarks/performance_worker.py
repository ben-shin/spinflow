"""Linux performance worker; run with an installed package, never source imports.

The line protocol keeps Python, dependencies and input data resident between
measurements. Operation elapsed times include the normal public API's validation
and provenance. RSS is the worker's lifetime high-water mark, not an operation
allocation. Instrumented/profile runs are separate from timing observations.
"""
from __future__ import annotations

import argparse
import cProfile
from collections import defaultdict
import ctypes
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import resource
import sys
import time
import traceback


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def record(path):
    path = Path(path).resolve()
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)}


def rss():
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def signature(dataset):
    h = hashlib.sha256()
    if dataset.data.flags.c_contiguous:
        h.update(memoryview(dataset.data).cast("B"))
    else:
        import numpy as np
        for chunk in np.nditer(dataset.data, flags=["external_loop", "buffered"],
                               op_flags=["readonly"], order="C", buffersize=262144):
            h.update(memoryview(chunk).cast("B"))
    return {"shape": list(dataset.data.shape), "dtype": str(dataset.data.dtype),
            "complex_axes": list(dataset.complex_axes), "data_sha256": h.hexdigest(),
            "metadata_sha256": hashlib.sha256(json.dumps(dataset.to_metadata(),
                sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()}


class Runtime:
    def __init__(self, args):
        import numpy as np
        import nmrnext
        import nmrnext._native as native
        from nmrnext.io import read_dataset, write_dataset
        from nmrnext.operations import run_operations
        self.np, self.read, self.write, self.run = np, read_dataset, write_dataset, run_operations
        self.args = args
        self.recipe = json.loads(Path(args.recipe).read_text())
        self.source = read_dataset(args.raw, format="varian-raw-2d", max_bytes=1024**3)
        self.export_source = read_dataset(args.export_source, format="native", max_bytes=1024**3)
        self.interactive = None
        self.collecting = False
        # This separate interactive prefix ends at indirect phase, before DI/TP.
        # Each measured change supplies a predeclared manual angle; no fit.
        last_phase = max(i for i, op in enumerate(self.recipe["operations"]) if op["op"] == "phase")
        self.interactive_operations = self.recipe["operations"][:last_phase + 1]
        package = Path(nmrnext.__file__).resolve().parent
        assert "site-packages" in str(package), package
        loaded = {}
        for line in Path("/proc/self/maps").read_text().splitlines():
            fields = line.split(maxsplit=5)
            if len(fields) == 6 and fields[5].startswith("/") and ".so" in fields[5]:
                path = Path(fields[5])
                if path.is_file(): loaded[str(path.resolve())] = record(path)
        pools = []
        for path in loaded:
            if "openblas" not in path.lower(): continue
            library = ctypes.CDLL(path)
            for symbol in ("scipy_openblas_get_num_threads64_", "scipy_openblas_get_num_threads_",
                           "scipy_openblas_get_num_threads", "openblas_get_num_threads"):
                try: getter = getattr(library, symbol)
                except AttributeError: continue
                getter.restype = ctypes.c_int
                pools.append({"library": path, "getter": symbol, "threads": getter()}); break
        self.pin = {"python": sys.version, "executable": str(Path(sys.executable).resolve()),
                    "python_binary": record(sys.executable),
                    "package": str(package), "native": record(native.__file__),
                    "native_backend": {"version": native.__version__, "pocketfft_commit": native.pocketfft_commit},
                    "installed_package_files": [record(p) for p in sorted(package.rglob("*"))
                       if p.is_file() and p.suffix in {".py", ".so"}],
                    "versions": {name: importlib.metadata.version(name)
                       for name in ("spinflow", "numpy", "scipy", "h5py", "nmrglue")},
                    "cpu_affinity": sorted(os.sched_getaffinity(0)), "cpu_count": os.cpu_count(),
                    "pool_environment": {k: os.environ.get(k) for k in
                       ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")},
                    "source_signature": signature(self.source),
                    "loaded_runtime_files": list(loaded.values()), "effective_openblas_pools": pools,
                    "export_source_signature": signature(self.export_source),
                    "rss_scope": "worker lifetime high-water RSS including imports, preload, prior operations and harness identity hashing; never per-operation RSS"}
        assert self.pin["cpu_affinity"] == [0] and os.cpu_count() == 1
        assert pools and all(pool["threads"] == 1 for pool in pools), pools

    def operation(self, task, output, phase_p0=None):
        if task == "vendor_import":
            return self.read(self.args.raw, format="varian-raw-2d", max_bytes=1024**3)
        if task == "warm_recipe":
            return self.run(self.source, self.recipe["operations"], backend="native", threads=1,
                            memory_bytes=1024**3)
        if task == "dataset_digest":
            from nmrnext.analysis import dataset_digest
            return {"dataset_digest": dataset_digest(self.export_source)}
        if task in {"export_pipe", "export_ucsf"}:
            return self.write(self.export_source, output,
                              format="nmrpipe-real" if task == "export_pipe" else "ucsf-real",
                              allow_losses=self.recipe["allow_losses"], overwrite=False)
        if task in {"interactive_unchanged", "interactive_changed"}:
            operations = json.loads(json.dumps(self.interactive_operations))
            if phase_p0 is not None:
                operations[-1]["p0"] = phase_p0
            if self.args.interactive_cache:
                if self.interactive is None:
                    from nmrnext._execution_cache import RecipeCache
                    self.interactive = RecipeCache(capacity_bytes=128 * 1024**2)
                result, _ = self.interactive.run(self.source, operations, backend="native", threads=1,
                                                memory_bytes=1024**3)
                return result
            return self.run(self.source, operations, backend="native", threads=1, memory_bytes=1024**3)
        raise ValueError(task)

    def measure(self, message):
        task = message["task"]
        output = Path(message["output"]) if "output" in message else None
        before_cpu = time.process_time()
        started = time.perf_counter()
        self.collecting = True
        try:
            result = self.operation(task, output, message.get("phase_p0"))
        finally:
            self.collecting = False
        elapsed, cpu = time.perf_counter() - started, time.process_time() - before_cpu
        high_water = rss()
        report = {"task": task, "elapsed_seconds": elapsed, "cpu_seconds": cpu,
                  "worker_lifetime_peak_rss_bytes": high_water, "rss_scope": self.pin["rss_scope"]}
        if self.interactive is not None:
            report["cache"] = {"retained_bytes": self.interactive.retained_bytes,
                "entry_count": self.interactive.entry_count,
                "last_reused_steps": self.interactive.last_reused_steps}
        # Hashes are outside measured API invocation. Export's own normal output SHA
        # remains inside the timed invocation and is present in api_result.
        if hasattr(result, "data"):
            report["result_signature"] = signature(result)
        else:
            report["api_result"] = result
            if output is not None and output.is_file():
                report["output"] = record(output)
        del result
        return report

    def instrumentation(self, message):
        import nmrnext.model as model
        np = self.np
        counts = defaultdict(lambda: {"calls": 0, "input_bytes": 0, "output_bytes": 0})
        originals = {}
        def wrap(name):
            original = getattr(np, name)
            originals[name] = original
            def counted(*args, **kwargs):
                out = original(*args, **kwargs)
                if self.collecting:
                    row = counts["numpy." + name]
                    row["calls"] += 1
                    row["input_bytes"] += int(getattr(args[0], "nbytes", 0)) if args else 0
                    row["output_bytes"] += int(getattr(out, "nbytes", 0))
                return out
            setattr(np, name, counted)
        for name in ("isfinite", "frombuffer", "array", "asarray", "empty", "empty_like", "zeros", "ascontiguousarray"):
            wrap(name)
        original_init = model.Dataset.__init__
        def init(dataset, data, *args, **kwargs):
            if self.collecting:
                row = counts["Dataset.public_constructor"]
                row["calls"] += 1; row["input_bytes"] += int(getattr(data, "nbytes", 0))
            return original_init(dataset, data, *args, **kwargs)
        model.Dataset.__init__ = init
        def profile(frame, event, arg):
            if self.collecting and event == "c_call" and getattr(arg, "__name__", "") in {"tobytes", "copy"}:
                owner = getattr(arg, "__self__", None)
                if isinstance(owner, np.ndarray):
                    row = counts["ndarray." + arg.__name__]
                    row["calls"] += 1; row["input_bytes"] += int(owner.nbytes)
        try:
            sys.setprofile(profile)
            report = self.measure(message)
        finally:
            sys.setprofile(None)
            model.Dataset.__init__ = original_init
            for name, original in originals.items(): setattr(np, name, original)
        return {"status": "INSTRUMENTED_NOT_TIMING_TRIAL", "counts": dict(counts), "observation": report,
                "scope": "Python-observable ndarray materializations, public Dataset boundaries and NumPy full-array finite scans inside the measured public API only. Native C++ internal allocation/copies are not directly counted. Harness result identity hashing is excluded."}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--raw", required=True); p.add_argument("--recipe", required=True)
    p.add_argument("--export-source", required=True); p.add_argument("--interactive-cache", action="store_true")
    p.add_argument("--profile-output")
    args = p.parse_args()
    runtime = Runtime(args)
    print(json.dumps({"ready": True, "runtime": runtime.pin}), flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get("quit"): break
            if request.get("instrument"):
                response = runtime.instrumentation(request)
            elif request.get("profile"):
                prof = cProfile.Profile(); prof.enable()
                response = runtime.measure(request)
                prof.disable(); prof.dump_stats(request["profile"])
                response["profile"] = record(request["profile"])
                response["status"] = "PROFILED_NOT_TIMING_TRIAL"
            else:
                response = runtime.measure(request)
            print(json.dumps({"ok": True, "result": response}), flush=True)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc), "traceback": traceback.format_exc()}), flush=True)


if __name__ == "__main__":
    main()
