# Public Bruker 2D and bounded automatic phase

The GUI budget control and explicit phase-budget behavior below describe the 0.3.0b1 experimental beta. The previous v0.2.0 prerelease has different budget behavior; use the [beta installation instructions](../README.md#install-on-linux) for this workflow.

This experimental workflow imports fully sampled `ser`/`acqus`/`acqu2s` acquisitions with `FnMODE=5` States-TPPI or `FnMODE=6` Echo-AntiEcho, `AQ_mod=3`, `AQSEQ=0`, `PARMODE=1`, `FnTYPE=0` and positive recorded `GRPDLY`. It preserves all four quadratures, acquisition texts, checksums and explicit axis/bit mapping. Optional DIGMOD must be integer 1 when present; omission is allowed, but explicit analog/invalid declarations are refused. Optional direct SW must agree with SW_h/SFO1 within relative 2e-5. Other quadrature modes, NUS, higher dimensions, ambiguous directories, malformed headers and inconsistent trace lengths are refused. No new runtime dependencies are added.

The two acquired examples are HSQC and TOCSY of 1,3-diaminopropane, [BMRB bmse000001](https://doi.org/10.13018/BMSE000001), by Francisca Jofre, Mark E. Anderson and John L. Markley. The [manifest](../tests/fixtures/bruker2d_public_manifest.json) pins 25 files (36,803,045 bytes), source URLs, original metadata, fixed regions and acceptance gates. BMRB's [public access policy](https://bmrb.io/metabolomics/data_policy.shtml) does not establish an explicit redistribution license here. The fetcher downloads directly to ignored local storage; raw files are not committed or redistributed, and vendor scripts are never executed.

## Reproduce

From the project root after [installation](../README.md), use the environment's Python and `spinflow` executables (`.venv/bin/` on Linux, `.venv/Scripts/` on Windows). These commands use Linux paths:

```bash
.venv/bin/python tests/fixtures/fetch_bruker2d_public.py
.venv/bin/python tools/validate_public_bruker2d.py --backend numpy
.venv/bin/python tools/validate_public_bruker2d.py --backend native
export SPINFLOW_BRUKER2D_ROOT="$PWD/.artifacts/bruker2d-realdata/bmse000001"
.venv/bin/python -m pytest -q tests/io/test_bruker2d_public.py tests/cli/test_bruker2d_public_cli.py
# With the GUI extra installed:
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/gui/test_bruker2d_public_gui.py
```

The validator explicitly checks every source hash before and after processing and exits unsuccessfully on a failed gate. The dedicated public-data CI job fetches both acquisitions and runs the real tests and both backends; download or hash failures fail that job. Ordinary offline tests never fetch implicitly. The four acquired-data tests in `test_bruker2d_public.py` and the two cases in each public CLI/GUI module skip when `SPINFLOW_BRUKER2D_ROOT` is absent; seven manifest/fetcher checks run offline. A skip is not real-data validation.

The fixed user recipes reproduce the validator's preprocessing and write new native spectra:

```bash
.venv/bin/spinflow run examples/bruker-2d-hsqc-public.json
.venv/bin/spinflow autophase .artifacts/bruker2d-realdata/bmse000001/hsqc-transformed.nmrh5 .artifacts/bruker2d-realdata/bmse000001/hsqc-phased.nmrh5 --report .artifacts/bruker2d-realdata/bmse000001/hsqc-phase.json --memory 512MiB
.venv/bin/spinflow run examples/bruker-2d-tocsy-public.json
.venv/bin/spinflow autophase .artifacts/bruker2d-realdata/bmse000001/tocsy-transformed.nmrh5 .artifacts/bruker2d-realdata/bmse000001/tocsy-phased.nmrh5 --report .artifacts/bruker2d-realdata/bmse000001/tocsy-phase.json --first-order --memory 512MiB
```

Outputs are never overwritten. Automatic phase defaults to zero-order at each axis's center in the CLI; the Python API retains `first_order=True`. HSQC's two usable peaks span less than 15% of each sweep, so slopes cannot be identified and the first-order request is refused. TOCSY uses both zero- and first-order correction; forcing zero-order fails shared-phase consistency. A 1D `--pivot POINT` remains valid. For 2D, optionally pass `--axis-pivot a0=POINT --axis-pivot a1=POINT` with both IDs and in-range zero-based points. Omitting both uses the centers. Units are points, never ppm.

## Processing and interpretation

The explicit direct-axis digital-filter correction runs once on the original grid before any window/zero-fill/FFT and preserves both quadrature bits. Both windows use `first_point_scale=0.5` and `lb_hz=log(100)/(pi*(N-1)*dwell)` from the original acquisition grid, followed by zero fill to 1024 on each axis. Here `T=(N-1)*dwell` is the **original acquisition duration**: the exponential's target is 1% at that original endpoint, not at the last retained sample after correction. Correction removes 69 direct samples in each public case, so the actual retained direct endpoint factors are **1.8623389% for HSQC** and **1.3642607% for TOCSY**; the untrimmed indirect endpoint is 1%. The separate 0.5 first-point weight affects the first sample only; these endpoint percentages describe the exponential factor, not a ratio to that weighted first point. This acquisition-based damping suppresses finite-FID sidelobes, improving the isolated-line model fit while broadening lines and sacrificing resolution. It is a fixed processing choice, not a data-fitted adjustment or a free correction inside autophase. The archived TopSpin processing used a different squared-cosine window. Calibration uses acquisition `SFO1` and archived processing `SF`: `(SFO1-SF)*1e6/SF`; no fitted peak alignment, reversal, receiver gain or archived phase enters the estimator.

The joint estimator fits isolated positive separable Lorentzian-like crosspeaks against all four quadratures and applies two explicit numeric `phase` operations, one per named axis. Magnitude is used only to seed candidates. Real-only or partially complex 2D data cannot supply this phase information and are refused. Crowded, mixed-sign, nonseparable, inconsistent, aliased, weak-span and noise-only examples may be refused. First-order estimation requires separated usable peaks on each axis. Fitted background is used for estimation only and is not subtracted from the data. The JSON retains `axis_results`, accepted/excluded candidates, convergence, per-line normalized RMS, source/result digests, point pivots and flags. Every accepted line must meet the fixed 0.04 full-quadrature normalized-RMS limit. There is a simultaneous 180° ambiguity on both axes that leaves the complete tensor unchanged; the report states the chosen canonical branch. This gauge is not independently determined absolute phase.

In the GUI set **Processing working-array budget (MiB)** to 512 before processing these examples, choose **Open acquisition folder…**, enable **Correct Bruker digital filter**, then choose each axis separately. Set the recipe's line broadening, **Window first-point scale** to 0.5, zero fill to 8× for the 128-point indirect axis, and to 2× (HSQC) or 1× (TOCSY) for the direct axis. A corrected direct axis at 1× is padded back to its original size. Enter each recipe's explicit reference values and **Process**. On **Analysis / figures**, leave first-order unchecked for HSQC and check it for TOCSY, then apply automatic phase. Both axes are estimated together regardless of the selected edit axis. Manual phase/pivot controls remain separately retained per axis. Diagnostics survive display-channel and source/result-view changes. **Save runnable workflow…** records the actual preprocessing plus both numeric corrections, and replays from the untouched source; the next **Process** rebuilds the manual controls and discards automatic corrections.

## Fixed reference checks and observed results

Archived TopSpin 2.1 processed data and April 2011 audit records predate this implementation. An independent direct int32 decoder applies `2**NC_proc` and `OFFSET - SW_p*index/(SF*SI)`. Signed region integrals in physical Hz² use fixed ppm rectangles and no baseline correction. A separate interpolation/trapezoid-weight oracle agrees with application integration to a maximum relative error below **1e-14** over six reference regions (9.63e-15 in the original Windows check; 9.8414e-15 in the Linux installed-wheel check), below the **1e-10** gate. This establishes numerical integration on acquired reference data; raw processing is checked separately.

Raw automatically phased maxima found inside the fixed regions agree with archived region maxima within **1.3e-5 ppm**, below two processed bins per axis. This is agreement of ROI grid maxima, not accuracy against curated chemical shifts or a test of the peak-picking algorithm.

A separate Linux installed-wheel check ran the actual production CLI picker on component 0, positive sign, threshold 5% of the global maximum, median baseline, one-point minimum separation and maximum 1000 peaks. It returned **2 HSQC candidates**, recovering both strong curated 1JCH targets. Their absolute (carbon, proton) errors were **(0.005979, 0.010765) ppm** and **(0.103870, 0.010143) ppm**, within the unchanged two-bin tolerances **(0.292990, 0.025373) ppm**. The weak curated LR entry was **not separately recovered at this threshold**. TOCSY returned **6 candidates**, including all four fixed archived multiplet-region maxima, with per-axis errors approximately **2.099e-6 and 4.923e-6 ppm**, below the two-bin tolerances of approximately 0.025373 ppm. Both reports had no flags or truncation caps. Unlisted candidates are not false-positive ground truth; no curated TOCSY assignment list is claimed.

| Within-spectrum volume ratio | Observed relative error vs archived TopSpin | Predeclared gate |
| --- | ---: | ---: |
| HSQC H2_C28 / H3_C39 | 2.4508% | ≤30% |
| TOCSY H3_to_H2 / H2_to_H3 | 8.2028% | ≤30% |
| TOCSY H3_diagonal / H2_to_H3 | 13.3191% | ≤30% |
| TOCSY H2_diagonal / H2_to_H3 | 8.3037% | ≤30% |

The ratio gates and regions were declared before evaluating raw volume agreement. Ratios cancel arbitrary global receiver/FFT gain; they do not cancel region-dependent tails or phase errors. The 30% threshold is a conservative engineering tolerance for finite fixed rectangles under different windows and fitted phases, not a confidence interval or claimed measurement accuracy. HSQC accepted-line residuals were 0.00696–0.00835; TOCSY 0.00470–0.02550, all below 0.04. The validator emits exact values, recipes and diagnostics; NumPy and native runs both pass these fixed gates.

These are two acquisitions of one compound on one instrument. They do not establish broad Bruker support, vendor amplitude/array parity, concentrations, deconvolved multiplet volumes, independent export reopening or NMRPipe numerical equivalence. Additional compounds, acquisition variants and independent laboratory use remain open gates.

The frozen Linux installed-wheel suite passed **438 tests and 101 subtests**, with zero skips/failures. Actual CLI processing, phase operations on both axes, complete tensor/metadata replay and nine automated virtual-X11 workflow groups also passed. The [validation record](public-validation.md) distinguishes the fresh core environment from reused plot/GUI/test environments, identifies the exact source/package artifacts and records the QEMU startup-timeout allowance. This is automated virtual-display evidence; human desktop usability, Wayland and GPU behavior remain untested. The two user recipes and phase commands explicitly allow 512 MiB working-array estimates; the CLI and GUI 256 MiB default may correctly refuse these workflows. This estimate is not a hard process-memory limit.
