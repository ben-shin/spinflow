# Batch Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Process many spectra with one reviewed recipe and reusable labelled assignment set, yielding a complete height/volume table and a small Qt front end.

**Architecture:** Keep schema-1 single-file recipes and scientific functions intact. Add data-only assignment, NMRPipe-import and batch-service modules; CLI and optional Qt call the same service. Each batch item gets an exclusive native output, independent identity, and a durable result row for every assignment.

**Tech Stack:** Python 3.12+, existing NumPy/SciPy/h5py/nmrglue and optional PySide6 Essentials; stdlib JSON/CSV/path/hash; no new dependency, database, server, shell executor or numerical kernel.

**Spec:** `docs/superpowers/specs/2026-11-09-batch-workflows-design.md` in isolated `batch-workflows/spinflow` worktree, commit `772c05f6d878310205b529ec33b4e99ad2741696` (approved spec SHA-256 prefix `64e782d`). Intended plan path after review: `docs/superpowers/plans/2026-11-09-batch-workflows.md`. This private draft is not a product change.

## Global Constraints

- Preserve public schema-1 `plan/run`, API defaults, precision, quadratures, provenance, operation order, tolerances and existing source/GUI behavior. New JSON sidecars have their own schema-1/kind tags; do not change native/session/recipe formats or support registry.
- Batch is serial, one input loaded/processed at a time; no globs, recursion, overwrite or adaptive algorithm. Existing readers define supported Bruker/Varian and processed profiles. Native outputs only. Analysis remains 1D/2D and caps each bounded ROI at 2,000,000 points per component and templates at 1,000 assignments; 3D/4D bounded processing is per input, not a claim of batch assignment analysis.
- Keys are `(item_id, assignment_id)`, not file hash, basename, peak rank or CSV position. Fixed integration bounds never recenter. Channel defaults to 0 and is validated; all quadratures remain in saved spectra.
- A NMRPipe script is parsed as data. No shell, `eval`, subprocess, converter execution or arbitrary macro. Preserve source hash/line/column and every refused directive. Initial command scope is the approved spec's guarded EM/SP, explicit ZF, gated FT/PS/TP; EXT/REV/GM and unsupported flags refuse. Do not advertise full NMRPipe parity.
- Do not promote FT/nonzero PS/TP before exact pinned NMRPipe 13 formula/header and synthetic plus acquired parity gates. The old strict scientific tolerances and original references remain; no fit, alignment, scaling or phase adjustment to force agreement.
- Worker method already selected by standing user delegation: existing role owners with proportional independent review, root plus at most three active descendants and global creation 10/12 at plan time. No Native one-owner implementation shortcut. Implementation follows user review of this plan; no worktree/source execution is authorized by this draft.

## File ownership and stable interfaces

| Owner | Files | Contract |
|---|---|---|
| Chemical/assignment owner | create `src/nmrnext/assignments.py`; `tests/test_assignments.py` | `load_assignment_template(path: Path) -> dict`; `save_assignment_template(template: dict, path: Path) -> dict`; `measure_assignments(dataset: Dataset, template: dict, *, axis_map: dict[str,str]) -> list[dict]`. No import from CLI/batch/Qt. |
| Recipe/batch owner | create `src/nmrnext/pipe_script.py`, `src/nmrnext/batch.py`; modify `src/nmrnext/cli.py`; tests `tests/test_pipe_script.py`, `tests/test_batch.py`, `tests/cli/test_batch_cli.py` | `parse_pipe_script(text: str, *, source_name: str) -> dict`; `compile_pipe_script(parsed: dict, header: DatasetHeader, *, input_path: Path, output_path: Path) -> dict`; `load_batch_manifest(path: Path) -> dict`; `save_batch_manifest(manifest: dict, path: Path) -> dict`; `plan_batch(path: Path) -> dict`; `run_batch(path: Path, *, resume: bool=False, continue_on_error: bool=False, should_stop: Callable[[],bool]|None=None, on_progress: Callable[[dict],None]|None=None) -> dict`. Parser never imports GUI. |
| GUI owner | modify `src/nmrnext/gui.py`; test `tests/gui/test_batch_gui.py` | Calls only `plan_batch`, `run_batch`, `load_assignment_template`; existing `_start_job`/`_job_finished` convention. No independent numerical or recipe path. |
| Integration owner | docs/examples and opt-in reference fixtures/tests; existing CI | Proves all slices together, version/source identities, scientific and resource gates. No private raw files committed. |

