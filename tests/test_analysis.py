"""Independent analytical oracles for analysis; no downloaded data required."""
from dataclasses import replace
import json
import math

import numpy as np
import pytest

from nmrnext.analysis import (autophase, dataset_digest, integrate_regions,
                              pick_peaks, propose_regions)
from nmrnext.errors import NMRNextError
from nmrnext.model import Axis, Dataset
from nmrnext.operations import run_operations


def spectrum(values, *, start=0., step=1., calibrated=True):
    values = np.asarray(values, dtype=float)
    axes = [Axis(f"f{i}", size, domain="frequency", unit="Hz", start=start, step=step,
                 observation_mhz=500. if calibrated else None, reference_ppm=4.7 if calibrated else None)
            for i, size in enumerate(values.shape)]
    return Dataset(values[None], axes)


def region(dataset, pairs=None, unit="points", identity="r1"):
    return {"id": identity, "bounds": {a.id: [0, a.size-1] for a in dataset.axes} if pairs is None else pairs, "unit": unit}


@pytest.mark.parametrize("descending", [False, True])
@pytest.mark.parametrize("p0,p1", [(43.,62.),(-90.,-100.),(160.,140.)])
def test_autophase_recovers_known_noisy_complex_lorentzian_ramp(descending, p0, p1):
    n = 2048
    x = np.linspace(100, -100, n) if descending else np.linspace(-100, 100, n)
    pivot = (n-1)/2
    t = (np.arange(n)-pivot)/n
    signal = sum(amplitude/(1+1j*(x-center)/width)
                 for center, width, amplitude in [(-51,1.4,1.),(-7,2.1,.65),(45,1.2,.8)])
    rng = np.random.default_rng(3)
    baseline = .04+.03*t+1j*(.02-.01*t)
    measured = signal*np.exp(1j*np.pi/180*(p0+p1*t))+baseline
    measured += rng.normal(0,.001,n)+1j*rng.normal(0,.001,n)
    d = Dataset(np.stack((measured.real, measured.imag)),
                [Axis("h",n,domain="frequency",unit="Hz",start=x[0],step=x[1]-x[0])], ["h"])
    result, report = autophase(d)
    op = report["operation"]
    assert abs(op["p0"]+p0) < .5
    assert abs(op["p1"]+p1) < 1.
    assert op["pivot"] == pivot
    expected = measured*np.exp(1j*np.pi/180*(op["p0"]+op["p1"]*t))
    np.testing.assert_allclose(result.data[0], expected.real, atol=1e-14)
    np.testing.assert_allclose(result.data[1], expected.imag, atol=1e-14)
    replay = run_operations(d, [op])
    assert replay.to_metadata() == result.to_metadata()
    assert report["result_dataset_digest"] == dataset_digest(replay)
    assert report["objective"]["normalized_rms"] < .002
    assert len(report["lines"]) == 3
    assert "inspect_phase" in report["flags"]
    json.dumps(report, allow_nan=False)


def test_single_line_only_zero_order_is_identifiable_and_pivot_is_points():
    x = np.arange(1024)
    z = 1/(1+1j*(x-500)/5)*np.exp(.7j)
    d = Dataset(np.stack((z.real,z.imag)), [Axis("h",1024,domain="frequency",unit="Hz")], ["h"])
    with pytest.raises(NMRNextError, match="Need 2"):
        autophase(d)
    _, report = autophase(d, first_order=False, pivot=7)
    assert report["operation"]["p0"] == pytest.approx(-.7*180/math.pi, abs=.01)
    assert report["operation"]["pivot"] == 7
    assert report["operation"]["p1"] == 0
    assert "first_order_not_estimated" in report["flags"]


def test_autophase_rejects_real_time_2d_zero_and_noise_only_profiles():
    with pytest.raises(NMRNextError):
        autophase(spectrum(np.ones(128)))
    with pytest.raises(NMRNextError):
        autophase(Dataset(np.zeros((2,128)),[Axis("h",128)],["h"]))
    with pytest.raises(NMRNextError):
        autophase(spectrum(np.ones((64,64))))
    for signal in (np.zeros((2,128)), np.random.default_rng(1).normal(size=(2,1024))):
        d = Dataset(signal,[Axis("h",signal.shape[1],domain="frequency",unit="Hz")],["h"])
        with pytest.raises(NMRNextError):
            autophase(d)


