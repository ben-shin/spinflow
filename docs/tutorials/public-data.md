# Optional public-data tutorials

These examples download external datasets to a local cache. Use the **GitHub tagged source archive** for the same release as the installed wheel, not the Python source distribution: the latter does not contain the helper scripts, manifests and recipes. Download and extract [the v0.2.0 source archive](https://github.com/ben-shin/spinflow/archive/refs/tags/v0.2.0.zip), enter its extracted directory, and activate your installed SpinFlow environment. Fetchers check the pinned byte counts and SHA-256 values in their manifests; they do not execute vendor scripts. Downloads need internet access, and data remain in `.artifacts/`. No redistribution licence has been established here. Run these commands from the extracted source root with `python` and `spinflow` from the activated environment.

## Bruker 1D

```sh
python tests/fixtures/fetch_bruker_public.py
spinflow plan examples/bruker-1d-public.json
spinflow run examples/bruker-1d-public.json
spinflow inspect .artifacts/science11-realdata/bmse000001/spinflow-processed.nmrh5
```

This fixed recipe reads the public BMRB bmse000001 1H acquisition, corrects its recorded positive digital-filter delay, and sets the explicit 4.8362249846447805 ppm reference at 499.839931909633 MHz. The output is a new native spectrum; acquisition files remain unchanged. The [full walkthrough](../real-bruker-and-analysis.md) gives optional phase, signed peaks, integration and figure commands, supported profile, assumptions and measured position comparisons. Its historical bootstrap instructions are for a source build; use the activated wheel environment for the commands here.

## Varian 1D

```sh
python tests/fixtures/fetch_varian_public.py
spinflow plan examples/varian-1d-public.json
spinflow run examples/varian-1d-public.json
spinflow inspect .artifacts/coverage-formats/native-varian-1d-calibrated.nmrh5
```

This declared unarrayed float32 complex profile uses a fixed sine window, phase and explicit 99 ppm reference at 125.681 MHz with frequency scale −1. The [Varian guide](../real-varian.md) specifies binary constraints, provenance, reference comparisons and limitations; it is not general VNMR compatibility. The recipe allows a 2 GiB working-array estimate, which is not an RSS limit.

For both examples, fetch failures or hash mismatches are errors, not a successful validation. Recipes refuse existing output paths; edit a copy of the recipe to choose a fresh output for a repeat. The [2D Bruker guide](../real-bruker-2d.md) covers its separately pinned acquisitions and explicit recipes. Real examples are limited to their stated profiles and do not establish universal phase, baseline, peak or integral accuracy.
