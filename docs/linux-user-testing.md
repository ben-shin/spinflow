# Linux user-testing walkthrough

This walkthrough starts with an **experimental synthetic 1D/2D session** on an Ubuntu 24.04 desktop with Python 3.12 or 3.13. It is a way to find installation and workflow problems, not a validation of NMRPipe equivalence. The [published baseline](public-validation.md) passed Linux CI, and its GUI workflow passed automated interaction on a virtual Ubuntu X11 display. Stage 11's real-data and analysis controls also passed bounded installed-wheel checks under virtual Ubuntu X11. These results do not replace a human on-screen test on your own desktop.

## Install and launch

Install `python3-venv`, `python3-dev`, and `build-essential` with your system package manager if needed. A minimal X11 desktop may need additional [Qt system libraries](linux-build.md#requirements). From a fresh [repository checkout](https://github.com/ben-shin/spinflow), run:

```bash
bash tools/bootstrap.sh --gui
.venv/bin/spinflow doctor
.venv/bin/spinflow gui
```

`bootstrap.sh` downloads Python dependencies, creates `.venv`, and builds the native C++ extension. The `--test` option is for contributors running source tests; GUI users do not need it. In the `doctor` output, check that `backends.native.available` is `true`. If the window fails to open, record your Linux version, Python version, desktop session (X11 or Wayland), the exact command, and the terminal output. The [build guide](linux-build.md) has setup details.

## Synthetic desktop session

Use a new empty folder for saved outputs. Do not select an existing file: the workbench refuses to overwrite one.

1. Click **1D demo**. Confirm that the plot and axis details appear. Choose a real and then an imaginary component above the plot; the signed plot should change. Wheel zoom and drag pan should work.
2. In **Process**, choose the time axis, adjust line broadening or zero fill, and keep Fourier transform selected. Click **Preview** and check that the preview is identified as such. Change a setting and check that the old preview clears. Click **Process** to make the result current. Compare **Current** and **Original**, then use **Reset to original** to check that the starting data is retained.
3. Process the 1D demo again. Under **Export**, choose **SpinFlow native · all channels**, click **Save current result…**, and save to a new `.nmrh5` path. Use **Open spectrum…** to reopen it; inspect the plot, axis details and available components. Try the same output path a second time and confirm that it is refused without changing the first file.
4. Repeat with **2D demo**. Select each spectral axis separately and set different processing values. Switching axes should retain each axis's settings; choosing **All spectral axes** should show when values differ. Preview and process the combined recipe, then switch the displayed quadrature component. Save the current result to a different new native `.nmrh5` path and reopen it. This checks the declared all-channel native round trip, not independent scientific accuracy.
5. For an interchange check, select **NMRPipe · component 0** for a 1D or 2D processed result, or **UCSF · component 0 · 2D** for a 2D result. Review the exact export preflight. The export should proceed only after you explicitly acknowledge every listed loss; cancel to leave no new output. These formats keep one real float32 component and may lose metadata or precision. Reopen a newly saved file in SpinFlow and observe its limited component set. This same-codec readback is not independent validation in NMRPipe or Sparky/Poky.

The GUI processes complete data even if the plot says it sampled the display. A preview is never the file exported by **Save current result…**; click **Process** first. Processing starts again from the original dataset rather than stacking on a prior result.

## Reproduce a processed session

After processing, choose **Save runnable workflow…** and enter a directory name that does **not** exist. The new folder contains `source.nmrh5`, a native copy of the original source, and `workflow.json`, a strict CLI recipe referencing that source and `result.nmrh5` with relative paths. It records the last **applied** operations and backend; a preview or unprocessed control change is not part of that recipe. From the checkout, replace the folder path below with your new directory:

```bash
.venv/bin/spinflow plan path/to/bundle/workflow.json
.venv/bin/spinflow run path/to/bundle/workflow.json
.venv/bin/spinflow inspect path/to/bundle/result.nmrh5 --json
```

`run` refuses an existing `result.nmrh5`; choose a new folder or remove only your own disposable test output before repeating. **Save audit report…** is an inspection JSON record with plan, axes and history; it is not a CLI `plan`/`run` recipe. Reopen `result.nmrh5` in the GUI and compare it with the originally processed display. For a numerical CLI comparison, run `spinflow compare` with the separate native GUI result and CLI result. The two files must represent the same processed source and settings.

## Processed real data and boundaries

If you have a permitted processed real NMRPipe 1D/2D or UCSF 2D test file, **Open spectrum…** can inspect it. For a frequency-domain spectral axis, select **Crop frequency region** and use zero-based **Crop start (point, included)** and **Crop stop (point, excluded)**. Preview, process, save to a new native path, and reopen it. Record the chosen index range and whether the displayed axis calibration matches your expectation. Crop applies to the original frequency-domain source; an axis still in the time domain cannot be cropped in the same run as its FFT. These are narrow declared format profiles, not arbitrary NMRPipe/UCSF support.

For a reproducible raw 1D trial, follow the [hash-pinned BMRB Bruker walkthrough](real-bruker-and-analysis.md) before selecting your own acquisition. In the GUI, **Correct Bruker digital filter** is an explicit opt-in for a recorded positive `GRPDLY`; **Apply explicit frequency reference** requires your chosen anchor. Run Preview and Process, then inspect the axis and signed spectrum before saving a new native result. The **Analysis / figures** tab can apply bounded 1D phase, find signed peaks, integrate entered regions and save a report. Install `bash tools/bootstrap.sh --gui --plot` if you also want SVG/PDF/PNG export. Keep the analysis JSON with the figure; the figure displays only a readable subset of labels. Unknown or zero delay, implicit DSP lookup, raw Bruker 2D, automatic 2D phase, and absolute concentration claims are outside this path. The single public position comparison does not validate your acquisition's phase, amplitude or volume.

For a useful report, include the commit SHA (`git rev-parse HEAD`), Linux release, Python version (`.venv/bin/python --version`), X11/Wayland session, 1D or 2D input type, steps, expected versus observed result, command output or screenshot, and whether any existing file changed. Do not attach private acquisition data without permission. Open an [issue](https://github.com/ben-shin/spinflow/issues) with a synthetic reproducer when possible. The workbench has reset-to-original, but no complete undo/redo history, batch processing or overlay comparison; use the CLI for its declared comparison command. The [current scope](scope.md) and [roadmap](../ROADMAP.md) list unsupported scientific workflows.
