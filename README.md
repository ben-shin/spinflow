# SpinFlow

SpinFlow is an experimental, open source NMR processing workbench. It provides a headless command line and an optional Qt spectrum viewer for synthetic 1D/2D workflows and bounded public Bruker and Varian/Agilent raw acquisition paths. The Python import package and command `nmrnext` retain their existing names; `spinflow` is also a command entry point. The [support registry](compatibility/support.json) identifies implemented source profiles and the proposed [finite processing support contract](docs/support-contract.md). The original project code is [MIT licensed](LICENSE), with separate [third-party notices](THIRD_PARTY_NOTICES).

**Scientific status:** this is an early processing slice, not a validated replacement for NMRPipe or a laboratory processing system. One public Bruker 1D acquisition has an archived peak-position check; two public 2D acquisitions add bounded both-axis automatic phase and fixed peak/relative-volume checks. One previously studied public Varian protein case has a bounded comparison with NMRPipe 13 and actual NMRDraw/Sparky reopening; broader real workflows, general parity, concentration accuracy and other independent reopening remain unverified. See [scope](docs/scope.md), [validation](docs/public-validation.md), and the [roadmap](ROADMAP.md).

Earlier Linux checks under software emulation passed the installed-package suite, public-data and automated virtual-desktop workflows, and a CPython 3.12 release-installer preflight. The [validation record](docs/public-validation.md) gives the exact tested candidates and limitations. The immutable published v0.3.0b1 beta and its [dated evidence](docs/v1-candidate-results.md) remain distinct from the focused [0.3.0b2 qualification](docs/v1-qualification-results.md). Version 0.2.0 remains an older experimental prerelease.

## Install on Linux

For Ubuntu 24.04 x86-64 with CPython 3.12 or 3.13 and its `venv`/`ensurepip` support, use the versioned installer **after the v0.3.0b2 assets are published and verified**:

```bash
curl -fL -o install-spinflow.py https://github.com/ben-shin/spinflow/releases/download/v0.3.0b2/install-spinflow.py
python3 install-spinflow.py --version 0.3.0b2
"$HOME/.local/share/spinflow/0.3.0b2/bin/spinflow" doctor
```

Installation needs internet access to obtain the checked release assets and pinned Python dependencies. The matching prebuilt native wheel does not need a compiler. The versioned wheels target Ubuntu 24.04 x86-64 for these Python versions; they are not general manylinux wheels. To include the optional desktop and figure support, rerun the installer with `--gui --plot`, then run `"$HOME/.local/share/spinflow/0.3.0b2/bin/spinflow" gui` on a desktop. Optional extras already installed are retained on later runs. Use `--prefix PATH` for another installation location; an existing installation must match the selected version and Python. See the [beginner tutorials](docs/tutorials/index.md) for the first spectrum and public-data workflows.

After installation, this command works offline and creates a new folder containing a synthetic spectrum, processing recipe and analysis reports:

```bash
"$HOME/.local/share/spinflow/0.3.0b2/bin/spinflow" first-spectrum first-spectrum
```

The folder must not already exist. If a run fails after creating it, choose a new folder name before retrying. The synthetic example illustrates the workflow and is not evidence of experimental accuracy.

