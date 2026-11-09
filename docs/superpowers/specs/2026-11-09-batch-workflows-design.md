# Batch workflows and reusable assignment templates

**Status:** proposed product design, awaiting written-spec approval. This document defines a feature direction and acceptance contract; it does not claim implementation, compatibility, or scientific validation.

## Problem and outcome

SpinFlow runs one strict recipe per input with analysis and an optional Qt workbench. It lacks command import, manifest processing, and stable cross-spectrum assignments; peak ranking IDs are not labels.

Add one shared batch engine for CLI/Python and Qt. Reuse validated operations and emit typed per-spectrum assignment results. This supports reproducible relative measurements, not chemical identification.

## User workflow

1. Add supported Varian/Bruker acquisitions or processed spectra through existing readers. Edit a recipe or review imported commands; inspect source/converter stages, mappings, and refusals. Never execute imported text.
2. Label signed peaks/regions with stable IDs, axis/units, sign, search limits, and fixed bounds.
3. Save a versioned template sidecar with reference calibration and identity.
4. Select inputs, recipe/template, and fresh output folder; preflight and run serially. The GUI shows file/status list, spectrum/editor, and **Open**, **Label**, **Apply**, **Export CSV** actions; advanced controls are collapsed.
5. Review all rows, including failures.

## Data and processing contract

Keep recipe schema 1 and single-file commands unchanged. Versioned batch JSON holds ordered entries with unique `item_id`, source locator, operation records, run settings, assignment template, and output policy. Identical files or repeated basenames remain separate entries. For each item bind actual input/unique native output into an ordinary schema-1 recipe, then validate/plan with run settings. Reuse one operation list; do not make fixed-path recipes. No globs, recursion, or overwrite. Preserve readers, defaults, precision, quadratures, and operations; add no alignment, fitting, scaling, or hidden operation.

Process one input at a time under the selected budget; preflight metadata and report refusals. Existing analysis loads the parent and caps components at two million points. Extract bounded ROIs first where supported; never rescan a full component per assignment. This is not universal out-of-core or an RSS guarantee. Preserve quadratures. Store source locator separately from content identity; hash once and identify acquisition trees by sorted path/size/hash. Never collapse repeated or byte-identical items.

Labels are independent of peak ranking. Templates store axis/quadrature mapping, units, sign, search/integration bounds, and `analysis_channel` (validated default `0`). Preserve all quadratures; explicitly select that component for height/volume and record effective channel/mapping. Validate channel range and mapping per item; mismatch fails rather than switching. Cap at 1,000 regions. Use ppm only with calibrated axes, Hz with a compatible axis, and points only under explicit identical-grid policy. Search declared limits only: zero candidates=`not_found`; multiple=`ambiguous`, never strongest. Keep unresolved IDs/counts/reasons. Matching never changes integration bounds.

Key results by `(item_id, assignment_id)` within the run, not by digest or CSV row position. Display stable item IDs with path labels so repeated names remain distinguishable. Keep a row for every item × assignment, including failed files. CSV records item ID, source label/locator, content digest, recipe/template/run digests, effective analysis channel and axis mapping, assignment data, status, coordinates/sign, candidate count, signed height/volume and units, fixed bounds, policies, warnings, and errors. Volume is the existing signed integral over fixed bounds (intensity·Hz or intensity·Hz²), not concentration or deconvolved area. Keep diagnostics in a versioned JSON sidecar.

## NMRPipe command-file import

Parse only literal `nmrPipe -fn NAME` stages separated by literal pipes and optional line continuations. Bind the script's single literal `-in/-out` paths to logical `@item.input/@item.output` roles and retain original strings. At runtime those roles resolve only to the selected manifest item and its unique new native output; they are never reused as fixed paths. Reject missing/repeated/conflicting or wildcard/variable/external bindings. Preserve documented `-ov` as source I/O intent, never overwrite: a fresh run requires absent destinations and publishes exclusively; on resume, an existing destination is skipped only after exact identity validation, otherwise refused. Preserve `-verb` only where explicitly documented as logging-only; reject unknown flags. Reject `.ft1/.ft2` batch output requests because initial batch export is native-only. Record original/effective paths and instantiate an ordinary recipe with actual paths and validated operations.

Inspect source/converter stages separately from NMRPipe processing. For raw Bruker/Varian folders, only the existing reader converts input; never invoke `var2pipe` or imported commands. The user selects folder/profile; reader metadata is authoritative. Replace a converter stage with that reader only if every required metadata assertion has a verified exact mapping; otherwise flag and block it until a supported processed input or qualified mapping is supplied. Raw NMRPipe streams remain unsupported. This covers existing raw vendor inputs and processed spectra without arbitrary converter compatibility.

A strict mapping table records exact flags, translated operation, units/defaults, guards, and evidence. Initial candidates are:

