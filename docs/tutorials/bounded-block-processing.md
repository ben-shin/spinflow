# Process a native file with bounded working memory

The source API and CLI provide a separate file-to-file mode for native schema-1 HDF5 files with one through four physical axes. It keeps the complete transformed axis and every quadrature component in each FFT slab, and tiles the other axes. The existing `Dataset`, `read_dataset` and `run_operations` APIs keep their eager behavior.

This feature uses the existing core dependencies and bundled native FFT. It needs no GUI or external NMR processing application. Availability in a source checkout does not add it to the immutable v0.2.0 release assets.

## Check the input and prepare a recipe

Start with an existing native `.nmrh5`, `.h5` or `.hdf5` file. Inspect its header without loading its sample cube:

```python
from nmrnext.io import describe_dataset

header = describe_dataset("input.nmrh5", format="native")
print(header.shape, header.dtype)
print([(axis.id, axis.size, axis.domain) for axis in header.axes])
```

Save a normal JSON recipe as `recipe.json`. This example expects a time-domain axis whose actual ID is `direct`; replace that ID with the one shown by your header. Paths are relative to the recipe file.

```json
{
  "schema_version": "1",
  "input": "input.nmrh5",
  "output": "processed.nmrh5",
  "input_format": "native",
  "output_format": "native",
  "backend": "native",
  "threads": 1,
  "operations": [
    {"op": "window", "axis": "direct", "lb_hz": 2},
    {"op": "fft", "axis": "direct"}
  ]
}
```

Use your established processing operations, references and phase values. Bounded execution does not estimate phases, correct acquisition encodings or choose a scientific recipe.

## Plan and run from the CLI

```console
spinflow plan recipe.json --block-memory 64MiB
spinflow run recipe.json --block-memory 64MiB
```

The accepted units are integer bytes, `B`, `KiB`, `MiB` and `GiB`; `64MiB` means 67,108,864 bytes. `64MB` is not accepted. The explicit block option overrides the recipe's `memory_bytes` field. It cannot be combined with `--memory`. Omitting `--block-memory` selects the existing eager CLI path.

Planning reads headers and storage declarations, validates the whole recipe, and reports minimum full-axis memory, selected working bytes, block geometry and required temporary disk space. It does not validate sample values. Execution scans every input sample for finiteness before processing, checks each operation's results and records full-file and canonical numerical hashes. It publishes the closed result atomically; an existing output is refused by the CLI.

## Use the Python file-to-file API

The public signatures are:

```python
plan_file_operations(input_path, operations, *, backend="native", threads=1,
                     memory_bytes=256*1024**2, input_format=None,
                     output_format="native", output_path=None)

process_to_file(input_path, output_path, operations, *, backend="native", threads=1,
                memory_bytes=256*1024**2, input_format=None,
                output_format="native", overwrite=False)
```

For the same saved recipe:

```python
import json
from pathlib import Path
from nmrnext.blocks import plan_file_operations, process_to_file
from nmrnext.io import describe_dataset

recipe_path = Path("recipe.json").resolve()
recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
source = recipe_path.parent / recipe["input"]
target = recipe_path.parent / recipe["output"]
options = dict(memory_bytes=64*1024**2, input_format="native", output_format="native")

plan = plan_file_operations(source, recipe["operations"], output_path=target, **options)
print(plan["minimum_working_bytes"], plan["estimated_working_bytes"])
report = process_to_file(source, target, recipe["operations"], **options)

result_header = describe_dataset(report["result"]["path"], format="native")
print(result_header.shape, report["result"]["dataset_digest"])
print(report["elapsed_seconds"], report["elapsed_scope"], report["cleanup_warnings"])
```

Run either the CLI or Python processing example for a given output path. The returned object is a JSON-compatible report; it does not expose a lazy `Dataset.data`. `describe_dataset` reads the result header without loading the full result. Calling `read_dataset` still loads an eager immutable dataset and requires its ordinary memory budget.

## Supported profile and refusals

| Item | Bounded mode |
| --- | --- |
| Input/output | Native schema-1 HDF5, float32/float64, 1–4 physical axes. Input storage must be uncompressed and unfiltered, with bounded chunks or contiguous storage. |
| Operations | Existing window, zero fill, FFT/inverse FFT, phase, reference, crop, reverse, transpose and explicit version-1 discard-imaginary. |
| Backend | Native, one thread. Full component order, dtype, axes, metadata and operation provenance are retained. |
| Outside this profile | Raw vendor 3D/4D import, compressed/filtered HDF5, digital-filter correction, NUS/global analysis, automatic phase, high-dimensional NMRPipe/UCSF export and GUI lazy viewing. |

The working budget covers declared slab buffers, complex/native workspace, vectors, finite checks, hash buffers, metadata and explicit HDF5 cache reserves. It is not a hard process-RSS cap. If one complete active-axis line or an input chunk cannot fit, planning refuses it with required/available byte details. It does not raise the cap or fall back to loading the cube.

Allow space on the output filesystem for the reported current-plus-next temporary stores in addition to the input. Failures preserve the input and existing output, clean only owned temporary files, and report any cleanup warnings. Input/output aliases are refused even with Python `overwrite=True`.

The [protocol](../block-processing-protocol.md) defines validation, memory accounting and measurement scopes. Native-file support does not establish a general raw-vendor 3D/4D conversion workflow or biological interpretation.
