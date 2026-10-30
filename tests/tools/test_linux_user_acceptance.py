"""The controller must preserve inputs and never promote failed trials to success."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

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
