"""Literal synthetic laboratory-harness checks; no acquired data are read."""
import importlib.util
import json
from pathlib import Path
import struct

import h5py
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('lab_compare_under_test',ROOT/'tools/lab_compare.py')
lab=importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


def literal_pipe(path, payload, *, xsize=2, specnum=4, q0=0, q1=0,
                 transposed=False, frequency=(False,False), order='<', changes=None):
    # Header is literal format evidence; payload is supplied explicitly per test.
    h=[0.0]*512
    for key,value in {2:2.345,9:2,24:1 if transposed else 2,25:2 if transposed else 1,
                      55:q0,56:q1,99:xsize,106:1 if q0==q1==1 else 0,219:specnum,
                      221:int(transposed),100:80,119:10,101:100,
                      229:40,218:20,249:200,220:int(frequency[1]),222:int(frequency[0])}.items():
        h[key]=value
    for key,value in (changes or {}).items():h[key]=value
    header=bytearray(struct.pack(order+'512f',*h))
    header[64:72]=b'13C\0\0\0\0\0';header[72:80]=b'15N\0\0\0\0\0'
    path.write_bytes(bytes(header)+struct.pack(order+str(len(payload))+'f',*payload))
    return path


@pytest.mark.parametrize('order',['<','>'])
def test_literal_four_channels_and_fixed_signs(tmp_path,order):
    p=literal_pipe(tmp_path/'raw.fid', [1,2, 9,10, 5,6, 13,14, 3,4, 11,12, 7,8, 15,16],order=order)
    r=lab.read_pipe(p)
    np.testing.assert_array_equal(r['data'], [[[1,2],[3,4]],[[5,6],[7,8]],[[9,10],[11,12]],[[13,14],[15,16]]])
    np.testing.assert_array_equal(lab.canonical_reference(r), [[[1,2],[3,4]],[[-5,-6],[-7,-8]],[[-9,-10],[-11,-12]],[[13,14],[15,16]]])
    assert r['complex_axes']==('a0','a1')


def test_literal_transposed_layout(tmp_path):
    p=literal_pipe(tmp_path/'tp.fid',[1,3, 5,7, 9,11, 13,15, 2,4, 6,8, 10,12, 14,16],transposed=True)
    r=lab.read_pipe(p)
    np.testing.assert_array_equal(r['data'], [[[1,2],[3,4]],[[5,6],[7,8]],[[9,10],[11,12]],[[13,14],[15,16]]])
    assert r['transposed']


def test_real_x_complex_y_count_exception_and_transpose(tmp_path):
    r=lab.read_pipe(literal_pipe(tmp_path/'partial.fid',[1,2,5,6,3,4,7,8],q1=1,specnum=2))
    t=lab.read_pipe(literal_pipe(tmp_path/'partial-tp.fid',[1,3,5,7,2,4,6,8],q1=1,specnum=2,transposed=True))
    np.testing.assert_array_equal(r['data'],[[[1,2],[3,4]],[[5,6],[7,8]]])
    np.testing.assert_array_equal(t['data'],r['data'])
    assert t['complex_axes']==('a0',)


@pytest.mark.parametrize('n',[3,4])
def test_absolute_coordinates_use_orig_last_point(tmp_path,n):
    r=lab.read_pipe(literal_pipe(tmp_path/'real.ft2',list(range(2*n)),xsize=n,specnum=2,q0=1,q1=1,frequency=(True,True)))
    # Direct ORIG=100Hz, SW=80Hz, OBS=10MHz. No fitted carrier shift.
    expected=[(100+(n-1-i)*80/n)/10 for i in range(n)]
    np.testing.assert_allclose(r['ppm']['a1'],expected,rtol=0,atol=1e-14)
    np.testing.assert_array_equal(r['ppm']['a0'],[11,10])


@pytest.mark.parametrize('changes',[{9:3},{24:1},{106:1},{55:0.5},{45:1},{99:0},{119:0},{221:2},{219:3}])
def test_unknown_inconsistent_or_unsafe_headers_refused(tmp_path,changes):
    p=literal_pipe(tmp_path/'bad.fid',list(range(16)),changes=changes)
    with pytest.raises(ValueError):lab.read_pipe(p)


