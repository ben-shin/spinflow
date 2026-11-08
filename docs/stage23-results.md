# Stage 23: exact raw conversion agreement for one Varian 2D case

Stage 23 now includes one preregistered, zero-tolerance raw-conversion
comparison for the cached `cpjdcptan2d` C13/N15 States 2D case. An unchanged
493-byte conversion script was used; it is not a complete spectral-processing
workflow. No new conversion was run for this comparison: the original retained
reference was reused. The comparison was run against the reviewed v4 checker;
the earlier v3 header guard's raw-word finding remains part of the history and
was corrected in v4's numeric-float32 marker interpretation.

## Recorded comparison

The full-float32 data had shape `(4, 166, 1500)`, representing four
quadratures and 996,000 total samples (249,000 per channel). Three independently decoded arrays
were compared: the installed product output, the literal FID decode, and the
original-format output after the preregistered channel signs `[1, -1, -1, 1]`.
All three pairwise comparisons were exact: zero differing samples and maximum
absolute difference zero, with unchanged `rtol=0` and `atol=0`. This is a
conversion result for this one case, not a general vendor-format or scientific
accuracy claim.

| Check | Result |
| --- | --- |
| Frozen actual comparison audit | PASS, 35 checks |
| Product vs literal FID, all four channels | Exact, zero differences across 996,000 total samples |
| Signed original-format output vs literal FID | Exact, zero differences across 996,000 total samples |
| Product vs signed original-format output | Exact, zero differences across 996,000 total samples |
| Numeric tolerance | `rtol=0`, `atol=0`; no fitting, alignment, scaling or tolerance change |

Source-derived time-axis observations were approximately 125.6906107 MHz and
50.6480504 MHz; the original conversion header records rounded values
125.691 and 50.648 MHz and declares carrier positions 55 and 120 ppm. The
product has no absolute reference. These are not an independent frequency or
ppm-axis match, and this comparison did not run the full spectral-processing
recipe, FFT, phase, peak or volume checks.

## Scope and status

The comparison used a retained reference artifact and reviewed application
build. It did not change the released v0.3.0b3 application, release assets,
schemas or support registry. No new support profile is promoted; the existing
Varian 2D experimental classification remains unchanged. The old v3 header
marker rejection is preserved as a historical checker defect, while the v4
raw quadrature comparison passes for this single case.

The original spectral-processing script is still unavailable. S19 remains
FAIL on six of eighteen fixed targets. Original full-processing parity,
absolute ppm agreement, scientific phase/peak/volume acceptance, physical
machine and researcher acceptance, and v1 readiness remain unverified or
failed as previously recorded. The retained acquisition, conversion output,
software package and private execution materials are not redistributed. See
the [Stage 22 results](stage22-results.md) for its separate tutorial workflow.
