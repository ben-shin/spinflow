"""Literal signed channel selection for explicit processing-time quadrature loss."""
import struct

import numpy as np
import pytest

from nmrnext.errors import NMRNextError
from nmrnext.io import export_preflight, read_dataset, write_dataset
from nmrnext.model import Axis, Dataset, DatasetHeader
from nmrnext.operations import apply_operation, quadrature_index_orientation, run_operations
from nmrnext.planning import plan_operations


def sample(dtype=np.float32,bits=('n','h')):
    axes=(Axis('n',2,domain='frequency',unit='Hz',start=10.,step=-2.,label='N15',nucleus='N15',observation_mhz=50.,reference_ppm=120.),
          Axis('h',3,domain='frequency',unit='Hz',start=5.,step=-1.,label='H1',nucleus='H1',observation_mhz=500.,reference_ppm=4.7))
    values=np.array([[[-1,-2,3],[4,-5,6]],[[11,12,-13],[14,15,-16]],
                     [[-21,22,23],[-24,25,26]],[[31,-32,33],[34,-35,36]]],dtype=dtype)
    return Dataset(values,axes,bits,{'import':{'original_encoding':'States'},
                                    'quadrature_index_orientation':{'n':1,'h':-1}},[{'fixture':'literal'}])


@pytest.mark.parametrize('dtype',[np.float32,np.float64])
@pytest.mark.parametrize('bits',[('n','h'),('h','n')])
@pytest.mark.parametrize('axis',['n','h'])
def test_one_hot_channels_keep_only_literal_zero_bit(dtype,bits,axis):
    base=sample(dtype,bits)
    expected_indices={'n':{'n':(0,2),'h':(0,1)},
                      'h':{'n':(0,1),'h':(0,2)}}[bits[0]][axis]
    for channel in range(4):
        values=np.zeros_like(base.data);values[channel,0,0]=-7
        source=Dataset(values,base.axes,bits,base.metadata)
        result=apply_operation(source,{'op':'discard_imaginary','axis':axis})
        expected=np.zeros((2,2,3),dtype=dtype)
        if channel in expected_indices:
            expected[expected_indices.index(channel),0,0]=-7
        np.testing.assert_array_equal(result.data,expected)


@pytest.mark.parametrize('dtype',[np.float32,np.float64])
@pytest.mark.parametrize('bits,axis,indices,remaining',[
    (('n','h'),'h',[0,1],('n',)),(('n','h'),'n',[0,2],('h',)),
    (('h','n'),'h',[0,2],('n',)),(('h','n'),'n',[0,1],('h',))])
@pytest.mark.parametrize('transpose',[False,True])
def test_literal_selected_bits_signed_dtype_and_source_immutable(dtype,bits,axis,indices,remaining,transpose):
    original=sample(dtype,bits)
    before=original.data.copy();before_meta=original.to_metadata()
    source=apply_operation(original,{'op':'transpose','order':['h','n']}) if transpose else original
    out=apply_operation(source,{'op':'discard_imaginary','axis':axis})
    expected=before[indices].transpose(0,2,1) if transpose else before[indices]
    np.testing.assert_array_equal(out.data,expected)
    assert out.data.dtype==dtype and out.complex_axes==remaining
    assert out.axis(axis).encoding=='real'
    for a in source.axes:
        np.testing.assert_array_equal(out.coordinates(a.id),source.coordinates(a.id))
        np.testing.assert_array_equal(out.coordinates(a.id,'ppm'),source.coordinates(a.id,'ppm'))
    assert out.metadata['import']==source.metadata['import']
    assert quadrature_index_orientation(out)=={a:source.metadata['quadrature_index_orientation'][a] for a in remaining}
    entry=out.provenance[-1]
    assert entry['losses']==['discarded_imaginary_quadrature']
    assert entry['discard_imaginary']['retained_input_components']==indices
    assert entry['discard_imaginary']['input_complex_axes']==list(bits)
    assert entry['discard_imaginary']['output_complex_axes']==list(remaining)
    assert entry['input_shape']==list(source.data.shape) and entry['output_shape']==list(out.data.shape)
    np.testing.assert_array_equal(original.data,before)
    assert original.to_metadata()==before_meta and not out.data.flags.writeable
    with pytest.raises(ValueError):out.data.flags.writeable=True


@pytest.mark.parametrize('dtype',[np.float32,np.float64])
def test_nonzero_phase_then_two_discards_keep_signed_real_and_remap_pair(dtype):
    ds=Dataset(np.array([[-1.],[2.],[3.],[-4.]],dtype=dtype).reshape(4,1,1),
        (Axis('n',1,domain='frequency',unit='Hz'),Axis('h',1,domain='frequency',unit='Hz')),('n','h'))
    operations=[{'op':'phase','axis':'h','p0':90}, {'op':'discard_imaginary','axis':'h'},
                {'op':'phase','axis':'n','p0':90}, {'op':'discard_imaginary','axis':'n'}]
    out=run_operations(ds,operations)
    np.testing.assert_allclose(out.data,[[[-4.]]],atol=1e-14 if dtype==np.float64 else 1e-6)
    assert out.complex_axes==() and all(a.encoding=='real' for a in out.axes)
    assert 'quadrature_index_orientation' not in out.metadata


