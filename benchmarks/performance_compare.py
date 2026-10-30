"""Preregistered Linux, one-CPU installed-package performance comparison.

Cold means a fresh CLI process, not a flushed filesystem cache. Warm timings use
two already running, separately installed workers. JSON preserves every warmup
and observation. No outliers are dropped. Profile/instrumentation runs are kept
separate. This bounded 2D acquired example makes no large 3D/4D scaling claim.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import time


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""): h.update(block)
    return h.hexdigest()


def rec(path):
    p = Path(path).resolve()
    return {"path": str(p), "bytes": p.stat().st_size, "sha256": sha(p)}


def summary(values):
    quartiles = statistics.quantiles(values, n=4, method="inclusive") if len(values) > 1 else values * 3
    return {"n": len(values), "median": statistics.median(values),
            "minimum": min(values), "maximum": max(values), "mean": statistics.mean(values),
            "q1": quartiles[0], "q3": quartiles[2], "iqr": quartiles[2] - quartiles[0],
            "quartile_definition": "statistics.quantiles(n=4, method='inclusive'): linear interpolation on sorted observations",
            "sample_stdev": statistics.stdev(values) if len(values) > 1 else 0,
            "all_observations": values}


def identity(path, ucsf):
    data = Path(path).read_bytes()
    header = 436 if ucsf else 2048
    ranges = [(23, 49)] if ucsf else [(4*i, 4*i+4) for i in (283, 284, 285, 294, 295, 296)]
    if ucsf: assert data.startswith(b"UCSF NMR") and data[10] == 2
    assert len(data) >= header
    canonical = bytearray(data)
    excluded = []
    for start, stop in ranges:
        excluded.append({"start": start, "stop_exclusive": stop, "raw_hex": data[start:stop].hex()})
        canonical[start:stop] = b"\0" * (stop - start)
    return {"raw": rec(path), "header_bytes": header,
            "payload_sha256": hashlib.sha256(data[header:]).hexdigest(),
            "canonical_excluding_only_creation_timestamp_sha256": hashlib.sha256(canonical).hexdigest(),
            "excluded_timestamp_bytes": excluded}


def env():
    result = os.environ.copy()
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        result[key] = "1"
    result["PYTHONHASHSEED"] = "0"
    result.pop("PYTHONPATH", None)
    return result


class Worker:
    def __init__(self, python, args, logdir, cache):
        self.log = (logdir / "stderr.log").open("xb")
        self.argv = [python, "-I", str(args.worker), "--raw", str(args.raw),
                     "--recipe", str(args.recipe), "--export-source", str(args.export_source)]
        if cache: self.argv.append("--interactive-cache")
        started = time.perf_counter()
        self.started = started
        self.p = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=self.log, text=True, env=env(), cwd=logdir)
        line = self.p.stdout.readline()
        if not line: raise RuntimeError(f"worker failed to start; inspect {logdir}")
        self.ready = json.loads(line)
        self.ready["ready_wall_seconds"] = time.perf_counter() - started
        self.ready["argv"] = self.argv
        assert self.ready["ready"]

    def request(self, message):
        started = time.perf_counter()
        self.p.stdin.write(json.dumps(message) + "\n"); self.p.stdin.flush()
        line = self.p.stdout.readline()
        if not line: raise RuntimeError("worker closed unexpectedly")
        result = json.loads(line)
        if not result["ok"]: raise RuntimeError(json.dumps(result))
        result["result"]["complete_request_wall_seconds"] = time.perf_counter() - started
        result["result"]["request_wall_scope"] = "parent request serialization/write through response read/decode, including API, product work, harness identity hash and transport"
        return result["result"]

    def close(self):
        if self.p.poll() is None:
            self.p.stdin.write('{"quit":true}\n'); self.p.stdin.flush()
        self.p.wait(timeout=30); self.log.close()
        assert self.p.returncode == 0
        self.ready["worker_lifetime_wall_seconds"] = time.perf_counter() - self.started
        self.ready["lifetime_wall_scope"] = "spawn through worker exit, including imports, preload, all requests, harness hashing, waits between interleaved requests and shutdown"


def cold(argv, work):
    # wait4 gives the actual just-waited process's Linux ru_maxrss, rather than
    # RUSAGE_CHILDREN's lifetime maximum across earlier jobs.
    with (work / "stdout.json").open("xb") as stdout, (work / "stderr.log").open("xb") as stderr:
        started = time.perf_counter()
        p = subprocess.Popen(argv, cwd=work, env=env(), stdout=stdout, stderr=stderr)
        _, status, usage = os.wait4(p.pid, 0)
        elapsed = time.perf_counter() - started
        p.returncode = os.waitstatus_to_exitcode(status)
    assert p.returncode == 0, (argv, work, p.returncode)
    return {"argv": argv, "cwd": str(work), "elapsed_seconds": elapsed,
            "cpu_seconds": usage.ru_utime + usage.ru_stime,
            "process_lifetime_peak_rss_bytes": int(usage.ru_maxrss) * 1024,
            "rss_scope": "Linux wait4 ru_maxrss of this fresh CLI process, including startup/import/read/process/export/provenance; not a summed process-tree peak",
            "cli_report": json.loads((work / "stdout.json").read_text())}


def paired_summary(jobs, task):
    rows = [j for j in jobs if j["task"] == task and j["kind"] == "measured"]
    answer = {"lanes": {}}
    for lane in ("baseline", "candidate"):
        own = [r["result"] for r in rows if r["lane"] == lane]
        answer["lanes"][lane] = {key: summary([r[key] for r in own])
            for key in ("elapsed_seconds", "cpu_seconds")}
        if not task.startswith("cold_"):
            answer["lanes"][lane]["complete_request_wall_seconds"] = summary([r["complete_request_wall_seconds"] for r in own])
        key = "process_lifetime_peak_rss_bytes" if task.startswith("cold_") else "worker_lifetime_peak_rss_bytes"
        answer["lanes"][lane][key] = summary([r[key] for r in own])
    ratios = []
    for pair in sorted({r["pair"] for r in rows}):
        own = {r["lane"]: r["result"] for r in rows if r["pair"] == pair}
        ratios.append({"pair": pair,
            "candidate_over_baseline_elapsed": own["candidate"]["elapsed_seconds"] / own["baseline"]["elapsed_seconds"],
            "candidate_over_baseline_cpu": own["candidate"]["cpu_seconds"] / own["baseline"]["cpu_seconds"]})
    answer["paired_ratios"] = ratios
    answer["candidate_over_baseline_elapsed"] = summary([r["candidate_over_baseline_elapsed"] for r in ratios])
    return answer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("baseline-python", "candidate-python", "raw", "recipe", "export-source", "output"):
        parser.add_argument("--" + arg, required=True, type=Path)
    parser.add_argument("--worker", type=Path, default=Path(__file__).with_name("performance_worker.py"))
    parser.add_argument("--pairs", type=int, default=7)
    parser.add_argument("--candidate-cache", action="store_true")
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    assert args.pairs >= 7, "At least seven measured pairs are required"
    assert os.sched_getaffinity(0) == {0} and os.cpu_count() == 1, "Use the pinned single-CPU guest"
    for name in ("raw", "recipe", "export_source", "worker", "output"):
        setattr(args, name, getattr(args, name).resolve())
    args.output.mkdir()
    recipe = json.loads(args.recipe.read_text())
    python = {"baseline": str(args.baseline_python), "candidate": str(args.candidate_python)}
    runtime_before = {}
    for lane in python:
        work = args.output / f"preflight-{lane}"; work.mkdir()
        probe = Worker(python[lane], args, work, False)
        runtime_before[lane] = probe.ready
        probe.close()
    tasks = args.only or ["cold_pipe", "cold_ucsf", "warm_recipe", "vendor_import", "dataset_digest", "export_pipe",
                         "export_ucsf", "interactive_unchanged", "interactive_changed"]
    pin = {"schema_version": 1, "status": "FROZEN_BEFORE_TIMING", "utc": datetime.now(timezone.utc).isoformat(),
           "platform": platform.platform(), "cpu_affinity": sorted(os.sched_getaffinity(0)),
           "cpu_count": os.cpu_count(), "cpuinfo": Path("/proc/cpuinfo").read_text(),
           "controller": rec(__file__), "worker": rec(args.worker), "recipe": rec(args.recipe),
           "raw": [rec(args.raw / name) for name in ("fid", "procpar")],
           "export_source": rec(args.export_source), "pairs": args.pairs,
           "tasks": tasks, "candidate_cache": args.candidate_cache,
           "runtimes_before": runtime_before,
           "memory_before": Path("/proc/meminfo").read_text(), "swaps_before": Path("/proc/swaps").read_text(),
           "warmups_per_task_lane": 1, "pair_order": ["BC" if i % 2 == 0 else "CB" for i in range(args.pairs)],
           "cache": "OS file cache uncontrolled; no purge, fsync, outlier exclusion or overhead subtraction",
           "interactive": "Fixed original first12 operations ending with indirect phase before final DI/TP; prime0degrees; changed warmup0.5degrees then manual1..7degrees; unchanged0degrees.128MiB retained-cache budget,1GiB total policy.",
           "startup_scope": "Cold fresh process includes startup through normal final output close plus CLI reporting. Warm API elapsed excludes worker imports, preload, harness result hash and pipe transport; normal product validation/provenance/output SHA remain included."}
    (args.output / "execution-lock.json").write_text(json.dumps(pin, indent=2) + "\n")
    jobs, runtimes = [], {}
    for task in tasks:
        workers = {}
        if not task.startswith("cold_"):
            for lane in python:
                work = args.output / f"{task}-{lane}"; work.mkdir()
                workers[lane] = Worker(python[lane], args, work, args.candidate_cache and lane == "candidate")
                runtimes[f"{task}-{lane}"] = workers[lane].ready
        try:
            sequence = [("warmup", 0, ["baseline", "candidate"])]
            sequence += [("measured", i, ["baseline", "candidate"] if i % 2 else ["candidate", "baseline"])
                         for i in range(1, args.pairs + 1)]
            if task.startswith("interactive_"):
                for lane, worker in workers.items():
                    priming = worker.request({"task": task, "phase_p0": 0.0})
                    jobs.append({"task": task, "kind": "priming", "pair": 0, "lane": lane, "result": priming})
            for kind, pair, order in sequence:
                for lane in order:
                    work = args.output / f"{task}-{kind}-{pair:02}-{lane}"; work.mkdir()
                    if task.startswith("cold_"):
                        staged = work / "agilent_2d"; staged.mkdir()
                        for name in ("fid", "procpar"):
                            shutil.copyfile(args.raw / name, staged / name); (staged / name).chmod(0o444)
                            assert sha(staged / name) == sha(args.raw / name)
                        r = dict(recipe, input=str(staged), output=str(work / "result"),
                                 output_format="ucsf-real" if task == "cold_ucsf" else "nmrpipe-real")
                        path = work / "recipe.json"; path.write_text(json.dumps(r, indent=2) + "\n")
                        result = cold([python[lane], "-I", "-m", "nmrnext", "run", str(path)], work)
                        result["output_identity"] = identity(work / "result", task == "cold_ucsf")
                    else:
                        message = {"task": task, "output": str(work / "result")}
                        if task.startswith("interactive_"):
                            message["phase_p0"] = (0.5 if kind == "warmup" else float(pair)) if task == "interactive_changed" else 0.0
                        result = workers[lane].request(message)
                        if task.startswith("export_"):
                            result["output_identity"] = identity(work / "result", task == "export_ucsf")
                    jobs.append({"task": task, "kind": kind, "pair": pair, "lane": lane, "result": result})
                    (args.output / "observations.json").write_text(json.dumps(jobs, indent=2) + "\n")
        finally:
            for worker in workers.values(): worker.close()
        print(json.dumps({"task_complete": task, "observations": len(jobs)}), flush=True)
    runtime_after = {}
    for lane, probe in runtime_before.items():
        files = probe["runtime"]["installed_package_files"] + probe["runtime"]["loaded_runtime_files"]
        for item in files:
            assert rec(item["path"]) == item, f"runtime changed during benchmark: {item['path']}"
        runtime_after[lane] = {"verified_unchanged_files": len(files)}
    report = {"status": "MEASURED_NO_OUTLIER_EXCLUSIONS", "execution_lock": rec(args.output / "execution-lock.json"),
              "runtimes": runtimes, "observations": jobs,
              "runtime_after": runtime_after, "memory_after": Path("/proc/meminfo").read_text(),
              "swaps_after": Path("/proc/swaps").read_text(),
              "summary": {task: paired_summary(jobs, task) for task in tasks},
              "raw_after": [rec(args.raw / name) for name in ("fid", "procpar")]}
    assert report["raw_after"] == pin["raw"]
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({task: report["summary"][task]["candidate_over_baseline_elapsed"] for task in tasks}, indent=2))


if __name__ == "__main__": main()
