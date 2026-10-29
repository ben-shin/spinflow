import hashlib,json,subprocess
from pathlib import Path
home=Path.home();e=home/'lab-evidence';sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest();rec=lambda p:{'path':str(Path(p).resolve()),'sha256':sha(p)}
base=e/'benchmark-execution-lock-v4-delegated-base.json';lock=json.loads(base.read_text());priorpath=e/'benchmark-execution-lock-v4-regular.json';prior=json.loads(priorpath.read_text())
assert all(not Path(t['output']).exists() for t in lock['trials'])
lock['status']='SEALED_NOT_EXECUTED_REQUIRES_ROOT_GO';lock['base_job_lock']=rec(base);lock['supersedes_setup_only']=rec(priorpath)
for key in ['measurement_addendum','output_validation_addendum','v4_semantic_update','regular_input_setup']:lock[key]=prior[key]
lock['sampler']=rec(home/'tree_sampler_delegated.py');lock['sampler_calibration']=rec(e/'sampler-io-calibration-delegated-v1.json')
lock['fixed_files'] += [rec(home/p) for p in ['io_sentinel.c','io_sentinel','sampler_delegated_calibration.py','sampler_delegated_orphan_calibration.py','benchmark_output_identity.py','preflight_delegated_v4.py','cgroup_keeper.py','seal_benchmark_delegated_v4.py']]+[rec(e/p) for p in ['sampler-orphan-delegated-calibration.json','export-determinism-evidence.json','ucsf-endpoint-validation-v4.json','signed-array-preservation-v4.json','benchmark-delegated-v4-preflight.json','desktop-v4-closed-before-benchmark.json','delegated-cgroup-setup.json','delegated-cgroup-state.json','benchmark-prelaunch-diagnosis.json','benchmark-results-v4-regular.json','empty-owned-cgroup-cleanup.json']]
for trial,old in zip(lock['trials'],prior['trials']):
 assert all(trial[k]==old[k] for k in ['id','endpoint','kind','pair','lane','expected_output_sha256'])
 trial['expected_output_identity']=old['expected_output_identity']
lock['regular_input_setup']['preflight']=rec(e/'benchmark-delegated-v4-preflight.json')
lock['delegated_measurement_setup']={'reason':'Previous attempt failed before first workload launch because systemd disabled root io controller. Zero timing observations, zero workloads/outputs; prior failure retained.',
 'unit':'spinflow-lab-s15-measure-v1.service','parent':'/sys/fs/cgroup/system.slice/spinflow-lab-s15-measure-v1.service','observer_leaf':'observer','workload_leaf_prefix':'workload-',
 'semantics':'Transient guest-only delegated service keeps cpu/io/memory/pids active; observer+idle keeper are outside each workload sibling. One guest CPU0, host QEMU logicalCPU2, 4GiB guest, no quota/cap/cache/precision/schedule change. Keeper sleeps30s; no overhead subtraction. Unit removed after completion.',
 'calibration':[rec(e/'sampler-io-calibration-delegated-v1.json'),rec(e/'sampler-orphan-delegated-calibration.json')],'state':rec(e/'delegated-cgroup-state.json'),'previous_attempt':rec(e/'benchmark-results-v4-regular.json'),'prior_workloads_launched':0,'trials':32}
# This is the active calibration/sampler authority; previous addendum records are retained only as historical contract.
lock['measurement_addendum']['delegated_hierarchy_superseding_measurement']=lock['delegated_measurement_setup']
path=e/'benchmark-execution-lock-v4-delegated.json'
with path.open('x') as f:json.dump(lock,f,indent=2);f.write('\n')
print(json.dumps({'status':'SEALED_NOT_EXECUTED','path':str(path),'sha256':sha(path),'trials':len(lock['trials']),'staged_inputs':64}))
