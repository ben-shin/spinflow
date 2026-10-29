"""Explicit current-grid width calibration, without changing signed samples."""
from dataclasses import replace
import json
from pathlib import Path
import struct

import numpy as np
import pytest

from nmrnext.errors import NMRNextError
from nmrnext.io import read_dataset, write_dataset
from nmrnext.model import Axis, Dataset, DatasetHeader
from nmrnext.operations import apply_operation, run_operations
from nmrnext.planning import plan_operations

RAW_WIDTH=5555.55555556
SCRIPT_WIDTH=5555.556


def operation(**kwargs):
    return dict(op='reference',axis='a0',reference_ppm=120,reference_hz=0,
                observation_mhz=50.648,frequency_scale=-1,spectral_width_hz=SCRIPT_WIDTH,**kwargs)


def source(dtype=np.float32,n=8):
    values=np.arange(4*n*3,dtype=dtype).reshape(4,n,3)-13
    values[::2]*=-1;values[0,0,0]=-0.0
    axes=(Axis('a0',n,domain='frequency',unit='Hz',start=-RAW_WIDTH/2,step=RAW_WIDTH/n,
               dwell_seconds=1/RAW_WIDTH,observation_mhz=50.648,nucleus='15N',label='15N'),
          Axis('a1',3,domain='frequency',unit='Hz',start=10,step=-5,
               observation_mhz=125.691,nucleus='13C',label='13C'))
    return Dataset(values,axes,('a0','a1'),{'import':{'sw1':RAW_WIDTH,'source_sha256':{'fid':'original'}},
                   'quadrature_index_orientation':{'a0':1,'a1':1}},[{'source':'literal signed fixture'}])


@pytest.mark.parametrize('dtype',[np.float32,np.float64])
@pytest.mark.parametrize('transposed',[False,True])
def test_samples_grid_and_provenance_exact_contract(dtype,transposed,tmp_path):
    original=source(dtype)
    ds=apply_operation(original,{'op':'transpose','order':['a1','a0']}) if transposed else original
    before=ds.data.tobytes();metadata=ds.to_metadata()
    result=apply_operation(ds,operation())
    assert result.data.tobytes()==before  # includes negative-zero sign bits
    assert result.data.dtype==dtype and result.complex_axes==ds.complex_axes
    assert [a.id for a in result.axes]==[a.id for a in ds.axes]
    assert result.metadata==ds.metadata and result.axis('a1')==ds.axis('a1')
    assert result.axis('a0').dwell_seconds==1/RAW_WIDTH
    assert result.axis('a0').start==SCRIPT_WIDTH/2
    assert result.axis('a0').step==-SCRIPT_WIDTH/8
    expected_hz=SCRIPT_WIDTH/2-np.arange(8)*SCRIPT_WIDTH/8
    np.testing.assert_array_equal(result.coordinates('a0'),expected_hz)
    np.testing.assert_allclose(result.coordinates('a0','ppm'),120+expected_hz/50.648,rtol=0,atol=1e-13)
    assert ds.data.tobytes()==before and ds.to_metadata()==metadata
    entry=result.provenance[-1]
    assert entry['operation']['spectral_width_hz']==SCRIPT_WIDTH
    detail=entry['reference_calibration']
    assert detail['requested_spectral_width_hz']==SCRIPT_WIDTH
    assert detail['prior_grid']=={'size':8,'start_hz':-RAW_WIDTH/2,'step_hz':RAW_WIDTH/8,'spectral_width_hz':RAW_WIDTH}
    assert detail['new_grid']=={'size':8,'start_hz':SCRIPT_WIDTH/2,'step_hz':-SCRIPT_WIDTH/8,'spectral_width_hz':SCRIPT_WIDTH}
    assert detail['acquisition_dwell_seconds']==1/RAW_WIDTH
    for obj in (ds,DatasetHeader.from_dataset(ds)):
        plan=plan_operations(obj,[operation()])
        assert plan['output_axes']==[a.to_dict() for a in result.axes]
        assert plan['output_complex_axes']==list(result.complex_axes)
    path=tmp_path/'calibrated.nmrh5';write_dataset(result,path)
    restored=read_dataset(path)
    assert restored.data.tobytes()==before and restored.to_metadata()==result.to_metadata()


def test_header_all_2048_coordinates_and_literal_float32_serialization():
    axis=source().axis('a0')
    header=DatasetHeader((replace(axis,size=2048,step=RAW_WIDTH/2048),),('a0',),'float32')
    plan=plan_operations(header,[operation()])
    result=Axis(**plan['output_axes'][0])
    expected=120+(SCRIPT_WIDTH/2-np.arange(2048)*SCRIPT_WIDTH/2048)/50.648
    actual=result.reference_ppm+(result.start+np.arange(2048)*result.step)/result.observation_mhz
    np.testing.assert_allclose(actual,expected,rtol=0,atol=1e-13)
    assert struct.unpack('<f',struct.pack('<f',result.size*abs(result.step)))[0]==5555.55615234375
    assert result.reference_ppm==120  # no reference-output carrier offset


