"""Planning-only values never allocate or masquerade as measured samples."""
from dataclasses import FrozenInstanceError
import tracemalloc

import numpy as np
import pytest

from nmrnext.errors import NMRNextError
from nmrnext.io import export_preflight, write_dataset
from nmrnext.model import Axis, Dataset, DatasetHeader
from nmrnext.operations import quadrature_index_orientation, run_operations
from nmrnext.planning import plan_operations


def test_huge_descriptor_has_constant_memory_and_no_sample_interface(monkeypatch):
    header=DatasetHeader((Axis('x',2**40,step=.001),),('x',),'float64')
    assert header.shape==(2,2**40) and header.nbytes==2**44
    assert not isinstance(header,Dataset) and not hasattr(header,'data') and not hasattr(header,'coordinates')
    for name in ('zeros','empty','ones','arange','asarray','frombuffer'):
        monkeypatch.setattr(np,name,lambda *a,**k:pytest.fail('Planning allocated a numeric array'))
    tracemalloc.start()
    try:
        plan=plan_operations(header,[{'op':'fft','axis':'x'}])
        _,peak=tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak<1<<20
    assert plan['estimated_working_bytes']==13*header.nbytes+4*1024**2
    with pytest.raises(NMRNextError,match='budget'):
        plan_operations(header,[],memory_bytes=256*1024**2)


def test_descriptor_is_immutable_and_defensively_copies_metadata():
    metadata={'example':{'values':[1]}}
    h=DatasetHeader((Axis('x',4),),('x',),metadata=metadata,source_identity={'scope':'header_only'})
    metadata['example']['values'].append(2)
    h.metadata['example']['values'].append(3)
    assert h.metadata=={'example':{'values':[1]}}
    with pytest.raises(FrozenInstanceError):h._dtype=np.dtype('float32')


@pytest.mark.parametrize('axes,bits,dtype',[((),(),'float64'),((Axis('x',3),Axis('x',4)),(),'float64'),
    ((Axis('x',3,encoding='real'),),('x',),'float64'),((Axis('x',3),),('missing',),'float32'),
    ((Axis('x',3),),(),'int32'),((Axis('x',3),),(),'complex64')])
def test_invalid_descriptors_rejected(axes,bits,dtype):
    with pytest.raises(NMRNextError):DatasetHeader(axes,bits,dtype)


def test_descriptor_cannot_execute_or_export_even_empty_recipe(tmp_path):
    h=DatasetHeader((Axis('x',5),),('x',))
    for action in (lambda:run_operations(h,[]),lambda:export_preflight(h,'nmrnext-hdf5'),
                   lambda:write_dataset(h,tmp_path/'fake.nmrh5')):
        with pytest.raises(NMRNextError,match='numeric'):action()
    assert not (tmp_path/'fake.nmrh5').exists()


@pytest.mark.parametrize('dtype',[np.float32,np.float64])
def test_small_numeric_and_header_plans_share_axes_bytes_and_orientation(dtype):
    ds=Dataset(np.arange(4*4*6,dtype=dtype).reshape(4,4,6),
               (Axis('n',4,step=.004),Axis('h',6,step=.001)),('n','h'))
    ops=[{'op':'window','axis':'h','kind':'sine','off':.35,'end':.9},
         {'op':'zero_fill','axis':'h','size':8},{'op':'fft','axis':'h'},
         {'op':'reference','axis':'h','reference_ppm':4.7,'reference_hz':0,'observation_mhz':500,'frequency_scale':-1},
         {'op':'reverse','axis':'h'},{'op':'transpose','order':['h','n']},
         {'op':'fft','axis':'n'},{'op':'crop','axis':'n','start':1,'stop':4}]
    numeric=plan_operations(ds,ops)
    header=plan_operations(DatasetHeader.from_dataset(ds),ops)
    assert numeric['steps']==header['steps']
    assert numeric['estimated_working_bytes']==header['estimated_working_bytes']
    result=run_operations(ds,ops)
    assert header['output_axes']==[a.to_dict() for a in result.axes]
    assert header['output_quadrature_index_orientation']==quadrature_index_orientation(result)=={'n':1,'h':-1}
    assert header['input_validation']=='header_only' and numeric['input_validation']=='numeric_dataset'
    bad=[*ops,{'op':'fft','axis':'h','inverse':True}]
    for source in (ds,DatasetHeader.from_dataset(ds)):
        with pytest.raises(NMRNextError,match='canonical'):plan_operations(source,bad)


def test_orientation_validation_is_not_skipped_by_header_plan():
    h=DatasetHeader((Axis('x',4,domain='frequency',unit='Hz'),),('x',),
                    metadata={'quadrature_index_orientation':{'x':True}})
    with pytest.raises(NMRNextError,match='orientation'):plan_operations(h,[])
