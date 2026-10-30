"""Headless CLI. Structured results on stdout, structured failures on stderr."""
import argparse
import csv
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time

import numpy as np

from . import __version__
from .demo import generate_demo
from .errors import NMRNextError
from .model import finite, positive_int
from .operations import run_operations, validate_backend
from .planning import plan_operations


def _memory(text):
    if text is None:
        return None
    if isinstance(text,int) and not isinstance(text,bool):
        return positive_int(text,"memory")
    match=re.fullmatch(r"([0-9]+)(B|KiB|MiB|GiB)?",str(text))
    if not match or int(match[1]) == 0:
        raise NMRNextError("INVALID_MEMORY","Memory must be positive bytes or an integer followed by KiB, MiB, or GiB")
    return int(match[1])*{"B":1,"KiB":1024,"MiB":1024**2,"GiB":1024**3,None:1}[match[2]]


def _json_file(path, *, analysis=False):
    limit = 8*1024*1024 if analysis else 1024*1024
    invalid = "INVALID_ANALYSIS_REPORT" if analysis else "INVALID_RECIPE"
    if path.suffix.lower() not in (".json",):
        code = "UNSUPPORTED_ANALYSIS_FORMAT" if analysis else "UNSUPPORTED_RECIPE_FORMAT"
        raise NMRNextError(code,"Input must be JSON; YAML/script execution is not supported")
    try:
        with path.open("rb") as stream:
            payload = stream.read(limit+1)
        if len(payload) > limit:
            if analysis:
                raise NMRNextError("ANALYSIS_TOO_LARGE", "Analysis JSON exceeds 8 MiB")
            raise NMRNextError("RECIPE_TOO_LARGE","Recipe exceeds 1 MiB")
        def pairs(items):
            result={}
            for key,value in items:
                if key in result:
                    raise NMRNextError(invalid,f"Duplicate JSON key {key!r}")
                result[key]=value
            return result
        def finite_float(value):
            parsed = float(value)
            if not math.isfinite(parsed):
                raise NMRNextError(invalid, f"Nonfinite JSON number {value}")
            return parsed
        return json.loads(payload.decode("utf-8"),object_pairs_hook=pairs,
                          parse_float=finite_float,
                          parse_constant=lambda value: (_ for _ in ()).throw(NMRNextError(invalid,f"Nonfinite JSON number {value}")))
    except json.JSONDecodeError as exc:
        raise NMRNextError(invalid,f"JSON error at line {exc.lineno}: {exc.msg}") from exc


def _load_recipe(args):
    override_memory=_memory(args.memory if args.memory is not None else getattr(args, 'block_memory', None))
    path=Path(args.recipe).resolve()
    recipe=_json_file(path)
    if not isinstance(recipe,dict) or recipe.get("schema_version") != "1":
        raise NMRNextError("UNSUPPORTED_RECIPE_SCHEMA","Expected recipe schema_version '1'")
    allowed={"schema_version","input","output","operations","backend","threads","memory_bytes","allow_losses","input_format","output_format"}
    if set(recipe)-allowed:
        raise NMRNextError("UNKNOWN_RECIPE_FIELD",f"Unknown recipe fields: {sorted(set(recipe)-allowed)}")
    for field in ("input","output"):
        if not isinstance(recipe.get(field),str) or not recipe[field]:
            raise NMRNextError("INVALID_RECIPE",f"Recipe {field} must be a nonempty path")
    if not isinstance(recipe.get("operations"),list):
        raise NMRNextError("INVALID_RECIPE","Recipe operations must be a list")
    if not isinstance(recipe.get("allow_losses",[]),list) or not all(isinstance(v,str) for v in recipe.get("allow_losses",[])):
        raise NMRNextError("INVALID_RECIPE","allow_losses must be a list of specific loss codes")
    source=(path.parent/recipe["input"]).resolve()
    target=(path.parent/recipe["output"]).resolve()
    if source == target:
        raise NMRNextError("INPUT_OVERWRITE","Recipe input and output must be different files")
    memory=override_memory or _memory(recipe.get("memory_bytes",256*1024**2))
    backend=args.backend or recipe.get("backend","native")
    threads=args.threads if args.threads is not None else recipe.get("threads",1)
    return recipe,source,target,memory,backend,threads


