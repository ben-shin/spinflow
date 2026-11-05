# Stored-spectrum analysis engineering results (S20)

**PASS for the fixed stored-spectrum mathematical/software check only: 38/38 checks, one acquired execution, exit code 0. The S19 scientific qualification remains FAIL (50/56); human/physical-machine acceptance remains UNVERIFIED.**

This follows the unchanged [predeclared engineering protocol](stored-analysis-protocol.md) and [manifest](../benchmarks/cases/bmse000002-stored-analysis.json). It exercises the actual public signed peak picker on previously exposed, already processed TopSpin `1r` and HSQC `2rr` matrices from BMRB bmse000002. The integer decoder, extrema/plateau oracle, coordinate grid and cell-volume arithmetic are independent of the product analysis helpers. This is source-host engineering validation on the same Madison source/instrument family, not a blind holdout, independent laboratory acceptance, raw-to-TopSpin processing parity or biological truth.

## Exact cohort and execution

The single execution closed at `2026-11-05T15:19:53.566541+00:00`. It used Windows 11 (`Windows-11-10.0.26300-SP0`), CPython 3.13.15, NumPy 2.5.4 and SciPy 1.18.1. The imported source package identifies itself as 0.3.0b2. This local environment has **no installed `spinflow` distribution metadata** (recorded as JSON null); it is not an installed Ubuntu release test. No native processing kernel or FFT is invoked by this check. There is no new runtime, memory or representative-hardware performance claim.

The prepared lock pins 682 imported Python/compiled modules, including actual NumPy `_multiarray_umath` and SciPy `ndimage._nd_image` compiled modules. All 19 original file identities passed before and after execution; complete full-array float64 sample retention and the original lock/tool/protocol/manifest identities passed. The final source report retains every oracle and product detection, complete product integration reports, every comparison and unmatched target/detection. Every output was new; nothing was overwritten.

| Record | SHA-256 |
| --- | --- |
| Protocol | `3912d0ee2dfa27c7b56b8e418ebf9d423693c163e2dda7cd47f961c7e756ece8` |
| Manifest | `3894f3b251955204a21d1bfca7837d75bc388d16eec3a92e980cbec2f598fd45` |
| Validator | `e6985093918673feeb8c6b5c1d04800878cd9c84bde16d80f3e3acefb54b5c97` |
| Analytical/falsification tests | `27ab97d6b0b70528e3be001808f2d7fb4fd629ad405ebc3eed3c8cbbd7305596` |
| Prepared and root-approved lock v3 (private) | `aadbae7f5e5fca02a23e2a2489744a68aff211d102b2bbe30899f1926e8f4c0f` |
| Acquired complete result (private) | `6604dd42b8ffd2f4f6770b74e17fe875745195740a5b5c7b0abd7e12683394c4` |
| Process/exit/identity receipt (private) | `7b683d5a81672b56b17ec976fa3491722092075e07ec657fe0f0d64343a996f8` |

The unchanged product analysis source SHA-256 is `27674769467f42f80df320b8b15b3972164f6ce431f704a0b422f7777ae3e00a`. Private receipts are under `.artifacts/s20/science/`; sample binaries and local runtime evidence are not redistributed. The frozen metadata/protocol are LF files, with zero CRLF pairs, so no line-ending conversion is part of this cohort.

## Complete peak-picker accounting

The fixed calls use both signs, full-array amplitude MAD/normal noise, SNR 5, no baseline subtraction, no masks, one-point separation and a maximum of 10000 peaks. There were no overflow or truncation flags. Full ordered point/sign/intensity/plateau/edge-flag agreement was exact; threshold and noise scale were exact. All detected physical coordinates passed the unchanged 2e-5 ppm gate (and its SF-converted Hz equivalent). Every full-axis grid point was checked; the worst ppm grid error was `2.842170943040401e-14`.

| Archived real component | All detections | Positive | Negative | Missing / extra / duplicate / differing versus oracle | Noise sigma | Threshold |
| --- | ---: | ---: | ---: | --- | ---: | ---: |
| 1H | 662 | 65 | 597 | 0 / 0 / 0 / 0 | 5631.47920171622 | 28157.3960085811 |
| 1H_13C_HSQC | 1887 | 1020 | 867 | 0 / 0 / 0 / 0 | 559.520177868216 | 2797.60088934108 |

These counts describe the declared software extrema. They are **not counts of assigned physical peaks** or a false-discovery estimate. Global amplitude MAD can be influenced by nonstationary baseline, signal tails, artifacts and correlated samples; five times this scale does not establish a calibrated significance level. The large number of detections, including negative features, remains fully recorded rather than filtered to make agreement look better.

## All 18 deposited targets: descriptive one-to-one matching

All 18 targets matched distinct positive extrema of the archived processed **real** 1H component under the predeclared strict `<0.002 ppm` rule. Each had one eligible detection in this run. The worst discrepancy was `0.000346992059545137 ppm`. The other 644 detections (47 positive and 597 negative) remain unmatched and retained; the deposited target list does not establish whether they are signal, artifacts or noise.

