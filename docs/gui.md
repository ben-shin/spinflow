# Spectrum workbench

On a Linux desktop, install the optional GUI with `bash tools/bootstrap.sh --gui`,
then run `.venv/bin/spinflow gui` from the project folder. Follow the
[Linux user-testing walkthrough](linux-user-testing.md) for a guided synthetic session. The workbench uses
the same `nmrnext` processing and file APIs as the command line.

## First run

1. Choose **1D demo** or **2D demo** for a synthetic, fully sampled acquisition.
2. Inspect the axes and select the real or imaginary quadrature channel above the plot. Channel labels follow the dataset's actual complex-axis order. The signed intensity is shown; negative peaks remain negative.
3. Set line broadening, zero fill and phase separately for each spectral axis; choose one backend for the complete recipe. Switching the selected axis retains its values; **All spectral axes** edits each changed setting across eligible axes and shows when current values differ. **Preview** computes the complete recipe without making it the current result. Changing a processing setting clears that preview. **Process** runs the recipe for all axes from the immutable original and makes the result current. **Reset to original** discards that result. For an already frequency-domain axis, **Crop frequency region** uses zero-based start-inclusive and stop-exclusive point indices.
4. Choose **Save current result…** for a spectrum file, **Save runnable workflow…** for a new folder containing the original native source and a CLI `workflow.json`, or **Save audit report…** for a JSON inspection record of operations, plan, axes and resolved processing history. The workflow reproduces the last applied result, rather than a preview or pending control edits. See the [Linux walkthrough](linux-user-testing.md) for exact replay commands.

The plot supports wheel zoom and drag pan. Frequency axes use ppm when calibrated and follow the usual descending NMR direction; time axes use milliseconds. For large arrays the display is sampled, and the footer says so. Processing and export always use complete data.

## Files and limits

**Open spectrum** accepts NMRNext native HDF5, the declared processed real NMRPipe 1D/2D profile and the declared processed real UCSF 2D profile. **Open Bruker FID** accepts the controlled raw 1D `acqus`/`fid` directory profile. Imported Bruker data with an unresolved digital filter can be inspected and saved in native form; Fourier transform is refused until a validated correction workflow exists.

The native HDF5 export preserves all components, dtype, axes, metadata and provenance. NMRPipe and UCSF exports write component 0 (the all-real quadrature channel) as float32. The workbench displays the exact preflight losses and requires an acknowledgment for each one before export. Preview results cannot be exported until processed. Existing files are never overwritten by the workbench.

Processed real files have no imaginary channel. Phase controls are disabled for those axes. If a selected recipe contains no operation, processing controls remain disabled while inspection and export remain available.

Scientific scope is limited to the implemented processor and file profiles. This interface does not provide digital-filter correction, automatic phase/baseline correction, 3D rendering, peak fitting or full NMRPipe compatibility. The [Linux validation record](public-validation.md) includes offscreen CI on the published baseline and automated interaction with this workbench on a virtual X11 display. A human desktop session and researcher usability remain untested.
