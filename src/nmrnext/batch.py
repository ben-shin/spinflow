"""Serial, fail-closed batch processing with stable item identities."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
from uuid import uuid4

from .assignments import (MAX_TEMPLATE_BYTES, _unique_object as _unique_template_object,
                          _validate_template, measure_assignments)
from .errors import NMRNextError
from .io import describe_dataset, read_dataset, write_dataset
from .model import json_copy, positive_int
from .operations import run_operations
from .planning import plan_operations
from .pipe_script import compile_pipe_script, parse_pipe_script


MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_CSV_FIELDS = ("item_id", "assignment_id", "label", "status", "source_locator",
               "source_sha256", "output", "run_sha256", "recipe_sha256",
               "template_sha256", "analysis_channel", "axis_map", "sign",
               "candidate_count", "count_truncated", "coordinates", "parent_points",
               "height", "volume", "volume_unit", "unit", "search_bounds",
               "integration_bounds", "point_bounds", "coordinate_bounds", "policy",
               "parent_dataset_digest", "warnings", "error", "stop_reason")


def _fail(code, message):
    raise NMRNextError(code, message)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("INVALID_BATCH_MANIFEST", f"Duplicate field {key!r}")
        result[key] = value
    return result


def _digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                     allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_manifest(manifest):
    try:
        m = json_copy(manifest, "batch manifest")
    except NMRNextError as exc:
        raise NMRNextError("INVALID_BATCH_MANIFEST", str(exc)) from exc
    required = {"schema_version", "kind", "backend", "threads", "memory_bytes", "items"}
    if not isinstance(m, dict) or set(m) - required - {"operations", "pipe_script", "assignment_template"} or required - set(m):
        _fail("INVALID_BATCH_MANIFEST", "Manifest fields are missing or unknown")
    if m["schema_version"] != "1" or m["kind"] != "batch_manifest":
        _fail("INVALID_BATCH_MANIFEST", "Expected batch_manifest schema 1")
    if ("operations" in m) == ("pipe_script" in m):
        _fail("INVALID_BATCH_MANIFEST", "Choose exactly one operations list or pipe_script")
    if "operations" in m and not isinstance(m["operations"], list):
        _fail("INVALID_BATCH_MANIFEST", "operations must be a list")
    if "pipe_script" in m and (not isinstance(m["pipe_script"], str) or not m["pipe_script"]):
        _fail("INVALID_BATCH_MANIFEST", "pipe_script must name one file")
    if not isinstance(m["backend"], str) or not m["backend"]:
        _fail("INVALID_BATCH_MANIFEST", "backend must be named")
    positive_int(m["threads"], "threads")
    positive_int(m["memory_bytes"], "memory_bytes")
    template = m.get("assignment_template")
    if template is not None and (not isinstance(template, str) or not template):
        _fail("INVALID_BATCH_MANIFEST", "assignment_template must name one file or be omitted")
    if not isinstance(m["items"], list) or not 1 <= len(m["items"]) <= 10000:
        _fail("INVALID_BATCH_MANIFEST", "Expected 1..10000 items")
    ids, outputs = set(), set()
    for item in m["items"]:
        if not isinstance(item, dict) or set(item) - {"item_id", "input", "output", "axis_map"} or {"item_id", "input", "output"} - set(item):
            _fail("INVALID_BATCH_MANIFEST", "Each item needs exact identity, paths and axis_map")
        identity = item["item_id"]
        if not isinstance(identity, str) or not identity.strip() or identity in ids:
            _fail("INVALID_BATCH_MANIFEST", "Item IDs must be nonempty and unique")
        ids.add(identity)
        if any(not isinstance(item[key], str) or not item[key] for key in ("input", "output")):
            _fail("INVALID_BATCH_MANIFEST", "Item paths must be nonempty")
        if template is not None and "axis_map" not in item:
            _fail("INVALID_BATCH_MANIFEST", "Assignment items require an axis_map")
        axis_map = item.setdefault("axis_map", {})
        if not isinstance(axis_map, dict) or any(
                not isinstance(k, str) or not isinstance(v, str) for k, v in axis_map.items()):
            _fail("INVALID_BATCH_MANIFEST", "axis_map must map axis IDs")
        if template is None and axis_map:
            _fail("INVALID_BATCH_MANIFEST", "Processing-only items require an empty axis_map")
        if item["output"] in outputs:
            _fail("OUTPUT_COLLISION", "Items must have distinct outputs")
        outputs.add(item["output"])
    return m


def _manifest_snapshot(path):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            _fail("INVALID_BATCH_MANIFEST", "Manifest exceeds 4 MiB")
        value = json.loads(raw, object_pairs_hook=_unique_object,
                           parse_constant=lambda x: _fail("INVALID_BATCH_MANIFEST", "Nonfinite number"))
    except (OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, NMRNextError):
            raise
        raise NMRNextError("INVALID_BATCH_MANIFEST", "Cannot load manifest JSON") from exc
    return _validate_manifest(value), hashlib.sha256(raw).hexdigest()


def load_batch_manifest(path: Path) -> dict:
    return _manifest_snapshot(path)[0]


def save_batch_manifest(manifest: dict, path: Path) -> dict:
    m = _validate_manifest(manifest)
    raw = (json.dumps(m, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2) + "\n").encode("utf-8")
    if len(raw) > MAX_MANIFEST_BYTES:
        _fail("INVALID_BATCH_MANIFEST", "Manifest exceeds 4 MiB")
    try:
        with Path(path).open("xb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise NMRNextError("OUTPUT_EXISTS", "Manifest destination already exists") from exc
    return m


def _resolve(base, name):
    path = Path(name)
    return (path if path.is_absolute() else base / path).resolve()


def _paths(manifest_path):
    path = Path(manifest_path).resolve()
    return {"journal_path": path.with_name(path.stem + ".batch-journal.json"),
            "json_path": path.with_name(path.stem + ".batch-results.json"),
            "csv_path": path.with_name(path.stem + ".batch-results.csv"),
            "commit_path": path.with_name(path.stem + ".batch-commit.json")}


def _source_identity(header, input_path):
    records = header.source_identity.get("files")
    if not isinstance(records, list) or not records:
        _fail("INVALID_HEADER_IDENTITY", "Source needs file identity")
    root = input_path if input_path.is_dir() else input_path.parent
    entries = []
    for record in records:
        path = Path(record["path"]).resolve()
        relative = path.relative_to(root).as_posix()
        entries.append({"path": relative, "size_bytes": record["size_bytes"],
                        "sha256": _hash_file(path)})
        stat = path.stat()
        if any(getattr(stat, attribute) != record[key] for attribute, key in
               (("st_size", "size_bytes"), ("st_mtime_ns", "mtime_ns"),
                ("st_dev", "device"), ("st_ino", "inode"))):
            _fail("INPUT_CHANGED", "Input changed while hashing source identity")
    entries.sort(key=lambda x: x["path"])
    return {"files": entries, "sha256": _digest(entries)}


def _prepare(manifest_path, *, existing=False):
    path = Path(manifest_path).resolve()
    m, manifest_sha = _manifest_snapshot(path)
    base = path.parent
    template_path = _resolve(base, m["assignment_template"]) if m.get("assignment_template") else None
    template = template_sha = None
    if template_path is not None:
        with template_path.open("rb") as stream:
            template_raw = stream.read(MAX_TEMPLATE_BYTES + 1)
        if len(template_raw) > MAX_TEMPLATE_BYTES:
            _fail("INVALID_ASSIGNMENT_TEMPLATE", "Template exceeds 4 MiB")
        try:
            template = _validate_template(json.loads(template_raw,
                           object_pairs_hook=_unique_template_object))[0]
        except (ValueError, UnicodeError) as exc:
            if isinstance(exc, NMRNextError):
                raise
            raise NMRNextError("INVALID_ASSIGNMENT_TEMPLATE", "Cannot parse assignment template") from exc
        template_sha = hashlib.sha256(template_raw).hexdigest()
    script = None
    if "pipe_script" in m:
        script_path = _resolve(base, m["pipe_script"])
        with script_path.open("rb") as stream:
            raw = stream.read((1 << 20) + 1)
        if len(raw) > (1 << 20):
            _fail("INVALID_SCRIPT", "Script exceeds 1 MiB")
        script = parse_pipe_script(raw.decode("utf-8"), source_name=str(script_path))
    sidecars = _paths(path)
    protected = {path, *sidecars.values()}
    if template_path is not None:
        protected.add(template_path)
    if script is not None:
        protected.add(script_path)
    targets, locations = set(), []
    for item in m["items"]:
        source, target = _resolve(base, item["input"]), _resolve(base, item["output"])
        if target.suffix.lower() not in (".nmrh5", ".h5") or not target.parent.is_dir():
            _fail("INVALID_BATCH_OUTPUT", "Output must be a native file in an existing directory")
        if target in targets or target in protected or target == source:
            _fail("OUTPUT_COLLISION", "Output overlaps another item or a control file")
        if source.is_dir() and target.is_relative_to(source):
            _fail("OUTPUT_COLLISION", "Output cannot be inside its acquisition input")
        targets.add(target)
        if target.exists() and not existing:
            _fail("OUTPUT_EXISTS", f"Batch output already exists: {target}")
        locations.append((item, source, target))
    sources = {source for _, source, _ in locations}
    if targets & sources:
        _fail("OUTPUT_COLLISION", "Batch output overlaps an input source")
    prepared = []
    for item, source, target in locations:
        entry = {"item_id": item["item_id"], "input": source, "output": target,
                 "axis_map": item["axis_map"], "header": None, "source_identity": None,
                 "recipe": None, "plan": None, "operations": None, "recipe_sha256": None,
                 "refusal": None}
        try:
            header = describe_dataset(source)
            identity = _source_identity(header, source)
            if script is None:
                operations = m["operations"]
                recipe = {"schema_version": "1", "operations": operations,
                          "effective_input": str(source), "effective_output": str(target)}
            else:
                # Compile with an absent peer on resume; only the effective
                # output locator changes back to the declared manifest target.
                compile_target = target
                if existing and target.exists():
                    compile_target = target.with_name("." + target.stem + "." + uuid4().hex + ".nmrh5")
                recipe = compile_pipe_script(script, header, input_path=source, output_path=compile_target)
                recipe["effective_output"] = str(target)
                operations = recipe["operations"]
            plan = plan_operations(header, operations, backend=m["backend"],
                                   threads=m["threads"], memory_bytes=m["memory_bytes"])
            if template is not None and len(plan["output_axes"]) > 2:
                _fail("UNSUPPORTED_ASSIGNMENT_DIMENSIONS", "Assignment analysis supports only 1D/2D")
            entry.update(header=header, source_identity=identity, recipe=recipe, plan=plan,
                         operations=operations, recipe_sha256=_digest(recipe))
        except (NMRNextError, OSError, UnicodeError) as exc:
            entry["refusal"] = exc.to_dict() if isinstance(exc, NMRNextError) else {
                "code": "BATCH_ITEM_PREFLIGHT_FAILED", "message": str(exc),
                "details": {"type": type(exc).__name__}}
        prepared.append(entry)
    run_identity = {"manifest_sha256": manifest_sha, "template_sha256": template_sha,
                    "script_sha256": script["source_sha256"] if script else None,
                    "backend": m["backend"], "threads": m["threads"],
                    "memory_bytes": m["memory_bytes"]}
    return {"manifest": m, "manifest_path": path, "template": template,
            "template_sha256": template_sha, "items": prepared,
            "run_sha256": _digest(run_identity), "sidecars": sidecars}


def plan_batch(path: Path) -> dict:
    """Plan every item from headers before reading a numeric sample."""
    prepared = _prepare(path)
    return {"schema_version": "1", "kind": "batch_plan",
            "run_sha256": prepared["run_sha256"],
            "items": [{"item_id": p["item_id"], "input": str(p["input"]),
                       "output": str(p["output"]),
                       "format": p["header"].source_identity["format"] if p["header"] else None,
                       "source_identity": p["source_identity"],
                       "header": {"shape": list(p["header"].shape),
                                  "dtype": p["header"].dtype.name,
                                  "axes": [a.to_dict() for a in p["header"].axes],
                                  "complex_axes": list(p["header"].complex_axes)} if p["header"] else None,
                       "recipe": p["recipe"], "operation_plan": p["plan"],
                       "refusal": p["refusal"]}
                      for p in prepared["items"]],
            "refusals": [{"item_id": p["item_id"], "error": p["refusal"]}
                         for p in prepared["items"] if p["refusal"] is not None]}


def _atomic_bytes(path, raw):
    """Fsync and publish a new immutable receipt without replacing any path."""
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        try:
            _publish(Path(temporary), path)
        except FileExistsError as exc:
            raise NMRNextError("OUTPUT_EXISTS", f"Report already exists: {path}") from exc
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       allow_nan=False, indent=2) + "\n").encode("utf-8")


def _publish(stage, target):
    if target.exists():
        _fail("OUTPUT_EXISTS", f"Batch output already exists: {target}")
    if os.name == "nt":
        os.rename(stage, target)  # Windows rename refuses an existing destination.
    else:
        os.link(stage, target)
        os.unlink(stage)


def _rollback_owned_output(path, identity, digest):
    """Remove only this attempt's unchanged, unpublished final output."""
    if identity is None or digest is None:
        return
    try:
        info = path.stat(follow_symlinks=False)
        if (info.st_dev, info.st_ino) == identity and _hash_file(path) == digest:
            path.unlink()
    except FileNotFoundError:
        pass


