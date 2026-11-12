# SpinFlow 0.4.0b1: fixed-file reader timing comparison

**Scope.** This is a descriptive, paired comparison of reader calls for one processed, real NMRPipe float32 2D file. Arm A used SpinFlow 0.4.0b1’s public `nmrnext.io.read_dataset(..., format="nmrpipe-real")` API. Arm B used `nmrglue 0.12`’s lower-level `pipe.read`. This is not a comparison with the NMRPipe program or its complete processing workflow, and it does not measure GUI loading-to-display time.

The input was 33,556,480 bytes, containing an 8,388,608-value `[2048, 4096]` float32 plane. All 98 timed reads were checked against the same full sample-bit hash, complete metadata and the 6,144 declared grid-coordinate checks. The coordinate checks establish consistency with recorded axes; they do not establish absolute chemical ppm correctness. Source and runtime pins are listed in [calculations and source pins](calculations-and-source-pins.json); the complete timed calls and per-worker RSS are in the accompanying CSV files.

## Timing results

Four endpoints were predeclared. Each row reports seven A/B worker pairs. The paired difference is A minus B, so positive values mean B took less time in that pair. The percentage is computed per pair as `100 × (A − B) / A`; it is not a ratio of group medians. Median, min, max and unscaled median absolute deviation (MAD) are shown. The full values and pair-by-pair observations are available in [paired-summary.csv](paired-summary.csv) and [paired-observations.csv](paired-observations.csv).

| Endpoint | A time, median [min–max]; MAD (ms) | B time, median [min–max]; MAD (ms) | Paired A−B, median [min–max]; MAD (ms) | B lower time, median paired % [min–max]; MAD | Pairs where B was lower |
|---|---:|---:|---:|---:|---:|
| Fresh worker, reader API | 613.626 [432.584–688.914]; 75.288 | 378.036 [374.750–380.571]; 1.850 | 233.055 [53.342–314.164]; 81.109 | 37.98 [12.33–45.60]; 7.62 | 7/7 |
| Fresh worker, dispatch to reader return | 672.791 [491.532–751.183]; 78.392 | 438.245 [432.402–439.892]; 1.646 | 232.899 [53.148–318.105]; 85.206 | 34.62 [10.81–42.35]; 7.73 | 7/7 |
| Warm worker, first timed read | 550.967 [425.542–633.948]; 81.694 | 389.830 [377.053–445.229]; 11.366 | 105.738 [−11.714–244.118]; 81.240 | 19.19 [−2.75–38.51]; 13.85 | 6/7 |
| Warm worker, median of later calls 1–5 | 35.003 [24.962–108.588]; 9.656 | 3.157 [2.698–13.615]; 0.154 | 31.811 [11.347–105.536]; 10.857 | 90.88 [45.46–97.19]; 5.81 | 7/7 |

The first call in each warm worker is shown separately. For the steady endpoint, each worker’s median of its later five calls is one observation; those seven worker medians are paired by worker. Calls were not pooled, warmup calls were not dropped from the CSV, and no result was selected or filtered after timing. The filesystem cache was not forced cold; “fresh” refers to fresh worker/dispatch, not OS-cold storage. See the [paired distribution figure](paired-timing-distribution.png) and vector [SVG](paired-timing-distribution.svg).

## What the clocks include

`read_elapsed` starts immediately before the reader call and ends on its return. NumPy is imported during worker setup before that clock; the reader API’s first lazy imports are inside it. In Arm A the reader is the SpinFlow dataset API; in Arm B it is bare `nmrglue.pipe.read`. The calls return different interface objects (dataset versus header/array), so this is a reader endpoint comparison, not equal end-to-end application work.

The fresh dispatch-to-return clock begins at parent dispatch and ends at reader return. It includes worker startup and pre-API work but excludes post-return adaptation, persistence, validation, and total process lifetime. The per-call timings are not whole-task timings.

## Worker memory observations

`worker-rss.csv` retains all 28 waited-shell lifetime peak RSS values. These receipts include startup, imports, read, checkpoint and raw-file fsync as applicable; they are not summed process RSS or aggregate cgroup memory. The matched cgroup memory gate is **UNVERIFIED**, so this report makes no memory-winner claim.

| Cohort | Arm A median [min–max] KiB; unscaled MAD | Arm B median [min–max] KiB; unscaled MAD |
|---|---:|---:|
| Fresh workers (n=7 each) | 180,884 [180,860–181,128]; 24 | 135,360 [135,188–135,420]; 24 |
| Warm workers (n=7 each) | 189,128 [188,828–189,288]; 120 | 135,412 [135,276–135,464]; 24 |

## Interpretation and limits

On this one file and this one constrained environment, the measured B reader-call times were lower than A in all seven fresh pairs, six of seven warm first-call pairs, and all seven pairs of warm per-worker later-call medians. That is a case-specific observation: it is not a general winner, a C++ optimization gain, or a before/after comparison. This study changed no reader or processing code.

Both cohorts ran in the same Ubuntu 24.04 x86-64, one-vCPU/4-GiB VM with CPU affinity 0 and library thread environment settings configured to one thread; the observed number of runtime threadpools was not measured. Per-child guards were 2 GiB address space, 60 CPU seconds, 64 MiB output-file limit and 60 seconds wall time. The task-managed jobs were quiet; external OS-wide idleness was not established. Seven pairs per endpoint are descriptive, with no confidence interval, p-value, or population-level claim. The 0.4.0b1 CPython 3.12 wheel identity was verified; this does not qualify the GUI, CPython 3.13, human use, or the complete original laboratory method.

The public [0.4.0b1 experimental prerelease](https://github.com/ben-shin/spinflow/releases/tag/v0.4.0b1) and [batch guide](https://github.com/ben-shin/spinflow/blob/95cdf4055a84c749c97217a3b94532d96a70b766/docs/batch-workflows.md) provide release context. Human acceptance and broader scientific validation remain separate and open.

