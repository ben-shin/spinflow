"""Frozen public-case candidate checkpoints, excluded from benchmark timing.

Run only after root authorization and execution-lock seal. `recipes` writes two
direct raw-to-final CLI recipes for the separately controlled benchmark harness.
It does not run a benchmark, establish affinity, or claim an RSS limit.
"""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path

try:
    from . import lab_compare
except ImportError:  # python tools/lab_candidate.py
    import lab_compare

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / 'docs/laboratory-workflow-protocol.md'
FIXTURE = ROOT / 'tests/fixtures/lab_workflow_case.json'
RECIPE = ROOT / 'examples/laboratory-workflow.json'
MEMORY_BYTES = 1024 * 1024**2
STAGE_SLICES = (('S01',0,1), ('S02',1,2), ('S03',2,4), ('S04',4,6),
                ('S05',6,7), ('S06',7,8), ('S07',8,9), ('S08',9,11),
                ('S09',11,13), ('S10',13,14))


def load_recipe():
    return lab_compare._json(RECIPE.read_text(encoding='utf8'))


def _runtime_paths():
    """Bind the code actually imported, including the loaded native extension."""
    package = importlib.import_module('nmrnext')
    native = importlib.import_module('nmrnext._native')
    package_root = Path(package.__file__).resolve().parent
    return {p.resolve() for p in package_root.rglob('*.py')} | {Path(native.__file__).resolve()}


def verify_lock(path):
    lock = lab_compare.verify_execution_lock(path, PROTOCOL, FIXTURE)
    for key, source in (('candidate_driver_sha256',Path(__file__)), ('candidate_recipe_sha256',RECIPE)):
        if lock.get(key) != lab_compare.sha256(source):
            raise ValueError('execution lock mismatch: '+key)
    recorded = {}
    for entry in lock['candidate_source_and_binary_hashes']:
        item = Path(entry['path'])
        if not item.is_absolute():
            item = Path(path).resolve().parent / item
        recorded[item.resolve()] = entry['sha256']
    for item in _runtime_paths():
        if recorded.get(item) != lab_compare.sha256(item):
            raise ValueError('actual imported candidate source/binary is not locked: '+str(item))
    return lock


def verify_input(source, case):
    """Only the two exact pinned acquisition members, never a general runner."""
    source = Path(source).resolve(strict=True)
    if not source.is_dir():
        raise ValueError('input must be the acquired fid/procpar directory')
    records = {}
    for name in ('fid','procpar'):
        matches = [r for r in case['files'] if r.get('source_member') == 'agilent2pipe_2d/agilent_2d/'+name]
        if len(matches) != 1:
            raise ValueError('fixture does not identify exactly one '+name)
        path = source / name
        expected = matches[0]
        if path.is_symlink() or not path.is_file() or path.stat().st_size != expected['bytes'] or lab_compare.sha256(path) != expected['sha256']:
            raise ValueError('acquisition byte identity mismatch: '+name)
        records[name] = {'path':str(path), 'bytes':expected['bytes'], 'sha256':expected['sha256']}
    return records


