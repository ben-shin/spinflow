"""Manufactured four-quadrature oracles, independent of the fit implementation."""
from dataclasses import replace
import json

import numpy as np
import pytest

from nmrnext.analysis import autophase, dataset_digest
from nmrnext.errors import NMRNextError
from nmrnext.model import Axis, Dataset
from nmrnext.operations import run_operations


def manufactured(*, reverse=False, bit_reverse=False, noise=.0002, mixed=False,
                 centers=((55, 67), (130, 166), (203, 251)), shape=(256, 320),
                 phases=(43., 62., -71., -94.), pivot=None):
    axes = [Axis('c', shape[0], domain='frequency', unit='Hz', start=100, step=-2 if reverse else 2),
            Axis('h', shape[1], domain='frequency', unit='Hz', start=500, step=1.5)]
    bits = ['h', 'c'] if bit_reverse else ['c', 'h']
    pivots = [(n-1)/2 for n in shape] if pivot is None else pivot
    data = np.zeros((4, *shape))
    for k, (cy, cx) in enumerate(centers):
        fy = 1/(1+1j*(-1 if reverse else 1)*(np.arange(shape[0])-cy)/(2.4+.2*k))
        fx = 1/(1+1j*(np.arange(shape[1])-cx)/(3.1+.1*k))
        fy *= np.exp(1j*np.deg2rad(phases[0]+phases[1]*(np.arange(shape[0])-pivots[0])/shape[0]))
        fx *= np.exp(1j*np.deg2rad(phases[2]+phases[3]*(np.arange(shape[1])-pivots[1])/shape[1]))
        amplitude = [1, .7, .9][k] * (-1 if mixed and k == 1 else 1)
        data[0] += amplitude*np.outer(fy.real, fx.real)
        data[bits.index('c')+1] += amplitude*np.outer(fy.imag, fx.real)
        data[bits.index('h')+1] += amplitude*np.outer(fy.real, fx.imag)
        data[3] += amplitude*np.outer(fy.imag, fx.imag)
    yy, xx = np.meshgrid(np.linspace(-1, 1, shape[0]), np.linspace(-1, 1, shape[1]), indexing='ij')
    for ch in range(4):
        data[ch] += .002*(ch+1)+.001*yy-.0015*xx
    data += np.random.default_rng(901).normal(0, noise, data.shape)
    return Dataset(data, axes, bits), pivots


@pytest.mark.parametrize('reverse,bit_reverse', [(False,False),(True,False),(False,True),(True,True)])
def test_full_tensor_two_axis_phase_recovery_and_replay(reverse, bit_reverse):
    d, pivots = manufactured(reverse=reverse, bit_reverse=bit_reverse)
    before = dataset_digest(d)
    corrected, report = autophase(d)
    for op, p0, p1, pivot in zip(report['operations'], [-43,71], [-62,94], pivots):
        assert op['p0'] == pytest.approx(p0, abs=.5)
        assert op['p1'] == pytest.approx(p1, abs=1.)
        assert op['pivot'] == pivot
    replay = run_operations(d, report['operations'])
    np.testing.assert_array_equal(replay.data, corrected.data)
    assert dataset_digest(d) == before == report['dataset_digest']
    assert dataset_digest(replay) == report['result_dataset_digest']
    assert replay.to_metadata() == corrected.to_metadata()
    assert len(corrected.provenance) == 2
    assert max(report['objective']['per_line_relative_rms']) < .005
    assert len(report['lines']) == 3
    assert 'simultaneous_180_degree_axis_ambiguity' in report['flags']
    json.dumps(report, allow_nan=False)


def test_gauge_and_noncentral_point_pivots():
    phases = (141, 55, 123, -83)
    pivots = [7., 21.]
    d, _ = manufactured(phases=phases, pivot=pivots)
    result, report = autophase(d, pivot={'c':7, 'h':21})
    assert report['operations'][0]['p0'] == pytest.approx(39, abs=.5)
    assert report['operations'][1]['p0'] == pytest.approx(57, abs=.5)
    exact = run_operations(d, [{'op':'phase', 'axis':a.id, 'p0':-p0, 'p1':-p1, 'pivot':p}
                              for a,p0,p1,p in zip(d.axes, phases[::2], phases[1::2], pivots)])
    np.testing.assert_allclose(result.data, exact.data, atol=.005)


