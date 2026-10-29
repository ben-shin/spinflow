"""Exact one-CPU trial runner. Root authorizes separately after hash-lock review."""
import argparse,hashlib,json,os,subprocess,sys,time
from pathlib import Path
from tree_sampler_delegated import meter
from benchmark_output_identity import identity,assert_same
home=Path('/home/validator');e=home/'lab-evidence'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
parser=argparse.ArgumentParser();parser.add_argument('--execute-after-root-benchmark-go',required=True,action='store_true');args=parser.parse_args()
lockpath=e/'benchmark-execution-lock-v4-delegated.json';lock=json.loads(lockpath.read_text())
assert os.geteuid()==0 and os.sched_getaffinity(0)=={0} and os.cpu_count()==1
assert lock['runner']['sha256']==sha(__file__)
for key in ['scientific_lock','schedule','sampler','sampler_calibration','preparer']:
    item=lock[key];assert sha(item['path'])==item['sha256'],key
for item in lock['fixed_files']+lock['case_source_files']:assert sha(item['path'])==item['sha256'],item['path']
science=json.loads(Path(lock['scientific_lock']['path']).read_text());bound={}
for value in science.values():
    if isinstance(value,list):
        for item in value:
            if isinstance(item,dict) and 'path' in item:bound[item['path']]=item['sha256']
def rehash():
    actual={p:sha(p) for p in bound};assert actual==bound
    return hashlib.sha256(json.dumps(actual,sort_keys=True).encode()).hexdigest()
processes=[]
for p in Path('/proc').iterdir():
    if not p.name.isdigit():continue
    try:
        cmd=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace');comm=(p/'comm').read_text().strip()
    except (FileNotFoundError,PermissionError,ProcessLookupError):continue
    assert comm not in ['Xvfb','openbox','nmrdraw64.exe'],(p.name,comm)
    assert '/lab-private/sparky/python2.5/' not in cmd,(p.name,cmd)
    if cmd:processes.append({'pid':int(p.name),'comm':comm,'command':cmd})
out=e/'benchmark-v4-delegated';out.mkdir()
(out/'idle-process-state.json').write_text(json.dumps(processes,indent=2)+'\n')
records=[];failure=None;remaining=[]
for index,trial in enumerate(lock['trials']):
    try:
        assert not Path(trial['output']).exists()
        for item in trial['staged_inputs']:
            p=Path(item['path']);assert p.is_file() and not p.is_symlink() and sha(p)==item['sha256'] and p.stat().st_mode & 0o777==item['mode']
        before=rehash()
        result=meter(trial['command'],trial['cwd'],out/(trial['id']+'.json'),timeout=180,sample_seconds=.005,final_output=trial['output'])
        after=rehash();assert before==after
        for item in trial['staged_inputs']:assert sha(item['path'])==item['sha256']
        assert result['status']=='PASS',trial['id']
        output_identity=identity(trial['output'],trial['endpoint']);assert_same(output_identity,trial['expected_output_identity'])
        record={**trial,'measurement':result,'output_identity':output_identity,'output_sha256':output_identity['raw_sha256'],'raw_hash_matches_prior_science_product':output_identity['raw_sha256']==trial['expected_output_sha256'],'actual_runtime_files_rehashed_before_and_after':len(bound),'runtime_hash_map_digest':after}
        records.append(record)
        (out/(trial['id']+'-integrity.json')).write_text(json.dumps({k:v for k,v in record.items() if k!='measurement'},indent=2)+'\n')
        print(json.dumps({'id':trial['id'],'wall_seconds':result['wall_seconds'],'tree_cpu_seconds':result['cgroup_full_tree_cpu']['usage_usec']/1e6,'status':'PASS'}),flush=True)
    except Exception as exc:
        failure={'id':trial['id'],'status':'FAILED_PRESERVED','error':repr(exc)}
        remaining=[{'id':t['id'],'status':'NOT_RUN'} for t in lock['trials'][index+1:]]
        break
for item in lock['case_source_files']:assert sha(item['path'])==item['sha256']
result={'status':'FAIL' if failure else 'PASS','benchmark_lock_sha256':sha(lockpath),'science_lock_sha256':sha(lock['scientific_lock']['path']),'records':records,'source_unchanged':True,'failure':failure,'remaining_trials':remaining}
(e/'benchmark-results-v4-delegated.json').write_text(json.dumps(result,indent=2)+'\n')
raise SystemExit(1 if failure else 0)
