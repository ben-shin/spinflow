# ASU methanol import/filter protocol, revision 1

This preregistration concerns one independent Arizona State University CHM343 methanol calibration acquisition. It checks complete raw integer import and the existing recorded-GRPDLY correction policy. It does not reproduce an original complete processing script. No FFT lane is included.

The source is pinned by `benchmarks/cases/asu-meod25-import-filter.json`: commit `af3b9c2c4642e63e379bc349cb40e1fe8c5730e6`, ZIP Git blob `6b4d3850d0167a48d9301030bcbe458e5eb1b29e`, 1,060,855 bytes, SHA-256 `38c8d260169d42f0d15ab5c7b9ce7084735419688c9ea66e76dc34fa6b356094`. The repository README identifies ASU acquisition provenance; its root license is GPL version 3, with no separate dataset-specific declaration found. Retain both. Raw binaries remain private validation inputs and must not be added to Git or a wheel.

The header records AQ_mod=3, PARMODE=0, little-endian signed int32 storage, TD=65536, GRPDLY=76, DECIM=2568, DSPFVS=21, DIGMOD=3, SW_h=7788.16199376947 Hz, SFO1=400.13311072 MHz, and nucleus 1H. This adds integer-delay/int32 coverage, not fractional acquired-delay or native-float storage coverage. The 262,144-byte FID exactly fills 256 1024-byte records and contains 32,768 complex pairs without padding.

## Separate authorization boundaries

1. **Download-only:** obtain the exact bounded HTTPS ZIP; require its size, Git blob hash, SHA-256, complete member directory, and four previously observed text metadata hashes/CRC values to match. The Git hash is SHA1 of ASCII `blob 1060855`, one NUL byte, then ZIP bytes. Read only the four known text members for this check. Do not extract files, decompress the FID, decode numbers, or import/execute SpinFlow against acquired input. Retain receipt, stdout, stderr, and exit code.
2. **Prepare, after separate root GO:** reverify the archive; extract only the two exact allowlisted `acqus` and `fid` members into a new directory, with duplicate/path/symlink/type/size/CRC guards; hash the bytes without numeric decoding. Freeze the tool, this protocol, case manifest, archive, extracted member SHA-256 values, required check IDs, recipe, tolerances, and fixed expected metadata in a prepared lock. Capture a separate exact installed runtime lock. Root and independent review must accept both locks and their external SHA-256 values before acquired execution.
3. **Run, after separate acquired GO:** supply the externally frozen SHA-256 values of both locks. Refuse any source, lock, runtime, module membership, version, interpreter, or binding change. Use installed SpinFlow `0.3.0b3`, nmrglue `0.12`, native backend and one thread. Validate all input bytes before the first sample decode and again after execution. No source-checkout fallback is allowed in acquired execution. Every action refuses an existing output directory and retains stdout/stderr/exit status; exceptions and incomplete checks always produce a failed receipt.

The helper's `download`, `prepare`, `runtime`, and `run` actions are mechanical stages, not grants of authorization. Running a later action requires the separate root GO above. No downloaded source body is executed. The helper is source-only tooling, outside wheel contents and the public processing API.

## Fixed mandatory checks

All six IDs must occur exactly once, with boolean pass values, no unexpected/missing IDs, and no incomplete checks. Otherwise overall PASS is impossible.

| ID | Evidence | Criterion |
| --- | --- | --- |
| ASU-01 | Archive, source, tool, protocol, case, prepared lock, and installed runtime bindings; before/after input identity | Exact pinned hashes, byte sizes, member set, source preservation and runtime equality. No mutable lock can approve itself: run requires externally supplied lock SHA-256 values. |
| ASU-02 | Standard-library `struct` literal decoder, independent of product/nmrglue readers | Decode every little-endian signed `ii` pair into Python integers, convert to float64 without scaling, and compare every real and imaginary imported point by exact equality. Exactly `(2,32768)` float64. No channel swap, conjugation, point loss, reorder, crop, gain, or imaginary discard. |
| ASU-03 | Time axis, quadrature and import metadata | Exactly one axis a0 and complex bit a0; size/acquired size 32768; origin/start 0 s; dwell/step 0.0001284 s; last center 4.2072828 s; observation 400.13311072 MHz; label/nucleus 1H; unknown ppm reference; the 13 importer-declared acquisition parameters, original raw acqus text including PARMODE, and source hashes retained; zero padding; uncorrected filter state. The independent header check verifies all 14 fixed literals. Every time coordinate equals the literal binary64 start+step*index grid. |
| ASU-04 | One explicit `digital_filter(axis="a0")` operation, native backend, one thread | Delay from recorded GRPDLY=76 only; trim 78, reverse-tail fold 72, output `(2,32690)` float64; both quadratures retained; original acquired count and time/observation metadata retained; corrected state and exact correction record; input samples/metadata unchanged. No delay override, firmware lookup, or additional processing. |
| ASU-05 | nmrglue 0.12 `rm_dig_filter(z,2568,21,76,truncate_grpdly=False)` | Every complex point within unchanged rtol=1e-11 and atol=1e-7. Reference version/source tree is bound in runtime. No fits, normalization, phase tuning, or alignment. This is differential corroboration of a shared policy, not physical deconvolution truth. |
| ASU-06 | Independent integer circular advance and fold | For N=32768 define u[j]=z[(j+76)%N]. For j=0..32689, y[j]=u[j]+u[N-1-j] if j<72, otherwise u[j]. Perform addition in Python integer arithmetic before float64 conversion, avoiding int32 overflow. Compare every complex point with the same rtol=1e-11, atol=1e-7. No FFT or product/nmrglue helper generates this oracle. |

Freeze all numeric values before acquired decoding. Synthetic failure-first controls must detect wrong byte order, channel swap, missing points/shape, incorrect trim/fold, incorrect metadata, source/lock/runtime mutations, missing/duplicate check IDs, unsafe ZIP members and overwrites. A source-host synthetic result is not installed acquired acceptance.

Stop on any pin mismatch, unexpected profile, exception, incomplete check, or unchanged tolerance failure. Preserve the original failed result; do not widen tolerances, substitute data or adjust operations to pass.

## Claims that remain separate

The original TopSpin processing audit includes automatic phase and unsupported baseline correction. Complete original numerical scripts are absent. Full raw-to-TopSpin final-array, phase, sign, peak and volume parity therefore remains **UNVERIFIED**. There is no NMRPipe comparison, fitted ppm reference, concentration or general scientific-accuracy claim. S19's six failed comparisons out of eighteen remain **FAIL**; S20 stored-spectrum arithmetic remains a separate **PASS**. Human acceptance remains **UNVERIFIED**. A pass here supplies only bounded independent-source input/filter evidence and does not promote a profile or release to stable.
