"""Fixed difficult-spectrum contracts; expectations are analytical, not fit outputs."""
import math

import numpy as np
import pytest

from nmrnext.analysis import autophase, dataset_digest, integrate_regions, pick_peaks
from nmrnext.errors import NMRNextError
from nmrnext.model import Axis, Dataset
from nmrnext.operations import run_operations


def _spectrum(data, steps=None):
    data = np.asarray(data)
    return Dataset(data[None], [Axis(f'f{i}', n, domain='frequency', unit='Hz',
                                    step=1. if steps is None else steps[i])
                                for i, n in enumerate(data.shape)])


def _regions(d, bounds):
    return [{'id': 'signal', 'bounds': dict(zip((a.id for a in d.axes), bounds)), 'unit': 'points'}]


def _mixed_phase(offset=180):
    x = np.arange(2048)
    z = sum(a/(1+1j*(x-c)/4)*np.exp(1j*np.deg2rad(p))
            for c, a, p in [(300,1,25),(1000,.04,25+offset),(1700,.9,25)])
    return Dataset(np.stack([z.real,z.imag]), [Axis('h',2048,domain='frequency',unit='Hz')], ['h'])


@pytest.mark.parametrize('offset', [35,60,180])
def test_weak_incompatible_phase_line_fails_per_line_gate(offset):
    with pytest.raises(NMRNextError) as error:
        autophase(_mixed_phase(offset))
    assert error.value.code == 'AUTOPHASE_MODEL_MISMATCH'


def test_explicit_positive_anchors_phase_mixed_sign_without_changing_negative_sign():
    d = _mixed_phase()
    valid = np.zeros(2048, dtype=bool)
    valid[240:361] = True
    valid[1640:1761] = True
    before = dataset_digest(d)
    result, report = autophase(d, inclusion_mask=valid)
    op = report['operation']
    assert op['p0'] == pytest.approx(-25, abs=.5)
    assert op['p1'] == pytest.approx(0, abs=1)
    assert result.data[0,1000] < 0
    assert report['selection']['included_points'] == 242
    assert len(report['parameters']['inclusion_mask_digest']) == 64
    assert 'excluded_regions_not_phase_certified' in report['flags']
    assert max(report['objective']['per_line_relative_rms']) <= .05
    replay = run_operations(d, [op])
    np.testing.assert_array_equal(result.data,replay.data)
    assert before == dataset_digest(d)
    assert dataset_digest(result) == report['result_dataset_digest']


def test_phase_mask_cannot_certify_a_clipped_window():
    valid = np.zeros(2048, dtype=bool)
    valid[298:303] = True
    valid[1698:1703] = True
    with pytest.raises(NMRNextError):
        autophase(_mixed_phase(), inclusion_mask=valid)


@pytest.mark.parametrize('mask', [np.zeros(2048,dtype=bool), np.ones(2047,dtype=bool), np.ones(2048)])
def test_invalid_phase_masks_fail_closed(mask):
    with pytest.raises(NMRNextError) as error:
        autophase(_mixed_phase(), inclusion_mask=mask)
    assert error.value.code == 'INVALID_MASK'


def test_crowded_weak_peak_uses_separate_noise_reference():
    rng = np.random.default_rng(54)
    n = 8192
    x = np.arange(n)
    y = rng.normal(0,.01,n)
    for c in np.arange(1000,8000,80):
        y += .9/(1+((x-c)/22)**2)
    y += .09*np.exp(-.5*((x-300)/4)**2)
    d = _spectrum(y)
    noise = x < 200
    report = pick_peaks(d, snr=6, baseline='none', min_separation=20, noise_mask=noise)
    assert report['noise']['sigma'] == pytest.approx(.01,rel=.15)
    assert any(abs(p['points']['f0']-300) <= 2 for p in report['peaks'])
    assert report['noise']['sample_count'] == 200
    assert report['noise']['selection'] == 'explicit_noise_mask'
    assert len(report['parameters']['noise_mask_digest']) == 64
    # Detection and reference samples are deliberately disjoint.
    detection = (x > 250) & (x < 350)
    local = pick_peaks(d,snr=6,baseline='none',min_separation=20,mask=detection,noise_mask=noise)
    assert any(abs(p['points']['f0']-300) <= 2 for p in local['peaks'])
    assert local['noise'] == report['noise']