def test_peak_sign_plateau_boundary_tie_order_and_threshold_override():
    d = spectrum([3,0,4,4,0,-5,0,4,0,2], start=500, step=-2)
    report = pick_peaks(d, sign="both", threshold=2., baseline="none")
    peaks = report["peaks"]
    assert [p["points"]["f0"] for p in peaks] == [5,2,7,0,9]
    assert peaks[0]["sign"] == "negative"
    assert peaks[0]["intensity"] == -5
    assert "plateau" in peaks[1]["flags"]
    assert "spectrum_edge" in peaks[-1]["flags"]
    assert peaks[1]["coordinates"]["f0"] == {"Hz":496., "ppm":5.692}
    assert report["threshold"] == 2.
    assert pick_peaks(d, sign="negative", threshold=2, baseline="none")["peaks"] == [dict(peaks[0],id="peak-1")]
    assert pick_peaks(d, sign="both", threshold=2, baseline="none") == report
    json.dumps(report, allow_nan=False)


def test_peak_2d_signed_plateau_centroid_mask_and_no_false_ppm():
    y = np.zeros((12,15)); y[3:5,4:6] = 7; y[8,10] = -9; y[0,13] = 6
    d = spectrum(y, calibrated=False)
    mask = np.ones_like(y,dtype=bool); mask[8,10] = False; mask[1,13] = False
    report = pick_peaks(d, sign="both", threshold=1, baseline="none", mask=mask)
    assert [p["points"] for p in report["peaks"]] == [{"f0":3,"f1":4},{"f0":0,"f1":13}]
    assert set(report["peaks"][1]["flags"]) == {"spectrum_edge","mask_edge"}
    assert "ppm" not in report["peaks"][0]["coordinates"]["f0"]
    with pytest.raises(NMRNextError):
        pick_peaks(d,separation_unit="ppm")


def test_physical_peak_separation_is_absolute_for_descending_axes():
    d = spectrum([0,4,0,3,0,0,5,0],step=-2.)
    r = pick_peaks(d,threshold=1,baseline="none",min_separation=5,separation_unit="Hz")
    assert [p["points"]["f0"] for p in r["peaks"]] == [6,1]
    # Strict exclusion means a distance equal to the requested minimum survives.
    r = pick_peaks(d,threshold=1,baseline="none",min_separation=4,separation_unit="Hz")
    assert len(r["peaks"]) == 3


def test_noise_estimate_sparse_signals_and_user_baseline():
    rng = np.random.default_rng(42)
    y = rng.normal(10,.2,40000); y[500] += 8.; y[10000] -= 8.
    d = spectrum(y)
    report = pick_peaks(d,sign="both",snr=8,baseline="constant",baseline_value=10)
    assert report["noise"]["sigma"] == pytest.approx(.2, rel=.03)
    assert {p["points"]["f0"] for p in report["peaks"]} == {500,10000}
    assert report["threshold"] == 8*report["noise"]["sigma"]


def test_selected_channel_is_explicit_and_reported():
    a = Axis("h",8,domain="frequency",unit="Hz")
    data = np.zeros((2,8)); data[0,2] = 3; data[1,5] = 6
    d = Dataset(data,[a],["h"])
    result = pick_peaks(d,channel=1,threshold=2,baseline="none")
    assert result["peaks"][0]["points"] == {"h":5}
    assert result["parameters"]["channel"] == 1


@pytest.mark.parametrize("step", [.2,-.2])
def test_gaussian_area_analytic_nonunit_axis_signed_baseline(step):
    x = np.linspace(-7,7,701)
    # Construct spacing from x, independent of production integration machinery.
    if step < 0:
        x = x[::-1]
    amplitude, sigma = -3., 1.3
    y = amplitude*np.exp(-x*x/(2*sigma*sigma)) + 2.5
    d = spectrum(y,start=x[0],step=x[1]-x[0])
    bounds = {"f0":[-7.,7.]}
    report = integrate_regions(d,[region(d,bounds,"Hz")],baseline="constant",baseline_value=2.5)
    expected = amplitude*sigma*math.sqrt(2*math.pi)*math.erf(7/(sigma*math.sqrt(2)))
    got = report["regions"][0]
    assert got["integral"] == pytest.approx(expected,rel=1e-7)
    assert got["integral_unit"] == "intensity*Hz"
    assert "spectrum_edge" in got["flags"]
    json.dumps(report,allow_nan=False)


