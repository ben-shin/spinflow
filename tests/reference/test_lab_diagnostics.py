"""Generated analytical add-on checks only; no acquired case is executed."""
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
spec=importlib.util.spec_from_file_location('lab_diagnostics_under_test',ROOT/'tools/lab_diagnostics.py')
diag=importlib.util.module_from_spec(spec);spec.loader.exec_module(diag)
sys.path.pop(0)


def hand_transform(z,sign):
    """Literal closed forms for length3/4, independent of the tested oracles."""
    if z.shape[-1]==4:
        a,b,c,d=(z[...,i] for i in range(4))
        unshifted=[a+b+c+d,a+sign*1j*b-c-sign*1j*d,a-b+c-d,a-sign*1j*b-c+sign*1j*d]
        return np.stack([unshifted[i] for i in (2,3,0,1)],axis=-1)
    a,b,c=(z[...,i] for i in range(3))
    w=-.5+sign*1j*np.sqrt(3)/2
    return np.stack([a+w*w*b+w*c,a+b+c,a+w*b+w*w*c],axis=-1)


def transition(axis,sign,bits=('a0','a1')):
    data=(np.arange(2**len(bits)*3*4,dtype=np.float32).reshape(2**len(bits),3,4)-15)/7
    after=np.empty_like(data)
    mask=1<<bits.index(axis);i=('a0','a1').index(axis)
    for c in range(len(data)):
        if c&mask:continue
        z=np.moveaxis(data[c].astype(np.complex128)+1j*data[c|mask],i,-1)
        expected=np.moveaxis(hand_transform(z,sign),-1,i)
        after[c]=expected.real;after[c|mask]=expected.imag
    before={'data':data,'complex_axes':bits,'frequency_axes':()}
    result={'data':after,'complex_axes':bits,'frequency_axes':(axis,)}
    return before,result


@pytest.mark.parametrize('axis',['a0','a1'])
@pytest.mark.parametrize('sign',[-1,1])
def test_full_transition_and_literal_subset_match_closed_forms(axis,sign):
    before,after=transition(axis,sign)
    r=diag.transition_metrics(before,after,axis,sign)
    assert r['full_array_transition']['status']=='PASS'
    assert r['literal_dft_oracle_crosscheck']['status']=='PASS'
    assert r['literal_dft_actual_subset']['status']=='PASS'
    assert r['literal_trace_indices']==([0,2,3] if axis=='a0' else [0,1,2])
    assert r['full_array_transition']['gate']==2e-6
    assert len(r['quadrature_pairs'])==2


