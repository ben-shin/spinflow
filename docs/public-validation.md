# Validation and limits

SpinFlow's processing contract is described in [numerical conventions](numerical-conventions.md), [data model](data-model.md), and [format profiles](formats.md). The test suite includes independent direct-DFT and analytical fixtures, native versus NumPy comparisons, codec failures and read-back checks, CLI workflows, and optional GUI integration.

## Linux evidence

The tested source was the staged Git tree `282a6b4e2c95bb5f5bcc950e3306707e1f0c4cab`. It was archived and built in an Ubuntu 24.04.5 x86-64 guest with Linux 6.8, glibc 2.39, GCC 13.3, and CPython 3.12.3 under QEMU TCG software emulation. The build produced a native `spinflow-0.1.0-cp312-cp312-linux_x86_64.whl` (SHA-256 `55fb0d89c88bf29c8a475c71a7249a4deeaf74d86ed6c3c7495b3a4ec7390075`) and `spinflow-0.1.0.tar.gz` (SHA-256 `380d34fb60e3abbf94ee2f8c6c5e919ce97df2ba1d27c8ed4e954153eeb654f8`). A wheel was also built from the source distribution. The Linux bootstrap and in-place source extension helper completed.

In a fresh virtual environment with the Linux wheel installed, `pip check`, the installed-package CLI/native smoke test, and a GUI-inclusive test run passed **153 tests and 97 subtests**. The tests were copied away from a sibling `src` directory, and both `nmrnext` and its compiled `_native` extension resolved inside the installed environment. Twenty serial launches of the original real-Qt CLI path exited cleanly after a shutdown fix. A separate fresh headless wheel environment passed **139 tests and 85 subtests**, with exactly three real-Qt CLI tests deselected.

The source checkout was also tested with its matching in-place native extension. After `bash tools/bootstrap.sh --gui --test` and `.venv/bin/python tools/build_native.py`, its complete offscreen Qt suite passed **153 tests and 97 subtests**. A separate Qt-free Python 3.12 environment reused that helper-built source extension and passed the headless selection `--ignore=tests/gui -k 'not test_gui_'`: **139 tests and 85 subtests**, with exactly three real-Qt CLI tests deselected. These source runs used the same opt-in QEMU timeout factor described below.

The emulated source-suite commands were:

```bash
SPINFLOW_TEST_TIMEOUT_FACTOR=3 QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
SPINFLOW_TEST_TIMEOUT_FACTOR=3 .venv/bin/python -m pytest -q --ignore=tests/gui -k 'not test_gui_'
```

These QEMU runs set `SPINFLOW_TEST_TIMEOUT_FACTOR=3` for three child-process startup guards that exceeded their original 3- or 4-second limits under emulation. The factor is an opt-in test-harness setting; it does not alter processing, parser behavior, or numerical acceptance limits. With the variable unset, the original deadlines remain in force, including in the configured GitHub CI workflow. The first unadjusted QEMU run had 147 passes and four failures: three startup timeouts and an intermittent Qt shutdown crash. The Qt lifecycle fix and repeated launch check addressed the crash; the subsequent installed-wheel runs passed with the stated timeout factor. This is not evidence that the complete suite passes under QEMU with default deadlines.

The earlier Windows x64/Python 3.13 preview passed 151 tests and 97 subtests after independent defect review; that is separate historical evidence. The [first GitHub Actions run](https://github.com/ben-shin/spinflow/actions/runs/40766518847) for commit `271f0f5ad830a13afea9d0f2666791c75527ae7b` completed successfully. On Ubuntu 24.04, the Python 3.12.15 and 3.13.17 headless jobs each passed **139 tests and 85 subtests** with three real-Qt CLI tests deselected; both built a native wheel and source distribution and passed isolated installed-package smoke checks. The Python 3.13.17 Qt offscreen job passed the complete source suite: **153 tests and 97 subtests**. That workflow did not set the QEMU-only timeout factor, so the original test deadlines applied. These results are specific to that GitHub runner and revision. Offscreen Qt checks do not establish physical desktop behavior or researcher usability; neither CI nor the emulated tests establish representative bare-metal performance.

The numerical fixtures are synthetic, fully sampled mathematical examples. They do not prove correctness for real vendor acquisitions or parity with NMRPipe. The Bruker raw 1D profile has synthetic layout checks; unknown or uncorrected digital filtering prevents FFT. Processed real NMRPipe/UCSF files are read back with the same nmrglue codec family plus byte-level tests, but have not been reopened in independent end-user applications. Lossy export requires explicit acknowledgment. GUI tests are automated; researcher usability sessions have not been completed.

The prior Windows benchmark is not a Linux performance claim. No NMRPipe runtime comparison, general speed ranking, hard process-memory bound, or out-of-core capability is established. See the [roadmap](../ROADMAP.md) for the validation still needed.
