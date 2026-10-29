"""Validate real Varian acquisitions against independently archived references.

No automatic downloading and no execution of scripts from the data archive.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import time
import tracemalloc

import numpy as np

from nmrnext.io import read_dataset
from nmrnext.operations import run_operations


REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO/'tests/fixtures/varian_public_manifest.json'


def _reference():
    spec = importlib.util.spec_from_file_location('varian_reference',REPO/'tests/reference/varian_reference.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate(root, backend='numpy'):
    root = Path(root)
    manifest = json.loads(MANIFEST.read_text())
    hashes = {}
    for archive in manifest['archives']:
        for item in archive['members']:
            path = root/item['path']
            if path.stat().st_size != item['bytes']:
                raise AssertionError('Source size differs: '+str(path))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != item['sha256']:
                raise AssertionError('Source hash differs: '+str(path))
            hashes[item['path']] = digest
    reference = _reference()
    results = []
    for case in manifest['cases']:
        dimensions = len(case['shape'])-1
        start = time.perf_counter()
        tracemalloc.start()
        dataset = read_dataset(root/case['raw'],format=case['format'])
        elapsed = time.perf_counter()-start
        _,peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert list(dataset.data.shape) == case['shape']
        archived = reference.pipe_components(root/case['raw_reference'],dimensions)
        signs = np.array([1,-1] if dimensions == 1 else [1,-1,-1,1],dtype=np.float32)
        signs = signs.reshape((-1,)+(1,)*dimensions)
        expected = archived*signs
        np.testing.assert_array_equal(dataset.data,expected)
        rows = reference.raw_blocks(root/case['raw']/'fid')
        original = np.stack((rows[0,::2],rows[0,1::2])) if dimensions == 1 else np.stack((rows[::2,::2],rows[1::2,::2],rows[::2,1::2],rows[1::2,1::2]))
        np.testing.assert_array_equal(dataset.data,original)
        operations = [{'op':'fft','axis':f'a{i}'} for i in range(dimensions)]
        spectrum = run_operations(dataset,operations,backend=backend)
        expected_spectrum = reference.positive_transform(archived,dimensions)*signs
        error = float(np.max(np.abs(spectrum.data-expected_spectrum))/np.max(np.abs(expected_spectrum)))
        assert error <= manifest['protocol']['native_fft_relative_max_tolerance']
        assert all(a.reference_ppm is None for a in spectrum.axes)
        recipe_path=REPO/f'examples/varian-{dimensions}d-public.json'
        recipe=json.loads(recipe_path.read_text())
        processed=run_operations(dataset,recipe['operations'],backend=backend,memory_bytes=recipe['memory_bytes'])
        real_reference,ppm_reference=reference.pipe_real(root/case['processed_reference'],dimensions)
        assert processed.data[0].shape==real_reference.shape
        processed_error=float(np.max(np.abs(processed.data[0]-real_reference))/np.max(np.abs(real_reference)))
        assert processed_error<=manifest['protocol']['processed_relative_max_tolerance']
        coordinate_errors=[]
        for axis,expected_ppm in zip(processed.axes,ppm_reference):
            coordinate_error=float(np.max(np.abs(processed.coordinates(axis.id,'ppm')-expected_ppm)))
            assert coordinate_error<=manifest['protocol']['physical_coordinate_tolerance_ppm']
            coordinate_errors.append(coordinate_error)
        if dimensions==1:
            boxes=[(slice(i,i+1024),) for i in range(0,4096,1024)]
        else:
            boxes=[]
            for line in (root/'processed_2d/plot_2d/limits.in').read_text().splitlines():
                if not line.strip() or line.startswith('#'):continue
                _,x0,y0,x1,y1=line.split()
                x0,x1=sorted((int(x0),int(x1)));y0,y1=sorted((int(y0),int(y1)))
                boxes.append((slice(y0,y1+1),slice(x0,x1+1)))
        window_errors=[]
        for box in boxes:
            candidate=processed.data[0][box];golden=real_reference[box]
            assert np.unravel_index(np.argmax(candidate),candidate.shape)==np.unravel_index(np.argmax(golden),golden.shape)
            golden_sum=float(np.sum(golden,dtype=np.float64))
            candidate_sum=float(np.sum(candidate,dtype=np.float64))
            window_error=abs(candidate_sum-golden_sum)/abs(golden_sum)
            assert window_error<=manifest['protocol']['processed_window_sum_relative_tolerance']
            window_errors.append(window_error)
        assert np.unravel_index(np.argmax(processed.data[0]),real_reference.shape)==np.unravel_index(np.argmax(real_reference),real_reference.shape)
        results.append({'id':case['id'],'shape':list(dataset.data.shape),'dtype':dataset.data.dtype.name,
                        'exact_archived_quadrature_match':True,'exact_independent_raw_decoder_match':True,
                        'native_fft_relative_max_error':error,'import_seconds':elapsed,
                        'tracemalloc_peak_bytes':peak,'array_bytes':dataset.data.nbytes,
                        'source_preserved':True,'processed_real_relative_max_error':processed_error,
                        'physical_ppm_max_errors':coordinate_errors,'fixed_window_signed_sum_relative_errors':window_errors,
                        'fixed_window_peak_bins_exact':True,'processed_archive_equivalence':'fixed archived recipe real component and independently decoded physical coordinates; other quadratures retained but no archived phase-channel oracle'})
    for name,digest in hashes.items():
        assert hashlib.sha256((root/name).read_bytes()).hexdigest() == digest
    fingerprint_paths = ['src/nmrnext/io/varian.py','src/nmrnext/io/__init__.py','src/nmrnext/operations.py',
                         'tests/reference/varian_reference.py','tests/fixtures/varian_public_manifest.json','tools/validate_public_varian.py',
                         'examples/varian-1d-public.json','examples/varian-2d-public.json']
    return {'schema_version':1,'backend':backend,'source_hashes':hashes,'cases':results,
            'fingerprints':{name:hashlib.sha256((REPO/name).read_bytes()).hexdigest() for name in fingerprint_paths},
            'limitations':['No vendor write','2D cpjdcptan2d States only','No automatic ppm reference',
                           'No general 3D/NUS/arrayed/integer/scaled-block support',
                           'tracemalloc is Python allocator evidence, not process RSS']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=REPO/'.artifacts/coverage-formats')
    parser.add_argument('--backend',choices=['numpy','native'],default='numpy')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    result=validate(args.root,args.backend)
    text=json.dumps(result,indent=2)+'\n'
    if args.report:
        if args.report.exists():
            raise FileExistsError('Report exists; choose a new evidence path')
        args.report.write_text(text)
    print(text)


if __name__=='__main__':main()
