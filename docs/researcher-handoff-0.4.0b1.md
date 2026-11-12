# Researcher handoff: SpinFlow 0.4.0b1

**Purpose:** a short, local Ubuntu researcher trial of the experimental development candidate. This is a handoff, not completed human acceptance.

## Candidate and installation

Pinned source: `0e3f896255a98dd766d73fa534951bf1ef6de725`. Associated GitHub Actions build: [43290423200](https://github.com/ben-shin/spinflow/actions/runs/43290423200). The public installed release remains experimental **0.3.0b3**; it predates the Batch workbench. The 0.4.0b1 build assets (CPython 3.12/3.13 Linux x86-64 wheels, source archive, installer, SHA256SUMS and build manifest) are retained in private `release-build-only/assets`; they are **not published downloadable release assets**. Use the pinned source-build route below for this handoff. Do not install over b3 or reuse its prefix.

From a new checkout/environment on Ubuntu 24.04 x86-64, Python 3.12 or 3.13:

```bash
git clone https://github.com/ben-shin/spinflow.git spinflow-040b1-human
cd spinflow-040b1-human
git fetch origin 0e3f896255a98dd766d73fa534951bf1ef6de725
git checkout --detach FETCH_HEAD
bash tools/bootstrap.sh --gui --plot
source .venv/bin/activate
spinflow --version
spinflow doctor
spinflow gui
```

Confirm version `0.4.0b1` and `backends.native.available: true`. Keep this shell activated while following the linked command examples; in a new terminal, run `source .venv/bin/activate` again before using bare `spinflow` commands. Record commit, OS, Python, Qt, native/backend versions, desktop session (X11/Wayland), CPU/RAM and output directory in the blank form. Dependency setup downloads packages; after setup, ordinary processing can run offline. This does not promise offline installation.

## Seven-step trial

Use permitted local files, new output paths and a copy of any working data. Preserve originals and hashes. Follow the pinned [Linux user-testing walkthrough](https://github.com/ben-shin/spinflow/blob/0e3f896255a98dd766d73fa534951bf1ef6de725/docs/linux-user-testing.md), [Batch guide](https://github.com/ben-shin/spinflow/blob/0e3f896255a98dd766d73fa534951bf1ef6de725/docs/batch-workflows.md) and [processing/session walkthrough](https://github.com/ben-shin/spinflow/blob/0e3f896255a98dd766d73fa534951bf1ef6de725/docs/tutorials/processing-session.md); note any difference from their instructions.

1. **Install and identify.** Run the commands above. Keep the `--version` and `doctor` output. Stop and record a failure if the package version or native backend does not match.
2. **Raw FID and recipe.** Choose only an acquisition profile explicitly listed in the guide (for example, its documented Bruker or Varian walkthrough). Inspect time axes/quadratures. Review windowing, zero fill, FFT and phase; compare preview with Processed output, then save a runnable workflow from the applied result. Unknown references remain in Hz until you supply a justified anchor. Record refusal/unsupported profiles as such.
3. **2D view.** Open or process a 2D spectrum. Inspect positive/negative contour rings, zoom with right-button drag and release, pan, then right-click **View All**. Check orientation, units and the 192×192 block-preview/possible feature-merging note; zoom is display-only and does not establish precision or scientific correctness.
4. **Guarded NMRPipe recipe reuse.** Import a command file only if it uses the documented supported subset and matches the declared input profile. Review the importer’s line-level refusals and operation mapping, save/reopen the manifest, plan, and apply it to new compatible files. This is not shell execution or full NMRPipe compatibility: unsupported flags or semantics must be refused, not worked around by changing the script silently.
5. **Two-file batch and fixed labels.** Add two compatible files with distinct item IDs and a fresh output directory. Plan and apply the same saved recipe. Add/open a label template only after checking its reference, component, sign, units, fixed search/integration regions, and per-file axis maps. Review item and assignment statuses, heights, and signed relative volumes in intensity·Hz (1D) or intensity·Hz² (2D). Ambiguous/not-found results are not zero; relative volumes are not concentrations or automatic chemical assignments.
6. **Figures, exports and external reopen.** Save a figure and native output. For NMRPipe/UCSF export, inspect the exact loss report and acknowledge only accepted losses. Reopen exported output in the intended independent application, record that application/version and inspect coordinates. Writing or reopening in SpinFlow itself is not an independent consumer check.
7. **Session and older recipe.** Save and reopen a session; confirm the applied recipe/source and current result return. If the checked-out tree includes the existing schema-1 recipe fixture, run `.venv/bin/spinflow plan PATH` then `.venv/bin/spinflow run PATH` with a fresh copied fixture/output location; report explicit compatibility refusal as a result. Do not edit an old recipe to force it through. Record failures, cancellation, data preservation and deviations.

Do not infer general vendor support, complete NMRPipe processing-script parity, chemical assignment, stable/v1 status, or success on other machines. Historical S19 testing had six failures among eighteen target checks; this session does not erase or reclassify them. Human/scientific acceptance starts **NOT RUN** and stays open until the researcher completes the form and reviews expected results independently.

## Draft asset description (for a future, separately authorized publication)

> **SpinFlow 0.4.0b1 — experimental development build for Ubuntu Linux x86-64.** Includes CPython 3.12 and 3.13 wheels, source archive and installer. Adds the Batch workflow and current experimental GUI. Build source: `0e3f896255a98dd766d73fa534951bf1ef6de725`; GitHub build `43290423200`. This is a beta candidate, not a stable release or a claim of scientific or full NMRPipe compatibility. Review documented supported profiles and export-loss reports before use. Install/setup requires network access; processing after setup can run offline. Preserve the input data and consult the testing guide.

This text is a draft only. Current assets remain private build outputs; do not describe them as publicly downloadable.