def test_lorentzian_area_converges_to_analytic_finite_bounds():
    errors = []
    amplitude,width = 2.,.7
    expected = 2*amplitude*width*math.atan(4/width)
    for n in [33,65,129,257]:
        x = np.linspace(-4,4,n)
        d = spectrum(amplitude/(1+(x/width)**2),start=-4,step=8/(n-1))
        value = integrate_regions(d,[region(d)])["regions"][0]["integral"]
        errors.append(abs(value-expected))
    assert all(a > b*3.8 for a,b in zip(errors,errors[1:]))
    assert errors[-1] < 1e-5


@pytest.mark.parametrize("reverse_x,reverse_y", [(False,False),(True,False),(False,True),(True,True)])
def test_2d_gaussian_volume_analytic_axes_and_affine_baseline(reverse_x,reverse_y):
    x,y = np.linspace(-5,5,151),np.linspace(-6,6,177)
    if reverse_x: x = x[::-1]
    if reverse_y: y = y[::-1]
    gx,gy = np.meshgrid(x,y,indexing="ij")
    z = -2*np.exp(-gx**2/(2*.7**2)-gy**2/(2*1.1**2)) + .8 + .03*gx-.02*gy
    axes = [Axis("h",len(x),domain="frequency",unit="Hz",start=x[0],step=x[1]-x[0]),
            Axis("n",len(y),domain="frequency",unit="Hz",start=y[0],step=y[1]-y[0])]
    d = Dataset(z[None],axes)
    got = integrate_regions(d,[region(d)],baseline="edge_linear")["regions"][0]
    expected = -2*2*math.pi*.7*1.1*math.erf(5/(.7*math.sqrt(2)))*math.erf(6/(1.1*math.sqrt(2)))
    assert got["integral"] == pytest.approx(expected,rel=4e-6)
    assert got["integral_unit"] == "intensity*Hz^2"
    assert "edge_baseline_may_remove_signal" in got["flags"]


def test_fractional_endpoint_integral_linear_function_and_ppm_bounds():
    d = spectrum(3+2*np.arange(10),start=100,step=-2)
    # Physical endpoints correspond to point .25 and 6.5. Integral of (3+2j)*2 dj.
    bounds = {"f0":[4.7+(100-.25*2)/500,4.7+(100-6.5*2)/500]}
    report = integrate_regions(d,[region(d,bounds,"ppm")])
    got = report["regions"][0]
    assert got["integral"] == pytest.approx(2*(3*(6.5-.25)+6.5**2-.25**2),abs=1e-10)
    assert got["point_bounds"]["f0"] == pytest.approx([.25,6.5])
    assert got["coordinate_bounds"]["f0"]["Hz"] == pytest.approx([99.5,87])


def test_overlap_rejected_allowed_explicit_and_touching_intervals_add():
    d = spectrum(np.ones((10,10)),step=2.)
    first = region(d,{"f0":[0,6],"f1":[0,6]})
    second = region(d,{"f0":[5,9],"f1":[5,9]},identity="r2")
    with pytest.raises(NMRNextError) as error:
        integrate_regions(d,[first,second])
    assert error.value.code == "OVERLAPPING_REGIONS"
    report = integrate_regions(d,[first,second],allow_overlap=True)
    assert report["regions"][0]["integral"] == 144
    assert report["regions"][0]["overlaps"] == ["r2"]
    assert "total" not in report
    line = spectrum(np.ones(10),step=-2.)
    rs = [region(line,{"f0":[0,4]}),region(line,{"f0":[4,9]},identity="r2")]
    assert sum(r["integral"] for r in integrate_regions(line,rs)["regions"]) == 18.