def _empty_rows(template, item, prepared, status, error=None, stop_reason=None):
    if template is None:
        return []
    return [_enrich({"assignment_id": a["assignment_id"], "label": a["label"],
                     "sign": a["sign"], "status": status, "candidate_count": None,
                     "count_truncated": False, "coordinates": None, "parent_points": None,
                     "height": None, "volume": None, "volume_unit": None, "unit": a["unit"],
                     "search_bounds": a["search_bounds"], "integration_bounds": a["integration_bounds"],
                     "point_bounds": None, "coordinate_bounds": None, "policy": None,
                     "parent_dataset_digest": None, "warnings": [], "error": error},
                    item, prepared, stop_reason)
            for a in template["assignments"]]


def _enrich(row, item, prepared, stop_reason=None):
    return {**row, "item_id": item["item_id"], "source_locator": str(item["input"]),
            "source_sha256": item["source_identity"]["sha256"] if item["source_identity"] else None,
            "output": str(item["output"]), "run_sha256": prepared["run_sha256"],
            "recipe_sha256": item["recipe_sha256"],
            "template_sha256": prepared["template_sha256"],
            "analysis_channel": prepared["template"]["analysis_channel"] if prepared["template"] else None,
            "axis_map": dict(item["axis_map"]), "stop_reason": stop_reason}


