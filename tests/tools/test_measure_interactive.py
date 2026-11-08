"""Check the engineering controller's accounting and narrow metadata allowance."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


TOOL = Path(__file__).resolve().parents[2] / "tools/measure_interactive.py"


def tool():
    spec = importlib.util.spec_from_file_location("interactive_measurement", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_help_is_standard_library_only_even_beside_a_shadow_product(tmp_path):
    (tmp_path / "nmrnext.py").write_text("raise RuntimeError('source import leak')\n")
    result = subprocess.run([sys.executable, str(TOOL), "--help"], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_metadata_comparison_only_allows_explicit_backend_version():
    module = tool()
    original = {"metadata": {"version": "scientific record"},
                "provenance": [{"backend": {"name": "native", "version": "0.3.0b2"},
                                "operation": {"op": "fft", "version": "1"}}]}
    changed = {"metadata": {"version": "scientific record"},
               "provenance": [{"backend": {"name": "native", "version": "0.3.0b3"},
                               "operation": {"op": "fft", "version": "1"}}]}
    assert module.numerical_metadata(original) == module.numerical_metadata(changed)
    assert original["provenance"][0]["backend"]["version"] == "0.3.0b2"
    changed["provenance"][0]["operation"]["version"] = "2"
    assert module.numerical_metadata(original) != module.numerical_metadata(changed)
    changed["provenance"][0]["operation"]["version"] = "1"
    changed["metadata"]["version"] = "changed scientific record"
    assert module.numerical_metadata(original) != module.numerical_metadata(changed)


def test_all_five_values_retained_in_declared_distribution():
    result = tool().distribution([1, 2, 3, 4, 100])
    assert result["raw"] == [1, 2, 3, 4, 100]
    assert result["count"] == 5 and result["median"] == 3
    assert result["q1"] == 2 and result["q3"] == 4 and result["max"] == 100


@pytest.mark.skipif(os.name != "posix", reason="genuine process-group/wait4 deadline control")
@pytest.mark.parametrize("role", ["worker", "comparison"])
def test_stalled_process_is_bounded_logged_and_reaped(tmp_path, role):
    module = tool()
    receipt = module.run_bounded([sys.executable, "-I", "-u", "-c",
        "import time; print('retained before stall',flush=True); time.sleep(30)"],
        tmp_path/role, tmp_path, os.environ.copy(), timeout=.15, grace=.1, role=role)
    assert receipt["timed_out"] and receipt["reaped"]
    assert receipt["exit_code"] != 0 and receipt["lifetime_peak_rss_bytes"] > 0
    assert receipt["elapsed_seconds"] < 5 and receipt["role"] == role
    assert b"retained before stall" in Path(receipt["stdout"]["path"]).read_bytes()
    with pytest.raises(ValueError, match="incomplete"):
        module.require_completed(receipt)


def test_late_failure_retains_every_completed_raw_observation(tmp_path):
    module = tool()
    path = tmp_path/"worker.journal.jsonl"
    def late_failure(config, journal):
        journal.emit("elapsed_captured_unvalidated", role="warmup", endpoint="warm_full_processing", elapsed_seconds=.125)
        journal.emit("validation_passed", role="warmup", endpoint="warm_full_processing")
        journal.emit("elapsed_captured_unvalidated", role="measured", endpoint="process_to_painted_current", elapsed_seconds=.25)
        raise ValueError("synthetic late validation failure")
    with pytest.raises(ValueError, match="late validation"):
        module.guarded_worker({"journal":str(path), "lane":"B", "pair":2}, late_failure)
    records=module.read_journal(path)
    assert [r["elapsed_seconds"] for r in records if "elapsed_seconds" in r] == [.125,.25]
    assert records[-1]["event"] == "worker_failed"
    assert records[-1]["endpoint"] == "process_to_painted_current"
    assert not any(r["event"] == "worker_complete" for r in records)


def test_same_shaped_wrong_region_fails_semantic_completion():
    module=tool()
    expected=((700,901,1),(1600,1801,1))
    ranges=((80.,98.),(126.,137.))
    assert module.frame_matches(expected,(1,1),(201,201),ranges,expected,ranges)
    assert not module.frame_matches(((720,921,1),(1620,1821,1)),(1,1),(201,201),ranges,expected,ranges)
    assert not module.frame_matches(expected,(1,1),(201,201),((81.,99.),ranges[1]),expected,ranges)


def test_changed_current_parent_fails_while_raw_source_can_stay_unchanged():
    module=tool();parent=object()
    module.require_parent(parent,parent,"saved-current-digest","saved-current-digest")
    with pytest.raises(ValueError,match="Current"):
        module.require_parent(parent,parent,"saved-current-digest","changed-current-digest")
    with pytest.raises(ValueError,match="Current"):
        module.require_parent(parent,object(),"saved-current-digest","saved-current-digest")


def history_fixture():
    recipe=json.loads((TOOL.parents[1]/"examples/laboratory-workflow.json").read_text())
    shapes=[(4,166,1500),(4,166,4096),(4,166,4096),(4,166,4096),(4,166,4096),
            (2,166,4096),(2,4096,166),(2,4096,166),(2,4096,2048),(2,4096,2048),
            (2,4096,2048),(2,4096,2048),(1,4096,2048),(1,2048,4096)]
    records=[];prior=(4,166,1500)
    for index,(operation,shape) in enumerate(zip(recipe["operations"],shapes)):
        op=dict(operation,version="1")
        if op["op"]=="fft":op["inverse"]=False
        entry={"operation":op,"dtype":"float32","input_shape":list(prior),"output_shape":list(shape),
               "backend":{"name":"native","version":"0.3.0b3","threads":1}}
        if index in (5,12):
            before,after,components=((['a0','a1'],['a0'],[0,1]) if index==5 else (['a0'],[],[0]))
            entry.update(losses=['discarded_imaginary_quadrature'],discard_imaginary={"axis":op["axis"],
                "input_complex_axes":before,"output_complex_axes":after,"retained_input_components":components})
        records.append(entry);prior=shape
    return recipe["operations"],records


@pytest.mark.parametrize("fault", ["missing", "dtype", "shape", "parameter", "quadrature", "cache"])
def test_wrong_scientific_work_or_cache_hit_is_rejected(fault):
    module=tool();operations,records=history_fixture()
    module.validate_history(records,[],operations,"0.3.0b3",reused_steps=0)
    reused=0
    if fault=="missing":records.pop()
    elif fault=="dtype":records[8]["dtype"]="float64"
    elif fault=="shape":records[8]["output_shape"][2]=1024
    elif fault=="parameter":records[4]["operation"]["p0"]=30
    elif fault=="quadrature":records[5]["discard_imaginary"]["retained_input_components"]=[0]
    elif fault=="cache":reused=12
    with pytest.raises(ValueError):module.validate_history(records,[],operations,"0.3.0b3",reused_steps=reused)
