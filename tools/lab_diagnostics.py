"""Additive preregistered FFT and fixed-region diagnostics, excluded from timing.

Full-array transition oracles use complex128 NumPy transforms. Literal DFTs
cover every bin on fixed orthogonal traces [0, middle, last], every quadrature
pair, with frequency batches of64. Native code is only the code under test.
No acquired-case reads are allowed before the additive execution lock passes.
"""
from __future__ import annotations

import argparse
import importlib
import json
import math
from pathlib import Path

import numpy as np

try:
    from . import lab_compare
except ImportError:
    import lab_compare

ROOT=Path(__file__).resolve().parents[1]
PROTOCOL=ROOT/'docs/laboratory-workflow-protocol.md'
FIXTURE=ROOT/'tests/fixtures/lab_workflow_case.json'
TESTS=ROOT/'tests/reference/test_lab_diagnostics.py'
FFT_GATE=2e-6
TRACE_BATCH=8
FREQUENCY_BATCH=64


def verify_lock(path):
    lock=lab_compare.verify_execution_lock(path,PROTOCOL,FIXTURE)
    for key,p in [('diagnostics_sha256',Path(__file__)),('diagnostics_tests_sha256',TESTS)]:
        if lock.get(key)!=lab_compare.sha256(p):
            raise ValueError('additive diagnostic lock mismatch: '+key)
    return lock


def locked_native(lock,path):
    native=importlib.import_module('nmrnext._native')
    actual=Path(native.__file__).resolve()
    recorded={}
    for entry in lock['candidate_source_and_binary_hashes']:
        p=Path(entry['path'])
        if not p.is_absolute():p=Path(path).resolve().parent/p
        recorded[p.resolve()]=entry['sha256']
    if recorded.get(actual)!=lab_compare.sha256(actual):
        raise ValueError('actual native extension is not bound by execution lock')
    return native.fft_complex


def _metric():
    return {'maximum_absolute_error':0.0,'maximum_reference_component':0.0,
            'squared_error':0.0,'squared_reference':0.0,'complex_values_checked':0}


def _accumulate(metric,actual,expected):
    actual=np.asarray(actual,dtype=np.complex128);expected=np.asarray(expected,dtype=np.complex128)
    if actual.shape!=expected.shape or not np.isfinite(actual).all() or not np.isfinite(expected).all():
        raise ValueError('nonfinite or incompatible transform arrays')
    delta=actual-expected
    # Existing gate is maximum real-component error, not complex modulus error.
    error=max(float(np.max(np.abs(delta.real))),float(np.max(np.abs(delta.imag))))
    denominator=max(float(np.max(np.abs(expected.real))),float(np.max(np.abs(expected.imag))))
    metric['maximum_absolute_error']=max(metric['maximum_absolute_error'],error)
    metric['maximum_reference_component']=max(metric['maximum_reference_component'],denominator)
    metric['squared_error']+=float(np.sum(delta.real*delta.real+delta.imag*delta.imag))
    metric['squared_reference']+=float(np.sum(expected.real*expected.real+expected.imag*expected.imag))
    metric['complex_values_checked']+=actual.size


def _finish(metric):
    result=dict(metric)
    denominator=result['maximum_reference_component']
    result['relative_max_error']=result['maximum_absolute_error']/denominator if denominator else None
    result['relative_l2_error']=math.sqrt(result['squared_error']/result['squared_reference']) if result['squared_reference'] else None
    result['gate']=FFT_GATE
    result['status']='UNVERIFIED' if denominator==0 else 'PASS' if result['relative_max_error']<=FFT_GATE else 'FAIL'
    return result


def numpy_oracle(z,exponent):
    z=np.asarray(z,dtype=np.complex128)
    result=np.fft.ifft(z,axis=-1)*z.shape[-1] if exponent==1 else np.fft.fft(z,axis=-1)
    return np.fft.fftshift(result,axes=-1)


