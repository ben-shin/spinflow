"""Literal vendor row layouts and independent hypercomplex DFT oracles."""
import hashlib
import math
import struct

import numpy as np
import pytest

from nmrnext import NMRNextError
from nmrnext.io import read_dataset, inspect_dataset, export_preflight
from nmrnext.operations import run_operations
from nmrnext.planning import plan_operations


def write_acquisition(path, *, mode=5, endian=0, dtype=0, td=64, rows=6):
    path.mkdir(exist_ok=True)
    direct = {"TD":td,"AQ_mod":3,"AQSEQ":0,"PARMODE":1,"FnTYPE":0,"BYTORDA":endian,
        "DTYPA":dtype,"SW_h":8000,"SFO1":500,"NUC1":"<1H>","GRPDLY":4.25}
    indirect = {"TD":rows,"FnMODE":mode,"SW":20,"SW_h":2000,"SFO1":100,"NUC1":"<13C>"}
    for name, params in (("acqus",direct),("acqu2s",indirect)):
        (path/name).write_text("##TITLE= independent fixture\n"+"".join(f"##${k}= {v}\n" for k,v in params.items())+"##END=\n",encoding="ascii")
    # struct is independent of the production numpy decoder. Padding carries
    # nonzero sentinels so accidental use is visible in every output channel.
    values = [[10000*r+10*j+1 for j in range(td)] for r in range(rows)]
    width = 4 if dtype==0 else 8
    padded = math.ceil(td*width/1024)*1024//width
    codec = (">" if endian else "<")+("i" if dtype==0 else "d")
    payload = b"".join(struct.pack(codec,v) for row in values for v in row+[-987654]*(padded-td))
    (path/"ser").write_bytes(payload)
    return np.array(values,dtype=float)


@pytest.mark.parametrize("mode",[5,6])
@pytest.mark.parametrize("endian,dtype",[(0,0),(1,0),(0,2),(1,2)])
def test_literal_row_order_padding_dtype_and_bit_mapping(tmp_path,mode,endian,dtype):
    rows = write_acquisition(tmp_path,mode=mode,endian=endian,dtype=dtype)
    before = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    ds = read_dataset(tmp_path)
    assert ds.data.shape == (4,3,32)
    assert ds.complex_axes == ("a0","a1")
    expected = np.empty_like(ds.data)
    for t1 in range(3):
        for t2 in range(32):
            er,ei = rows[2*t1,2*t2:2*t2+2]
            ar,ai = rows[2*t1+1,2*t2:2*t2+2]
            expected[:,t1,t2] = ([er,ar,ei,ai] if mode==5 else [er-ar,-ei-ai,ei-ai,er+ar])
            if mode==5:
                expected[:,t1,t2] *= (-1)**t1
    np.testing.assert_array_equal(ds.data,expected)
    assert ds.axis("a0").step == 1/2000
    assert ds.axis("a1").step == 1/8000
    assert all(a.reference_ppm is None for a in ds.axes)
    assert ds.metadata["import"]["source_sha256"] == before
    assert ds.metadata["import"]["quadrature"]["source_FnMODE"] == mode
    assert export_preflight(ds,"bruker-raw-2d")["errors"] == ["read_only_profile"]
    assert before == {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}


def set_param(path,name,value):
    text = path.read_text()
    lines = [f"##${name}= {value}" if line.startswith(f"##${name}=") else line for line in text.splitlines()]
    path.write_text("\n".join(lines)+"\n")


def add_param(path,name,value):
    path.write_text(path.read_text().replace("##END=",f"##${name}= {value}\n##END="))


