"""Fresh direct-installed-CLI paired benchmark with actual-child wait4 RSS.

All independent numeric/output comparisons run after both children exit. Worker
RSS never includes a comparison process, warm resident state or earlier trials.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 ** 2), b""):
            value.update(chunk)
    return value.hexdigest()


def summary(values):
    q = statistics.quantiles(values, n=4, method="inclusive") if len(values) > 1 else [values[0]] * 3
    return {"count": len(values), "minimum": min(values), "maximum": max(values),
            "median": statistics.median(values), "mean": statistics.mean(values),
            "stdev": statistics.stdev(values) if len(values) > 1 else 0.,
            "q1": q[0], "q3": q[2], "iqr": q[2] - q[0],
            "quartile_definition": "statistics.quantiles(n=4,method='inclusive')"}


def run_one(python, case, lane, index, kind, directory):
    prefix = directory / f"{case['name']}-{kind}-{index:02d}-{lane}"
    output = prefix.with_suffix(".nmrh5")
    original = json.loads(Path(case["recipe"]["path"]).read_text())
    recipe = {"schema_version": "1", "input": case["input"]["path"], "input_format": case.get("input_format", "native"),
              "output": str(output), "output_format": "native", "backend": "native", "threads": 1,
              "memory_bytes": case["eager_memory_bytes"], "operations": original["operations"]}
    recipe_path = prefix.with_suffix(".recipe.json")
    with recipe_path.open("x") as stream:
        json.dump(recipe, stream, indent=2)
    blocked = lane == "C" and case.get("comparison_mode") != "eager_regression"
    argv = [str(python), "-I", "-m", "nmrnext", "run", str(recipe_path),
            "--block-memory" if blocked else "--memory", str(case["block_memory_bytes"] if blocked else case["eager_memory_bytes"])]
    if shutil.disk_usage(directory).free < 6 * 1024 ** 3:
        raise RuntimeError("Below fixed6GiB remaining-disk safety reserve; preserving all trials and stopping")
    env = {**os.environ, **{key: "1" for key in ["OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"]}}
    started_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    started = time.perf_counter()
    with prefix.with_suffix(".stdout.json").open("xb") as stdout, prefix.with_suffix(".stderr.txt").open("xb") as stderr:
        process = subprocess.Popen(argv, stdout=stdout, stderr=stderr, env=env, cwd=directory)
        pid, status, usage = os.wait4(process.pid, 0)
        assert pid == process.pid
        process.returncode = os.waitstatus_to_exitcode(status)
    wall = time.perf_counter() - started
    report = {"case": case["name"], "lane": lane, "kind": kind, "index": index, "argv": argv,
              "recipe_sha256": sha(recipe_path), "start_utc": started_utc, "wall_seconds": wall,
              "peak_rss_bytes": int(usage.ru_maxrss) * 1024, "user_seconds": usage.ru_utime,
              "system_seconds": usage.ru_stime, "minor_faults": usage.ru_minflt, "major_faults": usage.ru_majflt,
              "input_blocks": usage.ru_inblock, "output_blocks": usage.ru_oublock,
              "returncode": process.returncode, "output": str(output),
              "rss_scope": "true Linuxwait4 highwater of this fresh directinstalledCLIchild,includingimports/normalplanning/inputfinitevalidation/numericalwork/productdigests/provenance/tempIO/finalclose/CLIreport; comparison processes excluded"}
    if process.returncode == 0:
        report["product_report"] = json.loads(prefix.with_suffix(".stdout.json").read_text())
    else:
        report["stdout"] = prefix.with_suffix(".stdout.json").read_text()
        report["stderr"] = prefix.with_suffix(".stderr.txt").read_text()
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--baseline-python", type=Path, required=True)
    p.add_argument("--candidate-python", type=Path)
    p.add_argument("--cases", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--pairs", type=int, default=7)
    p.add_argument("--warmups", type=int, default=1)
    p.add_argument("--only", action="append")
    p.add_argument("--baseline-only", action="store_true")
    args = p.parse_args()
    assert args.pairs >= 1 and args.warmups >= 0
    assert args.baseline_only or args.candidate_python
    args.output.mkdir()
    config = json.loads(args.cases.read_text())
    cases = [case for case in config["cases"] if not args.only or case["name"] in args.only]
    for case in cases:
        assert sha(case["recipe"]["path"]) == case["recipe"]["sha256"]
        if case.get("input_format", "native") == "native":
            assert sha(case["input"]["path"]) == case["input"]["sha256"]
        else:
            for rec in case["input"]["files"]:
                assert sha(rec["path"]) == rec["sha256"]
    facts = {"argv": sys.argv, "controller_sha256": sha(__file__), "audit_sha256": sha(Path(__file__).with_name("block_audit.py")),
             "cases_sha256": sha(args.cases), "cpu_affinity": sorted(os.sched_getaffinity(0)),
             "cpu_count": os.cpu_count(), "memory_before": Path("/proc/meminfo").read_text(),
             "swaps_before": Path("/proc/swaps").read_text(), "cases": cases,
             "scope": "FreshdirectCLIcompletewall/truechildwait4peak;baseline8GiBdeclaredworkingpolicy vsbounded64/128MiBintentional;identicalphysicalhardware/CPU/precision/recipe;noOScachepurge/fsync/outlierdrops/overheadsubtraction",
             "observations": [], "agreements": [], "summaries": {}}
    assert facts["cpu_count"] == 1 and facts["cpu_affinity"] == [0]
    log = args.output / "observations.jsonl"
    def save():
        (args.output / "results.json").write_text(json.dumps(facts, indent=2, allow_nan=False) + "\n")
    for case in cases:
        for kind, count in [("warmup", args.warmups), ("measured", args.pairs)]:
            for index in range(count):
                lanes = "B" if args.baseline_only else ("BC" if index % 2 == 0 else "CB")
                pair = {}
                for lane in lanes:
                    python = args.baseline_python if lane == "B" else args.candidate_python
                    result = run_one(python, case, lane, index, kind, args.output)
                    facts["observations"].append(result)
                    with log.open("a") as stream:
                        stream.write(json.dumps(result, allow_nan=False) + "\n")
                    save()
                    print(json.dumps({key: result[key] for key in ["case", "lane", "kind", "index", "wall_seconds", "peak_rss_bytes", "returncode"]}), flush=True)
                    if result["returncode"] != 0:
                        raise RuntimeError(f"CLI failed; all observations/outputs retained: {result}")
                    pair[lane] = result
                if not args.baseline_only:
                    audit_path = args.output / f"{case['name']}-{kind}-{index:02d}.agreement.json"
                    command = [str(args.baseline_python), "-I", str(Path(__file__).with_name("block_audit.py")),
                               "--reference", pair["B"]["output"], "--candidate", pair["C"]["output"], "--output", str(audit_path)]
                    child = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    if child.returncode != 0:
                        (args.output / f"{case['name']}-{kind}-{index:02d}.audit-failure.txt").write_text(child.stdout + child.stderr)
                        raise RuntimeError("Independent output audit failed; artifacts retained")
                    agreement = json.loads(audit_path.read_text())
                    expected = case.get("reference_anchor")
                    if expected:
                        assert agreement["reference_numeric_sha256"] == expected["numeric_sha256"], "baseline differs from pre-candidate fixed reference"
                        assert agreement["full_metadata_sha256"] == expected["full_metadata_sha256"], "baseline metadata differs from fixed reference"
                        assert [roi["center_indices"] for roi in agreement["rois"]] == expected["roi_centers"], "ROI anchors changed"
                    facts["agreements"].append({"case": case["name"], "kind": kind, "index": index,
                                                 "path": str(audit_path), "sha256": sha(audit_path),
                                                 "status": agreement["status"], "numeric_bytes_exact": agreement["numeric_bytes_exact"],
                                                 "full_metadata_history_exact": agreement["full_metadata_history_exact"],
                                                 "relative_maximum_error": agreement["relative_maximum_error"]})
                    save()
        rows = [row for row in facts["observations"] if row["case"] == case["name"] and row["kind"] == "measured"]
        facts["summaries"][case["name"]] = {lane: {field: summary([row[field] for row in rows if row["lane"] == lane])
                                                     for field in ["wall_seconds", "peak_rss_bytes", "user_seconds", "system_seconds"]}
                                              for lane in "B" if args.baseline_only} if args.baseline_only else {
                                                  lane: {field: summary([row[field] for row in rows if row["lane"] == lane])
                                                         for field in ["wall_seconds", "peak_rss_bytes", "user_seconds", "system_seconds"]} for lane in "BC"}
        if not args.baseline_only:
            pairs = {i: {row["lane"]: row for row in rows if row["index"] == i} for i in range(args.pairs)}
            facts["summaries"][case["name"]]["paired_C_over_B_wall"] = summary([pair["C"]["wall_seconds"] / pair["B"]["wall_seconds"] for pair in pairs.values()])
        save()
    facts["status"] = "PASS"
    regression = facts["summaries"].get("ordinary-eager-2d-regression", {})
    if not args.baseline_only and regression:
        ratio = regression["paired_C_over_B_wall"]["median"]
        facts["r6_eager_regression_gate"] = {"predeclared_threshold": 1.20, "paired_median_ratio": ratio,
                                           "status": "PASS" if ratio <= 1.20 else "REGRESSION_GATE_FAILED",
                                           "scope": "chosen ordinaryeager protein2D case; no blockperformance attribution; noisy gate failures require investigation/disclosure"}
        if ratio > 1.20:
            facts["status"] = "REGRESSION_GATE_FAILED"
    facts["memory_after"] = Path("/proc/meminfo").read_text()
    facts["swaps_after"] = Path("/proc/swaps").read_text()
    save()


if __name__ == "__main__":
    main()