def _format(path,explicit=None):
    if explicit:
        return explicit
    suffix=Path(path).suffix.lower()
    formats={".h5":"native",".hdf5":"native",".nmrh5":"native",".ft1":"nmrpipe",".ft2":"nmrpipe",".ucsf":"ucsf"}
    if suffix not in formats:
        raise NMRNextError("UNKNOWN_FORMAT","Specify --format for this output suffix")
    return formats[suffix]


def _doctor():
    deps={}
    for name in ("numpy","h5py","pybind11","scipy","nmrglue"):
        try: deps[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: deps[name]=None
    backends={}
    for name in ("numpy","native"):
        try: backends[name]={"available":True,**validate_backend(name,1)}
        except NMRNextError as exc: backends[name]={"available":False,"reason":exc.to_dict()}
    return {"program":"nmrnext","version":__version__,"status":"experimental",
            "python":{"executable":sys.executable,"version":sys.version},"dependencies":deps,"backends":backends,
            "legacy_reference":"NMRPipe not invoked or validated by doctor","offline_runtime":True}


def _compare(args):
    from .io import read_dataset
    budget=_memory(args.memory) or 256*1024**2
    rtol=finite(args.rtol,"rtol");atol=finite(args.atol,"atol")
    if rtol<0 or atol<0:
        raise NMRNextError("INVALID_TOLERANCE","Comparison tolerances must be nonnegative")
    candidate=read_dataset(args.candidate,max_bytes=budget//8)
    reference=read_dataset(args.reference,max_bytes=budget//8)
    mismatches=[]
    if candidate.complex_axes != reference.complex_axes:
        mismatches.append("quadrature axis IDs or order differ")
    if candidate.data.shape != reference.data.shape:
        mismatches.append("array shapes differ")
    if len(candidate.axes) != len(reference.axes):
        mismatches.append("physical dimensionality differs")
    for i,(a,b) in enumerate(zip(candidate.axes,reference.axes)):
        for field in ("id","role","domain","unit","nucleus","size"):
            if getattr(a,field) != getattr(b,field):
                mismatches.append(f"axis {i}: {field} differs")
        for field in ("start","step","observation_mhz","reference_ppm","reference_hz"):
            av,bv=getattr(a,field),getattr(b,field)
            if av is None or bv is None:
                equal=av == bv
            else:
                equal=math.isclose(av,bv,rel_tol=1e-7,abs_tol=1e-7)
            if not equal: mismatches.append(f"axis {i}: {field} differs")
    metrics=None
    if candidate.data.shape == reference.data.shape:
        left=candidate.data.astype(np.float64); right=reference.data.astype(np.float64)
        scale=max(float(np.max(np.abs(right))),float(np.max(np.abs(left))),1e-30)
        delta=(left/scale-right/scale)
        norm=float(np.linalg.norm(right/scale))
        denominator=max(norm,1e-30/scale)
        relative_numerator=float(np.linalg.norm(delta))
        rel=relative_numerator/denominator if denominator else None
        maximum=float(np.max(np.abs(delta)))*scale
        if rel is not None and not math.isfinite(rel): rel=None
        if not math.isfinite(maximum): maximum=None
        metrics={"relative_l2":rel,"max_absolute_error":maximum,"rtol":rtol,"atol":atol,
                 "reference_scale_floor":1e-30}
    passed=not mismatches and metrics is not None and metrics["relative_l2"] is not None and metrics["max_absolute_error"] is not None and metrics["relative_l2"]<=rtol and metrics["max_absolute_error"]<=atol
    return {"match":passed,"coordinate_mismatches":mismatches,"metrics":metrics,"alignment":"none","rescaling":"none"},0 if passed else 1


def _json_bytes(value):
    return (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _publish_staged_file(temporary, path):
    try:
        if os.name == "nt":
            os.rename(temporary, path)
        else:
            os.link(temporary, path)
    except FileExistsError as exc:
        raise NMRNextError("OUTPUT_EXISTS", f"Output already exists: {path}") from exc


def _save_new_files(outputs):
    """Publish several new files, removing only files this call actually created.

    Each payload is prepared in a private directory on the destination filesystem.
    A failed later publication leaves existing or concurrently replaced paths alone.
    """
    paths = [Path(path) for path, _ in outputs]
    if len({path.resolve() for path in paths}) != len(paths):
        raise NMRNextError("INVALID_OUTPUT", "Output paths must be distinct")
    for path in paths:
        if path.exists() or path.is_symlink():
            raise NMRNextError("OUTPUT_EXISTS", f"Output already exists: {path}")
    staged, published, cleanup_warnings = [], [], []
    complete = False
    try:
        for path, writer in outputs:
            path = Path(path)
            directory = Path(tempfile.mkdtemp(prefix=".spinflow-stage-", dir=path.parent))
            temporary = directory / "payload"
            staged.append((temporary, path))
            writer(temporary)
        for temporary, path in staged:
            info = temporary.stat(follow_symlinks=False)
            identity = (info.st_dev, info.st_ino)
            _publish_staged_file(temporary, path)
            published.append((path, identity))
        complete = True
    finally:
        if not complete:
            for path, identity in reversed(published):
                try:
                    info = path.stat(follow_symlinks=False)
                    if (info.st_dev, info.st_ino) == identity:
                        path.unlink()
                except OSError:
                    # Keep the publication/writer failure as the primary error.
                    pass
        for temporary, path in staged:
            try:
                temporary.unlink(missing_ok=True)
                temporary.parent.rmdir()
            except OSError:
                if complete:
                    cleanup_warnings.append({"code": "TEMPORARY_CLEANUP_FAILED",
                                             "output": str(path),
                                             "message": "Private staging cleanup failed after publication"})
                # Before publication, preserve the original writer/link error.
    return cleanup_warnings


def _write_bytes(payload):
    return lambda path: path.write_bytes(payload)


def _report_paths(output, csv_path=None):
    if Path(output).suffix.lower() != ".json":
        raise NMRNextError("REPORT_FORMAT", "Analysis report output must end in .json")
    if csv_path is not None and Path(csv_path).suffix.lower() != ".csv":
        raise NMRNextError("REPORT_FORMAT", "CSV table output must end in .csv")


def _analysis_csv(report):
    """Flat, signed peak/region table with explicit axis units and flags."""
    if "peaks" in report and "integrals" in report:
        peaks, integrals = report["peaks"], report["integrals"]
    elif report.get("kind") == "peak_picking":
        peaks, integrals = report, None
    elif report.get("kind") == "integration":
        peaks, integrals = None, report
    else:
        raise NMRNextError("CSV_UNSUPPORTED", "CSV output requires a peak or integration report")
    axes = (peaks or integrals)["axes"]
    ids = [axis["id"] for axis in axes]
    fields = ["peak_id", "region_id", "sign", "intensity", "height_above_baseline",
              "integral", "integral_unit", "flags", "overlaps", "noise_sigma", "threshold"]
    for identity in ids:
        fields += [f"{identity}_point", f"{identity}_Hz", f"{identity}_ppm",
                   f"{identity}_start_point", f"{identity}_stop_point",
                   f"{identity}_start_Hz", f"{identity}_stop_Hz",
                   f"{identity}_start_ppm", f"{identity}_stop_ppm"]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fields, lineterminator="\n")
    writer.writeheader()
    peak_rows = peaks["peaks"] if peaks else []
    region_rows = integrals["regions"] if integrals else []
    for index in range(max(len(peak_rows), len(region_rows))):
        row = {}
        if index < len(peak_rows):
            peak = peak_rows[index]
            row.update(peak_id=peak["id"], sign=peak["sign"], intensity=peak["intensity"],
                       height_above_baseline=peak["height_above_baseline"],
                       flags=";".join(peak["flags"]), noise_sigma=peaks["noise"]["sigma"],
                       threshold=peaks["threshold"])
            for identity in ids:
                row[f"{identity}_point"] = peak["points"][identity]
                for unit, value in peak["coordinates"][identity].items():
                    row[f"{identity}_{unit}"] = value
        if index < len(region_rows):
            region = region_rows[index]
            row.update(region_id=region["id"], integral=region["integral"],
                       integral_unit=region["integral_unit"], overlaps=";".join(region["overlaps"]))
            row["flags"] = ";".join(dict.fromkeys([*(row.get("flags", "").split(";") if row.get("flags") else []),
                                                      *region["flags"]]))
            for identity in ids:
                low, high = region["point_bounds"][identity]
                row[f"{identity}_start_point"], row[f"{identity}_stop_point"] = low, high
                for unit, bounds in region["coordinate_bounds"][identity].items():
                    row[f"{identity}_start_{unit}"], row[f"{identity}_stop_{unit}"] = bounds
        writer.writerow(row)
    return stream.getvalue().encode("utf-8")


def _analysis_input(path, memory):
    from .io import read_dataset
    return read_dataset(path, max_bytes=(_memory(memory) or 256 * 1024**2) // 8)


def _point_mask_selection(dataset, path):
    """Build a union of inclusive point-index boxes from an explicit JSON file."""
    if path is None:
        return None, None
    import numpy as np
    document = _json_file(Path(path), analysis=True)
    if not isinstance(document, dict) or set(document) != {"dataset_digest", "regions"}:
        raise NMRNextError("INVALID_MASK", "Mask JSON must contain dataset_digest and a regions list")
    shape = tuple(axis.size for axis in dataset.axes)
    if len(shape) not in (1, 2):
        raise NMRNextError("INVALID_MASK", "Mask requires a supported 1D or 2D analysis dataset")
    if np.prod(shape) > 2_000_000:
        raise NMRNextError("ANALYSIS_SIZE_LIMIT", "Analysis is limited to 2,000,000 points per component; crop the spectrum first")
    from .analysis import dataset_digest
    if document["dataset_digest"] != dataset_digest(dataset):
        raise NMRNextError("STALE_ANALYSIS", "Mask dataset digest must match the current spectrum")
    regions = document["regions"]
    if not isinstance(regions, list) or not 1 <= len(regions) <= 1000:
        raise NMRNextError("INVALID_MASK", "Mask requires 1 to 1000 point-index regions")
    mask = np.zeros(shape, dtype=bool)
    ids = [axis.id for axis in dataset.axes]
    for region in regions:
        if not isinstance(region, dict) or set(region) != set(ids):
            raise NMRNextError("INVALID_MASK", "Each mask region must bound every axis ID")
        slices = []
        for identity, size in zip(ids, shape):
            bounds = region[identity]
            if (not isinstance(bounds, list) or len(bounds) != 2 or
                    any(isinstance(v, bool) or not isinstance(v, int) for v in bounds) or
                    not 0 <= bounds[0] <= bounds[1] < size):
                raise NMRNextError("INVALID_MASK", "Mask bounds must be ordered inclusive point indices within each axis")
            slices.append(slice(bounds[0], bounds[1] + 1))
        mask[tuple(slices)] = True
    return mask, document


def _point_mask(dataset, path):
    return _point_mask_selection(dataset, path)[0]


def _attach_mask_selection(result, role, selection):
    if selection is not None:
        report = result[1] if isinstance(result, tuple) else result
        report.setdefault("mask_selections", {})[role] = selection
    return result


def _analysis_report(path):
    report = _json_file(Path(path), analysis=True)
    if not isinstance(report, dict):
        raise NMRNextError("INVALID_ANALYSIS_REPORT", "Analysis JSON must contain a report object")
    reports = [report] if "dataset_digest" in report else list(report.values())
    if not reports or not all(isinstance(item, dict) and item.get("schema_version") == "1" and
                              isinstance(item.get("dataset_digest"), str) for item in reports):
        raise NMRNextError("INVALID_ANALYSIS_REPORT", "Analysis reports require schema version 1 and a dataset digest")
    return report


def _analysis_dispatch(args):
    from .analysis import (autophase, dataset_digest, integrate_regions, pick_peaks,
                           propose_regions)
    dataset = _analysis_input(args.input, args.memory)
    if args.command == "autophase":
        from .io import write_dataset
        _report_paths(args.report)
        pivot = args.pivot
        if args.axis_pivot:
            pivot = {}
            for item in args.axis_pivot:
                try:
                    axis_id, point = item.rsplit('=', 1)
                    value = float(point)
                except ValueError:
                    raise NMRNextError('INVALID_PARAMETER', '--axis-pivot requires AXIS=POINT') from None
                if not axis_id or axis_id in pivot:
                    raise NMRNextError('INVALID_PARAMETER', '--axis-pivot requires distinct axis IDs')
                pivot[axis_id] = value
            if len(dataset.axes) != 2:
                raise NMRNextError('INVALID_PARAMETER', '--axis-pivot is for 2D; use --pivot for 1D')
        phase_mask, phase_selection = _point_mask_selection(dataset, args.phase_mask)
        result, report = _attach_mask_selection(autophase(dataset, pivot=pivot,
                                   first_order=args.first_order, max_p1=args.max_p1,
                                   max_peaks=args.max_peaks, min_snr=args.min_snr,
                                   backend=args.backend, threads=args.threads,
                                   inclusion_mask=phase_mask, memory_bytes=_memory(args.memory) or 256 * 1024**2), "phase", phase_selection)
        cleanup_warnings = _save_new_files([(args.output, lambda path: write_dataset(result, path, format="native")),
                                            (args.report, _write_bytes(_json_bytes(report)))])
        return {"output": str(args.output), "report": str(args.report),
                "result_dataset_digest": report["result_dataset_digest"],
                "cleanup_warnings": cleanup_warnings}, 0
    if args.command == "peaks":
        _report_paths(args.output, args.csv)
        if args.allow_overlap and not args.integrate:
            raise NMRNextError("INVALID_PARAMETER", "--allow-overlap requires --integrate")
        if args.baseline_mask and not args.integrate:
            raise NMRNextError("INVALID_PARAMETER", "--baseline-mask requires --integrate")
        noise_mask, noise_selection = _point_mask_selection(dataset, args.noise_mask)
        report = _attach_mask_selection(pick_peaks(dataset, channel=args.channel, sign=args.sign,
                            threshold=args.threshold, snr=args.snr,
                            min_separation=args.min_separation,
                            separation_unit=args.separation_unit,
                            baseline=args.baseline, baseline_value=args.baseline_value,
                            max_peaks=args.max_peaks,
                            noise_mask=noise_mask), "noise", noise_selection)
        if args.integrate:
            if not report["peaks"]:
                raise NMRNextError("NO_PEAKS", "No peaks met the threshold; no regions were integrated")
            proposal = propose_regions(dataset, report, half_width=args.half_width,
                                       unit=args.half_width_unit)
            baseline_mask, baseline_selection = _point_mask_selection(dataset, args.baseline_mask)
            integrated = _attach_mask_selection(integrate_regions(dataset, proposal["regions"], channel=args.channel,
                                           baseline=args.integral_baseline,
                                           baseline_value=args.integral_baseline_value,
                                           allow_overlap=args.allow_overlap,
                                           baseline_mask=baseline_mask), "baseline", baseline_selection)
            report = {"peaks": report, "proposal": proposal, "integrals": integrated}
        outputs = [(args.output, _write_bytes(_json_bytes(report)))]
        if args.csv:
            outputs.append((args.csv, _write_bytes(_analysis_csv(report))))
        cleanup_warnings = _save_new_files(outputs)
        return {"output": str(args.output), "csv": str(args.csv) if args.csv else None,
                "dataset_digest": dataset_digest(dataset), "cleanup_warnings": cleanup_warnings}, 0
    if args.command == "integrate":
        _report_paths(args.output, args.csv)
        source = _json_file(Path(args.regions), analysis=True)
        if isinstance(source, dict) and "proposal" in source:
            source = source["proposal"]
        if isinstance(source, dict):
            if source.get("schema_version") != "1" or source.get("kind") != "region_proposal" or source.get("dataset_digest") != dataset_digest(dataset):
                raise NMRNextError("STALE_ANALYSIS", "Region proposal must match the current dataset")
            source = source.get("regions")
        baseline_mask, baseline_selection = _point_mask_selection(dataset, args.baseline_mask)
        report = _attach_mask_selection(integrate_regions(dataset, source, channel=args.channel,
                                   baseline=args.baseline, baseline_value=args.baseline_value,
                                   allow_overlap=args.allow_overlap,
                                   baseline_mask=baseline_mask), "baseline", baseline_selection)
        outputs = [(args.output, _write_bytes(_json_bytes(report)))]
        if args.csv:
            outputs.append((args.csv, _write_bytes(_analysis_csv(report))))
        cleanup_warnings = _save_new_files(outputs)
        return {"output": str(args.output), "csv": str(args.csv) if args.csv else None,
                "dataset_digest": dataset_digest(dataset), "cleanup_warnings": cleanup_warnings}, 0
    if args.command == "figure":
        from .figures import export_figure
        analysis = _analysis_report(args.analysis) if args.analysis else None
        try:
            return export_figure(dataset, args.output, component=args.component,
                                 analysis=analysis, title=args.title,
                                 contour_level=args.contour_level), 0
        except (KeyError, TypeError, IndexError) as exc:
            if analysis is None:
                raise
            raise NMRNextError("INVALID_ANALYSIS_REPORT", "Analysis JSON has malformed peak or region fields") from exc
    raise NMRNextError("UNKNOWN_COMMAND", "Unknown analysis command")


def _dispatch(args):
    if args.command == "first-spectrum":
        return _first_spectrum(args.output_dir), 0
    if args.command == "gui":
        try:
            from .gui import main as gui_main
        except ImportError as exc:
            if sys.platform == "win32":
                setup = "run tools/bootstrap.ps1 -WithGui"
            elif sys.platform.startswith("linux"):
                setup = "run bash tools/bootstrap.sh --gui"
            else:
                setup = "install the gui extra with python -m pip install -e '.[gui]'"
            raise NMRNextError("GUI_UNAVAILABLE", f"Optional GUI dependencies are unavailable; {setup}") from exc
        return None,gui_main(["nmrnext"])
    if args.command == "doctor": return _doctor(),0
    if args.command == "compare": return _compare(args)
    if args.command in ("autophase", "peaks", "integrate", "figure"):
        return _analysis_dispatch(args)
    # Validate recipe schema before importing codecs or reading sample arrays.
    config=_load_recipe(args) if args.command in ("plan","run") else None
    from .io import capabilities,describe_dataset,export_preflight,inspect_dataset,read_dataset,write_dataset
    if args.command == "formats": return {"formats":capabilities()},0
    if args.command == "demo":
        dataset=generate_demo(dimensions=args.dimensions,points=args.points,dtype=args.dtype)
        return write_dataset(dataset,args.output,format=args.format),0
    if args.command == "inspect":
        return inspect_dataset(args.input,format=args.format,max_bytes=_memory(args.memory) or 256*1024**2),0
    if args.command == "convert":
        if Path(args.input).resolve() == Path(args.output).resolve():
            raise NMRNextError("INPUT_OVERWRITE","Conversion output must differ from input")
        dataset=read_dataset(args.input,format=args.input_format,max_bytes=_memory(args.memory) or 256*1024**2)
        fmt=_format(args.output,args.format)
        if args.dry_run:
            report=export_preflight(dataset,fmt,allow_losses=args.allow_loss)
            return report,0 if report["ok"] else 1
        return write_dataset(dataset,args.output,format=fmt,allow_losses=args.allow_loss),0
    if args.command in ("plan","run"):
        recipe,source,target,memory,backend,threads=config
        if args.block_memory is not None:
            from .blocks import plan_file_operations, process_to_file
            block_memory = _memory(args.block_memory)
            if args.command == 'plan':
                return plan_file_operations(source, recipe['operations'], backend=backend, threads=threads,
                                            memory_bytes=block_memory, input_format=recipe.get('input_format'),
                                            output_format=_format(target, recipe.get('output_format')),
                                            output_path=target), 0
            return process_to_file(source, target, recipe['operations'], backend=backend, threads=threads,
                                   memory_bytes=block_memory, input_format=recipe.get('input_format'),
                                   output_format=_format(target, recipe.get('output_format'))), 0
        if args.command == "run" and target.exists():
            raise NMRNextError("OUTPUT_EXISTS",f"Output already exists: {target}; choose a new output path")
        header=describe_dataset(source,format=recipe.get("input_format"))
        plan=plan_operations(header,recipe["operations"],backend=backend,threads=threads,memory_bytes=memory)
        plan.update({"input":str(source),"output":str(target),"output_format":_format(target,recipe.get("output_format"))})
        if args.command == "plan": return plan,0
        dataset=read_dataset(source,format=recipe.get("input_format"),max_bytes=memory,expected_header=header)
        plan=plan_operations(dataset,recipe["operations"],backend=backend,threads=threads,memory_bytes=memory)
        started=time.perf_counter()
        result=run_operations(dataset,recipe["operations"],backend=backend,threads=threads,memory_bytes=memory)
        report=write_dataset(result,target,format=_format(target,recipe.get("output_format")),allow_losses=recipe.get("allow_losses",()))
        return {"result":report,"backend":plan["backend"],"operations":len(plan["steps"]),
                "elapsed_processing_and_write_seconds":time.perf_counter()-started,
                "estimated_working_bytes":plan["estimated_working_bytes"],"memory_scope":plan["memory_scope"]},0
    raise NMRNextError("UNKNOWN_COMMAND","Unknown command")


def _first_spectrum(output_dir):
    """Run a small, reproducible synthetic walkthrough using the public CLI paths."""
    directory = Path(output_dir)
    try:
        directory.mkdir()
    except FileExistsError as exc:
        raise NMRNextError("OUTPUT_EXISTS", f"Choose a new output directory: {directory}") from exc
    # The first workflow intentionally adds a known phase so that the second
    # workflow demonstrates a correction without estimating an unknown phase.
    recipes = {
        "process.json": {"schema_version": "1", "input": "synthetic-raw.nmrh5",
            "output": "processed.nmrh5", "backend": "native", "operations": [
                {"op": "window", "axis": "t2", "kind": "exponential", "lb_hz": 1.0, "first_point_scale": 0.5},
                {"op": "zero_fill", "axis": "t2", "size": 512},
                {"op": "fft", "axis": "t2"},
                {"op": "phase", "axis": "t2", "p0": 35.0, "p1": 0.0}]},
        "phase.json": {"schema_version": "1", "input": "processed.nmrh5",
            "output": "phased.nmrh5", "backend": "native", "operations": [
                {"op": "phase", "axis": "t2", "p0": -35.0, "p1": 0.0}]},
    }
    _save_new_files([(directory / name, _write_bytes(_json_bytes(recipe)))
                     for name, recipe in recipes.items()])
    parser = _parser()
    commands = [
        ["demo", str(directory / "synthetic-raw.nmrh5"), "--dimensions", "1"],
        ["run", str(directory / "process.json")],
        ["run", str(directory / "phase.json")],
        ["peaks", str(directory / "phased.nmrh5"), "--sign", "both", "--threshold", "10",
         "--baseline", "none", "--min-separation", "6", "--integrate", "--half-width", "2",
         "--output", str(directory / "analysis.json"), "--csv", str(directory / "analysis.csv")],
    ]
    for command in commands:
        _dispatch(parser.parse_args(command))
    return {"output_dir": str(directory), "synthetic": True,
            "phase_example": "A known +35 degree phase was applied and explicitly corrected by -35 degrees; no automatic phase was estimated.",
            "files": ["synthetic-raw.nmrh5", "process.json", "processed.nmrh5", "phase.json",
                      "phased.nmrh5", "analysis.json", "analysis.csv"],
            "next_step": f"Inspect {directory / 'analysis.json'} or open {directory / 'phased.nmrh5'} in the GUI."}


def _parser():
    parser=argparse.ArgumentParser(prog="nmrnext",description="Experimental reproducible NMR processing; synthetic and declared processed profiles")
    parser.add_argument("--version",action="version",version=__version__)
    sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("doctor",help="Report runtime and backend availability")
    sub.add_parser("formats",help="Report exact read/write profiles")
    sub.add_parser("gui",help="Open the optional spectrum workbench")
    first=sub.add_parser("first-spectrum",help="Create an offline synthetic 1D walkthrough in a new directory")
    first.add_argument("output_dir")
    demo=sub.add_parser("demo",help="Generate clearly synthetic fully sampled data")
    demo.add_argument("output");demo.add_argument("--dimensions",type=int,default=2)
    demo.add_argument("--points",type=int,nargs="+");demo.add_argument("--dtype",choices=["float32","float64"],default="float64")
    demo.add_argument("--format")
    inspect=sub.add_parser("inspect",help="Inspect metadata and storage")
    inspect.add_argument("input");inspect.add_argument("--format");inspect.add_argument("--json",action="store_true");inspect.add_argument("--memory")
    for command in ("plan","run"):
        p=sub.add_parser(command,help="Validate" if command=="plan" else "Execute a JSON recipe")
        p.add_argument("recipe");p.add_argument("--backend",choices=["native","numpy"]);p.add_argument("--threads",type=int)
        budgets = p.add_mutually_exclusive_group()
        budgets.add_argument("--memory")
        budgets.add_argument("--block-memory", help="Use bounded native HDF5 file processing with this working-array budget")
        if command=="plan":p.add_argument("--explain",action="store_true",help="JSON plan always includes explanations")
    conv=sub.add_parser("convert",help="Preflight or convert with explicit loss permission")
    conv.add_argument("input");conv.add_argument("output");conv.add_argument("--input-format");conv.add_argument("--format")
    conv.add_argument("--dry-run",action="store_true");conv.add_argument("--allow-loss",action="append",default=[]);conv.add_argument("--memory")
    compare=sub.add_parser("compare",help="Compare coordinates and numeric arrays without alignment")
    compare.add_argument("candidate");compare.add_argument("reference");compare.add_argument("--json",action="store_true")
    compare.add_argument("--rtol",type=float,default=1e-5);compare.add_argument("--atol",type=float,default=1e-5);compare.add_argument("--memory")
    auto=sub.add_parser("autophase",help="Estimate bounded complex 1D or full-hypercomplex 2D phase; save native result and JSON")
    auto.add_argument("input");auto.add_argument("output");auto.add_argument("--report",required=True)
    pivots=auto.add_mutually_exclusive_group()
    pivots.add_argument("--pivot",type=float,help="1D zero-based point pivot; default center")
    pivots.add_argument("--axis-pivot",action="append",metavar="AXIS=POINT",help="2D point pivot; repeat for both axis IDs, or omit for both centers")
    auto.add_argument("--first-order",action="store_true",help="Estimate slopes as well as zero-order corrections; requires identifiable separated lines on each axis")
    auto.add_argument("--max-p1",type=float,default=180.);auto.add_argument("--max-peaks",type=int,default=8)
    auto.add_argument("--min-snr",type=float,default=15.)
    auto.add_argument("--phase-mask",help="JSON file of inclusive point-index boxes for phase fitting")
    auto.add_argument("--backend",choices=["native","numpy"],default="native")
    auto.add_argument("--threads",type=int,default=1);auto.add_argument("--memory")
    peaks=sub.add_parser("peaks",help="Pick signed 1D/2D peaks and optionally integrate proposed regions")
    peaks.add_argument("input");peaks.add_argument("--output",required=True);peaks.add_argument("--csv")
    peaks.add_argument("--channel",type=int,default=0);peaks.add_argument("--sign",choices=["positive","negative","both"],default="positive")
    peaks.add_argument("--threshold",type=float);peaks.add_argument("--snr",type=float,default=5.)
    peaks.add_argument("--noise-mask",help="JSON file of inclusive point-index boxes for noise estimation")
    peaks.add_argument("--min-separation",type=float,default=1.)
    peaks.add_argument("--separation-unit",choices=["points","Hz","ppm"],default="points")
    peaks.add_argument("--baseline",choices=["median","none","constant"],default="median")
    peaks.add_argument("--baseline-value",type=float);peaks.add_argument("--max-peaks",type=int,default=1000)
    peaks.add_argument("--integrate",action="store_true")
    peaks.add_argument("--half-width",type=float,default=8.)
    peaks.add_argument("--half-width-unit",choices=["points","Hz","ppm"],default="points")
    peaks.add_argument("--integral-baseline",choices=["none","constant","edge_linear","reference_linear"],default="none")
    peaks.add_argument("--baseline-mask",help="JSON file of off-region point-index boxes for reference_linear")
    peaks.add_argument("--integral-baseline-value",type=float)
    peaks.add_argument("--allow-overlap",action="store_true");peaks.add_argument("--memory")
    integrate=sub.add_parser("integrate",help="Integrate explicitly bounded 1D/2D regions")
    integrate.add_argument("input");integrate.add_argument("regions")
    integrate.add_argument("--output",required=True);integrate.add_argument("--csv")
    integrate.add_argument("--channel",type=int,default=0)
    integrate.add_argument("--baseline",choices=["none","constant","edge_linear","reference_linear"],default="none")
    integrate.add_argument("--baseline-mask",help="JSON file of off-region point-index boxes for reference_linear")
    integrate.add_argument("--baseline-value",type=float)
    integrate.add_argument("--allow-overlap",action="store_true");integrate.add_argument("--memory")
    figure=sub.add_parser("figure",help="Save an optional Matplotlib SVG, PDF or PNG from frequency data")
    figure.add_argument("input");figure.add_argument("output");figure.add_argument("--analysis")
    figure.add_argument("--component",type=int,default=0);figure.add_argument("--title",default="SpinFlow spectrum")
    figure.add_argument("--contour-level",type=float);figure.add_argument("--memory")
    return parser


def main(argv=None):
    args=_parser().parse_args(argv)
    try:
        result,code=_dispatch(args)
        if result is not None:
            print(json.dumps(result,indent=2,allow_nan=False))
        return code
    except NMRNextError as exc:
        print(json.dumps({"error":exc.to_dict()},allow_nan=False),file=sys.stderr)
        return 2
    except (OSError,UnicodeError) as exc:
        print(json.dumps({"error":{"code":"IO_ERROR","message":str(exc)}}),file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"error":{"code":"CANCELLED","message":"Operation interrupted"}}),file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
