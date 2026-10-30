# Stage 16 performance protocol

This stage measures and improves runtime and memory while retaining the Python orchestration layer, public API defaults, scientific conventions, supported dimensions and export contracts. The experimental application remains bounded to its documented acquisition and analysis profiles. Performance work does not establish general 3D/4D processing support.

## Frozen baseline

The baseline is clean Git commit `62cac5c7e5074091c382aedb9eae154a8f8d996d`, frozen before any Stage 16 edits. Its archive is `.artifacts/performance/baseline/source-62cac5c7e5074091c382aedb9eae154a8f8d996d.tar`, 3,112,960 bytes, SHA-256 `bfce934ef80dd88abb21d4331f2f75c57808e8b3136560d237a2dbd71ebd1cbb`. The read-only manifest records all 192 source hashes, the host interpreter and installed dependency versions, distribution metadata and the existing native binary. Manifest SHA-256: `12a9eb4d6472c52554ef61b6fe533cd41f0ced0ed8e0d887d38b93f0aa4e493d`.

The existing Windows extension was copied and fingerprinted, but its relationship to this exact source was not independently established at freeze time. Runtime evidence must identify a fresh baseline build from the archive and a separate candidate build. The source archive and manifest are immutable. Later build receipts are separate files. Historical Stage 15 laboratory seals and `.artifacts/lab*` are never edited.

## Acceptance contract

| ID | Required behavior | Evidence |
| --- | --- | --- |
| R1 | Separate cold-command, warm processing, import, export and interactive latency; measure peak memory. | Paired raw observations with exact timing boundaries and fresh-worker high-water memory; profiles explain selected costs. |
| R2 | Share immutable metadata and sample owners internally; reuse private working buffers without exposing writable aliases. | Mutation/ownership tests; complete provenance and axes; allocation/memory comparison. |
| R3 | Remove only demonstrably redundant validation, scans and conversions. | Public-boundary refusal tests, overflow checks, reference agreement and profiles identifying eliminated work. |
| R4 | Introduce at most one measured native kernel or fused path at a time, preserving an independent reference path. | Explicit dispatch, reference comparisons and paired results before any additional kernel. A kernel without an evidenced benefit is deferred. |
| R5 | Bound recipe-prefix and immutable numeric-identity caches by bytes and exact identity. | Full-plan validation before hits; zero/off path, eviction, source/backend/thread/prefix changes, failures and no-stale-result tests. |
| R6 | Require bounded block execution and measured memory before claims about large 3D/4D workloads. | This stage makes no such claim; existing unsupported acquisition/analysis profiles remain explicit. Any future claim needs its own block/memory evidence. |
| R7 | Reprofile before FFT or thread-policy changes. | Preserve existing PocketFFT implementation, precision and thread defaults in this stage. |
| R8 | Recheck acquired inputs and affected independently consumed outputs. | Fixed public protein workflow/checkpoints/ROIs and unchanged coordinate/sign gates; candidate NMRPipe/UCSF reopened by independent consumers where available. |
| R9 | Publish paired before/after distributions with exact source/build IDs, numeric agreement and RSS. | Raw trials, hashes, median/range/IQR and paired ratios; limitations and unverified checks remain visible. |
| R10 | Preserve precision, all quadratures, padding, provenance, tolerances and scientific defaults. | Byte/numeric/metadata/provenance checks without fitted alignment, scaling or phase adjustment. |

## Cases and controls

Use the previously studied public Varian protein acquisition, fixed reference and recipe in `tests/fixtures/lab_workflow_case.json` and `examples/laboratory-workflow.json`. Preserve all original recipe operations, parameters, zero-fill sizes and loss acknowledgments. Acquired source hashes must match before and after the run. Synthetic 1D/2D cases exercise exact ownership and cache edge cases; they do not replace the acquisition checks.

The Linux comparison uses a new Stage 16 qcow2 overlay and copied firmware variables under `.artifacts/performance/runtime`, with the Stage 15 disk as a read-only backing image. The planned guest is Ubuntu under QEMU WHPX, q35, one vCPU and 4 GiB RAM; QEMU is pinned to one host logical CPU and worker execution is pinned to guest CPU 0. Runtime numerical pools and native FFT threads are fixed to one. Record the actual hypervisor, command, CPU affinity, memory, toolchain, package versions, source archive, wheel and installed binary hashes. Virtualized results describe this setup only. Reusing installed reference programs and public data is allowed; their identities are recorded again.

Perform one warmup per lane/case, followed by at least seven measured baseline/candidate pairs in alternating order `AB, BA, AB, BA, AB, BA, AB`. Record every trial; no outlier deletion, silent retry substitution or cherry-picked distribution. Baseline and candidate use distinct installed environments and the same harness, inputs, recipes, resources and thread policy. OS file-cache state is warm/uncontrolled unless separately established. “Cold command” means a fresh process, not a cold filesystem cache.

