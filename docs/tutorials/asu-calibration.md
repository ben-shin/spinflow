# Process a small ASU calibration spectrum

This walkthrough uses a public ASU methanol calibration acquisition to load real data, correct its recorded digital-filter delay, apply a window, pad, Fourier transform, estimate a zero-order phase, pick signed peaks, calculate relative areas, save figures and reopen a session. It uses the published **0.3.0b3** installation on **Ubuntu24.04 x86-64**, with supported CPython3.12 or3.13. Processing retains both real/imaginary channels as float64; figures deliberately show the real component.

This is an engineering illustration. The ppm reference is unknown, so figures and regions use **relative mathematical Hz**. Phase estimation assumes positive Lorentzian-like lines; inspect its diagnostics. Peaks are extrema, not chemical assignments. Areas are signed relative intensityÂ·Hz, not concentrations or accuracy measurements. This case does not replace the [scientific qualification limits](../stage21-results.md).

## Get the software and example files

Use a source copy containing **this guide and its matching [example folder](../../examples/asu-calibration/README.md)**. From that source copy's root, run the following in a terminal. The example files are later tutorial content: the immutable b3 source archive downloaded below supplies the existing ASU download helper, not these new tutorial files.

```sh
set -eu
REPO=$(pwd -P)
TUTORIAL="$REPO/examples/asu-calibration"
test -f "$TUTORIAL/00-setup.sh"
(cd "$TUTORIAL" && sha256sum -c SHA256SUMS)
WORK="$HOME/SpinFlow-ASU-01"
mkdir "$WORK"                      # choose a different name if it exists
cd "$WORK"
curl -fL -o install-spinflow.py https://github.com/ben-shin/spinflow/releases/download/v0.3.0b3/install-spinflow.py
printf '%s\n' '85676c2b1faca4e40456d61a2a99d47339a560af47c10bd70d834aa7bc75f21e  install-spinflow.py' | sha256sum -c -
python3 install-spinflow.py --plot
curl -fL -o b3-source.tar.gz https://github.com/ben-shin/spinflow/archive/refs/tags/v0.3.0b3.tar.gz
printf '%s\n' '0a0f01c684813096a1f22867490b7b78efad05bb941e340f1ec64ff4fb28e799  b3-source.tar.gz' | sha256sum -c -
mkdir b3-source
tar -xzf b3-source.tar.gz --strip-components=1 -C b3-source
export PY="$HOME/.local/share/spinflow/0.3.0b3/bin/python"
export SF="$HOME/.local/share/spinflow/0.3.0b3/bin/spinflow"
export B3SRC="$WORK/b3-source"
export RUN="$WORK/run-01"
"$SF" --version
```

Stop on a failed command or checksum; do not update a hash to make a download pass. The b3 installer uses its normal public downloads and prebuilt native wheel, without a compiler or local-wheel override. Installation needs internet access. If you already have the matching default b3 installation, the installer can add plotting support while retaining installed extras. For a custom prefix, pass that same existing `--prefix` to every installer call and set matching PY/SF paths. These environment variables affect this terminal only.

