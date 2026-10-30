# Stage 16 performance results

**Final measured product: v3.** The frozen baseline and candidate completed nine tasks and all 63 measured pairs agreed. On this one acquired 2D case, cold command wall time fell by about 29–30%, full warm recipe latency by 62%, and the measured interactive phase-prefix latency by 74%. Import and export results show no consistent improvement. Actual NMRDraw and Sparky reopening checks passed for this case; this report does not claim NMRPipe speed parity.

The [protocol](performance-protocol.md) and [machine-readable observations](evidence/performance-v3.json) define the scope. The input is the previously studied public Varian `cpjdcptan2d` protein example, not a blinded holdout. Cold and warm full-recipe tasks retain all 14 original operations, float32 precision, quadratures, padding, calibration, provenance and normal output hashing. Interactive tasks use the separately declared first 12 operations ending at indirect phase, before final imaginary discard and transpose.

## Latency distributions

Seconds are median [minimum, maximum]. IQR uses `statistics.quantiles(n=4, method="inclusive")`. Ratios are the median of seven paired candidate/baseline wall ratios; they are not ratios of the two medians. All observations, including high values, remain included.

| Task | Baseline seconds | v3 seconds | IQR baseline / v3 | Paired v3/baseline ratio [range] |
| --- | ---: | ---: | ---: | ---: |
| Cold acquisition to NMRPipe, full 14 operations | 1.0736 [1.0199, 1.6037] | 0.7413 [0.7242, 0.7474] | 0.3036 / 0.0117 | 0.6961 [0.4559, 0.7281] |
| Cold acquisition to UCSF, full 14 operations | 1.0307 [1.0197, 1.0798] | 0.7315 [0.7180, 0.7586] | 0.0217 / 0.0147 | 0.7078 [0.6760, 0.7365] |
| Warm full 14-operation recipe | 0.3926 [0.3712, 0.5753] | 0.1467 [0.1431, 0.1586] | 0.0788 / 0.0087 | 0.3774 [0.2738, 0.3939] |
| Warm Varian acquisition import | 0.0088 [0.0082, 0.0098] | 0.0090 [0.0083, 0.0109] | 0.0008 / 0.0012 | 0.9992 [0.9068, 1.3173] |
| Repeated immutable S10 numeric digest | 0.016077 [0.015988, 0.016152] | 0.000009 [0.000008, 0.000012] | 0.000097 / 0.000001 | 0.0005 [0.0005, 0.0007] |
| Warm NMRPipe export of fixed accepted S10 | 0.0575 [0.0467, 0.0741] | 0.0563 [0.0532, 0.0727] | 0.0113 / 0.0045 | 1.0268 [0.7599, 1.1996] |
| Warm UCSF export of fixed accepted S10 | 0.0502 [0.0461, 0.0603] | 0.0504 [0.0463, 0.0613] | 0.0065 / 0.0076 | 1.0149 [0.7767, 1.3257] |
| Unchanged 12-operation phase prefix | 0.2955 [0.2914, 0.3129] | 0.0767 [0.0756, 0.0876] | 0.0073 / 0.0037 | 0.2590 [0.2455, 0.2987] |
| Changed last phase, 12-operation prefix | 0.2978 [0.2924, 0.3008] | 0.0764 [0.0760, 0.0870] | 0.0064 / 0.0037 | 0.2597 [0.2548, 0.2892] |

The digest candidate median is about 9 microseconds; its exact unrounded values are in the JSON. Import and export paired ratios are close to one; small differences and ranges are not evidence of a general improvement. The two interactive rows are 12-operation prefix costs, not full 14-operation processing or measured GUI rendering.

## CPU and memory

CPU values are median process seconds. RSS is MiB (1,048,576 bytes). Cold values are Linux `wait4` high-water RSS for each fresh CLI process, including imports and the endpoint. Warm values are persistent-worker lifetime high-water samples immediately after API return, before that response’s identity hash; setup, prior operations and prior harness hashes remain included. They are not seven independent per-operation memory peaks.

| Task | CPU baseline / v3, s | RSS baseline / v3, MiB |
| --- | ---: | ---: |
| Cold acquisition to NMRPipe, full 14 operations | 1.0590 / 0.7349 | 529.02 / 389.91 |
| Cold acquisition to UCSF, full 14 operations | 1.0174 / 0.7271 | 529.07 / 389.98 |
| Warm full 14-operation recipe | 0.3916 / 0.1462 | 575.50 / 436.34 |
| Warm Varian acquisition import | 0.0088 / 0.0090 | 195.43 / 195.46 |
| Repeated immutable S10 numeric digest | 0.016062 / 0.000010 | 195.44 / 195.45 |
| Warm NMRPipe export of fixed accepted S10 | 0.0548 / 0.0553 | 235.59 / 235.73 |
| Warm UCSF export of fixed accepted S10 | 0.0494 / 0.0494 | 235.80 / 235.68 |
| Unchanged 12-operation phase prefix | 0.2950 / 0.0764 | 575.44 / 564.70 |
| Changed last phase, 12-operation prefix | 0.2973 / 0.0763 | 575.36 / 564.72 |

