# Stage 20: bounded inspection and stored-spectrum engineering checks

These are **unreleased main changes after published experimental v0.3.0b2**,
starting at `b5d4c39c61717253147ec3138fe93552a3c9f597`. No new version, tag,
download or stable support profile is supplied by this stage. The
[predeclared stage protocol](stage20-protocol.md) separates display work from the
[stored-spectrum engineering protocol](stored-analysis-protocol.md). The
**S19 scientific qualification remains FAIL** (six of 18 strict peak targets);
human/physical-machine acceptance remains **UNVERIFIED**.

## What changes for inspection

The GUI now refreshes its bounded visible region from the immutable source
samples on zoom/pan. A region that fits the display limits shows every original
sample center. Tests deliberately put positive and negative narrow peaks between
overview samples and recover their original values after zoom. Image geometry
uses actual strided sample-center spacing, with calibrated ppm/Hz coordinates and
both orientations. Overview-derived signed color limits stay fixed during
navigation. The plot footer distinguishes sampled, complete-visible and empty
regions; sampled overviews can still omit peaks.

Preparation slices before copying, avoids complete-parent coordinate arrays and
keeps the existing upper bounds of 20,000 curve samples or 1,024 samples per image
axis. A conservative estimate reserves 64 bytes per displayed sample, 64 per axis
coordinate and 64 KiB overhead, capped at the smaller of 32 MiB and the current
GUI memory control. This bounds the display's own prepared arrays/scratch work,
not resident spectrum storage, opaque Qt/native allocations, OS buffers or RSS.
It changes no saved recipe budget or scientific calculation. Repeated range
events coalesce; stale sources/components and failed session restoration cannot
replace the prior good plot. Selected-region analysis and figures retain their
existing full-sample mapping and limits.

The first focused GUI/helper cohort passed 83 tests and 14 subtests, with four
unconfigured acquired-data skips. Independent review found that the sampled
footer clipped at the minimum window size and could hide “be omitted”. A bounded
repair enabled wrapping. Twelve regression cases check sampled/complete/empty
notes with referenced ppm or unreferenced Hz at both 950×620 and 1320×840. The
affected suite then passed **95 tests and 14 subtests**, with the same four
qualified skips, in **9.76 s**. All twelve actual Qt screenshots have enough text
height for the complete warning/reference caveat and retain at least 309 pixels
of plot height. Source freezes and failures remain private under
`.artifacts/s20/viewport/`; the v1 freeze was retained separately from v2.

## Selected acquired display observation

This is a Windows source-host display check, using the unchanged previously
studied public protein result. It is separate from the earlier installed Ubuntu
b2 artifact evidence. The cached native file SHA-256 is
`91858c8b337c9ac62ebba04146ae81fa5533ec0d03b638e9b1cf4ae1fbc656ae`;
the unchanged before/after Dataset digest is
`43be3087ec487d3fea81730c16737afa9d28a6e9199acfa010c65a5b3c34908d`.
Its float32 parent has shape `(1,2048,4096)`. A Current region of
`a0=[700,900], a1=[1600,1800]` produced all 201×201 original values with zero
observed pixel-center ppm discrepancy at the inspected corners/center, fixed
signed levels and exact parent-region selection. The noisy region is not a
curated sensitivity or biological assignment case.

Five raw frame-preparation observations were retained for each endpoint. These
descriptive Windows observations are not a before/after speed comparison, GUI
render latency distribution or Linux performance acceptance:

| Endpoint | Prepared shape / source strides | Payload | Conservative estimate / limit | Tracemalloc peak range | Median preparation |
| --- | --- | ---: | ---: | ---: | ---: |
| Full overview | 512×512 / 4×8 | 1,048,576 B | 16,908,288 / 33,554,432 B | 1,072,048–1,072,128 B | 0.7365 ms |
| Full overview, 1 MiB control | 64×128 / 32×32 | 32,768 B | 602,112 / 1,048,576 B | 40,584 B | 0.3677 ms |
| Visible 201×201 | 201×201 / 1×1 | 161,604 B | 2,676,928 / 33,554,432 B | 172,440–174,552 B | 0.9368 ms |

Tracemalloc covers traced new Python/NumPy allocation during preparation after
the parent is resident. A separate fresh-process lifetime peak working set was
253,521,920 B and includes imports, the complete parent read/hash, earlier trials,
GUI and OS effects. Nothing is subtracted. It is not incremental display memory,
an RSS cap or a scaling result. Python 3.13.15, NumPy 2.5.4, PySide6 6.12.0,
pyqtgraph 0.14.0 and h5py 3.16.0 ran on Windows 11 build 26300. Source Python
identified as b2 while the existing ignored Windows native module identified as
b1; no native processing kernel was invoked for this display observation.

The exact observation JSON is
`.artifacts/s20/viewport/acquired-display-observation.json`; it retains all fifteen
raw timing/allocation records and its original source hashes. Its GUI source
preceded only the one-line footer wrapping repair; the numeric display helper
was unchanged. No later installed build or Linux/human test is inferred from
this source-host check.

## Stored-spectrum analysis result and remaining gates

The separate [complete stored-spectrum results](stored-analysis-results.md)
record one approved acquired invocation: **38/38 fixed engineering checks PASS**,
exit 0. The actual signed picker exactly matched the independent oracle for 662
1H and 1,887 HSQC extrema, retaining every sign, point, height, flag and order.
All sixteen signed rectangle-volume comparisons passed the existing `1e-10`
relative gate; the maximum discrepancy was approximately `5.15246e-13`.
Full-grid coordinate checks retained the existing `2e-5 ppm` gate.

All eighteen deposited 1H targets matched distinct positive extrema of the
archived processed **real** component under the fixed descriptive `<0.002 ppm`
rule. The other 644 detections remain retained and unclassified. This uses
previously exposed stored TopSpin data and a different question from S19's
raw-data magnitude-window target test. It does not repair S19's six failures,
measure biological false positives, establish unseen-data sensitivity or qualify
raw processing/automatic phase/concentration accuracy. The new tool and its
independent checks change no product analysis algorithms or tolerances.

## Integrated source regression checkpoint

The complete Windows source suite passed **1,062 tests and 101 subtests, with
five qualified host skips, in 57.21 s**. Existing cached public Varian and
Bruker 2D inputs were enabled; new test outputs used S20 scratch locations.
Three skips require POSIX symlink/wait4 behavior and two require directory
symlink capability unavailable on this Windows host. The matching source-test
native module and Python package both reported 0.3.0b2; native SHA-256 was
`62d0614b4d9897ee8706ebaf8e2e5c221a0347dfdc0aea0a22b4882c0773c483`.
This is a rebuilt source-host cohort, separate from the earlier display
allocation observation and from any installed Ubuntu artifact.

The first complete attempt retained seventeen failures: sixteen came from an
inaccessible default temporary directory and one from the old ignored native
b1/Python-b2 identity mismatch. Process-local temporary paths were placed inside
the workspace and the unchanged C++ source was rebuilt with the existing
compiler/helper. Restricted compiler file operations failed on both existing and
fresh caches before the ordinary authorized build succeeded with reviewed
permissions. No product algorithm, tolerance or test was changed to conceal
these environment failures. Original logs and the old binary copy are retained
under `.artifacts/s20/viewport/`.

Independent rechecks accepted the unchanged scientific tool and repaired footer.
The final integrated source freeze, commit and exact-commit Linux CI remain
separate publication checkpoints. No claim of physical researcher acceptance,
a new installed release or stable 1.0 follows from this stage.
