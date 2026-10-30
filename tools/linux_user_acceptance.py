"""Local installed-Linux measurements and a deliberately unfilled human checklist.

The controller uses only the standard library. Product work runs in the selected
installation with -I; no downloads, script execution from data, or uploads occur.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
THREAD_ENV = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
RSS_SCOPE = ("Single fresh CLI child fork/spawn-to-exit lifetime high-water from wait4; "
             "may include the controller's inherited pre-exec resident floor. No subtraction, "
             "pure post-exec/incremental memory, OS cap, or flat-scaling claim. Kernel file "
             "page cache is outside process RSS. All comparisons run outside timing.")
MANUAL_CHECKS = (
    "Ubuntu 24.04 x86-64 physical/user hardware and actual operator identified",
    "Load the full acquired source; retain all 14 ordered processing operations",
    "Process and inspect the complete 2048x4096 result without implicit crop",
    "Pending edit stays distinct from Applied; export/save use the intended state",
    "Small supported automatic-phase result persists after explicit Process again",
    "Current calibrated ROI: parent-coordinate signed peaks and integrals agree",
    "Save a new figure; inspect labels, physical axes, contours and visible region",
    "Save session, close, reopen: applied recipe, analysis and source survive",
    "CLI replay and intended native/Pipe/UCSF export retain samples/axes/loss record",
    "Open exports in the actually intended independent application; record its version",
    "Cancel and malformed session leave the prior good state usable",
    "Record usability/latency problems and the intended workflow's acceptance decision",
)

# Only this constant is executed in the installed interpreter. No checkout imports.
WORKER = r'''
import hashlib, importlib.metadata as md, json, os, platform, sys
from pathlib import Path
import numpy as np
import nmrnext
from nmrnext import _native
from nmrnext.analysis import dataset_digest
from nmrnext.io import read_dataset
prefix=Path(sys.argv[1]).resolve()
assert Path(sys.prefix).resolve()==prefix, 'Wrong target prefix'
assert md.version('spinflow')==nmrnext.__version__==_native.__version__, 'Version mismatch'
def pin(path):
    path=Path(path).resolve()
    with path.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
    return {'path':str(path),'bytes':path.stat().st_size,'sha256':digest}
def check_imports():
    result=[]
    for name,mod in sorted(sys.modules.items()):
        if name=='nmrnext' or name.startswith('nmrnext.'):
            path=getattr(mod,'__file__',None)
            if path:
                assert Path(path).resolve().is_relative_to(prefix), 'Source-package leak: '+str(path)
                result.append({'module':name,**pin(path)})
    return result
def signature(d):
    return {'shape':list(d.data.shape),'dtype':str(d.data.dtype),
            'complex_axes':list(d.complex_axes),'numeric_digest':dataset_digest(d),
            'complete_metadata':d.to_metadata()}
mode=sys.argv[2]
if mode=='probe':
    deps={}
    for name in ('numpy','scipy','h5py','nmrglue','PySide6','pyqtgraph','matplotlib'):
        try: deps[name]=md.version(name)
        except md.PackageNotFoundError: deps[name]=None
    result={'version':nmrnext.__version__,'python':sys.version,'executable':sys.executable,
            'prefix':sys.prefix,'platform':platform.platform(),'machine':platform.machine(),
            'dependencies':deps,'native':pin(_native.__file__),
            'support_registry':pin(Path(nmrnext.__file__).parent/'support.json')}
    for name in ('cpuinfo','meminfo'):
        p=Path('/proc')/name
        result[name]=p.read_text() if p.exists() else None
elif mode=='source':
    task=json.loads(Path(sys.argv[3]).read_text())
    result=signature(read_dataset(task['input'],format=task.get('input_format'),
                                  max_bytes=task['memory_bytes']))
elif mode=='compare':
    paths=json.loads(Path(sys.argv[3]).read_text())
    first=read_dataset(paths[0],format='native')
    result={'reference':signature(first),'comparisons':[]}
    for path in paths:
        current=read_dataset(path,format='native')
        assert current.data.dtype==first.data.dtype, 'dtype changed'
        assert np.array_equal(current.data,first.data), 'Full-array/quadrature mismatch'
        assert current.to_metadata()==first.to_metadata(), 'Full metadata/provenance mismatch'
        result['comparisons'].append({'file':pin(path),'all_samples_and_complete_metadata_exact':True})
else: raise ValueError('Unknown worker endpoint')
result['installed_imports']=check_imports()
print(json.dumps(result,allow_nan=False))
'''


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def file_pin(path):
    path = Path(path)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest}


def input_snapshot(source):
    source = Path(source)
    if source.is_symlink():
        raise ValueError("Input symlink is unsupported")
    items = sorted(source.rglob("*")) if source.is_dir() else [source]
    records = []
    for item in items:
        if item.is_symlink():
            raise ValueError("Input tree contains a symlink")
        if item.is_file():
            records.append(file_pin(item))
        elif not item.is_dir():
            raise ValueError("Input contains a nonregular file")
    if not records:
        raise ValueError("Input has no regular files")
    return records


def unchanged_input(source, before):
    if input_snapshot(source) != before:
        raise ValueError("Input files changed; stop and retain this failed bundle")


def new_output(path, source):
    path, source = Path(path), Path(source).resolve()
    if path.exists() or path.is_symlink():
        raise ValueError("Output already exists; choose a new report directory")
    target = path.resolve()
    if target == source or source in target.parents or target in source.parents:
        raise ValueError("Output must be separate from input")
    target.mkdir()  # exclusive, existing parent required; no overwrite or cleanup
    return target


def load_task(path, override_input):
    path = Path(path).resolve(strict=True)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate recipe key")
            result[key] = value
        return result
    if path.stat().st_size > 1024**2:
        raise ValueError("Recipe exceeds 1 MiB")
    def finite_number(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("Recipe numbers must be finite")
        return result
    task = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique,
                      parse_float=finite_number,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Recipe numbers must be finite")))
    if not isinstance(task, dict) or task.get("schema_version") != "1":
        raise ValueError("Expected recipe schema 1 JSON")
    if type(task.get("threads", 1)) is not int or task.get("threads", 1) != 1:
        raise ValueError("Recipe threads must be 1; do not silently change the CPU budget")
    if type(task.get("memory_bytes")) is not int or task["memory_bytes"] <= 0:
        raise ValueError("Recipe must declare positive memory_bytes")
    if not isinstance(task.get("operations"), list):
        raise ValueError("Recipe operations must be a list")
    if not isinstance(task.get("input"), str) or not task["input"]:
        raise ValueError("Recipe input must be a nonempty path")
    original_source = Path(override_input) if override_input else path.parent / task["input"]
    if original_source.is_symlink():
        raise ValueError("Input symlink is unsupported")
    return task, original_source.resolve(strict=True)


def endpoint_recipe(task, source, output):
    # Native endpoint retains full metadata and provenance for exact replay checks.
    return dict(task, input=str(source), output=str(output), output_format="native",
                allow_losses=[], threads=1)


def distribution(values):
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return {"raw": list(values), "count": len(values), "min": min(values),
            "max": max(values), "median": statistics.median(values),
            "mean": statistics.mean(values), "q1": q1, "q3": q3, "iqr": q3-q1,
            "quartile_definition": "statistics.quantiles(n=4, method='inclusive')"}


def measure(argv, stem, cwd, env):
    stdout, stderr = Path(str(stem)+".stdout.json"), Path(str(stem)+".stderr.txt")
    floor = None
    if sys.platform.startswith("linux"):
        import resource
        floor = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    with stdout.open("xb") as out, stderr.open("xb") as err:
        start = time.perf_counter()
        child = subprocess.Popen(argv, cwd=cwd, env=env, stdout=out, stderr=err)
        if hasattr(os, "wait4"):
            _, status, usage = os.wait4(child.pid, 0)  # reap exactly this child once
            code = os.waitstatus_to_exitcode(status)
            child.returncode = code  # Popen must not reap it again
            rss = usage.ru_maxrss * (1024 if sys.platform.startswith("linux") else 1)
        else:
            code, rss = child.wait(), None
        elapsed = time.perf_counter()-start
    return {"argv": argv, "exit_code": code, "elapsed_seconds": elapsed,
            "peak_rss_bytes": rss, "controller_lifetime_peak_before_spawn_bytes": floor,
            "rss_scope": RSS_SCOPE if rss is not None else "UNAVAILABLE: no wait4",
            "stdout": file_pin(stdout), "stderr": file_pin(stderr)}


def require_success(observations, expected):
    if len(observations) != expected:
        raise ValueError("incomplete trials; no successful distribution")
    if any(item["exit_code"] != 0 for item in observations):
        raise ValueError("failed child; no successful distribution")


def run(args):
    if sys.platform != "linux" or platform.machine() not in ("x86_64", "amd64"):
        raise ValueError("This acceptance runner requires Linux x86-64")
    prefix = args.prefix.expanduser().resolve(strict=True)
    python = prefix / "bin/python"
    if not python.is_file():
        raise ValueError("Installed prefix/bin/python is missing")
    task, source = load_task(args.recipe, args.input)
    before = input_snapshot(source)  # warms filesystem cache, outside timing
    output = new_output(args.output.expanduser(), source)
    report = {"schema_version": "1", "utc": datetime.now(timezone.utc).isoformat(),
              "automated_status": "INCOMPLETE", "human_acceptance": "UNVERIFIED",
              "performance_budget": "NONE_PREDECLARED: descriptive measurements only",
              "controller": file_pin(__file__), "original_recipe": file_pin(args.recipe),
              "original_recipe_contents": task, "input_files_before": before,
              "samples_requested": args.samples, "observations": [], "rss_scope": RSS_SCOPE,
              "endpoint_scope": "Fresh installed CLI: startup, import, processing, normal hashes/provenance and native write/close. Comparison excluded. Input prehash warms filesystem cache; not cold I/O.",
              "native_endpoint_override": "Only input/output paths, threads=1, output_format=native and allow_losses=[] are bound; operations, padding, precision and memory policy remain as supplied."}
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.update({key: "1" for key in THREAD_ENV})
    try:
        allowed = sorted(os.sched_getaffinity(0))
        cpu = allowed[0] if args.cpu is None else args.cpu
        if cpu not in allowed:
            raise ValueError("Requested CPU is not in the allowed affinity set")
        os.sched_setaffinity(0, {cpu})  # process-local, inherited by children
        report["cpu"] = {"available_before": allowed, "selected": cpu,
                         "actual_affinity": sorted(os.sched_getaffinity(0))}
        report["thread_environment"] = {key: env[key] for key in THREAD_ENV}
        manual = {"operator": None, "hardware": None, "physical_linux_hardware": None,
                  "date": None, "checks": {text: None for text in MANUAL_CHECKS},
                  "decision": None, "notes": None}
        write_json(output / "manual-results.json", manual)
        with (output / "manual-checklist.md").open("x", encoding="utf-8", newline="\n") as stream:
            stream.write("# Manual acceptance — operator must fill this in\n\nOperator: ____  Hardware: ____  Date: ____\n\n")
            stream.writelines("- [ ] "+text+"\n" for text in MANUAL_CHECKS)
            stream.write("\nDecision and problems: ____\n\nNo automated result fills or approves this checklist.\n")
        def worker(mode, argument=None):
            argv = [str(python), "-I", "-B", "-c", WORKER, str(prefix), mode]
            if argument is not None:
                argv.append(str(argument))
            result = subprocess.run(argv, cwd=output, env=env, capture_output=True, text=True)
            (output / (mode+".stderr.txt")).write_text(result.stderr, encoding="utf-8")
            if result.returncode:
                raise ValueError("Installed "+mode+" check failed: "+result.stderr[-1000:])
            data = json.loads(result.stdout)
            write_json(output / (mode+".json"), data)
            return data
        report["installed"] = worker("probe")
        paths = []
        for index in range(args.samples):
            stem = output / f"trial-{index+1:02d}"
            target = Path(str(stem)+".nmrh5")
            current = endpoint_recipe(task, source, target)
            current_path = Path(str(stem)+".recipe.json")
            write_json(current_path, current)
            if index == 0:
                report["source"] = worker("source", current_path)
            unchanged_input(source, before)
            observed = measure([str(python), "-I", "-B", "-m", "nmrnext", "run", str(current_path)],
                               stem, output, env)
            report["observations"].append(observed)
            unchanged_input(source, before)
            require_success(report["observations"], index+1)
            if not target.is_file():
                raise ValueError("Successful child produced no native output")
            paths.append(str(target))
        require_success(report["observations"], args.samples)
        write_json(output / "outputs.json", paths)
        report["replay"] = worker("compare", output / "outputs.json")
        unchanged_input(source, before)
        report["input_files_after"] = input_snapshot(source)
        report["elapsed_seconds"] = distribution([o["elapsed_seconds"] for o in report["observations"]])
        rss = [o["peak_rss_bytes"] for o in report["observations"]]
        report["peak_rss_bytes"] = distribution(rss) if all(v is not None for v in rss) else None
        report["automated_status"] = "PASS_REPEATED_NATIVE_CLI_ONLY"
    except Exception as exc:
        report["automated_status"] = "FAILED_OR_INCOMPLETE"
        report["error"] = str(exc)
    write_json(output / "report.json", report)
    with (output / "report.md").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("# Installed Linux acceptance observations\n\n"+report["automated_status"]+"\n\n")
        stream.write("Human/physical-hardware acceptance: UNVERIFIED until the operator returns the manual results. No latency/RSS pass threshold was predeclared.\n\n")
        if "elapsed_seconds" in report:
            stats = report["elapsed_seconds"]
            stream.write(f"{stats['count']} mandatory fresh processes: median {stats['median']:.3f}s, range {stats['min']:.3f}–{stats['max']:.3f}s, IQR {stats['iqr']:.3f}s. All raw samples in report.json; none dropped.\n\n")
        stream.write(report["endpoint_scope"]+"\n\n"+RSS_SCOPE+"\n\nSee manual-checklist.md and manual-results.json. Nothing is uploaded automatically.\n")
    print(str(output / "report.json"))
    return 0 if report["automated_status"] == "PASS_REPEATED_NATIVE_CLI_ONLY" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True, help="Existing installed Linux prefix")
    parser.add_argument("--recipe", type=Path, help="Schema1 JSON recipe; its original output is never used")
    parser.add_argument("--input", type=Path, help="Override only the recipe input path")
    parser.add_argument("--output", type=Path, default=Path.home()/datetime.now(timezone.utc).strftime("spinflow-acceptance-%Y%m%dT%H%M%SZ"), help="New report directory, never overwritten")
    parser.add_argument("--samples", type=int, default=5, help="Mandatory fresh-process samples, 2–20 (default 5)")
    parser.add_argument("--cpu", type=int, help="One currently allowed logical CPU; default first available")
    args = parser.parse_args(argv)
    if not 2 <= args.samples <= 20:
        parser.error("--samples must be 2–20")
    if args.recipe is None:
        args.recipe = ROOT / "examples/laboratory-workflow.json"
        if args.input is None:
            args.input = ROOT / ".artifacts/coverage-formats/2d/agilent2pipe_2d/agilent_2d"
    try:
        return run(args)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