def test_two_dimensional_signed_correlated_noise_pick_with_separate_mask():
    rng = np.random.default_rng(182)
    # Average independent neighboring samples, preserving known marginal SD .01.
    noise = rng.normal(0,.01,(192,257))
    y = (noise[:,:-1]+noise[:,1:])/math.sqrt(2)
    yy,xx = np.indices(y.shape)
    y += .13*np.exp(-.5*((yy-65)/2)**2-.5*((xx-140)/3)**2)
    y -= .15*np.exp(-.5*((yy-125)/2)**2-.5*((xx-90)/3)**2)
    d = _spectrum(y,steps=[-2,.5])
    reference = yy < 30
    report = pick_peaks(d, sign='both', snr=6, baseline='none', noise_mask=reference, min_separation=12)
    assert report['noise']['sigma'] == pytest.approx(.01,rel=.08)
    for center,polarity in [((65,140),'positive'),((125,90),'negative')]:
        matches = [p for p in report['peaks'] if p['sign']==polarity and
                   all(abs(p['points'][f'f{i}']-v)<=2 for i,v in enumerate(center))]
        assert len(matches)==1


@pytest.mark.parametrize('mask', [np.ones((4,4)), np.ones((3,4),dtype=bool), np.zeros((4,4),dtype=bool)])
def test_invalid_noise_masks_fail_closed(mask):
    with pytest.raises(NMRNextError):
        pick_peaks(_spectrum(np.zeros((4,4))), noise_mask=mask)


@pytest.mark.parametrize('sign', [-1,1])
@pytest.mark.parametrize('steps', [[.1,.08],[-.1,.08],[.1,-.08]])
def test_affine_reference_baseline_signed_finite_gaussian_volume(sign, steps):
    n,m=151,177
    yy,xx=np.indices((n,m))
    x=(yy-75)*.1; y=(xx-88)*.08
    signal=sign*2*np.exp(-x*x/(2*.7**2)-y*y/(2*1.1**2))
    baseline=.8+.03*x-.02*y
    d=_spectrum(signal+baseline,steps=steps)
    reference=(yy<5)|(yy>145)|(xx<5)|(xx>171)
    bounds=[[25,125],[28,148]]
    report=integrate_regions(d,_regions(d,bounds),baseline='reference_linear',baseline_mask=reference)
    got=report['regions'][0]
    expected=sign*2*2*math.pi*.7*1.1*math.erf(5/(.7*math.sqrt(2)))*math.erf(4.8/(1.1*math.sqrt(2)))
    assert got['integral']==pytest.approx(expected,rel=1e-3)
    assert got['integral_unit']=='intensity*Hz^2'
    assert got['baseline']['sample_count']==int(reference.sum())
    assert got['baseline']['coefficients']==pytest.approx([.8,.03*15,-.02*14.08],abs=1e-7)
    assert 'reference_baseline_requires_signal_free_mask' in got['flags']


def test_reference_baseline_1d_fractional_endpoint_independent_quadrature():
    x=np.arange(401)
    signal=-3/(1+((x-200)/4)**2)
    baseline=.5+.0008*x
    # True off-region baseline observations: signal is explicitly zero outside support.
    signal[(x<100)|(x>300)]=0
    d=_spectrum(signal+baseline,steps=[-.3])
    reference=(x<80)|(x>320)
    lo,hi=170.25,235.75
    got=integrate_regions(d,_regions(d,[[lo,hi]]),baseline='reference_linear',baseline_mask=reference)['regions'][0]
    grid=np.r_[lo,np.arange(math.ceil(lo),math.floor(hi)+1),hi]
    values=np.interp(grid,x,signal)
    expected=sum((values[i]+values[i+1])*.5*(grid[i+1]-grid[i])*.3 for i in range(len(grid)-1))
    assert got['integral']==pytest.approx(expected,rel=1e-11,abs=1e-11)


def test_reference_baseline_rejects_overlap_rank_and_invalid_masks():
    d=_spectrum(np.zeros((20,30)))
    regions=_regions(d,[[5.5,10.5],[8,16]])
    mask=np.zeros((20,30),dtype=bool); mask[5,8]=True; mask[0,:]=True
    with pytest.raises(NMRNextError) as e:
        integrate_regions(d,regions,baseline='reference_linear',baseline_mask=mask)
    assert e.value.code=='CONTAMINATED_BASELINE_MASK'
    mask=np.zeros((20,30),dtype=bool); mask[0,:]=True
    with pytest.raises(NMRNextError) as e:
        integrate_regions(d,regions,baseline='reference_linear',baseline_mask=mask)
    assert e.value.code=='UNDERDETERMINED_BASELINE'
    for invalid in [None, np.ones((20,30)), np.ones((19,30),dtype=bool),np.zeros((20,30),dtype=bool)]:
        with pytest.raises(NMRNextError):
            integrate_regions(d,regions,baseline='reference_linear',baseline_mask=invalid)
    with pytest.raises(NMRNextError):
        integrate_regions(d,regions,baseline_mask=np.ones((20,30),dtype=bool))


