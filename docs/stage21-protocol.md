# Stage 21: experimental b3 release and installed workflow protocol

This finite stage prepares **0.3.0b3**, an experimental beta containing the
reviewed Stage 20 inspection work. It does not promote a stable profile or issue
an RC/1.0 release. The Stage 19 scientific qualification remains **FAIL** (six
of eighteen fixed peak targets); physical Ubuntu and independent researcher
acceptance remain **UNVERIFIED**. Earlier releases and sealed evidence are
immutable. New private evidence belongs only under `.artifacts/s21/`.

## Requirements and evidence

| ID | Requirement | Required evidence |
| --- | --- | --- |
| R1 | Coherent b3 package, native, installer and support identity; existing public API, schemas, processing and tolerances unchanged. | Identity tests; reviewed diff; Python/native installed version and public-entry-point checks. |
| R2 | Use a fresh COW Ubuntu runtime over the normally closed S19 disk. | Before/after closed-base pins; new top disk, UEFI variables, port and logs; one vCPU, 4 GiB guest RAM, host CPU affinity mask 4; graceful shutdown receipt. Never restart or append old VM/log paths. |
| R3 | Bind the exact source, build and installed binary, then test the actual public-download chain. | Exact source SHA and archive hash, exact CI asset checksums/build manifest, new outside-checkout prefix, isolated imports, no-compiler consumer install, actual release-URL downloads after reviewed prerelease publication. Local `--asset-dir` checks and actual URL checks are separate cohorts. |
| R4 | Complete the acquired installed workflow. | All fourteen fixed operations and full 2048x4096 result; Current zoom/pan refresh; bounded point/ppm ROI, signed analysis, selected PNG/SVG/PDF, explicit export losses, applied/pending distinction, session close/reopen and CLI replay. Complete numerical/axes/report checks accompany the screenshots. |
| R5 | Preserve selected saved-work compatibility. | Genuine Ubuntu b1/b2 saved cases and the unchanged archived v0.2 synthetic gate; exact session digests/reports, explicit backend-version provenance differences, and retained refusal controls. No tolerance relaxation. |
| R6 | Measure separately defined complete endpoints with fixed scientific work and resource policy. | All raw paired observations and distributions, independent untimed equality checks, exact source/binary/version/CPU/thread pins and honest RSS scope; the design below is frozen before any acquired timing. |
| R7 | Align source, published-release and tutorial status. | README, roadmap, GUI/session/tutorial and support records agree; new results bind actual evidence rather than planned checks; commits and earlier releases remain immutable. |
| R8 | Retain unresolved scientific/human gates. | Every status summary preserves S19 FAIL and human UNVERIFIED; no automated checklist is filled as a human verdict, and no general parity or biological-assignment claim is made. |

## Authorized packaging and ABI preparation, before measurements

The bounded b3 packaging candidate replaces the full `PySide6==6.12.0`
requirement with `PySide6-Essentials==6.12.0`, retaining pyqtgraph 0.14.0 and
matching shiboken. Adoption requires fresh isolated full/minimal prefixes,
`pip check`, actual distribution/module/plugin inventories, required Qt binary
identity checks, complete installed GUI/session/figure/CLI tests and the fixed
acquired R4 workflow. All lock/bootstrap/installer/CI routes must agree. Existing
full prefixes are retained; overlapping Qt files are never manually uninstalled.
Record actual deduplicated Qt-family and whole-prefix logical installed bytes
before adding test dependencies, with the counting scope and package inventory.
Later per-file captures must identify their own timepoint. Size reductions do
not establish runtime or download speed.

The required QWidget, SVG, OpenGL, PrintSupport and Test modules must continue
to work. Unused QtPdf image-reading, WebEngine and Addons functionality is outside
the processing-workbench promise. Product scientific PDF remains a Matplotlib
writer. Actual context-menu PNG/CSV/HDF5 and Matplotlib-window controls are
checked; the pre-existing full-stack pyqtgraph context-SVG `ValueError` is
retained separately and is not repaired or called a new minimal-stack failure.
Optional-version reports distinguish the PySide6 module from the installed
Essentials distribution and explicitly record absent full/Addons/WebEngine/Pdf
distributions. No processing API/schema/algorithm or numerical gate changes.