@pytest.mark.parametrize('n',[7,8,129])
@pytest.mark.parametrize('sign',[-1,1])
def test_literal_dft_centering_sign_and_frequency_batches(n,sign):
    j=np.arange(n)
    impulse=np.zeros(n,dtype=np.complex128);impulse[0]=2-3j
    tone=np.exp(-sign*2j*np.pi*j/n)
    result=diag.literal_dft(np.stack([impulse,tone]),sign)
    expected=np.zeros((2,n),dtype=np.complex128);expected[0]=2-3j;expected[1,n//2+1]=n
    np.testing.assert_allclose(result,expected,rtol=0,atol=3e-12)


def test_transition_rejects_wrong_scale_sign_and_nonfinite():
    before,after=transition('a1',1)
    for bad in [after['data']*2,-after['data'],np.flip(after['data'],axis=2)]:
        result=diag.transition_metrics(before,{**after,'data':bad},'a1',1)
        assert result['full_array_transition']['status']=='FAIL'
    bad=after['data'].copy();bad[0,0,0]=np.nan
    with pytest.raises(ValueError,match='nonfinite'):diag.transition_metrics(before,{**after,'data':bad},'a1',1)


def test_partial_quadrature_and_zero_reference_status():
    before,after=transition('a0',1,bits=('a0',))
    assert diag.transition_metrics(before,after,'a0',1)['full_array_transition']['status']=='PASS'
    before['data'][:]=0;after['data'][:]=0
    assert diag.transition_metrics(before,after,'a0',1)['full_array_transition']['status']=='UNVERIFIED'


def test_native_shared_input_uses_exact_fixed_signs_and_single_thread():
    before,_=transition('a1',1)
    seen=[]
    def fft(z,axis,inverse,threads):
        assert z.dtype==np.dtype('complex64') and axis==1 and not inverse and threads==1
        seen.append(z.copy())
        return np.fft.fft(z,axis=axis).astype(np.complex64)
    result=diag.native_shared_reference_metrics(before,'a1',fft)
    assert result['metric']['status']=='PASS'
    np.testing.assert_array_equal(seen[0].real,before['data'][0])
    np.testing.assert_array_equal(seen[0].imag,-before['data'][2])
    def wrong(z,axis,inverse,threads):return (np.fft.ifft(z,axis=axis)*z.shape[axis]).astype(np.complex64)
    assert diag.native_shared_reference_metrics(before,'a1',wrong)['metric']['status']=='FAIL'


def test_full_transition_maximum_gate_is_componentwise_not_modulus():
    m=diag._metric()
    diag._accumulate(m,np.array([1+1j]),np.array([1+0j]))
    assert diag._finish(m)['relative_max_error']==1


def regions():
    data=np.array([[[-2,1,4,2],[1,3,7,5],[2,4,3,-1]]],dtype=np.float32)
    ref={'data':data,'complex_axes':(),'frequency_axes':('a0','a1'),'transposed':False,
         'ppm':{'a0':np.array([3.,2.,1.]),'a1':np.array([8.,6.,4.,2.])}}
    can={**ref,'data':data.copy()}
    case={'stages':[{'id':'S10','shape_yx':[3,4],'complex_axes':[],'frequency_axes':['a0','a1'],
                     'reference_transposed':False,'relative_max_gate':2e-5}],
          'protocol_gates':{'physical_coordinate_tolerance_ppm':2e-5,'processed_window_sum_relative_tolerance':2e-5},
          'rois':[{'label':name,'inclusive_yx':[[0,2],[0,3]],'reference_peak_yx':[1,2],
                   'strength_class':kind,'expected_sign':'positive'} for name,kind in [('T11','weak'),('W43','weak'),('E15','strong'),('T49','strong'),('G14','intermediate')]],
          'noise_half_open_yx':[[[0,1],[0,2]],[[2,3],[2,4]]],'contours':{'magnitude_start':30000,'factor':1.2,'count':20,'signs':[-1,1]}}
    return can,ref,case


def test_all_fixed_regions_signed_slices_and_noise_are_reported_without_new_gates():
    can,ref,case=regions();result=diag.region_diagnostics(can,ref,case)
    assert result['mandatory_existing_final_gates']['pass']
    supplemental=result['supplementary_no_new_thresholds']
    assert len(supplemental['regions'])==5 and len(supplemental['noise_rectangles'])==2
    first=supplemental['regions'][0]
    assert first['fixed_slices']['horizontal']['candidate_values']==[1,3,7,5]
    assert first['fixed_slices']['vertical']['reference_values']==[4,7,3]
    assert first['fixed_slices']['horizontal']['reference_ppm']==[8,6,4,2]
    assert supplemental['noise_rectangles'][0]['reference']['mean']==-.5
    assert supplemental['noise_rectangles'][0]['reference']['mad_unscaled']==1.5
    assert 'status' not in supplemental
    # Moving the candidate maximum cannot move the predeclared slice.
    can['data'][0,0,0]=100
    changed=diag.region_diagnostics(can,ref,case)
    assert not changed['mandatory_existing_final_gates']['pass']
    assert changed['supplementary_no_new_thresholds']['regions'][0]['fixed_slices']['horizontal']['candidate_values']==[1,3,7,5]


def test_additive_hashes_are_required_before_case_read(monkeypatch,tmp_path):
    monkeypatch.setattr(diag.lab_compare,'verify_execution_lock',lambda *args:{})
    with pytest.raises(ValueError,match='additive diagnostic lock mismatch'):diag.verify_lock(tmp_path/'lock')


def test_wrong_stage_shape_or_storage_order_refused():
    item={'data':np.zeros((2,3,4),dtype=np.float32),'complex_axes':('a0',),
          'frequency_axes':('a1',),'transposed':True}
    stage={'id':'literal','complex_axes':['a0'],'shape_yx':[3,4],
           'frequency_axes':['a1'],'reference_transposed':True}
    diag._check_checkpoint(item,stage)
    with pytest.raises(ValueError,match='frozen checkpoint'):diag._check_checkpoint({**item,'transposed':False},stage)
    with pytest.raises(ValueError,match='frozen checkpoint'):diag._check_checkpoint({**item,'data':np.zeros((2,4,3),dtype=np.float32)},stage)


def test_absent_inputs_are_unverified_and_native_not_run(monkeypatch,tmp_path):
    monkeypatch.setattr(diag,'verify_lock',lambda path:{})
    monkeypatch.setattr(diag,'locked_native',lambda *args:pytest.fail('native must not run with missing inputs'))
    lock=tmp_path/'lock.json';lock.write_text('{}')
    report=tmp_path/'report.json'
    args=['--execution-lock',str(lock),'--report',str(report),'fft','--axis','a1']
    for name in ('reference-before','reference-after','candidate-before','candidate-after'):
        args += ['--'+name,str(tmp_path/name)]
    assert diag.main(args)==2
    result=json.loads(report.read_text())
    assert result['status']=='UNVERIFIED' and not result['acquired_comparison_executed']
