# SpinFlow tutorials

SpinFlow is experimental. Start with the offline synthetic example, then try the optional desktop or public-data examples. Synthetic outputs demonstrate the workflow; they do not validate real NMR measurements.

## 1. Your first spectrum, offline

On Ubuntu 24.04 x86-64 with CPython 3.12 or 3.13 and `python3-venv`, install the release using the commands below after v0.2.0 is published. Installation needs network access for the wheel and dependencies. The core installation needs neither Qt nor Matplotlib. For a desktop and figures, rerun the installer with `--gui --plot`; existing extras are retained. A different Python version or SpinFlow version requires a different `--prefix`. See the [installation instructions](../../README.md) for details.

```sh
curl -fLO https://github.com/ben-shin/spinflow/releases/download/v0.2.0/install-spinflow.py
python3 install-spinflow.py --version 0.2.0
. "$HOME/.local/share/spinflow/0.2.0/bin/activate"
spinflow doctor
```

Check that the native backend reports `available: true`. The following workflow runs offline.

From any writable working directory, choose a **new** directory name:

```sh
spinflow first-spectrum first-spectrum
spinflow inspect first-spectrum/phased.nmrh5
```

The command creates `first-spectrum/` and prints a JSON list of files. It will refuse an existing directory, even an empty one. Its parent directory must already exist. If interrupted or an error occurs, the new directory may contain partial outputs: choose another new name after inspecting the error.

Open `analysis.csv` in a spreadsheet or read `analysis.json` for full peak, region and integration details. You should see two signed peaks at point indices 320 (positive) and 224 (negative), with two small integration regions. These are synthetic, not molecular assignments. The raw data are labelled synthetic and retained in `synthetic-raw.nmrh5`; `processed.nmrh5` is the transformed spectrum and `phased.nmrh5` is the corrected result. The processor deliberately adds +35° of phase in `process.json` and corrects it by −35° in `phase.json`. This is a known manual correction, not an automatic phase estimate. The original source stays unchanged.

To replay, edit copies of the recipes so their `output` fields name new files in the same folder; `input` and `output` paths are relative to the recipe's location. Then run:

```sh
spinflow plan first-spectrum/process-copy.json
spinflow run first-spectrum/process-copy.json
spinflow compare first-spectrum/processed.nmrh5 first-spectrum/processed-copy.nmrh5
```

For example, set the copied recipe's output to `processed-copy.nmrh5` before those commands. Repeat with a copy of `phase.json`, pointing its input at the replayed processed file and output at a new name. `run` refuses existing outputs. Comparison is a replay check, not an independent scientific validation.

With the optional plot extra installed, make a figure at a new path:

```sh
spinflow figure first-spectrum/phased.nmrh5 first-spectrum/spectrum.svg --analysis first-spectrum/analysis.json
```

## 2. Explore the workbench

Install the optional GUI extra using the [installation instructions](../../README.md), then run `spinflow gui` on a desktop with a display. Click **1D demo**, then **Process** to make a spectrum. In **Analysis / figures**, choose **Pick peaks**; use **Save current result…** to save a new native file. Alternatively, **Open spectrum…** opens `first-spectrum/phased.nmrh5` from the CLI example. Use **Open acquisition folder…** for a supported raw acquisition directory. Preview is not the applied result; click **Process** before saving. See the [workbench guide](../gui.md) for channel selection, workflows and export losses.

## 3. Optional public acquisitions

The [public-data walkthrough](public-data.md) uses separately downloaded, hash-checked Bruker and Varian data and a matching source archive. It requires internet access. Data are not redistributed with SpinFlow.

## Interpretation and troubleshooting

Automatic phase fits a restricted positive, separable line model and can refuse unsuitable data. Inspect its diagnostics and the resulting spectrum; manual phase is available in recipes and the GUI. Peak picking finds signed local extrema, not assignments. Integrals are signed relative intensity times Hz (or Hz² in 2D), not concentrations. Thresholds, bounds, baselines and referencing require scientific judgment.

Analysis is capped at 2,000,000 points per component, including mask creation. Crop a frequency-domain spectrum first, then create masks for the cropped dataset; masks and reports are tied to the dataset digest. See [analysis regions](../analysis-regions.md). For unsupported formats or acquisition modes, consult [format profiles](../formats.md). If `spinflow` is not found, activate the installation environment; if `doctor` reports the native backend unavailable, revisit installation. GUI and figure errors can indicate missing optional extras or a desktop display. `spinflow --help` lists commands; structured JSON errors identify refused operations.
