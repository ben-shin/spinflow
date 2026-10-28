# Spectrum workbench

On a Linux desktop, install the optional GUI with `bash tools/bootstrap.sh --gui --test`,
then run `.venv/bin/python -m nmrnext gui` from the project folder. The workbench uses
the same `nmrnext` processing and file APIs as the command line.

## First run

1. Choose **1D demo** or **2D demo** for a synthetic, fully sampled acquisition.
2. Inspect the axes and select the real or imaginary quadrature channel above the plot. Channel labels follow the dataset's actual complex-axis order. The signed intensity is shown; negative peaks remain negative.
3. Set the axis, line broadening, zero-fill factor, phase and backend. **Preview** computes the complete recipe without making it the current result. Changing a processing setting clears that preview. **Process** runs the current recipe from the immutable original and makes the result current. **Reset to original** discards that result.
4. Choose **Save current result** for a spectrum file or **Save recipe and provenance** for a JSON record of the operations, plan, axes and resolved processing history.

The plot supports wheel zoom and drag pan. Frequency axes use ppm when calibrated and follow the usual descending NMR direction; time axes use milliseconds. For large arrays the display is sampled, and the footer says so. Processing and export always use complete data.

## Files and limits

**Open spectrum** accepts NMRNext native HDF5, the declared processed real NMRPipe 1D/2D profile and the declared processed real UCSF 2D profile. **Open Bruker FID** accepts the controlled raw 1D `acqus`/`fid` directory profile. Imported Bruker data with an unresolved digital filter can be inspected and saved in native form; Fourier transform is refused until a validated correction workflow exists.

The native HDF5 export preserves all components, dtype, axes, metadata and provenance. NMRPipe and UCSF exports write component 0 (the all-real quadrature channel) as float32. The workbench displays the exact preflight losses and requires an acknowledgment for each one before export. Preview results cannot be exported until processed. Existing files are never overwritten by the workbench.

Processed real files have no imaginary channel. Phase controls are disabled for those axes. If a selected recipe contains no operation, processing controls remain disabled while inspection and export remain available.

Scientific scope is limited to the implemented processor and file profiles. This interface does not provide digital-filter correction, automatic phase/baseline correction, 3D rendering, peak fitting or full NMRPipe compatibility. The earlier Windows PySide6 interface was exercised with offscreen integration tests. The configured Linux CI workflow runs Qt offscreen tests; desktop use and researcher usability still require direct validation.
