"""Strict data-only intake for a small, explicitly qualified NMRPipe syntax subset.

This module never invokes NMRPipe, a converter, a shell, or a macro. A parsed
file is not permission to process its original literal input/output paths.
"""

import hashlib
import math
from pathlib import Path
import re

from .errors import NMRNextError
from .model import DatasetHeader
from .operations import _transition


_TOKEN = re.compile(r"\||[^\s|]+")
_LITERAL = re.compile(r"[A-Za-z0-9_./@-]+\Z")
_FORBIDDEN = re.compile(r"[`$;&<>'\"{}\[\]()]|\\(?!\r?\n)")
_OPTIONS = {
    "EM": {"-lb", "-c"},
    "SP": {"-off", "-end", "-pow", "-c"},
    "ZF": {"-size"},
    "FT": set(),
    "PS": {"-p0", "-p1"},
    "TP": set(),
}


def _error(code, message, token, source_name):
    raise NMRNextError(code, f"{source_name}:{token['line']}:{token['column']}: {message}",
                       details={"source": source_name, "line": token["line"],
                                "column": token["column"], "token": token["text"]})


def _literal(token, source_name):
    text = token["text"]
    if (not _LITERAL.fullmatch(text) or text.startswith("/") or ".." in Path(text).parts
            or text in (".", "..") or any(c in text for c in ("*", "?", "\\", ":"))):
        _error("UNSAFE_SCRIPT_PATH", "Expected a literal relative item path", token, source_name)
    return text


def _numeric(token, source_name):
    try:
        value = float(token["text"])
    except ValueError:
        _error("INVALID_SCRIPT_VALUE", "Expected a finite number", token, source_name)
    if not math.isfinite(value):
        _error("INVALID_SCRIPT_VALUE", "Expected a finite number", token, source_name)
    return value


