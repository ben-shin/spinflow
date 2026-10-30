# New Bruker acquisition qualification protocol

**PREDECLARED; acquired candidate execution requires a separate root-approved installed-candidate lock.** This finite S19 protocol leaves every product algorithm, precision, public API and existing numerical tolerance unchanged. Results belong in [the separate record](bruker-qualification-results.md). It does not promote either experimental profile to stable support.

The inputs are [BMRB bmse000002](https://doi.org/10.13018/BMSE000002), 2,3-diphospho-D-glyceric acid, from the Madison Metabolomics Consortium: Francisca Jofre, Mark E. Anderson, John L. Markley and Melanie E. Ulrich. Exact directories are `nmr/set01/1H` and `nmr/set01/1H_13C_HSQC` under the [official entry](https://bmrb.io/ftp/pub/bmrb/metabolomics/entry_directories/bmse000002/). [Source pins](../benchmarks/cases/bmse000002-qualification.json) record every downloaded input's URL, size and SHA-256. The raw data and vendor files are not redistributed. [BMRB's access policy](https://bmrb.io/metabolomics/data_policy.shtml) establishes free public availability, not an explicit redistribution license.

These are new acquisitions relative to the inspected local qualification history, which previously used bmse000001 Bruker 1H/HSQC/TOCSY, Agilent protein examples and WR41 high-dimensional data. They share the Madison source/instrument family with the earlier Bruker acquisition. This is not independent laboratory use or a broad instrument cohort. Preflight saw entry metadata, public assigned-shift snippets, headers, pulse-program descriptions and audit records, and hashed downloaded sample bytes without decoding them. The next reference-only preparation sees the deposited 1D peak list and archived HSQC arrays, but no SpinFlow acquired numerical output. The work is not fully blinded; reference-only selection is explicitly disclosed.

## Fixed acquisition scope

1D is presaturated zgcppr, little-endian int32, AQ_mod3, DIGMOD1, TD19478, recorded GRPDLY67.985595703125, SW_h6493.50649350649Hz and SFO1 499.842349248MHz. Import has two float64 channels by9739 complex points; the78848-byte file includes1024-byte block padding. Digital-filter correction retains the existing69-point trim policy.

HSQC is multiplicity-edited hsqcedetgpsisp2.2, FnMODE6 Echo/AntiEcho, AQ_mod3, AQSEQ0, PARMODE1, FnTYPE0, DIGMOD1, little-endian int32, directTD1024 and indirectTD256, GRPDLY67.985595703125. Its1048576-byte ser gives four float64 channels by128 by512. Physical axis order is indirect13C then direct1H; quadrature order is RR,IR,RI,II with indirect bit first. Indirect SW145ppm times SFO1 125.69335172859MHz determines its width; direct parameters are as above. Mixed signs are expected; the positive separable-line automatic-phase model is not invoked.

Both production header-only descriptions passed during preflight with every numeric-payload open blocked. This establishes header compatibility only. It does not validate sample content or biological interpretation.

## Preparation and immutable execution lock

`tools/validate_bruker_qualification.py prepare` validates all source hashes without importing SpinFlow, retains all18 deposited Peak1D records in document order and freezes the exact recipe below. It independently decodes the archived HSQC 2rr/2ir/2ri/2ii int32 arrays, applies recorded2^-6 scaling and full-row tile layout (direct XDIM=SI1024; indirect XDIM128), and records their size/dtype/parameters.

Before inspecting the archived array, the selection rule is fixed: select the highest positive interior RR sample, then the next positive sample whose inclusive9-by9 box is disjoint; repeat for the largest negative magnitudes. Exclude a four-sample edge margin. Ties use flattened row-major order. No threshold, phase, normalization or fitted location is used. If a sign has no remaining positive signed score, omit that selector slot and record the resulting count. Freeze up to four boxes, their original point and ppm bounds, signed center values and independently calculated integrals for every archived component. These are mathematical test regions, not assigned or resolved peaks. At most four boxes cannot establish the earlier five-region weak/strong contract; **no weak/strong recovery or sensitivity claim is made**.

The private scientific lock pins this public protocol, source manifest, harness bytes, all target rows, recipe, regions, expected integrals and gates before any candidate numeric execution. A separate environment lock pins the installed SpinFlow0.3.0b2 package/native modules, Python, NumPy and nmrglue versions/files. Root approval and the installed-candidate identity are required before `run`. Changing any locked protocol, targets, regions, harness or source bytes requires a new documented lock before execution; never retune from a failed acquired result. The public protocol remains fixed while the results document is updated.

## Stage A: exact import and the existing narrow 1D recipe

The independent raw decoder uses byte buffers and literal integer layout, not product/nmrglue readers. The 1D oracle deinterleaves real/imaginary values after removing only declared padding. HSQC uses E=even complex row and A=odd row, C=E-A and S=i(E+A), without averaging. Fixed channels are [C.real,S.real,C.imag,S.imag]. Require exact sample equality, float64 dtype, shapes and quadrature IDs. Independently check every raw time coordinate, nucleus, physical axis order, observation frequency and missing-before-reference ppm calibration.

The 1D recipe is the existing magnitude-position protocol, with only the new acquisition's header-derived reference:

1. digital_filter on a0;
2. zero_fill a0 to32768;
3. forward centered fft a0;
4. reference a0 at0Hz with SF499.839904987752MHz and carrier `(499.842349248-SF)*1e6/SF` ppm.

There is no added window, fitted phase, baseline correction, conjugation, coordinate reflection or gain adjustment. Preserve float64 and both quadratures. Digital-filter differential corroboration uses the pinned nmrglue non-truncated-GRPDLY function with existing rtol1e-11/atol1e-7. This comparison is not independent biological truth; analytical controls remain the mathematical authority. Native versus NumPy arrays use existing rtol1e-11/atol1e-5, with equal shapes/dtypes/axes/quadratures. All absolute processed ppm coordinates use the existing2e-5ppm gate against the independently calculated centered FFT grid.

For **every** deposited target, select the magnitude maximum strictly within +/-0.004ppm; require absolute position discrepancy strictly<0.002ppm, exactly as in the previous Bruker1D protocol. Retain every row, failure, empty search region and measured discrepancy. The18 target coordinates are4.5159,4.51,4.5005,4.4914,4.485,4.0497,4.0429,4.0404,4.0334,4.0283,4.0214,4.019,4.0121,4.0088,3.9995,3.9903,3.9781,3.9691ppm. These are deposited picked maxima, not a promise of independent resonance identity or curated assignments.

## Stored-reference numerical integration lane

This lane tests numerical analysis on the **unchanged stored TopSpin matrices**, not processing raw HSQC to reproduce TopSpin. For each independently decoded archived component, construct a single-real-channel frequency-domain Dataset without changing any sample or dtype. Do not assign independently meaningful phase conventions to the four stored component labels. Verify exact full-array retention and every absolute ppm point against `OFFSET-index*SW_p/(SF*SI)`, with the existing2e-5ppm coordinate limit.

Integrate every frozen region with baseline none in physical Hz squared. The independent oracle uses a scalar sum of the four corner values divided by4 for every original cell, multiplied by the absolute two-axis cell area, with math.fsum accumulation. It does not call the product integration/interpolation functions. Require relative discrepancy<=1e-10, retaining signed values, flags and absolute errors. Do not use max(reference,1) to hide a small denominator. Zero denominators, or signed integrals no larger than1e-10 times their absolute-sample integral, are cancellation-dominated and remain explicitly unverified. This uses the existing integration precision gate to identify unusable relative comparisons; it is not a relaxed acceptance tolerance. No raw-to-TopSpin volume-ratio fallback is allowed.

Archive direct parameters: SF499.839903273614MHz, SW_p6493.50649350651Hz, SI1024, OFFSET11.3891ppm. Indirect: SF125.684493079274MHz, SW_p18225.5360006456Hz, SI1024, OFFSET142.9883ppm. Recorded metadata calibration is not an independently measured chemical-shift accuracy claim. Source hashes must match before and after execution.

## Original actions and confounders

Original audit actions, in deposited order (acquisition `zg` and ICON-NMR comment precede these processing actions):

| Acquisition | Ordered recorded processing actions | Confounding effect |
|---|---|---|
| 1H | em LB0.3/TDeff16K/SI32K; ft FT_mod6/PKNL1/SI32K; apk; abs ABSG5; pk fgphup PHC0=46.99228/PHC1=-60.65 | Window, digital-filter semantics, fitted phase, baseline correction and cumulative phase differ from the fixed magnitude recipe. Final procs PHC0=293.2586/PHC1=-128.6056 are separately retained. |
| HSQC | xfb F2 SI1K/WDW4/SSB2/FT_mod6/PKNL1/PHC0=59.2625/PHC1=-8; F1 SI1K/ME_mod2/NCOEF32/WDW4/SSB2/FT_mod6; pk2 PHC0=43.6375/PHC1=0; pk1 PHC0=-3.9625/PHC1=0; pk2 PHC0=23.4271/PHC1=-36.85; pk1 PHC0=-21.51807/PHC1=34.7 | Linear prediction, windows and cumulative phases differ from the historical SpinFlow exponential/autophase workflow. Final procs direct PHC0=126.3271/PHC1=-44.85; indirect PHC0=-25.48057/PHC1=34.7. Edited signs must remain. |

Deposited audits identify TopSpin2.1 and July2011 processing. Pulse programs are acquisition sequences, not executable processing scripts. No original NMRPipe conversion/processing script was found in the inspected acquisition/pdata directories. This stage does not engineer a substitute NMRPipe recipe, install new reference tools or add unsupported algorithms.

Full raw-to-original final-array/phase/sign/volume equivalence, intermediate processed quadrature parity, NMRPipe parity, weak/strong sensitivity, biological assignments, concentrations, independent consumer reopening, representative hardware performance and actual human researcher acceptance remain **UNVERIFIED**. A narrow Stage A or stored-reference mathematical pass closes none of those gates.
