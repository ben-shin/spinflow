# First laboratory-workflow comparison: preregistration

**PREDECLARED — PENDING EXECUTION LOCK. No acceptance PASS is claimed.**

The user selected the cached public Agilent/Varian `cpjdcptan2d` protein example and its original conversion and processing scripts. This case was already used in earlier SpinFlow comparisons against archived references: it is **not a new independent holdout or a blinded dataset**. The new comparison rules and deterministic weak/strong classification were fixed before the new NMRPipe 13 and imaginary-discard candidate comparisons. No private laboratory acquisition or researcher observation session is represented here.

The official plot labels the spectrum protein NCa; the original box-file comment says NCO. The protein name is unknown and the acquisition `samplename` is empty. Source labels such as T49 are retained as labels, not certified biological assignments. Scope is one exact float32 States acquisition and one recipe, not a complete vendor profile or general NMRPipe equivalence.

## Frozen inputs and authority

[lab_workflow_case.json](../tests/fixtures/lab_workflow_case.json) records source URLs, byte lengths, SHA-256 values, reference-only ROIs and the fixed scientific contract. Raw data and third-party binaries are not included in the repository. The six historical archives and selected members matched all 40 checks during intake; the selected three 2D archives and their members matched 21 checks. This establishes cached byte identity, not new NMRPipe execution.

