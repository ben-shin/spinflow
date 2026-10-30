# New Bruker acquisition qualification results

**Overall qualification: FAIL.** One acquired Stage A execution on the frozen installed SpinFlow 0.3.0b2 candidate returned exit code 1: **50 of 56 checks passed; six of the 18 deposited 1H target checks failed**. The failed rows are 1, 5, 8, 9, 12 and 14. The passing import and mathematical controls do not override the failed peak-position lane.

This is a result-reporting revision after the earlier pending checkpoint, not a new candidate algorithm or a replacement acceptance protocol. The [scientific protocol](bruker-qualification-protocol.md), [input manifest](../benchmarks/cases/bmse000002-qualification.json), harness, targets, recipe, phases, regions and tolerances stayed frozen. Root approved scientific lock v2 and the separate installed environment before execution. No acquired candidate rerun or post-output adjustment was made for this qualification. Independent adjudication confirmed the failure and retained every target.

## Exact candidate and evidence

The installed run used CPython 3.12.3 on Linux x86-64, NumPy 2.5.4 and nmrglue 0.12. Distribution, Python package and native extension reported 0.3.0b2. The source and installed artifacts below identify this private candidate cohort; this result is not validation of an arbitrary later build. All identities are SHA-256.

| Record | SHA-256 |
| --- | --- |
| Scientific lock v2 | `10c27a39fa0e6ae3fe3731de523c321ea1b50ee092b0c986ac3ffb6612300772` |
| Frozen source ZIP | `bbb526d0f83f741bcdc1f79f0017dad8cb09f675bd5cf13c805702e8e646799a` |
| Source manifest | `c6051b2f396a3cffffd66970da76acbce60de9676cd1f59d6b703691988ff6fb` |
| Installed CPython 3.12 Linux x86-64 wheel | `00a8a79d1889e39d2ee289b8dfc2056f81b2c6f76059eb18061669d622e03e2a` |
| Installed native extension | `edba4c56038216af718b850174332182394cc588204c5f741c201ea9635014de` |
| Umbrella installed-candidate environment lock | `0351c9be5f887509954edc4a3790d66018677ec730b5ce48fb149ee58512cea2` |
| Harness runtime lock referenced by acquired report | `ed7c86903b665f3b2a37c50de3f2677139560ec3194fd45ca5d009f179dcca1d` |
| Acquired result JSON | `cfe51396a23ab27c673da1d7407c0c7d03846db95c47d4deb93e4eeb24b5567b` |
| Independent acquired adjudication | `d80374de61a8078819f1db73b41a7be20ac2523b3df87d36cf01d2ca00047704` |
| Post-failure diagnostic JSON | `0b1fe7dc1dfca00106e0a214fd495ac3836930c682069afd8f3766c5b2ec3486` |

The umbrella environment lock binds the source, installed package and related evidence. The distinct harness runtime lock pins the actual imported modules and dependencies and is the environment hash recorded in the acquired result. Neither lock is itself a scientific pass.

Private retained evidence is under `.artifacts/v1-qualification/acceptance/stage-a-b2-v1/` (result JSON, stdout, stderr and actual exit code), `acceptance/b2-evidence-v1/` (identity records), `bruker/` (unchanged scientific locks v1/v2), and `review-a/acquired-v1/` (independent adjudication and diagnostic). The raw/vendor files remain private. The independent reviewer rehashed all 19 source files; the execution report also records unchanged input identities after the run.

## Results by lane

| Lane | Result | Evidence and precise scope |
| --- | --- | --- |
| Raw 1H import | PASS | Exact complete float64 `[2,9739]` sample equality against the literal integer decoder; maximum error 0. Quadrature ID and raw time coordinates/axis metadata passed. |
| Raw multiplicity-edited HSQC import | PASS | Exact complete float64 `[4,128,512]` equality against the fixed Echo/AntiEcho channel decoder; maximum error 0. Both quadrature IDs and physical axes passed. This does not establish processed absorptive phase or sign accuracy. |
| Existing 1D digital-filter corroboration | PASS | Maximum absolute difference `3.4612763550408431e-09` versus the pinned nmrglue function; unchanged rtol `1e-11` / atol `1e-7`. Shape `[2,9670]` retains the existing 69-point trim. Differential corroboration, not biological truth. |
| Native versus NumPy fixed 1D recipe | PASS | Maximum sample difference `0`; complete float64 `[2,32768]` arrays, axes and quadratures agree under unchanged rtol `1e-11` / atol `1e-5`. |
| Header-derived processed 1D grid | PASS | Maximum discrepancy `3.5527136788005009e-15` ppm under the fixed `2e-5` ppm gate. This verifies the declared grid formula, not independently measured chemical calibration. |
| All deposited 1H picked targets | **FAIL** | 12 of 18 pass the unchanged strict `<0.002` ppm limit after magnitude selection strictly within ±0.004 ppm. All rows appear below. |
| Stored-reference HSQC numerical analysis | PASS | Four complete component-retention checks, eight absolute-grid checks and all 16 signed integration comparisons passed. Worst grid discrepancy `2.8421709430404007e-14` ppm; worst integral relative error `5.1524615182244797e-13` versus `1e-10`. All denominators were usable. These calculations use unchanged archived TopSpin matrices, not candidate raw-to-TopSpin results. |

