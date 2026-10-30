# Final performance candidate v3 — actual independent consumers

Producer observation: **PASS for this single public case's declared application-reopening checks.** Independent release adjudication remains separate. The final candidate source archive is `60d732c38870695f3710f842bb7c4b10b117f72044fb8f8ed941b0ad2304ba71`; wheel `25b9c4cac4faec7ae225ac34fea5d10a6f0c71ab829f39cf4194f14d295b2920`. No product code, vendor binaries, spectrum samples, phase, reference, alignment, normalization, or host settings were changed during these checks.

## Actual applications and timeline

The pinned **NMRDraw 13.0 Rev2026.072.12.03 64-bit** opened the new v3 NMRPipe export. Its actual full view and five declared regions were captured and inspected. It was closed, freshly launched on the identical file, and the reopened full view was inspected before closing again. **Sparky 3.115** opened the new v3 UCSF export through its actual application reader; its full view and all five regions were captured and inspected on both initial opening and close-without-save/reopening. The application's built-in Python shell controlled only file opening, contour settings and viewport geometry.

Guest UTC timestamps from existing launch/capture records (not claims about exact human observation times):

| Event | UTC on 30 October 2026 |
| --- | --- |
| NMRDraw launch record written | 12:06:37.655423014 |
| NMRDraw first confirmed full-view capture | 12:06:55.614391425 |
| NMRDraw reopened full-view capture | 12:09:13.405892815 |
| Sparky 3.115 version-window capture | 12:09:31.585280430 |
| Sparky first full-spectrum capture | 12:10:24.171152641 |
| Sparky reopened full-spectrum capture | 12:11:17.107757905 |
| Fresh all-consumers-closed verification | 12:12:26.125285490 |

The new private display used the existing vendor font-path fix. Capture Python was 3.12.3; Ubuntu package versions were Xvfb `2:21.1.12-1ubuntu1.9`, Openbox `3.6.1-12build5`, xdotool `1:3.20160805.1-5build1`, and libX11 `2:1.8.7-1ubuntu0.1`. The first Sparky launch omitted DISPLAY and exited without opening a file; its error log and blank screenshot were retained. It was relaunched with the correct private display and all successful observations came from that instance. No alternative binary or library workaround was used.

## Immutable inputs and runtime

The new candidate files and retained reference files were hashed before opening, during both application checks, after reopen, and after all consumers closed. All four hashes remained identical:

| File | SHA256 |
| --- | --- |
| v3 `candidate.ft2` | `442556865eb8d5218c14ddc00c3b5d64162f136349678b43e4a9d7084f2c1b04` |
| v3 `candidate.ucsf` | `c3fc007120b6668b5b3ffbd30c9ad585a36103073f967f2ae6f8c7e158539f7a` |
| retained `S10.pipe` | `46744e0fb28e7b9f2fc2c5d81772ac9ce748b9dae1bf52d96aac6bc257199dfa` |
| retained `reference.ucsf` | `eae3b5b3b8a69fc3dbad78de3139c0ece4843e4e590d698b65781f7086307c7e` |

The candidate files are new v3 exports with their own identities, distinct from the references. All **504 prior executable/library/font pins** matched before, during and after the checks. All **85 observed loaded-file mappings** across actual NMRDraw and Sparky process captures matched either those pins or the five additional Sparky support modules previously verified against their original archive (`25dc377914a028e330109003039bf8cd6693c0a2561cd48b0b53e6c2a7abf856`). `runtime-verification.json` records the comparisons. The old S15 consumer record retains its original SHA256 `b4c6b54ec45352974852e1db4dcdd1f4e1ae9540e019d909d4848ed5bae31cf7`.

The copied historical `reference-runtime-pin.json` still contains its original early `NMRDraw_blank_window: UNVERIFIED` prerequisite status. That historical status was resolved by S15's successful font-path addendum and actual views, and is superseded for this run by the new v3 full/ROI/reopen screenshots plus actual-process runtime verification. The historical file itself was not rewritten.

## Views, signs and absolute coordinates

NMRDraw displays the final real matrix as 4096 horizontal × 2048 vertical points, carbon-13 horizontally and nitrogen-15 vertically. Carbon ppm decreases left to right; nitrogen ppm increases downward. Phasing is Off. Both signs are enabled with first magnitude 30000, multiplier 1.2 and 20 levels of each sign. Positive contours are cyan/blue; negative colors are red/orange. Exact inclusive one-based point limits were entered in Pts units:

| Region | Horizontal points | Vertical points |
| --- | --- | --- |
| T49 | 1993–2004 | 1317–1335 |
| T11 | 1997–2009 | 1285–1303 |
| G14 | 2033–2045 | 1294–1315 |
| E15 | 2078–2088 | 1005–1026 |
| W43 | 2009–2020 | 934–953 |

All five new NMRDraw regions were inspected beside the retained reference screenshots. Peak centers, positive sign, shape and contour counts agree visually: strong T49/E15, weak T11/W43 and intermediate G14. The limit dialog partly overlaps an upper corner without hiding the relevant centers. No viewport alignment or calibration adjustment was applied.

Sparky's actual API reports size `(2048,4096)`, nuclei `(N15,C13)`, display axis order `(1,0)` and scale offset `(0,0)`. These nuclei correspond to the retained reference's `(15N,13C)`. Its positive red and negative green contour settings read back as `+30000` and `-30000`, factor 1.2, 20 levels. All five initial and reopened positive-ROI views match the retained reference shapes, sign and approximate absolute positions. The full views show the same overall peak distribution. Reference views were retained from S15 after unchanged reference-file and full runtime/font verification; they were re-inspected locally during this run, not newly recaptured.

Actual Sparky spectrum endpoints, nitrogen axis first then carbon:

```
minimum (65.208784799378549, -143.80335578996264)
maximum (174.8447745507687,   253.9004751626006)
```

Each endpoint on each axis differs from the retained reference's actual API value by exactly **1.52587890625e-5 ppm**, below the unchanged **2e-5 ppm** gate. Initial and reopened candidate API metadata are exactly equal. `api-coordinate-corroboration.json` records source hashes and calculations. This is endpoint corroboration; the separately executed independent `ucsfdata` full-matrix/full-grid decoder evidence belongs to the science record. Rounded pixel labels cannot establish that ppm tolerance. ROI API files retain requested and actual viewport limits; fractional-pixel viewport snapping, crosshair positions, interpolation and color choices are display properties, not sample or calibration changes.

## Closure, scope and publication

Both applications were closed without saving the project. The inspected `72-all-consumers-closed.png` shows a blank private desktop; `after-closed.json` reports **zero matching consumer processes**, unchanged four input files, and unchanged 504 pins. Root and evidence owner were notified immediately; all benchmark/science/profile/Linux-suite guest work had completed before this GUI session began. No further guest writes were performed after closure. Subsequent timestamp/version retrieval was read-only, and summarization/publication ran locally on Windows.

`docs/evidence/performance-consumers/index.json` hashes **21 unmodified new screenshots**, with retained reference image identities listed separately. The public folder also contains actual Sparky API reports, coordinate corroboration and this record. No acquisition files or vendor software were copied into public evidence. New automation, logs, viewport/API records and runtime checks remain under `.artifacts/performance/consumers/v3`; prior S15 seals and all earlier performance candidates were preserved.

This is the previously exposed cached public protein case, not a new blinded holdout. The unresolved NCa/NCO label discrepancy and unknown protein identity remain unchanged. Automated observation of pinned applications on a private virtual desktop establishes this scoped reopening check; it does not establish general format compatibility, human usability, Wayland/GPU behavior or exact sample equality from pixels.
