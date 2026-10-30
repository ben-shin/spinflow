# Run your Linux acceptance check

This is a local check of your installed SpinFlow on Ubuntu 24.04 x86-64. It produces repeatable CLI observations and a **blank manual checklist** for you to complete. Automated checks do not establish that the application suits your research or that physical user hardware has passed. No files or results are uploaded.

Use the matching release's tagged source archive for the runner, recipes and pinned fetch helper. A wheel or Python source distribution alone does not include these tools. Extract that archive into a separate folder, enter it, and keep your installed prefix outside it. Use an existing supported Python installation; no compiler is required by this check.

Prepare the existing public acquisition once:

```sh
python3 tests/fixtures/fetch_varian_public.py
```

The helper downloads only the existing hash-pinned nmrglue example archives, verifies their size and SHA256 and extracts declared members into a local cache. It does not run acquisition/processing scripts. Data remain private in `.artifacts/`; no redistribution permission for these acquisition binaries is assumed. Fetch errors are failures, not successful preparation.

Then run, replacing the prefix with your actual installation:

```sh
python3 tools/linux_user_acceptance.py --prefix "$HOME/SpinFlow" --output "$HOME/SpinFlow acceptance 01"
```

The output folder must be new and its parent must exist. Repeats require a new folder; the runner retains every trial output and refuses existing output folders or any overlap with the input. Do not place the report inside the acquisition directory. Allow roughly 200 MiB for the five default native output files and logs. This is separate from the recipe's 1 GiB working-array policy, which is not an RSS or total-memory limit.

The default is the existing `examples/laboratory-workflow.json`: all 14 fixed operations, its original padding, manual phase, references and quadrature/discard conventions on the four-channel acquired C13/N15 case. The final native result has one real component and 2048×4096 points. The runner changes only the input/output paths and native endpoint/loss settings: `output_format=native`, `allow_losses=[]`, `threads=1`. Native output retains complete metadata and provenance for replay comparison; the original recipe's NMRPipe output/loss settings remain recorded in the report. The known public case retains its NCa/NCO label uncertainty and does not establish biological assignments or general format support.

For another **already fixed** schema1 recipe, use `--recipe /path/to/recipe.json` and, if necessary, `--input /path/to/acquisition`. Original recipe-relative input paths are honored. The recipe must declare one thread and a positive `memory_bytes`; its operations and memory policy are preserved. The runner does not download a new case, choose a phase, align/rescale spectra or select a scientific reference for you. A new dataset requires its own preregistered controls.

## What the automated result means

The standard-library controller invokes only the selected prefix's `bin/python -I`. Product imports must resolve inside that prefix; it records actual Python/package/native/dependency versions, imported module/native hashes, platform, CPU/memory, input-file hashes, source metadata, exact recipe and commands. It fixes its own process affinity and every child to one available logical CPU, and numerical thread environment variables to 1. `--cpu N` selects another currently allowed logical CPU. This is process-local and does not change global settings. It records only the selected environment allowlist, not your full environment.

Five fresh CLI processes run by default (`--samples 2` through `--samples 20` is available). Each timing includes startup, acquisition read, normal processing/finite checks/hashes/provenance, and native output write/close. Input identity hashing, installed probes and output comparisons are outside the measured endpoint. **Preflight input hashing warms filesystem caches: these are fresh-process timings, not cold-I/O measurements.** All samples are retained; none are discarded as outliers. JSON contains raw observations and minimum, maximum, median and inclusive-quartile IQR. A failed/nonzero/missing trial or an input mutation makes the bundle failed/incomplete and prevents a successful distribution.

On Linux, `wait4` reaps each actual child exactly once and records its fork/spawn-to-exit lifetime RSS high-water. That value may include an inherited controller resident-memory floor before program execution; the controller's own pre-spawn lifetime peak is recorded separately. Nothing is subtracted. This is not pure post-exec or incremental memory, an OS memory cap, a flat-scaling result or a kernel file-cache measurement. When genuine `wait4` is unavailable, RSS is marked unavailable rather than estimated. Full-array and full-metadata comparisons occur in separate installed processes outside timing.

Every trial must have identical dtype, shape, all component samples and complete axes/metadata/global provenance. This proves deterministic replay within this installed cohort; it does **not** independently validate the science, accuracy on new acquisitions, performance parity with NMRPipe or suitability for every machine. No latency/RSS pass threshold has been predeclared, so measurements are descriptive. Human/user-hardware acceptance remains **UNVERIFIED** until you fill and return the manual results.

## Complete the manual part

Read `report.md` and `report.json`. Keep the generated trial files. Open `manual-checklist.md` and fill `manual-results.json` with your operator/date/hardware, observations, failures and decision. All entries deliberately start blank. Record whether this was your physical Linux machine or a VM; an automated VM test cannot stand in for physical user hardware.

Use the actual GUI installation and the generated `trial-01.recipe.json` (its input path is absolute) as the fixed recipe record. Choose new paths for all manual saved files. Check:

1. Load the full source, review all 14 steps and Process the full result. Do not silently crop it to pass an analysis guard.
2. Make one pending edit and verify Pending versus Applied is clear. Check which state session save/export uses. On a separate supported small case, retain an explicit automatic-phase result after pressing Process again; the existing 2,000,000-point per-component analysis limit still applies.
3. Select a Current ROI on the current calibrated result. Inspect global parent coordinates, signed peaks and integrals. For the known case, point bounds `a0=[700,900]`, `a1=[1600,1800]` give a small fixed region; use its actual ppm coordinates for the equivalent physical selection. Do not relabel these as curated biological assignments.
4. Save a **new** figure. Inspect visible-region bounds, physical axis labels, contours and readability in the saved image.
5. Save a session to a new folder, close it and reopen it. Check source, full applied recipe and analysis. Replay the saved workflow through the actual installed CLI; compare complete values/axes/provenance and review declared export losses.
6. Export the intended native/NMRPipe/UCSF outputs and open them in your **actual intended external application**, recording its name/version. Inspect axis direction/calibration, signs and selected weak/strong regions. A successful SpinFlow write alone is not an independent application check.
7. Cancel an open/save action and try an invalid session copy. Your prior good state should stay usable. Record any unexpected behavior without overwriting originals.

Return only the reports/manual observations you choose to share after reviewing paths and data sensitivity. There is no automatic upload, telemetry, researcher-contact step or pre-filled approval.