def test_region_proposals_are_editable_flag_overlap_edges_and_stale_data():
    d = spectrum([8,0,0,9,0,0,0,0,0,0])
    peaks = pick_peaks(d,threshold=5,baseline="none")
    proposal = propose_regions(d,peaks,half_width=2)
    assert "overlapping_proposal" in proposal["regions"][0]["flags"]
    assert "spectrum_edge" in proposal["regions"][1]["flags"]
    with pytest.raises(NMRNextError,match="overlap"):
        integrate_regions(d,proposal["regions"])
    integrate_regions(d,proposal["regions"],allow_overlap=True)
    with pytest.raises(NMRNextError) as error:
        propose_regions(spectrum(np.zeros(10)),peaks)
    assert error.value.code == "STALE_ANALYSIS"


def test_digest_tracks_samples_axes_channels_but_not_metadata():
    d = spectrum([0,1,0,0])
    same = Dataset(d.data,d.axes,metadata={"note":"same numerical snapshot"})
    assert dataset_digest(d) == dataset_digest(same)
    assert dataset_digest(d) != dataset_digest(spectrum([0,2,0,0]))
    shifted = Dataset(d.data,[replace(d.axes[0],start=1)])
    assert dataset_digest(d) != dataset_digest(shifted)


@pytest.mark.parametrize("kwargs", [{"sign":[]},{"threshold":float("nan")},{"channel":True},
                         {"channel":4},{"min_separation":0},{"min_separation":{"wrong":2}},
                         {"baseline":"linear"},{"max_peaks":0},{"snr":-1},
                         {"mask":[1,0,1,1]},{"mask":np.zeros(4,dtype=bool)},
                         {"mask":np.ma.array([True]*4,mask=[False,True,False,False])}])
def test_peak_invalid_inputs_raise_structured_errors(kwargs):
    with pytest.raises(NMRNextError):
        pick_peaks(spectrum([0,1,0,0]),**kwargs)


@pytest.mark.parametrize("bounds", [[0,0],[-1,5],[0,99],[float("nan"),3],[False,4],[0]])
def test_invalid_integration_bounds_raise_structured_errors(bounds):
    d = spectrum(np.ones(10))
    with pytest.raises(NMRNextError):
        integrate_regions(d,[region(d,{"f0":bounds})])


def test_nonfinite_input_and_nonspectral_profile_fail_closed():
    with pytest.raises(NMRNextError):
        spectrum([0,np.nan,0])
    with pytest.raises(NMRNextError):
        spectrum([0,np.inf,0])
    time = Dataset(np.zeros((1,8)),[Axis("h",8)])
    for func in [pick_peaks, lambda d:integrate_regions(d,[region(d)])]:
        with pytest.raises(NMRNextError):
            func(time)


def test_sparse_mask_noise_failure_and_max_peaks_flag():
    d = spectrum([0,4,0,3,0,2,0])
    with pytest.raises(NMRNextError):
        pick_peaks(d,mask=np.array([False,True,False,False,False,False,False]))
    report = pick_peaks(d,threshold=1,baseline="none",max_peaks=1)
    assert len(report["peaks"]) == 1
    assert "max_peaks_reached" in report["flags"]


def test_1d_noisy_integral_error_is_consistent_with_noise_scale():
    x = np.linspace(-6,6,2001)
    y = np.exp(-x*x/2) + np.random.default_rng(64).normal(0,.01,x.size)
    d = spectrum(y,start=x[0],step=x[1]-x[0])
    got = integrate_regions(d,[region(d)])["regions"][0]["integral"]
    expected = math.sqrt(2*math.pi)*math.erf(6/math.sqrt(2))
    # Independent iid propagation for a trapezoid: endpoint weights are 1/2.
    sigma_area = .01*abs(x[1]-x[0])*math.sqrt(x.size-1.5)
    assert abs(got-expected) < 4*sigma_area


def test_constant_plateau_has_no_peak_and_finite_coordinate_ranges_are_required():
    assert pick_peaks(spectrum(np.ones((8,9))),threshold=.5,baseline="none")["peaks"] == []
    bad = spectrum([0,2,0,0],start=1e308,step=1e308)
    with pytest.raises(NMRNextError):
        pick_peaks(bad)
    normal = spectrum([0,2,0,0])
    with pytest.raises(NMRNextError):
        integrate_regions(normal,[region(normal,{"f0":[3+1e-10,3+2e-10]})])


