# Linux batch validation · 9 November 2026

The experimental batch workflow is available for source testing on `codex/batch-workflows`. Five CI jobs passed at commit `43501bc299b495fcee21766db265a1a96e35be59`. They checked out that revision and exercised the source build and tests. A separate Linux run checked the installed package.

## Try it on Linux

Use a fresh checkout on Ubuntu 24.04 with CPython 3.12, its development headers and venv support, a C++20 compiler, and a graphical desktop for Qt. Setup downloads dependencies. The bootstrap creates a local virtual environment.

```bash
git clone --branch codex/batch-workflows https://github.com/ben-shin/spinflow.git spinflow-batch-test
cd spinflow-batch-test
git checkout --detach 43501bc299b495fcee21766db265a1a96e35be59
PYTHON=python3.12 bash tools/bootstrap.sh --gui
.venv/bin/spinflow doctor
.venv/bin/spinflow batch-plan --help
.venv/bin/spinflow gui
```

Check that `doctor` reports the native backend available. To follow the command-line tutorial with its short `spinflow` commands, activate the environment with `source .venv/bin/activate`. Start with two of your native files, choose fresh output paths and inspect the plan before running. [Batch walkthrough](../batch-workflows.md) · [Linux build requirements](../linux-build.md)

## Installed regression suite

The CPython 3.12 x86-64 run used a private, unpublished `0.3.0b3` wheel (SHA-256 `836b50d4dcda5f5b543256a20aae5edaa46c7172de37da8da108f4e333058cba`). Its native extension matched the existing binary (SHA-256 `811fcb73e59727b13096c453a6a8f57c3e08e52957f9c0425f04363024f97d34`). All 31 installed product files were verified for the parent process. Some subprocess tests explicitly load source and provide source-level coverage.

**1,237 tests passed, 16 public-data tests were skipped and 101 subtests passed**, with zero errors or failures. Earlier staging and source-origin failures were retained. The successful suite worker ran for 44.932 seconds and reached 545,268 KiB peak resident memory across its lifetime. Those figures describe the test worker and provide no batch speed or RAM comparison.

## One full-size input/output check

The preflight used two byte-identical copies of one processed 2D spectrum at separate paths and wrote and reopened **one** 33,596,032-byte native HDF5 file (SHA-256 `47f406acd5f14181c1edaecaffe35ecffcdf8009c3d178224799dcef58cdd89a`). All 8,388,608 stored float32 samples matched the 32 MiB reference byte for byte. The complete imported record matched, and all 6,144 coordinates passed the existing 0.00002 ppm gate. The earlier legacy export had already projected to a real plane and omitted native axis annotations, metadata and provenance. Reopening reproduces the imported representation.

An independent calculation found four positive candidates in the fixed 9 × 9 search region. The expected assignment is `ambiguous`, with absent height and volume. This was a preflight expectation. The 100-item batch and its product assignment rows have yet to run. Weak and strong real-peak volumes, large 3D/4D batch capacity and peak memory remain unverified.

Guarded command import still refuses FT, nonzero PS, TP and `-di`. The original full laboratory processing script, independent end-user reopening, researcher and physical Ubuntu acceptance, and stable-v1 qualification remain open.