def _generation_paths(base, index):
    if index == 0:
        return base
    return {key: path.with_name(path.stem + f".{index:04d}" + path.suffix)
            for key, path in base.items()}


def _latest_generation(base):
    index, latest = 0, None
    while any(path.exists() or path.is_symlink() for path in _generation_paths(base, index).values()):
        paths = _generation_paths(base, index)
        if paths["commit_path"].exists() or paths["commit_path"].is_symlink():
            if not _sealed_generation(paths):
                _fail("INVALID_BATCH_RESUME", "Sealed batch receipt was changed")
            latest = index
        index += 1
    if index == 0:
        _fail("INVALID_BATCH_RESUME", "Resume needs an existing batch journal")
    return latest, _generation_paths(base, latest) if latest is not None else None, index


def _sealed_generation(paths):
    if not all(path.is_file() for path in paths.values()):
        return False
    try:
        marker = json.loads(paths["commit_path"].read_bytes())
        return (isinstance(marker, dict) and marker.get("schema_version") == "1" and
                marker.get("kind") == "batch_receipt_commit" and
                marker.get("files") == {key: _hash_file(path) for key, path in paths.items()
                                        if key != "commit_path"})
    except (OSError, ValueError, TypeError):
        return False


def _committed_rows(items, journal, rows_by_item=None):
    """Every generation carries every committed record, regardless of order."""
    rows_by_item = {} if rows_by_item is None else rows_by_item
    return [row for item in items
            for row in (journal["items"][item["item_id"]]["rows"]
                        if item["item_id"] in journal["items"] else
                        rows_by_item.get(item["item_id"], []))]


