import hashlib,json,os
from pathlib import Path
from tree_sampler_delegated import meter
home=Path('/home/validator');e=home/'lab-evidence';work=home/'lab-sampler-orphan-delegated-calibration';work.mkdir();os.chown(work,1000,1000)
script=work/'orphan.py'
script.write_text('''import os,time
from pathlib import Path
child=os.fork()
if child==0:
 time.sleep(.08)
 with Path('final.bin').open('wb') as f:f.write(b'x'*(2*1024**2));f.flush();os.fsync(f.fileno())
 os._exit(0)
os._exit(0)
''')
report=meter(['/usr/bin/python3',str(script)],work,e/'sampler-orphan-delegated-measurement.json',final_output=work/'final.bin')
counters=report['IO']['waited_tree_inclusive_counters']
assert report['status']=='PASS' and counters['direct_or_adopted_children_reaped']==2
assert counters['wchar']==2*1024**2 and report['disk']['final_output_bytes']['logical']==2*1024**2
result={'status':'PASS','orphan_adopted_and_reaped':True,'exact_inclusive_wchar':2*1024**2,'measurement':report,
 'sampler_sha256':hashlib.sha256((home/'tree_sampler_delegated.py').read_bytes()).hexdigest(),
 'calibration_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
 'workload_sha256':hashlib.sha256(script.read_bytes()).hexdigest()}
(e/'sampler-orphan-delegated-calibration.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'status':'PASS','adopted_children':2,'wchar':counters['wchar']}))
