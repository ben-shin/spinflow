# Loading a processed 2D NMRPipe spectrum

How long does a Python reader take to return a fully loaded spectrum? On one processed protein NMR spectrum, the first read in fresh workers took a median **710.979 ms with SpinFlow 0.3.0b3** and **414.109 ms with nmrglue 0.12**. For repeated reads in an existing worker, the corresponding medians were **30.286 ms** and **3.918 ms**. These clocks end at two different API boundaries. SpinFlow constructs an immutable Dataset with axes, metadata and provenance. The nmrglue library returns a header dictionary and a mutable array.

| Clock | SpinFlow median, ms | nmrglue median, ms | Median paired reduction¹ | Pairs with shorter nmrglue time |
| --- | ---: | ---: | ---: | ---: |
| Fresh worker, first API call | 710.979 | 414.109 | 43.53% | 7/7 |
| Fresh worker, dispatch to API return | 773.535 | 477.646 | 40.45% | 7/7 |
| Six-read worker, first API call | 515.512 | 393.992 | 23.28% | 7/7 |
| Six-read worker, median of next five calls | 30.286 | 3.918 | 86.46% | 7/7 |

¹ Within each pair, divide the difference (SpinFlow minus nmrglue) by the SpinFlow time. The reported percentage is the median of the seven results. All four clocks met the prespecified timing criteria. The required matched, whole-process-group memory peaks were unavailable, so the benchmark yields **no qualified winner**.

### What was measured

Both readers loaded the same 33,556,480-byte NMRPipe-format file derived from a cached protein acquisition. Its N15 and C13 frequency axes have 2,048 and 4,096 points. The 32 MiB sample plane was already real-only float32. Each call read all stored samples, with no further quadrature discard, Fourier transform, phase correction or precision change. The earlier legacy export omitted the original axis annotations, native metadata and provenance. Neither reader can recover those fields from this file. Raw FID conversion and the earlier real projection occurred before timing.

Each cohort used seven pairs in alternating order. One fresh worker per arm in each pair made either one read or six reads. The first API call includes lazy reader imports after NumPy has loaded. Dispatch to return also includes worker launch, common harness setup and file I/O. Individual read clocks exclude subsequent result adaptation, serialization, writes and validation. Warm workers serialized and synced the complete result after each read. Those writes can affect the shared, unforced page cache. Fresh denotes a new process, not cold storage.

| Pair, order | Fresh API | Fresh dispatch to return | Six-read worker, first | Same worker, later-five median |
| --- | ---: | ---: | ---: | ---: |
| 1 AB | 1,588.576 / 430.415 | 1,802.297 / 493.901 | 678.722 / 375.777 | 51.927 / 11.594 |
| 2 BA | 741.300 / 570.181 | 804.470 / 639.378 | 543.231 / 393.992 | 30.286 / 4.612 |
| 3 AB | 505.796 / 414.109 | 568.623 / 477.646 | 445.919 / 408.413 | 41.496 / 3.918 |
| 4 BA | 968.762 / 403.890 | 1,037.288 / 471.878 | 515.512 / 380.258 | 36.519 / 14.186 |
| 5 AB | 560.256 / 430.357 | 632.103 / 495.305 | 445.412 / 395.963 | 28.440 / 3.851 |
| 6 BA | 687.483 / 388.247 | 754.411 / 449.234 | 519.918 / 398.886 | 27.995 / 3.790 |
| 7 AB | 710.979 / 380.569 | 773.535 / 443.470 | 464.720 / 386.808 | 27.964 / 3.425 |

Each cell gives SpinFlow / nmrglue milliseconds. The two cohorts ran separately. All observations, including the 1,588.576 ms first SpinFlow call, contribute to the summaries. Each six-read worker contributes one later-five median. The evidence file includes all 98 unrounded call times, seven-pair distributions, paired differences, ranges and unscaled median absolute deviations. [Timing and reproducibility data](../evidence/processed-spectrum-loading.json)

### Worker lifetime and memory

Including setup, reads, persistence and cleanup changes the result. Fresh whole-worker medians were 1,173.408 ms and 713.839 ms for SpinFlow and nmrglue respectively. Six-read whole-worker medians were **1,277.004 ms and 1,873.721 ms**. In that cohort nmrglue had the shorter whole-worker time in five pairs, with a median paired reduction of 20.29%. A median of paired differences can point in a different direction from a comparison of the two arm medians.

Each worker ran on CPU 0 of the same x86-64 Linux VM with 4 GiB guest RAM and one thread per numerical pool. The timing records omit the distribution version, kernel release and CPU model. Both used CPython 3.12.3, NumPy 2.5.4 and SciPy 1.18.1. Worker lifetime peak RSS (median / maximum, KiB) was 180,868 / 181,148 for fresh SpinFlow and 135,384 / 135,540 for fresh nmrglue. For six-read workers it was 189,108 / 189,292 and 135,404 / 135,536 respectively. This diagnostic includes all six serializations in those workers. The controller's historical peak is recorded separately. Neither measure isolates reader allocations or gives aggregate memory.

A separate observation on 2026-11-09 at 14:36:48 UTC found Ubuntu 24.04.5 LTS, kernel 6.8.0-147-generic and one exposed virtual CPU. The guest reported an AMD Ryzen 7 9800X3D 8-Core Processor. The timed cohorts did not record a boot identifier, so their boot identity cannot be matched directly to this later observation.

### Output and scope

All 98 results matched the reference sample bytes exactly, including sign bits. Each complete metadata record matched its reader's previously qualified reference. The independent axis check covered all 6,144 coordinate points against the original absolute tolerance of 0.00002 ppm. This verifies reproduction of the stored axes. It does not establish experimental chemical referencing.

Only library loading was timed. NMRPipe 13's NULL read-through would also write a file and has no equivalent warm in-process return here. The NMRPipe executable, raw FID import, end-to-end processing and nmrPype were not benchmarked in this comparison. The measurements use the published SpinFlow b3 build. Later experimental native packing, memory and batch work are separate. The evidence identifies the release, input, expected output, source and method by hash. The acquisition and processed file are not redistributed, so exact repetition requires independent access to the matching input. [SpinFlow 0.3.0b3 release](https://github.com/ben-shin/spinflow/releases/tag/v0.3.0b3)