def _csv_bytes(rows):
    import io
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: json.dumps(row[key], ensure_ascii=False, sort_keys=True,
                                         allow_nan=False) if isinstance(row.get(key), (dict, list))
                         else row.get(key) for key in _CSV_FIELDS})
    return buffer.getvalue().encode("utf-8")


def _write_receipts(prepared, journal, rows):
    sidecars = _generation_paths(prepared["receipt_base"], prepared["receipt_index"])
    journal_raw = _json_bytes(journal)
    result = {"schema_version": "1", "kind": "batch_results", "run_sha256": prepared["run_sha256"],
              "rows": rows, "items": journal["items"]}
    payloads = {"journal_path": journal_raw, "json_path": _json_bytes(result),
                "csv_path": _csv_bytes(rows)}
    for key, raw in payloads.items():
        _atomic_bytes(sidecars[key], raw)
    marker = {"schema_version": "1", "kind": "batch_receipt_commit",
              "files": {key: hashlib.sha256(raw).hexdigest() for key, raw in payloads.items()}}
    _atomic_bytes(sidecars["commit_path"], _json_bytes(marker))
    prepared["sidecars"] = sidecars
    prepared["receipt_index"] += 1


def _event(callback, item, state, completed, total):
    if callback is not None:
        callback(MappingProxyType({"item_id": item["item_id"], "state": state,
                                   "completed": completed, "total": total}))


