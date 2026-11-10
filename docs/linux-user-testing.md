# Linux user-testing walkthrough

This walkthrough starts with an **experimental synthetic 1D/2D session** on an Ubuntu 24.04 desktop with Python 3.12 or 3.13. It is a way to find installation and workflow problems, not a validation of NMRPipe equivalence. The [published baseline](public-validation.md) passed Linux CI, and its GUI workflow passed automated interaction on a virtual Ubuntu X11 display. Stage 11's real-data and analysis controls also passed bounded installed-wheel checks under virtual Ubuntu X11. These results do not replace a human on-screen test on your own desktop.

## Install and launch

Install `python3-venv`, `python3-dev`, and `build-essential` with your system package manager if needed. A minimal X11 desktop may need additional [Qt system libraries](linux-build.md#requirements). For the batch controls, test the development branch in a fresh checkout. The published 0.3.0b3 installer predates batch support. The 0.4.0b1 package is a testing candidate. Use these source-build commands for this walkthrough.

```bash
git clone --branch codex/batch-workflows https://github.com/ben-shin/spinflow.git spinflow-user-test
cd spinflow-user-test
bash tools/bootstrap.sh --gui
.venv/bin/spinflow doctor
.venv/bin/spinflow gui
```

`bootstrap.sh` downloads Python dependencies, creates `.venv`, and builds the native C++ extension. The `--test` option is for contributors running source tests; GUI users do not need it. In the `doctor` output, check that `backends.native.available` is `true`. If the window fails to open, record your Linux version, Python version, desktop session (X11 or Wayland), the exact command, and the terminal output. The [build guide](linux-build.md) has setup details.

## Synthetic desktop session

Use a new empty folder for saved outputs. Do not select an existing file: the workbench refuses to overwrite one.

1. Click **1D demo**. Confirm that the plot and axis details appear. Choose a real and then an imaginary component above the plot; the signed plot should change. Use the wheel to zoom and drag with the left mouse button to pan.
2. In **Processing**, choose the time axis, adjust line broadening or zero fill, and keep Fourier transform selected. Click **Preview** and check that the preview is identified as such. Change a setting and check that the old preview clears. Click **Process** to make the result current. Compare **Current** and **Original**, then use **Reset to original** to check that the starting data is retained.
3. Process the 1D demo again. Under **Export**, choose **SpinFlow native · all channels**, click **Save current result…**, and save to a new `.nmrh5` path. Use **Open spectrum…** to reopen it; inspect the plot, axis details and available components. Try the same output path a second time and confirm that it is refused without changing the first file.
4. Repeat with **2D demo**. Select each spectral axis separately and set different processing values. Switching axes should retain each axis's settings; choosing **All spectral axes** should show when values differ. Preview and process the combined recipe, then switch the displayed quadrature component. Save the current result to a different new native `.nmrh5` path and reopen it. This checks the declared all-channel native round trip, not independent scientific accuracy.
5. With the processed 2D plot open, choose **Contours** under **2D display**. Switch between **Positive**, **Negative** and **Both**, then try **Raster**. The contour overview groups visible samples into at most 192 × 192 blocks and uses their minima and maxima. Hold the right mouse button and drag around one feature. Check that the box stays visible during the drag and that releasing the button zooms to its bounds. Zooming recalculates the display from source samples. To fit the plotted items, right-click without dragging and choose **View All**.
6. For an interchange check, select **NMRPipe · component 0** for a 1D or 2D processed result, or **UCSF · component 0 · 2D** for a 2D result. Review the exact export preflight. The export should proceed only after you explicitly acknowledge every listed loss; cancel to leave no new output. These formats keep one real float32 component and may lose metadata or precision. Reopen a newly saved file in SpinFlow and observe its limited component set. This same-codec readback is not independent validation in NMRPipe or Sparky/Poky.

The GUI processes complete data even if the plot says it sampled the display. A preview is never the file exported by **Save current result…**; click **Process** first. Processing starts again from the original dataset rather than stacking on a prior result.

## Try the Batch tab with two files

1. Prepare two native files with compatible axes and a fresh output directory. Select **Files…** in **Batch**, add both files and choose that output directory. Keep **Measure these labels for each input** unchecked. Choose **No processing · copy input** for a first all-component copy. Save a new manifest, select **Plan**, inspect each item, then **Apply to files**. Check that two distinct outputs exist. A processing-only run has no assignment rows.
2. To repeat a recipe, apply it to a compatible source in **Processing** first. Select **Use applied processing** in **Batch** and inspect the operation count, backend and memory setting. Unapplied edits and previews are excluded. Save to another manifest with fresh outputs. Open that manifest again, change an input or output and save with a new name. Check that the saved operations and resource settings still match the reopened manifest.
3. For relative measurements, open a processed 1D or 2D reference. In **Optional labels and fixed regions**, choose **New**, select an analysis component, then **Label**. SpinFlow generates a stable ID for each assignment. Edit the label and set the sign, cutoff, units and fixed search and integration bounds. Save the template, enable **Measure these labels for each input** and use **Map axes…** to map every reference axis to a processed target axis. Points require identical grids; Hz requires a matching reference frame; ppm requires calibration. Use an empty processing list to analyze spectra that are already processed.
4. Save, plan and apply. In **Export CSV…**, follow each item and assignment ID to its status, height, signed volume, units and warnings. Blank measurements with `not_found` or `ambiguous` deserve review; SpinFlow does not choose the strongest candidate automatically. On a separate run try **Stop after file**. Keep outputs and journal generations together, select **Resume exact matching items** under **Advanced run options**, and resume the same manifest.

Guarded command import supports declared forms of EM, SP, explicit-size ZF and PS with both phase parameters zero. FT, nonzero PS, TP, `-di` and arbitrary shell scripts remain outside this importer. For manifest examples and the scope of previous one-input scale testing, see the [batch walkthrough](batch-workflows.md) and [100-item Linux validation](validation/batch-scale-100-2026-11-09.md).

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

Use **Open FID folder…** to load a supported raw acquisition directory. Check the time-domain axes before applying processing. For a reproducible raw 1D trial, follow the [hash-pinned BMRB Bruker walkthrough](real-bruker-and-analysis.md); the separate [public 2D guide](real-bruker-2d.md) supplies HSQC/TOCSY acquisitions and fixed both-axis recipes. In the GUI, **Correct Bruker digital filter** is an explicit opt-in for a recorded positive `GRPDLY`; **Apply explicit frequency reference** requires your chosen anchor. Run Preview and Process, then inspect the axis and signed spectrum before saving a new native result. The **Analysis / figures** tab can apply bounded complex 1D or full-hypercomplex 2D phase, find signed peaks, integrate entered regions and save a report. Install `bash tools/bootstrap.sh --gui --plot` if you also want SVG/PDF/PNG export. Keep the analysis JSON with the figure; the figure displays only a readable subset of labels. Unknown/zero delay, implicit DSP lookup, unsupported 2D modes, NUS and absolute concentrations remain outside these paths. The bounded public comparisons do not establish general phase, amplitude or volume accuracy for your acquisition.

For a useful report, include the commit SHA (`git rev-parse HEAD`), Linux release, Python version (`.venv/bin/python --version`), X11/Wayland session, 1D or 2D input type, steps, expected versus observed result, command output or screenshot, and whether any existing file changed. Do not attach private acquisition data without permission. Open an [issue](https://github.com/ben-shin/spinflow/issues) with a synthetic reproducer when possible. The workbench has reset-to-original, but no complete undo/redo history or overlay comparison; use the CLI for its declared comparison command. The [current scope](scope.md) and [roadmap](../ROADMAP.md) list unsupported scientific workflows.


The frozen Stage 12 Linux installed-wheel check also exercised both acquired 2D pipelines and nine automated virtual-X11 groups, including both-axis automatic phase, diagnostics retention, native workflow replay and normal installed-console window closure. The full copied suite passed 438 tests and 101 subtests with zero skips/failures. These are automated virtual-display results; they do not replace a human usability session or establish Wayland/GPU behavior. See the [validation record](public-validation.md) for the exact source/package artifacts, fresh-core versus reused-GUI environments and QEMU startup allowance.
