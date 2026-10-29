"""Independent constructed bytes exercise profile constraints and all channels."""
import hashlib
import struct

import numpy as np
import pytest

from nmrnext.errors import NMRNextError
from nmrnext.io import capabilities, inspect_dataset, read_dataset, write_dataset, export_preflight
from nmrnext.io.varian import inspect_varian, read_varian


def make_acquisition(path, dimensions=1, overrides=None):
    path.mkdir(exist_ok=True)
    rows = np.arange((1 if dimensions == 1 else 6)*16,dtype=np.float32).reshape(-1,16)-20
    params = {'np':16,'sw':8000,'sfrq':125.6,'tn':'C13','array':'','arraydim':len(rows)}
    if dimensions == 2:
        params.update(ni=3,sw1=2000,dfrq2=50.6,dn2='N15',seqfil='cpjdcptan2d',phase=[1,2],array='phase',d2=0)
    params.update(overrides or {})
    lines=[]
    for k,v in params.items():
        vals=v if isinstance(v,list) else [v]
        typ=2 if isinstance(vals[0],str) else 1
        lines.append(f'{k} 1 {typ} 100000 0 1 1 1 0 1 64')
        lines.append(str(len(vals))+' '+(' '.join(map(str,vals)) if typ==1 else '\n'.join('"'+x+'"' for x in vals)))
        lines.append('0')
    (path/'procpar').write_text('\n'.join(lines)+'\n')
    raw=struct.pack('>6i2hi',len(rows),1,16,4,64,92,0,201,1)
    for i,row in enumerate(rows):
        raw+=struct.pack('>4hi4f',0,201,i+1,0,8,1.25,-2.5,0,0)+row.astype('>f4').tobytes()
    (path/'fid').write_bytes(raw)
    return rows


@pytest.mark.parametrize('dimensions',[1,2])
def test_every_channel_headers_roundtrip_and_read_only(tmp_path,dimensions):
    rows=make_acquisition(tmp_path,dimensions)
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    ds=read_dataset(tmp_path)
    expected=np.stack((rows[0,::2],rows[0,1::2])) if dimensions==1 else np.stack((rows[::2,::2],rows[1::2,::2],rows[::2,1::2],rows[1::2,1::2]))
    np.testing.assert_array_equal(ds.data,expected)
    assert ds.complex_axes==tuple(f'a{i}' for i in range(dimensions))
    assert all(a.reference_ppm is None for a in ds.axes)
    assert ds.axes[-1].step==1/8000
    imported=ds.metadata['import']
    assert imported['source_sha256']==before
    assert len(imported['block_headers'])==len(rows)
    assert imported['block_headers'][0]['lpval']==1.25
    assert imported['block_headers'][0]['rpval']==-2.5
    assert imported['raw_procpar_text']==(tmp_path/'procpar').read_bytes().decode('utf-8')
    native=tmp_path/'roundtrip.nmrh5'
    write_dataset(ds,native)
    restored=read_dataset(native)
    assert restored.metadata==ds.metadata
    np.testing.assert_array_equal(restored.data,ds.data)
    report=export_preflight(ds,f'varian-raw-{dimensions}d')
    assert report['errors']==['read_only_profile']
    with pytest.raises(NMRNextError):
        write_dataset(ds,tmp_path/'no.fid',format=f'varian-raw-{dimensions}d')
    assert not (tmp_path/'no.fid').exists()
    for name,digest in before.items():
        assert hashlib.sha256((tmp_path/name).read_bytes()).hexdigest()==digest


@pytest.mark.parametrize('field,value',[(0,0),(1,2),(2,15),(3,8),(4,60),(5,96),(6,1),(7,197),(8,2)])
def test_invalid_binary_layout_refused(tmp_path,field,value):
    make_acquisition(tmp_path)
    raw=bytearray((tmp_path/'fid').read_bytes());values=list(struct.unpack('>6i2hi',raw[:32]));values[field]=value
    raw[:32]=struct.pack('>6i2hi',*values);(tmp_path/'fid').write_bytes(raw)
    with pytest.raises(NMRNextError):read_dataset(tmp_path,format='varian1d')


