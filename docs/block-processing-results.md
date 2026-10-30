# Acquired 3D/4D block-processing results

The explicit native-HDF5 block path processed genuine acquired 3D and 4D data with all quadratures and outputs exactly matching the frozen eager reference. It trades throughput for a smaller working set: the full 4D case with a 64 MiB application budget recorded a median **about 77 MiB fork-to-exit child lifetime RSS upper bound**, versus 1194.5 MiB for eager processing, while taking 2.38× as long by the median paired ratio. This is a conservative lifetime measurement with an inherited launch floor, explained below. The ordinary eager 2D regression passed its predeclared gate. Default Dataset/API/CLI behavior remains eager.

The measured sources are baseline commit `16cc910b46990d32f3fbefbef3c0611afe573f86` and candidate-v2 archive SHA256 `edf690d6e1c5d5bbdf295f995f8fff73490e52013fec4c56abaddef93b84a6a4`. [Runtime/build pins](evidence/blocks-runtime-v2.json) distinguish measured archive/wheel bytes from later [Git publication mapping](evidence/blocks-source-map.json). No native C++ or FFT-library change was needed: both installed native binaries have SHA256 `854608737e5cfef2aef67fe9d10e39e8f13d8d6178609adcaffbe11a779c0a80`.

## Cases and scientific scope

