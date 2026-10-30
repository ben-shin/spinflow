"""Separate single-process API profile; never mixed into primary CLI timings."""
import argparse
import cProfile
from collections import defaultdict
import json
from pathlib import Path
import resource
import time

import numpy as np


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--lane", choices=["eager", "blocks"], required=True)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--recipe", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--memory", type=int, required=True)
    args = p.parse_args()
    operations = json.loads(args.recipe.read_text())["operations"]
    counts = defaultdict(lambda: {"calls": 0, "input_bytes": 0})
    original_finite = np.isfinite
    def finite(values, *a, **kw):
        counts["numpy.isfinite"]["calls"] += 1
        counts["numpy.isfinite"]["input_bytes"] += getattr(values, "nbytes", 0)
        return original_finite(values, *a, **kw)
    np.isfinite = finite
    profiler = cProfile.Profile()
    started, cpu = time.perf_counter(), time.process_time()
    profiler.enable()
    if args.lane == "eager":
        from nmrnext.io import read_dataset, write_dataset
        from nmrnext.operations import run_operations
        data = read_dataset(args.input, format="native", max_bytes=args.memory)
        result = run_operations(data, operations, backend="native", threads=1, memory_bytes=args.memory)
        report = write_dataset(result, args.output, format="native")
    else:
        from nmrnext.blocks import process_to_file
        report = process_to_file(args.input, args.output, operations, backend="native", threads=1, memory_bytes=args.memory)
    profiler.disable()
    elapsed, elapsed_cpu = time.perf_counter() - started, time.process_time() - cpu
    # Capture memory BEFORE any profile formatting/report serialization. This is
    # one fresh process/API task, including imports and all normal product work.
    memory = {"ru_maxrss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
              "proc_status": {line.split(":", 1)[0]: line.split(":", 1)[1].strip()
                              for line in Path("/proc/self/status").read_text().splitlines()
                              if line.startswith(("VmRSS:", "VmHWM:", "RssAnon:", "RssFile:", "RssShmem:"))}}
    np.isfinite = original_finite
    profile_path = args.report.with_suffix(".prof")
    profiler.dump_stats(str(profile_path))
    stats = []
    for entry in profiler.getstats():
        code = entry.code
        label = str(code) if isinstance(code, str) else f"{code.co_filename}:{code.co_firstlineno}({code.co_name})"
        stats.append({"function": label, "calls": entry.callcount, "recursive_calls": entry.reccallcount,
                      "self_seconds": entry.inlinetime, "cumulative_seconds": entry.totaltime})
    output = {"scope": "Separate instrumented fresh singleAPI task, includes imports/read/normalproductwork/write/hash; no comparisonarrays; cProfile/finitewrapper overhead means not performancecohort latency",
              "lane": args.lane, "input": str(args.input), "recipe": str(args.recipe), "memory_policy_bytes": args.memory,
              "instrumented_api_wall_seconds": elapsed, "instrumented_api_cpu_seconds": elapsed_cpu,
              "memory_at_api_return": memory, "numeric_finite_scan_instrumentation": dict(counts),
              "product_report": report, "profile_path": str(profile_path),
              "profile_top_cumulative": sorted(stats, key=lambda row: row["cumulative_seconds"], reverse=True)[:60],
              "profile_top_self": sorted(stats, key=lambda row: row["self_seconds"], reverse=True)[:60]}
    with args.report.open("x") as stream:
        json.dump(output, stream, indent=2, allow_nan=False)
    print(json.dumps({"lane": args.lane, "instrumented_api_seconds": elapsed, "memory": memory,
                      "finite": dict(counts), "report": str(args.report)}))


if __name__ == "__main__":
    main()