To exercise both advertised ABIs, prepare a task-private normal-GIL CPython
3.13 interpreter under `/home/validator/s21/python313/` from an exact immutable
official upstream release asset, pinning URL/source commit/size/SHA-256 and
retained runtime licenses before execution. It is a test helper, not bundled in
SpinFlow; no global interpreter/settings changes are allowed. Use a separately
built cp313 wheel and new minimal prefix for installed API, full-suite, acquired
R4 and selected saved-session checks. Preserve strict refusals and tolerances.
The paired R6 experiment remains **CPython 3.12 only**; do not add cp313 samples
to those ratios or change the 1.20 gate. Finish all downloads, builds, installs,
layout/backend probes and other active jobs before the explicit R6 GO and retain
fresh runtime/source/build/harness pins. X11 virtual-desktop diagnostics do not
qualify native Wayland, physical/GPU/compositor behavior or human acceptance.

## Frozen interactive measurement design

The before lane is exact development source
`a91b9e39f71d2fe4d1bdf1fc9b7254218a4dd494`, identifying as b2 and already
containing Stage 20. The after lane is the exact reviewed b3 build. Published b2
is **not** a performance baseline: its sampled zoom performs different work.
The same new harness executes both installed lanes using isolated `-I` workers
outside either checkout. Harness identity, source/build SHA, wheel, package,
native module and actual imported module paths/hashes are recorded per lane.

Use the unchanged known acquired Varian case, all fourteen operations in
`examples/laboratory-workflow.json`, one native FFT thread, numerical pool
environment variables set to one and the original precision/quadrature/padding
and 1 GiB recipe working-array policy. The actual acquisition header declares
big-endian float32 raw storage; the existing importer declares source shape
`(4,166,1500)`, float32, with both complex axes. The fourteen existing operations
preserve that dtype and end at `(1,2048,4096)` float32 after the recipe's two
explicit imaginary-discard operations. Header-only inspection and prior
installed operation records corroborate these distinctions; each new installed
worker must record and verify its actual source, intermediate provenance and
final dtypes. No processed S20 display fixture substitutes for the raw input and
no benchmark cast is permitted. One guest CPU and the same host affinity are
used in both lanes. Input hashes are checked before and after; prehashing warms
the filesystem cache. No fitting, alignment, normalization, precision change,
implicit crop, channel loss, dropped outlier or retry substitution is allowed.

The fixed Qt workbench is 1320x840 on the same xcb virtual desktop. Each fresh
worker preloads its source and full ordered recipe outside timed warm/interactive
endpoints, shows the window and drains setup events. There are **five mandatory
paired trials**, alternating AB, BA, AB, BA, AB, with one fresh worker per lane
and pair. Each worker performs and retains one labelled untimed warmup of every
warm/interactive endpoint **in that same worker process**, then restores the
original loaded-source/overview state and performs one measured full sequence.
Thus all ten measured workers are actually warmed; a separate earlier process
does not warm a later worker. Warmup observations are retained separately and
excluded by the preregistered role, never selected by measured results. A worker
receives the same fixed recipe, ranges and trial inputs in either lane.

Endpoints are separate:

1. **Fresh native CLI:** existing installed acceptance runner boundaries, from
   process spawn to exit, including startup/read/full processing/hashes and
   native write/close. Use its five mandatory fresh trials per lane independently
   of Qt timings. These CLI cohorts are descriptive, are not alternating pairs
   and do not receive the paired 1.20 gate. No cold-filesystem claim.
2. **Warm full processing:** preloaded immutable acquisition through completed
   `run_operations` for all fourteen operations, including normal public engine
   validation/provenance. Imports/read and untimed result comparisons excluded.
3. **Full Process to painted Current:** clear the actual GUI recipe cache before
   every trial; begin immediately before the real Process action and end after
   the asynchronous worker has finished, Current is installed, queued viewport
   refresh has completed, and the plot viewport has received its resulting Qt
   paint event. Include normal validation, full processing, controller state,
   frame preparation and Qt paint; exclude subsequent screenshot encoding and
   equality checks. No precomputed result or prefix/final cache shortcut.
4. **Zoom/pan to painted viewport:** start from the applied full overview and set
   the same calibrated ranges corresponding to inclusive parent point boxes
   `a0=[700,900], a1=[1600,1800]` (zoom), then
   `a0=[720,920], a1=[1620,1820]` (pan), including normal coalesced range updates,
   preparation and resulting Qt viewport paint. Record zoom and pan separately;
   do not time screenshot/file encoding. The visible boxes must show every
   original sample center and retain fixed signed color limits.