Contract fields to freeze in tests before production code: template `{'schema_version':'1','kind':'assignment_template','reference':{'dataset_digest':str,'axes':list,'complex_axes':list,'quadrature_index_orientation':dict},'analysis_channel':int,'assignments':[{'assignment_id':str,'label':str,'sign':'positive|negative','unit':'ppm|Hz|points','search_bounds':{axis_id:[lo,hi]},'integration_bounds':{axis_id:[lo,hi]},'threshold':float}]}`. Bounds are inclusive sample centers; `points` requires identical explicit grid. Batch manifest `{'schema_version':'1','kind':'batch_manifest','operations':list,'backend':str,'threads':int,'memory_bytes':int,'assignment_template':str,'items':[{'item_id':str,'input':str,'output':str,'axis_map':{template_axis_id:result_axis_id}}]}` plus optional `pipe_script` source path and declared run flags; require exact fields and decide one recipe source, never silently combine. A compiled script returns operations plus `source_sha256`, literal/effective `-in/-out`, and line/column-to-operation records. `measure_assignments` returns assignment-local fields only: assignment ID, sign, candidate count/`count_truncated`, status (`measured|not_found|ambiguous|failed`), coordinates, signed height and volume/units or null, bounds/policy, warnings/error. `batch.py` alone enriches final CSV/JSON rows with item ID, source label/locator/content identity, run/recipe/template digests, effective channel and axis map. Unattempted items after stop have one row per assignment with `status=not_run`, null science fields and a stop reason; they are not failed reads. JSON sidecar owns detailed diagnostics; CSV columns are fixed and documented. Match one positive/negative local extremum only inside fixed search bounds. Zero candidates=`not_found`; two or more=`ambiguous`, never choose strongest. If picker caps candidates, mark count as lower bound and ambiguous.

RED rule: import new functions inside each test from the intended `nmrnext` module, or invoke the new CLI subcommand, using valid synthetic fixtures. Before code, verify the absent module/symbol or specific missing behavior is the failure, not unrelated collection/import/environment errors. Existing tests use pytest (`pyproject.toml:42-44`); unchanged CI discovers `tests/` with `.venv/bin/python -m pytest -q --ignore=tests/gui --ignore=tests/plot --ignore=tests/cli/test_plot_cli.py -k 'not test_gui_'` and the optional GUI job uses `.venv/bin/python -m pytest -q` (`.github/workflows/ci.yml:32,96`).

## Review Focus

1. Duplicate-byte inputs with different IDs or repeated basenames must produce distinct rows/outputs; resume must refuse same ID with changed source, recipe, template, destination or output hash. Task 4 tests this.
2. Malicious or unsupported script text must refuse with exact source location before any input read/output creation; no shell evaluation. Task 2 tests this.
3. Uncalibrated ppm, swapped/missing axis map and invalid quadrature/channel must produce an item/assignment failure, not silent remapping. Task 1 and Task 4 tests this.
4. Missing, multiple, edge or overlapping candidates/bounds must retain explicit statuses and fixed integration bounds. Task 1 tests this.
5. Late write failure and cancellation between items must preserve prior complete items, discard current staging, retain journal/error rows and stay within serial memory policy. Task 4 and Task 6 test this.

---

### Task 1: Assignment template and bounded measurements

**Files:** Create `src/nmrnext/assignments.py`, `tests/test_assignments.py`. Read existing `analysis.py:208-303,306-337,434-515` and `regions.py:15-73,87-115` without changing their defaults.

**Interfaces:** Produces the `load_assignment_template`, `save_assignment_template` and `measure_assignments` signatures and assignment-local fields above. Batch owns item/source/run enrichment. Consumes `Dataset`, its axes/quadratures and existing ROI picker/integration mathematics. Batch/GUI may import this module; it must not import either.

