"""Data-only NMRPipe command intake, through its public parser/compiler seam."""
from pathlib import Path
import hashlib
import json

import pytest


ROOT = Path(__file__).resolve().parents[1]


def api():
    import nmrnext
    from import_origin_support import assert_package_origin
    assert_package_origin(nmrnext.__file__, __file__)
    from nmrnext.pipe_script import parse_pipe_script, compile_pipe_script
    return parse_pipe_script, compile_pipe_script


def test_literal_em_sp_and_zf_preserve_order_and_source():
    parse, _ = api()
    script = ("nmrPipe -in input.nmrh5 \\\n"
              "| nmrPipe -fn EM -lb 1.5 -c 0.5 \\\n"
              "| nmrPipe -fn SP -off 0.5 -end 0.95 -pow 2 -c 1 \\\n"
              "| nmrPipe -fn ZF -size 16 -out output.nmrh5 -ov\n")
    result = parse(script, source_name="process.com")
    assert result["source_sha256"] == hashlib.sha256(script.encode()).hexdigest()
    assert result["source_name"] == "process.com"
    assert result["input_literal"] == "input.nmrh5"
    assert result["output_literal"] == "output.nmrh5"
    assert result["overwrite_requested"] is True
    assert [(s["name"], s["line"], s["column"]) for s in result["stages"]] == [
        ("EM", 2, 11), ("SP", 3, 11), ("ZF", 4, 11)]


def test_guarded_full_grid_window_and_explicit_fill_compile_to_existing_operations(tmp_path):
    parse, compile_script = api()
    from nmrnext.model import Axis, DatasetHeader
    header = DatasetHeader([Axis("t", 8, step=0.001, dwell_seconds=0.001)],
                           ["t"], dtype="float32")
    script = "nmrPipe -in input.nmrh5 | nmrPipe -fn EM -lb 2 -c 0.5 | nmrPipe -fn ZF -size 16 -out output.nmrh5"
    parsed = parse(script, source_name="process.com")
    result = compile_script(parsed, header, input_path=tmp_path/"input.nmrh5",
                            output_path=tmp_path/"output.nmrh5")
    assert result["operations"] == [
        {"op":"window", "axis":"t", "kind":"exponential", "lb_hz":2.0,
         "first_point_scale":0.5},
        {"op":"zero_fill", "axis":"t", "size":16}]
    assert result["input_role"] == "@item.input"
    assert result["output_role"] == "@item.output"
    assert result["input_literal"] == "input.nmrh5"
    assert result["output_literal"] == "output.nmrh5"
    assert result["effective_input"] == str(tmp_path/"input.nmrh5")
    assert result["effective_output"] == str(tmp_path/"output.nmrh5")
    assert len(result["provenance"]) == 2