Cold NMRPipe-output RSS fell from about 529.02 to 389.91 MiB; full warm worker high-water samples fell from about 575.50 to 436.34 MiB. Cache residency is separate from RSS and is bounded by the selected policy.

## Timing and resource controls

There are 148 recorded observations: 126 measured (nine tasks × seven pairs × two lanes), 18 warmups and four separately labeled interactive priming runs. Order alternates baseline/candidate and candidate/baseline. No cache purge, fsync equalization, outlier removal, retry substitution or overhead subtraction was used. “Cold” means a fresh process, not a cold filesystem cache.

Cold wall time runs from before direct CLI process creation through exit: startup, imports, raw conversion/read, validation/planning, processing, required source/provenance hashes, export, normal output close and CLI reporting stay inside. Warm API time brackets the public operation in an already running worker; imports and input preload are excluded. Normal product validation/provenance and writer hashing remain included. Harness result identity hashes and transport are outside API time. Complete parent request wall and complete worker lifecycle wall are recorded separately in the JSON; lifecycle wall includes interleaved idle waits and is not an operation latency.

The final cohort used Ubuntu 24.04.5, Python 3.12.3, one QEMU q35/WHPX vCPU, 4 GiB RAM and a 48 GiB virtual disk. QEMU was pinned to host logical CPU 2 (affinity mask 4) on a Ryzen 7 9800X3D; workers used guest CPU 0, native FFT thread count one, and effective numerical pools one. SMT sibling, host activity, thermal and power conditions were uncontrolled. These are virtualized results, not bare-metal measurements. Both final lanes used the same expanded guest; superseded v2’s 32 GiB cohort is not mixed into these statistics.

## What removed work

The private execution frame reuses mutable working buffers and shares established immutable sample owners and metadata records between internal transitions. Public Dataset boundaries still validate and return immutable samples with defensive metadata/provenance access. Exact built-in Dataset and Axis eligibility guards preserve uncached public behavior for subclasses and mutable axis variants.

Separate [profiles and instrumentation](evidence/performance-profiles-v3.json), excluded from timing trials, recorded the following for the full acquired recipe:

| Python-visible work | Baseline | v3 |
| --- | ---: | ---: |
| Public Dataset freezes / ndarray `tobytes` | 14 | 1 |
| Bytes materialized at those freezes | 399,362,688 | 33,554,432 |
| Full-array finite scans | 28 | 7 |
| Input bytes covered by finite scans | 798,725,376 | 198,953,600 |

The metadata-heavy deep-copy work also falls sharply in the diagnostic profile; complete axes, metadata and provenance still match in the measured signatures. cProfile covers the measure wrapper, including later signature/report work; engine interpretation uses the `Runtime.operation` / `run_operations` subtree. Profiled times are not benchmark speed ratios. Native C++ internal allocations are not directly counted by Python instrumentation.

No new C++ kernel, FFT library, precision or threading policy was introduced. Baseline and v3 native extensions are byte-identical, as are all 667 dependency shared-library files checked. The existing NumPy reference backend and frozen baseline remain available for comparison. Reprofiling supported removing copies and record duplication first; another native kernel is deferred until a remaining measured cost justifies it.

The recipe cache uses at most 128 MiB and is reduced further by headroom after the complete plan’s conservative working-array estimate; this acquired benchmark selects a 1 GiB working-array policy. The GUI default remains 256 MiB. Retention includes unique numeric owners, metadata, keys and record overhead. A separate numeric-digest memo has a 32 KiB object budget. Complete recipe validation precedes hits, and source/provenance hashing required by CLI endpoints is retained.

When predecessor and final snapshots cannot both fit, v3 keeps the predecessor. Every one of the seven measured changed-phase updates reused 11 steps. This prioritizes stable parameter editing over holding every final result: unchanged phase-prefix calls also replay the last phase in this bounded case. Priming cost and cache retained bytes are preserved separately in every observation.

## Scientific and installed-package checks

The [scientific record](evidence/performance-science-v3.json) rechecks all 12 acquired checkpoints (S00–S10 and S03P), both isolated FFT transitions, all five fixed weak/strong ROIs, signed samples, peak bins and complete ppm grids. S00 matches exactly. The largest checkpoint relative maximum is 1.6941844577212034e-7; final S10 is 1.6183475296127254e-7. Full native ppm errors are 5.139000066378685e-6 and 1.2523987322765606e-5 ppm. Independent pinned Sparky `ucsfdata` full-matrix extraction matches each lane’s accepted S10 exactly, with cross-lane maximum 1.52587890625e-5 ppm on both axes.