def test_payload_length_nonfinite_and_budget_refused(tmp_path):
    p=literal_pipe(tmp_path/'bad.fid',list(range(15)))
    with pytest.raises(ValueError,match='payload size'):lab.read_pipe(p)
    p=literal_pipe(p,[float('nan')]+list(range(15)))
    with pytest.raises(ValueError,match='nonfinite'):lab.read_pipe(p)
    with pytest.raises(ValueError,match='byte limit'):lab.read_pipe(p,max_bytes=2048)


def native(path,data,*,ids=('a0','a1'),bits=(),domains=('frequency','frequency')):
    axes=[]
    for i,identity in enumerate(ids):
        axes.append({'id':identity,'size':data.shape[i+1],'role':'spectral','domain':domains[i],
                     'unit':'Hz' if domains[i]=='frequency' else 's','start':0,'step':-1,
                     'reference_ppm':10,'reference_hz':0,'observation_mhz':10,
                     'encoding':'complex' if identity in bits else 'real'})
    m={'schema_version':'1','axes':axes,'complex_axes':bits,'metadata':{},'provenance':[]}
    with h5py.File(path,'w') as h:
        h.attrs['schema_version']='1';h.attrs['metadata_json']=json.dumps(m)
        h.create_dataset('data',data=data)
    return path


def test_native_axes_and_quadrature_ids_canonicalize(tmp_path):
    source=np.arange(4*2*3,dtype=np.float32).reshape(4,2,3)
    p=native(tmp_path/'native.h5',source,ids=('a1','a0'),bits=('a1','a0'))
    result=lab.read_native(p)
    np.testing.assert_array_equal(result['data'],np.stack([source[0].T,source[2].T,source[1].T,source[3].T]))
    assert result['complex_axes']==('a0','a1')
    with pytest.raises(ValueError,match='byte limit'):lab.read_native(p,max_bytes=4)


def test_native_external_links_refused(tmp_path):
    p=tmp_path/'bad.h5'
    with h5py.File(p,'w') as h:
        h.attrs['schema_version']='1';h['data']=h5py.ExternalLink('unread.h5','data')
    with pytest.raises(ValueError,match='schema/link'):lab.read_native(p)


def comparison_pair():
    a=np.array([[[1,2],[3,8]]],dtype=np.float32)
    item={'data':a,'complex_axes':(),'frequency_axes':('a0','a1'),
          'ppm':{'a0':np.array([11.,10.]),'a1':np.array([12.,10.])},'transposed':False}
    c={**item,'data':a.copy(),'ppm':{k:v.copy() for k,v in item['ppm'].items()}}
    stage={'id':'S10','shape_yx':[2,2],'complex_axes':[],'frequency_axes':['a0','a1'],
           'reference_transposed':False,'relative_max_gate':2e-5}
    case={'protocol_gates':{'physical_coordinate_tolerance_ppm':2e-5,'processed_window_sum_relative_tolerance':2e-5},
          'rois':[{'label':'literal','strength_class':'weak','inclusive_yx':[[0,1],[0,1]]}]}
    return c,item,stage,case


def test_comparison_rejects_wrong_sign_scale_phase_and_coordinates():
    c,r,s,f=comparison_pair()
    assert lab.compare_stage(c,r,s,f)['pass']
    for changed in (-r['data'],2*r['data'],np.flip(r['data'],axis=2)):
        assert not lab.compare_stage({**c,'data':changed},r,s,f)['pass']
    c['ppm']['a0']+=0.001
    assert not lab.compare_stage(c,r,s,f)['pass']


def test_partial_quadrature_phase_rotation_is_not_fitted():
    c,r,s,f=comparison_pair()
    r['data']=np.array([[[1,2],[3,4]],[[5,6],[7,8]]],dtype=np.float32)
    r['complex_axes']=('a0',)
    c={**r,'data':np.array([[[1,2],[3,4]],[[-5,-6],[-7,-8]]],dtype=np.float32)}
    s={**s,'id':'S08','complex_axes':['a0']}
    assert lab.compare_stage(c,r,s,f)['pass']
    # A 90-degree complex rotation changes R/I; the comparator must not refit it.
    wrong=np.stack([-c['data'][1],c['data'][0]])
    assert not lab.compare_stage({**c,'data':wrong},r,s,f)['pass']


