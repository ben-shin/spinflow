# Explicit analysis regions

Optional masks let you choose phase anchors, noise-reference samples, and baseline-reference samples. Create a UTF-8 JSON file containing a union of inclusive, zero-based point-index boxes. Each box must include every current dataset axis ID. For example, for a 2D dataset with axes `c` and `h`:

```json
{"dataset_digest": "COPY_FROM_REPORT", "regions": [{"c": [10, 30], "h": [40, 70]}, {"c": [50, 65], "h": [80, 100]}]}
```

Use `spinflow inspect INPUT --json` to check the axis IDs and sizes. Obtain `dataset_digest` from an analysis report for the exact current dataset, for example `spinflow peaks INPUT --output initial-peaks.json`, and copy it into the mask. Bounds are ordered integers and may include both endpoints. A changed dataset fails the digest check: revise the regions and digest after processing, cropping or axis ordering changes. Resolved nondefault quadrature orientation is also part of the digest; regenerate masks made for an earlier reflected dataset if the digest changes. The GUI has optional file selectors for the same JSON. Clearing a path disables that selection.

Analysis is limited to **2,000,000 points per component**. Crop a larger spectrum before obtaining its digest or constructing masks. For the public Agilent example, the following Linux commands fetch the checksum-pinned archive, crop its stored processed spectrum from 2048 × 4096 to 1024 × 1536 points, and run a noise-mask analysis. Run them from the project root with the native backend installed. They use new output paths and refuse to overwrite existing output; choose fresh paths before repeating. The source remains untouched.

```sh
.venv/bin/python tests/fixtures/fetch_varian_public.py --root .artifacts/coverage-formats
mkdir -p .artifacts/coverage-integration
.venv/bin/python -m nmrnext run examples/hard-mask-crop.json
.venv/bin/python -m nmrnext peaks .artifacts/coverage-integration/hard-mask-crop.nmrh5 --output .artifacts/coverage-integration/hard-mask-initial.json --threshold 30000 --memory 512MiB
.venv/bin/python - <<'PY'
import json
from pathlib import Path
folder = Path('.artifacts/coverage-integration')
report = json.loads((folder / 'hard-mask-initial.json').read_text())
mask = {'dataset_digest': report['dataset_digest'], 'regions': [
    {'a0': [0, 255], 'a1': [1264, 1535]},
    {'a0': [768, 1023], 'a1': [1264, 1535]}]}
with (folder / 'hard-mask.json').open('x', encoding='utf-8') as stream:
    json.dump(mask, stream)
PY
.venv/bin/python -m nmrnext peaks .artifacts/coverage-integration/hard-mask-crop.nmrh5 --output .artifacts/coverage-integration/hard-mask-peaks.json --noise-mask .artifacts/coverage-integration/hard-mask.json --snr 6 --sign positive --baseline none --min-separation 4 --memory 512MiB
```

The crop uses half-open source-point intervals; mask boxes use inclusive indices in the cropped grid. These sample regions are a reproducible example, not a guarantee that reference samples are signal-free. Inspect the full report and the data before drawing conclusions.

* `autophase INPUT OUTPUT --report REPORT --phase-mask REGIONS.json` restricts candidate phase fitting to the selected regions. The complete fit window must be inside the mask; a result does not certify phase outside it. The estimator still requires identifiable positive lines and appropriate quadrature channels.
* `peaks INPUT --output REPORT --noise-mask REGIONS.json` uses the selected samples for the noise estimate, independently of the detection area. Choose noise-only samples; the program cannot establish that they contain no signal. Thresholds and peak reports still require inspection.
* `integrate INPUT REGIONS.json --output REPORT --baseline reference_linear --baseline-mask MASK.json` fits an affine baseline to the selected off-region samples. The mask must not touch integration sample coverage and must provide enough independent points. `peaks --integrate` also accepts `--integral-baseline reference_linear --baseline-mask MASK.json`. These samples must be assessed as signal-free; the fit does not deconvolve overlapping peaks or determine concentration.

CLI and GUI reports retain the validated selection document and its role, alongside mask digests and diagnostics. The digest identifies the selected grid points, not the scientific suitability of the selection. Defaults remain unchanged when masks are omitted.
