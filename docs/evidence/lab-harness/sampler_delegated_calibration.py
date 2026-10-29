import hashlib,json,os,sys
from pathlib import Path
from tree_sampler_delegated import meter,workspace_files
home=Path('/home/validator');e=home/'lab-evidence';work=home/'lab-sampler-io-calibration-delegated-v1';work.mkdir();os.chown(work,1000,1000)
external=home/'lab-sampler-io-external-delegated-v1.bin'
with external.open('xb') as f:f.truncate(64*1024**2)
(work/'external-raw-link').symlink_to(external)
script=work/'synthetic.py'
script.write_text('''import json,os,time
from pathlib import Path
root=Path.cwd();MiB=1024**2
def job(name):
 data=bytearray(16*MiB)
 for i in range(0,len(data),4096):data[i]=7
 path=root/(name+'.temporary')
 with path.open('wb') as f:f.write(data[:4*MiB]);f.flush();os.fsync(f.fileno())
 with path.open('rb') as f:assert len(f.read())==4*MiB
 time.sleep(.15)
 path.unlink()
 (root/(name+'.pid')).write_text(str(os.getpid()))
children=[]
for i in range(2):
 pid=os.fork()
 if pid==0:
  if i==0:
   grand=os.fork()
   if grand==0:job('grandchild');os._exit(0)
  job('child'+str(i))
  if i==0:os.waitpid(grand,0)
  os._exit(0)
 children.append(pid)
for pid in children:assert os.waitpid(pid,0)[1]==0
# Deliberately short-lived burst demonstrates why process/temporary samples are not exact.
fast=os.fork()
if fast==0:
 p=root/'short-lived.tmp'
 with p.open('wb') as f:f.write(b'x'*(2*MiB));f.flush();os.fsync(f.fileno())
 p.unlink();os._exit(0)
assert os.waitpid(fast,0)[1]==0
with (root/'final.bin').open('wb') as f:f.write(b'z'*(3*MiB));f.flush();os.fsync(f.fileno())
print(json.dumps({'worker_pids':[int(p.read_text()) for p in root.glob('*.pid')],'fast_pid':fast,'known_written_file_bytes':17*MiB,'held_temporary_peak_bytes':12*MiB,'final_bytes':3*MiB,'external_symlink_target_bytes':64*MiB}))
''')
report=meter(['/usr/bin/python3',str(script)],str(work),e/'sampler-io-calibration-delegated-v1-measurement.json',timeout=30,final_output=work/'final.bin')
assert report['status']=='PASS'
assert report['cgroup_full_tree_cpu']['usage_usec']>0
assert report['cgroup_peak_memory_charge_bytes']>=16*1024**2
waited=report['rusage_waited_tree_user_seconds']+report['rusage_waited_tree_system_seconds']
assert abs(report['cgroup_full_tree_cpu']['usage_usec']/1e6-waited)<.05
truth=json.loads((e/'sampler-io-calibration-delegated-v1-measurement.stdout').read_text().splitlines()[0])
assert report['max_simultaneously_observed_processes']>=4
assert report['disk']['final_output_bytes']['logical']==truth['final_bytes']
assert report['disk']['sampled_peak_bytes']['temporary_logical']>=truth['held_temporary_peak_bytes']
assert report['disk']['sampled_peak_bytes']['total_logical']<truth['external_symlink_target_bytes']
assert not any('external-raw-link' in p for p in report['disk']['final_workspace_files'])
assert report['IO']['sampled_tree_logical_counter_lower_bounds']['rchar']>=4*1024**2
block_written=sum(r.get('wbytes',0) for r in report['IO']['cgroup_virtual_block_by_device'].values())
assert block_written>=truth['known_written_file_bytes'],block_written
inclusive=report['IO']['waited_tree_inclusive_counters']
assert truth['known_written_file_bytes'] <= inclusive['wchar'] <= truth['known_written_file_bytes']+4096
assert inclusive['rchar']>=12*1024**2
assert inclusive['command_returncode']==0
observed={int(p.split(':')[0]) for p in report['IO']['sampled_process_identities']}
# Explicitly show that an entirely between-snapshot create/delete is invisible.
before=workspace_files(work);transient=work/'unobservable-between-samples';transient.write_bytes(b'x'*1024**2);transient.unlink();after=workspace_files(work)
assert before==after
result={'status':'PASS','synthetic_only':True,'sampler_sha256':hashlib.sha256((home/'tree_sampler_delegated.py').read_bytes()).hexdigest(),
 'calibration_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'workload_sha256':hashlib.sha256(script.read_bytes()).hexdigest(),
 'truth':truth,'measurement':report,'fast_process_observed':truth['fast_pid'] in observed,
 'waited_tree_inclusive_write_bytes_verified':True,'sentinel_binary_sha256':report['io_sentinel_sha256'],
 'known_limitation_demonstrated':'A 1 MiB create/delete entirely between explicit workspace snapshots leaves identical snapshots. Process counter sums also cannot recover already reaped unobserved descendants; fast-burst actual observation recorded without claiming completeness.',
 'disk_io_scope':'Synthetic uses fsync to calibrate device counters only; actual benchmark has normal close and NO added fsync/cache manipulation.'}
(e/'sampler-io-calibration-delegated-v1.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'status':'PASS','fast_process_observed':result['fast_process_observed'],'virtual_block_written':block_written,'sampled_logical_wchar_lower_bound':report['IO']['sampled_tree_logical_counter_lower_bounds']['wchar']}))
