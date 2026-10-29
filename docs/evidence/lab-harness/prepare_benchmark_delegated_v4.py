"""Create exact commands without executing measured workloads."""
import hashlib,json,os,shutil
from pathlib import Path
home=Path.home();e=home/'lab-evidence';source=home/'lab-candidate-v4';root=home/'lab-benchmark-v4-delegated';root.mkdir()
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
rec=lambda p:{'path':str(Path(p).resolve()),'sha256':sha(p)}
original_convert=home/'coverage-public/2d/agilent2pipe_2d/agilent2pipe_2d.com'
original_process=home/'coverage-public/reference_2d/processing/pipe2pipe_2d.com'
raw=home/'coverage-public/2d/agilent2pipe_2d/agilent_2d'
init=home/'lab-private/nmrpipe/com/nmrInit.linux239_64.com'
recipe=json.loads((source/'examples/laboratory-workflow.json').read_text())
fixed_schedule=json.loads((e/'benchmark-predeclared-schedule.json').read_text())
jobs=[];files=[]
for spec in fixed_schedule['endpoints']:
    endpoint=spec['name'];is_ucsf=endpoint=='raw_to_ucsf_real'
    sequence=[('warmup',0,lane) for lane in spec['warmup_order']]
    sequence += [('measured',pair,lane) for pair,lanes in enumerate(spec['measured_pairs'],1) for lane in lanes]
    for ordinal,(kind,pair,lane) in enumerate(sequence):
        label=f'{endpoint}-{ordinal:02}-{kind}-{lane}';work=root/label;work.mkdir()
        (work/'agilent_2d').mkdir();(work/'nmrpipe_2d').mkdir()
        input_records=[]
        for name in ['fid','procpar']:
            staged=work/'agilent_2d'/name;shutil.copyfile(raw/name,staged);staged.chmod(0o444)
            assert sha(staged)==sha(raw/name) and not staged.is_symlink()
            item={**rec(staged),'bytes':staged.stat().st_size,'mode':staged.stat().st_mode & 0o777};files.append(item);input_records.append(item)
        output=work/('result.ucsf' if is_ucsf else 'nmrpipe_2d/test.ft2')
        if lane=='A':
            (work/'nmrpipe_2d/test.fid').symlink_to(work/'agilent_2d/test.fid')
            text='#!/bin/tcsh -f\nsource '+str(init)+'\n/bin/tcsh -f '+str(original_convert)+'\nif ($status != 0) exit $status\n/bin/tcsh -f '+str(original_process)+'\nif ($status != 0) exit $status\n'
            if is_ucsf:text+=str(home/'lab-private/sparky/bin/pipe2ucsf')+' '+str(work/'nmrpipe_2d/test.ft2')+' '+str(output)+'\nexit $status\n'
            else:text+='exit 0\n'
            wrapper=work/'reference.csh';wrapper.write_text(text);command=['/bin/tcsh','-f',str(wrapper)];files.append(rec(wrapper))
        else:
            r=dict(recipe,input=str(work/'agilent_2d'),output=str(output),output_format='ucsf-real' if is_ucsf else 'nmrpipe-real')
            wrapper=work/'recipe.json';wrapper.write_text(json.dumps(r,indent=2)+'\n')
            command=[str(home/'lab-runtime-v4/bin/python'),'-m','nmrnext','run',str(wrapper)];files.append(rec(wrapper))
        expected=home/('lab-reference-checkpoints/reference.ucsf' if is_ucsf else 'lab-reference-checkpoints/S10.pipe') if lane=='A' else home/('lab-native-recipes-v4/candidate.ucsf' if is_ucsf else 'lab-native-recipes-v4/candidate.ft2')
        jobs.append({'id':label,'endpoint':endpoint,'kind':kind,'pair':pair,'lane':lane,'cwd':str(work),'command':command,'staged_inputs':input_records,'output':str(output),'expected_output_sha256':sha(expected)})
plan={'schema_version':1,'status':'FROZEN_NOT_EXECUTED','scientific_lock':rec(e/'scientific-execution-lock-v4.json'),
 'schedule':rec(e/'benchmark-predeclared-schedule.json'),'sampler':rec(home/'tree_sampler.py'),'sampler_calibration':rec(e/'sampler-calibration-v2.json'),
 'runner':rec(home/'run_benchmark_delegated_v4.py'),'preparer':rec(__file__),'fixed_files':files+[rec(original_convert),rec(original_process),rec(init)],
 'case_source_files':[rec(raw/'fid'),rec(raw/'procpar')],'trials':jobs,
 'timed_scope':'Native CLI start through final output close; reference tcsh initialization, exact original conversion and processing scripts, plus pipe2ucsf for UCSF. Identical read-only regular raw input copies in both lanes, output directories, recipe files and exact wrapper creation are untimed. No numerical intermediates for native; original reference file handoff is included.',
 'metrics':'Full descendant cgroup CPU, elapsed wall including sampler interference, cgroup memory.peak, simultaneous summed RSS sampled5ms; observer CPU separately. No overhead subtraction.',
 'runtime_integrity':'All directly bound science-lock files rehashed before and after each trial. Actual output checked against its prior scientifically validated byte hash after timing. Raw source hashes checked before/after all trials.',
 'cache':'Warm/cache-uncontrolled. No fsync added; endpoint is normal final output close. No trial excluded based on outcome or timing.',
 'pending':'Requires root benchmark GO and consumer producer barrier; owned GUI/X11 stopped and process state captured before any warmup.'}
path=e/'benchmark-execution-lock-v4-delegated-base.json'
with path.open('x') as f:json.dump(plan,f,indent=2);f.write('\n')
print(json.dumps({'path':str(path),'sha256':sha(path),'trials':len(jobs),'execution':'NOT_RUN'}))