The data source is [ASU CHM343, commit af3b9c2](https://raw.githubusercontent.com/CHM343/NMR/af3b9c2c4642e63e379bc349cb40e1fe8c5730e6/Data/MeOD_25C_Temp_Cal_Bruker400.zip),1,060,855 bytes, SHA256 `38c8d260169d42f0d15ab5c7b9ce7084735419688c9ea66e76dc34fa6b356094`. The existing helper checks that archive and extracts only the pinned fid/acqus pair. ASU's repository carries GPLv3; no separate dataset-specific permission statement was found. Keep downloads, native snapshots and sessions private by default; this example distributes no acquired data. See [provenance and import/filter scope](../asu-import-filter-results.md).

## Stage1: process and inspect phase diagnostics

Run setup once, then process once:

```sh
(set -C; sh "$TUTORIAL/00-setup.sh" > "$WORK/setup.stdout.log" 2> "$WORK/setup.stderr.log")
cd "$RUN"
(set -C; sh "$TUTORIAL/10-process-and-diagnostics.sh" > outputs/stage1.stdout.log 2> outputs/stage1.stderr.log)
```

Open the four `outputs/diagnostic-*.png` files and read `outputs/phase-summary.txt` with `outputs/phase.json`. The before-phase real and imaginary views are available before phase continuation. The fixed recipe uses the recorded positive GRPDLY, exponential0.5Hz broadening, first-point scale1, zero-fill65536, native FFT and one thread. Automatic phase remains zero-order with pivot32767.5 and the unchanged screening settings in the script. The64MiB limit is a working-array estimate, not a total-process RAM cap. No reference or channel discard is added.

Look for a plausible real/imaginary relationship, fit assumptions, per-line residuals, convergence and flags. Imaginary signal need not vanish; a positive-looking peak does not establish physical sign or accuracy. Preserve a refusal or unsuitable fit. Do not force a phase, silently change parameters or treat this tutorial's observed estimate as a target.

The original engineering run fitted three lines with normalized residual0.02462â†’0.02339 and retained the `inspect_phase` and positive-line-assumption flags. That is a recorded illustration, not a required outcome on another installation or scientific phase acceptance. The packaged scripts passed the installed Ubuntu CPython3.12.3 check recorded in [Stage22](../stage22-results.md), including execution from paths with spaces and a different working directory. This tutorial case was not repeated on CPython3.13; the software's separate ABI support checks are recorded in Stage21.

## Stage2: continue without repeating Stage1

If automatic phase succeeded, choose an explicit continuation mode. `engineering-preview` makes no human-review claim. Use `reader-reviewed` only for your own visual decision; it still makes no independent scientific-acceptance claim.

```sh
cd "$RUN"
export CONTINUATION_MODE=engineering-preview
(set -C; sh "$TUTORIAL/20-analysis-and-figures.sh" > outputs/stage2.stdout.log 2> outputs/stage2.stderr.log)
(set -C; sh "$TUTORIAL/30-session-replay.sh" > outputs/session.stdout.log 2> outputs/session.stderr.log)
```

Stage2 searches both signs, SNR5, baseline none,8Hz separation and at most20 peaks. It proposes Â±2Hz regions and integrates signed areas without baseline correction. These are illustrative regions requiring review, without deconvolution. Read full methods/flags in `outputs/analysis.json` and the table in `outputs/analysis.csv`. View `calibration.png`, `.svg` and `.pdf`; annotation limits may omit labels, never records. The original run found six candidates/six regions, all six positive, and displayed four of six labels in its PNG. Searching both signs in this positive-only result does not validate acquired negative signals. No assignment, concentration or resolved-volume accuracy is established.

The session step saves/reopens the applied recipe and literal fitted phase, then runs the generated workflow through the installed CLI. **After that CLI run**, `check-cli-replay.py` compares the actual new output with the applied result: dtype, shape, both quadratures, every sample, digest, exact serialized axes and complete metadata/provenance. Keep `outputs/exact-cli-replay.json`; the following CLI0/0 comparison alone is not the exact-axis check. Original Stage2 evidence passed that complete check for float64 shape(2,65536), with no phase re-estimation.

If autophase explicitly refused, **skip normal Stage2 and30**. Keep the error and unphased plots; optionally run the separate diagnostic session/replay file:

```sh
cd "$RUN"
(set -C; sh "$TUTORIAL/31-diagnostic-session-replay.sh" > outputs/diagnostic-session.stdout.log 2> outputs/diagnostic-session.stderr.log)
```

That branch saves/replays the four preprocessing operations without phase, peaks or areas and performs the same post-CLI exact check against the unphased file. The actual ASU run did not refuse phase. A separately labelled constructed refusal-entry control exercised this diagnostic-only session and exact replay path; it is not evidence of an observed acquired refusal or phase acceptance.

## Pause, resume and reuse the session

Keep the source example folder and WORK/RUN paths. In a new terminal, restore REPO/TUTORIAL/WORK and the PY/SF/B3SRC/RUN exports above, then `cd "$RUN"`. A completed Stage1 resumes at Stage2; do not rerun preprocessing/autophase. A completed session is opened directly rather than recreated. Scripts refuse existing outputs; preserve failed/partial folders and use a new RUN and new log names for a complete retry.

For an optional desktop view, add the GUI in the same default installation:

```sh
"$PY" "$WORK/install-spinflow.py" --gui --plot
"$SF" gui
```

Choose **Open saved sessionâ€¦**, then the existing `session` folder (`session-diagnostic` for the unphased branch). Inspect **Current**, channels, relative-Hz axes, applied history and linked reports. Choose **Save current resultâ€¦** for a new standalone native file, or **Save applied sessionâ€¦** for a new folder such as `gui-session-01`. Close/reopen that session. **Save runnable workflowâ€¦** creates a separate workflow folder; run its workflow with the same CLI, then use the supplied exact checker on the actual output path declared in that workflow and the corresponding applied file. Use new receipt/output paths.

View the saved CLI/API reports to preserve this tutorial's exact analysis settings. Fresh GUI picking exposes different controls/defaults and integration uses manually entered bounds; new GUI analysis is exploration, not a reproduction of the fixed8Hz/max20/baseline-none/Â±2Hz lane. Opening a standalone processed file also does not recreate the raw-source recipe/session. The [session guide](processing-session.md) explains the existing actions. An automated installed xcb check opened the fixed session, viewed Source in its actual time units and Current real/imaginary in relative Hz, saved and closed the app, then reopened it in a fresh process and exported a workflow that passed the complete exact CLI check. This used Workbench controller actions and actual view controls; it does not establish mouse/menu coverage or physical-human acceptance. See [Stage22](../stage22-results.md) for the bounded runtime and retained limits.

Processing, source receipts, both quadratures and provenance remain in native files/sessions. A figure's real-channel projection is intentional. Sharing a session can share its original raw snapshot and local paths; review provenance/privacy first. The familiar calibration example and its engineering replay do not repair S19's six failed targets, establish original-program spectral parity, or change human acceptance from UNVERIFIED.
