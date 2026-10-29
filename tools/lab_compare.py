"""Independent, bounded decoder/comparator for the preregistered 2D lab case.

No nmrglue or SpinFlow processing imports. CLI refuses case reads until an
external execution lock binds the exact protocol, fixture, harness and evidence.
This is diagnostic instrumentation, never a benchmark timer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct

import h5py
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 256 * 1024**2
AXES = ('a0', 'a1')
# Public NMRPipe fdata header indices, interpreted independently here.
FIELDS = {'a0': {'quad':55,'ft':222,'sw':229,'obs':218,'orig':249,'p0':245,'p1':246,'label':72},
          'a1': {'quad':56,'ft':220,'sw':100,'obs':119,'orig':101,'p0':109,'p1':110,'label':64}}


def sha256(path, max_bytes=None):
    digest = hashlib.sha256()
    count=0
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024**2), b''):
            count+=len(chunk)
            if max_bytes is not None and count>max_bytes:
                raise ValueError('hash input outside byte limit')
            digest.update(chunk)
    return digest.hexdigest()


def _json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('nonfinite JSON constant: '+value)
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def _integer(value, name, allowed=None):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value != int(value):
        raise ValueError(f'invalid integer {name}')
    result = int(value)
    if (allowed is not None and result not in allowed) or (allowed is None and result < 1):
        raise ValueError(f'unsupported {name}')
    return result


def read_pipe(path, max_bytes=MAX_BYTES):
    """Read only bounded 2D single-file data; return canonical physical axes.

    Current X stores contiguous real then imaginary halves; current Y stores
    interleaved quadrature traces. FDQUADFLAG/FDSPECNUM's real-X exception is
    handled explicitly. No layout is inferred from candidate correlation.
    """
    path = Path(path)
    size = path.stat().st_size
    if not 2048 <= size <= max_bytes:
        raise ValueError('NMRPipe file outside byte limit')
    with path.open('rb') as stream:
        raw = stream.read(2048)
        orders = [o for o in ('<','>') if abs(struct.unpack(o+'f',raw[8:12])[0]-2.345)<1e-5]
        if len(orders) != 1:
            raise ValueError('invalid byte-order marker')
        order = orders[0]
        h = struct.unpack(order+'512f',raw)
        _integer(h[9],'dimension count',{2})
        transposed = _integer(h[221],'transpose flag',{0,1})
        dimorder = [_integer(h[i],'dimension order',{1,2}) for i in (24,25)]
        if dimorder != ([1,2] if transposed else [2,1]):
            raise ValueError('inconsistent physical dimension order')
        if h[45] != 0:
            raise ValueError('NUS is outside the frozen profile')
        complex_axes = tuple(a for a in AXES if _integer(h[FIELDS[a]['quad']],a+' quad flag',{0,1}) == 0)
        global_quad = _integer(h[106],'global quad flag',{0,1})
        if global_quad != (0 if complex_axes else 1):
            raise ValueError('inconsistent global quadrature flag')
        current_x,current_y = ('a0','a1') if transposed else ('a1','a0')
        qx,qy = (2 if a in complex_axes else 1 for a in (current_x,current_y))
        nx = _integer(h[99],'X size')
        rows = _integer(h[219],'spectrum count')
        if global_quad == 0 and qx == 1:
            rows *= 2
        if rows % qy:
            raise ValueError('incomplete Y quadrature pairs')
        ny = rows//qy
        expected = 2048+4*rows*nx*qx
        if size != expected:
            raise ValueError('payload size does not match header')
        payload=stream.read(expected-2048)
        if len(payload)!=expected-2048 or stream.read(1):
            raise ValueError('input changed during read')
        values = np.frombuffer(payload, dtype=order+'f4').reshape(rows,nx*qx)
    if not np.isfinite(values).all():
        raise ValueError('nonfinite payload')
    channels=[]
    for c in range(2**len(complex_axes)):
        bits={a:(c>>complex_axes.index(a))&1 for a in complex_axes}
        part=values[bits.get(current_y,0)::qy, bits.get(current_x,0)*nx:(bits.get(current_x,0)+1)*nx]
        channels.append(part.T if transposed else part)
    data=np.stack(channels).astype(np.float32,copy=False)
    ppm={}; frequency_axes=[]; parameters={}
    for axis in AXES:
        f=FIELDS[axis]
        ft=_integer(h[f['ft']],axis+' FT flag',{0,1})
        selected={name:h[f[name]] for name in ('sw','obs','orig','p0','p1')}
        if not all(math.isfinite(v) for v in selected.values()) or selected['sw']<=0 or selected['obs']<=0:
            raise ValueError('invalid spectral calibration')
        selected['label']=raw[f['label']:f['label']+8].split(b'\0',1)[0].decode('ascii').strip()
        parameters[axis]=selected
        if ft:
            frequency_axes.append(axis)
            n=data.shape[1+AXES.index(axis)]
            ppm[axis]=(selected['orig']+(n-1-np.arange(n))*selected['sw']/n)/selected['obs']
    return {'data':data,'complex_axes':complex_axes,'frequency_axes':tuple(frequency_axes),
            'ppm':ppm,'parameters':parameters,'transposed':bool(transposed)}


def read_native(path, max_bytes=MAX_BYTES):
    """Decode the native container directly, without production model/reader."""
    if Path(path).stat().st_size>max_bytes:
        raise ValueError('native file outside byte limit')
    with h5py.File(path,'r') as h:
        if h.attrs.get('schema_version') != '1' or not isinstance(h.get('data', getlink=True), h5py.HardLink):
            raise ValueError('unsupported native schema/link')
        text=h.attrs.get('metadata_json','')
        if not isinstance(text,str) or len(text.encode('utf8'))>1024**2:
            raise ValueError('metadata outside limit')
        m=_json(text)
        if m.get('schema_version')!='1' or set(m)!={'schema_version','axes','complex_axes','metadata','provenance'}:
            raise ValueError('unsupported native metadata schema')
        axes=m['axes']; ids=tuple(a['id'] for a in axes); bits=tuple(m['complex_axes'])
        if len(ids)!=2 or set(ids)!=set(AXES) or len(set(bits))!=len(bits) or not set(bits)<=set(AXES):
            raise ValueError('unsupported native axes')
        d=h['data']
        sizes=[_integer(a['size'],'native size') for a in axes]
        for a in axes:
            if (a['role']!='spectral' or a['domain'] not in ('time','frequency')
                    or a['unit']!=('s' if a['domain']=='time' else 'Hz')
                    or a['encoding'] not in (('complex','states') if a['id'] in bits else ('real','none'))):
                raise ValueError('unsupported native axis profile')
            if not all(math.isfinite(a[k]) for k in ('start','step','reference_hz')) or a['step']==0:
                raise ValueError('invalid native coordinates')
        if (not isinstance(d,h5py.Dataset) or d.is_virtual or d.external or d.dtype not in (np.dtype('float32'),np.dtype('float64'))
                or d.shape!=(2**len(bits),*sizes) or d.size*d.dtype.itemsize>max_bytes):
            raise ValueError('unsupported native payload')
        data=d[()]
    if not np.isfinite(data).all():
        raise ValueError('nonfinite native payload')
    new_bits=tuple(a for a in AXES if a in bits)
    indices=[sum(((c>>new_bits.index(a))&1)<<bits.index(a) for a in bits) for c in range(2**len(bits))]
    data=np.transpose(data[indices],(0,1+ids.index('a0'),1+ids.index('a1')))
    ppm={}; frequency_axes=[]
    for a in axes:
        if a['domain']=='frequency':
            frequency_axes.append(a['id'])
            if a.get('reference_ppm') is not None and a.get('observation_mhz') is not None:
                if a['observation_mhz']<=0:
                    raise ValueError('invalid native frequency')
                ppm[a['id']]=a['reference_ppm']+(a['start']+np.arange(a['size'])*a['step']-a['reference_hz'])/a['observation_mhz']
    return {'data':data,'complex_axes':new_bits,'frequency_axes':tuple(a for a in AXES if a in frequency_axes),
            'ppm':ppm,'metadata':m,'transposed':ids==('a1','a0')}


def canonical_reference(reference):
    """Fixed independent-axis conjugation; never selected from measured data."""
    signs=np.array([(-1)**c.bit_count() for c in range(len(reference['data']))],dtype=np.float32)
    return reference['data']*signs[:,None,None]


def compare_stage(candidate, reference, stage, case):
    expected_shape=(2**len(stage['complex_axes']),*stage['shape_yx'])
    for label, item in (('candidate',candidate),('reference',reference)):
        if item['data'].shape!=expected_shape or item['complex_axes']!=tuple(stage['complex_axes']):
            raise ValueError(label+' shape/quadrature mismatch')
        if item['data'].dtype!=np.dtype('float32'):
            raise ValueError(label+' precision mismatch')
        if item['frequency_axes']!=tuple(stage['frequency_axes']):
            raise ValueError(label+' frequency axes mismatch')
    if reference['transposed']!=stage['reference_transposed']:
        raise ValueError('reference storage orientation mismatch')
    # Compare bounded signed chunks; never allocate whole float64 spectra.
    actual=candidate['data']; target=reference['data']
    maximum=denominator=delta_squared=target_squared=0.0
    exact=True
    candidate_min=target_min=math.inf; candidate_max=target_max=-math.inf
    for channel in range(len(actual)):
        sign=(-1)**channel.bit_count()
        for start in range(0,actual[channel].size,262144):
            a=actual[channel].flat[start:start+262144].astype(np.float64)
            r=target[channel].flat[start:start+262144].astype(np.float64)*sign
            delta=a-r
            maximum=max(maximum,float(np.max(np.abs(delta))))
            denominator=max(denominator,float(np.max(np.abs(r))))
            delta_squared+=float(np.dot(delta,delta));target_squared+=float(np.dot(r,r))
            exact &= bool(np.array_equal(a,r))
            candidate_min=min(candidate_min,float(a.min()));candidate_max=max(candidate_max,float(a.max()))
            target_min=min(target_min,float(r.min()));target_max=max(target_max,float(r.max()))
    relative=maximum/denominator if denominator else None
    passed=exact if stage['id']=='S00' else relative is not None and relative<=stage['relative_max_gate']
    result={'stage':stage['id'],'pass':bool(passed),'exact':exact,'relative_max_error':relative,
            'maximum_absolute_error':maximum,
            'relative_l2_error':math.sqrt(delta_squared/target_squared) if target_squared else None,
            'reference_minmax':[target_min,target_max],
            'candidate_minmax':[candidate_min,candidate_max]}
    result['reference_header_parameters']=reference.get('parameters',{})
    result['candidate_phase_history']=[entry['operation'] for entry in candidate.get('metadata',{}).get('provenance',[])
                                       if isinstance(entry,dict) and isinstance(entry.get('operation'),dict)
                                       and entry['operation'].get('op')=='phase']
    result['phase_comparison']='fixed signed pre/post-phase arrays; no residual phase fitting'
    if stage['id']=='S10':
        tolerance=case['protocol_gates']['physical_coordinate_tolerance_ppm']
        coordinate_errors={}
        for axis in AXES:
            if axis not in candidate['ppm'] or axis not in reference['ppm']:
                raise ValueError('missing absolute ppm grid')
            error=float(np.max(np.abs(candidate['ppm'][axis]-reference['ppm'][axis])))
            if not math.isfinite(error):
                raise ValueError('nonfinite ppm error')
            coordinate_errors[axis]=error
        result['ppm_max_errors']=coordinate_errors
        result['pass'] &= all(e<=tolerance for e in coordinate_errors.values())
        result['global_peak_bin_equal']=np.unravel_index(np.argmax(actual[0]),actual[0].shape)==np.unravel_index(np.argmax(target[0]),target[0].shape)
        result['pass'] &= result['global_peak_bin_equal']
        result['rois']=[]
        for roi in case['rois']:
            (y0,y1),(x0,x1)=roi['inclusive_yx']
            if not (0<=y0<=y1<actual.shape[1] and 0<=x0<=x1<actual.shape[2]):
                raise ValueError('ROI outside array')
            a=actual[0,y0:y1+1,x0:x1+1]; r=target[0,y0:y1+1,x0:x1+1]
            refsum=float(r.sum(dtype=np.float64)); cansum=float(a.sum(dtype=np.float64))
            err=abs(cansum-refsum)/abs(refsum) if refsum else None
            peak_equal=np.unravel_index(np.argmax(a),a.shape)==np.unravel_index(np.argmax(r),r.shape)
            ok=err is not None and err<=case['protocol_gates']['processed_window_sum_relative_tolerance'] and peak_equal
            result['rois'].append({'label':roi['label'],'strength_class':roi['strength_class'],'reference_sum':refsum,
                'candidate_sum':cansum,'signed_sum_relative_error':err,'peak_bin_equal':peak_equal,
                'candidate_minmax':[float(a.min()),float(a.max())],'reference_minmax':[float(r.min()),float(r.max())],'pass':bool(ok)})
            result['pass'] &= bool(ok)
    return result


def verify_execution_lock(path, protocol, fixture):
    lock=_json(Path(path).read_text(encoding='utf8'))
    if lock.get('schema_version')!=1 or lock.get('status')!='SEALED':
        raise ValueError('execution lock is not sealed')
    for key,actual in [('protocol_sha256',protocol),('case_fixture_sha256',fixture),('harness_sha256',Path(__file__)),
                       ('candidate_driver_sha256',ROOT/'tools/lab_candidate.py'),
                       ('candidate_recipe_sha256',ROOT/'examples/laboratory-workflow.json')]:
        if not lock.get(key) or lock[key]!=sha256(actual):
            raise ValueError('execution lock mismatch: '+key)
    required=('candidate_source_and_binary_hashes','actual_reference_version_and_binary_hashes','actual_consumer_binary_hashes',
              'dependency_runtime_versions','original_script_hashes','captured_actual_help_hashes',
              'analytic_ft_auto_semantics_record','header_gui_prerequisite_evidence','hardware_and_resource_controls','benchmark_trial_schedule')
    # Each evidence item is an immutable file record, not an unchecked prose claim.
    for key in required:
        entries=lock.get(key)
        if not isinstance(entries,list) or not entries:
            raise ValueError('missing lock evidence: '+key)
        for entry in entries:
            p=Path(entry['path'])
            if not p.is_absolute():
                p=Path(path).resolve().parent/p
            if sha256(p)!=entry['sha256']:
                raise ValueError('lock evidence hash mismatch: '+key)
    return lock


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execution-lock',required=True,type=Path)
    parser.add_argument('--fixture',type=Path,default=ROOT/'tests/fixtures/lab_workflow_case.json')
    parser.add_argument('--protocol',type=Path,default=ROOT/'docs/laboratory-workflow-protocol.md')
    parser.add_argument('--stage',required=True)
    parser.add_argument('--candidate',required=True,type=Path)
    parser.add_argument('--candidate-format',choices=['native','nmrpipe-real'],default='native')
    parser.add_argument('--reference',required=True,type=Path)
    parser.add_argument('--discard-reference',type=Path,help='Original S04 reference required for S03P side-branch projection check')
    parser.add_argument('--report',required=True,type=Path)
    parser.add_argument('--memory-bytes',type=int,default=512*1024**2,
                        help='Diagnostic working-array budget; excludes interpreter/library overhead')
    args=parser.parse_args()
    if args.report.exists():
        parser.error('report already exists')
    verify_execution_lock(args.execution_lock,args.protocol,args.fixture)
    case=_json(args.fixture.read_text(encoding='utf8'))
    stage=next((s for s in case['stages'] if s['id']==args.stage),None)
    if args.stage=='S03P':
        stage={**next(s for s in case['stages'] if s['id']=='S03'),'id':'S03P'}
        if args.discard_reference is None:
            parser.error('S03P requires --discard-reference pointing to original S04')
    if stage is None:
        parser.error('stage not in frozen fixture')
    if args.candidate_format=='nmrpipe-real' and args.stage!='S10':
        parser.error('processed real NMRPipe candidate is valid only at S10')
    if args.memory_bytes<=32*1024**2:
        parser.error('memory budget must exceed the 32 MiB diagnostic allowance')
    payload_limit=min(MAX_BYTES,(args.memory_bytes-32*1024**2)//6)
    inputs=[('candidate',args.candidate),('reference',args.reference)]
    if args.discard_reference is not None:
        inputs.append(('discard_reference',args.discard_reference))
    before={name:sha256(p,payload_limit) for name,p in inputs}
    candidate_reader=read_native if args.candidate_format=='native' else read_pipe
    candidate=candidate_reader(args.candidate,payload_limit)
    reference=read_pipe(args.reference,payload_limit)
    result=compare_stage(candidate,reference,stage,case)
    if args.stage=='S03P':
        del candidate
        projected={**reference,'data':canonical_reference(reference)[:2], 'complex_axes':('a0',)}
        s04=next(s for s in case['stages'] if s['id']=='S04')
        projection=compare_stage(projected,read_pipe(args.discard_reference,payload_limit),s04,case)
        result['reference_side_branch_projection_to_S04']=projection
        result['pass'] &= projection['pass']
    after={name:sha256(p,payload_limit) for name,p in inputs}
    if before!=after:
        raise ValueError('input changed during comparison')
    result.update({'case_id':case['case_id'],'execution_lock_sha256':sha256(args.execution_lock),
        'candidate_sha256':after['candidate'],'reference_sha256':after['reference'],
        'discard_reference_sha256':after.get('discard_reference'),
        'working_array_budget_bytes':args.memory_bytes,'payload_limit_per_input':payload_limit,
        'scope':'stage comparison only; overall laboratory gate remains UNVERIFIED until complete evidence'})
    with args.report.open('x',encoding='utf8') as f:
        json.dump(result,f,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps({'stage':args.stage,'pass':result['pass']}))
    return 0 if result['pass'] else 1


if __name__=='__main__':
    raise SystemExit(main())
