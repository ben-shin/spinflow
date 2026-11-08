"""The controller must preserve inputs and never promote failed trials to success."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


TOOL = Path(__file__).resolve().parents[2] / "tools/linux_user_acceptance.py"


def controller():
    assert TOOL.is_file(), "The installed-user acceptance controller is missing"
    spec = importlib.util.spec_from_file_location("linux_acceptance_controller", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def recipe(tmp_path, **changes):
    source = tmp_path / "source.h5"
    source.write_bytes(b"original input")
    payload = {"schema_version": "1", "input": "source.h5", "output": "old.ft2",
               "output_format": "nmrpipe-real", "backend": "native", "threads": 1,
               "memory_bytes": 1073741824,
               "allow_losses": ["metadata", "provenance", "axis_annotations"],
               "operations": [{"op": "fft", "axis": "a0"}]}
    payload.update(changes)
    path = tmp_path / "original.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, source, payload


def test_help_does_not_import_checkout_product(tmp_path):
    # Importing checkout nmrnext would fail here; help needs only the stdlib.
    (tmp_path / "nmrnext.py").write_text("raise RuntimeError('checkout leak')\n")
    result = subprocess.run([sys.executable, str(TOOL), "--help"], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_existing_output_and_source_overlap_are_refused_without_writes(tmp_path):
    mod = controller()
    source = tmp_path / "input"
    source.mkdir()
    (source / "fid").write_bytes(b"retain")
    existing = tmp_path / "existing"
    existing.mkdir()
    sentinel = existing / "sentinel"
    sentinel.write_bytes(b"old")
    for target in (existing, source / "new", tmp_path):
        with pytest.raises(ValueError):
            mod.new_output(target, source)
    assert sentinel.read_bytes() == b"old"
    assert not (source / "new").exists()


def test_recipe_retains_operations_budget_and_records_native_endpoint(tmp_path):
    mod = controller()
    path, source, original = recipe(tmp_path)
    loaded, actual_source = mod.load_task(path, None)
    endpoint = mod.endpoint_recipe(loaded, actual_source, tmp_path / "trial.nmrh5")
    assert endpoint["operations"] == [{"op": "fft", "axis": "a0"}]
    assert endpoint["memory_bytes"] == 1073741824
    assert endpoint["input"] == str(source.resolve())
    assert endpoint["output_format"] == "native"
    assert endpoint["allow_losses"] == []
    assert json.loads(path.read_text()) == original


def test_non_single_cpu_recipe_is_refused(tmp_path):
    mod = controller()
    path, _, _ = recipe(tmp_path, threads=2)
    with pytest.raises(ValueError, match="threads"):
        mod.load_task(path, None)


def test_overflowing_recipe_number_is_refused_before_any_output(tmp_path):
    mod = controller()
    path, _, _ = recipe(tmp_path)
    path.write_text(path.read_text().replace('"op": "fft"',
                                            '"op": "phase", "p0": 1e1000'))
    with pytest.raises(ValueError, match="finite"):
        mod.load_task(path, None)


def test_input_mutation_and_extra_file_are_detected(tmp_path):
    mod = controller()
    source = tmp_path / "input"
    source.mkdir()
    (source / "fid").write_bytes(b"initial")
    before = mod.input_snapshot(source)
    (source / "fid").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        mod.unchanged_input(source, before)
    (source / "fid").write_bytes(b"initial")
    (source / "extra").write_bytes(b"new")
    with pytest.raises(ValueError, match="changed"):
        mod.unchanged_input(source, before)


@pytest.mark.skipif(os.name != "posix", reason="actual POSIX symlink refusal")
def test_input_tree_symlinks_are_refused(tmp_path):
    mod = controller()
    source = tmp_path / "input"
    source.mkdir()
    (source / "fid").symlink_to(tmp_path / "other")
    with pytest.raises(ValueError, match="symlink"):
        mod.input_snapshot(source)


def test_distributions_include_every_value_with_inclusive_quartiles():
    mod = controller()
    values = [1, 2, 3, 4, 100]
    stats = mod.distribution(values)
    assert stats["raw"] == values
    assert stats["count"] == 5
    assert stats["median"] == 3
    assert stats["min"] == 1 and stats["max"] == 100
    assert stats["q1"] == 2 and stats["q3"] == 4 and stats["iqr"] == 2


@pytest.mark.skipif(not hasattr(os, "wait4"), reason="genuine wait4 is unavailable")
def test_child_status_rusage_and_large_logs_do_not_deadlock(tmp_path):
    mod = controller()
    argv = [sys.executable, "-I", "-c",
            "import sys; print('x'*200000); print('y'*200000,file=sys.stderr)"]
    result = mod.measure(argv, tmp_path / "ok", tmp_path, os.environ.copy())
    assert result["exit_code"] == 0
    assert result["peak_rss_bytes"] > 0
    assert result["stdout"]["bytes"] >= 200000
    assert result["stderr"]["bytes"] >= 200000


@pytest.mark.skipif(not hasattr(os, "wait4"), reason="genuine wait4 is unavailable")
def test_failed_child_is_preserved_and_cannot_form_a_pass_distribution(tmp_path):
    mod = controller()
    result = mod.measure([sys.executable, "-I", "-c", "raise SystemExit(7)"],
                         tmp_path / "bad", tmp_path, os.environ.copy())
    assert result["exit_code"] == 7
    with pytest.raises(ValueError, match="failed"):
        mod.require_success([result], 1)
    with pytest.raises(ValueError, match="incomplete"):
        mod.require_success([], 1)


def constructed_run(tmp_path, monkeypatch, *, late_failure=False):
    """Exercise controller accounting only; no product imports/acquired arrays."""
    mod = controller()
    path, source, original = recipe(tmp_path)
    prefix = tmp_path / "prefix"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/python").touch()
    args = SimpleNamespace(prefix=prefix, recipe=path, input=source,
                           output=tmp_path / "observations", samples=5, cpu=0)
    monkeypatch.setattr(mod.sys, "platform", "linux")
    monkeypatch.setattr(mod.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(mod.os, "sched_getaffinity", lambda _: {0}, raising=False)
    monkeypatch.setattr(mod.os, "sched_setaffinity", lambda *_: None, raising=False)
    observations = []

    def worker(argv, **kwargs):
        if argv[6] == "compare" and late_failure:
            raise ValueError("constructed late comparison failure")
        return SimpleNamespace(returncode=0, stdout='{"constructed":true}', stderr="")

    def measured(argv, stem, cwd, env):
        number = len(observations)+1
        assert argv[:-1] == [str(prefix / "bin/python"), "-I", "-B", "-m", "nmrnext", "run"]
        task = json.loads(Path(argv[-1]).read_text())
        assert task["operations"] == original["operations"]
        assert task["memory_bytes"] == original["memory_bytes"]
        assert task["output_format"] == "native" and task["threads"] == 1
        Path(task["output"]).write_bytes(b"constructed endpoint")
        stdout, stderr = Path(str(stem)+".stdout.json"), Path(str(stem)+".stderr.txt")
        stdout.write_text("{}"); stderr.write_text("")
        result = {"argv": argv, "exit_code": 0, "elapsed_seconds": number/10,
                  "peak_rss_bytes": number*1024, "controller_lifetime_peak_before_spawn_bytes": 9876,
                  "rss_scope": mod.RSS_SCOPE, "stdout": mod.file_pin(stdout), "stderr": mod.file_pin(stderr)}
        observations.append(result)
        return result

    monkeypatch.setattr(mod.subprocess, "run", worker)
    monkeypatch.setattr(mod, "measure", measured)
    return mod, args, observations


def journal_records(output):
    return [json.loads(line) for line in (output / "observations.journal.jsonl").read_text().splitlines()]


def test_completed_raw_journal_is_fsynced_before_validation_and_matches_statistics(tmp_path, monkeypatch):
    mod, args, originals = constructed_run(tmp_path, monkeypatch)
    fsync_observations = []
    real_fsync = mod.os.fsync
    def synced(fd):
        real_fsync(fd)
        fsync_observations.append(journal_records(args.output))
    monkeypatch.setattr(mod.os, "fsync", synced)
    actual_unchanged = mod.unchanged_input
    def validate_after_persistence(source, before):
        if originals:
            completed = [r["observation"] for r in journal_records(args.output) if r["event"] == "trial_completed"]
            assert completed == originals
            assert any(snapshot[-1].get("observation") == originals[-1] for snapshot in fsync_observations)
        return actual_unchanged(source, before)
    monkeypatch.setattr(mod, "unchanged_input", validate_after_persistence)
    assert mod.run(args) == 0
    report = json.loads((args.output / "report.json").read_text())
    completed = [r for r in journal_records(args.output) if r["event"] == "trial_completed"]
    assert [r["trial"] for r in completed] == [1, 2, 3, 4, 5]
    assert [r["observation"] for r in completed] == report["observations"] == originals
    assert report["elapsed_seconds"] == mod.distribution([r["elapsed_seconds"] for r in originals])
    assert report["peak_rss_bytes"] == mod.distribution([r["peak_rss_bytes"] for r in originals])
    for row in completed:
        assert any(snapshot[-1] == row for snapshot in fsync_observations)
        assert row["observation"]["controller_lifetime_peak_before_spawn_bytes"] == 9876


def test_late_failure_preserves_original_completed_trials_without_success_distribution(tmp_path, monkeypatch):
    mod, args, originals = constructed_run(tmp_path, monkeypatch, late_failure=True)
    assert mod.run(args) == 1
    report = json.loads((args.output / "report.json").read_text())
    rows = journal_records(args.output)
    assert [r["observation"] for r in rows if r["event"] == "trial_completed"] == originals
    assert len(originals) == 5
    assert report["automated_status"] == "FAILED_OR_INCOMPLETE"
    assert "elapsed_seconds" not in report and "peak_rss_bytes" not in report
    assert any(r.get("stage") == "installed_compare" for r in rows)
    assert rows[-1]["event"] == "runner_failed"
    retained = (args.output / "observations.journal.jsonl").read_bytes()
    with pytest.raises(ValueError, match="exists"):
        mod.run(args)
    assert (args.output / "observations.journal.jsonl").read_bytes() == retained


def test_raw_journal_is_exclusive_and_cannot_overwrite_existing_evidence(tmp_path):
    mod = controller()
    path = tmp_path / "observations.journal.jsonl"
    path.write_bytes(b"existing evidence\n")
    with pytest.raises(FileExistsError):
        mod.ObservationJournal(path)
    assert path.read_bytes() == b"existing evidence\n"


@pytest.mark.skipif(not hasattr(os, "wait4"), reason="genuine owned POSIX process-group termination")
@pytest.mark.parametrize("stall,completed", [("later_trial", 1), ("comparison", 2), ("initial_probe", 0)])
def test_completed_observations_survive_later_group_termination(tmp_path, stall, completed):
    """Short synthetic subprocess faults; never run acquired product work."""
    helper_path = TOOL.with_name("measure_interactive.py")
    spec = importlib.util.spec_from_file_location("bounded_test_helper", helper_path)
    bounded = importlib.util.module_from_spec(spec); spec.loader.exec_module(bounded)
    script = tmp_path / "constructed_stall.py"
    script.write_text(r'''
import importlib.util,json,pathlib,subprocess,sys,time
from types import SimpleNamespace
tool,root,stall=pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2]),sys.argv[3]
spec=importlib.util.spec_from_file_location('acceptance_fault_control',tool)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
source=root/'input';source.write_bytes(b'constructed input')
recipe=root/'recipe.json';recipe.write_text(json.dumps({'schema_version':'1','input':str(source),'output':'unused',
 'output_format':'native','allow_losses':[],'backend':'native','threads':1,'memory_bytes':1073741824,'operations':[{'op':'fft','axis':'a0'}]}))
prefix=root/'prefix';(prefix/'bin').mkdir(parents=True);(prefix/'bin/python').touch()
actual_measure=mod.measure;count=0
def worker(argv,**kwargs):
 mode=argv[6]
 if (stall=='initial_probe' and mode=='probe') or (stall=='comparison' and mode=='compare'):time.sleep(300)
 return SimpleNamespace(returncode=0,stdout='{}',stderr='')
def measured(argv,stem,cwd,env):
 global count
 count+=1
 target=json.loads(pathlib.Path(argv[-1]).read_text())['output']
 body='import pathlib,sys;pathlib.Path(sys.argv[1]).write_bytes(b"constructed output");print("{}")'
 if stall=='later_trial' and count==2:body='import time;time.sleep(300)'
 return actual_measure([sys.executable,'-I','-B','-c',body,target],stem,cwd,env)
mod.subprocess.run=worker;mod.measure=measured
mod.run(SimpleNamespace(prefix=prefix,recipe=recipe,input=source,output=root/'output',samples=2,cpu=min(mod.os.sched_getaffinity(0))))
''', encoding="utf-8")
    receipt = bounded.run_bounded([sys.executable, "-I", "-B", str(script), str(TOOL), str(tmp_path), stall],
                                  tmp_path / "outer", tmp_path, os.environ.copy(), timeout=1.5, grace=.2,
                                  role="synthetic_late_stall")
    (tmp_path / "outer-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    assert receipt["timed_out"] and receipt["reaped"] and receipt["exit_code"] != 0
    rows = journal_records(tmp_path / "output")
    original = [r for r in rows if r["event"] == "trial_completed"]
    assert len(original) == completed
    assert all(r["observation"]["elapsed_seconds"] > 0 and r["observation"]["peak_rss_bytes"] > 0 for r in original)
    assert all(Path(r["observation"]["stdout"]["path"]).exists() and Path(r["observation"]["stderr"]["path"]).exists() for r in original)
    expected_stage = {"later_trial":"trial_measurement", "comparison":"installed_compare", "initial_probe":"installed_probe"}[stall]
    assert rows[-1]["stage"] == expected_stage
    assert not (tmp_path / "output/report.json").exists()
    with pytest.raises(ValueError, match="failed/incomplete"):
        bounded.require_completed(receipt)