def parse_pipe_script(text: str, *, source_name: str) -> dict:
    """Parse literal pipeline stages and return source-located data, never code."""
    if not isinstance(text, str) or not isinstance(source_name, str) or not source_name:
        raise NMRNextError("INVALID_SCRIPT", "Script text and source name are required")
    raw = text.encode("utf-8")
    if not raw or len(raw) > 1024 * 1024 or "\x00" in text:
        raise NMRNextError("INVALID_SCRIPT", "Script must be nonempty UTF-8 text under 1 MiB")
    # Replace only literal continuation characters, retaining all offsets.
    cleaned = re.sub(r"\\(?=\r?\n)", " ", text)
    tokens = []
    line, line_start, scan_from = 1, 0, 0
    for match in _TOKEN.finditer(cleaned):
        start = match.start()
        while (newline := text.find("\n", scan_from, start)) != -1:
            line += 1
            line_start = newline + 1
            scan_from = newline + 1
        scan_from = start
        value = match.group()
        token = {"text": value, "line": line, "column": start - line_start + 1}
        if len(tokens) >= 2048:
            _error("INVALID_SCRIPT", "More than 2048 tokens", token, source_name)
        if _FORBIDDEN.search(value) or value == "\\":
            _error("UNSAFE_SCRIPT", "Shell syntax is not supported", token, source_name)
        tokens.append(token)
    if not tokens:
        raise NMRNextError("INVALID_SCRIPT", "Empty pipeline")
    chunks = [[]]
    for token in tokens:
        if token["text"] == "|":
            if not chunks[-1]:
                _error("INVALID_SCRIPT", "Empty pipeline stage", token, source_name)
            chunks.append([])
        else:
            chunks[-1].append(token)
    if not chunks[-1] or len(chunks) > 256:
        _error("INVALID_SCRIPT", "Empty or oversized pipeline", tokens[-1], source_name)
    stages = []
    input_literal = output_literal = None
    overwrite = False
    for index, chunk in enumerate(chunks):
        if chunk[0]["text"] != "nmrPipe":
            _error("UNSUPPORTED_SCRIPT_COMMAND", "Only literal nmrPipe stages are supported",
                   chunk[0], source_name)
        name = None
        fn_location = None
        options = {}
        j = 1
        while j < len(chunk):
            flag = chunk[j]
            key = flag["text"]
            if key == "-ov":
                if overwrite:
                    _error("DUPLICATE_SCRIPT_FLAG", "Repeated -ov", flag, source_name)
                overwrite = True
                j += 1
                continue
            if key in ("-fn", "-in", "-out") or key.startswith("-"):
                if key not in ("-fn", "-in", "-out") and name is None:
                    _error("UNSUPPORTED_SCRIPT_FLAG", "Function must precede its options", flag, source_name)
                if key not in ("-fn", "-in", "-out") and key not in _OPTIONS[name]:
                    _error("UNSUPPORTED_SCRIPT_FLAG", f"Unsupported {name} flag {key}", flag, source_name)
                if j + 1 >= len(chunk):
                    _error("INVALID_SCRIPT", "Flag requires one value", flag, source_name)
                value = chunk[j + 1]
                if key in ("-fn", "-in", "-out") and value["text"].startswith("-"):
                    _error("INVALID_SCRIPT", "Flag requires one value", flag, source_name)
                if key == "-fn":
                    if name is not None:
                        _error("DUPLICATE_SCRIPT_FLAG", "Repeated -fn", flag, source_name)
                    name = value["text"]
                    fn_location = flag
                    if name not in _OPTIONS:
                        _error("UNSUPPORTED_SCRIPT_FUNCTION", f"Unsupported function {name}", value, source_name)
                elif key == "-in":
                    if input_literal is not None or index != 0:
                        _error("INVALID_SCRIPT_BINDING", "-in must occur once in first stage", flag, source_name)
                    input_literal = _literal(value, source_name)
                elif key == "-out":
                    if output_literal is not None or index != len(chunks) - 1:
                        _error("INVALID_SCRIPT_BINDING", "-out must occur once in last stage", flag, source_name)
                    output_literal = _literal(value, source_name)
                    if Path(output_literal).suffix.lower() in (".ft1", ".ft2"):
                        _error("UNSUPPORTED_SCRIPT_OUTPUT", "Batch output is native only", value, source_name)
                else:
                    if key in options:
                        _error("DUPLICATE_SCRIPT_FLAG", f"Repeated {key}", flag, source_name)
                    options[key] = _numeric(value, source_name)
                j += 2
            else:
                _error("UNSUPPORTED_SCRIPT_TOKEN", "Unexpected token", flag, source_name)
        if name is None:
            if index != 0 or input_literal is None or len(chunk) != 3:
                _error("INVALID_SCRIPT", "Only the first stage may bind input without -fn",
                       chunk[0], source_name)
        else:
            stages.append({"name": name, "options": options,
                           "line": fn_location["line"], "column": fn_location["column"],
                           "source": source_name})
    if input_literal is None or output_literal is None or not stages:
        _error("INVALID_SCRIPT_BINDING", "One input, output and processing stage are required",
               tokens[0], source_name)
    return {"schema_version": "1", "kind": "pipe_script", "source_name": source_name,
            "source_text": text,
            "source_sha256": hashlib.sha256(raw).hexdigest(), "input_literal": input_literal,
            "output_literal": output_literal, "overwrite_requested": overwrite,
            "stages": stages}


