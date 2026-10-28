# Contributing to SpinFlow

SpinFlow is an experimental scientific tool. Small, focused changes with a clear test case are welcome. For a behavior change, describe the input profile, expected numerical or file behavior, and any unsupported cases.

## Develop on Linux

Use Python 3.12 or 3.13 on Ubuntu 24.04:

```bash
bash tools/bootstrap.sh --test
.venv/bin/python tools/build_native.py
.venv/bin/python -m pytest -q --ignore=tests/gui --ignore=tests/plot --ignore=tests/cli/test_plot_cli.py -k 'not test_gui_'
```

For the complete source suite with Qt and optional Matplotlib:

```bash
bash tools/bootstrap.sh --gui --plot --test
.venv/bin/python tools/build_native.py
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

The source suite imports `src/nmrnext` directly, so build its local native extension first. Core headless commands do not require Qt or Matplotlib; figure export needs the separate `plot` extra. Desktop GUI launch uses `.venv/bin/spinflow gui` on a system with a display. See [Linux build instructions](docs/linux-build.md) for manual setup and wheel creation.

Numerical changes need independent expected values or a documented analytical argument. Do not use the production FFT or a processed output to generate its own expected answer. For file adapters, add malformed-input and round-trip tests and state whether the reader is independent of the writer. Existing outputs are refused by default; keep that behavior in examples and tests.

Please keep generated spectra, virtual environments, binaries, downloaded toolchains, benchmark result files, and local work records out of commits. The repository contains the vendored PocketFFT source and its license. Preserve dependency notices when changing the dependency or vendored-source set.

Reports about real acquisition data should identify the instrument or file profile and the observable failure, while omitting private sample data unless you have the right to share it. Current scientific limits are in [validation](docs/public-validation.md) and future work in the [roadmap](ROADMAP.md).
