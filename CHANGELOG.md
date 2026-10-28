# Changes

## Linux-first repository preparation — 2026-10-28

- Renamed the public project and distribution SpinFlow while retaining the `nmrnext` Python module and command; added a `spinflow` command alias.
- Added a Linux bootstrap and a CMake/scikit-build-core source and platform-wheel build that includes the native C++/PocketFFT extension and its license.
- Added Ubuntu 24.04 CI for Python 3.12/3.13, installed wheel and sdist smoke checks, and offscreen Qt tests. CI execution on GitHub is pending publication.
- Kept historical Windows review records and generated outputs local. The [public validation summary](docs/public-validation.md) distinguishes prior Windows evidence from new Linux checks.

## R3 final I/O repair candidate — 2026-10-28

- Closed the remaining malformed Bruker key/EOF hang with a bounded parser stream and an independently checked failure flag; preserved valid Latin-1 and unknown parameter content.
- Added target-header representability checks for direct and derived calibration fields, rejecting float32 overflow/underflow before staging or writing. This extends the earlier intensity-overflow fix.
- The R2 judge verdict was REVISE. This was the final allocated repair round; independent R3 recheck subsequently closed all eight distinct findings and the judge accepted the restricted experimental Windows GUI/CLI preview. See [validation](docs/public-validation.md). Numerical engine and GUI source are unchanged from R2.
- Final repaired producer suite: 151 tests and 97 subtests passed. A fresh wheel/isolated target and the same seven-run frozen-source benchmark protocol passed their checks. Earlier candidate evidence remains unchanged.

## R2 repair candidate — 2026-10-28

- Independent review found calibration, overflow, parser termination, GUI state and diagnostic defects despite passing ordinary-path tests. The [public validation summary](docs/public-validation.md) states the evidence boundary; detailed local review records are retained outside the publication set.
- Native thread requests now reject values outside the platform `py::ssize_t` range during both planning and execution, avoiding an uncaught binding exception. No FFT mathematical convention changed.
- Corrected odd-length NMRPipe coordinate calibration; refused float32 export overflow before committing; bounded malformed Bruker array/string parsing at EOF.
- Invalidated GUI previews on recipe changes and made loading type-safe and transactional for opaque metadata. Corrected the GUI guide's relative Python path.
- Full repaired producer suite: 131 tests and 97 subtests passed. Stable-source repeated benchmarks and a newly built wheel were checked; original R1 evidence was preserved.
- Release review was pending at the R2 freeze; the final scoped R3 acceptance is recorded above.

## 0.1.0 — experimental first processing slice, 2026-10-28

- Introduced independent physical axes and explicit quadrature-bit channels, immutable numeric inputs, preserved JSON metadata and operation provenance.
- Added explicit modern windows, zero fill, centered complex FFT/inverse, phase, point crop, reverse and named-axis transpose, with whole-recipe validation.
- Added headless demo, inspect, plan, run, convert, compare, formats and doctor commands; no overwrite by default and no hidden alignment in comparisons.
- Added a C++/PocketFFT boundary and explicitly experimental NumPy bootstrap. Availability is reported by doctor; requested native execution never silently falls back.
- Added versioned HDF5 and restricted processed-file profiles. Loss preflight distinguishes projection, precision and metadata losses. See the generated capabilities and format notes.
- Added independent analytical fixtures and production comparisons, including both backends, individual weak-peak checks, mixed domains and quadrature tensors.
- Added an optional Qt spectrum workbench with complete-recipe preview/processing, explicit channel display, phase controls, safe worker lifetime and acknowledged export losses.
- Added a controlled experimental Bruker 1D inspection/import profile; unknown digital filtering blocks FFT. Real vendor validation, legacy numerical parity, independent NMRPipe/Sparky execution, NUS reconstruction and full suite coverage remain unimplemented or unverified.
