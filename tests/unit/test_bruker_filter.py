"""Analytical oracles for the explicit Bruker fractional-delay operation."""
from dataclasses import replace

import numpy as np
import pytest

from nmrnext import Axis, Dataset, NMRNextError
from nmrnext.operations import run_operations
from nmrnext.planning import plan_operations


OP = {"op": "digital_filter", "axis": "a0"}


def raw(z, delay=4.25, **changes):
    z = np.asarray(z)
    metadata = {"import": {"format": "bruker-raw-1d", "acquisition_parameters":
        {"AQ_mod": 3, "TD": 2*z.size, "SW_h": 1000}, "digital_filter":
        {"state": "uncorrected_or_unknown", "GRPDLY": delay, "DECIM": 3080, "DSPFVS": 20}}}
    metadata["import"]["digital_filter"].update(changes)
    return Dataset(np.stack((z.real, z.imag)).astype(float),
                   (Axis("a0", z.size, step=.001, dwell_seconds=.001),), ("a0",), metadata)


@pytest.mark.parametrize("n,delay", [(31, 4.25), (32, 4.0), (32, 7.75)])
def test_fractional_advance_matches_continuous_fourier_modes_and_tail_policy(n, delay):
    # Each unsigned DFT mode is translated analytically, without an FFT oracle.
    k = np.array([0, 2, n-3]); amplitude = np.array([.2, 2+1j, -.7j])
    t = np.arange(n)
    z = sum(c*np.exp(2j*np.pi*q*t/n) for c,q in zip(amplitude,k))
    advanced = sum(c*np.exp(2j*np.pi*q*(t+delay)/n) for c,q in zip(amplitude,k))
    trim = int(delay)+2
    fold = max(trim-6,0)
    expected = advanced[:n-trim].copy()
    for j in range(fold):
        expected[j] += advanced[n-1-j]
    source = raw(z, delay)
    result = run_operations(source, [OP])
    np.testing.assert_allclose(result.data[0]+1j*result.data[1], expected, atol=2e-12)
    np.testing.assert_array_equal(source.data, np.stack((z.real,z.imag)))
    assert source.metadata["import"]["digital_filter"]["state"] == "uncorrected_or_unknown"
    assert result.axis("a0").start == 0
    assert result.axis("a0").step == .001
    assert result.axis("a0").acquired_size == n
    correction = result.metadata["import"]["digital_filter"]["correction"]
    assert correction["trim_complex_points"] == trim
    assert correction["fold_complex_points"] == fold
    assert correction["first_point_scale"] == 1


def test_complete_recipe_planner_advances_filter_state_and_rejects_second_correction():
    source = raw(np.arange(32)+1j, 4.25)
    ops = [OP, {"op":"zero_fill","axis":"a0","size":64}, {"op":"fft","axis":"a0"}]
    plan = plan_operations(source, ops)
    assert plan["steps"][0]["output_shape"] == [2,26]
    assert plan["output_shape"] == [2,64]
    result = run_operations(source, ops)
    assert result.axis("a0").domain == "frequency"
    assert result.metadata["import"]["digital_filter"]["GRPDLY"] == 4.25
    with pytest.raises(NMRNextError, match="already"):
        plan_operations(source, [OP, OP])
    with pytest.raises(NMRNextError) as exc:
        plan_operations(source, ops, memory_bytes=1)
    assert exc.value.code == "MEMORY_BUDGET_EXCEEDED"


@pytest.mark.parametrize("delay", [None, 0, -1, True, "4", [], 1e100])
def test_unknown_or_invalid_group_delay_refused(delay):
    with pytest.raises(NMRNextError):
        run_operations(raw(np.ones(32)+1j, delay), [OP])


def test_correction_refuses_delay_override_unknown_profile_and_nonraw_order():
    source = raw(np.ones(32)+1j)
    for ops in ([dict(OP, GRPDLY=2)], [{"op":"window","axis":"a0"}, OP],
                [{"op":"crop","axis":"a0","start":0,"stop":20},OP]):
        with pytest.raises(NMRNextError):
            plan_operations(source, ops)
    with pytest.raises(NMRNextError):
        run_operations(Dataset(source.data, source.axes, source.complex_axes), [OP])
    with pytest.raises(NMRNextError):
        run_operations(Dataset(source.data, (replace(source.axes[0],start=.1),),
                               source.complex_axes,source.metadata),[OP])


def test_nmrglue_differential_corroboration_is_not_the_analytic_oracle():
    import nmrglue as ng
    rng = np.random.default_rng(27)
    z = rng.normal(size=301)+1j*rng.normal(size=301)
    actual = run_operations(raw(z, 67.985595703125), [OP])
    baseline = ng.bruker.rm_dig_filter(z,3080,20,67.985595703125,truncate_grpdly=False)
    np.testing.assert_allclose(actual.data[0]+1j*actual.data[1],baseline,atol=1e-12)


def test_explicit_reference_preserves_samples_and_nonzero_anchor_through_crop_reverse():
    source = raw(np.ones(32)+1j)
    spectrum = run_operations(source,[OP,{"op":"fft","axis":"a0"}])
    op = {"op":"reference","axis":"a0","reference_ppm":3.,"reference_hz":100.,"observation_mhz":500.}
    calibrated = run_operations(spectrum,[op])
    np.testing.assert_array_equal(spectrum.data, calibrated.data)
    np.testing.assert_allclose(calibrated.coordinates("a0","ppm"),3+(calibrated.coordinates("a0")-100)/500)
    reversed_crop = run_operations(calibrated,[{"op":"reverse","axis":"a0"},{"op":"crop","axis":"a0","start":2,"stop":8}])
    # Equivalent affine coordinates can differ by one binary64 rounding unit.
    np.testing.assert_allclose(reversed_crop.coordinates("a0","ppm"), calibrated.coordinates("a0","ppm")[::-1][2:8],rtol=0,atol=1e-14)
    with pytest.raises(NMRNextError):
        run_operations(source,[op])
    for bad in (0,-1,True,None,"500"):
        with pytest.raises(NMRNextError):
            run_operations(spectrum,[dict(op,observation_mhz=bad)])


def test_valid_spectral_width_survives_reciprocal_rounding():
    source = raw(np.ones(32)+1j)
    sw = 1.5945945945945947
    metadata = source.metadata
    metadata["import"]["acquisition_parameters"]["SW_h"] = sw
    source = Dataset(source.data,(replace(source.axes[0],step=1/sw,dwell_seconds=1/sw),),source.complex_axes,metadata)
    assert run_operations(source,[OP]).data.shape == (2,26)
