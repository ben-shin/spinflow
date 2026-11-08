# ASU methanol import/filter results

**Bounded case verdict: PASS, six of six preregistered gates.** One authorized execution of the frozen [ASU protocol](asu-import-filter-protocol.md) completed on installed SpinFlow **0.3.0b3**, CPython **3.12.3**, Linux x86-64, using the **native backend and one thread**. The complete raw import matched the independent literal decoder exactly. Both complete corrected quadratures passed the unchanged `rtol=1e-11`, `atol=1e-7` comparisons against nmrglue and the independent integer-delay oracle.

This supplies independent-laboratory evidence for this int32 raw-import and recorded-GRPDLY correction case. Full raw-to-TopSpin final-spectrum, phase, peak and volume parity remains **UNVERIFIED**. Historical [S19 qualification](bruker-qualification-results.md) remains **FAIL**, with six failed comparisons out of eighteen. Human acceptance remains **UNVERIFIED**. No stable promotion follows from this result.

## Source and exact installed candidate

The [ASU CHM343 repository README](https://github.com/CHM343/NMR/blob/af3b9c2c4642e63e379bc349cb40e1fe8c5730e6/README.md) identifies the acquisition laboratory as Arizona State University. This acquisition is independent of the Wisconsin/Madison MMCD/BMRB cases. The [source archive](https://raw.githubusercontent.com/CHM343/NMR/af3b9c2c4642e63e379bc349cb40e1fe8c5730e6/Data/MeOD_25C_Temp_Cal_Bruker400.zip) is the public methanol calibration file `Data/MeOD_25C_Temp_Cal_Bruker400.zip`, at commit `af3b9c2c4642e63e379bc349cb40e1fe8c5730e6`. Its acquisition directory is `2023_10_04_AcAc_Solvents_Temp_Bruker400/232/`. The repository root [license](https://github.com/CHM343/NMR/blob/af3b9c2c4642e63e379bc349cb40e1fe8c5730e6/LICENSE) is GPL version 3; no separate dataset-specific license statement was found. Original provenance and license were retained. Raw binaries remain private validation inputs.

The [case manifest](../benchmarks/cases/asu-meod25-import-filter.json) pins the complete archive directory and known text metadata. The ZIP is **1,060,855 bytes**, Git blob SHA-1 `6b4d3850d0167a48d9301030bcbe458e5eb1b29e`. The extracted FID is **262,144 bytes**, CRC-32 `ee6a4852`; the header is **8,861 bytes**, CRC-32 `a0521f2d`. Hash-only preparation and independent lock review preceded the separate acquired GO.

The tested installed prefix was `/home/validator/s21/candidate-essentials-runtime-v2`. The runtime lock records SpinFlow distribution/Python package 0.3.0b3, NumPy 2.5.4, SciPy 1.18.1, h5py 3.16.0 and nmrglue 0.12 on Linux 6.8.0-147 x86-64 with glibc 2.39. Python reported `3.12.3 (main, Aug 31 2026, 10:18:26) [GCC 13.3.0]`. Its 2,639-file inventory binds the installed package, native extension and numerical dependencies. These identities describe the tested cohort, rather than any other build sharing the version number.

| Identity | SHA-256 |
| --- | --- |
| Original ZIP | `38c8d260169d42f0d15ab5c7b9ce7084735419688c9ea66e76dc34fa6b356094` |
| Original FID | `34457683c737ca05b3bc632b71959a3eb3f892a96e65886ea4c7a8b840cb147d` |
| Original acqus | `6d108f8ae59c7a4eaaff8abe414d1794fda35fc6ef7bc300326c13843fb8480c` |
| Installed `_native.cpython-312-x86_64-linux-gnu.so` | `811fcb73e59727b13096c453a6a8f57c3e08e52957f9c0425f04363024f97d34` |
| Installed nmrglue `fileio/bruker.py` | `bf537c35926e599068b07db4fab33af79ae0c4fbc76fa2a0167494dc54366eeb` |
| Actual `/usr/bin/python3.12` executable | `e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f` |

## All six original gates

The acquisition records `AQ_mod=3`, `PARMODE=0`, `BYTORDA=0`, `DTYPA=0`, `TD=65536`, `GRPDLY=76`, `DECIM=2568`, `DSPFVS=21`, `DIGMOD=3`, `SW_h=7788.16199376947` Hz, `SFO1=400.13311072` MHz and nucleus 1H. There are 32,768 signed real/imaginary int32 pairs: exactly 256 complete 1024-byte records, with no extra stored padding values. This is integer-delay/int32 acquisition coverage; fractional acquired-delay and native-float vendor-storage coverage are separate requirements.

| Gate | Original result | Evidence and scope |
| --- | --- | --- |
| ASU-01 — source/runtime/input identity | PASS | All pinned hashes were reverified before and after execution; the input dataset remained unchanged. Both externally frozen lock hashes stayed identical in the owner's before/after records. |
| ASU-02 — complete literal import | PASS | Standard-library little-endian signed `ii` decoding matched every imported real and imaginary value exactly: **32,768 pairs**, float64 shape **`[2,32768]`**, maximum absolute error **0**. No scaling or channel projection was applied. |
| ASU-03 — full time grid and metadata | PASS | Axis, complete grid and metadata checks all passed: a0 complex quadrature, time start/origin 0 s, step/dwell **0.0001284 s**, last center **4.2072828 s**, acquired size 32768, observation **400.13311072 MHz**, label/nucleus 1H and unknown ppm reference. All 14 header literals were checked; the importer-declared 13-key mapping, original header text including PARMODE, source hashes, zero padding and uncorrected filter state were retained. |
| ASU-04 — explicit correction policy | PASS | The sole operation was `digital_filter(axis="a0")`, native backend/one thread. Recorded delay **76** yielded trim **78**, reverse-tail fold **72**, float64 output **`[2,32690]`** and both quadratures. Axis/dwell/observation/original acquired count, corrected state and exact operation history checks passed. |
| ASU-05 — pinned nmrglue differential | PASS | All **32,690 complex points** versus nmrglue 0.12 `rm_dig_filter(z,2568,21,76,truncate_grpdly=False)`: maximum absolute complex-sample difference **`9.094947017729282e-12`**, unchanged **rtol `1e-11` / atol `1e-7`**. |
| ASU-06 — independent integer oracle | PASS | All **32,690 complex points** versus the separately implemented circular integer advance and reverse-tail fold: maximum absolute difference **`2.10144217385811e-9`**, the same unchanged **rtol `1e-11` / atol `1e-7`**. Python-integer addition before float64 conversion avoids int32 overflow. |

For ASU-06, the preregistered oracle is `u[j]=z[(j+76)%32768]`; output `y[j]=u[j]+u[32767-j]` for `j<72`, otherwise `y[j]=u[j]`, retaining `j=0..32689`. It uses neither an FFT nor a product/nmrglue helper. ASU-05 corroborates a shared declared correction policy; ASU-06 is a distinct mathematical implementation for this integer delay. Neither establishes physical instrument deconvolution truth.

The root GO was recorded at **2026-11-08 00:31:15.660841 UTC** after independent source/input/runtime lock approval. The sole VM owner completed the one invocation at **00:31:17.415505 UTC**, with exit **0**, one-CPU affinity, thread controls set to one, no timeout/signals and a reaped child. The owner records **one execution and zero replacement reruns**. No parameters, tolerances, phases, fits or source files were changed after observing outputs. Process elapsed/RSS records are diagnostic evidence, outside performance acceptance.

## Frozen evidence and review limits

All identities below refer to retained original files. Private run evidence is under `.artifacts/s21/asu-linux-run-once-v1/guest-run/`, including source copies, original ZIP/raw inputs, prepared/runtime locks, independent pre-execution review, root GO, owner process record and the complete `run-once-v1/` directory. Stdout, empty stderr and exit status are preserved alongside the original receipt. The [source helper](../tools/validate_asu_filter.py) is outside wheel contents and the public processing API.

| Record | SHA-256 |
| --- | --- |
| Four-file source freeze | `0a26e2e436a816c43a6d9b7e9f0d03bae7ff78b7a20fde0c3b347f6396ed3cfc` |
| Validator source | `a0c9ef8e73fe351f7c2184ead9061834121abaa5304ce5db50017cfa6f419e95` |
| Fixed protocol | `48e7fcf3235a7dee2197adaa6f4c41d2566f3bd035a06f0151e7a237938a3cbb` |
| Prepared lock | `da68527167f6aba794093e22fc2515bfa991b4e691106064a53b955876db2370` |
| Installed runtime lock | `7efc6b635192077d1fe95377bbfd83e906972021d2bf5f409f3157f15d39a345` |
| Independent pre-execution lock review | `c41efe5450332dd3d332ef90ae1849a42049375da8d464253a78e936a17d2556` |
| Original six-gate execution receipt | `d3dba75a454e49536d8316214304dab7f9d66fff50be66f233648c089be83c51` |
| Owner single-execution summary | `74fe1efbe4e4e496e8ecbe6db6d8973ded4b6bc855afd24e33159c4d331916a3` |
| Producer post-run reconciliation | `c9d1cc41b82df7006fac5b02788c18c7d161769de54074eeb4fb95a0445ffd46` |

The producer reconciled the original receipt, all six unique mandatory IDs, accepted source/lock identities, original command/authorization, owner before/after lock records and preserved logs. A separate streamed byte-only audit of the retained ZIP/FID/header matched their frozen hashes and CRC values. No numerical sample decoding, product/reference invocation or acquired rerun was performed during this reconciliation. The unchanged executed helper reports complete input/source/runtime revalidation before and after its comparisons; the host receipt audit does not independently rehash physical guest runtime files after execution.

**Corrected sample arrays and separate decoder/reference/oracle arrays were not retained.** The run directory contains `receipt.json`, `stdout.txt`, `stderr.txt` and `exit-code.txt`. The hash-bound original invocation performed the full-point comparisons recorded above. Subsequent reviewers can audit those records, the exact helper/oracle implementations, locks and original inputs, but cannot separately recompute the reported acquired errors from retained corrected arrays. The independent lock review occurred before execution; it is not a second calculation of the acquired outputs. No such additional execution is claimed or needed to report this original result faithfully.

The original TopSpin audit includes automatic phase and unsupported baseline correction, and a complete original numerical conversion/processing script was absent. This case therefore supplies no final-spectrum parity, calibrated ppm/chemical-peak accuracy, absorptive phase or signed peak/volume/concentration evidence. Downstream FFT, windowing, phasing, baseline correction, peak picking and integration were outside the executed recipe. General NMRPipe parity, broader vendor encodings, native-float records, stable profile promotion and human acceptance remain unverified. The separate [S20 stored-spectrum arithmetic result](stored-analysis-results.md) does not change that boundary or the S19 failure.
