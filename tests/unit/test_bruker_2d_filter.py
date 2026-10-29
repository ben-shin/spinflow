"""Direct filter correction must preserve both independent quadrature bits."""
from dataclasses import replace

import numpy as np
import pytest

from nmrnext import Axis, Dataset, NMRNextError
from nmrnext.operations import run_operations
from nmrnext.planning import plan_operations


def acquisition(n=32, m=5, delay=7.25):
    t = np.arange(n)
    # Distinct amplitudes and unsigned modes in every indirect quadrature row.
    z = np.stack([np.stack([(q+1+1j*j)*np.exp(2j*np.pi*(q+j+1)*t/n)
                            for j in range(m)]) for q in (0, 1)])
    data = np.stack((z[0].real, z[1].real, z[0].imag, z[1].imag))
    axes = (Axis("a0",m,encoding="states",step=.01,dwell_seconds=.01),
            Axis("a1",n,step=.001,dwell_seconds=.001))
    metadata = {"sampling":"fully_sampled", "import":{"format":"bruker-raw-2d",
        "direct_axis":"a1", "indirect_axis":"a0", "acquisition_parameters":{
            "TD":2*n,"AQ_mod":3,"SW_h":1000},
        "indirect_acquisition_parameters":{"TD":2*m,"FnMODE":4,"SW_h":100},
        "digital_filter":{"state":"uncorrected_or_unknown","GRPDLY":delay}}}
    return Dataset(data,axes,("a0","a1"),metadata),z


@pytest.mark.parametrize("backend",["numpy","native"])
def test_filter_each_pair_matches_analytic_translation_without_flattening(backend):
    ds,z = acquisition()
    expected = []
    for q in (0,1):
        advanced = np.stack([(q+1+1j*j)*np.exp(2j*np.pi*(q+j+1)*(np.arange(32)+7.25)/32)
                             for j in range(5)])
        target = advanced[:,:23].copy()
        target[:,:3] += advanced[:,-1:-4:-1]
        expected.append(target)
    ops = [{"op":"digital_filter","axis":"a1"}]
    actual = run_operations(ds,ops,backend=backend)
    assert actual.data.shape == (4,5,23)
    np.testing.assert_allclose(actual.data[0]+1j*actual.data[2],expected[0],atol=2e-13)
    np.testing.assert_allclose(actual.data[1]+1j*actual.data[3],expected[1],atol=2e-13)
    assert actual.axes[0] == ds.axes[0]
    assert actual.axis("a1").acquired_size == 32
    assert plan_operations(ds,ops)["output_shape"] == [4,5,23]
    np.testing.assert_array_equal(ds.data[0]+1j*ds.data[2],z[0])
    with pytest.raises(NMRNextError,match="already"):
        run_operations(actual,ops)


def test_filter_refuses_wrong_axis_and_modified_indirect_grid():
    ds,_ = acquisition()
    with pytest.raises(NMRNextError):
        run_operations(ds,[{"op":"digital_filter","axis":"a0"}])
    changed = Dataset(ds.data,(replace(ds.axes[0],domain="frequency",unit="Hz"),ds.axes[1]),
                      ds.complex_axes,ds.metadata)
    with pytest.raises(NMRNextError):
        run_operations(changed,[{"op":"digital_filter","axis":"a1"}])


@pytest.mark.parametrize("backend",["numpy","native"])
def test_filter_follows_physical_axis_and_quadrature_bits(backend):
    ds,_ = acquisition()
    # Raw data represented with swapped physical axes AND reversed bit order.
    moved = Dataset(ds.data[[0,2,1,3]].transpose(0,2,1),ds.axes[::-1],
                    ds.complex_axes[::-1],ds.metadata)
    op = [{"op":"digital_filter","axis":"a1"}]
    canonical = run_operations(ds,op,backend=backend)
    actual = run_operations(moved,op,backend=backend)
    np.testing.assert_allclose(actual.data,canonical.data[[0,2,1,3]].transpose(0,2,1),atol=1e-14)


@pytest.mark.parametrize("value",[0,2,-1,1.5,True,None,"1"])
@pytest.mark.parametrize("native_roundtrip",[False,True])
def test_filter_refuses_preserved_invalid_digmod_before_execution(tmp_path,monkeypatch,value,native_roundtrip):
    from nmrnext.io import read_dataset, write_dataset
    import nmrnext.operations as operations
    ds,_ = acquisition()
    metadata = ds.metadata
    metadata["import"]["acquisition_parameters"]["DIGMOD"] = value
    source = Dataset(ds.data,ds.axes,ds.complex_axes,metadata)
    if native_roundtrip:
        path = tmp_path/"raw.nmrh5"
        write_dataset(source,path)
        source = read_dataset(path)
    before = source.data.copy()
    monkeypatch.setattr(operations,"_execute",lambda *args:pytest.fail("numeric correction must not run"))
    with pytest.raises(NMRNextError) as exc:
        operations.run_operations(source,[{"op":"digital_filter","axis":"a1"}])
    assert exc.value.code == "UNSUPPORTED_DIGITAL_FILTER"
    np.testing.assert_array_equal(source.data,before)
    assert source.metadata == metadata