The original limits remain 2e-5 for processed relative maxima and signed ROI sums, 2e-6 for isolated FFT transitions, 2e-5 ppm for every physical coordinate, and exact peak bins/raw quadratures. No phase, alignment or amplitude fit was introduced. All 63 measured pairs agree: 28 numeric/metadata/provenance signature pairs, seven digest pairs, and 28 output pairs with identical payload and every header byte except the declared creation timestamps. Raw timestamp bytes and full output hashes remain recorded.

The final fresh installed Linux package passed **863 tests and 101 subtests, zero skips**, in 24.91 seconds under ordinary deadlines. All 20 imported production modules resolved to the installed wheel, and package bytes remained unchanged. This suite follows the scientific and diagnostic runs; it is not a performance observation.

## Reproduction boundary

The controller used the following invocation in the isolated test guest. Its `performance/harness-v3` files are byte-identical to the tracked [controller](../benchmarks/performance_compare.py) and [worker](../benchmarks/performance_worker.py). Both lanes must be separate installed environments built from the pinned baseline and v3 archives with the recorded dependencies, one guest CPU and one thread per numerical pool. The command refuses an existing output directory; a replay must choose a new empty output path.

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
python3 performance/harness-v3/performance_compare.py \
  --baseline-python /home/validator/performance/baseline-runtime/bin/python \
  --candidate-python /home/validator/performance/candidate-v3-runtime/bin/python \
  --raw /home/validator/coverage-public/2d/agilent2pipe_2d/agilent_2d \
  --recipe /home/validator/performance/baseline-source/examples/laboratory-workflow.json \
  --export-source /home/validator/lab-native-checkpoints-v4/S10.nmrh5 \
  --output /home/validator/performance/paired-v3 --candidate-cache
