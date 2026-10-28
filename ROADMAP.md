# SpinFlow roadmap

The [current scope](docs/scope.md) is a synthetic 1D/2D processing slice plus one controlled public Bruker raw 1D workflow, with strict recipes, native HDF5, narrow processed real interchange profiles, a C++ FFT backend, bounded analysis reports, and optional Qt/Matplotlib presentation. The compatibility inventories in [functions](compatibility/functions.yaml), [formats](compatibility/formats.yaml), and [workflows](compatibility/workflows.yaml) retain the broader feature space. An inventory item is not an implementation claim.

## Near-term validation

- Extend the [passing Ubuntu 24.04 CI builds](docs/public-validation.md) with direct Linux desktop and researcher workflow checks. CI exercises the native wheel, sdist and source tests on Python 3.12 and 3.13, including Qt offscreen on 3.13.
- Extend the [single hash-pinned public Bruker 1D check](docs/real-bruker-and-analysis.md) to additional permitted acquisitions, filter variants, corrected referencing and quantitative comparisons.
- Reopen exported processed files in independent NMRPipe and Sparky/Poky consumers; record calibration and loss behavior.
- Check researcher workflows in the Qt viewer and collect reproducible failures.
- Benchmark representative 1D and 2D datasets on Linux with source and input hashes; avoid general speed claims from synthetic small-workload timing.

## Later processing work

- Additional vendor digital-filter policies, DSP lookup support with evidence, and further acquisition profiles.
- NUS schedule validation and reconstruction, multidimensional acquisition, and larger datasets.
- Broader/2D automatic phase, automatic baseline correction, linear prediction, fitting, and validated peak/volume workflows with uncertainty.
- NEF interchange, script migration, and expanded NMRPipe compatibility.
- Hard memory budgeting or out-of-core processing, if justified by measured workloads.

These are targets, not promised release dates. Scientific parity and external compatibility require evidence for each profile and operation.
