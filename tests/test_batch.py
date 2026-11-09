"""Serial batch behavior over tiny real native spectra."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil

import numpy as np
import pytest

from nmrnext.analysis import dataset_digest
from nmrnext.assignments import save_assignment_template
from nmrnext.errors import NMRNextError
from nmrnext.io import write_dataset
from nmrnext.model import Axis, Dataset


def _fixture(tmp_path, *, count=2):
    from nmrnext.operations import quadrature_index_orientation
    axis = Axis("h", 9, domain="frequency", unit="Hz", start=100, step=-2,
                nucleus="1H", observation_mhz=500, reference_ppm=4.7)
    d = Dataset(np.asarray([[0, 0, 2, 6, 2, 0, 0, -3, 0]], dtype="float32"), [axis])
    first = tmp_path / "one" / "same.nmrh5"
    second = tmp_path / "two" / "same.nmrh5"
    first.parent.mkdir(); second.parent.mkdir()
    write_dataset(d, first, format="native")
    shutil.copyfile(first, second)
    template = {"schema_version":"1", "kind":"assignment_template",
                "reference":{"dataset_digest":dataset_digest(d),"axes":[axis.to_dict()],
                             "complex_axes":[],"quadrature_index_orientation":quadrature_index_orientation(d)},
                "analysis_channel":0,
                "assignments":[{"assignment_id":"peak-a","label":"signal","sign":"positive",
                                "unit":"points","search_bounds":{"h":[1,5]},
                                "integration_bounds":{"h":[1,5]},"threshold":1.}]}
    template_path = tmp_path / "template.json"
    save_assignment_template(template, template_path)
    inputs = [first, second][:count]
    manifest = {"schema_version":"1","kind":"batch_manifest","operations":[],
                "backend":"numpy","threads":1,"memory_bytes":64<<20,
                "assignment_template":str(template_path),
                "items":[{"item_id":f"item-{i}","input":str(source),
                          "output":str(tmp_path / f"result-{i}.nmrh5"),"axis_map":{"h":"h"}}
                         for i,source in enumerate(inputs)]}
    return manifest, tmp_path / "batch.json", first, second


def test_import_origin_and_manifest_roundtrip(tmp_path):
    import nmrnext.batch as batch
    assert Path(batch.__file__).resolve().parent == Path(__file__).resolve().parents[1] / "src/nmrnext"
    manifest, path, _, _ = _fixture(tmp_path)
    assert batch.save_batch_manifest(manifest, path) == manifest
    assert batch.load_batch_manifest(path) == manifest
    with pytest.raises(NMRNextError, match="exists"):
        batch.save_batch_manifest(manifest, path)


def test_identical_bytes_keep_distinct_ids_and_full_rows(tmp_path):
    from nmrnext.batch import save_batch_manifest, plan_batch, run_batch
    manifest, path, first, second = _fixture(tmp_path)
    save_batch_manifest(manifest, path)
    plan = plan_batch(path)
    assert [item["item_id"] for item in plan["items"]] == ["item-0", "item-1"]
    assert all(item["recipe"]["schema_version"] == "1" for item in plan["items"])
    events = []
    report = run_batch(path, on_progress=events.append)
    assert report["completed"] == 2
    rows = json.loads(Path(report["json_path"]).read_text())["rows"]
    assert [(r["item_id"],r["assignment_id"],r["status"]) for r in rows] == [
        ("item-0","peak-a","measured"),("item-1","peak-a","measured")]
    assert [r["height"] for r in rows] == [6.,6.]
    assert [r["volume"] for r in rows] == [20.,20.]
    assert rows[0]["source_sha256"] == rows[1]["source_sha256"]
    assert rows[0]["item_id"] != rows[1]["item_id"]
    assert [e["state"] for e in events] == ["started","completed","started","completed"]
    assert [e["completed"] for e in events] == [0,1,1,2]
    with pytest.raises(TypeError): events[0]["item_id"]="changed"
    assert first.exists() and second.exists()


def test_stop_after_first_preserves_complete_and_marks_unattempted(tmp_path, monkeypatch):
    import nmrnext.batch as batch
    manifest,path,_,_ = _fixture(tmp_path)
    batch.save_batch_manifest(manifest,path)
    original = batch.read_dataset
    loads = []
    def counted(*args, **kwargs):
        loads.append(args[0]); return original(*args, **kwargs)
    monkeypatch.setattr(batch,"read_dataset",counted)
    events=[]
    report=batch.run_batch(path,should_stop=lambda:len(loads)>0,on_progress=events.append)
    rows=json.loads(Path(report["json_path"]).read_text())["rows"]
    assert len(loads)==1 and report["completed"]==1
    assert [r["status"] for r in rows]==["measured","not_run"]
    assert rows[1]["height"] is None and rows[1]["stop_reason"]=="requested"
    assert [e["state"] for e in events]==["started","completed","not_run"]
    assert Path(manifest["items"][0]["output"]).exists()
    assert not Path(manifest["items"][1]["output"]).exists()


def test_resume_exact_identity_skips_and_changed_dest_or_output_refuses(tmp_path):
    from nmrnext.batch import save_batch_manifest,run_batch
    manifest,path,_,_= _fixture(tmp_path,count=1)
    save_batch_manifest(manifest,path)
    run_batch(path)
    assert run_batch(path,resume=True)["skipped"]==1
    output=Path(manifest["items"][0]["output"])
    with output.open("ab") as stream: stream.write(b"changed")
    with pytest.raises(NMRNextError): run_batch(path,resume=True)
    output.write_bytes(output.read_bytes()[:-7])
    changed=deepcopy(manifest); changed["items"][0]["output"]=str(tmp_path/"wrong.nmrh5")
    path.write_text(json.dumps(changed))
    with pytest.raises(NMRNextError): run_batch(path,resume=True)


@pytest.mark.parametrize("change",["input","operations","template","threads"])
def test_resume_refuses_changed_binding(tmp_path,change):
    from nmrnext.batch import save_batch_manifest,run_batch
    manifest,path,first,second= _fixture(tmp_path,count=1)
    save_batch_manifest(manifest,path); run_batch(path)
    changed=deepcopy(manifest)
    if change=="input": changed["items"][0]["input"]=str(second)
    elif change=="operations": changed["operations"]=[{"op":"reference","axis":"h","reference_ppm":4.7}]
    elif change=="template":
        t=json.loads(Path(manifest["assignment_template"]).read_text())
        t["assignments"][0]["label"]="changed"
        Path(manifest["assignment_template"]).write_text(json.dumps(t))
    else: changed["threads"]=2
    path.write_text(json.dumps(changed))
    with pytest.raises(NMRNextError): run_batch(path,resume=True)


def test_explicit_continue_after_failure_and_callback_failure_stop(tmp_path,monkeypatch):
    import nmrnext.batch as batch
    manifest,path,_,_=_fixture(tmp_path)
    batch.save_batch_manifest(manifest,path)
    original=batch.read_dataset
    def fail_first(source,*args,**kwargs):
        if Path(source)==Path(manifest["items"][0]["input"]):
            raise NMRNextError("READ_FAILED","forced read failure")
        return original(source,*args,**kwargs)
    monkeypatch.setattr(batch,"read_dataset",fail_first)
    report=batch.run_batch(path,continue_on_error=True)
    rows=json.loads(Path(report["json_path"]).read_text())["rows"]
    assert [r["status"] for r in rows]==["failed","measured"]
    assert not Path(manifest["items"][0]["output"]).exists()

    other=tmp_path/"callback"; other.mkdir()
    manifest2,path2,_,_=_fixture(other)
    batch.save_batch_manifest(manifest2,path2)
    monkeypatch.setattr(batch,"read_dataset",original)
    def callback(event):
        if event["state"]=="completed": raise RuntimeError("callback failed")
    report2=batch.run_batch(path2,on_progress=callback)
    rows2=json.loads(Path(report2["json_path"]).read_text())["rows"]
    assert [r["status"] for r in rows2]==["measured","not_run"]
    assert Path(manifest2["items"][0]["output"]).exists()
    assert not Path(manifest2["items"][1]["output"]).exists()


def test_late_output_failure_leaves_no_final_and_keeps_journal(tmp_path,monkeypatch):
    import nmrnext.batch as batch
    manifest,path,_,_=_fixture(tmp_path,count=1)
    batch.save_batch_manifest(manifest,path)
    def fail_write(*args,**kwargs): raise OSError("forced late write")
    monkeypatch.setattr(batch,"write_dataset",fail_write)
    report=batch.run_batch(path)
    assert report["failed"]==1
    assert not Path(manifest["items"][0]["output"]).exists()
    assert Path(report["journal_path"]).exists()
    assert json.loads(Path(report["json_path"]).read_text())["rows"][0]["status"]=="failed"


def test_preflight_refuses_canonical_output_collision_before_any_read(tmp_path,monkeypatch):
    import nmrnext.batch as batch
    manifest,path,_,_=_fixture(tmp_path)
    manifest["items"][1]["output"]=str(tmp_path)+"/./result-0.nmrh5"
    batch.save_batch_manifest(manifest,path)
    monkeypatch.setattr(batch,"read_dataset",lambda *a,**k: pytest.fail("sample read before preflight"))
    with pytest.raises(NMRNextError) as caught: batch.run_batch(path)
    assert caught.value.code=="OUTPUT_COLLISION"
    assert not Path(manifest["items"][0]["output"]).exists()


def test_same_locator_changed_source_bytes_refuses_resume(tmp_path):
    from nmrnext.batch import save_batch_manifest,run_batch
    manifest,path,first,_=_fixture(tmp_path,count=1)
    save_batch_manifest(manifest,path); run_batch(path)
    raw=bytearray(first.read_bytes())
    raw[-1]^=1
    first.write_bytes(raw)
    with pytest.raises(NMRNextError) as caught: run_batch(path,resume=True)
    assert caught.value.code=="INVALID_BATCH_RESUME"


def test_unsupported_script_refused_before_sample_read_and_output(tmp_path,monkeypatch):
    import nmrnext.batch as batch
    manifest,path,_,_=_fixture(tmp_path,count=1)
    manifest.pop("operations")
    script=tmp_path/"process.com"
    script.write_text("nmrPipe -in @item.input | nmrPipe -fn FT -out @item.output")
    manifest["pipe_script"]=str(script)
    batch.save_batch_manifest(manifest,path)
    monkeypatch.setattr(batch,"read_dataset",lambda *a,**k: pytest.fail("sample read before script gate"))
    planned=batch.plan_batch(path)
    assert planned["refusals"][0]["item_id"]==manifest["items"][0]["item_id"]
    assert planned["refusals"][0]["error"]["code"] in {
        "UNQUALIFIED_SCRIPT_FUNCTION","UNSUPPORTED_SCRIPT_PROFILE"}
    report=batch.run_batch(path)
    assert report["failed"]==1
    assert json.loads(Path(report["json_path"]).read_text())["rows"][0]["status"]=="failed"
    assert not Path(manifest["items"][0]["output"]).exists()


def test_late_second_write_failure_keeps_first_and_marks_failure(tmp_path,monkeypatch):
    import nmrnext.batch as batch
    manifest,path,_,_=_fixture(tmp_path)
    batch.save_batch_manifest(manifest,path)
    original=batch.write_dataset
    calls=[]
    def fail_second(*args,**kwargs):
        calls.append(args[1])
        if len(calls)==2: raise OSError("late second write")
        return original(*args,**kwargs)
    monkeypatch.setattr(batch,"write_dataset",fail_second)
    report=batch.run_batch(path)
    assert report["completed"]==2 and report["failed"]==1
    assert Path(manifest["items"][0]["output"]).exists()
    assert not Path(manifest["items"][1]["output"]).exists()
    rows=json.loads(Path(report["json_path"]).read_text())["rows"]
    assert [r["status"] for r in rows]==["measured","failed"]
    assert rows[1]["error"]["code"]=="BATCH_ITEM_FAILED"


def test_fresh_report_race_never_replaces_foreign_user_bytes(tmp_path, monkeypatch):
    import nmrnext.batch as batch
    manifest,path,_,_=_fixture(tmp_path,count=1)
    batch.save_batch_manifest(manifest,path)
    journal=path.with_name(path.stem+".batch-journal.json")
    original=batch._atomic_bytes
    def race(target,raw):
        if Path(target)==journal and not journal.exists():
            journal.write_bytes(b"USER JOURNAL")
        return original(target,raw)
    monkeypatch.setattr(batch,"_atomic_bytes",race)
    with pytest.raises((NMRNextError,FileExistsError)): batch.run_batch(path)
    assert journal.read_bytes()==b"USER JOURNAL"
    assert not Path(manifest["items"][0]["output"]).exists()


def test_failed_write_cannot_delete_unowned_stage(tmp_path, monkeypatch):
    import nmrnext.batch as batch
    manifest,path,_,_=_fixture(tmp_path,count=1)
    batch.save_batch_manifest(manifest,path)
    collided=[]
    def occupy_stage(dataset,stage,**kwargs):
        stage=Path(stage)
        stage.write_bytes(b"USER STAGING FILE")
        collided.append(stage)
        raise NMRNextError("OUTPUT_EXISTS","stage appeared before writer")
    monkeypatch.setattr(batch,"write_dataset",occupy_stage)
    target=Path(manifest["items"][0]["output"])
    report=batch.run_batch(path)
    assert report["failed"]==1
    assert len(collided)==1 and collided[0].read_bytes()==b"USER STAGING FILE"
    assert not target.exists()


def test_template_digest_binds_bytes_actually_used(tmp_path,monkeypatch):
    import nmrnext.batch as batch
    manifest,path,_,_=_fixture(tmp_path,count=1)
    batch.save_batch_manifest(manifest,path)
    template_path=Path(manifest["assignment_template"])
    original=template_path.read_bytes()
    original_digest=hashlib.sha256(original).hexdigest()
    later=original.replace(b'"threshold": 1.0',b'"threshold": 1000.0')
    assert later!=original
    real_hash=batch._hash_file
    def race(p):
        if Path(p)==template_path:
            template_path.write_bytes(later)
        return real_hash(p)
    monkeypatch.setattr(batch,"_hash_file",race)
    report=batch.run_batch(path)
    row=json.loads(Path(report["json_path"]).read_text())["rows"][0]
    assert row["height"]==6.
    assert row["template_sha256"]==original_digest


@pytest.mark.parametrize("mutation",["height","assignment_id","item_id"])
def test_resume_rejects_forged_journal_row_before_export(tmp_path,mutation):
    import nmrnext.batch as batch
    manifest,path,_,_=_fixture(tmp_path,count=1)
    batch.save_batch_manifest(manifest,path)
    report=batch.run_batch(path)
    journal_path=Path(report["journal_path"])
    journal=json.loads(journal_path.read_text())
    row=journal["items"]["item-0"]["rows"][0]
    row[mutation]=9999. if mutation=="height" else "foreign"
    journal_path.write_text(json.dumps(journal))
    before=Path(report["json_path"]).read_bytes()
    with pytest.raises(NMRNextError): batch.run_batch(path,resume=True)
    assert Path(report["json_path"]).read_bytes()==before


def test_missing_second_input_is_per_item_refusal_with_continue_and_default_stop(tmp_path):
    import nmrnext.batch as batch
    manifest,path,_,_=_fixture(tmp_path)
    manifest["items"][1]["input"]=str(tmp_path/"missing.nmrh5")
    batch.save_batch_manifest(manifest,path)
    plan=batch.plan_batch(path)
    assert [item["item_id"] for item in plan["items"]]==["item-0","item-1"]
    assert plan["refusals"][0]["item_id"]=="item-1"
    report=batch.run_batch(path,continue_on_error=True)
    rows=json.loads(Path(report["json_path"]).read_text())["rows"]
    assert [r["status"] for r in rows]==["measured","failed"]
    assert Path(manifest["items"][0]["output"]).exists()

    other=tmp_path/"default";other.mkdir()
    m,p,_,_=_fixture(other)
    m["items"][0]["input"]=str(other/"missing.nmrh5")
    batch.save_batch_manifest(m,p)
    default=batch.run_batch(p)
    default_rows=json.loads(Path(default["json_path"]).read_text())["rows"]
    assert [r["status"] for r in default_rows]==["failed","not_run"]


def test_processing_only_3d_preserves_all_quadratures(tmp_path):
    import nmrnext.batch as batch
    axes=[Axis(k,2,step=.001,dwell_seconds=.001) for k in ("x","y","z")]
    data=np.arange(64,dtype="float32").reshape(8,2,2,2)
    source=tmp_path/"three.nmrh5"
    write_dataset(Dataset(data,axes,["x","y","z"]),source,format="native")
    target=tmp_path/"three-out.nmrh5"
    manifest={"schema_version":"1","kind":"batch_manifest","operations":[],
              "backend":"numpy","threads":1,"memory_bytes":64<<20,
              "items":[{"item_id":"three","input":str(source),"output":str(target),"axis_map":{}}]}
    path=tmp_path/"three-batch.json"
    batch.save_batch_manifest(manifest,path)
    report=batch.run_batch(path)
    assert report["completed"]==1 and report["failed"]==0
    from nmrnext.io import read_dataset
    actual=read_dataset(target,format="native")
    assert actual.complex_axes==("x","y","z")
    np.testing.assert_array_equal(actual.data,data)
    assert Path(report["csv_path"]).read_text().startswith("item_id,")


@pytest.mark.parametrize("stop_after",[0,1])
def test_stopped_resume_keeps_all_prior_completed_rows_and_can_resume_again(tmp_path, monkeypatch, stop_after):
    import nmrnext.batch as batch
    manifest,path,_,_=_fixture(tmp_path)
    batch.save_batch_manifest(manifest,path)
    original=batch.run_batch(path)
    original_rows=json.loads(Path(original["json_path"]).read_text())["rows"]
    output_bytes=[Path(item["output"]).read_bytes() for item in manifest["items"]]
    original_receipts={key:Path(original[key]).read_bytes() for key in
                       ("journal_path","json_path","csv_path")}
    real_read=batch.read_dataset
    source_reads=[]
    def track_read(input_path,*args,**kwargs):
        if Path(input_path) in [Path(i["input"]) for i in manifest["items"]]:
            source_reads.append(str(input_path))
        return real_read(input_path,*args,**kwargs)
    monkeypatch.setattr(batch,"read_dataset",track_read)
    checks=[]
    def stop():
        checks.append(True)
        return len(checks)>stop_after
    stopped=batch.run_batch(path,resume=True,should_stop=stop)
    assert stopped["completed"]==2 and stopped["skipped"]==2 and stopped["not_run"]==0
    stopped_rows=json.loads(Path(stopped["json_path"]).read_text())["rows"]
    assert stopped_rows==original_rows and not source_reads
    assert [Path(item["output"]).read_bytes() for item in manifest["items"]]==output_bytes
    assert {key:Path(original[key]).read_bytes() for key in original_receipts}==original_receipts
    again=batch.run_batch(path,resume=True)
    assert again["completed"]==2 and again["skipped"]==2
    assert json.loads(Path(again["json_path"]).read_text())["rows"]==original_rows


def test_partial_prior_completion_survives_repeated_stop_then_finishes(tmp_path):
    import nmrnext.batch as batch
    manifest,path,_,_=_fixture(tmp_path)
    batch.save_batch_manifest(manifest,path)
    initial=batch.run_batch(path,should_stop=lambda:Path(manifest["items"][0]["output"]).exists())
    assert initial["completed"]==1 and initial["not_run"]==1
    first_bytes=Path(manifest["items"][0]["output"]).read_bytes()
    first_row=json.loads(Path(initial["json_path"]).read_text())["rows"][0]
    for _ in range(2):
        stopped=batch.run_batch(path,resume=True,should_stop=lambda:True)
        assert stopped["completed"]==1 and stopped["skipped"]==1 and stopped["not_run"]==1
        rows=json.loads(Path(stopped["json_path"]).read_text())["rows"]
        assert rows[0]==first_row and rows[1]["status"]=="not_run"
        assert Path(manifest["items"][0]["output"]).read_bytes()==first_bytes
        assert not Path(manifest["items"][1]["output"]).exists()
    resumed=batch.run_batch(path,resume=True)
    assert resumed["completed"]==2 and resumed["skipped"]==1 and resumed["not_run"]==0
    assert [row["status"] for row in json.loads(Path(resumed["json_path"]).read_text())["rows"]]==[
        "measured","measured"]


def test_stopped_retry_does_not_republish_old_failed_row_as_current(tmp_path, monkeypatch):
    import nmrnext.batch as batch
    manifest,path,_,_=_fixture(tmp_path)
    batch.save_batch_manifest(manifest,path)
    real_read=batch.read_dataset
    def first_failure(input_path,*args,**kwargs):
        if Path(input_path)==Path(manifest["items"][0]["input"]):
            raise NMRNextError("READ_FAILED","first attempt failed")
        return real_read(input_path,*args,**kwargs)
    monkeypatch.setattr(batch,"read_dataset",first_failure)
    first=batch.run_batch(path)
    assert [r["status"] for r in json.loads(Path(first["json_path"]).read_text())["rows"]]==[
        "failed","not_run"]
    monkeypatch.setattr(batch,"read_dataset",real_read)
    stopped=batch.run_batch(path,resume=True,should_stop=lambda:True)
    assert [r["status"] for r in json.loads(Path(stopped["json_path"]).read_text())["rows"]]==[
        "not_run","not_run"]
    assert json.loads(Path(stopped["journal_path"]).read_text())["items"]=={}
    finished=batch.run_batch(path,resume=True)
    assert finished["completed"]==2


@pytest.mark.parametrize("phase",["intermediate","final"])
@pytest.mark.parametrize("receipt",["journal","json","csv","commit"])
def test_nonprefix_commits_survive_publication_failure_and_resume(tmp_path,monkeypatch,phase,receipt):
    import nmrnext.batch as batch
    manifest,path,_,_=_fixture(tmp_path)
    batch.save_batch_manifest(manifest,path)
    original_read=batch.read_dataset
    first_source=Path(manifest["items"][0]["input"])
    def first_failure(input_path,*args,**kwargs):
        if Path(input_path)==first_source:
            raise NMRNextError("READ_FAILED","first attempt failed")
        return original_read(input_path,*args,**kwargs)
    monkeypatch.setattr(batch,"read_dataset",first_failure)
    prior=batch.run_batch(path,continue_on_error=True)
    monkeypatch.setattr(batch,"read_dataset",original_read)
    prior_rows=json.loads(Path(prior["json_path"]).read_text())["rows"]
    assert [row["status"] for row in prior_rows]==["failed","measured"]
    second_output=Path(manifest["items"][1]["output"])
    second_bytes=second_output.read_bytes()
    prior_receipts={key:Path(prior[key]).read_bytes() for key in
                    ("journal_path","json_path","csv_path")}
    real_write=batch._atomic_bytes
    calls=[]
    chosen={"journal":1,"json":2,"csv":3,"commit":4}[receipt]
    fail_on=(4 if phase=="intermediate" else 8)+chosen
    foreign=[]
    def fail_once(target,raw):
        calls.append(str(target))
        if len(calls)==fail_on:
            if receipt=="commit":
                raise OSError("commit marker write failed before publication")
            Path(target).write_bytes(b"FOREIGN RECEIPT")
            foreign.append(Path(target))
            raise NMRNextError("OUTPUT_EXISTS","foreign receipt arrived")
        return real_write(target,raw)
    monkeypatch.setattr(batch,"_atomic_bytes",fail_once)
    with pytest.raises((NMRNextError,OSError)): batch.run_batch(path,resume=True)
    monkeypatch.setattr(batch,"_atomic_bytes",real_write)
    if receipt!="commit":
        assert len(foreign)==1 and foreign[0].read_bytes()==b"FOREIGN RECEIPT"
    assert second_output.read_bytes()==second_bytes
    assert {key:Path(prior[key]).read_bytes() for key in prior_receipts}==prior_receipts
    first_output=Path(manifest["items"][0]["output"])
    assert first_output.exists()==(phase=="final")
    resumed=batch.run_batch(path,resume=True)
    assert resumed["completed"]==2 and resumed["failed"]==0
    assert resumed["skipped"]==(2 if phase=="final" else 1)
    rows=json.loads(Path(resumed["json_path"]).read_text())["rows"]
    assert [(row["item_id"],row["status"]) for row in rows]==[
        ("item-0","measured"),("item-1","measured")]
    assert second_output.read_bytes()==second_bytes


def test_rollback_never_deletes_same_byte_foreign_replacement(tmp_path,monkeypatch):
    import nmrnext.batch as batch
    manifest,path,_,_=_fixture(tmp_path)
    batch.save_batch_manifest(manifest,path)
    first_output=Path(manifest["items"][0]["output"])
    prior=batch.run_batch(path,should_stop=lambda:first_output.exists())
    prior_output=first_output.read_bytes()
    prior_receipts={key:Path(prior[key]).read_bytes() for key in
                    ("journal_path","json_path","csv_path")}
    second_output=Path(manifest["items"][1]["output"])
    original_publish=batch._publish
    replaced=[]
    def replace_after_publish(stage,target):
        owned=stage.stat(follow_symlinks=False)
        original_publish(stage,target)
        if Path(target)==second_output:
            foreign=tmp_path/"foreign-same-bytes.nmrh5"
            foreign.write_bytes(Path(target).read_bytes())
            os.replace(foreign,target)
            now=Path(target).stat(follow_symlinks=False)
            assert (now.st_dev,now.st_ino)!=(owned.st_dev,owned.st_ino)
            replaced.append(Path(target).read_bytes())
    monkeypatch.setattr(batch,"_publish",replace_after_publish)
    original_write=batch._atomic_bytes
    calls=[]
    def late_receipt_failure(target,raw):
        calls.append(target)
        if len(calls)==5:
            raise NMRNextError("OUTPUT_EXISTS","receipt publication failed")
        return original_write(target,raw)
    monkeypatch.setattr(batch,"_atomic_bytes",late_receipt_failure)
    with pytest.raises(NMRNextError): batch.run_batch(path,resume=True)
    assert len(replaced)==1 and second_output.read_bytes()==replaced[0]
    assert first_output.read_bytes()==prior_output
    assert {key:Path(prior[key]).read_bytes() for key in prior_receipts}==prior_receipts