| Directive | Mapping and required guard |
|---|---|
| `EM -lb L [-c C]` | Exponential window with explicit `lb_hz=L`, first-point scale `C` (default 1); require finite `L>=0`, `dwell=1/sw`, complete current time grid, before fill, and no header/partial-window modes. |
| `SP [-off O] [-end E] [-pow P] [-c C]` | Sine window with the documented full-grid formula, finite `0<=O<=E<=1`, `P>0`, valid `N>1`, and before fill. |
| `ZF -size N` | Explicit final zero-fill size `N>=current_size`, tail append only. Bare ZF, `-auto`, and default doubling are unsupported. |
| bare `FT` | Forward FFT only on a fully sampled declared complex axis with intended quadratures preserved. Enable only after original-versus-translated numerical fixture and independent review. |
| `PS -p0 0 -p1 0` | Explicit identity phase; it is not sufficient for routine phased processing. Admit nonzero P0/P1 only after freezing the NMRPipe 13.0 degree/sign/pivot/normalization formulas and passing independent synthetic and acquired parity fixtures. |
| `TP` | Admit 2D transpose only after freezing axis/channel packing and header formulas and passing independent full-quadrature synthetic and acquired parity fixtures. Until then reject it and any 2D script that requires it. |
| `EXT`, `REV`, `GM` | Unsupported initially. Axis calibration/crop/reverse and Gaussian-window semantics do not map safely by name alone; require their own exact formula/header mapping and fixtures before admission. |

Reject unknown flags, ambiguous defaults, unsupported dimensions, and unrepresentable semantics with source line/column. Do not guess NMRPipe window defaults, ZF auto sizing, FT mode flags, phase pivots, or transpose/header behavior. Reject control flow, expansion, substitution, shell-dependent quotes, redirects, semicolons, and arbitrary commands. Hash the original text and preserve line-to-operation provenance. Validate all stages before data reads or output creation. Initial batch outputs are new native spectra plus reports. Existing `.ft1/.ft2` readers accept processed-real 1D/2D only; raw/complex/hypercomplex NMRPipe streams and NMRPipe 3D/4D are unsupported. Do not claim full NMRPipe 13 compatibility. Syntax references: [EM](https://spin.niddk.nih.gov/bax-apps/NMRPipe/ref/nmrpipe/em.html), [SP](https://spin.niddk.nih.gov/bax-apps/NMRPipe/ref/nmrpipe/sp.html), [ZF](https://spin.niddk.nih.gov/bax-apps/NMRPipe/ref/nmrpipe/zf.html), [FT](https://spin.niddk.nih.gov/bax-apps/NMRPipe/ref/nmrpipe/ft.html), [PS](https://spin.niddk.nih.gov/bax-apps/NMRPipe/ref/nmrpipe/ps.html), and [TP](https://spin.niddk.nih.gov/bax-apps/NMRPipe/ref/nmrpipe/tp.html); these manuals describe syntax, not SpinFlow parity.

## Persistence, safety, and errors

Save templates and batch specs as versioned sidecars. Do not change native/session/recipe schemas, released API/assets, or registry. Validate IDs, finite values, channel/axis/unit compatibility, sign, and bounds before work. A template's reference digest records origin, not a reusable analysis report; resolve coordinates per input.

Write each item to an exclusive staging path; validate and close before atomic publish, never replacing existing output. Preflight collisions. An atomic journal keys each record by unique `item_id` and stores source locator, content digest, canonical destination, recipe/template/run identity, status, outputs, and errors. Resume skips only when that same item ID, current source/content, canonical destination, and exact recipe/template/run identity match a completed valid output; otherwise require a fresh destination. Thus byte-identical files in distinct manifest entries never merge. Stop cooperatively between spectra, safely finishing or discarding current staging data. No worker kill or partial final files. Continue after item failures only by explicit choice.

## GUI and public boundaries

Qt remains optional; CLI/API and GUI share service, planners, records, and serializers. Show file/status table, plot with editable assignments, recipe/template selection, progress, and export review. Use worker conventions; show errors and disable conflicting actions while busy. Cancellation is between items only. No new GUI framework, database/server, AI, decorative color system, or automatic residue assignment. Keep sign semantics and readable text status.

## Acceptance criteria

1. **Import:** preserve schema 1; bind/revalidate per item and retain aliases/provenance; block unsupported converters. End-to-end pipelines require mapped EM/SP, explicit ZF, FT, nonzero PS and 2D TP. FT/PS/TP require pinned NMRPipe 13 synthetic/acquired parity and independent review. No full-compat claim.
2. **Shared processing:** one engine; fixture raw Bruker/Varian and processed inputs through existing readers.
3. **Assignments:** round-trip IDs, channel, axes/quadratures, units, sign, bounds; test mismatch, calibration, missing/ambiguous. Never switch channels, select strongest ambiguously, or recenter.
4. **Scale/recovery:** 100 inputs including duplicate basenames/content; separate IDs/rows/outputs; test serial budget/RSS, stop, and resume against exact item, destination, content, and run digests.
5. **GUI:** edit/run/review/export without blocking; show errors and ambiguity.
6. **Validation:** synthetic and public acquired cases; preserve strict gates and released assets/API.
7. **Docs:** commands, fields, statuses, memory, reproducibility, and limits; no assignment or concentration claim.

## Existing boundaries used by this design

Current boundaries are documented in [architecture.md](../../architecture.md), [analysis-regions.md](../../analysis-regions.md), and [real-bruker-and-analysis.md](../../real-bruker-and-analysis.md). Source anchors: `cli.py:68–91,237,486–512,571–622`; `operations.py:14–23,118–259,449–455`; `planning.py:9–41`; `analysis.py:208–303,306–337,434–445`; `regions.py:53–118`; `gui.py:716,1083–1120,1122–1147,1321,1601`; `session.py:645,768`. These boundaries remain unchanged pending separate implementation and qualification.