Original sources: [conversion example](https://nmrglue.readthedocs.io/en/latest/examples/agilent2pipe_2d.html), [processing example](https://nmrglue.readthedocs.io/en/latest/examples/process_pipe_2d.html), [protein plot](https://nmrglue.readthedocs.io/en/latest/examples/plot_2d_spectrum.html). No explicit acquisition-data redistribution permission was located; the nmrglue code license does not establish data rights.

The private preregistration baselines are immutable and identified in the fixture: scientific protocol2 `aaacaac19be3ab7a5b906b3d4fb3498e3c397cd226b49bd281122bc62508b455`, inputs/ROI record `153c3d0e2bcbbdc39bf757794be9594491e86384db734202a47a87fa0f59edbf`, and DI contract `02fa4b59bae33bbaea576601545301323b454284709e749802fc1071dc4572a1`. This public document removes private intake details and superseded conditional wording without changing those scientific rules.

The original scripts call `/bin/csh`, `var2pipe` and `nmrPipe`; neither sources another script. Run their exact bytes in fresh private directories with preserved relative paths. Feed the freshly converted FID into the processing script; do not substitute the archived processed/reference FID or overwrite it. Preserve raw source bytes. Record environment initialization and all actual executable/runtime dependencies.

## Fixed mathematics and recipe

Canonical physical axes are `(a0=indirect 15N,a1=direct 13C)` and quadratures `[RR,IR,RI,II]`, with bit0 belonging to a0. Raw shape is four channels by166 by1500; output is real2048 by4096. Samples remain float32. Original even/odd States traces are retained. NMRPipe channels convert to the native convention with fixed factors `[1,-1,-1,1]`; after direct imaginary discard the factors are `[1,-1]`, and after both discards `[1]`. This is a case-specific convention, never selected by correlation with candidate peaks.

Native FFT is forward unscaled, negative exponent and FFT-centered. The reference convention is positive exponent with the fixed independent-axis conjugation above. Physical frequency coordinates are reflected with explicit `frequency_scale=-1` on both axes without reordering samples. Direct calibration is SW50000Hz, OBS125.691MHz, CAR55ppm; indirect calibration is SW5555.556Hz, OBS50.648MHz, CAR120ppm. Every absolute ppm coordinate is checked, not only relative peak spacing.

Original direct processing is sine off0.35/end0.98/power1/first-point1, ZF-auto expected4096, FT-auto, PS(-29,0), imaginary discard. Then TP, indirect sine off0.35/end0.9/power1/first-point0.5, ZF2048, FT-auto, PS(0,0), imaginary discard, and TP back. Native phase is (+29,0) direct and (0,0) indirect with zero-based pivot0 and denominatorN. Phase follows the original script; no automatic phase fitting is used.

The explicit native `discard_imaginary(axis)` contract removes the axis's quadrature bit and retains precisely old channels whose removed bit is zero. S04 retains old[0,1]; S09 retains old[0]. It changes current axis encoding to real and removes only the discarded orientation entry, preserving signed samples, dtype, coordinates, acquisition metadata and explicit loss provenance. The planner must evolve the component count and retain earlier larger memory requirements. Merely projecting all-real at final export leaves unequal intermediate work and does not qualify for a fair speed comparison.

NMRPipe FT-auto behavior must be resolved from actual version13 help, acquisition headers and independent analytic impulse/complex-tone controls **before case outputs**. The [legacy FT](https://spin.niddk.nih.gov/bax-apps/NMRPipe/ref/nmrpipe/ft.html), [TP](https://spin.niddk.nih.gov/NMRPipe/ref/nmrpipe/tp.html) and [PS](https://spin.niddk.nih.gov/NMRPipe/ref/nmrpipe/ps.html) documentation guides interpretation but does not establish installed-version behavior. Any unexpected semantics require a documented additive version-semantics lock before new comparisons; never choose a shift, sign, scale or phase to improve a match.

## Checkpoints and gates

Checkpoint sizes are canonical `(a0,a1)`, independent of file-axis order.

| Stage | Boundary | Size | Retained channels |
|---|---|---|---|
| S00 | conversion |166x1500|RR,IR,RI,II|
| S01 | direct window |166x1500|RR,IR,RI,II|
| S02 | direct zero-fill |166x4096|RR,IR,RI,II|
| S03 | direct FFT |166x4096|RR,IR,RI,II|
| S04 | direct phase and discard |166x4096|RR,IR|
| S05 | transpose |166x4096|RR,IR|
| S06 | indirect window |166x4096|RR,IR|
| S07 | indirect zero-fill |2048x4096|RR,IR|
| S08 | indirect FFT |2048x4096|RR,IR|
| S09 | indirect phase and discard |2048x4096|RR|
| S10 | final transpose |2048x4096|RR|

S03P is a separate diagnostic branch applying the same direct phase without discard; its real projection must equal S04. It is not fed into a modified processing pipeline. Every available intermediate quadrature is compared. Missing checkpoints remain unverified. Instrumented prefixes must reproduce the uninstrumented original result. Instrumentation is excluded from both benchmark lanes.

Existing gates are unchanged: S00 acquired quadratures match exactly; isolated FFT transitions have relative maximum error<=2e-6; processed boundaries and full final arrays<=2e-5; signed ROI sums<=2e-5; global and ROI peak-bin discrepancy0; every absolute ppm coordinate error<=2e-5ppm. Relative array error is `max(abs(candidate-reference))/max(abs(reference))`. ROI error is `abs(sum(candidate)-sum(reference))/abs(sum(reference))`, accumulated in float64. Undefined/zero or scientifically near-zero denominators cannot pass by division tricks; report them explicitly. Absolute and relative L2 errors are supplementary diagnostics.

No alignment, interpolation, normalization, conjugation search, reference offset, phase refit or tolerance relaxation is permitted. Independent byte decoders must not call production/nmrglue comparison helpers. Report shapes, channels, signed extrema, phase metadata and physical calibration as well as numeric errors.

## Fixed weak and strong regions

All five original inclusive, zero-based boxes remain mandatory. Before inspecting new candidate outputs, a reference-only decoder ranked their positive maxima with lexical tie-breaking: lowest two weak, highest two strong, middle intermediate. This is relative strength among these boxes, not a sensitivity claim. Absolute ppm bounds, signed sums and selected slice coordinates are frozen in the fixture.

| Label | y bounds | x bounds | Class |
|---|---|---|---|
| T49 |1316..1334|1992..2003|strong|
| T11 |1284..1302|1996..2008|weak|
| G14 |1293..1314|2032..2044|intermediate|
| E15 |1004..1025|2077..2087|strong|
| W43 |933..952|2008..2019|weak|

Expected signal sign is positive. Diagnostic contours use both signs at magnitudes30000*1.2^k for k0..19. The prior six-MAD picking threshold and four-point separation remain unchanged auxiliary analysis. Fixed half-open noise rectangles are y512:768/x2800:3072 and y1280:1536/x2800:3072; their signal-free interpretation is an assumption, not certified truth. Report local residuals, signed sums and extrema separately. Near-noise sign differences are reported with intensity rather than converted into a new strict sign gate.

## Software lock and independent reopening

The [IBBR distribution](https://www.ibbr.umd.edu/nmrpipe/install) lists NMRPipe13.0 revision2026.072.12.03 and script update20May2026. Pin actual archives, invoked binaries/scripts/plugins, displayed revision, dynamic libraries, shell environment and captured help. NMRPipe cannot be redistributed without author permission. Only required archives NMRPipeX.tZ, s.tZ, install.com and binval.com are needed for this script; optional plugins are outside scope. The two inspected installer-text hashes are in the fixture; no installer execution is implied by inspection.

Open exact exported files in actual pinned NMRDraw and UCSF Sparky, recording hashes, versions, opening actions, full extent and all declared weak/strong zooms, absolute coordinate checks, axis/nucleus labels and signed contours. Close and reopen them. No consumer rephasing, re-referencing or intensity normalization is allowed. Header/payload checks supplement this; codec readback alone does not count as end-user application reopening.

[Original UCSF Sparky3.115](https://www.cgl.ucsf.edu/home/sparky/) provides `bin/sparky`, `bin/pipe2ucsf` and `bin/ucsfdata` in the linked Linux64 archive. Its [license](https://www.cgl.ucsf.edu/home/sparky/sparky-license.html) allows use subject to notices/acknowledgment, without academic eligibility assertions. It is old and unmaintained; actual runtime compatibility must be tested. NMRDraw requires a working Linux/X11 environment. Software availability and successful reopening are pending, not assumed.

## Equal end-to-end benchmarks

Measure raw acquisition to final NMRPipe and raw acquisition to final UCSF as separate endpoints. Include process startup, input conversion/read, processing and required output conversion/write through close; use direct final output without unnecessary HDF5 staging. The reference UCSF lane additionally runs pinned Sparky `pipe2ucsf`; the original example scripts themselves do not produce UCSF. Both lanes must match scientific steps, retained quadratures, dimensions, layout and float32 precision. Unavoidable extra work must be disclosed and invalidates a fair parity claim if unequal.

Use the same hardware, OS/environment and storage class; pin the entire process tree to the same single physical core and verify internal pools each use one thread. Per-process thread limits alone do not constrain concurrent NMRPipe pipeline stages. Record hardware, power state, virtualization, memory, filesystem, affinity and effective pools. Hardware-accelerated virtualization remains labelled virtualized; software-emulation timings cannot be portrayed as bare-metal performance.

After one declared warmup per lane, run at least seven measured pairs with alternating AB/BA order fixed in the execution lock, fresh output directories, and output equivalence checks outside timing. Retain all observations, failures and slow runs. Label cache state warm/cache-uncontrolled unless an equally applied cold-cache procedure was declared. No uncontrolled system-cache purge is required. Report median, spread and paired ratios; a single case cannot establish a suite-wide speed claim.

Collect externally measured wall time, total descendant user+system CPU, maximum simultaneous summed process-tree RSS with interval/shared-page caveat, logical and physical I/O separately, peak temporary disk usage and final output size. Do not sum independent process RSS high-water marks. Record cgroup memory peak separately if available. Validate descendant accounting with a multi-process control. CLI processing-only elapsed fields exclude input loading and cannot serve as end-to-end timings. State final hash/verification overhead consistently.

## Execution and publication boundary

Before acquisition comparison, pass header-only planning and GUI-budget prerequisites, analytic DI tests and version-semantics controls, then seal protocol/fixture/harness hashes with actual binaries/runtime/hardware and trial schedule. Planning must read bounded metadata only, not numeric payloads or whole-payload hashes. GUI import, preview, processing and relevant export paths must consistently honor the selected budget. Array budgets are not process RSS limits. Do not enlarge datasets before these prerequisites pass.

The comparison lock also binds the candidate checkpoint driver and direct-output recipe. It uses schema1, status `SEALED`, SHA-256 fields for protocol, fixture, comparator, candidate driver and recipe, plus nonempty hashed-file evidence lists for candidate source/binaries, reference version/binaries, consumer binaries, dependencies, original scripts, captured help, analytic FT-auto controls, header/GUI prerequisites, hardware controls and trial schedule. Root acceptance of that evidence is required before sealing. Consumer startup/reopening is a separate outstanding gate; recording a binary hash is not a successful reopen. Sampler-control and benchmark-implementation evidence may be added in a versioned lock before timing, without changing the scientific rules or schedule. Missing acquired-case evidence remains UNVERIFIED.

For each endpoint the exact measured schedule is AB,BA,AB,BA,AB,BA,AB, where A is the original NMRPipe13 script pipeline (plus pipe2ucsf for the UCSF endpoint), and B is SpinFlow. One warmup per lane precedes each endpoint's measured pairs. A one-vCPU guest with host affinity to one logical CPU constrains total pipeline compute, but activity on its physical core's SMT sibling remains a disclosed contention limitation unless separately isolated.

Publish PASS/FAIL/UNVERIFIED per requirement with evidence paths, all material failures and limitations. Add compatibility entries only for actual tested flags/profile/dimension/dtype/revision and distinguish native from legacy-backed execution. No absent reference run, real-application reopening, or fair benchmark is a PASS. No general protein phasing, quantitative concentration, private laboratory usability or broad NMRPipe compatibility claim follows from this case.
