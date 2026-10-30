# Process, analyze, and reopen a session

This walkthrough is for the **0.3.0b1 experimental beta** on the targeted Ubuntu 24.04 desktop with the optional GUI installed. It describes a beta workflow, not a stable 1.0 claim or a feature of the previous v0.2.0 prerelease. Confirm `spinflow doctor` reports the native backend available and use a [declared acquisition profile](../formats.md). The [support registry](../../compatibility/support.json) gives profile status; keep your original acquisition and its checksum.

## Apply the complete recipe

1. Put a schema-1 `workflow.json` and its referenced input in the same relative layout. For the previously studied public Varian protein case, [the 14-step example](../../examples/laboratory-workflow.json) expects a controlled `agilent_2d` acquisition directory beside a copy of that recipe. Use the [case protocol](../laboratory-workflow-protocol.md) for the hash-pinned files and fixed reference values; it is a known case, not an independent holdout. For your own data, check every format restriction and reference before processing.
2. Start `spinflow gui`, choose **Load ordered recipe…**, and select the JSON recipe. Inspect **Ordered processing steps** and the preflight. Double-click a step to edit its fields; transpose order accepts axis IDs such as `a0, a1`. **Edit complete operation JSON** exposes parameters the quick controls do not show. An edit changes the *pending* recipe; the applied result remains intact until **Process** succeeds. **Preview** is likewise not an applied result.
3. Choose **Process**, then show **Current** and inspect both axes, quadrature/channel labels, phase, signs and the processing history. The fixed Varian example uses explicit manual phase and `discard_imaginary`; its real output is not eligible for automatic phase fitting. On an eligible complex 1D or full-hypercomplex 2D result, **Apply automatic phase (1D / 2D)…** can add explicit editable phase steps. Inspect its diagnostics and spectrum; if the fit is unsuitable, use a reviewed manual correction. A later **Process** replays the applied phase steps unless you deliberately remove them. Save only after the desired result is applied.

## Analyze a bounded part of the applied result

For a large processed 1D/2D spectrum, whole-spectrum analysis still refuses more than **2,000,000 points per component**. In **Current** view, zoom to the region of interest and choose **Use visible plot area as region**. That control is disabled for Original and Preview views so it cannot interpret an unrelated viewport as the applied spectrum. Alternatively, enter inclusive sample-center bounds for *every* axis in points, Hz, or calibrated ppm and choose **Select entered analysis region**. Ppm requires a known reference. Keep the selected rectangle within 2,000,000 points per component, with at least two sample centers on each axis.

Check **Analyze selected region of applied spectrum**, then choose **Pick peaks** or **Integrate entered region**. Peak sign can be positive, negative, or both; peak picking reports signed local extrema, not assignments. Review the noise threshold, baseline, integration bounds, flags, and any overlap warning. Integrals are signed relative intensity·Hz in 1D or intensity·Hz² in 2D, **not concentrations**. The region report uses local points for its bounded analysis and also records `parent_points`/`parent_coordinates` for peaks or `parent_point_bounds`/`parent_coordinate_bounds` for integrals. Confirm those parent values against the full applied spectrum; changing the source or processing invalidates the old digest link. The [analysis-region guide](../analysis-regions.md) explains masks and their limits.

## Save, close, reopen, and replay in the CLI

Choose **Save current result…** for a native numeric file if you want a direct GUI/CLI comparison. Choose **Save applied session…** and name a **new** folder. The saved folder contains `source.nmrh5`, `workflow.json`, and `session.json`; it retains the applied recipe, backend/budget, selected region and supported reports, while pending edits and previews are excluded. Close the workbench, restart it, choose **Open saved session…**, and select the folder. Verify that Current, ordered steps, axes, region and analysis reports return. Failed or cancelled open must leave the prior good view intact.

`session.json` is published last as a commit marker; the three files are published sequentially, so the whole directory is not one atomic snapshot. The source/workflow hashes and report checksum detect accidental changes. They are **not authentication signatures** and do not independently verify scientific conclusions. Keep the original acquisition and session folder as evidence.

For an independent CLI replay, copy the saved folder to a **new** folder and run the saved recipe there. Its paths are relative to that folder, and `run` refuses an existing output:

```sh
cp -a /path/to/session /path/to/session-cli
spinflow plan /path/to/session-cli/workflow.json
spinflow run /path/to/session-cli/workflow.json
spinflow compare /path/to/gui-result.nmrh5 /path/to/session-cli/result.nmrh5 --rtol 0 --atol 0 --json
spinflow inspect /path/to/session-cli/result.nmrh5 --json
```

Replace the example paths with your new session copy and optional GUI native export. `compare` checks numeric arrays and axes under the chosen tolerances; inspect both files' processing history and export loss reports separately. For processed real NMRPipe/UCSF output, review every `convert --dry-run` loss and acknowledge only the losses you accept. Reopen the exported file in the actual intended external application and check its coordinates there. A successful CLI replay alone is not independent scientific validation.

## Independent researcher acceptance record

This is a blank record for a **future human session**. Automated Qt or virtual-desktop tests do not fill it in. Freeze expected results before seeing the candidate output, then retain the filled record and input hashes.

| Field to record | Researcher entry |
| --- | --- |
| Researcher, date, independent acquisition ID, raw file hashes, permission to use | |
| Instrument/sequence, declared import profile, sampling/quadrature mode, reference anchors | |
| Predeclared axis/ppm, sign, weak/strong and crowded-region checks; quantitative tolerance and unsuitable-fit/failure cases | |
| Candidate wheel hash, Python/native versions, OS/desktop, CPU, memory budget, observed elapsed/peak memory and free temporary disk | |
| Recipe and session paths/hashes; pending versus applied edits, phase decision, chosen region bounds and parent-coordinate checks | |
| Peak/integral signs, units, baselines, flags, expected versus observed values and deviations | |
| Export profile, acknowledged losses, actual independent application/version, reopened coordinates and any rejected file | |
| Failure/cancellation behavior, data preservation, reproducible problems, researcher verdict (pass/fail/unverified) | |

Independent researcher acceptance is currently **UNVERIFIED**. A selected archived NMRDraw/Sparky reopening and automated desktop replay do not replace this record for newly chosen acquisitions.
