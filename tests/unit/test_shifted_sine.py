"""Known windows and explicit per-axis physical Fourier-direction semantics."""
import numpy as np
import pytest

from nmrnext.errors import NMRNextError
from nmrnext.io import read_dataset, write_dataset
from nmrnext.model import Axis, Dataset
from nmrnext.operations import apply_operation, run_operations
from nmrnext.planning import plan_operations


def one(dtype=np.float32):
    return Dataset(np.ones((2,5),dtype=dtype),(Axis('x',5,step=.002),),('x',))


def test_shifted_sine_known_angles_and_first_point():
    result=apply_operation(one(),{'op':'window','axis':'x','kind':'sine','off':.25,'end':.75,'power':2,'first_point_scale':.5})
    expected=np.array([.25,(2+np.sqrt(2))/4,1,(2+np.sqrt(2))/4,.5],dtype=np.float32)
    np.testing.assert_allclose(result.data,np.stack((expected,expected)),rtol=1e-7,atol=1e-7)


@pytest.mark.parametrize('dtype',[np.float32,np.float64])
def test_default_sine_exact_old_expression(dtype):
    ds=one(dtype)
    result=apply_operation(ds,{'op':'window','axis':'x','kind':'sine','power':1.3})
    old=(np.sin(np.pi*np.arange(5)/(5-1))**1.3).astype(dtype)
    np.testing.assert_array_equal(result.data,ds.data*old)


@pytest.mark.parametrize('dtype',[np.float32,np.float64])
@pytest.mark.parametrize('size',[2,64,256,1024,1500])
@pytest.mark.parametrize('off',[.05,.2,.35,.7])
def test_fractional_shifted_sine_endpoint_is_finite(dtype,size,off):
    ds=Dataset(np.ones((2,size),dtype=dtype),(Axis('x',size),),('x',))
    with np.errstate(invalid='raise'):
        result=apply_operation(ds,{'op':'window','axis':'x','kind':'sine','off':off,'end':1.,'power':.5})
    assert result.data.dtype==dtype
    assert np.isfinite(result.data).all() and np.all(result.data>=0)
    assert result.data[0,0]==pytest.approx(np.sqrt(np.sin(np.pi*off)),rel=2e-7)
    # The exact endpoint is sin(pi)**.5 = 0. Positive floating-point
    # residuals are preserved; only negative roundoff excursions are clipped.
    assert result.data[0,-1]<=3e-8
    if size>2:
        theta=np.pi*(off+(1-off)*np.arange(1,size-1)/(size-1))
        np.testing.assert_allclose(result.data[0,1:-1],np.sqrt(np.sin(theta)),rtol=2e-7,atol=1e-14)


@pytest.mark.parametrize('dtype',[np.float32,np.float64])
def test_fractional_shifted_sine_known_angles(dtype):
    ds=Dataset(np.ones((2,3),dtype=dtype),(Axis('x',3),),('x',))
    result=apply_operation(ds,{'op':'window','axis':'x','kind':'sine','off':.5,'end':1.,'power':.5})
    np.testing.assert_allclose(result.data[:,:2],[[1.,2**(-.25)]]*2,rtol=1e-7,atol=1e-15)


@pytest.mark.parametrize('dtype',[np.float32,np.float64])
@pytest.mark.parametrize('size',[2,64,256,1024,1500])
def test_fractional_default_sine_preserves_original_expression(dtype,size):
    ds=Dataset(np.ones((2,size),dtype=dtype),(Axis('x',size),),('x',))
    result=apply_operation(ds,{'op':'window','axis':'x','kind':'sine','power':.5})
    old=np.sqrt(np.sin(np.pi*np.arange(size)/(size-1))).astype(dtype)
    np.testing.assert_array_equal(result.data,ds.data*old)


@pytest.mark.parametrize('bad',[{'off':-.1},{'end':1.1},{'off':.8,'end':.2},{'off':True},{'end':float('nan')},{'off':None},{'kind':'exponential','off':0}])
def test_invalid_window_parameters_fail_before_execution(bad):
    op={'op':'window','axis':'x','kind':'sine',**bad}
    with pytest.raises(NMRNextError):plan_operations(one(),[op])


@pytest.mark.parametrize('scale',[True,False,-1.,0,2,None,float('nan'),float('inf'),'descending'])
def test_invalid_reference_scale_refused(scale):
    ds=apply_operation(one(),{'op':'fft','axis':'x'})
    with pytest.raises(NMRNextError,match='frequency_scale'):
        plan_operations(ds,[{'op':'reference','axis':'x','reference_ppm':4.,'reference_hz':0.,'observation_mhz':400.,'frequency_scale':scale}])


