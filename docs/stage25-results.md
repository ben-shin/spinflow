# Stage 25: native result export checks

One pair of real-spectrum exports was made from the accepted 14-step native
result in a private CPython 3.12 package environment. This was an export check,
not another processing run. The public application remains the experimental
`0.3.0b3` release built from
[`7598ae5`](https://github.com/ben-shin/spinflow/tree/7598ae53267d743fbd46ba708ed85f5b3478cbeb),
with native component SHA-256
`811fcb73e59727b13096c453a6a8f57c3e08e52957f9c0425f04363024f97d34`. The
private test package candidate is not the public release; no release assets
were changed.

For each format, all 8,388,608 float32 values matched the independent reference
bit for bit, including sign. Every non-date byte also matched. Six four-byte
Pipe date words and the 26-byte UCSF date field matched actual writer-default
predictions that were durably recorded before writing. Each format's 6,144
scalar axis coordinates (2,048 + 4,096) were within the existing `2e-5 ppm`
gate, for 12,288 coordinates across both exports. All five fixed ROI bounds
were covered by the matching samples and axes; no new ROI peak or volume
assessment was performed. The full native record and provenance were retained.
The existing reported export losses were axis annotations, metadata and
provenance.

The export pair used a native input with SHA-256
`1f7d4006354e57a5193f5d7899777e5163dc156b1468a841f7f837af7b9428ba`. This
result does not establish original absolute-ppm truth: references were
inherited. The retained 493-byte original script performs conversion only; the
original author's full spectral-processing script is unavailable. No end-user
Sparky or NMRDraw reopening was performed. Prior
Open/Cancel/normal-close checks had no spectrum loaded, so data reading and
physical Ubuntu use remain unverified. S19 remains FAIL on six of eighteen
targets, and all support profiles remain experimental. No phase, peak,
volume, human or v1 acceptance is claimed.