def compile_pipe_script(parsed: dict, header: DatasetHeader, *, input_path: Path,
                        output_path: Path) -> dict:
    """Bind safe parsed operations to one new native item output, sample-free."""
    if not isinstance(parsed, dict) or parsed.get("kind") != "pipe_script":
        raise NMRNextError("INVALID_SCRIPT", "Parsed pipeline record required")
    source_text, source_name = parsed.get("source_text"), parsed.get("source_name")
    if not isinstance(source_text, str) or not isinstance(source_name, str):
        raise NMRNextError("INVALID_SCRIPT_INTEGRITY", "Original literal script is required")
    try:
        canonical = parse_pipe_script(source_text, source_name=source_name)
    except NMRNextError as exc:
        raise NMRNextError("INVALID_SCRIPT_INTEGRITY", "Literal script no longer parses") from exc
    if parsed != canonical:
        raise NMRNextError("INVALID_SCRIPT_INTEGRITY", "Parsed stages differ from literal script")
    if not isinstance(header, DatasetHeader):
        raise NMRNextError("INVALID_DATASET", "A sample-free DatasetHeader is required")
    source = parsed["source_name"]
    target = Path(output_path)
    origin = Path(input_path)
    if not origin.is_absolute() or not target.is_absolute() or origin == target:
        raise NMRNextError("INVALID_SCRIPT_BINDING", "Distinct absolute item paths required")
    if target.suffix.lower() not in (".nmrh5", ".h5"):
        raise NMRNextError("UNSUPPORTED_SCRIPT_OUTPUT", "Batch output is native only")
    if target.exists():
        raise NMRNextError("OUTPUT_EXISTS", "Batch output must be new, even with -ov")
    axes, bits, metadata = header.axes, header.complex_axes, header.metadata
    operations, provenance = [], []
    for stage in parsed["stages"]:
        name, opts = stage["name"], stage["options"]
        token = {"text": name, "line": stage["line"], "column": stage["column"]}
        if name not in _OPTIONS:
            _error("UNSUPPORTED_SCRIPT_FUNCTION", "Unsupported function", token, source)
        if not isinstance(opts, dict) or set(opts) - _OPTIONS[name]:
            _error("UNSUPPORTED_SCRIPT_FLAG", "Unsupported or unparsed function flag", token, source)
        if any(type(value) not in (int, float) or not math.isfinite(value)
               for value in opts.values()):
            _error("INVALID_SCRIPT_VALUE", "Nonfinite or nonnumeric flag value", token, source)
        if name in ("FT", "TP"):
            _error("UNQUALIFIED_SCRIPT_FUNCTION", f"{name} requires pinned NMRPipe 13 parity",
                   token, source)
        if name == "PS" and (opts.get("-p0", 0) != 0 or opts.get("-p1", 0) != 0):
            _error("UNQUALIFIED_SCRIPT_FUNCTION", "Nonzero PS requires pinned NMRPipe 13 parity",
                   token, source)
        if len(axes) not in (1, 2) or not bits:
            _error("UNSUPPORTED_SCRIPT_PROFILE", "Complex 1D/2D input required", token, source)
        axis = axes[-1]
        if name in ("EM", "SP"):
            if (axis.domain != "time" or axis.acquired_size != axis.size or
                    axis.dwell_seconds is None or not math.isclose(axis.step, axis.dwell_seconds,
                    rel_tol=1e-12, abs_tol=0)):
                _error("UNSUPPORTED_SCRIPT_PROFILE", "Full recorded time grid required",
                       token, source)
        if name == "EM":
            lb = opts.get("-lb", 0.0)
            if lb < 0:
                _error("INVALID_SCRIPT_VALUE", "Negative line broadening is unsupported", token, source)
            op = {"op":"window", "axis":axis.id, "kind":"exponential",
                  "lb_hz":lb, "first_point_scale":opts.get("-c", 1.0)}
        elif name == "SP":
            off, end, power = opts.get("-off", 0.0), opts.get("-end", 1.0), opts.get("-pow", 1.0)
            if not 0 <= off <= end <= 1 or power <= 0 or axis.size < 2:
                _error("INVALID_SCRIPT_VALUE", "Invalid full-grid sine window", token, source)
            op = {"op":"window", "axis":axis.id, "kind":"sine", "off":off,
                  "end":end, "power":power, "first_point_scale":opts.get("-c", 1.0)}
        elif name == "ZF":
            value = opts.get("-size")
            if value is None or not value.is_integer() or value < axis.size:
                _error("INVALID_SCRIPT_VALUE", "ZF needs explicit final size", token, source)
            op = {"op":"zero_fill", "axis":axis.id, "size":int(value)}
        elif name == "PS":
            op = {"op":"phase", "axis":axis.id, "p0":0.0, "p1":0.0, "pivot":0.0}
        else:
            _error("UNSUPPORTED_SCRIPT_FUNCTION", "Unsupported function", token, source)
        try:
            axes, bits, metadata, _ = _transition(axes, bits, metadata, header.provenance, op)
        except NMRNextError as exc:
            _error(exc.code, str(exc), token, source)
        operations.append(op)
        provenance.append({"source": source, "line": stage["line"],
                           "column": stage["column"], "function": name,
                           "operation_index": len(operations) - 1})
    return {"schema_version":"1", "operations": operations,
            "source_sha256": parsed["source_sha256"], "source_name": source,
            "input_role":"@item.input", "output_role":"@item.output",
            "input_literal":parsed["input_literal"], "output_literal":parsed["output_literal"],
            "effective_input":str(origin), "effective_output":str(target),
            "overwrite_requested":parsed["overwrite_requested"], "provenance":provenance}