def test_zero_order_and_single_peak_and_weak_span():
    d, _ = manufactured(centers=((128,160),), phases=(32,0,-53,0))
    with pytest.raises(NMRNextError, match='Need 2'):
        autophase(d)
    _, report = autophase(d, first_order=False)
    assert [op['p0'] for op in report['operations']] == pytest.approx([-32,53], abs=.1)
    assert all(op['p1'] == 0 for op in report['operations'])
    d, _ = manufactured(centers=((95,65),(110,166),(120,251)))
    with pytest.raises(NMRNextError, match='15% of EACH'):
        autophase(d)


def test_reject_mixed_sign_and_unsupported_quadrature_domain():
    d, _ = manufactured(mixed=True)
    with pytest.raises(NMRNextError):
        autophase(d)
    for invalid in [Dataset(d.data[:1], d.axes), Dataset(d.data[:2], d.axes, ['c']),
                    Dataset(d.data, [replace(d.axes[0], domain='time',unit='s'),d.axes[1]], d.complex_axes)]:
        with pytest.raises(NMRNextError):
            autophase(invalid)
    for kwargs in [{'axis':'c'}, {'pivot':2}, {'pivot':{'c':3}}, {'pivot':{'c':-1,'h':3}}]:
        with pytest.raises(NMRNextError):
            autophase(d, **kwargs)


def test_noise_only_and_nonseparable_cross_quadrature_are_not_certified():
    d, _ = manufactured()
    noise = Dataset(np.random.default_rng(1).normal(size=d.data.shape),d.axes,d.complex_axes)
    with pytest.raises(NMRNextError):
        autophase(noise)
    altered = d.data.copy()
    altered[3] *= -1
    with pytest.raises(NMRNextError):
        autophase(Dataset(altered,d.axes,d.complex_axes))


def test_sparse_peak_phase_branch_alias_is_rejected():
    # The 180-degree ambiguity at each cross-peak can otherwise produce a
    # convincing wrong slope even with a small full-component residual.
    d, _ = manufactured(centers=((55,67),(203,251)), phases=(20,170,-40,-160))
    with pytest.raises(NMRNextError, match='multiple 180-degree phase branches'):
        autophase(d)


def test_antiphase_doublet_is_not_a_positive_absorptive_cross_peak():
    d, _ = manufactured(centers=((80,98),(80,109)),mixed=True)
    with pytest.raises(NMRNextError):
        autophase(d,first_order=False)


def test_zero_filled_finite_fid_with_correlated_noise_recovers_both_axes():
    n = 128
    t = np.arange(n)
    data = np.zeros((4,n,n))
    for f0,f1,amp in [(-.28,-.24,1.),(.02,.03,.7),(.29,.28,.9)]:
        z0 = np.exp((2j*np.pi*f0-.08)*t)
        z1 = np.exp((2j*np.pi*f1-.09)*t)
        data[0] += amp*np.outer(z0.real,z1.real)
        data[1] += amp*np.outer(z0.imag,z1.real)
        data[2] += amp*np.outer(z0.real,z1.imag)
        data[3] += amp*np.outer(z0.imag,z1.imag)
    data += np.random.default_rng(14).normal(0,.0003,data.shape)
    raw = Dataset(data,[Axis('c',n,step=.001),Axis('h',n,step=.001)],['c','h'])
    recipe = []
    for a in raw.axes:
        recipe.extend([{'op':'window','axis':a.id,'kind':'exponential','lb_hz':0,'first_point_scale':.5},
                       {'op':'zero_fill','axis':a.id,'size':256},{'op':'fft','axis':a.id}])
    frequency = run_operations(raw,recipe)
    altered = run_operations(frequency,[{'op':'phase','axis':'c','p0':-31,'p1':75,'pivot':43},
                                        {'op':'phase','axis':'h','p0':64,'p1':-52,'pivot':111}])
    result, report = autophase(altered,pivot={'c':43,'h':111})
    for op,p0,p1 in zip(report['operations'],[31,-64],[-75,52]):
        assert op['p0'] == pytest.approx(p0,abs=.3)
        assert op['p1'] == pytest.approx(p1,abs=.6)
    assert max(report['objective']['per_line_relative_rms']) < .003
    np.testing.assert_allclose(result.data,frequency.data,atol=.15)