@pytest.mark.parametrize("injected,code,token", [
    ("-di", "UNSUPPORTED_SCRIPT_FLAG", "-di"),
    ("-hdr", "UNSUPPORTED_SCRIPT_FLAG", "-hdr"),
    ("-bogus 7", "UNSUPPORTED_SCRIPT_FLAG", "-bogus"),
    ("-lb $HOME", "UNSAFE_SCRIPT", "$HOME"),
    ("-lb `echo 1`", "UNSAFE_SCRIPT", "`echo"),
    ("-lb $(echo)", "UNSAFE_SCRIPT", "$(echo)"),
    ("-lb 1; echo", "UNSAFE_SCRIPT", "1;"),
    ("-lb 1 > x", "UNSAFE_SCRIPT", ">"),
    ("-lb '1'", "UNSAFE_SCRIPT", "'1'"),
])
def test_unsupported_or_shell_token_refuses_at_source_before_io(injected, code, token, tmp_path):
    parse, _ = api()
    text = f"nmrPipe -in input.nmrh5 | nmrPipe -fn EM {injected} -out output.nmrh5"
    with pytest.raises(ValueError) as caught:
        parse(text, source_name="unsafe.com")
    assert caught.value.code == code
    assert caught.value.details["line"] == 1
    assert caught.value.details["token"] == token
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("script,code", [
    ("var2pipe -in input.nmrh5 | nmrPipe -fn EM -lb 1 -out output.nmrh5",
     "UNSUPPORTED_SCRIPT_COMMAND"),
    ("nmrPipe -in input.nmrh5 | nmrPipe -fn GM -out output.nmrh5",
     "UNSUPPORTED_SCRIPT_FUNCTION"),
    ("nmrPipe -in input.nmrh5 | nmrPipe -fn ZF -auto -out output.nmrh5",
     "UNSUPPORTED_SCRIPT_FLAG"),
    ("nmrPipe -in input.nmrh5 | nmrPipe -fn EM -lb 1 -out output.ft2",
     "UNSUPPORTED_SCRIPT_OUTPUT"),
    ("nmrPipe -in input.nmrh5 | nmrPipe -in other.nmrh5 -fn EM -out output.nmrh5",
     "INVALID_SCRIPT_BINDING"),
    ("nmrPipe -in input.nmrh5 | nmrPipe -fn EM -lb 1 -out *.nmrh5",
     "UNSAFE_SCRIPT_PATH"),
])
def test_unknown_function_mode_or_binding_refuses(script, code):
    parse, _ = api()
    with pytest.raises(ValueError) as caught:
        parse(script, source_name="blocked.com")
    assert caught.value.code == code
    assert caught.value.details["source"] == "blocked.com"
    assert caught.value.details["line"] == 1


@pytest.mark.parametrize("operation", ["FT", "TP", "PS -p0 45 -p1 0"])
def test_unqualified_ft_nonzero_phase_and_transpose_do_not_emit_operations(operation, tmp_path):
    parse, compile_script = api()
    from nmrnext.model import Axis, DatasetHeader
    header = DatasetHeader([Axis("t", 8, step=0.001, dwell_seconds=0.001)],
                           ["t"], dtype="float32")
    parsed = parse(f"nmrPipe -in input.nmrh5 | nmrPipe -fn {operation} -out output.nmrh5",
                   source_name="gated.com")
    with pytest.raises(ValueError) as caught:
        compile_script(parsed, header, input_path=tmp_path/"input.nmrh5",
                       output_path=tmp_path/"output.nmrh5")
    assert caught.value.code == "UNQUALIFIED_SCRIPT_FUNCTION"
    assert not (tmp_path/"output.nmrh5").exists()


def test_overwrite_intent_never_replaces_existing_output(tmp_path):
    parse, compile_script = api()
    from nmrnext.model import Axis, DatasetHeader
    header = DatasetHeader([Axis("t", 8, step=0.001, dwell_seconds=0.001)], ["t"])
    parsed = parse("nmrPipe -in input.nmrh5 | nmrPipe -fn EM -lb 1 -out output.nmrh5 -ov",
                   source_name="fresh.com")
    target = tmp_path/"output.nmrh5"
    target.write_bytes(b"foreign")
    with pytest.raises(ValueError) as caught:
        compile_script(parsed, header, input_path=tmp_path/"input.nmrh5", output_path=target)
    assert caught.value.code == "OUTPUT_EXISTS"
    assert target.read_bytes() == b"foreign"


def test_sine_window_defaults_and_explicit_values_preserve_existing_operation_names(tmp_path):
    parse, compile_script = api()
    from nmrnext.model import Axis, DatasetHeader
    header = DatasetHeader([Axis("t", 8, step=0.001, dwell_seconds=0.001)], ["t"])
    parsed = parse("nmrPipe -in input.nmrh5 | nmrPipe -fn SP -off 0.5 -end 0.95 -pow 2 -c 0.5 -out result.nmrh5",
                   source_name="sine.com")
    result = compile_script(parsed, header, input_path=tmp_path/"in.nmrh5",
                            output_path=tmp_path/"new.nmrh5")
    assert result["operations"] == [{"op":"window", "axis":"t", "kind":"sine",
                                     "off":0.5, "end":0.95, "power":2.0,
                                     "first_point_scale":0.5}]
    assert result["provenance"][0]["function"] == "SP"
    assert result["output_literal"] == "result.nmrh5"
    assert result["effective_output"] == str(tmp_path/"new.nmrh5")


