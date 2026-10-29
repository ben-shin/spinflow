# Public Bruker 1D and bounded analysis

This is an **experimental, narrow** path from one public complex Bruker 1D FID to a native spectrum, analysis reports and an optional figure. It does not establish NMRPipe parity, general Bruker support, reliable assignments or absolute concentrations. The nine source and archived TopSpin files are fetched to an ignored local directory; they are not redistributed in this repository. Their URLs, byte counts, SHA-256 checksums, original authors and reference scope are in the [manifest](../tests/fixtures/bruker_public_manifest.json). [BMRB's policy](https://bmrb.io/metabolomics/data_policy.shtml) says the community data are publicly available free of charge; it does not supply an explicit redistribution license in that statement.

## Reproduce the public 1D path

From a clean checkout on Ubuntu 24.04, install the core processor plus the optional, Qt-free figure extra. The build needs Python 3.12 or 3.13 and a C++20 compiler, as described in the [Linux build guide](linux-build.md):

```bash
bash tools/bootstrap.sh --plot
.venv/bin/python tests/fixtures/fetch_bruker_public.py
.venv/bin/python tools/validate_public_bruker.py > .artifacts/science11-realdata/bmse000001/validation.json
.venv/bin/spinflow plan examples/bruker-1d-public.json --explain
.venv/bin/spinflow run examples/bruker-1d-public.json
```

The [recipe](../examples/bruker-1d-public.json) reads the hash-pinned `bmse000001` 1H acquisition, explicitly corrects its recorded positive `GRPDLY`, zero fills, transforms and sets a fixed ppm anchor from the archived `procs` frequency and acquisition carrier. It neither fits the reference to peaks nor guesses a digital filter from firmware. `run` refuses an existing output; use a new output path when repeating a run. The original `acqus`/`fid` remain unchanged.

The optional analysis commands operate on **fully frequency-domain** native or declared processed spectra. This 1D example estimates a first-order phase, then detects signed extrema, proposes fixed-width regions and integrates each independently:

```bash
.venv/bin/spinflow autophase .artifacts/science11-realdata/bmse000001/spinflow-processed.nmrh5 .artifacts/science11-realdata/bmse000001/spinflow-phased.nmrh5 --report .artifacts/science11-realdata/bmse000001/spinflow-phase.json --first-order
.venv/bin/spinflow peaks .artifacts/science11-realdata/bmse000001/spinflow-phased.nmrh5 --sign both --threshold 5000000 --baseline none --min-separation 6 --integrate --half-width 2 --output .artifacts/science11-realdata/bmse000001/spinflow-analysis.json --csv .artifacts/science11-realdata/bmse000001/spinflow-analysis.csv
.venv/bin/spinflow figure .artifacts/science11-realdata/bmse000001/spinflow-phased.nmrh5 .artifacts/science11-realdata/bmse000001/spinflow-analysis.svg --analysis .artifacts/science11-realdata/bmse000001/spinflow-analysis.json
```

These explicit threshold/width choices are a reproducible demonstration, not universal defaults. Inspect and edit proposed bounds before using volumes scientifically. The combined JSON retains the peak report, geometric region proposal and signed integration report. CSV flattens the peak sign, coordinates, region bounds, physical units and flags for tabular inspection. Both are linked to the exact numerical dataset by a digest. A figure checks the report digest, labels only a readable subset of peaks, and points to the full JSON. SVG and PDF retain vector graphics; PNG is rendered at 300 dpi. Matplotlib is optional (`--plot` or `spinflow[plot]`); headless core commands do not import Qt or Matplotlib.

For manual integration, provide a JSON list of regions with stable axis IDs and inclusive sample-center bounds, then run `spinflow integrate INPUT REGIONS.json --output integrals.json --csv integrals.csv`. For this example, one region could be:

```json
[{"id":"near-3.1-ppm","bounds":{"a0":[3.07,3.14]},"unit":"ppm"}]
```

`peaks --integrate` refuses overlapping regions unless `--allow-overlap` is explicit. Opted-in overlaps are reported as **independent** measurements; no sum is presented. Baselines are explicit (`none`, `constant` with a value, or `edge_linear` for integration). Volumes are signed intensity·Hz for 1D and intensity·Hz² for 2D, not concentrations or fitted multiplet areas. Fully frequency-domain 2D spectra support automatic peaks and rectangular integrations; automatic phase also supports full-hypercomplex 2D with identifiable positive separable lines. The [2D guide](real-bruker-2d.md) specifies both-axis operations, gauge, fixed public recipes and bounded real-data evidence. Manual phase remains available through recipes on supported complex axes.

## Method and evidence limits

The digital-filter operation uses the recorded positive `GRPDLY` and a declared fractional Fourier advance, reverse-tail fold and trim policy corresponding to [nmrglue's non-truncated mode](https://nmrglue.readthedocs.io/en/latest/reference/generated/nmrglue.fileio.bruker.rm_dig_filter.html). SpinFlow's implementation is original Python/NumPy code and does not imply that every processing operation runs in C++; the FFT in the declared recipe uses the selected C++/PocketFFT backend. Unknown or zero delay, DSP lookup/defaults, already corrected data and broader vendor profiles remain unsupported. The separate [fully sampled 2D profile](real-bruker-2d.md) supports FnMODE 5/6 with explicit direct-axis correction.

Automatic phase fits isolated positive Lorentzian-like complex lines and a shared correction ramp; it is a bounded estimator rather than a general or [Heuer](https://doi.org/10.1016/0022-2364(91)90189-Z) reproduction. Zero-order is the CLI default; `--first-order` requires identifiable lines across the spectrum. Always inspect the phased result and report flags. Peak picking finds local signed extrema rather than deconvolved assignments. Its default noise estimate uses the central amplitude population's median absolute deviation scaled for a Gaussian standard deviation; stationary central noise is an assumption, and the nominal SNR setting is not a false-discovery-rate guarantee. Region integration uses trapezoids with absolute Hz coordinate measure and records baseline, overlap, boundary and model flags. Zero filling changes the density and correlation of candidate extrema; inspect thresholds and candidates instead of treating every local maximum as a true resonance.

The public recipe's magnitude-only check compares eight archived TopSpin peak **positions** against a fixed prior reference, with a 0.002 ppm tolerance; its observed maximum difference is 0.001276 ppm. A separate installed Linux analysis demonstration used a declared 0.3 Hz window, bounded phase estimation and a 5%-of-maximum peak threshold with 4 Hz separation; its nearest-candidate maximum difference was 0.0002041724 ppm. Those are different processing/selection protocols. Neither certifies phase, amplitude, baseline, individual peak identity or integrals, and neither compares an NMRPipe executable or reopens exported data in an independent consumer. See the [validation record](public-validation.md), [format limits](formats.md) and [roadmap](../ROADMAP.md) before applying it to other data.
