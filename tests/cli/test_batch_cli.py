"""Actual isolated CLI processes for saved scripts and serial batches."""
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from nmrnext.analysis import dataset_digest
from nmrnext.assignments import save_assignment_template
from nmrnext.io import write_dataset
from nmrnext.model import Axis, Dataset
from nmrnext.operations import quadrature_index_orientation


ROOT = Path(__file__).resolve().parents[2]


def _command(*argv):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + str(ROOT / "tests")
    return subprocess.run([sys.executable, "-m", "nmrnext", *map(str, argv)],
                          cwd=ROOT, env=env, text=True, capture_output=True, timeout=30)


def _source(tmp_path, *, frequency=True):
    if frequency:
        axis = Axis("h", 9, domain="frequency", unit="Hz", start=100, step=-2,
                    nucleus="1H", observation_mhz=500, reference_ppm=4.7)
        data = np.asarray([[0, 0, 2, 6, 2, 0, 0, -3, 0]], dtype="float32")
        d = Dataset(data, [axis])
    else:
        axis = Axis("t", 8, step=.001, dwell_seconds=.001)
        d = Dataset(np.asarray([[1,2,3,4,3,2,1,0],[0,0,0,0,0,0,0,0]],dtype="float32"),
                    [axis], ["t"])
    source = tmp_path / "source.nmrh5"
    write_dataset(d, source, format="native")
    return source, d


def _batch_manifest(tmp_path, *, labels=True, two=True):
    source, d = _source(tmp_path)
    manifest = {"schema_version":"1","kind":"batch_manifest","operations":[],
                "backend":"numpy","threads":1,"memory_bytes":64<<20,
                "items":[{"item_id":f"sample-{i}","input":str(source),
                          "output":str(tmp_path/f"result-{i}.nmrh5"),"axis_map":{"h":"h"} if labels else {}}
                         for i in range(2 if two else 1)]}
    if labels:
        template={"schema_version":"1","kind":"assignment_template",
                  "reference":{"dataset_digest":dataset_digest(d),"axes":[d.axes[0].to_dict()],
                               "complex_axes":[],"quadrature_index_orientation":quadrature_index_orientation(d)},
                  "analysis_channel":0,
                  "assignments":[{"assignment_id":"signal","label":"signal","sign":"positive",
                                  "unit":"points","search_bounds":{"h":[1,5]},
                                  "integration_bounds":{"h":[1,5]},"threshold":1.}]}
        template_path=tmp_path/"labels.json"
        save_assignment_template(template,template_path)
        manifest["assignment_template"]=str(template_path)
    path=tmp_path/"batch.json"
    path.write_text(json.dumps(manifest))
    return path, manifest


def test_child_process_import_origin_is_isolated_tree():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + str(ROOT / "tests")
    proc=subprocess.run([sys.executable,"-c","import nmrnext,nmrnext.cli; print(nmrnext.__file__); print(nmrnext.cli.__file__)"],
                        cwd=ROOT,env=env,text=True,capture_output=True,timeout=30)
    assert proc.returncode==0,proc.stderr
    assert all(Path(line).resolve().is_relative_to(ROOT/"src") for line in proc.stdout.splitlines())


def test_saved_batch_plan_run_resume_and_csv(tmp_path):
    path, manifest = _batch_manifest(tmp_path)
    planned = _command("batch-plan",path)
    assert planned.returncode==0,planned.stderr
    plan=json.loads(planned.stdout)
    assert [item["item_id"] for item in plan["items"]]==["sample-0","sample-1"]
    assert not any(Path(item["output"]).exists() for item in manifest["items"])
    run=_command("batch-run",path)
    assert run.returncode==0,run.stderr
    result=json.loads(run.stdout)
    assert result["completed"]==2
    with Path(result["csv_path"]).open(newline="",encoding="utf-8") as stream:
        rows=list(csv.DictReader(stream))
    assert [(r["item_id"],r["assignment_id"],r["status"]) for r in rows]==[
        ("sample-0","signal","measured"),("sample-1","signal","measured")]
    resumed=_command("batch-run",path,"--resume")
    assert resumed.returncode==0,resumed.stderr
    assert json.loads(resumed.stdout)["skipped"]==2
    manifest["items"][0]["output"]=str(tmp_path/"wrong.nmrh5")
    path.write_text(json.dumps(manifest))
    wrong=_command("batch-run",path,"--resume")
    assert wrong.returncode==2
    assert json.loads(wrong.stderr)["error"]["code"]=="INVALID_BATCH_RESUME"