@pytest.mark.parametrize('field,value',[(0,1),(1,73),(2,2),(3,1),(4,0),(5,float('nan')),(7,1),(8,1)])
def test_ambiguous_block_scaling_phase_modes_drift_refused(tmp_path,field,value):
    make_acquisition(tmp_path)
    raw=bytearray((tmp_path/'fid').read_bytes());values=list(struct.unpack('>4hi4f',raw[32:60]));values[field]=value
    raw[32:60]=struct.pack('>4hi4f',*values);(tmp_path/'fid').write_bytes(raw)
    with pytest.raises(NMRNextError):read_dataset(tmp_path)


@pytest.mark.parametrize('overrides',[{'array':'temp'},{'ni2':1},{'nf':2},{'np':18},{'arraydim':2},{'sw':float('inf')},{'sw':0},{'nus':'y'},{'tn':'unknown'}])
def test_1d_conflicting_acquisition_metadata_refused(tmp_path,overrides):
    make_acquisition(tmp_path,overrides=overrides)
    with pytest.raises(NMRNextError):read_dataset(tmp_path)


@pytest.mark.parametrize('overrides',[{'phase':[2,1]},{'phase':[1]},{'array':'phase,temp'},{'seqfil':'ghsqc'},{'dn2':'H1'},{'ni':4},{'f1180':'y'},{'d2':-0.1}])
def test_2d_unknown_quadrature_mapping_refused(tmp_path,overrides):
    make_acquisition(tmp_path,2,overrides)
    with pytest.raises(NMRNextError):read_dataset(tmp_path)


@pytest.mark.parametrize('alter',['truncate','extra','nan','duplicate','count','quote','unknown_type','nus'])
def test_malformed_inputs_are_bounded(tmp_path,alter):
    make_acquisition(tmp_path)
    fid=tmp_path/'fid';proc=tmp_path/'procpar'
    if alter=='truncate':fid.write_bytes(fid.read_bytes()[:-1])
    elif alter=='extra':fid.write_bytes(fid.read_bytes()+b'\0')
    elif alter=='nan':
        b=bytearray(fid.read_bytes());b[60:64]=struct.pack('>f',float('nan'));fid.write_bytes(b)
    elif alter=='duplicate':proc.write_text(proc.read_text()*2)
    elif alter=='count':proc.write_text(proc.read_text().replace('1 16','999999999 16'))
    elif alter=='quote':proc.write_text(proc.read_text().replace('"C13"','"C13'))
    elif alter=='unknown_type':proc.write_text(proc.read_text().replace('np 1 1','np 1 3'))
    else:(tmp_path/'nuslist').write_text('0 2 4')
    with pytest.raises(NMRNextError):read_dataset(tmp_path)


def test_budget_before_numeric_reader(tmp_path,monkeypatch):
    make_acquisition(tmp_path,2)
    monkeypatch.setattr('nmrnext.io._nmrglue',lambda:pytest.fail('Codec must not load'))
    with pytest.raises(NMRNextError,match='budget'):read_dataset(tmp_path,max_bytes=1)


def test_changed_header_refused(tmp_path):
    make_acquisition(tmp_path)
    head=inspect_varian(tmp_path,1<<20,dimensions=1)
    fid=tmp_path/'fid';raw=bytearray(fid.read_bytes());raw[32:34]=struct.pack('>h',1);fid.write_bytes(raw)
    with pytest.raises(NMRNextError,match='changed'):read_varian(tmp_path,head)


def test_capabilities_are_exact():
    profiles=[p for p in capabilities() if p['format'].startswith('varian')]
    assert len(profiles)==2 and all(p['read'] and not p['write'] for p in profiles)