def _load_resume_receipts(prepared, paths):
    if not _sealed_generation(paths):
        _fail("INVALID_BATCH_RESUME", "Receipt seal changed during resume")
    try:
        def no_constant(value):
            _fail("INVALID_BATCH_RESUME", f"Nonfinite receipt value {value}")
        def finite_float(value):
            parsed = float(value)
            if not math.isfinite(parsed):
                _fail("INVALID_BATCH_RESUME", "Nonfinite receipt number")
            return parsed
        journal = json.loads(paths["journal_path"].read_bytes(),
                             object_pairs_hook=_unique_object, parse_constant=no_constant,
                             parse_float=finite_float)
        result = json.loads(paths["json_path"].read_bytes(),
                            object_pairs_hook=_unique_object, parse_constant=no_constant,
                            parse_float=finite_float)
    except (OSError, ValueError, UnicodeError) as exc:
        if isinstance(exc, NMRNextError):
            raise NMRNextError("INVALID_BATCH_RESUME", str(exc)) from exc
        raise NMRNextError("INVALID_BATCH_RESUME", "Cannot read batch receipts") from exc
    if (not isinstance(journal, dict) or set(journal) != {"schema_version", "kind", "run_sha256", "items"} or
            journal["kind"] != "batch_journal" or journal["schema_version"] != "1" or
            journal["run_sha256"] != prepared["run_sha256"] or not isinstance(journal["items"], dict) or
            not isinstance(result, dict) or result.get("kind") != "batch_results" or
            result.get("schema_version") != "1" or result.get("run_sha256") != prepared["run_sha256"] or
            result.get("items") != journal["items"] or not isinstance(result.get("rows"), list)):
        _fail("INVALID_BATCH_RESUME", "Receipt schema or run identity changed")
    try:
        if paths["csv_path"].read_bytes() != _csv_bytes(result["rows"]):
            _fail("INVALID_BATCH_RESUME", "CSV differs from the result receipt")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, NMRNextError):
            raise
        raise NMRNextError("INVALID_BATCH_RESUME", "Cannot validate CSV receipt") from exc
    by_id = {p["item_id"]: p for p in prepared["items"]}
    if set(journal["items"]) - set(by_id):
        _fail("INVALID_BATCH_RESUME", "Receipt has a foreign item ID")
    assignment_ids = (set() if prepared["template"] is None else
                      {a["assignment_id"] for a in prepared["template"]["assignments"]})
    seen_rows = set()
    for row in result["rows"]:
        if not isinstance(row, dict):
            _fail("INVALID_BATCH_RESUME", "Malformed result row")
        pair = (row.get("item_id"), row.get("assignment_id"))
        if pair[0] not in by_id or pair[1] not in assignment_ids or pair in seen_rows:
            _fail("INVALID_BATCH_RESUME", "Result has foreign or duplicate item/assignment row")
        seen_rows.add(pair)
        if pair[0] not in journal["items"] and (row.get("status") != "not_run" or
                row.get("height") is not None or row.get("volume") is not None):
            _fail("INVALID_BATCH_RESUME", "Unattempted result has scientific values")
    for identity, record in journal["items"].items():
        item = by_id[identity]
        if not isinstance(record, dict) or set(record) != {"item_id", "input", "output",
                "source_identity", "recipe_sha256", "template_sha256", "axis_map",
                "status", "output_sha256", "rows", "error"}:
            _fail("INVALID_BATCH_RESUME", "Malformed saved item record")
        if (record["item_id"] != identity or record["input"] != str(item["input"]) or
                record["output"] != str(item["output"]) or
                record["source_identity"] != item["source_identity"] or
                record["recipe_sha256"] != item["recipe_sha256"] or
                record["template_sha256"] != prepared["template_sha256"] or
                record["axis_map"] != item["axis_map"] or
                record["status"] not in ("completed", "failed") or
                not isinstance(record["rows"], list)):
            _fail("INVALID_BATCH_RESUME", "Saved item binding or status changed")
        assignments = [] if prepared["template"] is None else [
            a["assignment_id"] for a in prepared["template"]["assignments"]]
        rows = record["rows"]
        if len(rows) != len(assignments) or any(not isinstance(row, dict) for row in rows) or [
                row.get("assignment_id") for row in rows] != assignments:
            _fail("INVALID_BATCH_RESUME", "Saved assignment row identities changed")
        for row in rows:
            if (row.get("item_id") != identity or row.get("source_locator") != str(item["input"]) or
                    row.get("source_sha256") != (item["source_identity"] or {}).get("sha256") or
                    row.get("output") != str(item["output"]) or
                    row.get("run_sha256") != prepared["run_sha256"] or
                    row.get("recipe_sha256") != item["recipe_sha256"] or
                    row.get("template_sha256") != prepared["template_sha256"] or
                    row.get("axis_map") != item["axis_map"] or
                    row.get("analysis_channel") != prepared["template"]["analysis_channel"]):
                _fail("INVALID_BATCH_RESUME", "Saved row context changed")
            if record["status"] == "failed" and (row.get("status") != "failed" or
                    row.get("height") is not None or row.get("volume") is not None):
                _fail("INVALID_BATCH_RESUME", "Failed row contains scientific values")
        receipt_rows = [row for row in result["rows"] if isinstance(row, dict) and row.get("item_id") == identity]
        if receipt_rows != rows:
            _fail("INVALID_BATCH_RESUME", "Journal rows differ from the independent result receipt")
        if record["status"] == "completed":
            if (not item["output"].is_file() or record["output_sha256"] != _hash_file(item["output"])):
                _fail("INVALID_BATCH_RESUME", "Completed native output identity changed")
            if prepared["template"] is not None:
                dataset = read_dataset(item["output"], format="native",
                                       max_bytes=prepared["manifest"]["memory_bytes"])
                expected = [_enrich(row, item, prepared) for row in
                            measure_assignments(dataset, prepared["template"], axis_map=item["axis_map"])]
                if rows != expected:
                    _fail("INVALID_BATCH_RESUME", "Saved science rows disagree with native output")
                dataset = None
        elif item["output"].exists():
            _fail("INVALID_BATCH_RESUME", "Failed item unexpectedly has a final output")
    return journal