The official [BMRB 5329 WR41 archive](https://bmrb.io/ftp/pub/bmrb/entry_directories/bmr5329/timedomain_data/WR41.tar.gz) contains independent 3D HNCO and 4D CC-NOESY acquisitions associated with [PDB 1L7Y](https://www.rcsb.org/structure/1L7Y). They are real acquisitions, never repeated or tiled 2D. Archive 415,008,339 bytes, SHA256 `fe6e0be28c864e4008a30cf33a167eced607d978e1f2e4e764440e7501e58414`. [BMRB’s distribution statement](https://bmrb.io/bmrb/distribution.shtml) shares its data usage/license policy with wwPDB; the linked [wwPDB policy](https://www.wwpdb.org/about/usage-policies) specifies CC0. Source binaries remain private; URLs and complete identities are published in [scientific evidence](evidence/blocks-science-v2.json).

| Acquisition | All-channel native shape | Numeric payload | Physical axes, slowest→direct |
| --- | --- | --- | --- |
|3D HNCO|8×35×40×512 float32|21.875 MiB|15N,13CO,1H|
|4D CC-NOESY|16×16×18×60×320 float32|337.5 MiB|13C,13C,1H,1H|

All 94,208,000 original big-endian int32 scalar values are exactly representable in float32; block scale 0 and original scan counts are retained without amplitude fitting/division. An explicit mixed-radix reader, independent nmrglue indexing and actual pinned NMRPipe 13 conversion agree on every raw word. The HNCO echo/antiecho sum/difference basis agrees with the external Varian Rance-Kay macro and reconstructs every raw channel exactly. The original pulse programs establish States-TPPI parity; its reversal is independently checked. These benchmark conversion tools establish the selected fixtures, not general product 3D/4D vendor import.

The original parameters/pulse programs/deposition are pinned. The archive provides no original processed spectrum or processing script. Full recipes apply a fixed sine window (off 0.5, end 1, power 1), complete-axis FFT and recorded manual rp/lp phase, direct→slowest, retaining every component. Dwell, half-dwell origins, nucleus and transmitter frequencies are preserved. Absolute ppm calibration is unknown (`reference_ppm=null`): results validate relative Hz grids and numerical processing, with no claim of absorptive biological assignment accuracy or NMRPipe processed-spectrum parity. No alignment, rescaling, phase fitting, NUS reconstruction or per-block/global baseline fitting is used.

The 4D quarter/half cases select the first 4/8 of 16 actual a0 time increments, retaining all other samples/channels and honest original crop history. The 3D control selects 12×16×128 actual samples, then exercises 14 fixed stages including direct zero fill to 256, frequency crop, reverse, transpose and explicit one-axis imaginary discard. It is an operator control, not an additional full acquisition. The seventh case uses the original acquired 2D protein/full 14-step recipe through the ordinary eager path in both versions.

## Complete CLI latency

One fresh installed CLI process per observation includes startup, normal complete planning/finite scans/per-step validation, product hashes/provenance, all temporary file I/O, output close/cleanup and CLI reporting. Independent output comparisons run afterward in separate processes. Raw product API timing fields have different eager/block boundaries and are retained without cross-comparison.

Seven alternating pairs per case (BC, CB, BC, CB, BC, CB, BC), one warmup per case/lane: **112 observations = 98 measured + 14 warmups; 49 measured + 7 warmup agreement checks**. No observations were dropped, no cache purge/fsync/overhead subtraction. Each cell is median[min–max]; IQR in seconds, except the paired ratio. Quartiles use Python inclusive quantiles. Ratios are the median of seven matched C/B ratios.

| Case | Baseline seconds | Candidate seconds | Paired C/B ratio |
| --- | --- | --- | --- |
| 3D full, 64 MiB | 0.177 [0.165–0.187]; 0.007 | 0.328 [0.325–0.427]; 0.009 | 1.938 [1.824–2.286]; 0.123 |
| 4D first 4 a0 increments, 64 MiB | 0.552 [0.499–0.622]; 0.031 | 1.104 [1.019–1.438]; 0.163 | 2.183 [1.639–2.639]; 0.204 |
| 4D first 8 a0 increments, 64 MiB | 1.215 [1.080–1.445]; 0.187 | 3.118 [2.933–3.442]; 0.160 | 2.537 [2.030–2.931]; 0.449 |
| 4D full, 64 MiB | 2.736 [2.397–3.030]; 0.116 | 6.519 [5.698–9.684]; 0.514 | 2.377 [2.040–3.587]; 0.061 |
| 4D full, 128 MiB | 2.711 [2.395–3.082]; 0.277 | 5.333 [4.743–7.999]; 0.398 | 1.987 [1.710–2.776]; 0.262 |
| 3D 14-stage subcube control | 0.091 [0.089–0.103]; 0.002 | 0.200 [0.199–0.202]; 0.001 | 2.216 [1.930–2.233]; 0.037 |
| Ordinary eager 2D regression | 0.672 [0.661–0.693]; 0.017 | 0.674 [0.661–0.718]; 0.020 | 1.003 [0.967–1.050]; 0.020 |

The block path is slower in these cases. Ordinary eager 2D has paired median ratio 1.00262 (range 0.96714–1.05014), passing the predeclared≤1.20 regression gate; this is one selected eager case, not universal performance parity. [All 112 compact observations, distributions, commands and 49 measured agreement identities](evidence/blocks-v2.json) remain available, including both warmups and long observations.

## Memory policy and lifetime RSS limitation

Both lanes use the same QEMU/WHPX guest, one vCPU,4 GiB physical RAM,128 GiB virtual disk, host Ryzen 7 9800X3D logical CPU 2 affinity mask 4, all numeric thread pools 1. OS file cache, host load, thermals and the SMT sibling are uncontrolled. The eager ND policy allows 8 GiB working arrays; the explicit block path selects 64/128 MiB. These intentionally different application policies share physical hardware/precision/recipe and are **not hard RSS caps**. Ordinary eager 2D allows 1 GiB in both versions. [The uniquely named hardware snapshot](evidence/blocks-hardware-v2.json) is byte-pinned; input HDF chunk allowance, two 64 KiB caches, native FFT workspace, metadata and hash buffers are charged in each block plan.

The table reports **true Linux wait4 fork-to-exit child lifetime high-water RSS**, including the inherited pre-exec controller floor. It is a conservative upper bound on post-exec CLI RSS. Cells are median[min–max]; IQR in MiB.

| Case | Baseline lifetime RSS MiB | Candidate lifetime RSS MiB |
| --- | --- | --- |
| 3D full, 64 MiB | 152.2 [152.2–152.2]; 0.035 | 78.7 [78.6–78.7]; 0.113 |
| 4D first 4 a0 increments, 64 MiB | 377.8 [377.7–377.8]; 0.033 | 98.9 [98.8–98.9]; 0.027 |
| 4D first 8 a0 increments, 64 MiB | 641.6 [641.5–641.6]; 0.062 | 80.4 [80.3–80.7]; 0.184 |
| 4D full, 64 MiB | 1194.5 [1194.5–1194.5]; 0.004 | 76.9 [76.6–78.1]; 0.598 |
| 4D full, 128 MiB | 1194.5 [1194.5–1194.6]; 0.008 | 88.7 [88.3–88.9]; 0.186 |
| 3D 14-stage subcube control | 68.4 [66.9–71.6]; 2.484 | 67.9 [66.9–71.6]; 2.484 |
| Ordinary eager 2D regression | 388.8 [388.7–389.2]; 0.186 | 388.8 [388.6–389.1]; 0.436 |

The frozen controller docstring says: “Worker RSS never includes a comparison process, warm resident state or earlier trials.” CLI executions are fresh and comparisons execute separately, but that sentence is too broad for Linux’s OS high-water metric: the controller retains earlier reports and can contribute an inherited pre-exec floor. A post-cohort diagnostic with a 100 MiB touched parent recorded parent VmRSS 114,740 KiB; the minimal exec child’s `/proc` VmHWM was 11,412 KiB while both its own ru_maxrss and parent wait4 reported 117,309,440 bytes. [Exact probe and qualification](evidence/blocks-v2.json) retain raw receipt SHA256 `c4854ec3c4bbaee47ca62f481b269baaf8f08b450ee451476ec44e0a0d773a48`. Frozen harness/archive/raw data were preserved. Nothing was subtracted, relabeled as pure post-exec peak, or replaced.

Accordingly, no small-control algorithmic RAM benefit, incremental peak or flat-scaling RAM claim is made. The full 4D lifetime upper bound remains conservative. Separate instrumented fresh single-API diagnostics recorded `/proc` VmHWM 85,782,528 bytes for blocks and 1,251,500,032 bytes for eager; these corroborate the selected task only and are **not paired distributions or performance timings**. Kernel file-backed page cache is outside process RSS.

All 568 recorded stages across 48 bounded observations stayed within their planned read/write shapes/counts and selected working estimates. The full 4D 64 MiB plan estimated 67,086,140 bytes, including explicit external reserves 9,267,004 bytes; largest observed read 16,384,000 bytes and packing 1,802,240 bytes. Temporary disk estimate 711,983,104 bytes covers current+next stores and metadata. Sparse large-header/scanner probes separately demonstrate constant first-yield control allocation at 100k/1M/2M slabs; they are not real large-acquisition payload tests.

## Verification and profile

All 49 measured pairs and 7 warmup pairs are byte-exact in every numeric value and identical in complete axes/metadata/global provenance, including unchanged signed peak bins and six fixed all-channel ROIs. Container file hashes differ where HDF layout differs; canonical data/metadata identities are compared separately. All 14 acquired-control prefix pairs are exact. An independent bounded NumPy full-line FFT oracle checked every component on all 7 physical axes; worst relative error 1.47443e-7, below the unchanged 2e-6 FFT gate. Processed/per-component/ROI gate 2e-5; complete relative Hz coordinates/axes/history exact. No absolute ppm gate is pretended applicable. [Scientific receipts](evidence/blocks-science-v2.json) contain full identities, phase/window settings, ROI selectors and every prefix/FFT result.

The fresh installed Linux wheel passed **927 tests + 101 subtests,0 skips in 26.40 s**, ordinary deadlines and all real fixture roots, with 22 production modules imported exclusively from the installed wheel and all 23 installed files unchanged. The independent Linux probe passed 9/9, including actual successful POSIX foreign-scratch replacements in both empty and arithmetic paths, source/foreign-byte preservation, no publication and closed owned handles. Four 3D/4D float32/64 canonical digest/copy controls passed.

Separate [instrumented profiles](evidence/blocks-profiles-v2.json) explain the cost: bounded processing writes intermediate stores and keeps all finite/source/output checks. It scanned 13.448GB through 4,260 isfinite calls versus 4.601GB/14 calls for eager. Bounded execute_stage 3.897 s, HDF write 1.705 s, file hash 0.904 s and owned intermediate unlink 0.838 s were prominent profiled cumulative costs; these nested times are not additive. The eager native FFT subtree took 0.309 s. Existing unchanged native FFT code was sufficient; no new C++ kernel was introduced.

## Replay and retained artifacts

Use separate Python 3.12 Linux installations built from the pinned baseline/candidate source archives and their exact pinned dependency versions; measured wheel SHAs are baseline `5c01ce087284a77f810e5b14ca45329708f1d311a25a8a52fa643601f38d1ec1`, candidate `a33d9fead55b04709fe0d336e2122e2702d9f21300bc197dee4e54115445decd`. Native HDF fixtures require the pinned public acquisitions, their original metadata and declared audited conversion; absolute anchors are left unknown. The primary controller was run from its separately frozen archive SHA256 `949e5a35e0b073fcdbe2b58192bcedf4784f2fe32ceefae41f5e4fc7e17ade7c`:

```sh
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
/home/validator/blocks/baseline-runtime/bin/python \
  /home/validator/blocks/frozen-harness-v1/benchmarks/block_compare.py \
  --baseline-python /home/validator/blocks/baseline-runtime/bin/python \
  --candidate-python /home/validator/blocks/candidate-v2-runtime/bin/python \
  --cases /home/validator/blocks/reference-anchors-v2/cases-frozen-v1.json \
  --output /home/validator/blocks/paired-v2 --pairs 7 --warmups 1
```

The public `benchmarks/block_prepare.py`, `block_cases.py`, `block_compare.py`, `block_audit.py` and separate diagnostic oracle/profile tools expose the conversion, selection, measurement and comparison logic. The exact private fixture/ROI/runtime/closed-log audit bundle and licensed reference-converter installation are retained privately; a public checkout alone is not the byte-exact replay bundle. Native 3D/4D output is independently reopened/decoded through h5py in the audit; no full-ND NMRPipe/Sparky export or GUI consumer claim is made. See [supported profile and refusals](block-processing-protocol.md) and the [executable tutorial](tutorials/bounded-block-processing.md).

V1 diagnostic observations/source/builds/profiles/binaries remain retained separately. The reviewed repair replaced a growing scanner iterator, secured unbuffered scratch descriptors through HDF writes, and completed API elapsed accounting through cleanup; arithmetic/native code stayed unchanged. [Superseded evidence](evidence/blocks-superseded.json) and [operational history](evidence/blocks-operational.json) disclose the private pre-freeze authoring failures, new-overlay expansion and preservation scope. All 112 final trial binaries remain retained. The verified private guest shut down normally on 2026-10-30 at 14:49:02 UTC (PID 55180 exited, port 22231 closed, zero scientific jobs, no force kill/deletion). The final read-only graph checked 1,430 runtime/source/input references, all 112 trial output hashes and 56 agreement reports with zero errors/conflicts. Four actual full 3D/4D baseline/candidate endpoints (794,265,864 bytes) and all 14 control-prefix pairs were copied privately to the host and hash-verified for offline audit. The closed 69,110 byte serial log SHA256 is `3fb298768fe002e7f2f3441cdd3cfed9baa862c8d0d4fdd6d2be446195074ce9`. Only closed source/evidence/log files enter the explicit final seal; the opaque VM disk/UEFI and unrelated large test scratch are excluded. Old S15/S16 preservation qualifications remain explicit; no historical seal or earlier hardware snapshot was rewritten.
