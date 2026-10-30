# Changes

## 0.3.0b1 — experimental beta

- Adds an ordered Qt recipe editor with separate pending and applied state, explicit automatic-phase steps, and a saved session that reopens and verifies the applied result. The documented CLI recipe remains replayable.
- Adds bounded point/ppm region selection on the applied current spectrum, retaining parent coordinates and signed peak/integral reports. Whole-spectrum analysis retains its 2,000,000-point guard.
- Includes experimental native schema-1 HDF5 slab processing for one through four axes under a declared block budget. Acquired 3D/4D evidence uses relative Hz coordinates; this does not add raw high-dimensional vendor import, global analysis or high-dimensional NMRPipe/UCSF export.
- A frozen v3 code diagnostic passed 979 installed Linux CPython 3.12 tests and 101 subtests, one acquired 14-operation 8,388,608-point automated X11 desktop/session/CLI workflow, selected current-export reopening in NMRDraw 13 and Sparky 3.115, and one selected v0.2.0 saved-artifact replay. The [candidate results](docs/v1-candidate-results.md) identify its hashes and limits. Publication-source assets and CI require their own checks.
- Profiles remain experimental. Independent researcher sessions, broader scientific validation, quantitative accuracy, general NMRPipe parity and a stable 1.0 contract remain unverified. The previous v0.2.0 release assets are unchanged.

## 0.2.0 — experimental release candidate, 2026-10-29

- Adds controlled Varian/Agilent 1D and selected States 2D acquisition imports, plus an experimental real 1D JCAMP-DX reader with synthetic evidence only. See the [format profiles](docs/formats.md) for exact scope.
- Adds explicit frequency orientation and quadrature-handedness tracking, selected phase/noise regions, reference-sample baseline integration and a crop-first large-spectrum workflow. Reports retain the selections and dataset digest.
- Provides a binary-first Ubuntu 24.04 x86-64 installation path for CPython 3.12 and 3.13, alongside the source build. Qt and figure support remain optional; processing and the synthetic first workflow run offline after installation.
- This is an experimental prerelease, not a validated laboratory replacement for NMRPipe. General vendor support, real JCAMP validation, independent export reopening, concentration accuracy and human usability remain unverified. The [validation record](docs/public-validation.md) describes the measured scope.

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