@pytest.mark.parametrize('backend',['numpy','native'])
def test_explicit_reflection_multiaxis_inverse_transpose_and_storage(tmp_path,backend):
    values=np.arange(4*3*5,dtype=np.float64).reshape(4,3,5)
    ds=Dataset(values,(Axis('a0',3,start=.005,step=.002),Axis('a1',5,step=.001)),('a0','a1'))
    intermediate=run_operations(ds,[{'op':'fft','axis':'a1'},{'op':'transpose','order':['a1','a0']}],backend=backend)
    assert intermediate.axis('a1').domain=='frequency' and intermediate.axis('a0').domain=='time'
    both=apply_operation(intermediate,{'op':'fft','axis':'a0'},backend=backend)
    references=[{'op':'reference','axis':a,'reference_ppm':5.,'reference_hz':0.,'observation_mhz':400.,'frequency_scale':-1} for a in ('a0','a1')]
    calibrated=run_operations(both,references,backend=backend)
    np.testing.assert_array_equal(calibrated.data,both.data)
    for a,dt,n in [('a0',.002,3),('a1',.001,5)]:
        np.testing.assert_allclose(calibrated.coordinates(a),-np.fft.fftshift(np.fft.fftfreq(n,dt)))
        with pytest.raises(NMRNextError,match='canonical'):
            apply_operation(calibrated,{'op':'fft','axis':a,'inverse':True})
    path=tmp_path/'reflected.nmrh5';write_dataset(calibrated,path);restored=read_dataset(path)
    assert restored.to_metadata()==calibrated.to_metadata()
    restored=run_operations(restored,references,backend=backend)
    result=run_operations(restored,[{'op':'fft','axis':'a0','inverse':True},{'op':'fft','axis':'a1','inverse':True},{'op':'transpose','order':['a0','a1']}],backend=backend)
    np.testing.assert_allclose(result.data,values,atol=2e-14)
    assert result.axis('a0').start==.005 and result.axis('a0').step==.002
    for op in ({'op':'crop','axis':'a1','start':1,'stop':4},{'op':'reverse','axis':'a1'}):
        altered=apply_operation(both,op)
        with pytest.raises(NMRNextError,match='canonical'):apply_operation(altered,{'op':'fft','axis':'a1','inverse':True})


def test_reference_default_preserves_coordinates_and_values():
    ds=apply_operation(one(),{'op':'fft','axis':'x'})
    result=apply_operation(ds,{'op':'reference','axis':'x','reference_ppm':4.,'reference_hz':0.,'observation_mhz':400.})
    np.testing.assert_array_equal(result.data,ds.data)
    np.testing.assert_array_equal(result.coordinates('x'),ds.coordinates('x'))
    assert result.provenance[-1]['operation']['frequency_scale']==1


@pytest.mark.parametrize('dimensions',[1,2])
def test_signed_integral_unchanged_by_explicit_frequency_reflection(dimensions):
    from nmrnext.analysis import integrate_regions
    shape=(5,)*dimensions
    values=-np.ones(shape,dtype=np.float64)*3
    axes=tuple(Axis(f'a{i}',5,domain='frequency',unit='Hz',step=2.,observation_mhz=400.) for i in range(dimensions))
    ds=Dataset(values[None],axes)
    regions=[{'id':'signed','bounds':{a.id:[0,4] for a in axes},'unit':'points'}]
    expected=-3*8**dimensions
    calibrated=run_operations(ds,[{'op':'reference','axis':a.id,'reference_ppm':4.,'reference_hz':0.,'frequency_scale':-1} for a in axes])
    assert integrate_regions(ds,regions)['regions'][0]['integral']==pytest.approx(expected)
    assert integrate_regions(calibrated,regions)['regions'][0]['integral']==pytest.approx(expected)


def test_phase_conjugation_algebra_uses_point_denominator():
    # NMRPipe PS and native PS both use exp(+i*(p0+p1*j/N)).
    # Conjugating a spectrum therefore negates both phase angles, including p1.
    z=np.array([2+3j,-1+4j,5-2j,3+1j,1-6j])
    ds=Dataset(np.stack((z.real,-z.imag)),(Axis('x',5),),('x',))
    got=apply_operation(ds,{'op':'phase','axis':'x','p0':17.7,'p1':36.0})
    archived=z*np.exp(1j*np.deg2rad(-17.7-36*np.arange(5)/5))
    np.testing.assert_allclose(got.data,np.stack((archived.real,-archived.imag)),rtol=2e-15)
