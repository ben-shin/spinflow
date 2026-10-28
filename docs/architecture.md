# First engine architecture

Python provides immutable numeric/axis objects, recipe validation, CLI, adapters and provenance. Operations share one engine across clients. C++20/pybind11/PocketFFT supplies the native FFT boundary: `_native.fft_complex(array, axis, inverse=False)` accepts complex64/128 and returns same-dtype unshifted output, preserving input. The Python engine assembles independent hypercomplex pairs and applies explicit centering. Selecting `native` must fail if unavailable; `numpy` is an explicit experimental bootstrap backend, not a hidden replacement.

HDF5 schema 1 is native storage; controlled NMRPipe and UCSF profiles are independent adapters. All writes use explicit output paths, no overwrite by default, temporary staging and atomic completion. Import never executes scripts. Runtime is headless and offline. The public project is SpinFlow; the existing Python module and primary entry point remain `nmrnext`, and `spinflow` is an alias. The native extension is built into platform-specific wheels. Original code is MIT licensed; dependency notices are separate.

Operation API: `apply_operation(dataset, op, backend='numpy', threads=1)`, `run_operations(dataset, operations, backend='numpy', threads=1, memory_bytes=None)`. Planner validates the entire recipe using shape/axis metadata before work, estimates conservative array scratch requirements, and rejects an insufficient working-array budget. This budget is not total process RSS and does not establish out-of-core capability. Arrays remain in memory in this increment.

Changes to the numerical contract need independent reference cases. The CLI, Qt viewer, and file adapters share the same validated core operations.
