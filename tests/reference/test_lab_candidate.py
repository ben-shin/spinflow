"""Synthetic protocol checks only: no acquired payload or reference execution."""
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest

from nmrnext.model import Axis, Dataset, DatasetHeader
from nmrnext.operations import run_operations
from nmrnext.planning import plan_operations

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
spec = importlib.util.spec_from_file_location('lab_candidate_under_test',ROOT/'tools/lab_candidate.py')
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
sys.path.pop(0)


def header():
    # Actual procpar value differs from the original conversion's explicit SW.
    return DatasetHeader((Axis('a0',166,step=1/5555.55555556,encoding='states',observation_mhz=50.648),
                          Axis('a1',1500,step=1/50000,observation_mhz=125.691)),('a0','a1'),'float32')


def test_fixed_recipe_operation_order_and_resources():
    recipe=candidate.load_recipe();ops=recipe['operations']
    assert [o['op'] for o in ops] == ['window','zero_fill','fft','reference','phase','discard_imaginary',
        'transpose','window','zero_fill','fft','reference','phase','discard_imaginary','transpose']
    assert recipe['backend']=='native' and recipe['threads']==1 and recipe['memory_bytes']==1024**3
    assert recipe['input_format']=='varian-raw-2d' and recipe['output_format']=='nmrpipe-real'
    assert recipe['allow_losses']==['metadata','provenance','axis_annotations']
    for index,axis,ppm,obs,width in [(3,'a1',55,125.691,50000),(10,'a0',120,50.648,5555.556)]:
        assert ops[index]=={'op':'reference','axis':axis,'reference_ppm':ppm,'reference_hz':0,
                            'observation_mhz':obs,'frequency_scale':-1,'spectral_width_hz':width}
    assert ops[4]=={'op':'phase','axis':'a1','p0':29,'p1':0,'pivot':0}
    assert ops[11]=={'op':'phase','axis':'a0','p0':0,'p1':0,'pivot':0}
    assert ops[0]=={'op':'window','axis':'a1','kind':'sine','off':.35,'end':.98,'power':1,'first_point_scale':1}
    assert ops[7]=={'op':'window','axis':'a0','kind':'sine','off':.35,'end':.9,'power':1,'first_point_scale':.5}
    assert ops[1]['size']==4096 and ops[8]['size']==2048


def test_every_checkpoint_header_matches_fixture_without_payload(monkeypatch):
    case=json.loads(candidate.FIXTURE.read_text());recipe=candidate.load_recipe()
    monkeypatch.setattr('nmrnext.operations._execute',lambda *a,**k:pytest.fail('header plan ran numeric kernel'))
    plan=plan_operations(header(),recipe['operations'],backend='native',threads=1,memory_bytes=candidate.MEMORY_BYTES)
    stages={s['id']:s for s in case['stages']}
    candidate._check_stage(header(),stages['S00'])
    for name,_,stop in candidate.STAGE_SLICES:
        step=plan['steps'][stop-1]
        state=DatasetHeader(tuple(Axis(**a) for a in step['output_axes']),step['output_complex_axes'],'float32')
        candidate._check_stage(state,stages[name])
    assert plan['output_shape']==[1,2048,4096]
    assert plan['estimated_working_bytes']<=1024**3
    for axis,size,width,obs,car in [('a0',2048,5555.556,50.648,120),('a1',4096,50000,125.691,55)]:
        item=next(a for a in plan['output_axes'] if a['id']==axis)
        assert item['step']==pytest.approx(-width/size)
        assert item['start']==pytest.approx(width/2)
        assert item['reference_ppm']==car and item['observation_mhz']==obs
        # Literal expected first/last physical positions, no array matching.
        actual=car+(item['start']+np.array([0,size-1])*item['step'])/obs
        expected=car+np.array([width/2,-width/2+width/size])/obs
        np.testing.assert_allclose(actual,expected,rtol=0,atol=1e-12)


def test_basis_phase_diagnostic_and_discard_are_separate_signed_branches():
    axes=(Axis('a0',1,domain='frequency',unit='Hz'),Axis('a1',1,domain='frequency',unit='Hz'))
    recipe=candidate.load_recipe()
    for index in range(4):
        raw=np.zeros((4,1,1),dtype=np.float32);raw[index,0,0]=-2
        source=Dataset(raw,axes,('a0','a1'))
        diagnostic=run_operations(source,recipe['operations'][4:5],backend='native')
        main=run_operations(source,recipe['operations'][4:6],backend='native')
        np.testing.assert_array_equal(main.data,diagnostic.data[:2])
        assert diagnostic.complex_axes==('a0','a1') and main.complex_axes==('a0',)
        expected=np.zeros((2,1,1),dtype=np.float32)
        expected[index%2,0,0]=-2*(np.cos(np.deg2rad(29)) if index<2 else -np.sin(np.deg2rad(29)))
        np.testing.assert_allclose(main.data,expected,rtol=0,atol=2e-7)
        np.testing.assert_array_equal(source.data,raw)


