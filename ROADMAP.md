# SpinFlow roadmap

The [current scope](docs/scope.md) is a synthetic 1D/2D processing slice with strict recipes, native HDF5, narrow processed real interchange profiles, a controlled raw 1D Bruker reader, a C++ FFT backend, and an optional Qt viewer. The compatibility inventories in [functions](compatibility/functions.yaml), [formats](compatibility/formats.yaml), and [workflows](compatibility/workflows.yaml) retain the broader feature space. An inventory item is not an implementation claim.

## Near-term validation

- Exercise the Linux source build, native wheel and sdist on Ubuntu 24.04 and both supported Python versions in CI.
- Validate real acquisitions with known correction and referencing semantics, beginning with the Bruker raw 1D profile.
- Reopen exported processed files in independent NMRPipe and Sparky/Poky consumers; record calibration and loss behavior.
- Check researcher workflows in the Qt viewer and collect reproducible failures.
- Benchmark representative 1D and 2D datasets on Linux with source and input hashes; avoid general speed claims from synthetic small-workload timing.

## Later processing work

- Explicit digital-filter correction and additional vendor profiles.
- NUS schedule validation and reconstruction, multidimensional acquisition, and larger datasets.
- Automatic phase and baseline correction, linear prediction, fitting, and peak-oriented workflows.
- NEF interchange, script migration, and expanded NMRPipe compatibility.
- Hard memory budgeting or out-of-core processing, if justified by measured workloads.

These are targets, not promised release dates. Scientific parity and external compatibility require evidence for each profile and operation.
