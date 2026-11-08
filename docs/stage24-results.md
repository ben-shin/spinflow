# Stage 24: figure baseline and import diagnostic

The Stage 24 baseline contains 40 measured figure-workflow trials: five trials
in each of eight scenarios. The original per-trial outcomes remain 25 PASS and
15 with PDF semantics UNVERIFIED. Separate checks passed for 41 retained SVG
files and 40 host-rendered PDF files. These separate checks do not rewrite the
original cohort's outcomes.

The three warm-format scenarios (PNG, SVG and PDF) used five workers per format,
15 warm workers total. Each worker made one first call followed by five later
calls; those repeated calls are clustered within the same worker. These timing
observations provide a baseline only; no speedup or performance improvement was
established. One additional import-profile run retained a nested import trace.
It is diagnostic, not causal timing or a benchmark. A two-file startup
candidate has not yet been qualified or timed on Linux.

No product or release change is represented by these results. Preserve the
original PASS/UNVERIFIED distinctions when comparing future runs.