def test_zero_phase_only_on_existing_complex_frequency_axis(tmp_path):
    parse, compile_script = api()
    from nmrnext.model import Axis, DatasetHeader
    header = DatasetHeader([Axis("f", 8, domain="frequency", unit="Hz", step=100.)], ["f"])
    parsed = parse("nmrPipe -in input.nmrh5 | nmrPipe -fn PS -p0 0 -p1 0 -out result.nmrh5",
                   source_name="identity.com")
    result = compile_script(parsed, header, input_path=tmp_path/"in.nmrh5",
                            output_path=tmp_path/"new.nmrh5")
    assert result["operations"] == [{"op":"phase", "axis":"f", "p0":0.0,
                                     "p1":0.0, "pivot":0.0}]


@pytest.mark.parametrize("operation,header_kwargs,code", [
    ("EM -lb -1", {}, "INVALID_SCRIPT_VALUE"),
    ("EM -lb 1", {"dwell_seconds":None}, "UNSUPPORTED_SCRIPT_PROFILE"),
    ("EM -lb 1", {"dwell_seconds":0.002}, "UNSUPPORTED_SCRIPT_PROFILE"),
    ("SP -off 0.8 -end 0.2", {}, "INVALID_SCRIPT_VALUE"),
    ("SP -pow 0", {}, "INVALID_SCRIPT_VALUE"),
    ("ZF -size 4", {}, "INVALID_SCRIPT_VALUE"),
    ("ZF -size 8.5", {}, "INVALID_SCRIPT_VALUE"),
])
def test_unrepresentable_window_or_fill_refuses_before_output(operation, header_kwargs, code, tmp_path):
    parse, compile_script = api()
    from nmrnext.model import Axis, DatasetHeader
    header = DatasetHeader([Axis("t", 8, step=0.001,
                                 **({"dwell_seconds":0.001} | header_kwargs))], ["t"])
    parsed = parse(f"nmrPipe -in input.nmrh5 | nmrPipe -fn {operation} -out result.nmrh5",
                   source_name="guard.com")
    with pytest.raises(ValueError) as caught:
        compile_script(parsed, header, input_path=tmp_path/"in.nmrh5",
                       output_path=tmp_path/"new.nmrh5")
    assert caught.value.code == code
    assert not (tmp_path/"new.nmrh5").exists()


@pytest.mark.parametrize("path", ["../other.nmrh5", "C:/other.nmrh5", "/tmp/other.nmrh5",
                                        "sub/*.nmrh5", "${INPUT}"])
def test_script_paths_are_literals_not_external_bindings(path):
    parse, _ = api()
    with pytest.raises(ValueError) as caught:
        parse(f"nmrPipe -in {path} | nmrPipe -fn EM -lb 1 -out result.nmrh5",
              source_name="paths.com")
    assert caught.value.code in ("UNSAFE_SCRIPT_PATH", "UNSAFE_SCRIPT")


def test_compiler_does_not_trust_mutated_parsed_flags_or_emit_discard(tmp_path):
    parse, compile_script = api()
    from nmrnext.model import Axis, DatasetHeader
    header = DatasetHeader([Axis("f", 8, domain="frequency", unit="Hz", step=100.)], ["f"])
    parsed = parse("nmrPipe -in input.nmrh5 | nmrPipe -fn PS -p0 0 -out result.nmrh5",
                   source_name="mutated.com")
    parsed["stages"][0]["options"]["-di"] = 1
    with pytest.raises(ValueError) as caught:
        compile_script(parsed, header, input_path=tmp_path/"in.nmrh5",
                       output_path=tmp_path/"new.nmrh5")
    assert caught.value.code == "INVALID_SCRIPT_INTEGRITY"
    assert not (tmp_path/"new.nmrh5").exists()