- [ ] **Step 1: Write failing tests** for exact template save/reopen round-trip/unknown fields and exclusive output; calibrated ppm and compatible Hz; points-grid identity; channel 0 and invalid channel; swapped/missing map; one/no/two/edge candidates; overlapping fixed boxes; signed height and signed volume; 1,000 cap and >2M ROI refusal. Assert all per-assignment IDs/statuses/bounds; a second larger peak must never replace an ambiguous match.
- [ ] **Step 2: Run** `python -m pytest tests/test_assignments.py -q`; expect the absent `nmrnext.assignments` import inside the relevant test or the exact missing feature assertion, with valid fixture and clean collection.
- [ ] **Step 3: Implement** exact interfaces. Resolve each bounded ROI using existing `regions._selection` bounds and slice only that ROI; compute parent digest once per item, not once per assignment. Call existing local `pick_peaks`/`integrate_regions` for science; preserve parent coordinate mapping and fixed integration bounds. Validate all template and axis/channel fields before measurement. Do not treat stored reference digest as a per-item analysis report.
- [ ] **Step 4: Run** the same test file, expect pass; then run `python -m pytest tests/test_analysis.py tests/test_regions_session.py -q`, expect pass.
- [ ] **Step 5: Commit** only Task 1 files in the isolated branch after reviewer gate.

### Task 2: Data-only NMRPipe parser and reusable operations

**Files:** Create `src/nmrnext/pipe_script.py`, `tests/test_pipe_script.py`. No shell execution. Read the frozen `script-semantics-v1/SEMANTIC-MAP.md` and official linked manuals.

**Interfaces:** Produces `parse_pipe_script` and `compile_pipe_script` above. Compiler consumes a sample-free `DatasetHeader` and logical `@item.input/@item.output` bindings; output is a strict schema-1 compatible ordered operation list and source/provenance record. `batch.py` owns actual path binding and output policy.

- [ ] **Step 1: Write failing tests**: literal EM/SP full-grid and first-point options, explicit-size ZF, bare FT gate state, zero PS; line continuations and exact source line/column; `-di`/unsupported flags/unknown directive; `$`, backtick, `$(...)`, redirect, semicolon, shell quote/control and `var2pipe` refusal; duplicate/missing/conflicting `-in/-out`, wildcard path, `.ft1/.ft2` output and `-ov` on existing destination refusal. Assert refusal precedes sample reads and output creation.
- [ ] **Step 2: Run** `python -m pytest tests/test_pipe_script.py -q`; expect absent `nmrnext.pipe_script` inside the test or the exact missing parser behavior, with clean collection.
- [ ] **Step 3: Implement** a finite tokenizer/parser, not `shlex` execution. Accept only approved literal stages and fully validated options; bind original paths to logical item roles, preserve original strings/source SHA and operation provenance. `-ov` records intent but only permits a fresh exclusive destination. Converter stages are inspect-only and block unless every metadata assertion maps to an existing reader; never execute them. `FT` emits only when qualification gate in Task 3 is present; PS nonzero/TP remain refused until Task 3. Reject ambiguous NDAPOD, ZF-auto, phase pivot, sign, header/calibration semantics.
- [ ] **Step 4: Run** the same test file, expect pass, and `python -m pytest tests/unit/test_operations.py tests/cli/test_plot_cli.py -q` for unchanged operations/analysis.
- [ ] **Step 5: Commit** only Task 2 files after reviewer gate.

### Task 3: Qualify FT, nonzero PS and 2D TP admission

**Files:** Extend `tests/test_pipe_script.py`; add reference fixture metadata under `tests/fixtures/` only with independent review; modify `src/nmrnext/pipe_script.py` admission table. No new operation or NMRPipe runtime dependency in product.

**Interfaces:** Same compiler API. A versioned evidence table names exact NMRPipe 13 binary/source/fixture hashes, formula and supported flags; compiler admits only proven cases. If unavailable or failed, keep that directive refused and report the blocked accepted-spec gate; do not substitute zero phase for nonzero.