def test_zero_denominator_never_yields_processed_pass():
    c,r,s,f=comparison_pair()
    c['data'][:]=0;r['data']=np.zeros_like(r['data'])
    result=lab.compare_stage(c,r,s,f)
    assert not result['pass'] and result['relative_max_error'] is None
    assert result['rois'][0]['signed_sum_relative_error'] is None


def test_lock_requires_actual_evidence_hashes_and_current_harness(tmp_path):
    protocol=tmp_path/'p.md';protocol.write_text('protocol')
    fixture=tmp_path/'c.json';fixture.write_text('{}')
    path=tmp_path/'lock.json';path.write_text('{"schema_version":1,"status":"PENDING"}')
    with pytest.raises(ValueError,match='not sealed'):lab.verify_execution_lock(path,protocol,fixture)
    lock={'schema_version':1,'status':'SEALED','protocol_sha256':lab.sha256(protocol),
          'case_fixture_sha256':lab.sha256(fixture),'harness_sha256':lab.sha256(lab.__file__)}
    path.write_text(json.dumps(lock))
    with pytest.raises(ValueError,match='lock mismatch'):lab.verify_execution_lock(path,protocol,fixture)


def test_raw_stage_is_exact_even_when_processed_tolerance_would_pass():
    c,r,s,f=comparison_pair()
    s={**s,'id':'S00','relative_max_gate':0}
    c['data'][0,0,0]+=np.float32(1e-6)
    assert not lab.compare_stage(c,r,s,f)['pass']


def test_hash_byte_limit_is_enforced_during_streaming(tmp_path):
    p=tmp_path/'large';p.write_bytes(b'12345')
    with pytest.raises(ValueError,match='hash input'):lab.sha256(p,max_bytes=4)


def test_sealed_lock_verifies_files_then_rejects_evidence_mutation(tmp_path,monkeypatch):
    monkeypatch.setattr(lab,'ROOT',tmp_path)
    (tmp_path/'tools').mkdir();(tmp_path/'examples').mkdir()
    driver=tmp_path/'tools/lab_candidate.py';driver.write_text('literal driver')
    recipe=tmp_path/'examples/laboratory-workflow.json';recipe.write_text('{}')
    protocol=tmp_path/'protocol.md';protocol.write_text('literal protocol')
    fixture=tmp_path/'fixture.json';fixture.write_text('{}')
    evidence=tmp_path/'evidence.json';evidence.write_text('{"status":"PASS","synthetic":true}')
    lock={'schema_version':1,'status':'SEALED','protocol_sha256':lab.sha256(protocol),'case_fixture_sha256':lab.sha256(fixture),
          'harness_sha256':lab.sha256(lab.__file__),'candidate_driver_sha256':lab.sha256(driver),'candidate_recipe_sha256':lab.sha256(recipe)}
    for name in ('candidate_source_and_binary_hashes','actual_reference_version_and_binary_hashes','actual_consumer_binary_hashes',
                 'dependency_runtime_versions','original_script_hashes','captured_actual_help_hashes',
                 'analytic_ft_auto_semantics_record','header_gui_prerequisite_evidence','hardware_and_resource_controls','benchmark_trial_schedule'):
        lock[name]=[{'path':'evidence.json','sha256':lab.sha256(evidence)}]
    path=tmp_path/'lock.json';path.write_text(json.dumps(lock))
    assert lab.verify_execution_lock(path,protocol,fixture)['status']=='SEALED'
    evidence.write_text('modified')
    with pytest.raises(ValueError,match='evidence hash mismatch'):lab.verify_execution_lock(path,protocol,fixture)


def test_public_case_preserves_fixed_gates_and_all_original_rois():
    c=json.loads((ROOT/'tests/fixtures/lab_workflow_case.json').read_text())
    old=json.loads((ROOT/'tests/fixtures/varian_public_manifest.json').read_text())
    assert c['protocol_gates']==old['protocol']
    assert c['status']=='PREDECLARED_PENDING_EXECUTION_LOCK'
    assert {r['label']:r['strength_class'] for r in c['rois']}=={'T11':'weak','W43':'weak','E15':'strong','T49':'strong','G14':'intermediate'}
    assert c['benchmark']['pair_order']=='AB,BA,AB,BA,AB,BA,AB'
    assert c['nmrpipe_to_native_signs']==[1,-1,-1,1]
    assert 'not a new independent holdout' in c['prior_exposure']
    assert all(not Path(i['cache_relative_path']).is_absolute() for i in c['files'])
