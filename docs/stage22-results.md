# Stage 22: an acquired-data tutorial and exact session replay

The [ASU calibration walkthrough](tutorials/asu-calibration.md) now has a
completed installed execution and desktop/session check on **Ubuntu 24.04.5,
CPython 3.12.3 and public experimental 0.3.0b3**. This adds a reproducible
illustration and matching example files. It changes no processing algorithm,
public API, schema, precision, product version or released binary.

The checks describe arithmetic and persistence, not scientific accuracy.
Scientific phase acceptance remains **false** and independent physical-human
acceptance remains **UNVERIFIED**. S19 remains **FAIL on six of eighteen
targets**. The released validation tool's **S21 R6 whole-process-group timeout
guard remains FAIL**, with repair pending authorization and independent review;
see the [current qualification](stage21-results.md#current-qualification--8-november-2026-j21-r6-timeout-01).

## Reproduce the illustration

Use the guide and [matching examples](../examples/asu-calibration/README.md)
from one source revision. The examples' `SHA256SUMS` checks all ten runnable
assets; the [scalar evidence](evidence/stage22-tutorial.json) binds all twelve
guide/example files. The immutable
[b3 release](https://github.com/ben-shin/spinflow/releases/tag/v0.3.0b3)
supplies the installed application. Its tagged source supplies the existing
ASU downloader/case/protocol, rather than these later tutorial files. The
application source remains
[`7598ae5`](https://github.com/ben-shin/spinflow/tree/7598ae53267d743fbd46ba708ed85f5b3478cbeb).
No release tag or asset was changed.

The [primary ASU acquisition](https://raw.githubusercontent.com/CHM343/NMR/af3b9c2c4642e63e379bc349cb40e1fe8c5730e6/Data/MeOD_25C_Temp_Cal_Bruker400.zip)
is pinned to commit `af3b9c2c4642e63e379bc349cb40e1fe8c5730e6`,
1,060,855 bytes and SHA256
`38c8d260169d42f0d15ab5c7b9ce7084735419688c9ea66e76dc34fa6b356094`.
Only its pinned fid/acqus pair is extracted. Acquired arrays, figures,
sessions, private execution helpers and vendor software are not distributed
with this tutorial. See the earlier [import/filter provenance and limits](asu-import-filter-results.md).

## Actual checks on 8 November 2026

| Check | Recorded outcome |
| --- | --- |
| Packaged normal flow | Literal whole `/bin/sh` files 00, 10, 20 and 30 each ran once and exited zero; `/bin/sh` resolved to Dash. RUN and example paths contained spaces, with setup invoked outside the example directory. |
| Processing and analysis | Unphased and phased arrays, axes, full metadata/provenance and phase/analysis JSON matched the earlier fixed illustration exactly. Both float64 quadratures were retained, shape `(2,65536)`. |
| Actual CLI replay | After producing the new CLI result, all eleven exact checks passed: dtype, shape, quadratures, every sample, digest, serialized axes, metadata, provenance, complete metadata, unchanged inputs and installed origins. |
| Desktop persistence | Two fresh xcb GUI processes opened the fixed session, viewed Source/Current real/imaginary, saved/closed, reopened and exported a runnable workflow. Its actual CLI replay passed the same eleven checks. Five literal operations and the three cached reports remained exact. |
| Separate diagnostic branch | Constructed refusal-entry control 31 passed four-operation unphased session/CLI replay and eleven exact checks. **The actual ASU phase fit did not refuse**; this is a labelled control, not an acquired refusal result. |
| Focused non-NMR controls | Asset discovery with quoted paths/inherited CDPATH; missing PY; missing SF; existing RUN with unchanged sentinel; and invalid continuation. Expected nonzero refusals were retained. |
| Evidence preservation | 115 copied original files matched guest bytes: 25 controller, 58 normal and 32 control files. The full result manifest has 138 members. Earlier 86 Stage1/Stage2/GUI files and all 28 installed product/resource files remained unchanged. |

All eleven validation-owned process groups were reaped, with no timeout or live
descendant remaining. The executor was the separately reviewed private S22
helper; the failed released S21 guard was neither reused nor repaired. These
normal exits do not establish that all timeout/failure paths were exercised
by the acquired case.

The VM had one vCPU, 4 GiB, guest affinity CPU0 and numerical pools of one.
The 64 MiB setting bounds the processing working-array estimate, not process
RSS. Desktop checks used a 1320×840 normal window on a private 3200×2000 xcb
display at DPR1. Whole-shell wait4 peaks include descendants the shell waited
for; they are not aggregate or per-command RAM measurements. Elapsed times are
diagnostic observations, **not a benchmark or speed claim**. The sealed S21
backing disk and protected log/UEFI identities remained unchanged. This tutorial
case was not repeated on CPython 3.13; its separate application-support evidence
remains in Stage21.

## Interpretation and limits

The fixed workflow retains both channels through recorded digital-filter
correction, exponential 0.5 Hz broadening, zero-fill to 65536, FFT and literal
zero-order phase. The observed fit used p0 `61.879063537629605`, p1 `-0.0`
and pivot `32767.5`; all four phase-review flags remain. These values are an
observation, not a target for refitting or a scientifically accepted phase.

Source retains its actual time axis, displayed in milliseconds. Current uses
relative mathematical Hz because the ppm reference remains unknown. The fixed
both-sign search found **six positive candidates and no negative candidates**;
it supplies no acquired negative-signal validation. Six proposed ±2 Hz regions
have signed relative intensity·Hz areas with no baseline correction or
deconvolution. Their row flags require review; broader limitations are also in
the JSON report. All six records remain, while the PNG shows four labels.
PNG, SVG and PDF output do not establish assignments, concentration or peak/
volume accuracy.

The desktop check used installed Workbench controller actions and actual view
controls. Ten screenshots and both complete observer reports are retained
privately. Initial-scroll screenshots do not show every offscreen report
table/control; mouse/menu coverage and independent human suitability were not
established. Source/Current overviews were sampled at strides two/four, with
every displayed value and coordinate checked against its parent slices; the
complete saved arrays and reports remained exact. Native Wayland, physical
HiDPI/GPU behavior, original-program spectral parity and the missing original
spectral-processing first-laboratory gate remain unverified.

The scalar evidence binds the original fourteen-asset audit, GUI source/results,
packaged source/bindings and result freeze. The matching Python helper bytes
retain historical proposal comments; this dated execution record distinguishes
their actual normal run from the separately constructed diagnostic control.
