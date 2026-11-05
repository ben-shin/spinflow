# Stage 20 protocol: bounded scientific inspection

This protocol is fixed before changing the GUI display. The starting revision is
`b5d4c39c61717253147ec3138fe93552a3c9f597`, after published experimental
`v0.3.0b2`. Work described here is unreleased main until separately built and
published. Human Ubuntu acceptance remains pending.

## Requirements and checks

- **R1 — Samples and coordinates.** A zoomed region that fits the display limits
  shows every original signed sample. Test narrow positive and negative peaks
  omitted by overview striding, exact bounded values, ppm/Hz/time units, both
  coordinate orientations, singleton axes and image pixel-center positions.
  Keep overview-derived intensity levels fixed while panning and zooming.
- **R2 — Bounded display work.** Retain the existing upper bounds of 20,000 curve
  samples and 1,024 samples per image axis. Use a conservative declared estimate
  for display working arrays within `min(32 MiB, current GUI memory control)`.
  Slice before copying/converting, avoid complete-parent coordinate arrays, and
  measure selected real-data preparation with tracemalloc and fresh-process peak
  memory separately. The estimate excludes the resident immutable spectrum and
  opaque Qt/native/OS allocations; it is neither an RSS bound nor a new processing
  memory policy. Overview striding can still omit narrow peaks; disclose it.
- **R3 — State safety.** Coalesce repeated range changes and use the latest view.
  Test pan without range reset, invalid/nonintersecting/huge ranges, component and
  source switches, Original/Preview/Current behavior, rollback, region selection
  and saved workflows. A queued update must not repaint a stale dataset.
- **R4 — Scientific compatibility.** Preserve public Python interfaces, numeric
  and saved-artifact schemas, operation conventions, tolerances, precision,
  quadratures, padding, provenance and analysis/figure limits. No processing,
  peak-picking, volume or phasing algorithm is changed for display work.
- **R5 — Separate scientific evidence.** Any new post-failure real-data tool uses
  its own frozen expected inputs and criteria. The original Stage 19 holdout
  remains **FAIL** (six of 18 targets); explanatory overlap diagnostics do not
  replace it or qualify an independent holdout.
- **R6 — Identity and limits.** Record exact source, Python/native/dependency and
  OS identities and test outcomes. Distinguish source-host Qt checks from Linux
  CI and actual installed-release evidence. Do not claim physical-machine,
  researcher, blind-science or stable 1.0 acceptance from automated checks.
- **R7 — Publication.** Keep stage notes, review the frozen integrated changes,
  then commit and push only the supported result. Prior releases and sealed
  evidence remain unchanged. New scratch evidence lives under `.artifacts/s20/`.

Independent review covers scientific correctness, failure/state robustness and
user inspection. Two consolidated repair rounds are available. A demonstrated
failure is retained and blocks its corresponding claim.
