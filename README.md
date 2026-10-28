# SpinFlow

SpinFlow is an experimental, open source NMR processing workbench. It provides a headless command line and an optional Qt spectrum viewer for synthetic 1D/2D workflows and a bounded public Bruker raw 1D path. The Python import package and command `nmrnext` retain their existing names; `spinflow` is also a command entry point. The original project code is [MIT licensed](LICENSE), with separate [third-party notices](THIRD_PARTY_NOTICES).

**Scientific status:** this is an early processing slice, not a validated replacement for NMRPipe or a laboratory processing system. One hash-pinned public Bruker 1D acquisition has a narrow correction and archived peak-position check; broader real workflows, NMRPipe numerical parity, quantitative integrals and independent reopening in external consumers remain unverified. See [scope](docs/scope.md), [validation](docs/public-validation.md), and the [roadmap](ROADMAP.md).

## Install on Linux

Ubuntu 24.04 and Python 3.12 or 3.13 are the initial Linux build targets. From a checkout:

```bash
bash tools/bootstrap.sh
.venv/bin/python -m nmrnext doctor
.venv/bin/python -m nmrnext formats
```

`bootstrap.sh` creates a local environment, installs the project, and builds the native C++/PocketFFT extension. Setup downloads dependencies; processing itself runs offline. For the optional GUI, use `bash tools/bootstrap.sh --gui` and then `.venv/bin/spinflow gui` on a desktop with a display. Add `--plot` for optional Matplotlib SVG/PDF/PNG export without making it a core dependency. Follow the [Linux user-testing guide](docs/linux-user-testing.md) for a synthetic walkthrough. See the [Linux build guide](docs/linux-build.md) for system packages, manual installation, wheel builds, and troubleshooting. The native backend must be reported available by `doctor` before choosing it in a recipe.

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

Operations currently include explicit Bruker digital-filter correction and frequency referencing, window, zero fill, FFT, phase, crop, reverse, and transpose. Planning validates an entire JSON recipe before numerical execution. The optional `numpy` backend is an explicitly selected experimental bootstrap; an unavailable requested native backend fails rather than silently falling back. The memory option estimates working arrays and is not a process RSS limit or out-of-core execution.

## Public Bruker 1D and analysis

The [bounded real-data walkthrough](docs/real-bruker-and-analysis.md) fetches nine hash-pinned BMRB files to an ignored local folder, runs an explicit correction/FFT/reference recipe, and shows headless `autophase`, signed 1D/2D `peaks` and `integrate` reports, and optional `figure` export. Automatic phase is limited to identifiable 1D complex lines. Peak-derived region volumes are signed relative intensity·Hz or intensity·Hz² with inspectable bounds and flags; they are not concentrations or deconvolved assignments. The GUI exposes bounded phase, peak finding, entered-region integration and optional figures without changing the headless core.

## File interchange

Native HDF5 retains the numeric channels, axes, metadata, and provenance. Narrow processed real NMRPipe 1D/2D and UCSF 2D profiles are available; export projects to one real float32 channel and reports losses. Bruker raw 1D import is controlled and experimental. FFT requires explicit correction when a supported positive `GRPDLY` is recorded; unknown/zero delays and broader vendor profiles remain blocked. See the [exact format profiles](docs/formats.md), [data model](docs/data-model.md), and [numerical conventions](docs/numerical-conventions.md).

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

The source tests import `src/nmrnext` directly, so they need the matching in-place extension from `tools/build_native.py`. Desktop GUI use is described in the [workbench guide](docs/gui.md). The [architecture](docs/architecture.md), [validation record](docs/public-validation.md), and [contribution guide](CONTRIBUTING.md) describe the implementation and evidence limits. The [last published Linux CI run](https://github.com/ben-shin/spinflow/actions/runs/40773797213) passed Ubuntu 24.04/Python 3.12 and 3.13 headless jobs plus a Python 3.13 offscreen Qt job for the prior user-testing revision. Stage 11 has separate archived Linux wheel, public-data, plotting and virtual-X11 checks; its new GitHub Actions workflow has not yet run. NUS reconstruction, 2D automatic phase, automatic baseline correction, fitting, NEF and script migration remain future work.