- [ ] **Step 1: Write failing synthetic and opt-in acquired parity tests** for forward sign/channel/centering, PS signed p0 and p1 pivot/N behavior, TP all four quadratures and final axes/headers. Freeze originals and strict existing dtype-specific tolerance before candidate output; include a negative `PS -di` and every other `-di` form that keeps all quadratures.
- [ ] **Step 2: Run synthetic tests and separately authorized pinned NMRPipe 13 opt-in tests;** expect feature gate failure initially. Never run external software from imported text.
- [ ] **Step 3: Document exact NMRPipe formulas/header effects and extend only the compiler mapping proven representable by existing `fft`, `phase`, `transpose` operations. Reject every `-di` command, including when standalone `discard_imaginary` exists; refuse unrepresentable packing/loss. No numerical algorithm change or fitted correction.
- [ ] **Step 4: Rerun exact parity and existing operations tests;** require all arrays, axes, quadratures, provenance and strict original gates. Independent scientific review signs each admitted form.
- [ ] **Step 5: Commit** only mapping/tests/fixture metadata after qualification. A failed gate blocks full-script acceptance but leaves Task 2's supported subset usable.

### Task 4: Serial batch service, persistence and resume

**Files:** Create `src/nmrnext/batch.py`, `tests/test_batch.py`; import Tasks 1-3. No change to `operations.py` or native kernel.

**Interfaces:** Produces `load_batch_manifest`, `save_batch_manifest`, `plan_batch`, `run_batch` signatures above. `plan_batch` returns per-item source/format/header, bound schema-1 recipe, output and refusals before samples; `run_batch` returns journal/CSV/JSON paths and counts. `should_stop()` is polled between items only. `on_progress` receives immutable `{'item_id':str,'state':'started|completed|failed|skipped|not_run','completed':int,'total':int}` snapshots at item boundaries; completed counts terminal attempted/resume-verified items, not unattempted items. Callback failure stops before the next item and keeps completed outputs. CLI/GUI call these APIs.

- [ ] **Step 1: Write failing tests** for two different `item_id`s with identical bytes/basename; per-item recipe binding and axis map; manifest save/reopen and source content/tree identity; all item×assignment rows including failure and truthful `not_run` after stop; exact progress event IDs/states/counts and callback error stop; output collision and wrong-destination resume; changed source/recipe/template/run/output hash refusal; late write cleanup, explicit continue-on-error, and stop after first complete item. Assert no second item loads on cancel and no final partial output.
- [ ] **Step 2: Run** `python -m pytest tests/test_batch.py -q`; expect absent `nmrnext.batch` inside a valid test or the exact missing service behavior, with clean collection.
- [ ] **Step 3: Implement** exact manifest validation, sample-free preflight for every item, serial existing `describe_dataset`/`read_dataset`/`plan_operations`/`run_operations`/`write_dataset` composition, exclusive stage/atomic publish, fsynced item journal and deterministic CSV plus JSON diagnostics. Hash each source once per item; acquisition-tree identity is sorted relative path/size/SHA. Resume skips only exact item+source+destination+recipe/template/run+output identity, else refuses. Batch enriches assignment-local rows with item/source/run identities; unattempted items get null-science `not_run` rows and explicit stop reason. Emit progress at boundaries. Preserve failures/outliers; never silently replace/merge outputs. Cap ROI via Task 1; label 3D/4D analysis unsupported even where per-input bounded processing works.
- [ ] **Step 4: Run** the same tests and `python -m pytest tests/test_workflow_bundle.py tests/test_regions_session.py -q`, expect pass.
- [ ] **Step 5: Commit** only Task 4 files after reviewer gate.

### Task 5: Shared CLI commands

**Files:** Modify `src/nmrnext/cli.py` (parser/dispatch only); create `tests/cli/test_batch_cli.py`. Keep existing command signatures and errors intact.

**Interfaces:** `spinflow batch-plan MANIFEST`, `spinflow batch-run MANIFEST [--resume] [--continue-on-error]`; optional `spinflow import-pipe-script SCRIPT --input INPUT --output OUTPUT` reports compiled schema-1 operations/provenance without running data. CLI delegates to Task 2/4 and emits JSON status; no duplicate engine.

- [ ] **Step 1: Write failing CLI tests** for plan-before-read, actual two-item JSON/CSV, unsupported script line errors, resume wrong destination and unchanged `spinflow plan/run` behavior.
- [ ] **Step 2: Run** `python -m pytest tests/cli/test_batch_cli.py -q`; expect the specific unknown `batch-plan`/`batch-run` subcommand for valid fixtures, not unrelated import/collection errors.
- [ ] **Step 3: Add** lazy dispatch and parser arguments for these exact commands; use service APIs, no CLI-local scientific code or eval.
- [ ] **Step 4: Run** CLI tests plus `python -m pytest tests/cli -q`, expect pass.
- [ ] **Step 5: Commit** CLI/test changes after reviewer gate.