def test_planner_evolves_components_and_keeps_larger_earlier_peak(monkeypatch):
    ds=Dataset(np.ones((4,8,16),dtype=np.float32),(Axis('n',8),Axis('h',16)),('n','h'))
    ops=[{'op':'zero_fill','axis':'h','size':32},{'op':'fft','axis':'h'},
         {'op':'discard_imaginary','axis':'h'},{'op':'transpose','order':['h','n']},
         {'op':'fft','axis':'n'},{'op':'discard_imaginary','axis':'n'},
         {'op':'transpose','order':['n','h']}]
    header=DatasetHeader.from_dataset(ds)
    expected=plan_operations(ds,ops)
    with monkeypatch.context() as m:
        m.setattr('nmrnext.operations._execute',lambda *a,**k:pytest.fail('Kernel called during plan'))
        planned=plan_operations(header,ops)
    assert planned['steps']==expected['steps']
    assert [s['output_shape'][0] for s in planned['steps']]==[4,4,2,2,2,1,1]
    assert planned['steps'][2]['output_complex_axes']==['n'] and planned['output_complex_axes']==[]
    assert planned['estimated_working_bytes']==ds.data.nbytes+12*(4*8*32*4)+4*1024**2
    with pytest.raises(NMRNextError,match='budget'):
        plan_operations(header,ops,memory_bytes=planned['estimated_working_bytes']-1)
    current=ds
    for op,step in zip(ops,planned['steps']):
        current=apply_operation(current,op)
        assert list(current.data.shape)==step['output_shape']
        assert list(current.complex_axes)==step['output_complex_axes']
        assert current.data.nbytes==step['output_bytes']
        assert [a.to_dict() for a in current.axes]==step['output_axes']


@pytest.mark.parametrize('op',[{'op':'discard_imaginary','axis':'missing'},
    {'op':'discard_imaginary','axis':'n','version':'2'}, {'op':'discard_imaginary','axis':'n','magnitude':True}])
def test_invalid_parameters_refused_before_any_kernels(monkeypatch,op):
    monkeypatch.setattr('nmrnext.operations._execute',lambda *a,**k:pytest.fail('Preflight did not stop'))
    with pytest.raises(NMRNextError):run_operations(sample(),[{'op':'phase','axis':'h','p0':10},op])


@pytest.mark.parametrize('axis,bits',[(Axis('x',4),('x',)),
    (Axis('x',4,role='series',domain='frequency',unit='Hz'),('x',)),
    (Axis('x',4,domain='frequency',unit='Hz',encoding='real'),())])
def test_wrong_domain_role_or_already_real_refused(axis,bits):
    ds=Dataset(np.ones((2**len(bits),4),dtype=np.float32),(axis,),bits)
    for obj in (ds,DatasetHeader.from_dataset(ds)):
        with pytest.raises(NMRNextError):plan_operations(obj,[{'op':'discard_imaginary','axis':'x'}])


def test_repeated_discard_and_later_complex_operations_fail_full_preflight():
    for next_op in ({'op':'discard_imaginary','axis':'h'}, {'op':'phase','axis':'h','p0':5}, {'op':'fft','axis':'h','inverse':True}):
        with pytest.raises(NMRNextError):run_operations(sample(),[{'op':'discard_imaginary','axis':'h'},next_op])


def test_roundtrip_and_export_have_no_spurious_projection_and_literal_ppm(tmp_path):
    out=run_operations(sample(),[{'op':'discard_imaginary','axis':'h'}, {'op':'discard_imaginary','axis':'n'}])
    path=tmp_path/'real.nmrh5';write_dataset(out,path);restored=read_dataset(path)
    np.testing.assert_array_equal(restored.data,out.data)
    assert restored.to_metadata()==out.to_metadata()
    report=export_preflight(restored,'nmrpipe-real',allow_losses=('metadata','provenance'))
    assert report['ok'] and report['losses']==['metadata','provenance']
    pipe=tmp_path/'real.ft2';write_dataset(restored,pipe,allow_losses=('metadata','provenance'))
    raw=pipe.read_bytes();h=struct.unpack('<512f',raw[:2048])
    np.testing.assert_array_equal(np.frombuffer(raw,dtype='<f4',offset=2048).reshape(2,3),[[-1,-2,3],[4,-5,6]])
    for axis,n,sw,obs,origin in [('n',2,229,218,249),('h',3,100,119,101)]:
        first=h[origin]+h[sw]-h[sw]/n
        ppm=(first-np.arange(n)*h[sw]/n)/h[obs]
        np.testing.assert_allclose(ppm,restored.coordinates(axis,'ppm'),rtol=0,atol=1e-5)
