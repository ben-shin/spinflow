# First laboratory-workflow comparison: results

**Status: SCOPED PASS as of code commit `03cd13aa1517f19cd5f2f4933be9292c8020e061`.** The independent judge accepted R1–R7 for the declared single-case workflow after publication and CI verification on 29 October 2026. The original candidate failed the UCSF coordinate gate; the repaired candidate passed the fixed numerical and consumer checks. The selected cached public Agilent/Varian protein example was previously studied in this project and is not an independent holdout. The [protocol](laboratory-workflow-protocol.md) and [case record](../tests/fixtures/lab_workflow_case.json) define the exact scope; this acceptance does not establish general laboratory or format compatibility.

| Gate | Required evidence | Status |
|---|---|---|
| R1 — protocol and reference | Fixed source and original scripts; actual pinned NMRPipe 13 execution and version-semantics lock | PASS, scoped |
| R2 — resource prerequisites | Header-only planning and explicit GUI budget propagation, tested before larger processing | PASS, scoped |
| R3 — intermediate quadratures | S00–S10 and S03P available quadratures, including isolated FFT gates | PASS, scoped |
| R4 — final scientific comparison | Complete signed arrays, every absolute ppm coordinate, fixed manual phase and all five predeclared weak/strong ROIs | PASS after repair; original UCSF failure retained |
| R5 — independent consumers | Actual NMRDraw and UCSF Sparky reopening, signed contours, coordinates, and prescribed views | PASS, scoped |
| R6 — end-to-end benchmark | Equal-precision, equal-work single-core comparisons including conversion and output; wall, CPU, memory, I/O and scratch evidence | PASS for measurement protocol; SpinFlow slower |
| R7 — publication and scope | Measured results, limitations, and only evidence-supported compatibility entries | PASS as of code commit and CI below |

Gate statuses are reported against the preregistered limits. Missing checkpoints, unavailable applications, unequal work, or failed tests will remain visible; successful codec readback does not substitute for opening files in the named applications. Working-array estimates do not establish a process RSS limit, and one selected case cannot establish general laboratory or format compatibility.

## Original candidate numerical comparison

The bounded reports compare the predeclared public `cpjdcptan2d` case using NMRPipe 13.0 revision 2026.072.12.03 and the frozen native candidate. The independent small impulse and complex-tone control resolved `FT -auto` before this comparison: positive-exponent, unscaled centered transforms for the fixed States mode. Native data use the separately fixed quadrature-sign mapping and manual phase; no residual phase fit or peak-based realignment was used. The preregistered limit is 2e-6 for isolated FFT transitions and 2e-5 for processed full-array relative maxima, signed ROI sums and absolute ppm errors. The initial acquired quadratures must match exactly and peak bins must agree.

| Boundary | Relative maximum error |
|---|---:|
| S00 | 0 |
| S01 | 9.50571698e-08 |
| S02 | 9.50571698e-08 |
| S03 | 1.55915124e-07 |
| S04 | 1.4058739e-07 |
| S05 | 1.4058739e-07 |
| S06 | 1.69418446e-07 |
| S07 | 1.69418446e-07 |
| S08 | 1.61834753e-07 |
| S09 | 1.61834753e-07 |
| S10 | 1.61834753e-07 |
| S03P | 1.4058739e-07 |

S00 matched exactly. All checkpoint reports pass their declared gates; the separate S03P reference real projection matched S04 exactly. The largest checkpoint relative maximum was 1.69418446e-7. The full-array isolated FFT transition relative maxima were 1.36050265e-7 (reference) and 1.08444662e-7 (candidate) for direct a1, and 1.62612083e-7 and 1.34362286e-7 respectively for indirect a0; all are below 2e-6. The oracle uses complex128 FFT and separately checks fixed traces against literal DFT; actual case values are float32.

The final S10 signed-array relative maximum was 1.61834753e-7 (maximum absolute difference 0.03125). Maximum absolute ppm errors across every point were 9.52225285e-6 for a0 and 1.25239873e-5 for a1, each below 2e-5 ppm. The global peak bin and each of the five ROI peak bins agreed. Fixed native phase history was a1 +29 degrees and a0 0 degrees with zero first order; the reference direct phase was -29 degrees under its opposite quadrature convention. No automatic phase fitting was used.

| Source label | Predeclared class | Signed sum relative error | Peak bin |
|---|---|---:|---|
| T49 | strong | 1.77214005e-09 | equal |
| T11 | weak | 1.825684e-08 | equal |
| G14 | intermediate | 1.22605086e-08 | equal |
| E15 | strong | 5.15273616e-08 | equal |
| W43 | weak | 5.53465448e-09 | equal |

