# SpinFlow

SpinFlow is an experimental, open source NMR processing workbench. It provides a headless command line and an optional Qt spectrum viewer for explicitly synthetic 1D and 2D workflows. The Python import package and command `nmrnext` retain their existing names; `spinflow` is also a command entry point. The original project code is [MIT licensed](LICENSE), with separate [third-party notices](THIRD_PARTY_NOTICES).

**Scientific status:** this is an early processing slice, not a validated replacement for NMRPipe or a laboratory processing system. Real acquisition workflows, NMRPipe numerical parity, and independent reopening in external consumers remain unverified. See [scope](docs/scope.md), [validation](docs/public-validation.md), and the [roadmap](ROADMAP.md).

## Install on Linux

Ubuntu 24.04 and Python 3.12 or 3.13 are the initial Linux build targets. From a checkout:

```bash
bash tools/bootstrap.sh --test
.venv/bin/python -m nmrnext doctor
.venv/bin/python -m nmrnext formats
```

`bootstrap.sh` creates a local environment, installs the project, and builds the native C++/PocketFFT extension. Setup downloads dependencies; processing itself runs offline. For the optional GUI, use `bash tools/bootstrap.sh --gui --test` and then `.venv/bin/python -m nmrnext gui` on a desktop with a display. See the [Linux build guide](docs/linux-build.md) for system packages, manual installation, wheel builds, and troubleshooting. The native backend must be reported available by `doctor` before choosing it in a recipe.

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

Operations currently include window, zero fill, FFT, phase, crop, reverse, and transpose. Planning validates an entire JSON recipe before numerical execution. The optional `numpy` backend is an explicitly selected experimental bootstrap; an unavailable requested native backend fails rather than silently falling back. The memory option estimates working arrays and is not a process RSS limit or out-of-core execution.

## File interchange

Native HDF5 retains the numeric channels, axes, metadata, and provenance. Narrow processed real NMRPipe 1D/2D and UCSF 2D profiles are available; export projects to one real float32 channel and reports losses. Bruker raw 1D import is controlled and experimental. Unknown or uncorrected digital filtering blocks FFT. See the [exact format profiles](docs/formats.md), [data model](docs/data-model.md), and [numerical conventions](docs/numerical-conventions.md).

Use `convert ... --dry-run` to inspect losses before export. Acknowledgment is required for every permitted loss; unrepresentable values or headers fail. Import never executes acquisition scripts, and output files are not overwritten by default.

## Develop

Headless source checkout:

```bash
bash tools/bootstrap.sh --test
.venv/bin/python tools/build_native.py
.venv/bin/python -m pytest -q --ignore=tests/gui -k 'not test_gui_'
```

Complete source suite with Qt offscreen:

```bash
bash tools/bootstrap.sh --gui --test
.venv/bin/python tools/build_native.py
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

The source tests import `src/nmrnext` directly, so they need the matching in-place extension from `tools/build_native.py`. Desktop GUI use is described in the [workbench guide](docs/gui.md). The [architecture](docs/architecture.md), [validation record](docs/public-validation.md), and [contribution guide](CONTRIBUTING.md) describe the implementation and evidence limits. On Ubuntu 24.04/Python 3.12 under software emulation, both the source checkout and a fresh installed Linux wheel passed 153 GUI-inclusive tests and 97 subtests with an explicit timeout allowance for three subprocess-startup checks; corresponding headless runs passed 139 tests and 85 subtests. GitHub CI and Linux Python 3.13 have not run yet. NUS reconstruction, automatic phase and baseline correction, fitting, NEF, and script migration are future work.
