# Researcher handoff: SpinFlow 0.4.0b1

**Purpose:** a short, local Ubuntu researcher trial of published experimental beta 0.4.0b1. This is a handoff, not completed human acceptance.

## Candidate and installation

SpinFlow 0.4.0b1 is published as an experimental beta at [GitHub release v0.4.0b1](https://github.com/ben-shin/spinflow/releases/tag/v0.4.0b1) (release ID `443486663`; GitHub marks it as a prerelease and it is not the latest release). The tag points to source `0e3f896255a98dd766d73fa534951bf1ef6de725`; the six public assets and their SHA256SUMS/server digests were verified against the release. This is not a stable release and is marked as a GitHub prerelease, not the latest release. The previous published beta is 0.3.0b3. Keep each version in its own prefix.

### Recommended: install the prebuilt Linux release

For Ubuntu 24.04 x86-64, use ordinary CPython 3.12 or 3.13 with `venv`/`ensurepip` available. The prebuilt release wheels need no compiler. A minimal X11 desktop may need the Qt runtime libraries listed in the [Linux build requirements](https://github.com/ben-shin/spinflow/blob/0e3f896255a98dd766d73fa534951bf1ef6de725/docs/linux-build.md#requirements); a regular desktop may already provide them. The release installer does not change system packages. Installation downloads the release wheel and Python dependencies, so setup needs internet. Once installed, processing can run offline.

Download the installer, verify its published SHA256 before running it, and install the GUI and figure extras into a versioned prefix separate from 0.3.0b3:

```bash
mkdir -p "$HOME/Downloads/spinflow-0.4.0b1"
cd "$HOME/Downloads/spinflow-0.4.0b1"
curl -fL -o install-spinflow.py https://github.com/ben-shin/spinflow/releases/download/v0.4.0b1/install-spinflow.py
printf '%s  %s\n' '4edeea8b08f667795fe0a1323cdcd79bd20f982af5f467d955c2577578666166' install-spinflow.py | sha256sum -c -
python3 install-spinflow.py --version 0.4.0b1 --prefix "$HOME/.local/share/spinflow/0.4.0b1" --gui --plot
source "$HOME/.local/share/spinflow/0.4.0b1/bin/activate"
spinflow --version
spinflow doctor
spinflow gui
```

Use `python3.13` instead of `python3` if CPython 3.13 is the selected interpreter. Check that `--version` reports `0.4.0b1` and `doctor` reports `backends.native.available: true`. The installer verifies the selected wheel against the release `SHA256SUMS` before installation and confirms the installed version/native backend. Activate the chosen release prefix before running CLI commands or following linked guide examples: `source "$HOME/.local/share/spinflow/0.4.0b1/bin/activate"`. In a new terminal, activate that same prefix again. Then bare `spinflow` commands resolve to the selected release. The installer route does not build from source and does not require a compiler.

### Separate source-development route

To inspect or modify source, use a fresh checkout and its own `.venv`; this is a developer build, separate from the published installation and prefix. It downloads dependencies and compiles the native extension with a C++20 compiler:

```bash
git clone https://github.com/ben-shin/spinflow.git spinflow-040b1-source
cd spinflow-040b1-source
git fetch origin 0e3f896255a98dd766d73fa534951bf1ef6de725
git checkout --detach FETCH_HEAD
bash tools/bootstrap.sh --gui --plot
source .venv/bin/activate
spinflow --version
spinflow doctor
spinflow gui
```

Keep that source `.venv` active for linked source-tree examples; reactivate it in a new terminal. Use the active environment for every bare `spinflow` example. When following linked workflow guides from the release install, skip their source-bootstrap instructions and use the activated 0.4.0b1 prefix for CLI commands; adapt any `.venv/bin/spinflow` example to the active `spinflow` command. Record the selected install route, release/source identity, OS, Python, Qt, native/backend versions, desktop session (X11/Wayland), CPU/RAM, and output directory in the blank form. A successful install or automated check is not human acceptance.
## Seven-step trial

Use permitted local files, new output paths and a copy of any working data. Preserve originals and hashes. Follow the pinned [Linux user-testing walkthrough](https://github.com/ben-shin/spinflow/blob/0e3f896255a98dd766d73fa534951bf1ef6de725/docs/linux-user-testing.md), [Batch guide](https://github.com/ben-shin/spinflow/blob/0e3f896255a98dd766d73fa534951bf1ef6de725/docs/batch-workflows.md) and [processing/session walkthrough](https://github.com/ben-shin/spinflow/blob/0e3f896255a98dd766d73fa534951bf1ef6de725/docs/tutorials/processing-session.md); note any difference from their instructions.

1. **Install and identify.** Run the commands for the chosen release-wheel or source-build route above. Keep the `--version` and `doctor` output. Stop and record a failure if the package version or native backend does not match.
2. **Raw FID and recipe.** Choose only an acquisition profile explicitly listed in the guide (for example, its documented Bruker or Varian walkthrough). Inspect time axes/quadratures. Review windowing, zero fill, FFT and phase; compare preview with Processed output, then save a runnable workflow from the applied result. Unknown references remain in Hz until you supply a justified anchor. Record refusal/unsupported profiles as such.
3. **2D view.** Open or process a 2D spectrum. Inspect positive/negative contour rings, zoom with right-button drag and release, pan, then right-click **View All**. Check orientation, units and the 192×192 block-preview/possible feature-merging note; zoom is display-only and does not establish precision or scientific correctness.
4. **Guarded NMRPipe recipe reuse.** Import a command file only if it uses the documented supported subset and matches the declared input profile. Review the importer’s line-level refusals and operation mapping, save/reopen the manifest, plan, and apply it to new compatible files. This is not shell execution or full NMRPipe compatibility: unsupported flags or semantics must be refused, not worked around by changing the script silently.
5. **Two-file batch and fixed labels.** Add two compatible files with distinct item IDs and a fresh output directory. Plan and apply the same saved recipe. Add/open a label template only after checking its reference, component, sign, units, fixed search/integration regions, and per-file axis maps. Review item and assignment statuses, heights, and signed relative volumes in intensity·Hz (1D) or intensity·Hz² (2D). Ambiguous/not-found results are not zero; relative volumes are not concentrations or automatic chemical assignments.
6. **Figures, exports and external reopen.** Save a figure and native output. For NMRPipe/UCSF export, inspect the exact loss report and acknowledge only accepted losses. Reopen exported output in the intended independent application, record that application/version and inspect coordinates. Writing or reopening in SpinFlow itself is not an independent consumer check.
7. **Session and older recipe.** Save and reopen a session; confirm the applied recipe/source and current result return. For the source-development route only, if the checkout includes the existing schema-1 recipe fixture, run `spinflow plan PATH` then `spinflow run PATH` with a fresh copied fixture/output location, using the chosen active release or source environment; report explicit compatibility refusal as a result. Do not edit an old recipe to force it through. Record failures, cancellation, data preservation and deviations.

Do not infer general vendor support, complete NMRPipe processing-script parity, chemical assignment, stable/v1 status, or success on other machines. Historical S19 testing had six failures among eighteen target checks; this session does not erase or reclassify them. Human/scientific acceptance starts **NOT RUN** and stays open until the researcher completes the form and reviews expected results independently.

## Published release status

The experimental beta is publicly available at [release v0.4.0b1](https://github.com/ben-shin/spinflow/releases/tag/v0.4.0b1). The release contains CPython 3.12 and 3.13 Ubuntu 24.04 x86-64 wheels, a source archive, installer, `SHA256SUMS`, and build manifest. For current asset availability, use the release page. The public release does not change the documented scientific and compatibility limits above or complete human acceptance.