This differs scientifically from S19's raw-data magnitude-window test. It uses an already processed reference component and a different predeclared descriptive matching question. It does not repair or reclassify any of S19's six failures, demonstrate automatic phase accuracy, prove all target assignments or establish sensitivity on unseen acquisitions.

| Row | Deposited ppm | Detected ppm | Absolute error ppm | Detection index (zero based) |
| --- | ---: | ---: | ---: | ---: |
| 1 | 4.5159 | 4.516222729018129 | 0.000322729018129131 | 58 |
| 2 | 4.5100 | 4.510275842132648 | 0.000275842132648307 | 45 |
| 3 | 4.5005 | 4.500760823115879 | 0.000260823115879383 | 48 |
| 4 | 4.4914 | 4.491642263224808 | 0.000242263224808248 | 44 |
| 5 | 4.4850 | 4.485298917213629 | 0.000298917213628513 | 57 |
| 6 | 4.0497 | 4.049986797196424 | 0.000286797196424438 | 61 |
| 7 | 4.0429 | 4.043246992059546 | 0.000346992059545137 | 55 |
| 8 | 4.0404 | 4.040471778179655 | 7.17781796550909e-05 | 56 |
| 9 | 4.0334 | 4.033731973042777 | 0.000331973042776212 | 54 |
| 10 | 4.0283 | 4.028578004408693 | 0.000278004408692745 | 39 |
| 11 | 4.0214 | 4.021441740146116 | 4.17401461163536e-05 | 33 |
| 12 | 4.0190 | 4.019062985391924 | 6.2985391923398e-05 | 32 |
| 13 | 4.0121 | 4.012323180255046 | 0.000223180255045641 | 38 |
| 14 | 4.0088 | 4.008755048123756 | 4.49518762435375e-05 | 37 |
| 15 | 3.9995 | 3.999636488232686 | 0.000136488232686638 | 26 |
| 16 | 3.9903 | 3.990517928341615 | 0.00021792834161527 | 40 |
| 17 | 3.9781 | 3.978227695444954 | 0.000127695444954501 | 47 |
| 18 | 3.9691 | 3.969109135553883 | 9.13555388315501e-06 | 60 |

## Signed mathematical volumes

All four original nine-by-nine boxes and all four archived HSQC components were retained. Every component's integration report had exactly the four requested unique IDs in the declared order, schema and kind. All 16 product values passed the existing `1e-10` relative gate against scalar four-corner cell summation and the original frozen oracle anchors. Every anchor was reproduced exactly. All denominators were usable; no zero/cancellation-dominated result was replaced with an absolute-error fallback. The largest relative discrepancy was `5.15246151822448e-13`.

Relative discrepancies below retain the component labels of the archived files; they do not assign those component labels an independent absorptive phase convention.

| Original region | 2rr | 2ir | 2ri | 2ii |
| --- | ---: | ---: | ---: | ---: |
| positive-1 | 1.9621506238e-15 | 4.97661262052e-13 | 2.64928723612e-14 | 9.70628744779e-15 |
| positive-2 | 0 | 5.15246151822e-13 | 2.77346776043e-14 | 2.12891481456e-13 |
| negative-1 | 1.54693366651e-16 | 5.69238155455e-16 | 0 | 2.01842337143e-16 |
| negative-2 | 1.9512907831e-15 | 1.4628171155e-14 | 3.5006071334e-14 | 4.30469088844e-14 |

These are signed mathematical rectangle integrals in positive Hz-squared coordinate measure. They are not deconvolved peak volumes, concentrations, quantitative biological validation or candidate raw-processing/reference-volume agreement.

## Analytical controls and limitations

Before the acquired invocation, 34 synthetic/analytical controls passed on this source host. They cover independent oracle agreement, signs, edges, plateau ties and diagonal connectivity, threshold equality, ambiguous one-to-one matching, exact signed cell arithmetic, unusable denominators, decoder retention and corruption, changed frozen sources and output refusal. Fault controls reject missing/extra/duplicate/reversed-sign/altered-height/altered-flag/shifted-coordinate detections. Complete integration reports reject missing, extra, duplicate, reordered and misidentified regions and wrong kind/schema while retaining all 16 gates. Lock checks reject unapproved hashes/status and mutation during execution.

An initial preparation attempt retained an execution-error receipt because installed distribution metadata was absent. The identity recorder was corrected to record that absence explicitly; no acquired sample was decoded or processed in that attempt. Independent pre-execution review also found omitted-region and lock-byte verification gaps in the validator. One bounded repair added the falsification checks above before the approved acquired invocation. Original source/lock/error receipts remain retained. Neither preparation work nor the repair altered scientific algorithms, thresholds, target/region selection or tolerances.

This run used exactly the approved protocol/manifest/tool/lock once, with empty stderr and no repeats or post-output tuning. Linux acquired-artifact replay, new independent acquisitions, physical user acceptance, biological interpretation, actual automatic-phase correctness and general quantitative performance remain unverified. **Stable 1.0 promotion remains blocked by the retained scientific and human acceptance gates.**
