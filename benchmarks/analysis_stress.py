"""Reproduce bounded integration timing/allocation measurements.

Run with --baseline PATH pointing to an untouched analysis.py from the declared
comparison commit. This is an implementation comparison, not a speed guarantee.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import time
import tracemalloc

import numpy as np
import scipy
from nmrnext import analysis, operations
from nmrnext.model import Axis, Dataset


def measure(module, dataset, regions, baseline):
    start=time.perf_counter()
    report=module.integrate_regions(dataset,regions,baseline=baseline)
    return time.perf_counter()-start, report


def allocation(module, dataset, regions, baseline):
    tracemalloc.start()
    module.integrate_regions(dataset,regions,baseline=baseline)
    _,peak=tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline',type=Path,required=True)
    args=parser.parse_args()
    spec=importlib.util.spec_from_file_location('nmrnext._benchmark_baseline',args.baseline)
    previous=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    yy,xx=np.indices((1024,1024),dtype=float)
    values=.7+.0003*yy-.0002*xx+np.exp(-((yy-500)/20)**2-((xx-500)/30)**2)
    dataset=Dataset(values[None],[Axis('y',1024,domain='frequency',unit='Hz',step=-2.),
                                  Axis('x',1024,domain='frequency',unit='Hz',step=.5)])
    regions=[{'id':'full','bounds':{'y':[0,1023],'x':[0,1023]},'unit':'points'}]
    cases={}
    for method in ['none','edge_linear']:
        timings={"before":[],"after":[]}
        reports={}
        # Warm both imports/code paths; alternate which executes first to limit drift.
        measure(previous,dataset,regions,method)
        measure(analysis,dataset,regions,method)
        for repeat in range(8):
            order=[('before',previous),('after',analysis)]
            if repeat%2:order.reverse()
            for name,module in order:
                elapsed,reports[name]=measure(module,dataset,regions,method)
                timings[name].append(elapsed)
        results={}
        for name,module in [('before',previous),('after',analysis)]:
            samples=timings[name]
            results[name]={'median_seconds':float(np.median(samples)), 'times_seconds':samples,
                           'min_seconds':min(samples),'max_seconds':max(samples),
                           'peak_traced_bytes':allocation(module,dataset,regions,method),
                           'integrals':[r['integral'] for r in reports[name]['regions']]}
        old,new=results['before'],results['after']
        np.testing.assert_allclose(new['integrals'],old['integrals'],rtol=1e-11,atol=1e-8)
        cases[method]={**results,'speed_ratio':old['median_seconds']/new['median_seconds'],
                       'allocation_ratio':old['peak_traced_bytes']/new['peak_traced_bytes']}
    print(json.dumps({'platform':platform.platform(),'python':platform.python_version(),
        'numpy':np.__version__,'scipy':scipy.__version__,
        'threads':{key:os.environ.get(key) for key in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']},
        'baseline_path':str(args.baseline),'baseline_sha256':hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
        'candidate_sha256':hashlib.sha256(Path(analysis.__file__).read_bytes()).hexdigest(),
        'operations_sha256':hashlib.sha256(Path(operations.__file__).read_bytes()).hexdigest(),
        'shape':[1024,1024],'repeats':8,'ordering':'interleaved, alternating first implementation','allocation_method':'tracemalloc peak during complete call; not process RSS',
        'cases':cases},indent=2))


if __name__=='__main__':
    main()
