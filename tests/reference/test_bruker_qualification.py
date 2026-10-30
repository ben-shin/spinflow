"""Synthetic decoder/oracle controls; no acquired output is opened."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

spec=importlib.util.spec_from_file_location("qualification",Path(__file__).resolve().parents[2]/"tools/validate_bruker_qualification.py")
q=importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)


def header(path, **values):
    path.write_text("\n".join(f"##${key}= {value}" for key,value in values.items()))


def test_integer_decoder_padding_and_echo_antiecho_signs(tmp_path):
    header(tmp_path/"acqus",DTYPA=0,BYTORDA=0,AQ_mod=3,TD=4,AQSEQ=0,PARMODE=1,FnTYPE=0)
    header(tmp_path/"acqu2s",TD=2,FnMODE=6)
    rows=np.full((2,256),123456,dtype="<i4")
    rows[0,:4]=[1,2,3,4]
    rows[1,:4]=[5,6,7,8]
    (tmp_path/"ser").write_bytes(rows.tobytes())
    actual=q.raw_decode(tmp_path,2)
    np.testing.assert_array_equal(actual,[[[-4,-4]],[[-8,-12]],[[-4,-4]],[[6,10]]])
    assert actual.dtype==np.float64
    (tmp_path/"fid").write_bytes(rows[0].tobytes())
    np.testing.assert_array_equal(q.raw_decode(tmp_path,1),[[1,3],[2,4]])
    (tmp_path/"ser").write_bytes(rows.tobytes()[:-1])
    with pytest.raises(ValueError):
        q.raw_decode(tmp_path,2)


def test_cell_oracle_signed_bilinear_exact_integral():
    y,x=np.mgrid[:9,:9]
    array=2+y-3*x+2*y*x
    p=[{"SW_p":18,"SI":9},{"SW_p":27,"SI":9}]
    # Mean of bilinear function on [1,5]x[2,7] equals center value 18.5.
    assert q.archive_oracle(array,[[1,5],[2,7]],p)==18.5*4*5*6
    assert q.archive_oracle(-array,[[1,5],[2,7]],p)==-18.5*4*5*6


def test_reference_only_selector_preserves_signs_and_disjoint_boxes():
    a=np.zeros((48,48))
    for point,value in [((10,10),10),((10,30),8),((30,10),-9),((30,30),-7),((0,0),100)]:
        a[point]=value
    result=q.select_reference_regions(a,[np.arange(48)[::-1],np.arange(48)[::-1]])
    assert [r["reference_center_value"] for r in result]==[10,8,-9,-7]
    assert [r["center_points"] for r in result]==[[10,10],[10,30],[30,10],[30,30]]
    for i,a in enumerate(result):
        for b in result[i+1:]:
            assert any(x[1]<y[0] or y[1]<x[0] for x,y in zip(a["bounds_points"],b["bounds_points"]))


def test_full_row_archive_decoder_and_integer_scale(tmp_path):
    common=dict(BYTORDP=0,DTYPP=0,NC_proc=-6,SF=100,OFFSET=10,SW_p=400)
    header(tmp_path/"proc2s",SI=4,XDIM=2,**common)
    header(tmp_path/"procs",SI=3,XDIM=3,**common)
    for index,name in enumerate(("2rr","2ir","2ri","2ii")):
        (tmp_path/name).write_bytes((np.arange(12,dtype="<i4")+index*64).tobytes())
    data,params,ppm=q.archive_decode(tmp_path)
    assert data.shape==(4,4,3)
    np.testing.assert_array_equal(data[2],np.arange(12).reshape(4,3)/64+2)
    np.testing.assert_array_equal(ppm[0],[10,9,8,7])
    header(tmp_path/"procs",SI=3,XDIM=1,**common)
    with pytest.raises(ValueError,match="tiling"):
        q.archive_decode(tmp_path)


def test_output_collision_and_input_identity(tmp_path):
    path=tmp_path/"source"
    path.write_bytes(b"pinned")
    manifest={"files":[{"path":"source","bytes":6,"sha256":q.digest(path)}]}
    q.verify_inputs(tmp_path,manifest)
    path.write_bytes(b"edited")
    with pytest.raises(ValueError,match="identity"):
        q.verify_inputs(tmp_path,manifest)
    with pytest.raises(FileExistsError):
        q.write_new(path,{})