def test_full_quadrature_mask_uses_positive_anchors_and_preserves_negative_peak():
    shape=(256,320)
    data=np.zeros((4,*shape))
    expected=np.zeros_like(data)
    mask=np.zeros(shape,dtype=bool)
    for cy,cx,amp in [(55,65,1),(115,140,.8),(175,215,.7),(208,270,-.5)]:
        y=1/(1+1j*(np.arange(shape[0])-cy)/2.4)
        x=1/(1+1j*(np.arange(shape[1])-cx)/3.1)
        for target,fy,fx in [(expected,y,x),(data,y*np.exp(.4j),x*np.exp(-.6j))]:
            target[0]+=amp*np.outer(fy.real,fx.real)
            target[1]+=amp*np.outer(fy.imag,fx.real)
            target[2]+=amp*np.outer(fy.real,fx.imag)
            target[3]+=amp*np.outer(fy.imag,fx.imag)
        if amp>0:mask[cy-20:cy+21,cx-24:cx+25]=True
    axes=[Axis('n',shape[0],domain='frequency',unit='Hz'),Axis('c',shape[1],domain='frequency',unit='Hz')]
    d=Dataset(data,axes,['n','c'])
    result,report=autophase(d,inclusion_mask=mask)
    assert [op['p0'] for op in report['operations']]==pytest.approx([-np.rad2deg(.4),np.rad2deg(.6)],abs=.5)
    assert [op['p1'] for op in report['operations']]==pytest.approx([0,0],abs=1)
    assert result.data[0,208,270]<0
    np.testing.assert_allclose(result.data,expected,atol=.005)
    assert max(report['objective']['per_line_relative_rms'])<.04
    replay=run_operations(d,report['operations'])
    np.testing.assert_array_equal(replay.data,result.data)
    assert dataset_digest(replay)==report['result_dataset_digest']


def test_integer_and_fractional_region_fast_paths_match_direct_linear_oracle():
    yy,xx=np.indices((60,80),dtype=float)
    d=_spectrum(2+.3*yy-.7*xx,steps=[-2,.5])
    for bounds in [[[3,45],[11,66]],[[3.25,45.75],[11.4,66.8]]]:
        lo,hi=bounds[0];left,right=bounds[1]
        expected=(hi-lo)*(right-left)*(2+.3*(lo+hi)/2-.7*(left+right)/2)
        actual=integrate_regions(d,_regions(d,bounds))['regions'][0]['integral']
        assert actual==pytest.approx(expected,rel=1e-12,abs=1e-11)

# SCI-01: coordinate reflection must not conjugate the line model.
def _reflection(identity):
    return {'op':'reference','axis':identity,'reference_ppm':0.,'reference_hz':0.,
            'observation_mhz':400.,'frequency_scale':-1}


def _oriented_lines_1d(direction=1):
    n=2048
    j=np.arange(n);pivot=(n-1)/2
    z=sum(a/(1+1j*direction*(j-c)/4) for c,a in [(300,1.),(1000,.7),(1700,.9)])
    z*=np.exp(1j*np.deg2rad(31+40*(j-pivot)/n))
    d=Dataset(np.stack([z.real,z.imag]),[Axis('h',n,domain='frequency',unit='Hz',step=direction)],['h'])
    mask=np.zeros(n,dtype=bool)
    for c in [300,1000,1700]:mask[c-60:c+61]=True
    return d,mask