The published [v0.3.0b1 release](https://github.com/ben-shin/spinflow/releases/tag/v0.3.0b1) remains available while b2 is under review; [v0.2.0](https://github.com/ben-shin/spinflow/releases/tag/v0.2.0) is older. Use a different prefix for each installed version.

## Install from source on Linux

The header-only planning, GUI budget control, `discard_imaginary`, explicit spectral-width calibration, execution/cache improvements and bounded native-file processing described below are changes since the immutable v0.2.0 archive. Use a matching 0.3 beta source checkout for these features. The v0.2.0 release installer cannot supply them.

Ubuntu 24.04 and Python 3.12 or 3.13 are the initial Linux build targets. From a checkout:

```bash
bash tools/bootstrap.sh
.venv/bin/python -m nmrnext doctor
.venv/bin/python -m nmrnext formats
```

`bootstrap.sh` creates a local environment, installs the project, and builds the native C++/PocketFFT extension. Setup downloads dependencies; processing itself runs offline. For the optional GUI, use `bash tools/bootstrap.sh --gui` and then `.venv/bin/spinflow gui` on a desktop with a display. Add `--plot` for optional Matplotlib SVG/PDF/PNG export without making it a core dependency. Follow the [Linux user-testing guide](docs/linux-user-testing.md) for a synthetic walkthrough. See the [Linux build guide](docs/linux-build.md) for system packages, manual installation, wheel builds, and troubleshooting. The native backend must be reported available by `doctor` before choosing it in a recipe.

The [processing-session walkthrough](docs/tutorials/processing-session.md) covers ordered recipe editing, full-size bounded region analysis, applied-session save/reopen and CLI replay in the 0.3 beta line. The [Linux acceptance runner](docs/tutorials/linux-acceptance.md) prepares a repeatable installed-package check and a blank human record; it is not a completed researcher session.

## Run a synthetic 2D workflow

The included recipe uses paths relative to `examples/` and refuses to overwrite existing output. Run these commands on a fresh checkout, or choose new paths after a prior run:

```bash
.venv/bin/python -m nmrnext demo examples/demo-raw.h5 --dimensions 2 --points 64 256
.venv/bin/python -m nmrnext plan examples/process-2d.json --explain
.venv/bin/python -m nmrnext run examples/process-2d.json --backend native --threads 2 --memory 256MiB
.venv/bin/python -m nmrnext inspect examples/demo-processed.h5 --json
.venv/bin/python -m nmrnext compare examples/demo-processed.h5 examples/demo-processed.h5 --json
```

The self-comparison checks a storage and CLI round trip; it does not establish scientific accuracy. `examples/process-1d.json` provides a 1D path after generating `examples/demo-1d-raw.h5 --dimensions 1 --points 256`.

Operations currently include explicit Bruker digital-filter correction and frequency referencing, window, zero fill, FFT, phase, explicit `discard_imaginary` on a frequency axis, crop, reverse, and transpose. The discard removes the selected imaginary quadratures with recorded loss; it is not magnitude conversion. An optional `reference.spectral_width_hz` explicitly calibrates the current grid width while retaining samples and acquisition dwell. For supported binary profiles, `plan` validates the recipe using bounded header metadata before numeric payload reads; `run` repeats that preflight before loading and validating samples. Header planning does not validate sample content. JCAMP-DX cannot be planned from a header alone; convert it to native format first. The eager GUI and CLI paths default to a 256 MiB processing working-array budget, which can be explicitly raised; the separate `--block-memory` mode below processes native files in bounded slabs. Neither budget is a process RSS cap. The optional `numpy` backend is an explicitly selected experimental bootstrap; an unavailable requested native backend fails rather than silently falling back. See the [first laboratory-workflow protocol](docs/laboratory-workflow-protocol.md) and its [selected results](docs/laboratory-workflow-results.md) for the selected comparison and its gates.

## Public Bruker 1D/2D and analysis

The [bounded real-data walkthrough](docs/real-bruker-and-analysis.md) fetches nine hash-pinned BMRB files to an ignored local folder, runs an explicit correction/FFT/reference recipe, and shows headless `autophase`, signed 1D/2D `peaks` and `integrate` reports, and optional `figure` export. The [public 2D guide](docs/real-bruker-2d.md) adds hash-pinned HSQC/TOCSY ser imports, fixed recipes and both-axis full-hypercomplex automatic phase with identifiability and gauge flags. The 25 acquired files stay outside Git. The predeclared raw/reference volume-ratio gate is 30%; observed errors are 2.45–13.32%, without an absolute amplitude or concentration claim. Peak-derived region volumes are signed relative intensity·Hz or intensity·Hz² with inspectable bounds and flags; they are not concentrations or deconvolved assignments. The GUI exposes bounded phase, peak finding, entered-region integration and optional figures without changing the headless core.

The b2 source candidate also supports **Save scientific figure** from an applied, selected Current region. The saved figure uses the bounded local dataset with compatible local peak/integral annotations; the large parent remains unchanged. A source-host check produced SVG, PNG and PDF from a 201×201 region of an 8,388,608-point result, with 286 local report peaks and a signed integral. That check used Python 0.3.0b2 with an existing host native 0.3.0b1 binary; installed b2 validation is separate. The viewer still samples large displays, and adaptive viewport zoom remains future work. See [qualification results](docs/v1-qualification-results.md) and the [workbench guide](docs/gui.md).

The [new Bruker qualification protocol](docs/bruker-qualification-protocol.md) freezes a distinct public acquisition, 18 deposited 1D targets and four independent 2D reference regions before candidate numerical execution. Its [results record](docs/bruker-qualification-results.md) distinguishes header/preparation checks from a future installed b2 run; no new scientific PASS or general weak/strong sensitivity claim follows from preparation alone.

## Measured source performance

The [Stage 16 results](docs/performance-results.md) compare frozen baseline and improved source builds on one previously studied public Varian 2D protein case. They report separate fresh-command, warm processing, import/export and interactive phase-prefix distributions, CPU time and qualified process memory measurements. The [protocol](docs/performance-protocol.md) preserves precision, quadratures, padding, provenance, scientific tolerances and thread policy; all 63 measured pairs agree, and the final outputs were reopened in pinned NMRDraw and Sparky applications.

These improvements are not in the immutable v0.2.0 release. The results identify the exact measured archive and wheel separately from later documentation commits. They do not establish general 3D/4D support, bare-metal performance or NMRPipe speed parity.

## Bounded native 3D/4D file processing

The [bounded-processing tutorial](docs/tutorials/bounded-block-processing.md) shows the separate Python file-to-file API and `plan/run --block-memory 64MiB` mode. It processes uncompressed, unfiltered native schema-1 HDF5 files with one through four physical axes, keeping each transformed axis complete while tiling the others. The [protocol](docs/block-processing-protocol.md) defines working-memory accounting, finite validation, source identity, temporary-disk refusals and atomic output. Existing Dataset, eager CLI and GUI behavior is unchanged.

This feature uses the existing core dependencies and native FFT. It does not add raw vendor 3D/4D import, global analysis, GUI lazy viewing or high-dimensional NMRPipe/UCSF export. The [Stage 17 results](docs/block-processing-results.md) retain all 49 measured pairs and the matching acquired outputs. On the selected full 4D case, bounded execution reduced recorded child-lifetime peak memory and was 2.377 times slower at a 64 MiB working-array policy. Those RSS measurements include the inherited pre-exec launcher floor; they do not establish incremental memory use or small-case scaling. The immutable v0.2.0 release does not include this mode.

## File interchange

Native HDF5 retains the numeric channels, axes, metadata, and provenance. Narrow processed real NMRPipe 1D/2D and UCSF 2D profiles are available; export projects to one real float32 channel and reports losses. Controlled Bruker raw 1D/2D and [Varian/Agilent 1D and selected States 2D](docs/real-varian.md) imports are experimental. The Varian profile requires an explicit reference with `frequency_scale=-1` and a known anchor for physical ppm. A narrow experimental real 1D JCAMP-DX Hz/AFFN reader has synthetic evidence only. Bruker FFT requires explicit correction when a supported positive `GRPDLY` is recorded; unknown/zero delays and broader vendor profiles remain blocked. See the [exact format profiles](docs/formats.md), [data model](docs/data-model.md), and [numerical conventions](docs/numerical-conventions.md).

[Explicit analysis regions](docs/analysis-regions.md) support phase anchors, noise estimation and a reference-sample baseline in both CLI and GUI. They are user-selected, digest-bound inputs; inspect the reports and the spectrum. On one difficult public protein spectrum the masked and default picker each found all five predeclared boxes; automatic phase refused that spectrum under the fixed protocol. These checks do not establish general peak recovery or concentration accuracy.

Use `convert ... --dry-run` to inspect losses before export. Acknowledgment is required for every permitted loss; unrepresentable values or headers fail. Import never executes acquisition scripts, and output files are not overwritten by default.

## Develop

Headless source checkout:

```bash
bash tools/bootstrap.sh --test
.venv/bin/python tools/build_native.py
.venv/bin/python -m pytest -q --ignore=tests/gui --ignore=tests/plot --ignore=tests/cli/test_plot_cli.py -k 'not test_gui_'
```

Complete source suite with Qt offscreen:

```bash
bash tools/bootstrap.sh --gui --plot --test
.venv/bin/python tools/build_native.py
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

The source tests import `src/nmrnext` directly, so they need the matching in-place extension from `tools/build_native.py`. Desktop GUI use is described in the [workbench guide](docs/gui.md). The [architecture](docs/architecture.md), [validation record](docs/public-validation.md), and [contribution guide](CONTRIBUTING.md) describe the implementation and evidence limits. NUS reconstruction, broader automatic phase, automatic baseline correction, fitting, NEF and script migration remain future work.