A 30-second event-loop watchdog fails stalled/incomplete Qt endpoints. The
controller independently limits each **whole worker to 180 seconds**, including
imports, input load, warmups, synchronous processing, comparisons, output writes
and final pinning. Each **whole cross-lane comparison is limited to 60 seconds**.
Those generous limits are fixed before acquired timings. Each child starts a new
owned process session; on expiry only that owned group receives TERM, with five
seconds to reap, then KILL and a further five-second bounded reap. A child that
cannot be reaped is explicitly incomplete with unavailable RSS, never a pass.
Regular-file stdout/stderr, exit/timeout/signals, process identity and available
wait4 usage are retained; no retry replaces the failed trial.

An exclusively created per-worker JSON-lines journal flushes and fsyncs each
completed raw warmup/measured elapsed observation **before** subsequent
validation, then appends validation/failure/location and lifetime-RSS records.
The controller also journals each child/comparison receipt and complete pair.
A late validation, write, import-pin failure or timeout therefore retains earlier
raw measurements. Incomplete cohorts cannot produce passing distributions.

Clock completion
must require both controller/viewport completion and the resulting paint event.
An event filter observes paint dispatch **before** `paintEvent`; completion is
therefore recorded only in a queued callback after that dispatch returns, with
the expected state/frame still current and no pending viewport update. This is
Qt/raster paint completion, not GPU/compositor or physical-display latency. An
arbitrary sleep or a successful `process_now()` alone is insufficient. Record
actual frame ranges, shape, strides, footer and paint count. Semantic paint
completion requires the exact requested calibrated ranges, exact parent slices
`(700:901:1,1600:1801:1)` or `(720:921:1,1620:1821:1)`, unit strides and the same
applied Current object. Untimed checks compare all expected parent values and
coordinates, and the applied Current identity/digest before/after each move.
After full Process the actual GUI cache must report `last_reused_steps == 0`.
Both engine and GUI require exactly fourteen appended executed provenance
entries with the original resolved operations/defaults, native backend/one
thread, float32 and the fixed input/output shape trajectory; the two explicit
quadrature discards must retain the declared channels. Complete output
array, dtype, axes, quadratures, numeric digest and provenance checks occur
**outside** timing. Across versions, only explicit backend software-version
provenance fields may differ; retain both complete metadata records and compare
the remainder. Each lane's GUI and engine must agree exactly. Zoom/pan values
and coordinates must match the immutable applied parent; parent digest must not
change.

For every endpoint retain all raw measurements, minimum, maximum, median and
inclusive-quartile IQR. For paired endpoints retain each B/A ratio and its
distribution. The preregistered engineering regression gate is median paired
elapsed ratio **B/A <= 1.20** for each identical-work endpoint. This is a
no-regression gate for the four paired warm/interactive measurements on this
VM/case (warm full processing, full Process, zoom and pan), not a speedup or physical-user performance
claim. A failed gate remains failed with all observations; investigate rather
than change its threshold or exclude trials. No NMRPipe speed-parity claim is
made.

Controller `wait4` lifetime high-water RSS is recorded for each fresh worker
when available. It can include the controller's inherited pre-exec floor and all
setup and prior work inside that worker; report that floor separately and
subtract nothing. Current `/proc` RSS may be recorded separately. Neither metric
is incremental endpoint memory, an OS cap or a flat-scaling claim. Working-array
and display estimates retain their existing narrower meanings.

Before the repaired harness is accepted, synthetic controls must reject a
same-shaped wrong region, wrong calibrated ranges, changed Current identity or
digest while raw source stays unchanged, missing/wrong-dtype/wrong-shape or
wrong-parameter history, wrong discard channels and a cache hit. Actual stalled
worker/comparison children must terminate/reap within their deliberately short
test-only deadlines and retain logs. A synthetic late failure must retain both
earlier warmup/measured raw observations and explicit failure location without a
completed-worker verdict. These controls alter no acquired work or 1.20 gate.

## Execution and release order

Protocol/harness inspection and new-runtime pins precede the coordinator's
explicit GO for acquired benchmarks. Source and wheel preparation are permitted
before that GO. First verify a private distinctly identified b3 build, then
freeze the integrated source for independent Standard review. Fix accepted
findings within the shared two-round repair budget, rerun affected checks and
bind exact-commit CI assets. Before publication, use those assets for the
installed full workflow and compatibility checks. After the reviewed
experimental prerelease is published, check a fresh install through its actual
public URLs and repeat the affected installed workflow checks. Publication alone
does not establish the download-chain check. Close the new VM normally and pin
its final evidence; all old seals/logs remain read-only.

The human handoff uses the matching tagged **source archive** for the existing
standard-library runner, recipe and hash-pinned fetch helper, plus the installed
release prefix. Wheels and Python sdists alone do not contain that runner.
Return a blank manual checklist and concise launch instructions; a user's later
physical-machine observations are a separate future record.