@pytest.mark.parametrize("value",[0,2,-1,1.5,"nan","<invalid>"])
@pytest.mark.parametrize("action",[inspect_dataset,read_dataset])
def test_explicit_incompatible_digmod_rejected_before_samples(tmp_path,monkeypatch,value,action):
    write_acquisition(tmp_path)
    add_param(tmp_path/"acqus","DIGMOD",value)
    before = {p.name:p.read_bytes() for p in tmp_path.iterdir()}
    import nmrnext.io.bruker2d as decoder
    monkeypatch.setattr(decoder,"read_bruker_2d",lambda *args:pytest.fail("numeric import must not run"))
    with pytest.raises(NMRNextError) as exc:
        action(tmp_path)
    assert exc.value.code in {"INVALID_HEADER","UNSUPPORTED_PROFILE"}
    assert before == {p.name:p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize("value",[1,0,-1,"nan","inf","1e308"])
def test_direct_sw_contradiction_or_invalid_value_refused(tmp_path,monkeypatch,value):
    write_acquisition(tmp_path)
    add_param(tmp_path/"acqus","SW",value)
    before = {p.name:p.read_bytes() for p in tmp_path.iterdir()}
    import nmrnext.io.bruker2d as decoder
    monkeypatch.setattr(decoder,"read_bruker_2d",lambda *args:pytest.fail("numeric import must not run"))
    with pytest.raises(NMRNextError) as exc:
        read_dataset(tmp_path)
    assert exc.value.code == "INVALID_HEADER"
    assert before == {p.name:p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize("present",[False,True])
def test_optional_digmod_and_direct_sw_missing_or_consistent(tmp_path,present):
    write_acquisition(tmp_path)
    if present:
        add_param(tmp_path/"acqus","DIGMOD",1)
        add_param(tmp_path/"acqus","SW",16.0001)
        # Calibration uses SFO1, even when base frequency BF1 differs.
        add_param(tmp_path/"acqus","BF1",400)
    ds = read_dataset(tmp_path)
    params = ds.metadata["import"]["acquisition_parameters"]
    assert ("DIGMOD" in params) is present
    if present:
        assert params["DIGMOD"] == 1
        assert params["SW"] == 16.0001
    assert run_operations(ds,[{"op":"digital_filter","axis":"a1"}]).data.shape == (4,3,26)


@pytest.mark.parametrize("file,key,value",[
    ("acqus","TD",63),("acqu2s","TD",5),("acqu2s","FnMODE",4),("acqu2s","FnMODE",0),
    ("acqus","AQ_mod",1),("acqus","AQSEQ",1),("acqus","PARMODE",2),("acqus","FnTYPE",2),
    ("acqus","DTYPA",1),("acqus","BYTORDA",2),("acqus","GRPDLY",0),
    ("acqus","GRPDLY",30),("acqus","SW_h",-1),("acqu2s","SW",21),
    ("acqus","TD",64.5),("acqu2s","FnMODE",5.5),("acqus","SFO1","nan"),
    ("acqus","SFO1","1"+"0"*400),("acqu2s","SW","1e300"),
])
def test_refuse_unsupported_or_inconsistent_metadata(tmp_path,file,key,value):
    write_acquisition(tmp_path)
    set_param(tmp_path/file,key,value)
    with pytest.raises(NMRNextError):
        inspect_dataset(tmp_path)


@pytest.mark.parametrize("extra",["nuslist","acqu3s","fid"])
def test_refuse_nus_and_ambiguous_acquisitions(tmp_path,extra):
    write_acquisition(tmp_path)
    (tmp_path/extra).write_text("0\n")
    with pytest.raises(NMRNextError):
        read_dataset(tmp_path)


@pytest.mark.parametrize("change",["truncated","extra","duplicate","missing","budget"])
def test_strict_lengths_header_completeness_and_budget(tmp_path,change):
    write_acquisition(tmp_path)
    if change in ("truncated","extra"):
        raw = (tmp_path/"ser").read_bytes()
        (tmp_path/"ser").write_bytes(raw[:-1] if change=="truncated" else raw+b"x")
    if change=="duplicate":
        text = (tmp_path/"acqus").read_text().replace("##END=","##$TD= 64\n##END=")
        (tmp_path/"acqus").write_text(text)
    if change=="missing":
        text = (tmp_path/"acqus").read_text().replace("##$FnTYPE= 0\n","")
        (tmp_path/"acqus").write_text(text)
    with pytest.raises(NMRNextError):
        read_dataset(tmp_path,max_bytes=1 if change=="budget" else 1<<20)


def _direct_dft(vector,inverse=False):
    """O(N^2) textbook sums; no FFT and no production quadrature utilities."""
    n = len(vector)
    return np.array([sum(vector[t]*np.exp((1 if inverse else -1)*2j*np.pi*k*t/n)
                         for t in range(n))/(n if inverse else 1) for k in range(n)])


@pytest.mark.parametrize("mode",[5,6])
@pytest.mark.parametrize("backend",["numpy","native"])
def test_raw_to_two_axis_transform_against_independent_sum_oracle(tmp_path,mode,backend):
    rows = write_acquisition(tmp_path,mode=mode,td=32,rows=6)
    # Recover every literal C/S row independently, then apply the specified
    # fractional filter with slow Fourier sums, tail fold, and trim.
    direct = []
    for t1 in range(3):
        e = np.array([complex(*rows[2*t1,j:j+2]) for j in range(0,32,2)])
        a = np.array([complex(*rows[2*t1+1,j:j+2]) for j in range(0,32,2)])
        pair = (e*(-1)**t1,a*(-1)**t1) if mode==5 else (e-a,1j*(e+a))
        corrected = [_direct_dft(_direct_dft(v)*np.exp(2j*np.pi*4.25*np.arange(16)/16),True)[:10] for v in pair]
        direct.append([np.roll(_direct_dft(v),5) for v in corrected])
    direct = np.array(direct)
    expected = np.empty((4,3,10))
    for t2 in range(10):
        # F1 transforms both independent direct real and imaginary traces.
        rr_ir = np.roll(_direct_dft(direct[:,0,t2].real+1j*direct[:,1,t2].real),1)
        ri_ii = np.roll(_direct_dft(direct[:,0,t2].imag+1j*direct[:,1,t2].imag),1)
        expected[:,:,t2] = [rr_ir.real,rr_ir.imag,ri_ii.real,ri_ii.imag]
    ds = read_dataset(tmp_path)
    ops = [{"op":"digital_filter","axis":"a1"},{"op":"fft","axis":"a1"},{"op":"fft","axis":"a0"}]
    result = run_operations(ds,ops,backend=backend)
    np.testing.assert_allclose(result.data,expected,rtol=3e-12,atol=2e-8)
    assert plan_operations(ds,ops)["output_shape"] == list(result.data.shape)
    assert result.axis("a0").acquired_size == 3
    assert result.axis("a1").acquired_size == 16
    assert result.axis("a0").start == pytest.approx(-2000/3)
    assert result.axis("a1").start == -4000
    assert result.metadata["import"]["digital_filter"]["state"] == "corrected"