@pytest.mark.parametrize('direction',[-1,1])
def test_phase_index_orientation_survives_coordinate_reflections_and_sample_reverse(direction):
    from nmrnext.operations import quadrature_index_orientation
    d,mask=_oriented_lines_1d(direction)
    before=dataset_digest(d)
    expected,report=autophase(d,inclusion_mask=mask)
    assert report['operation']['p0']==pytest.approx(-31,abs=.5)
    assert report['operation']['p1']==pytest.approx(-40,abs=1.)
    reflected=run_operations(d,[_reflection('h')])
    assert quadrature_index_orientation(reflected)=={'h':direction}
    np.testing.assert_array_equal(reflected.data,d.data)
    actual,changed=autophase(reflected,inclusion_mask=mask)
    assert changed['operation']==report['operation']
    np.testing.assert_array_equal(actual.data,expected.data)
    repeated=run_operations(reflected,[_reflection('h')])
    assert quadrature_index_orientation(repeated)=={'h':direction}
    assert autophase(repeated,inclusion_mask=mask)[1]['operation']==report['operation']
    reversed_data=run_operations(reflected,[{'op':'reverse','axis':'h'}])
    assert quadrature_index_orientation(reversed_data)=={'h':-direction}
    reversed_result,reversed_report=autophase(reversed_data,inclusion_mask=mask[::-1])
    assert reversed_report['operation']['p0']==pytest.approx(-31,abs=.5)
    assert reversed_report['operation']['p1']==pytest.approx(40,abs=1.)
    np.testing.assert_allclose(reversed_result.data,expected.data[:,::-1],atol=1e-5)
    assert dataset_digest(d)==before


def _oriented_tensor(directions=(1,1)):
    shape=(256,320);data=np.zeros((4,*shape));mask=np.zeros(shape,dtype=bool)
    for cy,cx,amp in [(55,65,1),(115,140,.8),(175,215,.7)]:
        fy=1/(1+1j*directions[0]*(np.arange(shape[0])-cy)/2.4)
        fx=1/(1+1j*directions[1]*(np.arange(shape[1])-cx)/3.1)
        fy*=np.exp(1j*np.deg2rad(23+40*(np.arange(shape[0])-(shape[0]-1)/2)/shape[0]))
        fx*=np.exp(1j*np.deg2rad(-34-50*(np.arange(shape[1])-(shape[1]-1)/2)/shape[1]))
        for k,(a,b) in enumerate([(fy.real,fx.real),(fy.imag,fx.real),(fy.real,fx.imag),(fy.imag,fx.imag)]):
            data[k]+=amp*np.outer(a,b)
        mask[cy-20:cy+21,cx-24:cx+25]=True
    axes=[Axis('n',shape[0],domain='frequency',unit='Hz',step=2*directions[0]),
          Axis('c',shape[1],domain='frequency',unit='Hz',step=3*directions[1])]
    return Dataset(data,axes,['n','c']),mask


@pytest.mark.parametrize('directions',[(1,1),(-1,1),(1,-1),(-1,-1)])
@pytest.mark.parametrize('reflected_axes',[['n'],['c'],['n','c']])
def test_full_tensor_first_order_reflection_keeps_every_quadrature(directions,reflected_axes):
    d,mask=_oriented_tensor(directions)
    expected,original=autophase(d,inclusion_mask=mask)
    assert [op['p0'] for op in original['operations']]==pytest.approx([-23,34],abs=.5)
    assert [op['p1'] for op in original['operations']]==pytest.approx([-40,50],abs=1.)
    reflected=run_operations(d,[_reflection(a) for a in reflected_axes])
    result,report=autophase(reflected,inclusion_mask=mask)
    assert report['operations']==original['operations']
    np.testing.assert_array_equal(result.data,expected.data)
    assert report['quadrature_index_orientation']==dict(zip(['n','c'],directions))
    np.testing.assert_array_equal(run_operations(reflected,report['operations']).data,result.data)


def test_orientation_survives_reverse_crop_transpose_native_storage_and_replay(tmp_path):
    from nmrnext.io import read_dataset,write_dataset
    from nmrnext.operations import quadrature_index_orientation
    d,mask=_oriented_tensor()
    exact=run_operations(d,[{'op':'phase','axis':a.id,'p0':p0,'p1':p1,'pivot':(a.size-1)/2}
                            for a,p0,p1 in zip(d.axes,[-23,34],[-40,50])])
    changes=[_reflection('n'),_reflection('c'),{'op':'reverse','axis':'n'},
             {'op':'crop','axis':'n','start':20,'stop':240},
             {'op':'crop','axis':'c','start':20,'stop':300},{'op':'transpose','order':['c','n']}]
    changed=run_operations(d,changes)
    expected=run_operations(exact,changes)
    assert quadrature_index_orientation(changed)=={'n':-1,'c':1}
    path=tmp_path/'orientation.nmrh5'
    write_dataset(changed,path)
    restored=read_dataset(path)
    assert restored.to_metadata()==changed.to_metadata()
    assert dataset_digest(restored)==dataset_digest(changed)
    transformed_mask=mask[::-1][20:240,20:300].T
    result,report=autophase(restored,inclusion_mask=transformed_mask)
    np.testing.assert_allclose(result.data,expected.data,atol=.005)
    replay=run_operations(restored,report['operations'])
    assert replay.to_metadata()==result.to_metadata()
    assert dataset_digest(replay)==report['result_dataset_digest']
    np.testing.assert_array_equal(replay.data,result.data)


