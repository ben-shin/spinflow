# SpinFlow 0.4.0b1: validation status

**Experimental beta; not a stable release.** This note describes source `0e3f896255a98dd766d73fa534951bf1ef6de725`, built by [GitHub Actions run 43290423200](https://github.com/ben-shin/spinflow/actions/runs/43290423200). See [GitHub Releases](https://github.com/ben-shin/spinflow/releases) for current availability and the accompanying [asset checksum list](asset-manifest.md).

## Build and automated checks

The build produced CPython 3.12 and 3.13 Ubuntu 24.04 x86-64 wheels, a source archive, an installer, and checksum/build manifests. All three build jobs succeeded. A private CPython 3.12 installation qualification confirmed the version, native `doctor` availability, and installed-file identities.

A CPython 3.12 full-suite run on one CPU with 4 GiB memory recorded **1,260 passed, 101 aggregated subtests passed, 16 skipped, 0 failures, and 0 errors**. The skipped tests depend on public-cache data and are not real-data evidence. The suite included two test-only files, `tests/import_origin_support.py` and `tests/test_import_origin_support.py`, used to authenticate installed-wheel identity; they do not change scientific fixtures or numerical tolerances. Some CLI child checks intentionally used the source tree, so this does not show that every test child imported only the installed wheel. A separate CPython 3.13 CI installer/GUI smoke check ran, but the full suite was not repeated against that artifact.

The measured suite worker reported 46.368 seconds and 551,048 KiB lifetime RSS. These are single-run diagnostics, not a matched speed comparison or aggregate memory result. Loading-time comparisons remain pending.

## Fixed-reference measurement

A batch measurement on two byte-identical copies of one processed real-data plane produced four feature rows. Both copies retained the same full float32 sample array and exact output identities. They are repeatability copies of one plane, not independent acquisitions.

For the selected strong and lower-amplitude positive features, the measured heights were 193,098.1875 and 51,761.62109375. Their signed volumes were 332,190,179.2110199 and 77,592,932.69692841 intensity·Hz², respectively. Each feature was measured at the same fixed coordinates on both copies. All four volume comparisons passed the preregistered 1e-10 relative gate against the frozen reference calculation. No fitting, recentering, scaling, or phase adjustment was used.

The negative reference remains **UNVERIFIED**: no eligible disjoint negative feature met the frozen selection rule, and none was substituted. These fixed-coordinate checks do not establish absolute ppm calibration, biochemical identity, full end-to-end processing accuracy, or performance gains.

## Remaining limits

- The strict six-case NMRPipe NULL file-byte identity gate remains failed; matching decoded sample values do not clear byte identity.
- Import supports a guarded command subset, not complete NMRPipe shell pipelines. Full FT/TP and nonzero PS behavior are not admitted.
- The complete original protein processing method is unvalidated. The newer BMRB gChsqc LP/POLY steps remain unsupported by the declared profile.
- No broad 3D/4D or general vendor-format support is claimed. Batch labels are not automatic chemical assignments; volumes are not concentrations.
- Human laboratory acceptance, researcher-selected acquisitions, independent consumer reopen, matched processing performance, and complete consumer validation remain **NOT RUN / PENDING**. Older S19 comparison retains six failed checks among eighteen.

The researcher handoff and blank acceptance record are in development at [source commit 95cdf405](https://github.com/ben-shin/spinflow/tree/95cdf4055a84c749c97217a3b94532d96a70b766): [handoff](https://github.com/ben-shin/spinflow/blob/95cdf4055a84c749c97217a3b94532d96a70b766/docs/researcher-handoff-0.4.0b1.md) and [acceptance form](https://github.com/ben-shin/spinflow/blob/95cdf4055a84c749c97217a3b94532d96a70b766/docs/acceptance-form-0.4.0b1.md). These do not constitute human acceptance. The tested wheel identity remains source commit `0e3f896255a98dd766d73fa534951bf1ef6de725`.