These labels and strength classes are protocol identifiers, not certified assignments or evidence of concentration accuracy. Near-noise sign differences remain diagnostic rather than a newly introduced gate. Direct NMRPipe export read-back also passed its numerical checks; this is not independent application reopening. At that original checkpoint, independent consumer checks were not yet complete; the later repaired candidate checks are reported below.

The [sanitized numerical record](evidence/laboratory-workflow-science.json) gives source report SHA-256 values, checkpoint input/output hashes and phase history without private paths. The execution lock SHA-256 is `36c48bd055c396d1d18fea99109e0be95e5ebc82a511fc9ad139bb4cf4e1026a`; the exact working-byte source snapshot SHA-256 is `1615641c823cc94d27bb911e8fd16177b4e632a8618300a4a4f9109950dcad40`. The [protocol](laboratory-workflow-protocol.md) identifies the original source scripts, exact recipe, input hashes, operations and comparison limits. These original-candidate measurements are case-specific; later repaired-candidate consumer and performance checks are reported below.

## Original candidate UCSF endpoint failure

The original candidate’s independently decoded UCSF endpoints failed the predeclared all-grid coordinate gate: cross-lane a0 maximum error is **2.0074422664606573e-5 ppm**, above the fixed **2e-5 ppm** limit; a1 is 1.52587890625e-5 ppm. The signed full-array relative maximum remains 1.6183475296127254e-7, and all five ROI peak bins and signed-sum gates pass. Both UCSF files' decoded matrices exactly match their own source lane; their individual same-lane a0 coordinate errors are 2.35994909e-6 ppm (candidate) and 1.29098143e-5 ppm (reference). These observations do not turn the cross-lane failure into a pass.

The fixed original script and protocol specify indirect sweep width 5555.556 Hz. The original candidate recipe did not explicitly apply that rounded conversion width and retained the acquisition-derived 5555.55555556 Hz; the exported UCSF headers record 5555.5556640625 Hz and 5555.55615234375 Hz respectively. The repaired recipe now applies the predeclared width explicitly. No tolerance was relaxed, peak alignment fitted, or result overwritten. The [sanitized failure record](evidence/laboratory-workflow-ucsf-failure.json) preserves the original measurements and file hashes. The original failed endpoint was not benchmarked; the later repaired candidate was benchmarked as described below.

## Repaired candidate numerical recheck

The repaired recipe explicitly applies the original script’s predeclared 50000 Hz and 5555.556 Hz sweep widths. It does not fit coordinates or change the tolerance. The full signed float32 arrays at all 12 checkpoints, including S03P, were bitwise identical to the original candidate; retained quadratures and acquisition metadata were unchanged. Frequency coordinates and their provenance changed as declared. This means coordinate-weighted Hz integrals can change even though signed point sums remain identical.

All 12 repaired checkpoint comparisons and both isolated FFT checks pass their original gates. S10 signed-array relative maximum remains **1.6183475296127254e-7**; maximum absolute ppm errors are **5.139000066378685e-6** for a0 and **1.2523987322765606e-5** for a1. The global and all five ROI peak bins agree, and all fixed signed-sum gates pass.

The independently executed Sparky `ucsfdata` decoder reproduced the full candidate and reference UCSF matrices exactly against their own accepted S10 arrays. Across the two UCSF files, each axis’s maximum ppm difference is **1.52587890625e-5**, below the unchanged **2e-5 ppm** gate. This is an independent binary decoder check; it does not by itself prove an interactive application reopening. The [sanitized repaired record](evidence/laboratory-workflow-science-v4.json) includes report and output hashes, all checkpoint measurements and bitwise-preservation evidence. Its scientific lock SHA-256 is `bf502422a34b698276fc7382dfe0fb6cf5937b140c28f1786d69d20b3756d57b` and its exact working-byte source snapshot SHA-256 is `c93565a83abc7bfcc2a9fddcfca5a759d84352c270d3febc8aa9cf17f16bcade`. The earlier failed output and failure record remain preserved; this recheck does not rewrite that result. The scoped consumer checks, full repaired installed suite and benchmark are reported below.

## Independent application reopening

NMRDraw 13.0 Rev2026.072.12.03 opened the repaired NMRPipe export, displayed the full extent and five fixed regions, and was closed and reopened on the same file. Sparky 3.115 independently opened the repaired UCSF export through its application reader; the full extent and all five regions were inspected on initial open and reopen. Reference views were carried forward from the prior session only after checking both reference file hashes and the pinned application/runtime/font environment. All four spectrum files remained unchanged across the checks.