def test_import_script_saves_strict_single_recipe_and_replays(tmp_path):
    source,_=_source(tmp_path,frequency=False)
    target=tmp_path/"processed.nmrh5"
    script=tmp_path/"process.com"
    script.write_text("nmrPipe -in original.fid | nmrPipe -fn EM -lb 1 -c 0.5 -out original.nmrh5")
    recipe=tmp_path/"saved-recipe.json"
    imported=_command("import-pipe-script",script,"--input",source,"--output",target,
                      "--save-recipe",recipe,"--backend","numpy")
    assert imported.returncode==0,imported.stderr
    compiled=json.loads(imported.stdout)
    saved=json.loads(recipe.read_text())
    assert compiled["operations"]==saved["operations"]
    assert set(saved) <= {"schema_version","input","output","operations","backend","threads","memory_bytes"}
    assert saved["input"]==str(source) and saved["output"]==str(target)
    assert _command("plan",recipe).returncode==0
    executed=_command("run",recipe)
    assert executed.returncode==0,executed.stderr
    assert target.exists()


def test_import_script_reports_unqualified_phase_at_source_line(tmp_path):
    source,_=_source(tmp_path,frequency=False)
    script=tmp_path/"unsafe-phase.com"
    script.write_text("nmrPipe -in in.fid |\n" "nmrPipe -fn PS -p0 45 -out out.nmrh5")
    target=tmp_path/"never.nmrh5"
    result=_command("import-pipe-script",script,"--input",source,"--output",target)
    assert result.returncode==2
    error=json.loads(result.stderr)["error"]
    assert error["code"]=="UNQUALIFIED_SCRIPT_FUNCTION"
    assert error["details"]["line"]==2
    assert not target.exists()


def test_processing_only_needs_no_dummy_assignment_template(tmp_path):
    path, manifest = _batch_manifest(tmp_path,labels=False,two=False)
    run=_command("batch-run",path)
    assert run.returncode==0,run.stderr
    result=json.loads(run.stdout)
    assert result["completed"]==1
    assert Path(manifest["items"][0]["output"]).exists()


def test_continue_after_missing_item_keeps_full_item_status(tmp_path):
    path,manifest=_batch_manifest(tmp_path)
    manifest["items"][1]["input"]=str(tmp_path/"missing.nmrh5")
    path.write_text(json.dumps(manifest))
    plan=_command("batch-plan",path)
    assert plan.returncode==0,plan.stderr
    assert plan.stdout and json.loads(plan.stdout)["refusals"]
    run=_command("batch-run",path,"--continue-on-error")
    assert run.returncode==0,run.stderr
    result=json.loads(run.stdout)
    rows=json.loads(Path(result["json_path"]).read_text())["rows"]
    assert [r["status"] for r in rows]==["measured","failed"]
    assert Path(manifest["items"][0]["output"]).exists()
    assert not Path(manifest["items"][1]["output"]).exists()


@pytest.mark.parametrize("threads",["0","-1"])
def test_import_rejects_invalid_threads_before_saved_recipe_publication(tmp_path,threads):
    source,_=_source(tmp_path,frequency=False)
    script=tmp_path/"process.com"
    script.write_text("nmrPipe -in in.fid | nmrPipe -fn EM -lb 1 -out out.nmrh5")
    saved=tmp_path/"must-not-exist.json"
    result=_command("import-pipe-script",script,"--input",source,"--output",tmp_path/"target.nmrh5",
                    "--save-recipe",saved,"--threads",threads)
    assert result.returncode==2
    assert json.loads(result.stderr)["error"]["code"]=="INVALID_INTEGER"
    assert not saved.exists()


def test_import_preserves_crlf_original_bytes_and_caps_large_script(tmp_path):
    source,_=_source(tmp_path,frequency=False)
    target=tmp_path/"target.nmrh5"
    script=tmp_path/"crlf.com"
    raw=b"nmrPipe -in in.fid |\r\nnmrPipe -fn EM -lb 1 -out out.nmrh5\r\n"
    script.write_bytes(raw)
    accepted=_command("import-pipe-script",script,"--input",source,"--output",target)
    assert accepted.returncode==0,accepted.stderr
    assert json.loads(accepted.stdout)["source_sha256"]==hashlib.sha256(raw).hexdigest()
    script.write_bytes(b"x"*((1<<20)+1))
    refused=_command("import-pipe-script",script,"--input",source,"--output",target)
    assert refused.returncode==2
    assert json.loads(refused.stderr)["error"]["code"]=="INVALID_SCRIPT"
    assert not target.exists()
