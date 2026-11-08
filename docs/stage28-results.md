# Stage 28 — immutable sample packing on one acquired 2D recipe

**Draft results for an unpublished build.** The candidate completed the warmed 14-operation recipe faster in all seven pairs on the previously studied Varian `cpjdcptan2d` protein acquisition. The median within-pair time reduction was **24.17%**. Full worker lifetime showed no improvement. The candidate passed the peak-memory gate, with too little difference to infer a useful RAM saving.

The proposed experimental branch would expose the candidate source for review. The measured candidate wheels remain private. Main and the published b3 package still use the earlier implementation. Independent consumer reopening and normal close remain pending.

| Measurement | Public b3 (A) | Private candidate (B) |
| --- | ---: | ---: |
| Warmed recipe function, median | 150.426152 ms | 114.924415 ms |
| Complete fresh-worker lifetime, median | 1.263509154 s | 1.267098298 s |
| Fresh-worker peak RSS, median | 483,176 KiB | 483,012 KiB |
| Fresh-worker peak RSS, maximum | 483,304 KiB | 483,108 KiB |

Each of 14 fresh workers ran one warmup followed by one measured call on the same one-vCPU Ubuntu VM. The seven pair orders were AB, BA, AB, BA, AB, BA, AB. The preregistered gates required at least a 5% median within-pair reduction, a faster candidate in at least six pairs, and candidate median and maximum worker peak RSS no higher than A. All observations count, including the first A call at 214.046235 ms in a worker lasting 4.241610 s. The ratio of arm medians was 0.763992. The 24.17% figure uses each pair's own baseline.

The function clock covered only the warmed recipe. Worker peak RSS covers startup, imports, guards, setup, preload, warmup, validation and reporting. Earlier allocations can determine its high-water mark. The controller's separate historical peak was 24,436 KiB throughout. These measurements do not establish an isolated allocation saving or a startup, export, GUI, bare-metal or larger-volume performance gain. The evidence file includes all 14 function times, lifecycle readings, peak-memory observations and inclusive quartiles. [Timing, science and build evidence](evidence/stage28-immutable-packing.json)

## What changed

An earlier baseline measured 47.403 ms for the final finiteness check and byte copy on the production layout, and 164.396 ms for the whole recipe in separate workers. That 28.83% cost share motivated the experiment. The seven-pair comparison above measured the actual build difference.

The Python model still validates finiteness and exposes immutable C-order sample bytes. If the native module is already loaded, the candidate can copy one aligned, densely transposed plane of native float32 or float64 samples directly into those bytes in tiles. The limit is 32 MiB. Other layouts and installations without the helper use the existing NumPy copy. The public constructor, FFT, thread settings, recipe operations and dependency versions were preserved.

The published b3 wheel came from source `7598ae53`. The candidate was built from `a743e93a` with private patch `10523913`. Both report version `0.3.0b3`; their wheel and native-binary hashes distinguish them. Both build logs identify GNU C++ 13.3.0, CMake 4.4.4 and pybind11 3.1.0. Build Python differed (3.12.16 for A, 3.12.3 for B), and the exact public compiler executable and effective flag line were unavailable. The comparison measures the two complete builds. Both ran under CPython 3.12.3, with native threads and numerical-library pools set to one and the same 1 GiB working-array budget.

## Scientific agreement and qualification

All 14 timed calls matched the accepted output's complete bytes and record in the worker. An independent audit reconciled the validation source, sample hashes, records and receipts. Separate timed result arrays were not retained for a second decode.

For the untimed check, an independent decoder compared both stored output files with their goldens. All 8,519,680 sample values, full files, axes, schema, metadata and provenance matched exactly. The F32 recipe began with four quadratures of shape `(4,166,1500)`, retained its authored padding and explicit imaginary discards, and ended with a real plane of shape `(1,2048,4096)`. One native copy produced its 33,554,432 immutable bytes. The F64 ASU reference retained both quadratures, shape `(2,65536)` and unknown absolute ppm reference. It used the fallback. All comparisons used the original tolerances and needed no alignment, amplitude adjustment or phase fit.

The focused CPython 3.12 suite passed 92 cases and 10 subtests. The full suite then passed on the same installed private wheel under Ubuntu CPython 3.12.3 with an offscreen GUI. It completed **1,129 cases and 101 subtests in 51.15 s**, with zero failures, errors or skips. Two expected NumPy 2.5 deprecation warnings came from tests that change an exposed array’s dtype or shape to check immutability. The XML lists 1,129 case elements and reports 1,230 in its aggregate count, including the 101 subtests.

An earlier launch stopped at a test-support import after 765 cases and 91 subtests passed. Adding the staged test directory at the end of the import search path allowed the full suite to run. The candidate binary, test selection, assertions and tolerances stayed the same. An independent review verified the installed module origins, source and fixture checks, and both run records.

## CPython 3.13 qualification

A separate offline build used the same four reviewed source and test changes on normal-GIL CPython 3.13.18. The focused suite passed 92 cases and 10 subtests. Both float widths produced immutable bytes on the native path and matched NumPy when the genuine older CPython 3.13 extension lacked the helper.

In a separate untimed replay, the independently decoded F32 and F64 files again matched all 8,519,680 values, every file byte and the complete scientific records. F32 used one native copy, and F64 used the fallback with both quadratures intact. The seven-pair performance result applies only to CPython 3.12.

The full installed CPython 3.13 suite passed **1,129 cases and 101 subtests**, with zero failures, errors or skips and the same two expected ownership-test warnings. The XML contained 1,129 case elements and an aggregate count of 1,230. An initial inventory check stopped at an obsolete build-time library path. A second inventory verified the relocated files. The suite launcher also received a separate output directory before its single run so it could preserve the earlier records.

**Independent consumer qualification, public integration and final release review remain pending.** Public b3 remains the published package. All profiles remain experimental. Six of 18 fixed Stage 19 targets still fail. The original laboratory processing workflow, absolute ppm agreement, first independent laboratory acceptance, researcher sign-off and physical Ubuntu acceptance remain open.
