"""Private Linux process-tree meter. No case command is embedded or run by default."""
import hashlib,json,os,pwd,resource,stat,subprocess,time,uuid
from pathlib import Path

def workspace_files(root):
    """Regular files only; never follow raw-input or other symlinks."""
    result={};pending=[Path(root)]
    while pending:
        directory=pending.pop()
        try:entries=list(os.scandir(directory))
        except FileNotFoundError:continue
        for item in entries:
            try:
                s=item.stat(follow_symlinks=False)
                if stat.S_ISDIR(s.st_mode):pending.append(Path(item.path))
                elif stat.S_ISREG(s.st_mode):result[str(Path(item.path).absolute())]={'logical':s.st_size,'allocated':s.st_blocks*512}
            except FileNotFoundError:continue
    return result

def meter(command,cwd,output,environment=None,timeout=300,sample_seconds=.005,final_output=None):
    assert os.geteuid()==0,'Root needed only for a new private cgroup and dropping child credentials'
    assert len(os.sched_getaffinity(0))==1 and os.sched_getaffinity(0)=={0}
    output=Path(output);assert output.parent.resolve().is_relative_to(Path('/home/validator/lab-evidence'))
    assert not output.exists() and Path(cwd).resolve().is_relative_to(Path('/home/validator'))
    identity=pwd.getpwnam('validator')
    setup=json.loads(Path('/home/validator/lab-evidence/delegated-cgroup-setup.json').read_text())
    parent=Path(setup['parent']);observer=Path(setup['observer'])
    assert parent==Path('/sys/fs/cgroup/system.slice/spinflow-lab-s15-measure-v1.service') and parent.resolve()==parent and parent.stat().st_uid==0
    assert observer==parent/'observer' and setup['status']=='READY'
    assert {'cpu','memory','io','pids'}<=set((parent/'cgroup.subtree_control').read_text().split())
    assert not (parent/'cgroup.procs').read_text().strip()
    assert str(setup['keeper_pid']) in (observer/'cgroup.procs').read_text().split()
    assert (parent/'cpu.max').read_text().strip()==setup['cpu_max']=='max 100000'
    assert (parent/'memory.max').read_text().strip()==setup['memory_max']=='max'
    (observer/'cgroup.procs').write_text(str(os.getpid()))
    assert Path('/proc/self/cgroup').read_text().strip().endswith('::/system.slice/spinflow-lab-s15-measure-v1.service/observer')
    group=parent/('workload-'+uuid.uuid4().hex);group.mkdir()
    if not all((group/name).is_file() for name in ['memory.peak','cpu.stat','io.stat']):
        assert not (group/'cgroup.procs').read_text().strip();group.rmdir()
        raise RuntimeError('Delegated workload controllers unavailable before launch')
    env=dict(os.environ,HOME=identity.pw_dir,USER='validator',LOGNAME='validator',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',VECLIB_MAXIMUM_THREADS='1')
    if environment:env.update(environment)
    assert all(env[k]=='1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'])
    page=os.sysconf('SC_PAGE_SIZE');observations=[];peak=0;max_processes=0;observed_pids=set();status='RUNNING';process=None
    baseline=workspace_files(cwd);logical_io={};disk_peaks={k:0 for k in ['total_logical','total_allocated','temporary_logical','temporary_allocated']}
    final_name=str(Path(final_output).absolute()) if final_output is not None else None
    def enter():
        (group/'cgroup.procs').write_text(str(os.getpid()))
        os.sched_setaffinity(0,{0});os.initgroups(identity.pw_name,identity.pw_gid);os.setgid(identity.pw_gid);os.setuid(identity.pw_uid)
    start_cpu=resource.getrusage(resource.RUSAGE_CHILDREN);start_observer=resource.getrusage(resource.RUSAGE_SELF);start=time.monotonic()
    try:
        with output.with_suffix('.stdout').open('xb') as stdout,output.with_suffix('.stderr').open('xb') as stderr:
            sentinel=Path(__file__).parent/'io_sentinel';assert sentinel.is_file()
            effective_command=[str(sentinel),*map(str,command)]
            process=subprocess.Popen(effective_command,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,preexec_fn=enter,start_new_session=True)
            while True:
                pids=[int(x) for x in (group/'cgroup.procs').read_text().split()];observed_pids.update(pids);rss=0;alive=[]
                for pid in pids:
                    try:
                        rss+=int(Path(f'/proc/{pid}/statm').read_text().split()[1])*page;alive.append(pid)
                        process_stat=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
                        identity_key=f'{pid}:{process_stat[19]}'
                        counters={k:int(v) for k,v in (line.split(':',1) for line in Path(f'/proc/{pid}/io').read_text().splitlines())}
                        previous=logical_io.setdefault(identity_key,{})
                        for key,value in counters.items():previous[key]=max(previous.get(key,0),value)
                    except (FileNotFoundError,ProcessLookupError):pass
                peak=max(peak,rss);max_processes=max(max_processes,len(alive));elapsed=time.monotonic()-start
                files=workspace_files(cwd)
                disk={'total_logical':sum(v['logical'] for v in files.values()),'total_allocated':sum(v['allocated'] for v in files.values()),
                      'temporary_logical':sum(v['logical'] for p,v in files.items() if p not in baseline and p!=final_name),
                      'temporary_allocated':sum(v['allocated'] for p,v in files.items() if p not in baseline and p!=final_name)}
                for key,value in disk.items():disk_peaks[key]=max(disk_peaks[key],value)
                observations.append({'seconds':elapsed,'simultaneous_sum_rss_bytes':rss,'pids':alive,'workspace':disk})
                code=process.poll()
                if code is not None and not pids:break
                if elapsed>timeout:
                    (group/'cgroup.kill').write_text('1');process.wait(timeout=10);status='TIMEOUT';break
                time.sleep(sample_seconds)
            elapsed=time.monotonic()-start;end_cpu=resource.getrusage(resource.RUSAGE_CHILDREN);end_observer=resource.getrusage(resource.RUSAGE_SELF)
            cpu={k:int(v) for k,v in (line.split() for line in (group/'cpu.stat').read_text().splitlines())}
            peak_charge=int((group/'memory.peak').read_text());events=(group/'memory.events').read_text()
            if status=='RUNNING':status='PASS' if process.returncode==0 else 'COMMAND_FAILED'
            report={'status':status,'command':list(map(str,command)),'cwd':str(cwd),'wall_seconds':elapsed,'returncode':process.returncode,'cgroup_full_tree_cpu':cpu,'cgroup_peak_memory_charge_bytes':peak_charge,'sampled_peak_simultaneous_sum_RSS_bytes':peak,'max_simultaneously_observed_processes':max_processes,'all_observed_pids':sorted(observed_pids),'sample_period_seconds':sample_seconds,'samples':len(observations),'rusage_waited_tree_user_seconds':end_cpu.ru_utime-start_cpu.ru_utime,'rusage_waited_tree_system_seconds':end_cpu.ru_stime-start_cpu.ru_stime,'memory_events':events,'guest_affinity':[0],'guest_logical_CPUs':os.cpu_count(),'cpu_max':(group/'cpu.max').read_text().strip(),'memory_max':(group/'memory.max').read_text().strip(),'memory_scope':'cgroup memory.peak includes charged page cache/kernel memory; summed process RSS double-counts shared pages and sampled peak is a lower bound; neither is falsely called exact aggregate RSS','CPU_scope':'cgroup cpu.stat counts all descendants including exited/reaped forked stages; parent sampler is outside workload cgroup','pool_env':{k:env[k] for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']},'executed_as_uid':identity.pw_uid}
            report['delegated_hierarchy']={'parent':str(parent),'observer':str(observer),'workload':str(group),'setup_sha256':hashlib.sha256(Path('/home/validator/lab-evidence/delegated-cgroup-setup.json').read_bytes()).hexdigest(),'parent_cpu_max':setup['cpu_max'],'parent_memory_max':setup['memory_max']}
            report['observer_CPU_seconds']={'user':end_observer.ru_utime-start_observer.ru_utime,'system':end_observer.ru_stime-start_observer.ru_stime,'total':end_observer.ru_utime+end_observer.ru_stime-start_observer.ru_utime-start_observer.ru_stime}
            report['observer_scope']='Observer is outside workload cgroup but competes on the same one guest CPU. Wall time includes its contention; differing process counts can change observer cost. No overhead subtraction.'
            io={}
            for line in (group/'io.stat').read_text().splitlines():
                device,*values=line.split();io[device]={k:int(v) for k,v in (item.split('=') for item in values)}
            final_files=workspace_files(cwd)
            report['IO']={'cgroup_virtual_block_by_device':io,
                'sampled_tree_logical_counter_lower_bounds':{k:max((row.get(k,0) for row in logical_io.values()),default=0) for k in ['rchar','wchar','syscr','syscw','read_bytes','write_bytes']},
                'sampled_process_identities':logical_io,
                'waited_tree_input_blocks_512B':end_cpu.ru_inblock-start_cpu.ru_inblock,
                'waited_tree_output_blocks_512B':end_cpu.ru_oublock-start_cpu.ru_oublock,
                'scope':'cgroup io.stat is guest virtual-block accounting, not host physical-media I/O; cached reads and delayed writeback may show zero. Logical lower bound is the MAXIMUM observed individual /proc counter across PID/start-time records, NOT their sum: ancestor counters include waited descendants and summing histories double-counts. This conservative maximum can omit siblings, short-lived/reaped processes and post-last-sample work; it is not complete logical tree traffic. Waited block accounting is separate. No fsync or cache manipulation added to real trials.'}
            stdout.flush()
            lines=output.with_suffix('.stdout').read_text(errors='replace').splitlines()
            inclusive=[json.loads(line.removeprefix('SPINFLOW_TREE_IO ')) for line in lines if line.startswith('SPINFLOW_TREE_IO ')]
            assert len(inclusive)==1,'Missing or ambiguous waited-tree sentinel I/O record'
            report['IO']['waited_tree_inclusive_counters']=inclusive[0]
            report['IO']['sentinel_scope']='Tiny C child-subreaper waits direct and orphaned descendants, then reads its inclusive Linux /proc/self/io counters. Delta covers command tree plus sentinel setup/self-read overhead; metadata-line write occurs afterward. No overlapping ancestor sum. CPU/RSS/wall/cgroup counters include identical sentinel and metadata emission in both lanes, with no subtraction. Not application-only logical I/O or host physical-media traffic.'
            report['effective_command']=effective_command
            report['io_sentinel_sha256']=hashlib.sha256(sentinel.read_bytes()).hexdigest()
            report['disk']={'baseline_files':baseline,'baseline_logical_bytes':sum(v['logical'] for v in baseline.values()),
                'baseline_allocated_bytes':sum(v['allocated'] for v in baseline.values()),'sampled_peak_bytes':disk_peaks,
                'final_workspace_files':final_files,'final_output_path':final_name,'final_output_bytes':final_files.get(final_name),
                'scope':'Regular files in trial cwd only, symlinks and external raw targets excluded. Total workspace includes precreated wrapper/recipe. Temporary excludes baseline files and declared final output. Logical size and st_blocks*512 allocated bytes are distinct. Sampled peaks are LOWER BOUNDS; transient create/delete between polls is missed. Output/log files outside cwd not counted here.'}
            report['actual_largest_sample_gap_seconds']=max((b['seconds']-a['seconds'] for a,b in zip(observations,observations[1:])),default=0)
            output.write_text(json.dumps(report,indent=2)+'\n');output.with_suffix('.samples.json').write_text(json.dumps(observations)+'\n')
            return report
    finally:
        if (group/'cgroup.procs').read_text().strip():
            (group/'cgroup.kill').write_text('1')
            if process:process.wait(timeout=10)
        for _ in range(100):
            if not (group/'cgroup.procs').read_text().strip():break
            time.sleep(.01)
        group.rmdir()

if __name__=='__main__':
    raise SystemExit('Library only; use the explicit synthetic calibration or a separately approved locked benchmark driver.')