## All 18 deposited targets

Values below are generated directly from the acquired JSON. A PASS applies only to the stated picked-target neighborhood test; closely spaced rows can select the same magnitude maximum. No targets were removed or reclassified.

| Row | Deposited target (ppm) | Candidate selected maximum (ppm) | Absolute discrepancy (ppm) | Strict gate |
| --- | ---: | ---: | ---: | --- |
| 1 | 4.5159 | 4.512260706070489 | 0.003639293929512 | **FAIL** |
| 2 | 4.5100 | 4.510674869567693 | 0.000674869567693 | PASS |
| 3 | 4.5005 | 4.501159850550924 | 0.000659850550925 | PASS |
| 4 | 4.4914 | 4.491644831534154 | 0.000244831534155 | PASS |
| 5 | 4.4850 | 4.488869617654263 | 0.003869617654263 | **FAIL** |
| 6 | 4.0497 | 4.051178742882868 | 0.001478742882869 | PASS |
| 7 | 4.0429 | 4.044042478620291 | 0.001142478620291 | PASS |
| 8 | 4.0404 | 4.044042478620291 | 0.003642478620291 | **FAIL** |
| 9 | 4.0334 | 4.029769950095138 | 0.003630049904863 | **FAIL** |
| 10 | 4.0283 | 4.028977031843739 | 0.000677031843740 | PASS |
| 11 | 4.0214 | 4.022237226706862 | 0.000837226706862 | PASS |
| 12 | 4.0190 | 4.022237226706862 | 0.003237226706862 | **FAIL** |
| 13 | 4.0121 | 4.012722207690091 | 0.000622207690091 | PASS |
| 14 | 4.0088 | 4.012722207690091 | 0.003922207690091 | **FAIL** |
| 15 | 3.9995 | 3.999639056542034 | 0.000139056542034 | PASS |
| 16 | 3.9903 | 3.990124037525264 | 0.000175962474736 | PASS |
| 17 | 3.9781 | 3.977437345502905 | 0.000662654497095 | PASS |
| 18 | 3.9691 | 3.968318785611834 | 0.000781214388166 | PASS |

## Post-failure reference diagnostic — explanatory only

After the failed run, the independent reviewer decoded the pinned archived TopSpin 1r/1i arrays and their recorded ppm grid, then applied the **same** magnitude selection, ±0.004 ppm neighborhood and strict `<0.002` ppm criterion to all 18 targets. This was not preregistered acceptance evidence and did not rerun the candidate.

The stored reference fails exactly the same six rows:

| Row | Archived selected maximum (ppm) | Archived absolute target discrepancy (ppm) | Same strict criterion |
| --- | ---: | ---: | --- |
| 1 | 4.512258137761141 | 0.003641862238859 | **FAIL** |
| 5 | 4.488867049344917 | 0.003867049344916 | **FAIL** |
| 8 | 4.044039910310943 | 0.003639910310943 | **FAIL** |
| 9 | 4.029767381785789 | 0.003632618214211 | **FAIL** |
| 12 | 4.022234658397513 | 0.003234658397513 | **FAIL** |
| 14 | 4.012719639380743 | 0.003919639380743 | **FAIL** |

For these six failures, the candidate-selected and archived-selected maxima differ by only `2.568309347e-06`–`2.568309348e-06` ppm. Rows 7/8, 11/12 and 13/14 select the same respective maximum in both spectra; their target spacings are 0.0025, 0.0024 and 0.0033 ppm, within the fixed search radius. The complete 18-row reference diagnostic remains in the pinned diagnostic JSON.

This supports a mismatch between the fixed magnitude-neighborhood test and this deposited picked-target list as an explanation, rather than a candidate-specific gross coordinate displacement. It does not reconstruct the depositor's picking semantics or prove identical processing. Original windowing, automatic/manual phase, baseline correction and HSQC linear prediction remain confounders, as recorded in the unchanged protocol. **The actual fixed qualification remains FAIL.** No narrower passing lane or later diagnostic retroactively changes it.

## Remaining limits

The two acquisitions come from the same Madison source/instrument family as earlier Bruker evidence, not an independent laboratory cohort. Metadata, the deposited target list and reference-only mathematical regions were exposed as declared. Four signed mathematical regions do not establish five-region weak/strong recovery, sensitivity or assigned peaks. No automatic phase was fitted for this stage.

Raw-to-original TopSpin or NMRPipe full-processing parity, intermediate processed-quadrature parity, physical phase/sign/volume accuracy, biological assignments, concentrations, independent consumer reopening, representative hardware performance and actual human researcher acceptance remain **UNVERIFIED**. The original TopSpin baseline, linear-prediction and phase actions were not silently omitted from a claimed equivalent workflow. No stable profile promotion or new compatibility guarantee follows from this result. Any future changed scientific question would need its own preregistration and appropriate independent evidence while preserving this failure.

The preparation record remains separate: sample-blocked header descriptions passed, and five synthetic harness controls passed. A first local test attempt encountered an inaccessible temporary directory; the subsequent private-directory run passed without a product change. Those preparation passes were never an acquired-data acceptance result.