def test_actual_synthetic_exports_encode_script_width_without_carrier_fitting(tmp_path):
    original=source()
    axes=tuple(replace(a,encoding='real',reference_ppm=55 if a.id=='a1' else None) for a in original.axes)
    real=Dataset(original.data[:1],axes,())
    calibrated=apply_operation(real,operation())
    expected=120+(SCRIPT_WIDTH/2-np.arange(8)*SCRIPT_WIDTH/8)/50.648
    for fmt,suffix in [('nmrpipe-real','.ft2'),('ucsf-real','.ucsf')]:
        path=tmp_path/('synthetic'+suffix)
        write_dataset(calibrated,path,format=fmt,allow_losses=('metadata','provenance','axis_annotations'))
        raw=path.read_bytes()
        if fmt=='nmrpipe-real':
            fields=struct.unpack('<512f',raw[:2048])
            width,obs,origin=fields[229],fields[218],fields[249]
            actual=(origin+(7-np.arange(8))*width/8)/obs
        else:
            obs,width,center=struct.unpack_from('>3f',raw,180+20)
            assert center==120
            actual=center+(width/2-np.arange(8)*width/8)/obs
        assert width==5555.55615234375
        np.testing.assert_allclose(actual,expected,rtol=0,atol=2e-5)
    assert calibrated.data.tobytes()==real.data.tobytes()


def test_width_of_current_cropped_grid_is_not_original_acquisition_width():
    ds=source(n=8)
    cropped=apply_operation(ds,{'op':'crop','axis':'a0','start':2,'stop':6})
    out=apply_operation(cropped,operation())
    assert out.axis('a0').size==4 and out.axis('a0').acquired_size==8
    assert out.axis('a0').step==-SCRIPT_WIDTH/4
    assert out.axis('a0').start==SCRIPT_WIDTH/2
    assert out.data.tobytes()==cropped.data.tobytes()
    assert out.provenance[-1]['reference_calibration']['prior_grid']['spectral_width_hz']==RAW_WIDTH/2


def test_default_preserves_previous_exact_coordinates_and_provenance_shape():
    ds=source()
    op=operation();del op['spectral_width_hz']
    out=apply_operation(ds,op)
    expected=replace(ds.axis('a0'),start=RAW_WIDTH/2,step=-RAW_WIDTH/8,
                     reference_ppm=120,reference_hz=0,observation_mhz=50.648)
    assert out.axis('a0')==expected and out.data.tobytes()==ds.data.tobytes()
    assert 'spectral_width_hz' not in out.provenance[-1]['operation']
    assert 'reference_calibration' not in out.provenance[-1]


@pytest.mark.parametrize('bad',[True,False,None,0,-1,float('nan'),float('inf'),-float('inf'),'5555.556'])
def test_invalid_width_fails_complete_preflight_before_numeric_work(monkeypatch,bad):
    op=operation();op['spectral_width_hz']=bad
    monkeypatch.setattr('nmrnext.operations._execute',lambda *a,**k:pytest.fail('numeric work before rejection'))
    for obj in (source(),DatasetHeader.from_dataset(source())):
        with pytest.raises(NMRNextError):plan_operations(obj,[op])
    with pytest.raises(NMRNextError):run_operations(source(),[{'op':'phase','axis':'a1','p0':20},op])


def test_repeated_width_idempotent_but_inverse_requires_original_grid():
    ds=source()
    calibrated=apply_operation(ds,operation())
    keep=operation();keep['frequency_scale']=1
    repeated=apply_operation(calibrated,keep)
    assert repeated.axes==calibrated.axes
    with pytest.raises(NMRNextError,match='canonical FFT'):apply_operation(repeated,{'op':'fft','axis':'a0','inverse':True})
    flip=operation();flip['frequency_scale']=-1
    width_only=apply_operation(repeated,flip)
    with pytest.raises(NMRNextError,match='canonical FFT'):apply_operation(width_only,{'op':'fft','axis':'a0','inverse':True})
    restore=operation();restore.update(spectral_width_hz=RAW_WIDTH,frequency_scale=1)
    original_grid=apply_operation(width_only,restore)
    inverse=apply_operation(original_grid,{'op':'fft','axis':'a0','inverse':True})
    assert inverse.axis('a0').domain=='time' and inverse.axis('a0').step==1/RAW_WIDTH


def test_signed_point_sums_unchanged_while_hz_measure_changes():
    ds=source(np.float64);op=operation();op['frequency_scale']=1
    out=apply_operation(ds,op)
    assert np.sum(out.data,dtype=np.float64)==np.sum(ds.data,dtype=np.float64)
    # Independent trapezoid weights: amplitudes unchanged, coordinate measure scales.
    line=ds.data[0,:,0];weights=line.copy();weights[[0,-1]]*=.5
    prior=weights.sum()*ds.axis('a0').step
    after=weights.sum()*out.axis('a0').step
    assert after==pytest.approx(prior*SCRIPT_WIDTH/RAW_WIDTH,rel=1e-15)
    assert after!=prior


def test_recipe_schema_declares_positive_optional_current_grid_width():
    schema=json.loads((Path(__file__).resolve().parents[2]/'schemas/recipe.schema.json').read_text())
    def objects(node):
        if isinstance(node,dict):
            yield node
            for v in node.values():yield from objects(v)
        elif isinstance(node,list):
            for v in node:yield from objects(v)
    variants=[n for n in objects(schema) if n.get('properties',{}).get('op',{}).get('const')=='reference']
    assert len(variants)==1
    ref=variants[0]
    assert ref['properties']['spectral_width_hz']['type']=='number'
    assert ref['properties']['spectral_width_hz']['exclusiveMinimum']==0
    assert 'spectral_width_hz' not in ref['required']