Both positive and negative contours were enabled at magnitudes 30000×1.2^k for 20 levels. The five selected positive regions showed the expected signs and comparable shapes; NMRDraw's limit controls partly overlap an upper corner without covering the relevant peak centers. Sparky's actual API endpoints on both axes differ from the reference by 1.52587890625e-5 ppm, below the unchanged 2e-5 ppm gate. Pixel labels are rounded and cannot establish that tolerance; the separate complete-grid `ucsfdata` check provides the numeric gate. Display interpolation and color schemes are qualitative, not a substitute for sample comparison.

The [consumer evidence index](evidence/laboratory-consumers/index.json) links hashes to unmodified screenshots, including the full views, all five fixed regions, and candidate reopen views. [Sparky API endpoint corroboration](evidence/laboratory-consumers/sparky-api-coordinates.json) records the coordinate calculation. The screenshots display generic test-guest paths. Automated observation on a virtual desktop is not a human usability, Wayland or GPU validation. The earlier failed UCSF result remains recorded above.

## Installed regression

The measured v4 source archive `c93565a83abc7bfcc2a9fddcfca5a759d84352c270d3febc8aa9cf17f16bcade` and its wheel `705f7283cb297d7f2eac180910027f263a39996ed7737d58a60f0390b69a1bca` are the authority for the numerical, consumer and benchmark measurements above. That installed-package suite passed **813 tests and 101 subtests**, with zero skips, under ordinary test deadlines. These measurements were not repeated on the later final package.

After measurement, the candidate changed workflow-bundle cleanup and its regression test, plus documentation and public evidence. A separately built final-source archive `d561c427d7d8ef0059f3cc0452920403890a618511392d13e54480a615a868fb` produced the Linux wheel `38cd61b3abe3f147813fb52ff07f6db87d5da92488c99d40dcba7df4e4da51e9` and source distribution `26647370d2d77b4ad742abdf363e68807a480566a60ad00c59054e656289514c`. Its fresh installed-package suite passed **814 tests and 101 subtests**, zero skips, in 30.77 seconds; targeted POSIX checks passed **49 tests and 14 subtests**. This final validation precedes this documentary evidence update.

The only changed installed application module was `workflow_bundle.py`. Executed CLI, numerical, recipe and harness source hashes match v4; the native extension and 52 same-named loaded libraries were byte-identical, and the CLI import probe did not load the changed bundle module. The [sanitized final-validation record](evidence/laboratory-final-validation.json) records the exact hashes, source-delta and integrity report fingerprints. The final package is not relabeled as the measured binary, and these tests do not replace the selected-case scientific or consumer evidence.

## End-to-end benchmark on this case

**SpinFlow was slower in this measurement.** The median paired SpinFlow/NMRPipe wall-time ratio was **5.463×** for raw acquisition to NMRPipe output and **3.905×** for raw acquisition to UCSF output. These are seven paired observations per endpoint, not a general performance claim. Timing ran from each command start through exit, including process startup, reading/conversion, processing and output close. SpinFlow also performed its normal source/provenance and final-output SHA work inside that timed command; external harness identity and runtime checks occurred outside timing for both lanes. These are end-to-end results, not isolated kernel speed measurements; the UCSF reference lane additionally ran Sparky `pipe2ucsf`. Both used the fixed float32 processing sequence and explicit imaginary-channel discards.

| Endpoint and lane | Wall median (range), seconds | Full-tree CPU median, seconds | Sampled summed RSS median, bytes | Cgroup memory charge median, bytes |
|---|---:|---:|---:|---:|
| NMRPipe output — NMRPipe | 0.2391 (0.2314–0.2741) | 0.2195 | 64,983,040 | 73,818,112 |
| NMRPipe output — SpinFlow | 1.3391 (1.1287–1.5751) | 1.2667 | 554,123,264 | 507,072,512 |
| UCSF output — NMRPipe | 0.3015 (0.2891–0.5608) | 0.2771 | 64,933,888 | 83,722,240 |
| UCSF output — SpinFlow | 1.1796 (1.1291–1.2577) | 1.1038 | 554,500,096 | 507,084,800 |

The paired wall ratio ranges were 4.221–6.440 for NMRPipe output and 2.103–4.160 for UCSF output. Median paired total CPU ratios were 5.752 and 4.045 respectively. Median observer CPU was 0.0183/0.0564 seconds for the NMRPipe-output reference/candidate lanes and 0.0216/0.0494 seconds for UCSF. It was recorded separately, not subtracted.