def literal_dft(z,exponent):
    """Independent exponential sum, bounded kernel blocks; no FFT call."""
    z=np.asarray(z,dtype=np.complex128)
    if z.ndim!=2 or exponent not in (-1,1):raise ValueError('DFT requires traces and exponent +/-1')
    n=z.shape[-1];result=np.empty(z.shape,dtype=np.complex128)
    j=np.arange(n,dtype=np.float64)
    for start in range(0,n,FREQUENCY_BATCH):
        k=np.arange(start,min(start+FREQUENCY_BATCH,n),dtype=np.float64)-n//2
        kernel=np.exp(exponent*2j*np.pi*(k[:,None]*j[None,:])/n)
        result[:,start:start+len(k)]=z@kernel.T
    return result


def _pairs(bits,axis):
    if axis not in bits:raise ValueError('transform axis lacks quadrature')
    mask=1<<bits.index(axis)
    return [(c,c|mask) for c in range(2**len(bits)) if not c&mask]


def _complex_traces(data,real,imag,axis,selection):
    i=('a0','a1').index(axis)
    r=np.moveaxis(data[real],i,-1)[selection]
    im=np.moveaxis(data[imag],i,-1)[selection]
    z=np.empty(r.shape,dtype=np.complex128);z.real=r;z.imag=im
    return z


def _transition_state(before,after,axis):
    if before['data'].shape!=after['data'].shape or before['complex_axes']!=after['complex_axes']:
        raise ValueError('FFT transition changed sizes or quadrature bits')
    if before['data'].dtype!=np.dtype('float32') or after['data'].dtype!=np.dtype('float32'):
        raise ValueError('actual transition must retain float32')
    if axis in before['frequency_axes'] or set(after['frequency_axes'])!=set(before['frequency_axes'])|{axis}:
        raise ValueError('FFT transition has unexpected domains')


def _check_checkpoint(item,stage):
    if (item['data'].shape!=(2**len(stage['complex_axes']),*stage['shape_yx'])
            or item['complex_axes']!=tuple(stage['complex_axes'])
            or item['frequency_axes']!=tuple(stage['frequency_axes'])
            or item['transposed']!=stage['reference_transposed']):
        raise ValueError('input is not the declared frozen checkpoint: '+stage['id'])


