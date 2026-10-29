import hashlib,json,os,struct,subprocess
from pathlib import Path
from nmrnext.io import describe_dataset
home=Path.home();e=home/'lab-evidence';base=e/'benchmark-execution-lock-v4-delegated-base.json';lock=json.loads(base.read_text());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
records=[];env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
for trial in lock['trials']:
    for item in trial['staged_inputs']:
        p=Path(item['path']);assert not p.is_symlink() and p.stat().st_mode & 0o777==item['mode']==0o444 and sha(p)==item['sha256']
    assert not Path(trial['output']).exists()
for endpoint in ['raw_to_nmrpipe_real','raw_to_ucsf_real']:
    native=next(t for t in lock['trials'] if t['endpoint']==endpoint and t['kind']=='warmup' and t['lane']=='B')
    recipe=json.loads(Path(native['command'][-1]).read_text())
    assert recipe['memory_bytes']==1024**3 and recipe['backend']=='native' and recipe['threads']==1
    widths={op['axis']:op['spectral_width_hz'] for op in recipe['operations'] if op['op']=='reference'};assert widths=={'a0':5555.556,'a1':50000}
    cmd=[str(home/'lab-runtime-v4/bin/python'),'-m','nmrnext','plan',native['command'][-1]]
    p=subprocess.run(cmd,cwd=native['cwd'],env=env,capture_output=True,text=True,timeout=30)
    (e/(endpoint+'-delegated-preflight.stdout')).write_text(p.stdout);(e/(endpoint+'-delegated-preflight.stderr')).write_text(p.stderr)
    assert p.returncode==0,p.stderr
    plan=json.loads(p.stdout);assert plan['input_validation']=='header_only' and plan['estimated_working_bytes']==813484672 and plan['memory_bytes']==1024**3 and plan['output_shape']==[1,2048,4096]
    reference=next(t for t in lock['trials'] if t['endpoint']==endpoint and t['kind']=='warmup' and t['lane']=='A')
    raw=Path(reference['cwd'])/'agilent_2d';header=describe_dataset(raw,format='varian-raw-2d')
    with (raw/'fid').open('rb') as f:fields=struct.unpack('>6i2hi',f.read(32))
    assert header.shape==(4,166,1500)
    records.append({'endpoint':endpoint,'native_command':cmd,'native_cwd':native['cwd'],'returncode':p.returncode,'plan':plan,
       'explicit_widths':widths,'reference_staged_input':str(raw),'reference_fid_header_big_endian_6i2hi':list(fields),'reference_descriptor_shape':list(header.shape),
       'reference_original_scripts_unchanged':all(sha(r['path'])==r['sha256'] for r in lock['fixed_files'] if r['path'].endswith('.com'))})
assert all(not Path(t['output']).exists() for t in lock['trials'])
result={'status':'PASS','base_lock_sha256':sha(base),'all_staged_inputs_hash_mode_verified':64,'regular_read_only_inputs_both_lanes':True,'all32outputs_absent':True,'header_only_no_spectrum_processing':True,'endpoints':records}
with (e/'benchmark-delegated-v4-preflight.json').open('x') as f:json.dump(result,f,indent=2);f.write('\n')
print(json.dumps({'status':'PASS','native_preflights':2,'reference_headers':2,'regular_inputs':64,'outputs_created':0}))