```

The default is seven pairs for each of all nine tasks. The raw acquisition must match both pinned `fid` and `procpar` identities; the recipe and accepted S10 preload must also match the execution-lock identities below. The fixture’s public-source URLs and original scientific conventions are in the [case record](../tests/fixtures/lab_workflow_case.json) and [laboratory protocol](laboratory-workflow-protocol.md).

| Reproduction input | SHA-256 |
| --- | --- |
| `fid` (3,993,328 bytes) | `4a35f4b98f9116ba67760d1c0c83cc48a17b4e53897b5dbe1e25f85f28b1eac8` |
| `procpar` (22,523 bytes) | `54cf12841987ec3a05f5ca76f2b09dff09b7648d84b2936627f2c9757472c817` |
| Frozen recipe (1,382 bytes) | `69e6c1e26c80c1fdc3ac0401c2477355a2d55dc05b97471b68dacdeea9787cb5` |
| Accepted native S10 export preload (33,721,208 bytes) | `2c16f31d12789049229e25bf17f9569f4cb5d2b9266d05a980d412d80a46917d` |

The exact Linux wheels, installed environments, accepted native S10 preload, licensed reference programs and binary trial outputs are retained private audit artifacts. This invocation documents the executed cohort; the public checkout alone is not a byte-for-byte replay bundle. Compact public JSON retains all timing observations and input/output identities without distributing acquisition or vendor/spectrum binaries.

## Exact build identities

[Runtime/source/build receipts](evidence/performance-runtime-v3.json) bind the source archives, build outputs, installed modules, dependency libraries and active pools. The baseline is Git commit `62cac5c7e5074091c382aedb9eae154a8f8d996d`. The measured candidate is frozen product v3; a later documentary commit is not relabeled as this measured wheel. The [source-to-publication map](evidence/performance-source-map.json) records the measured archive bytes against publication Git blobs, including intentional CRLF-to-LF normalization. The measured wheel was built from the archived working bytes; Git normalization does not make a later rebuilt wheel byte-identical.

| Identity | SHA-256 |
| --- | --- |
| Baseline source archive | `bfce934ef80dd88abb21d4331f2f75c57808e8b3136560d237a2dbd71ebd1cbb` |
| Baseline Linux wheel | `69a48c451e0be160e8ad504759f1aef0e23e009e2697e48617dd02500e983fc9` |
| Candidate v3 source archive | `60d732c38870695f3710f842bb7c4b10b117f72044fb8f8ed941b0ad2304ba71` |
| Candidate v3 Linux wheel | `25b9c4cac4faec7ae225ac34fea5d10a6f0c71ab829f39cf4194f14d295b2920` |
| Shared native extension | `854608737e5cfef2aef67fe9d10e39e8f13d8d6178609adcaffbe11a779c0a80` |
| Frozen two-file harness archive | `4bb14e5d13ee7adc39264e0d69f54d4924e64aaa4efe8a235579d0b9e4737cbc` |
| Scientific execution lock v3 | `add1487e413ea20ff06044d58d205f672037a30460b8995baa24fc897f3e0fb1` |

GNU 13.3.0, CMake Release/C++20 `-O3`, scikit-build-core 1.1.1 and pybind11 3.1.0 built both lanes offline with cached tooling. The native PocketFFT commit remains `4d865cba24359b05b37da4e75aee24498c3b78e7`. The final audit resolves and matches all 80 direct science-lock records, 693 runtime-pin transitive records and 25 baseline receipt records.

## Independent consumers

The [actual consumer record](evidence/performance-consumers/consumer-record.md) and [21 unmodified new screenshots](evidence/performance-consumers/index.json) document NMRDraw 13.0 Rev2026.072.12.03 and Sparky 3.115 opening the new v3 exports. Both applications displayed the full spectrum and all five prescribed regions. NMRDraw was closed and freshly reopened for its full view; Sparky repeated the full view and five regions after close-without-save/reopen. Both contour signs were enabled at magnitudes 30000 × 1.2^k for 20 levels. Retained reference views were re-inspected after full reference-file/runtime/font verification; they were not newly recaptured.

Actual Sparky API endpoints differ from the reference by 1.52587890625e-5 ppm on each axis, below the unchanged 2e-5 ppm gate, and initial/reopened API metadata match exactly. Rounded pixel labels cannot establish that tolerance; the independent full-matrix/full-grid decoder supplies the numerical check. All four spectrum files, all 504 executable/library/font pins and all 85 observed loaded-file mappings remained consistent. Both applications were closed and zero consumer processes remained. The record retains a DISPLAY-less initial Sparky startup failure before successful relaunch. Automated observation on a private virtual desktop does not establish human usability, Wayland/GPU behavior or broad format compatibility.

## Superseded attempts and operational qualifications

The [superseded record](evidence/performance-superseded.json) retains v1’s 34 completed observations and all 148 v2 observations separately. V1 was stopped on root request after public-subclass compatibility defects; its numeric diagnostics were not final acceptance. V2 returned correct values but evicted the reusable phase predecessor on alternating updates under byte pressure. V3 is a fresh complete cohort after the second focused repair; no observations were selectively removed or reused as final v3 measurements.

A private v2 diagnostic driver passed `PosixPath` to `pstats.Stats` and stopped before starting its installed suite. The failed attempt remains retained; a separate corrected retry completed, followed by 861 tests and 101 subtests. This did not alter the frozen timing harness or any measured trial.

A separate S16 bookkeeping error reused `hardware-current.json` while sealing v1, v2 and v3. The original v1 and v2 hardware snapshots were overwritten; their old hashes and the exact missing-file qualification remain public in the [operational receipt](evidence/performance-operational.json). Every other direct v1/v2 lock record verified. Those superseded locks are not fully replay-ready. Final v3’s snapshot matches its lock and is preserved as a separate [versioned hardware record](evidence/performance-hardware-v3.json); no old snapshot was fabricated. Trial observations, source/build/runtime/science products and spectrum payloads remain retained privately.

Automatic approval review rejected deletion of redundant trial payloads. **Zero outputs were deleted.** The owned new S16 overlay was expanded from 32 to 48 GiB instead, with graceful guest shutdown, new restart logs and unchanged CPU/RAM controls. Old backing disks were read-only. The owned final guest shut down gracefully at 12:20:19 UTC on 30 October 2026: verified QEMU PID 41232 exited and localhost port 22230 closed, with no force kill or host-global setting change. Closed serial log length is 69,286 bytes, SHA-256 `46f32ddb2de7a10dfdae089a46c48ffe390f66e33db42978b809972255d34fec`. Stable private evidence sealing follows shutdown, rather than sealing an open live log. Four final/reference spectra are retained separately on the private host for offline independent matrix auditing; no spectrum or vendor binary is published.

Historical S15 integrity checks verified ten manifests: 1,550 members retain full-file equality, while the old serial log retains its exact 60,650-byte sealed prefix plus the previously authorized 8,456-byte shutdown append. This is not a claim that all 1,551 raw full-file hashes equal their pre-shutdown seals. No historical seal was edited.

These results establish bounded improvements for this selected virtualized 2D workflow. They do not establish NMRPipe performance parity, a broad backend ranking, bare-metal performance or large 3D/4D processing. Any future large-dimensional claim requires a proven bounded block path and measured memory for that workload.
