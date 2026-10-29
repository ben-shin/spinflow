"""Transient delegated-cgroup keeper; no case workloads."""
import json,os,time
from pathlib import Path
unit='spinflow-lab-s15-measure-v1.service'
expected='/system.slice/'+unit
actual=Path('/proc/self/cgroup').read_text().strip().split('::',1)[1]
assert actual==expected,(actual,expected)
parent=Path('/sys/fs/cgroup')/actual.lstrip('/')
assert parent.resolve()==parent and parent.stat().st_uid==0
observer=parent/'observer';observer.mkdir()
(observer/'cgroup.procs').write_text(str(os.getpid()))
assert not (parent/'cgroup.procs').read_text().strip()
(parent/'cgroup.subtree_control').write_text('+cpu +memory +io +pids')
controllers=(parent/'cgroup.subtree_control').read_text().split()
assert {'cpu','memory','io','pids'}<=set(controllers)
record={'status':'READY','unit':unit,'parent':str(parent),'observer':str(observer),'keeper_pid':os.getpid(),'controllers':controllers,'cpu_max':(parent/'cpu.max').read_text().strip(),'memory_max':(parent/'memory.max').read_text().strip(),'guest_cpu_count':os.cpu_count(),'guest_affinity':sorted(os.sched_getaffinity(0)),'scope':'Ephemeral systemd delegated service, observer leaf and sibling workload leaves. No persistent unit or host settings. Shared one-CPU guest, no added CPU quota or memory cap.'}
assert record['guest_cpu_count']==1 and record['guest_affinity']==[0]
with Path('/home/validator/lab-evidence/delegated-cgroup-setup.json').open('x') as f:json.dump(record,f,indent=2);f.write('\n')
while True:time.sleep(30)
