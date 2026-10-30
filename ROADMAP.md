# SpinFlow roadmap

The current 0.3.0b1 beta scope is a synthetic 1D/2D processing slice plus controlled public Bruker and Varian/Agilent raw workflows, a synthetic-tested experimental JCAMP-DX profile, strict recipes, native HDF5, narrow processed real interchange profiles, a C++ FFT backend, bounded analysis reports, and optional Qt/Matplotlib presentation. It also has a separate experimental [bounded native-file mode](docs/tutorials/bounded-block-processing.md) for schema-1 HDF5 with one through four axes; it does not import raw vendor 3D/4D data or provide global high-dimensional analysis. The [support registry](compatibility/support.json) and [candidate processing contract](docs/support-contract.md) distinguish this experimental beta, the previous v0.2.0 prerelease and proposed future promotion scope. The inventories in [functions](compatibility/functions.yaml), [formats](compatibility/formats.yaml), and [workflows](compatibility/workflows.yaml) retain detail and broader feature space. An inventory item is not an implementation claim.

## Near-term validation

- Extend the [passing Ubuntu 24.04 CI builds](docs/public-validation.md) with direct Linux desktop and researcher workflow checks. CI exercises the native wheel, sdist and source tests on Python 3.12 and 3.13, including Qt offscreen on 3.13.
- Extend the [bounded public 1D](docs/real-bruker-and-analysis.md) and [two acquired 2D checks](docs/real-bruker-2d.md) beyond one compound/instrument, with additional filter/acquisition variants and stricter quantitative comparisons. Fixed 2D relative-volume checks do not establish concentrations or absolute vendor parity.
- Extend the selected NMRDraw 13 and Sparky 3.115 reopening of one Varian protein case to independent acquisitions and supported output profiles; record calibration and loss behavior.
- Check researcher workflows in the Qt viewer and collect reproducible failures.
- Validate JCAMP with permitted acquired spectra and independent consumers; broaden Varian acquisition encodings only with explicit quadrature evidence.
- Measure the exact candidate on representative 1D/2D datasets and an actual supported desktop with source and input hashes. The selected prior source speedup is against earlier SpinFlow, not general NMRPipe speed parity.

## Later processing work

- Additional vendor digital-filter policies, DSP lookup support with evidence, and further acquisition profiles.
- NUS schedule validation and reconstruction, raw-vendor multidimensional acquisition, calibrated 3D/4D inspection/export and global analysis. The separate native-file block engine already handles selected large fully sampled cubes numerically under an explicit working budget.
- Broader automatic phase beyond isolated positive separable 1D/2D lines, automatic baseline correction, linear prediction, fitting, and validated peak/volume workflows with uncertainty.
- NEF interchange, script migration, and expanded NMRPipe compatibility.
- Hard process-RSS budgeting and broader streaming paths, if justified by measured workloads; the present native-file block budget is an accounting policy, not an RSS cap.

These are targets, not promised release dates. Scientific parity and external compatibility require evidence for each profile and operation.