| Endpoint and lane | Inclusive logical read / write, bytes | Guest virtual-block read / write, bytes | Sampled temporary logical / allocated peak, bytes | Final output logical / allocated, bytes |
|---|---:|---:|---:|---:|
| NMRPipe output — NMRPipe | 221,908,848 / 251,390,188 | 4,096 / 0 | 3,986,048 / 3,989,504 | 33,556,480 / 33,558,528 |
| NMRPipe output — SpinFlow | 62,324,091 / 33,557,823 | 4,096 / 33,558,528 | 33,556,480 / 33,558,528 | 33,556,480 / 33,558,528 |
| UCSF output — NMRPipe | 294,564,905 / 318,499,488 | 4,096 / 0 | 37,542,528 / 37,548,032 | 33,554,868 / 33,558,528 |
| UCSF output — SpinFlow | 62,322,462 / 33,556,197 | 4,096 / 33,558,528 | 33,554,868 / 33,558,528 | 33,554,868 / 33,558,528 |

Values in the tables are medians. The [sanitized measurement record](evidence/laboratory-benchmark-v4.json) retains all 28 measured jobs, four warmups, full spreads, paired ordering, output identities and individual observations. The [measurement and calibration sources](evidence/lab-harness/README.md) are exact-run audit material. Inputs were regular read-only copies in each lane, staged and hashed before timing; an earlier symlink preflight was refused before any timing and is not a trial. Workspaces included baseline input/config files in total disk counts; temporary figures exclude those baseline files and the final output. Sampled disk peaks are lower bounds, and an in-progress temporary file may later become the declared output.

The test used a single-vCPU WHPX guest with 4 GiB memory and one pinned host logical CPU. Its SMT sibling, host activity, thermal and power conditions were uncontrolled; these are virtualized results, not bare-metal measurements. A 5 ms observer shared the guest CPU outside the workload cgroup. Summed RSS double-counts shared pages and may miss peaks; cgroup charge includes cache and kernel memory. Logical I/O includes the small identical sentinel and observer/accounting effects. Guest virtual-block counters are not host physical-media I/O and reflect warm/cache-uncontrolled data and delayed writeback. No cache purge, fsync equalization, outlier removal or overhead subtraction was applied. Output identity validation retained raw hashes and excluded only documented creation timestamps for canonical comparison; other header and sample bytes remained checked.

## Validation environment closure

The owned test guest shut down gracefully and its local forwarded port closed on 29 October 2026. Its earlier 765-file science seal is a **pre-shutdown snapshot**: the live serial log subsequently received an authorized 8,456-byte shutdown append. Its exact 60,650-byte sealed prefix remains intact; the original log was not truncated, restored or rewritten. The other 1,550 sealed evidence files and all ten prior manifest files remained unchanged. The [sanitized closure record](evidence/laboratory-operational-closure.json) gives the prefix, suffix, full-log and manifest hashes. This operational qualification does not change the scientific or timing measurements.

## Publication and independent acceptance

On 29 October 2026 the independent judge accepted this scoped R1–R7 result on published code commit [`03cd13aa1517f19cd5f2f4933be9292c8020e061`](https://github.com/ben-shin/spinflow/commit/03cd13aa1517f19cd5f2f4933be9292c8020e061), tree `a958a5fee303e489f641b7529ffaa296666e7a63`. All five jobs in the [exact-commit hosted CI run](https://github.com/ben-shin/spinflow/actions/runs/40940807897) passed:

| Hosted job | Result |
|---|---|
| Ubuntu 24.04, Python 3.12 | 730 passed, 11 skipped, 3 deselected, 87 subtests; installed wheel and source distribution checks passed |
| Ubuntu 24.04, Python 3.13 | 730 passed, 11 skipped, 3 deselected, 87 subtests; installed wheel and source distribution checks passed |
| Python 3.13, Qt offscreen | 805 passed, 9 skipped, 101 subtests |
| Python 3.13, plotting without Qt | 4 passed |
| Public acquisition validation | 1 Bruker 1D, 16 Bruker 2D and 11 Varian/hard-spectrum/crop tests passed, with their validators |

The skips reflect unavailable public-data roots in those general jobs; deselections exclude GUI cases from the headless runs. They are not counted as acquired-data validation. The dedicated public job supplies its own data and checks. The separate final installed Linux validation passed 814 tests and 101 subtests without skips, as described above. Scientific and benchmark measurements remain attributed to the earlier v4 build, not the later code commit's rebuilt binary. The observed 5.463× and 3.905× median wall-time ratios show SpinFlow was slower in this one virtualized case. The published v0.2.0 prerelease remains unchanged and does not include this later source update.
