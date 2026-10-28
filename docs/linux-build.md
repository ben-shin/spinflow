# Linux source build

SpinFlow is currently an experimental processing workbench. Linux is the primary build target. The Python import package and original CLI remain `nmrnext`; `spinflow` is a second CLI name for the same program.

## Requirements

- CPython 3.12 or newer with `venv` and development headers
- A C++20 compiler (`g++` or `clang++`)
- Internet access for the initial Python package installation
- An x86-64 or ARM64 Linux environment with wheels available for the pinned NumPy, SciPy, h5py, nmrglue and optional Qt versions

On Ubuntu 24.04, the system packages are typically `python3-venv`, `python3-dev`, and `build-essential`. Install them with your usual package manager before running the script if they are missing. The script does not modify system packages.

For a minimal X11 desktop, Qt's `xcb` plugin may also need `libgl1`, `libegl1`, `libopengl0`, `libxkbcommon0`, `libxkbcommon-x11-0`, `libxcb-cursor0`, `libxcb-icccm4`, `libxcb-keysyms1`, `libxcb-shape0`, `libxcb-xinerama0`, `libxcb-xkb1`, `libdbus-1-3`, and `fonts-dejavu-core`. The Ubuntu X11 validation guest has this package set. A regular desktop installation may already provide them; Wayland has not been validated directly.

From the repository root:

```bash
bash tools/bootstrap.sh
.venv/bin/spinflow doctor
.venv/bin/spinflow formats
```

The bootstrap creates `.venv`, installs the project and its pinned runtime dependencies, compiles the C++ PocketFFT extension, and checks the resulting environment. It installs a normal local package; run it again after changing source. Select a Python version with `PYTHON=python3.13 bash tools/bootstrap.sh` if several versions are installed.

For normal desktop use of the optional GUI:

```bash
bash tools/bootstrap.sh --gui
.venv/bin/spinflow gui
```

For GUI development and the source test dependencies (pytest, pybind11, CMake and Ninja):

```bash
bash tools/bootstrap.sh --gui --test
.venv/bin/python tools/build_native.py
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/spinflow gui
```

The source test suite imports `src/nmrnext` directly. `tools/build_native.py` places a matching local extension there for those tests. The installed package already contains its own native extension and does not need this helper. GUI use requires a working graphical desktop; headless CI can set `QT_QPA_PLATFORM=offscreen` for its GUI tests.

## Build release artifacts

```bash
.venv/bin/python -m pip install 'build==1.4.0'
.venv/bin/python -m build
```

The source distribution contains the Python and C++ sources plus the PocketFFT header and license. Building a wheel compiles the native extension for the current Python and Linux platform, so the resulting wheel has a platform tag. Do not upload a wheel built on one Linux host as a generic wheel for other Linux systems; binary compatibility and auditwheel policy need separate validation before broad distribution. The repository can be published as source independently of wheel publication.

To check an installed wheel rather than the source tree, use a fresh virtual environment and run the smoke command from outside this checkout, without `PYTHONPATH`:

```bash
python3 -m venv /tmp/spinflow-wheel-check
/tmp/spinflow-wheel-check/bin/python -m pip install dist/spinflow-*.whl
cd /tmp
/tmp/spinflow-wheel-check/bin/python -I -c 'import nmrnext, nmrnext._native as native; print(nmrnext.__file__); print(native.__file__)'
/tmp/spinflow-wheel-check/bin/spinflow doctor
```

The `doctor` command reports backend availability. Native output remains experimental and should be checked against the reference tests before scientific use.