### Task 6: Minimal native Qt batch front end

**Files:** Modify `src/nmrnext/gui.py`; create `tests/gui/test_batch_gui.py`. GUI owner only.

**Interfaces:** Calls Task 1/4 load/save/plan/run APIs through existing `_start_job`; displays item IDs/path labels and exact result statuses. Capture backend/budget/paths/template/manifest snapshots on GUI thread before worker launch. Pass `threading.Event.is_set` as `should_stop`; worker `on_progress` emits a queued Qt signal, never reads widgets or mutates UI. Buttons **Open**, **Label**, **Apply**, **Export CSV**; advanced settings collapsed; system/native Qt style and neutral colors.

- [ ] **Step 1: Write failing GUI tests** for assignment edit/save/reopen, input list with duplicate basenames, progress IDs/states/counts and error/`not_run` rows, busy-state action disabling, GUI-thread snapshot isolation, queued worker signal, `threading.Event` cancellation between items, and CSV export; assert no UI-thread processing or unreviewed rerun.
- [ ] **Step 2: Run** `python -m pytest tests/gui/test_batch_gui.py -q` under the existing supported GUI test environment; expect missing batch controls/signals under a valid Qt fixture, not unrelated environment/collection failure.
- [ ] **Step 3: Add** a compact batch panel using existing worker/ticket lifecycle, queued progress signal and shared APIs; preserve current spectrum plot, recipe editor, preview/current state and visible source controls. Avoid new style framework, animation or AI styling.
- [ ] **Step 4: Run** new GUI tests plus `python -m pytest tests/gui -q`, expect pass.
- [ ] **Step 5: Commit** GUI/test changes after reviewer gate.

### Task 7: Integrated reference, scale, docs and release gate

**Files:** Add batch examples/docs under `docs/` and `examples/`; integration tests under `tests/`; update release notes only after qualification. Integration owner; no proprietary/raw files in Git.

**Interfaces:** Consume the frozen Task 1-6 APIs. Publish no scientific or NMRPipe-compat claim beyond qualified cases.

- [ ] **Step 1: Write failing integration tests** for 100 serial items (including duplicate bytes/names), fixture raw Bruker/Varian and processed spectra, complete CSV/JSON and all outputs, fixed ROI cap, failure/resume/cancel, and one real Qt run. Pin exact versions/source/binary/fixture hashes and predeclared strict science tolerances for NMRPipe 13 opt-in reference checks; no fitting or retuning.
- [ ] **Step 2: Run** focused integration tests before code merge; expect fail or explicitly blocked external-reference gate, never call that a pass.
- [ ] **Step 3: Document** manifest/template/CSV fields, status meanings, scientific limits, unsupported script flags, memory scope, provenance and exact CLI/GUI walkthrough. Correct examples only to admitted command forms.
- [ ] **Step 4: Run** `python -m pytest -q` once at integration, existing CI on actual installed Linux package, NMRPipe 13 opt-in parity with independent source review, and 100-item sequential peak-RSS capture. Distinguish worker lifetime RSS from aggregate memory; retain raw distribution and all failures. Verify unchanged public single-file tests and any acquired-data/consumer gates required by the existing release process.
- [ ] **Step 5: Commit** docs/tests and prepare reviewed branch/PR; root coordinates publication and user review. Human/phase/scientific gates previously failed or unverified remain so unless this exact evidence closes them.

## Self-review before handoff

Spec coverage: user intake, recipe/script import, independent labels, serial execution/resume, rows, GUI, validation and docs each have an owning task. The admitted FT/PS/TP gate is explicit; if unavailable, full end-to-end NMRPipe-script acceptance stays blocked rather than bypassed. `assignments.py` never imports `batch.py`; `batch.py` imports assignment/script services; CLI and GUI import batch lazily, avoiding a cycle. Task 4's callback is between items; Tasks 1/2 do not create output. All five Review Focus cases have named RED-first tests. No tests, scientific computations, app launches or source edits were run while writing this plan.