def run_batch(path: Path, *, resume: bool = False, continue_on_error: bool = False,
              should_stop=None, on_progress=None) -> dict:
    """Preflight all items, then process one input and one output at a time."""
    prepared = _prepare(path, existing=resume)
    prepared["receipt_base"] = prepared["sidecars"]
    if resume:
        index, paths, next_index = _latest_generation(prepared["receipt_base"])
        prepared["receipt_index"] = next_index
        if index is None:
            journal = {"schema_version": "1", "kind": "batch_journal",
                       "run_sha256": prepared["run_sha256"], "items": {}}
        else:
            prepared["sidecars"] = paths
            journal = _load_resume_receipts(prepared, paths)
        # Failed attempts remain in the prior immutable generation. This new
        # attempt commits only completed outputs; failed items may be retried
        # or truthfully marked not_run if stopping before their turn.
        journal = {**journal, "items": {identity: record for identity, record in
                    journal["items"].items() if record["status"] == "completed"}}
    else:
        if any(p.exists() for p in prepared["receipt_base"].values()):
            _fail("OUTPUT_EXISTS", "Batch report destination already exists")
        prepared["receipt_index"] = 0
        journal = {"schema_version": "1", "kind": "batch_journal",
                   "run_sha256": prepared["run_sha256"], "items": {}}
    # Refuse every existing output without a matching, complete journal entry
    # before any numeric input is read or new output is made.
    for item in prepared["items"]:
        old = journal["items"].get(item["item_id"])
        if item["output"].exists():
            if (not resume or old is None or old.get("status") != "completed" or
                    old.get("output_sha256") != _hash_file(item["output"])):
                _fail("INVALID_BATCH_RESUME", "Existing output does not match a completed item")
        elif old is not None and old.get("status") == "completed":
            _fail("INVALID_BATCH_RESUME", "Completed output is missing")
    items = prepared["items"]
    rows_by_item = {item["item_id"]: journal["items"][item["item_id"]]["rows"]
                    for item in items if item["item_id"] in journal["items"] and
                    journal["items"][item["item_id"]]["status"] == "completed"}
    _write_receipts(prepared, journal, _committed_rows(items, journal, rows_by_item))
    completed = skipped = failed = 0
    stopped = None
    for index, item in enumerate(items):
        identity = item["item_id"]
        if stopped is None and should_stop is not None:
            try:
                if should_stop(): stopped = "requested"
            except Exception as exc:
                stopped = f"stop_callback_error:{type(exc).__name__}:{exc}"
        old = journal["items"].get(identity)
        if resume and old is not None and old.get("status") == "completed":
            completed += 1; skipped += 1
            rows_by_item[identity] = old["rows"]
            try:
                _event(on_progress, item, "skipped", completed, len(items))
            except Exception as exc:
                stopped = f"progress_callback_error:{type(exc).__name__}:{exc}"
            continue
        if stopped is not None:
            rows_by_item[identity] = _empty_rows(prepared["template"], item, prepared,
                                                 "not_run", stop_reason=stopped)
            try:
                _event(on_progress, item, "not_run", completed, len(items))
            except Exception:
                pass
            continue
        try:
            _event(on_progress, item, "started", completed, len(items))
        except Exception as exc:
            stopped = f"progress_callback_error:{type(exc).__name__}:{exc}"
            rows_by_item[identity] = _empty_rows(prepared["template"], item, prepared,
                                                 "not_run", stop_reason=stopped)
            continue
        stage_dir = Path(tempfile.mkdtemp(prefix=".spinflow-batch-", dir=item["output"].parent))
        stage = stage_dir / "payload.nmrh5"
        stage_owned = False
        published_identity = stage_sha = None
        dataset = result = None
        try:
            if item["refusal"] is not None:
                raise NMRNextError(item["refusal"]["code"], item["refusal"]["message"],
                                   details=item["refusal"].get("details"))
            # read_dataset checks the inspected header/stat identity again.
            dataset = read_dataset(item["input"], expected_header=item["header"],
                                   max_bytes=prepared["manifest"]["memory_bytes"])
            result = run_operations(dataset, item["operations"], backend=prepared["manifest"]["backend"],
                                    threads=prepared["manifest"]["threads"],
                                    memory_bytes=prepared["manifest"]["memory_bytes"])
            local_rows = (measure_assignments(result, prepared["template"], axis_map=item["axis_map"])
                          if prepared["template"] is not None else [])
            enriched = [_enrich(row, item, prepared) for row in local_rows]
            stage_report = write_dataset(result, stage, format="native")
            stage_owned = True
            stage_sha = stage_report["output_sha256"]
            owned_info = stage.stat(follow_symlinks=False)
            owned_identity = (owned_info.st_dev, owned_info.st_ino)
            _publish(stage, item["output"])
            published_identity = owned_identity
            info = item["output"].stat(follow_symlinks=False)
            if (info.st_dev, info.st_ino) != owned_identity:
                _fail("OUTPUT_CHANGED", "Published output identity differs from owned stage")
            output_sha = _hash_file(item["output"])
            status, error = "completed", None
        except Exception as exc:
            _rollback_owned_output(item["output"], published_identity, stage_sha)
            status = "failed"
            error = exc.to_dict() if isinstance(exc, NMRNextError) else {
                "code": "BATCH_ITEM_FAILED", "message": str(exc), "details": {"type": type(exc).__name__}}
            enriched = _empty_rows(prepared["template"], item, prepared, "failed", error)
            output_sha = None
        finally:
            if stage_owned:
                stage.unlink(missing_ok=True)
            try:
                stage_dir.rmdir()
            except OSError:
                pass  # Preserve an unowned file written into our private directory.
            dataset = result = None  # Release prior item before the next reader.
        record = {"item_id": identity, "input": str(item["input"]), "output": str(item["output"]),
                  "source_identity": item["source_identity"], "recipe_sha256": item["recipe_sha256"],
                  "template_sha256": prepared["template_sha256"], "axis_map": item["axis_map"],
                  "status": status, "output_sha256": output_sha, "rows": enriched, "error": error}
        journal["items"][identity] = record
        rows_by_item[identity] = enriched
        try:
            _write_receipts(prepared, journal, _committed_rows(items, journal, rows_by_item))
        except Exception:
            if status == "completed":
                _rollback_owned_output(item["output"], published_identity, stage_sha)
            raise
        completed += 1
        if status == "failed": failed += 1
        try:
            _event(on_progress, item, status, completed, len(items))
        except Exception as exc:
            stopped = f"progress_callback_error:{type(exc).__name__}:{exc}"
        if status == "failed" and not continue_on_error:
            stopped = stopped or "item_failed"
    rows = _committed_rows(items, journal, rows_by_item)
    _write_receipts(prepared, journal, rows)
    sidecars = prepared["sidecars"]
    return {"schema_version": "1", "kind": "batch_run", "run_sha256": prepared["run_sha256"],
            "journal_path": str(sidecars["journal_path"]), "json_path": str(sidecars["json_path"]),
            "csv_path": str(sidecars["csv_path"]), "total": len(items), "completed": completed,
            "skipped": skipped, "failed": failed, "not_run": len(items)-completed,
            "stop_reason": stopped}