def test_single_stage_source_location_points_to_actual_fn_not_command_start():
    parse, _ = api()
    script = "nmrPipe -in input.nmrh5 -fn EM -lb 1 -out output.nmrh5"
    parsed = parse(script, source_name="single.com")
    assert len(parsed["stages"]) == 1
    assert parsed["stages"][0]["line"] == 1
    assert parsed["stages"][0]["column"] == script.index("-fn") + 1


@pytest.mark.parametrize("mutation", ["option", "empty", "source_hash", "location"])
def test_compiler_rejects_changed_or_omitted_ir_with_original_script_identity(mutation, tmp_path):
    parse, compile_script = api()
    from nmrnext.model import Axis, DatasetHeader
    text = "nmrPipe -in input.nmrh5 | nmrPipe -fn EM -lb 1 -out result.nmrh5"
    parsed = parse(text, source_name="original.com")
    header = DatasetHeader([Axis("t", 8, step=.001, dwell_seconds=.001)], ["t"])
    if mutation == "option":
        parsed["stages"][0]["options"]["-lb"] = 2.0
    elif mutation == "empty":
        parsed["stages"] = []
    elif mutation == "source_hash":
        parsed["source_sha256"] = "0" * 64
    else:
        parsed["stages"][0]["column"] += 1
    with pytest.raises(ValueError) as caught:
        compile_script(parsed, header, input_path=tmp_path/"input.nmrh5",
                       output_path=tmp_path/"result.nmrh5")
    assert caught.value.code == "INVALID_SCRIPT_INTEGRITY"
    assert not (tmp_path/"result.nmrh5").exists()


def test_json_roundtrip_of_untouched_parsed_script_keeps_source_and_operation(tmp_path):
    parse, compile_script = api()
    from nmrnext.model import Axis, DatasetHeader
    text = "nmrPipe -in input.nmrh5 | nmrPipe -fn EM -lb 1 -out result.nmrh5"
    record = json.loads(json.dumps(parse(text, source_name="original.com")))
    header = DatasetHeader([Axis("t", 8, step=.001, dwell_seconds=.001)], ["t"])
    result = compile_script(record, header, input_path=tmp_path/"input.nmrh5",
                            output_path=tmp_path/"result.nmrh5")
    assert result["source_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert result["operations"] == [{"op":"window", "axis":"t", "kind":"exponential",
                                     "lb_hz":1.0, "first_point_scale":1.0}]


def test_2049th_token_refuses_early_without_scanning_or_appending_remaining_tokens(monkeypatch):
    parse, _ = api()
    import nmrnext.pipe_script as module
    original = module._TOKEN
    seen = 0

    class CountMatches:
        def finditer(self, value):
            nonlocal seen
            for match in original.finditer(value):
                seen += 1
                yield match

    monkeypatch.setattr(module, "_TOKEN", CountMatches())
    text = "x " * 4096
    with pytest.raises(ValueError) as caught:
        parse(text, source_name="long.com")
    assert caught.value.code == "INVALID_SCRIPT"
    assert seen == 2049


def test_token_cap_boundary_and_utf8_byte_cap_are_separate():
    parse, _ = api()
    with pytest.raises(ValueError) as at_limit:
        parse("x " * 2048, source_name="limit.com")
    assert at_limit.value.code == "UNSUPPORTED_SCRIPT_COMMAND"
    with pytest.raises(ValueError) as over_limit:
        parse("x " * 2049, source_name="limit.com")
    assert over_limit.value.code == "INVALID_SCRIPT"
    with pytest.raises(ValueError) as over_bytes:
        parse("x" * (1024 * 1024 + 1), source_name="bytes.com")
    assert over_bytes.value.code == "INVALID_SCRIPT"