def _new_workspace(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError('output workspace must be separate from the acquisition directory')
    if output.exists() or not output.parent.is_dir():
        raise ValueError('output workspace must be a new directory with an existing parent')
    output.mkdir()  # exclusive: no overwriting old checkpoints or source files
    return output


def _write_json(path, value):
    with Path(path).open('x',encoding='utf8') as stream:
        json.dump(value,stream,indent=2,allow_nan=False)
        stream.write('\n')


def stage_description(dataset):
    """Scalar description, canonical physical order irrespective of transpose."""
    return {'shape_yx':[dataset.axis(a).size for a in ('a0','a1')],
            'complex_axes':list(dataset.complex_axes),
            'frequency_axes':[a for a in ('a0','a1') if dataset.axis(a).domain == 'frequency'],
            'reference_transposed':tuple(a.id for a in dataset.axes) == ('a1','a0')}


def _check_stage(dataset, stage):
    actual = stage_description(dataset)
    if any(actual[k] != stage[k] for k in actual):
        raise ValueError('candidate checkpoint state contradicts frozen fixture: '+stage['id'])
    dtype = dataset.dtype if hasattr(dataset,'dtype') else dataset.data.dtype
    if str(dtype) != 'float32':
        raise ValueError('candidate checkpoint must remain float32')


def write_recipes(source, output, recipe):
    """Direct final endpoints: no checkpoint or HDF5 staging in either lane."""
    result = {}
    for fmt, suffix in (('nmrpipe-real','.ft2'), ('ucsf-real','.ucsf')):
        current = dict(recipe, input=str(Path(source).resolve()), output=str(output/('candidate'+suffix)), output_format=fmt)
        path = output/('recipe-'+fmt+'.json')
        _write_json(path,current)
        result[fmt] = {'path':str(path),'sha256':lab_compare.sha256(path),
                       'argv':['python','-m','nmrnext','run',str(path)]}
    return result


def write_checkpoints(source, output, recipe, case):
    from nmrnext.io import describe_dataset, read_dataset, write_dataset
    from nmrnext.operations import run_operations
    from nmrnext.planning import plan_operations
    header = describe_dataset(source,format='varian-raw-2d')
    stages = {s['id']:s for s in case['stages']}
    _check_stage(header,stages['S00'])
    plan = plan_operations(header,recipe['operations'],backend='native',threads=1,memory_bytes=MEMORY_BYTES)
    current = read_dataset(source,format='varian-raw-2d',max_bytes=MEMORY_BYTES,expected_header=header)
    # Hashes recorded by numeric ingestion must still equal the pinned bytes.
    expected = verify_input(source,case)
    if current.metadata['import']['source_sha256'] != {k:v['sha256'] for k,v in expected.items()}:
        raise ValueError('acquisition changed during numeric import')
    records = {}
    def save(stage, dataset):
        path = output/(stage+'.nmrh5')
        write_dataset(dataset,path,format='native')
        records[stage] = {'path':str(path),'sha256':lab_compare.sha256(path),
                          'bytes':path.stat().st_size,**stage_description(dataset)}
    save('S00',current)
    for name, start, stop in STAGE_SLICES:
        current = run_operations(current,recipe['operations'][start:stop],backend='native',threads=1,memory_bytes=MEMORY_BYTES)
        _check_stage(current,stages[name])
        save(name,current)
        if name == 'S03':
            diagnostic = run_operations(current,recipe['operations'][4:5],backend='native',threads=1,memory_bytes=MEMORY_BYTES)
            save('S03P',diagnostic)
            del diagnostic  # separate branch; never becomes main-pipeline input
    return {'checkpoints':records,'plan':plan,'timing_scope':'diagnostic only; HDF5 checkpoints excluded from benchmarks'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execution-lock',required=True,type=Path)
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--output-directory',required=True,type=Path)
    parser.add_argument('--mode',required=True,choices=('checkpoints','recipes'))
    args = parser.parse_args(argv)
    verify_lock(args.execution_lock)  # before acquisition reads or writes
    case = lab_compare._json(FIXTURE.read_text(encoding='utf8'))
    source_records = verify_input(args.input,case)
    recipe = load_recipe()
    output = _new_workspace(args.input,args.output_directory)
    result = write_checkpoints(args.input,output,recipe,case) if args.mode == 'checkpoints' else {'recipes':write_recipes(args.input,output,recipe)}
    verify_input(args.input,case)
    result.update({'case_id':case['case_id'],'source_files':source_records,
                   'execution_lock_sha256':lab_compare.sha256(args.execution_lock),
                   'candidate_driver_sha256':lab_compare.sha256(__file__),
                   'candidate_recipe_sha256':lab_compare.sha256(RECIPE)})
    _write_json(output/'candidate-record.json',result)
    print(json.dumps({'record':str(output/'candidate-record.json'),'mode':args.mode}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
