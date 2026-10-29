"""Fixed public protein spectrum checks; run after fetch_varian_public.py.

The externally authored assignment boxes and contour threshold are retained.
These checks establish numerical behavior on real data, not peak identity,
deconvolution, signal-free reference regions, or biological quantitation.
"""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import nmrglue as ng
import numpy as np

from nmrnext.analysis import autophase, dataset_digest, integrate_regions, pick_peaks
from nmrnext.errors import NMRNextError
from nmrnext.io import read_dataset
from nmrnext.model import Dataset
from nmrnext.operations import quadrature_index_orientation, run_operations

CROP=((512,1536),(1536,3072))
REFERENCE=(((512,768),(2800,3072)),((1280,1536),(2800,3072)))


def fingerprints(root,manifest):
    records=[]
    wanted=('processed_2d/plot_2d/nmrpipe_2d/test.ft2','processed_2d/plot_2d/limits.in',
            'processed_2d/plot_2d/plot_assignments.py')
    members={m['path']:m for archive in manifest['archives'] for m in archive['members']}
    for name in wanted:
        value=hashlib.sha256((root/name).read_bytes()).hexdigest()
        if value!=members[name]['sha256']:
            raise ValueError(f'Source hash mismatch: {name}')
        records.append({'path':name,'sha256':value})
    return records


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('.artifacts/coverage-formats'))
    parser.add_argument("--native-phased",type=Path,help="Optional independently validated full-quadrature Varian fixed-recipe spectrum")
    args=parser.parse_args()
    root=args.root
    manifest=json.loads(Path('tests/fixtures/varian_public_manifest.json').read_text())
    before=fingerprints(root,manifest)
    full=read_dataset(root/'processed_2d/plot_2d/nmrpipe_2d/test.ft2',format='nmrpipe-real')
    axes=[replace(a,start=a.start+lo*a.step,size=hi-lo) for a,(lo,hi) in zip(full.axes,CROP)]
    data=full.data[(slice(None),*(slice(lo,hi) for lo,hi in CROP))]
    dataset=Dataset(data,axes)
    mask=np.zeros(dataset.data.shape[1:],dtype=bool)
    for box in REFERENCE:
        mask[tuple(slice(lo-c[0],hi-c[0]) for (lo,hi),c in zip(box,CROP))]=True
    targets=[]
    for line in (root/'processed_2d/plot_2d/limits.in').read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):continue
        name,x0,y0,x1,y1=line.split()
        pairs=[sorted([int(y0),int(y1)]),sorted([int(x0),int(x1)])]
        bounds={a.id:[lo-c[0],hi-c[0]] for a,(lo,hi),c in zip(axes,pairs,CROP)}
        targets.append({'id':name,'bounds':bounds,'unit':'points'})
    peaks=pick_peaks(dataset,noise_mask=mask,snr=6,sign='positive',baseline='none',min_separation=4)
    unmasked=pick_peaks(dataset,snr=6,sign='positive',baseline='none',min_separation=4)
    fixed=pick_peaks(dataset,threshold=30000,sign='positive',baseline='none',min_separation=4)
    volumes=integrate_regions(dataset,targets,baseline='reference_linear',baseline_mask=mask)
    # Independent baseline fit: solve normal equations, rather than production SVD least squares.
    locations=np.nonzero(mask)
    columns=np.array([np.ones(len(locations[0])),*(p/(a.size-1)-.5 for p,a in zip(locations,axes))])
    coef=np.linalg.solve(columns@columns.T,columns@dataset.data[0][mask])
    cases=[]
    for target, measured in zip(targets,volumes['regions']):
        bounds=[target['bounds'][a.id] for a in axes]
        def candidates(report):
            return [p for p in report['peaks'] if all(lo<=p['points'][a.id]<=hi for a,(lo,hi) in zip(axes,bounds))]
        picks=candidates(peaks)
        fixed_picks=candidates(fixed)
        unmasked_picks=candidates(unmasked)
        slices=tuple(slice(lo,hi+1) for lo,hi in bounds)
        yy,xx=np.meshgrid(*(np.arange(lo,hi+1) for lo,hi in bounds),indexing='ij')
        block=dataset.data[0][slices]-(coef[0]+coef[1]*(yy/(axes[0].size-1)-.5)+coef[2]*(xx/(axes[1].size-1)-.5))
        # Explicit endpoint-weighted sum, independent of production interpolation and trapezoid calls.
        wy=np.ones(block.shape[0]);wy[[0,-1]]=.5
        wx=np.ones(block.shape[1]);wx[[0,-1]]=.5
        expected=float(wy@block@wx*abs(axes[0].step*axes[1].step))
        error=abs(measured['integral']-expected)
        tolerance=1e-10*abs(expected)+1e-8*max(1.,abs(expected))
        cases.append({'source_box':target,'six_mad_candidates':picks,'source_threshold_candidates':fixed_picks,'unmasked_six_mad_candidates':unmasked_picks,
                      'integral':measured['integral'],'independent_integral':expected,'absolute_error':error,
                      'integration_pass':error<=tolerance,
                      'coordinate_bounds':measured['coordinate_bounds'],
                      'recovery_status':'candidate_in_source_box' if picks else 'no_candidate_in_source_box'})
    # Stored processed profile is real-only; refusing its automatic phase is required.
    try:
        autophase(dataset,inclusion_mask=mask)
        phase={'status':'unexpected_success'}
    except NMRNextError as exc:
        phase={'status':'refused','code':exc.code,'reason':'Archived processed reference has no complex quadratures'}
    native_phase={'status':'not_run','reason':'Supply --native-phased after fixed-recipe quadrature and coordinate validation'}
    if args.native_phased is not None:
        native=read_dataset(args.native_phased)
        if tuple(a.size for a in native.axes)!=tuple(a.size for a in full.axes):
            raise ValueError('Native phase diagnostic requires the exact archived full-grid shape')
        native_crop=run_operations(native,[{"op":"crop","axis":a.id,"start":lo,"stop":hi}
                                           for a,(lo,hi) in zip(native.axes,CROP)])
        inclusion=np.zeros(native_crop.data.shape[1:],dtype=bool)
        for target in targets:
            inclusion[tuple(slice(max(0,target['bounds'][a.id][0]-16),
                                  min(a.size,target['bounds'][a.id][1]+17)) for a in axes)]=True
        native_before=dataset_digest(native_crop)
        try:
            corrected,phase_report=autophase(native_crop,first_order=False,inclusion_mask=inclusion)
            replay=run_operations(native_crop,phase_report['operations'])
            if dataset_digest(replay)!=dataset_digest(corrected):
                raise AssertionError('Phase replay changed data')
            native_phase={'status':'fit_returned_requires_inspection','report':phase_report,
                          'replay_digest_matches':True,
                          'interpretation':'Diagnostic only; line-model acceptance is not independent phase certification'}
        except NMRNextError as exc:
            native_phase={'status':'refused','error':exc.to_dict(),
                          'interpretation':'Fixed trusted-anchor protocol did not satisfy the restricted line model; no retuning'}
        if native_before!=dataset_digest(native_crop):
            raise AssertionError('Phase diagnostic mutated its input')
        native_phase['quadrature_index_orientation']=quadrature_index_orientation(native_crop)
        native_phase['input_dataset_digest']=native_before
        native_phase['mask_expansion_points']=16
        native_phase['first_order']=False
    assert before==fingerprints(root,manifest)
    report={'source_files':before,'source_untouched':True,'crop_source_points_half_open':CROP,
            'reference_boxes_source_points_half_open':REFERENCE,
            'reference_physical_Hz':[[[full.axes[i].start+v*full.axes[i].step for v in (b[0],b[1]-1)] for i,b in enumerate(box)] for box in REFERENCE],
            'protocol':{'snr':6,'source_threshold':30000,'min_separation_points':4,'sign':'positive','baseline':'none',
                        'integration_baseline':'reference_linear','rtol':1e-10,'scaled_atol':1e-8},
            'noise':peaks['noise'],'threshold':peaks['threshold'],'total_six_mad_candidates':len(peaks['peaks']),
            'total_source_threshold_candidates':len(fixed['peaks']),
            'unmasked_comparison':{'noise':unmasked['noise'],'threshold':unmasked['threshold'],'total_candidates':len(unmasked['peaks']),
                                   'interpretation':'Same detection controls; reference noise population alone changes. No improvement assumed.'},
            'reference_baseline':volumes['regions'][0]['baseline'],'cases':cases,'phase':phase,'native_phase':native_phase,
            'numpy_version':np.__version__,'nmrglue_version':ng.__version__,
            'implementation_sha256':hashlib.sha256(Path('src/nmrnext/analysis.py').read_bytes()).hexdigest(),
            'source_sha256':{name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in
                             ['src/nmrnext/analysis.py','src/nmrnext/operations.py','tools/validate_public_hard_spectra.py']},
            'limitations':['Source boxes are not certified peak centers or deconvolved identities',
                          'Reference mask signal-free assumption needs inspection; no automatic contamination certification',
                          'Volumes test numerical agreement on stored real data, not independent biological volume truth',
                          'Reference-only profile cannot validate automatic phase'],
            'pass':all(c['integration_pass'] for c in cases) and phase.get('code')=='UNSUPPORTED_AUTOPHASE'}
    print(json.dumps(report,indent=2))
    if not report['pass']:raise SystemExit(1)


if __name__=='__main__':main()