def test_numpy_scalar_parameters_still_produce_plain_serializable_reports():
    d = spectrum([0,2,0,0])
    report = pick_peaks(d,threshold=np.float64(1),min_separation=np.int64(2))
    json.dumps(report,allow_nan=False)
    assert isinstance(report["parameters"]["min_separation"],int)


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("factor", [1,2,4,8,16])
def test_zero_filled_noise_amplitude_sigma_and_false_picks(seed, factor):
    # FFT of complex iid Gaussian noise has known marginal real SD sqrt(N).
    # Zero filling changes adjacent correlation, not that amplitude variance.
    rng = np.random.default_rng(seed)
    fid = rng.normal(size=4096)+1j*rng.normal(size=4096)
    y = np.fft.fftshift(np.fft.fft(fid,n=4096*factor)).real
    report = pick_peaks(spectrum(y,step=1/factor))
    assert report["noise"]["sigma"] == pytest.approx(64.,rel=.08)
    assert report["noise"]["method"] == "amplitude_MAD_normal"
    assert len(report["peaks"]) <= 1
    assert "max_peaks_reached" not in report["flags"]


@pytest.mark.parametrize("seed", range(3))
@pytest.mark.parametrize("factor", [1,2,4,8])
def test_2d_apodized_correlated_noise_has_correct_amplitude_scale(seed, factor):
    rng = np.random.default_rng(seed)
    fid = rng.normal(size=(64,64))+1j*rng.normal(size=(64,64))
    window = np.exp(-np.arange(64)/32.)
    weight = window[:,None]*window[None,:]
    y = np.fft.fftshift(np.fft.fft2(fid*weight,s=(64*factor,64*factor))).real
    # Independent time-sample variance propagation, not a production estimator.
    true_sigma = math.sqrt(float(np.sum(weight**2)))
    report = pick_peaks(spectrum(y,step=1/factor))
    assert report["noise"]["sigma"] == pytest.approx(true_sigma,rel=.12)
    assert len(report["peaks"]) <= 1


@pytest.mark.parametrize("factor", [1,4,16])
def test_correlated_noise_signal_recovery_baseline_and_absolute_override(factor):
    rng = np.random.default_rng(32)
    n = 4096
    fid = rng.normal(size=n)+1j*rng.normal(size=n)
    window = np.exp(-np.arange(n)/(n/2))
    true_sigma = math.sqrt(float(np.sum(window**2)))
    y = np.fft.fftshift(np.fft.fft(fid*window,n=n*factor)).real
    x = np.arange(n*factor)/factor
    y += 20*true_sigma*np.exp(-((x-1234)/4)**2)
    y += 8*true_sigma  # constant baseline must not become noise amplitude
    report = pick_peaks(spectrum(y,step=1/factor))
    assert report["noise"]["sigma"] == pytest.approx(true_sigma,rel=.08)
    assert any(abs(p["points"]["f0"]/factor-1234) < 2 for p in report["peaks"])
    assert len(report["peaks"]) <= 2
    override = pick_peaks(spectrum(y),threshold=true_sigma)
    assert override["threshold"] == true_sigma
    assert len(override["peaks"]) > len(report["peaks"])


def test_noiseless_constant_and_varying_baseline_estimator_is_explicit():
    constant = pick_peaks(spectrum(np.full(128,3.)))
    assert constant["peaks"] == []
    assert constant["noise"]["sigma"] == 0
    assert "zero_noise_estimate" in constant["flags"]
    y = np.zeros(128); y[44] = 5
    noiseless = pick_peaks(spectrum(y))
    assert noiseless["peaks"][0]["points"]["f0"] == 44
    assert "zero_noise_estimate" in noiseless["flags"]
    drift = pick_peaks(spectrum(np.linspace(-5,5,2048)))
    assert drift["peaks"] == []
    assert "varying baselines may inflate" in drift["noise"]["assumption"]