def test_orientation_legacy_history_and_explicit_forms_have_same_digest(tmp_path):
    from nmrnext.io import read_dataset,write_dataset
    from nmrnext.operations import quadrature_index_orientation
    d,mask=_oriented_lines_1d()
    reflected=run_operations(d,[_reflection('h')])
    legacy=Dataset(reflected.data,reflected.axes,reflected.complex_axes,{},reflected.provenance)
    assert quadrature_index_orientation(legacy)=={'h':1}
    assert dataset_digest(legacy)==dataset_digest(reflected)
    unrelated=Dataset(reflected.data,reflected.axes,reflected.complex_axes,
                      {**reflected.metadata,'note':'unrelated'},reflected.provenance)
    assert dataset_digest(unrelated)==dataset_digest(reflected)
    lost=Dataset(reflected.data,reflected.axes,reflected.complex_axes)
    assert quadrature_index_orientation(lost)=={'h':-1}
    assert dataset_digest(lost)!=dataset_digest(reflected)
    path=tmp_path/'legacy-reflected.nmrh5';write_dataset(legacy,path)
    restored=read_dataset(path)
    assert autophase(restored,inclusion_mask=mask)[1]['operation']==autophase(d,inclusion_mask=mask)[1]['operation']
    # Backward-compatible identity for ordinary constructed frequency snapshots.
    import hashlib,json
    header={'axes':[a.to_dict() for a in d.axes],'complex_axes':list(d.complex_axes),
            'dtype':d.data.dtype.str,'shape':list(d.data.shape)}
    h=hashlib.sha256(json.dumps(header,sort_keys=True,separators=(',',':'),allow_nan=False).encode())
    h.update(memoryview(d.data).cast('B'))
    assert dataset_digest(d)==h.hexdigest()


@pytest.mark.parametrize('value',[{'h':True},{'h':1.0},{'h':0},{'wrong':1},{},[1]])
def test_invalid_orientation_metadata_fails_before_scientific_use(value):
    d,_=_oriented_lines_1d()
    bad=Dataset(d.data,d.axes,d.complex_axes,{'quadrature_index_orientation':value})
    for call in [lambda:autophase(bad),lambda:dataset_digest(bad),lambda:run_operations(bad,[{'op':'phase','axis':'h'}])]:
        with pytest.raises(NMRNextError) as error:call()
        assert error.value.code=='INVALID_QUADRATURE_ORIENTATION'


def test_legacy_invalid_reference_scale_fails_and_fft_resets_orientation():
    from nmrnext.operations import quadrature_index_orientation
    d,_=_oriented_lines_1d()
    bad=Dataset(d.data,d.axes,d.complex_axes,{},[{'operation':{'op':'reference','axis':'h','frequency_scale':True}}])
    with pytest.raises(NMRNextError,match='frequency_scale'):dataset_digest(bad)
    n=128;t=np.arange(n)
    z=np.exp(-t/10+2j*np.pi*.1*t)
    raw=Dataset(np.stack([z.real,z.imag]),[Axis('h',n,step=.001)],['h'])
    transformed=run_operations(raw,[{'op':'fft','axis':'h'}])
    assert quadrature_index_orientation(transformed)=={'h':1}
    reflected=run_operations(transformed,[_reflection('h')])
    with pytest.raises(NMRNextError) as error:run_operations(reflected,[{'op':'fft','axis':'h','inverse':True}])
    assert error.value.code=='INVALID_FREQUENCY_GRID'
    restored=run_operations(reflected,[_reflection('h'),{'op':'fft','axis':'h','inverse':True}])
    assert 'quadrature_index_orientation' not in restored.metadata
    np.testing.assert_allclose(restored.data,raw.data,atol=1e-14)
    again=run_operations(restored,[{'op':'fft','axis':'h'}])
    assert quadrature_index_orientation(again)=={'h':1}
