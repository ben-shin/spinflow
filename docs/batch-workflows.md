# Process several spectra with one recipe

This guide covers the development version of SpinFlow. Start in a Linux shell with that version installed. Check that `spinflow batch-plan --help` lists a manifest argument. You can process spectra without labels, measure already processed spectra, or apply the same processing and measurements to each input.

## Start with two files

Put two of your native spectra at `inputs/run-01.nmrh5` and `inputs/run-02.nmrh5`. From the same directory, run `mkdir results` to create a fresh output directory. Save this as `series.json` next to `inputs/`. The example makes native outputs with no processing and no assignments, so you can check the input and output bindings first.

```json
{
  "schema_version": "1",
  "kind": "batch_manifest",
  "backend": "numpy",
  "threads": 1,
  "memory_bytes": 268435456,
  "operations": [],
  "items": [
    {"item_id": "run-01", "input": "inputs/run-01.nmrh5", "output": "results/01.nmrh5"},
    {"item_id": "run-02", "input": "inputs/run-02.nmrh5", "output": "results/02.nmrh5"}
  ]
}
```

Paths are relative to the manifest. Each item needs a unique ID and a new native output path. Files with the same basename, identical bytes or even the same input path can represent distinct items. List each entry explicitly. To process without measuring labels, keep the template field absent.

To reuse a saved SpinFlow schema-1 recipe, copy its `operations` array into the manifest before running it. Keep the operation order and axis IDs, and set `backend`, `threads` and `memory_bytes` to the settings you intend. The batch supplies each input and output path. Inspect the example below with your actual files and chosen operations.

```bash
spinflow inspect inputs/run-01.nmrh5 --json
spinflow batch-plan series.json
spinflow batch-run series.json
```

Read the plan before issuing the last command. Check every item's source identity, axes, component layout, effective recipe, memory estimate and `refusals`. The runner processes items serially. Its memory budget applies to reader and processing checks. Assignment analysis still needs a materialized parent dataset and bounded regions. The budget is neither a process RSS ceiling nor a guarantee of out-of-core 3D/4D execution.

## Measure the same label in several spectra

Use a processed frequency-domain 1D or 2D reference that you have inspected. Run `spinflow gui`, open the reference and switch to **Batch**. In **Optional labels and fixed regions**, use **New** to start a template from the current spectrum. Select the analysis component and choose **Label**. Give each label a stable assignment ID, positive or negative sign, cutoff, units, search bounds and separate fixed integration bounds for every axis. Choose **Save** and save a new template such as `labels.json`. The template records the actual reference axes, digest, complex-axis order and quadrature orientation.

For analysis only, start a **new** manifest with fresh outputs, keep `"operations": []`, add `"assignment_template": "labels.json"` at the top level and give every item an `axis_map`. For example, `"axis_map": {"f2": "a0"}` maps reference axis ID `f2` to processed target axis ID `a0` in a 1D item. Substitute your actual IDs, and map both axes in 2D. To process first, supply the reviewed operation list and map to its **output** axes. Each spectrum's native output keeps the components produced by that recipe. Selecting `analysis_channel` (default 0) chooses one component for measurement without discarding the others.

Use points only on identical mapped grids. Hz requires the same observation and reference frame. Ppm requires calibrated axes in both spectra. SpinFlow checks mapped axis semantics and quadrature order and orientation. It searches each specified box for a unique signed local extremum above the cutoff and integrates its fixed box with baseline set to none. Use non-overlapping integration boxes. The bounds stay fixed across spectra, with no alignment or recentering. Height and volume are relative signal measurements.

In the GUI, **Files…** adds spectra and **Folder…** adds an acquisition folder. Choose a fresh output directory. Select **Measure these labels for each input**, open or save the template, then use **Map axes…** for each file. Choose **No processing · copy input** for analysis alone. To reuse processing from the current workbench, apply it in **Processing** first, then select **Use applied processing**. Verify the saved manifest's operations and resource settings when reopening it. Choose **Save manifest…**, **Plan**, then **Apply to files**. **Stop after file** lets the current item finish. **Export CSV…** copies the resulting table.

## Read the results and resume

The run summary names the current journal, JSON result and CSV beside the manifest. Follow the returned paths, including any numbered generation suffix. Preserve the journals, result generations, commit markers and native outputs together. The journal identifies item outcomes and output hashes. For processing-only runs the assignment CSV has a header and no measurement rows.

With a template, use `item_id` and `assignment_id` to track a label across files. Read `status` before using `height`, `volume` and `volume_unit`.

| Status | Interpretation |
| --- | --- |
| `measured` | One signed candidate matched. Inspect the coordinates, fixed bounds, component and warnings. |
| `not_found` | The search found no qualifying candidate. A blank measurement is missing, not zero. |
| `ambiguous` | Multiple candidates or a truncated search. No strongest-peak substitution. |
| `failed` | Read the structured error for an item or label, for example incompatible coordinates or overlapping integration regions. |
| `not_run` | The item was left unprocessed after a stop or prior item failure. Inspect the stop reason. |

By default, an item failure stops subsequent work. To attempt later items, run `spinflow batch-run series.json --continue-on-error` as your initial command. For a stopped run, use `spinflow batch-run series.json --resume` with the same saved manifest and original inputs. Resume checks source, recipe, template, receipts and output identity before skipping completed items. A changed or missing completed output requires investigation. For a different recipe or corrected manifest, choose fresh output paths and a new manifest name.

## Import a supported NMRPipe command file

SpinFlow reads command text as data. For example, save this one-line pipeline as `window.pipe` for a complex time-domain input with the full recorded direct-axis grid. The broadening value is an example parameter.

```text
nmrPipe -in input.fid | nmrPipe -fn EM -lb 1.0 -out output.ft
```

```bash
spinflow import-pipe-script window.pipe --input inputs/run-01.nmrh5 --output results/window-check.nmrh5 --backend numpy --threads 1 --save-recipe window.json
spinflow plan window.json
```

This compiles a new single-file recipe and displays the literal bindings, effective paths and mapped operations. The two commands leave spectrum output creation to a subsequent run. For a batch, replace the manifest's `operations` field with `"pipe_script": "window.pipe"` and plan again with fresh output paths. Each item binds the script to its own input and output.

The importer admits guarded forms of EM, SP, explicit-size ZF and zero-order/first-order phase parameters both set to zero on supported input profiles. Imported FT, TP, nonzero PS and `-di` are refused. It does not execute shell code, expand variables or run vendor converters. A successful import validates that mapping for the supplied header. It does not establish full NMRPipe compatibility.