| Case | Timing boundary | Memory boundary |
| --- | --- | --- |
| Cold raw to NMRPipe | Parent before worker/process creation through process exit; startup, imports, raw read, validation/planning, full 14-operation recipe, export and complete requested provenance included. | Fresh worker lifetime high-water RSS, with any child aggregation scope stated. |
| Cold raw to UCSF | Same boundary and full recipe, UCSF endpoint. | Same fresh-worker scope. |
| Warm full recipe | Preloaded validated acquisition to completed `run_operations`; imports and input read excluded. | Worker lifetime high-water, explicitly including setup; it is not an operation-only memory delta. |
| Vendor import | Immediately before `read_dataset` to validated immutable Dataset returned. | Fresh worker lifetime high-water including imports; input bytes/hash recorded. |
| Export NMRPipe / UCSF | Fixed accepted S10 Dataset to completed writer/required provenance; each endpoint recorded separately. | Fresh worker lifetime high-water including result setup; operation time reported separately. |
| Interactive unchanged recipe | Primed controller cache to completed identical first-12-operation recipe, ending at indirect phase before final discard/transpose, including full current validation. Baseline replays those same 12 operations through the public engine. | Cache residency and fresh-worker lifetime peak recorded separately. |
| Interactive changed last phase | The same first 12 operations, with only the final indirect phase parameters changed; original source and unchanged prefix retained. Prime at 0 degrees, warmup at 0.5 degrees, then measured manual phases 1 through 7 degrees. | Same residency and lifetime peak scope. |

Keep wall time, process CPU time and memory in distinct fields. Record both operation elapsed time and complete worker wall time where applicable. A lifetime RSS measurement cannot establish the incremental peak of a warm operation. Traced Python allocation values cannot stand in for process RSS. Run result agreement checks outside the timing unless the endpoint contract requires them; include mandatory endpoint hashing/provenance work inside its actual endpoint.

## Safe implementation order

1. Capture the immutable baseline and initial measurements/profiles.
2. Improve internal sample/metadata ownership and remove duplicated work with preserved boundary checks.
3. Add budgeted interactive prefix reuse and exact digest memoization; retain a zero-cache reference path.
4. Reprofile. Add a native kernel only for a remaining measured cost, in a separate tested change. Do not change FFT or thread policy.
5. Freeze the integrated candidate source/build, run the paired suite and acquired/consumer checks, then perform independent correctness, robustness and maintainability reviews plus a separate judge.
6. Resolve a consolidated review request in at most two repair rounds. Update the development log and publish only evidence tied to the accepted candidate. Commit/push occurs only after root acceptance; no release or tag is created.

## Cache and ownership invariants

The GUI always describes processing from its original source. Reusing a prefix must produce the same axes, dtype, quadrature ordering, samples, metadata and complete operation provenance as replay. A hit is valid only for the identical immutable source and normalized recipe prefix, backend/version, threads and relevant memory policy. The complete requested recipe is validated before any hit; cache contents cannot bypass a newly reduced memory budget or invalid suffix. Source reload/reset, failures, configuration changes and eviction must not expose stale output. A disabled cache uses the reference path.

Both caches require an exact built-in `Dataset` containing only exact built-in `Axis` objects. Subclasses may override equality, hashing, public samples or mutable axis properties; they retain uncached digest and execution behavior. The recipe cache clears existing entries before executing an ineligible source. This eligibility check preserves public subclass behavior while limiting reuse to established immutable invariants.

The recipe cache has an explicit 128 MiB capacity, limited further by headroom after the current complete plan's conservative working-array estimate. The GUI's default working-array budget remains 256 MiB; the acquired benchmark records its selected larger budget. Capacity counts retained unique numeric owners, metadata, keys and record/object overhead, with shared owners counted once. Existing entries and newly staged snapshots are counted together; old entries may be evicted during a failed run, but partial new checkpoints are never published. Sparse snapshots preserve the final boundary and its predecessor when they fit, preferring the predecessor when both cannot fit. This is a working-array policy, not a hard RSS guarantee. Numeric digest memoization has a separate 32 KiB object-overhead budget and must preserve the existing serialized header and exact sample bytes, including orientation overrides; it must not retain a dataset after its owner is gone. Source/provenance hashing required by a CLI endpoint is not removed by interactive caching.

## Agreement and reporting

Record exact dtype/shape, all quadrature channels, axis metadata, sample/sign agreement and provenance. Baseline/candidate comparison uses unchanged science/reference gates and the original independent reference outputs; no candidate-dependent alignment, amplitude scaling or phase fitting is allowed. Retain the original failed checks if any. Report all mandatory checks as PASS, FAIL or UNVERIFIED, with the actual command/evidence identity.

Publish a compact tracked results record plus raw machine-readable observations, exact source/build/harness/input/output hashes, memory method and timing definitions. Include median, minimum, maximum, IQR and paired ratios for all cases. Do not infer bare-metal performance, broad dimensional support, NMRPipe speed parity or a general backend ranking from this one virtualized acquired case.
