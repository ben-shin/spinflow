"""Instrument actual I/O boundaries to prove no sample reads while planning."""
import builtins
import importlib.util
import io
import json
from pathlib import Path
import struct

import h5py
import nmrglue as ng
import numpy as np
import pytest

from nmrnext.errors import NMRNextError
from nmrnext.io import describe_dataset, read_dataset, write_dataset
from nmrnext.model import Axis, Dataset, DatasetHeader
from nmrnext.planning import plan_operations


def _module(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def _make(path,profile):
    if profile.startswith('varian'):
        _module('test_varian').make_acquisition(path,1 if profile.endswith('1d') else 2)
    elif profile=='bruker-raw-2d':
        _module('test_bruker_2d').write_acquisition(path)
    elif profile=='bruker-raw-1d':
        path.mkdir()
        (path/'acqus').write_text('##TITLE= header fixture\n##$TD= 64\n##$AQ_mod= 3\n##$BYTORDA= 0\n##$DTYPA= 0\n##$SW_h= 8000\n##$SFO1= 500\n##$NUC1= <1H>\n##$GRPDLY= 4.25\n##END=\n')
        (path/'fid').write_bytes(struct.pack('<256i',*range(256)))
    else:
        ds=_module('test_codecs').spectrum(shape=(8,) if profile=='nmrpipe-1d' else (4,8))
        write_dataset(ds,path,format='nmrpipe-real' if profile=='nmrpipe-1d' else profile)


PROFILES=['nmrnext-hdf5','nmrpipe-real','nmrpipe-1d','ucsf-real','bruker-raw-1d','bruker-raw-2d','varian-raw-1d','varian-raw-2d']


def _ops(ds):
    ops=[]
    if ds.metadata.get('import',{}).get('digital_filter'):
        ops.append({'op':'digital_filter','axis':ds.axes[-1].id})
    for axis in reversed(ds.axes):
        if axis.domain=='time':
            ops.extend([{'op':'zero_fill','axis':axis.id,'size':max(64,axis.size)}, {'op':'fft','axis':axis.id},
                        {'op':'reference','axis':axis.id,'reference_ppm':4.7,'reference_hz':0,'observation_mhz':500,'frequency_scale':-1}])
    ops.append({'op':'reverse','axis':ds.axes[-1].id})
    if len(ds.axes)==2:ops.append({'op':'transpose','order':[a.id for a in reversed(ds.axes)]})
    return ops


@pytest.mark.parametrize('profile',PROFILES)
def test_all_binary_profiles_plan_without_samples_and_match_numeric(tmp_path,monkeypatch,profile):
    path=tmp_path/'input'
    _make(path,profile)
    dataset=read_dataset(path)
    operations=_ops(dataset)
    expected=plan_operations(dataset,operations)
    def forbidden(*a,**k):pytest.fail('Payload decoder/hash/selection used during description')
    monkeypatch.setattr(h5py.Dataset,'__getitem__',forbidden)
    monkeypatch.setattr(h5py.Dataset,'read_direct',forbidden)
    monkeypatch.setattr(ng.pipe,'read',forbidden)
    monkeypatch.setattr(ng.sparky,'read',forbidden)
    monkeypatch.setattr(ng.bruker,'read_binary',forbidden)
    monkeypatch.setattr(ng.varian,'get_nblocks',forbidden)
    monkeypatch.setattr('nmrnext.io._hash_file',forbidden)
    original_fromfile=np.fromfile
    def bounded_fromfile(file,dtype=float,count=-1,*a,**k):
        assert profile.startswith('nmrpipe') and count==512
        return original_fromfile(file,dtype,count,*a,**k)
    monkeypatch.setattr(np,'fromfile',bounded_fromfile)
    # The wrapper rejects any actual request that intersects binary samples.
    original_open=builtins.open;original_io_open=io.open
    class Checked:
        def __init__(self,stream,filename):self.stream,self.filename=stream,Path(filename)
        def __enter__(self):return self
        def __exit__(self,*args):return self.stream.__exit__(*args)
        def __getattr__(self,name):return getattr(self.stream,name)
        def read(self,count=-1):
            start=self.stream.tell();stop=start+count
            assert count>=0,'Unbounded binary read'
            if self.filename.name in {'fid','ser'}:
                assert profile.startswith('varian'),'Bruker sample file must never open/read'
                allowed=[(0,32)]+[(32+i*92,60+i*92) for i in range(1 if profile.endswith('1d') else 6)]
                assert any(start>=lo and stop<=hi for lo,hi in allowed)
            elif profile=='ucsf-real':assert stop<=436
            elif profile.startswith('nmrpipe'):assert stop<=2048
            return self.stream.read(count)
    def wrap(original):
        def open_checked(filename,*a,**k):
            stream=original(filename,*a,**k)
            if isinstance(filename,(str,Path)) and (Path(filename)==path or Path(filename).name in {'fid','ser'}):
                return Checked(stream,filename)
            return stream
        return open_checked
    monkeypatch.setattr(builtins,'open',wrap(original_open));monkeypatch.setattr(io,'open',wrap(original_io_open))
    header=describe_dataset(path)
    got=plan_operations(header,operations)
    assert header.axes==dataset.axes and header.complex_axes==dataset.complex_axes
    assert header.dtype==dataset.data.dtype and header.nbytes==dataset.data.nbytes
    assert got['steps']==expected['steps']
    assert got['estimated_working_bytes']==expected['estimated_working_bytes']
    assert got['output_quadrature_index_orientation']==expected['output_quadrature_index_orientation']
    assert header.source_identity['payload_validated'] is False
    assert header.source_identity['scope']=='header_and_stat_only'


def test_large_unallocated_hdf5_layout_low_budget_never_selects_samples(tmp_path,monkeypatch):
    path=tmp_path/'large.nmrh5'
    axis=Axis('x',2**38)
    with h5py.File(path,'w') as f:
        f.attrs['schema_version']='1'
        f.attrs['metadata_json']=json.dumps({'schema_version':'1','axes':[axis.to_dict()],
            'complex_axes':['x'],'metadata':{},'provenance':[]})
        data=f.create_dataset('data',shape=(2,2**38),dtype='float64',chunks=(1,1024))
        assert data.id.get_storage_size()==0
    monkeypatch.setattr(h5py.Dataset,'__getitem__',lambda *a:pytest.fail('Sample selection'))
    monkeypatch.setattr(h5py.Dataset,'read_direct',lambda *a:pytest.fail('Sample read'))
    header=describe_dataset(path)
    assert header.nbytes==2**42 and path.stat().st_size<1<<20
    with pytest.raises(NMRNextError,match='budget'):plan_operations(header,[],memory_bytes=256*1024**2)


def test_jcamp_plan_refuses_before_reading_table(tmp_path,monkeypatch):
    path=tmp_path/'spectrum.dx';path.write_text(_module('test_jcamp_profile').TEXT)
    monkeypatch.setattr('nmrnext.io.jcamp.inspect_jcamp',lambda *a:pytest.fail('Full JCAMP inspector'))
    original=builtins.open
    class Prefix:
        def __init__(self,stream):self.stream=stream
        def __enter__(self):return self
        def __exit__(self,*a):return self.stream.__exit__(*a)
        def read(self,n):assert n<=12;return self.stream.read(n)
    monkeypatch.setattr(builtins,'open',lambda *a,**k:Prefix(original(*a,**k)))
    with pytest.raises(NMRNextError,match='convert') as exc:describe_dataset(path)
    assert exc.value.code=='HEADER_PLANNING_UNSUPPORTED'


def test_expected_header_detects_changed_metadata_before_numeric_read(tmp_path,monkeypatch):
    path=tmp_path/'input';_make(path,'nmrnext-hdf5');header=describe_dataset(path)
    with h5py.File(path,'r+') as f:
        meta=json.loads(f.attrs['metadata_json']);meta['axes'][0]['start']+=1
        f.attrs['metadata_json']=json.dumps(meta)
    monkeypatch.setattr('nmrnext.io._read_dataset',lambda *a,**k:pytest.fail('Changed source ingested'))
    with pytest.raises(NMRNextError) as exc:read_dataset(path,expected_header=header)
    assert exc.value.code=='INPUT_CHANGED'


def test_expected_header_revalidates_payload_and_catches_midread_change(tmp_path,monkeypatch):
    import nmrnext.io as codecs
    path=tmp_path/'input';_make(path,'nmrnext-hdf5');header=describe_dataset(path)
    read_dataset(path,expected_header=header)
    original=codecs._read_dataset
    def changed(*a,**k):
        ds=original(*a,**k)
        with h5py.File(path,'r+') as f:f.attrs['extra']='changed'
        return ds
    monkeypatch.setattr(codecs,'_read_dataset',changed)
    with pytest.raises(NMRNextError) as exc:read_dataset(path,expected_header=header)
    assert exc.value.code=='INPUT_CHANGED'


def test_header_does_not_claim_payload_finiteness(tmp_path):
    path=tmp_path/'input';_make(path,'nmrnext-hdf5')
    with h5py.File(path,'r+') as f:f['data'][0,0,0]=float('nan')
    header=describe_dataset(path)
    assert header.source_identity['payload_validated'] is False
    with pytest.raises(NMRNextError) as exc:read_dataset(path,expected_header=header)
    assert exc.value.code=='NONFINITE_DATA'


def test_forged_descriptor_dimensions_cannot_reuse_real_identity(tmp_path,monkeypatch):
    path=tmp_path/'input';_make(path,'nmrnext-hdf5');real=describe_dataset(path)
    forged=DatasetHeader((Axis('fake',1),),(),real.dtype,real.metadata,real.provenance,real.source_identity)
    monkeypatch.setattr('nmrnext.io._read_dataset',lambda *a,**k:pytest.fail('Forged small descriptor ingested'))
    with pytest.raises(NMRNextError) as exc:read_dataset(path,expected_header=forged)
    assert exc.value.code=='INPUT_CHANGED'


def test_malformed_native_header_refused_without_sample_read(tmp_path,monkeypatch):
    path=tmp_path/'input';_make(path,'nmrnext-hdf5')
    with h5py.File(path,'r+') as f:
        meta=json.loads(f.attrs['metadata_json']);meta['axes'][0]['size']+=1
        f.attrs['metadata_json']=json.dumps(meta)
    monkeypatch.setattr(h5py.Dataset,'__getitem__',lambda *a:pytest.fail('Sample selection'))
    with pytest.raises(NMRNextError) as exc:describe_dataset(path)
    assert exc.value.code=='INVALID_SHAPE'


def test_varian_header_budget_stops_before_block_scan(tmp_path,monkeypatch):
    import os
    from nmrnext.io.varian import inspect_varian
    path=tmp_path/'acq';_make(path,'varian-raw-1d')
    fid=path/'fid';raw=bytearray(fid.read_bytes());raw[:4]=struct.pack('>i',1_000_000);fid.write_bytes(raw)
    original_stat=Path.stat
    def stat(target,*a,**k):
        value=original_stat(target,*a,**k)
        if target==fid:
            fields=list(value);fields[6]=32+1_000_000*92
            return os.stat_result(fields)
        return value
    monkeypatch.setattr(Path,'stat',stat)
    with pytest.raises(NMRNextError) as exc:inspect_varian(path,(1<<63)-1,dimensions=1)
    assert exc.value.code=='HEADER_BUDGET_EXCEEDED'