def test_final_recipes_are_direct_new_outputs(tmp_path):
    source=tmp_path/'raw';source.mkdir();output=tmp_path/'outputs';output.mkdir()
    recipe=candidate.load_recipe()
    records=candidate.write_recipes(source,output,recipe)
    for fmt,suffix in [('nmrpipe-real','.ft2'),('ucsf-real','.ucsf')]:
        actual=json.loads(Path(records[fmt]['path']).read_text())
        assert actual['input']==str(source.resolve()) and actual['output']==str(output/('candidate'+suffix))
        assert actual['output_format']==fmt and actual['operations']==recipe['operations']
        assert '.nmrh5' not in json.dumps(actual)
    with pytest.raises(FileExistsError):candidate.write_recipes(source,output,recipe)


def test_checkpoint_orchestration_has_no_diagnostic_feed_or_hidden_stage(monkeypatch,tmp_path):
    base=header()
    hashes={'fid':'f'*64,'procpar':'p'*64}
    source=DatasetHeader(base.axes,base.complex_axes,'float32',{'import':{'source_sha256':hashes}})
    monkeypatch.setattr('nmrnext.io.describe_dataset',lambda *a,**k:source)
    monkeypatch.setattr('nmrnext.io.read_dataset',lambda *a,**k:source)
    monkeypatch.setattr(candidate,'verify_input',lambda *a:{k:{'sha256':v} for k,v in hashes.items()})
    calls=[];saved={}
    def scalar_run(current,ops,**kwargs):
        assert kwargs=={'backend':'native','threads':1,'memory_bytes':1024**3}
        plan=plan_operations(current,ops,**kwargs)
        result=DatasetHeader(tuple(Axis(**a) for a in plan['output_axes']),plan['output_complex_axes'],'float32')
        calls.append((current,ops,result))
        return result
    def fake_write(dataset,path,**kwargs):
        assert kwargs=={'format':'native'}
        saved[path.stem]=dataset
        path.write_bytes(b'header-test placeholder; no numeric payload')
    monkeypatch.setattr('nmrnext.operations.run_operations',scalar_run)
    monkeypatch.setattr('nmrnext.io.write_dataset',fake_write)
    case=json.loads(candidate.FIXTURE.read_text())
    result=candidate.write_checkpoints(tmp_path/'raw',tmp_path,candidate.load_recipe(),case)
    assert set(saved)=={f'S{i:02}' for i in range(11)}|{'S03P'}
    assert len(calls)==11
    assert calls[3][0] is saved['S03']  # diagnostic phase branch
    assert calls[4][0] is saved['S03']  # main phase/discard restarts from S03
    assert calls[4][0] is not saved['S03P']
    assert calls[5][0] is saved['S04']
    assert result['checkpoints']['S10']['complex_axes']==[]


def test_acquisition_hash_and_size_guard_uses_exact_members(tmp_path):
    files=[]
    for name,content in [('fid',b'literal fid'),('procpar',b'literal parameters')]:
        path=tmp_path/name;path.write_bytes(content)
        files.append({'source_member':'agilent2pipe_2d/agilent_2d/'+name,'bytes':len(content),'sha256':candidate.lab_compare.sha256(path)})
    case={'files':files}
    assert set(candidate.verify_input(tmp_path,case))=={'fid','procpar'}
    (tmp_path/'fid').write_bytes(b'changed fid')
    with pytest.raises(ValueError,match='identity mismatch'):candidate.verify_input(tmp_path,case)


@pytest.mark.parametrize('relation',['same','child','parent','existing'])
def test_workspace_refuses_acquisition_overlap_and_existing(tmp_path,relation):
    source=tmp_path/'raw';source.mkdir()
    other=tmp_path/'existing';other.mkdir()
    out={'same':source,'child':source/'out','parent':tmp_path,'existing':other}[relation]
    with pytest.raises(ValueError):candidate._new_workspace(source,out)
    assert source.is_dir() and other.is_dir()


def test_lock_failure_precedes_input_or_output_access(monkeypatch,tmp_path):
    def refuse(*a):raise ValueError('lock unavailable')
    monkeypatch.setattr(candidate,'verify_lock',refuse)
    monkeypatch.setattr(candidate,'verify_input',lambda *a:pytest.fail('read acquisition before lock'))
    out=tmp_path/'new'
    with pytest.raises(ValueError,match='lock unavailable'):
        candidate.main(['--execution-lock',str(tmp_path/'absent'),'--input',str(tmp_path/'missing'),
                        '--output-directory',str(out),'--mode','checkpoints'])
    assert not out.exists()


def test_actual_runtime_and_recipe_hashes_must_be_bound(monkeypatch,tmp_path):
    binary=tmp_path/'native.so';binary.write_bytes(b'literal fixture binary')
    lock={'candidate_driver_sha256':candidate.lab_compare.sha256(candidate.__file__),
          'candidate_recipe_sha256':candidate.lab_compare.sha256(candidate.RECIPE),
          'candidate_source_and_binary_hashes':[{'path':'native.so','sha256':candidate.lab_compare.sha256(binary)}]}
    monkeypatch.setattr(candidate.lab_compare,'verify_execution_lock',lambda *a:lock)
    monkeypatch.setattr(candidate,'_runtime_paths',lambda:{binary.resolve()})
    assert candidate.verify_lock(tmp_path/'lock.json')==lock
    binary.write_bytes(b'different native binary')
    with pytest.raises(ValueError,match='not locked'):candidate.verify_lock(tmp_path/'lock.json')
    lock['candidate_recipe_sha256']='0'*64
    with pytest.raises(ValueError,match='candidate_recipe'):candidate.verify_lock(tmp_path/'lock.json')