def transition_metrics(before,after,axis,exponent):
    _transition_state(before,after,axis)
    pairs=_pairs(before['complex_axes'],axis)
    orthogonal=before['data'].shape[2 if axis=='a0' else 1]
    selected=sorted({0,orthogonal//2,orthogonal-1})
    full=_metric();literal_crosscheck=_metric();literal_actual=_metric()
    for real,imag in pairs:
        for start in range(0,orthogonal,TRACE_BATCH):
            selection=slice(start,min(start+TRACE_BATCH,orthogonal))
            z=_complex_traces(before['data'],real,imag,axis,selection)
            actual=_complex_traces(after['data'],real,imag,axis,selection)
            _accumulate(full,actual,numpy_oracle(z,exponent))
        z=_complex_traces(before['data'],real,imag,axis,selected)
        literal=literal_dft(z,exponent)
        _accumulate(literal_crosscheck,numpy_oracle(z,exponent),literal)
        _accumulate(literal_actual,_complex_traces(after['data'],real,imag,axis,selected),literal)
    return {'full_array_transition':_finish(full),'literal_dft_oracle_crosscheck':_finish(literal_crosscheck),
            'literal_dft_actual_subset':_finish(literal_actual),'literal_trace_indices':selected,
            'literal_coverage':'all frequency bins on fixed orthogonal traces only, every quadrature pair',
            'full_array_oracle':'complex128 NumPy FFT/IFFT, independently checked against literal DFT subset',
            'actual_precision':'float32','quadrature_pairs':[list(p) for p in pairs],
            'frequency_batch':FREQUENCY_BATCH,'trace_batch':TRACE_BATCH,'exponent':exponent}


def native_shared_reference_metrics(reference_before,axis,native_fft):
    """Run only locked native code on canonicalized reference preceding samples."""
    data=reference_before['data'];bits=reference_before['complex_axes']
    if data.dtype!=np.dtype('float32'):raise ValueError('shared input must be float32')
    orthogonal=data.shape[2 if axis=='a0' else 1]
    metric=_metric()
    for real,imag in _pairs(bits,axis):
        sign_r=(-1)**real.bit_count();sign_i=(-1)**imag.bit_count()
        for start in range(0,orthogonal,TRACE_BATCH):
            z=_complex_traces(data,real,imag,axis,slice(start,min(start+TRACE_BATCH,orthogonal)))
            positive=numpy_oracle(z,1)
            expected=positive.real*sign_r+1j*positive.imag*sign_i
            actual_input=np.empty(z.shape,dtype=np.complex64)
            actual_input.real=z.real*sign_r;actual_input.imag=z.imag*sign_i
            actual=native_fft(actual_input,1,inverse=False,threads=1)
            if actual.dtype!=np.dtype('complex64'):raise ValueError('native FFT changed precision')
            actual=np.fft.fftshift(actual,axes=1)
            _accumulate(metric,actual,expected)
    return {'shared_reference_preceding_input':True,'input_convention':'fixed independent-axis conjugation',
            'code_under_test':'locked nmrnext._native.fft_complex, complex64, one thread',
            'oracle':'positive exponent complex128 transform followed by fixed channel signs',
            'metric':_finish(metric)}


def _summary(a):
    a=np.asarray(a,dtype=np.float64)
    center=float(np.median(a))
    return {'count':a.size,'mean':float(a.mean()),'median':center,'mad_unscaled':float(np.median(np.abs(a-center))),
            'standard_deviation_population':float(a.std()),'minimum':float(a.min()),'maximum':float(a.max())}


def region_diagnostics(candidate,reference,case):
    stage=next(s for s in case['stages'] if s['id']=='S10')
    mandatory=lab_compare.compare_stage(candidate,reference,stage,case)
    a=candidate['data'][0];r=reference['data'][0]
    regions=[]
    for roi in case['rois']:
        (y0,y1),(x0,x1)=roi['inclusive_yx'];py,px=roi['reference_peak_yx']
        if not (y0<=py<=y1 and x0<=px<=x1):raise ValueError('frozen slice coordinate outside ROI')
        ca=a[y0:y1+1,x0:x1+1];ra=r[y0:y1+1,x0:x1+1]
        slices={}
        for name,actual,expected,axis,start,stop in [('horizontal',a[py,x0:x1+1],r[py,x0:x1+1],'a1',x0,x1),
                                                   ('vertical',a[y0:y1+1,px],r[y0:y1+1,px],'a0',y0,y1)]:
            residual=actual.astype(np.float64)-expected
            slices[name]={'point_indices':list(range(start,stop+1)),
                          'reference_ppm':reference['ppm'][axis][start:stop+1].tolist(),
                          'candidate_ppm':candidate['ppm'][axis][start:stop+1].tolist(),
                          'candidate_values':actual.tolist(),'reference_values':expected.tolist(),
                          'residual_values':residual.tolist(),'residual_summary':_summary(residual)}
        regions.append({'label':roi['label'],'strength_class':roi['strength_class'],'expected_signal_sign':roi['expected_sign'],
                        'candidate_positive_maximum':bool(ca.max()>0),'reference_positive_maximum':bool(ra.max()>0),
                        'candidate_summary':_summary(ca),'reference_summary':_summary(ra),
                        'residual_summary':_summary(ca.astype(np.float64)-ra),'fixed_slices':slices})
    noise=[]
    for (y0,y1),(x0,x1) in case['noise_half_open_yx']:
        if not (0<=y0<y1<=a.shape[0] and 0<=x0<x1<=a.shape[1]):raise ValueError('noise rectangle outside array')
        ca=a[y0:y1,x0:x1];ra=r[y0:y1,x0:x1]
        noise.append({'half_open_yx':[[y0,y1],[x0,x1]],'candidate':_summary(ca),'reference':_summary(ra),
                      'residual':_summary(ca.astype(np.float64)-ra)})
    return {'mandatory_existing_final_gates':mandatory,
            'supplementary_no_new_thresholds':{'regions':regions,'noise_rectangles':noise,'contours':case['contours'],
                'limitations':['Noise regions are assumed signal-free, not certified','Source labels are not verified biological assignments',
                               'Sign, slice and noise summaries add no fitted correction or new numerical acceptance gate']}}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execution-lock',required=True,type=Path)
    parser.add_argument('--report',required=True,type=Path)
    parser.add_argument('--memory-bytes',type=int,default=1024**3)
    commands=parser.add_subparsers(dest='mode',required=True)
    fft=commands.add_parser('fft')
    fft.add_argument('--axis',choices=['a0','a1'],required=True)
    for name in ('reference-before','reference-after','candidate-before','candidate-after'):
        fft.add_argument('--'+name,required=True,type=Path)
    regions=commands.add_parser('regions')
    regions.add_argument('--reference',required=True,type=Path)
    regions.add_argument('--candidate',required=True,type=Path)
    args=parser.parse_args(argv)
    if args.report.exists():parser.error('report already exists')
    lock=verify_lock(args.execution_lock)
    case=lab_compare._json(FIXTURE.read_text(encoding='utf8'))
    if case['protocol_gates']['native_fft_relative_max_tolerance']!=FFT_GATE:raise ValueError('frozen FFT gate changed')
    if args.memory_bytes<=64*1024**2:parser.error('diagnostic budget must exceed64MiB')
    limit=min(lab_compare.MAX_BYTES,(args.memory_bytes-64*1024**2)//8)
    names=('reference_before','reference_after','candidate_before','candidate_after') if args.mode=='fft' else ('reference','candidate')
    missing=[name for name in names if not getattr(args,name).is_file()]
    if missing:
        result={'status':'UNVERIFIED','missing_input_roles':missing,'acquired_comparison_executed':False}
    else:
        before_hashes={name:lab_compare.sha256(getattr(args,name),limit) for name in names}
        if args.mode=='fft':
            stage_ids=('S02','S03') if args.axis=='a1' else ('S07','S08')
            stages=[next(s for s in case['stages'] if s['id']==identifier) for identifier in stage_ids]
            before=lab_compare.read_pipe(args.reference_before,limit);after=lab_compare.read_pipe(args.reference_after,limit)
            _check_checkpoint(before,stages[0]);_check_checkpoint(after,stages[1])
            ref=transition_metrics(before,after,args.axis,1)
            shared=native_shared_reference_metrics(before,args.axis,locked_native(lock,args.execution_lock))
            del before,after
            before=lab_compare.read_native(args.candidate_before,limit);after=lab_compare.read_native(args.candidate_after,limit)
            _check_checkpoint(before,stages[0]);_check_checkpoint(after,stages[1])
            candidate=transition_metrics(before,after,args.axis,-1)
            del before,after
            statuses=[ref[k]['status'] for k in ('full_array_transition','literal_dft_oracle_crosscheck','literal_dft_actual_subset')]
            statuses += [candidate[k]['status'] for k in ('full_array_transition','literal_dft_oracle_crosscheck','literal_dft_actual_subset')]
            statuses.append(shared['metric']['status'])
            result={'status':'FAIL' if 'FAIL' in statuses else 'UNVERIFIED' if 'UNVERIFIED' in statuses else 'PASS',
                    'mandatory_fft_gate':FFT_GATE,'axis':args.axis,'checkpoint_ids':stage_ids,'reference_transition':ref,'candidate_transition':candidate,
                    'isolated_native_shared_input':shared,'scope':'isolated FFT checks only; overall laboratory gate remains UNVERIFIED'}
        else:
            result=region_diagnostics(lab_compare.read_native(args.candidate,limit),lab_compare.read_pipe(args.reference,limit),case)
            result['status']='PASS' if result['mandatory_existing_final_gates']['pass'] else 'FAIL'
            result['scope']='Existing final numeric gates plus supplementary diagnostics; overall laboratory gate remains UNVERIFIED'
        after_hashes={name:lab_compare.sha256(getattr(args,name),limit) for name in names}
        if before_hashes!=after_hashes:raise ValueError('diagnostic inputs changed during read')
        result['input_sha256']=after_hashes
        result['acquired_comparison_executed']=True
    result.update({'execution_lock_sha256':lab_compare.sha256(args.execution_lock),'diagnostics_sha256':lab_compare.sha256(__file__),
                   'case_id':case['case_id'],'working_array_budget_bytes':args.memory_bytes,'timing_scope':'diagnostic only, excluded from benchmark'})
    with args.report.open('x',encoding='utf8') as f:json.dump(result,f,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps({'status':result['status'],'scope':result.get('scope','no acquired comparison')}))
    return 0 if result['status']=='PASS' else 2 if result['status']=='UNVERIFIED' else 1


if __name__=='__main__':raise SystemExit(main())
