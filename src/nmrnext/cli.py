"""Headless CLI. Structured results on stdout, structured failures on stderr."""
import argparse
import importlib.metadata
import json
import math
from pathlib import Path
import re
import sys
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


def _json_file(path):
    if path.suffix.lower() not in (".json",):
        raise NMRNextError("UNSUPPORTED_RECIPE_FORMAT","Recipes must be JSON; YAML/script execution is not supported")
    try:
        if path.stat().st_size > 1024*1024:
            raise NMRNextError("RECIPE_TOO_LARGE","Recipe exceeds 1 MiB")
        def pairs(items):
            result={}
            for key,value in items:
                if key in result:
                    raise NMRNextError("INVALID_RECIPE",f"Duplicate JSON key {key!r}")
                result[key]=value
            return result
        return json.loads(path.read_text(encoding="utf-8"),object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(NMRNextError("INVALID_RECIPE",f"Nonfinite JSON number {value}")))
    except json.JSONDecodeError as exc:
        raise NMRNextError("INVALID_RECIPE",f"JSON error at line {exc.lineno}: {exc.msg}") from exc


def _load_recipe(args):
    override_memory=_memory(args.memory)
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


def _dispatch(args):
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
    # Validate recipe schema before importing codecs or reading sample arrays.
    config=_load_recipe(args) if args.command in ("plan","run") else None
    from .io import capabilities,export_preflight,inspect_dataset,read_dataset,write_dataset
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
        if args.command == "run" and target.exists():
            raise NMRNextError("OUTPUT_EXISTS",f"Output already exists: {target}; choose a new output path")
        dataset=read_dataset(source,format=recipe.get("input_format"),max_bytes=memory)
        plan=plan_operations(dataset,recipe["operations"],backend=backend,threads=threads,memory_bytes=memory)
        plan.update({"input":str(source),"output":str(target),"output_format":_format(target,recipe.get("output_format"))})
        if args.command == "plan": return plan,0
        started=time.perf_counter()
        result=run_operations(dataset,recipe["operations"],backend=backend,threads=threads,memory_bytes=memory)
        report=write_dataset(result,target,format=plan["output_format"],allow_losses=recipe.get("allow_losses",()))
        return {"result":report,"backend":plan["backend"],"operations":len(plan["steps"]),
                "elapsed_processing_and_write_seconds":time.perf_counter()-started,
                "estimated_working_bytes":plan["estimated_working_bytes"],"memory_scope":plan["memory_scope"]},0
    raise NMRNextError("UNKNOWN_COMMAND","Unknown command")


def _parser():
    parser=argparse.ArgumentParser(prog="nmrnext",description="Experimental reproducible NMR processing; synthetic and declared processed profiles")
    parser.add_argument("--version",action="version",version=__version__)
    sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("doctor",help="Report runtime and backend availability")
    sub.add_parser("formats",help="Report exact read/write profiles")
    sub.add_parser("gui",help="Open the optional spectrum workbench")
    demo=sub.add_parser("demo",help="Generate clearly synthetic fully sampled data")
    demo.add_argument("output");demo.add_argument("--dimensions",type=int,default=2)
    demo.add_argument("--points",type=int,nargs="+");demo.add_argument("--dtype",choices=["float32","float64"],default="float64")
    demo.add_argument("--format")
    inspect=sub.add_parser("inspect",help="Inspect metadata and storage")
    inspect.add_argument("input");inspect.add_argument("--format");inspect.add_argument("--json",action="store_true");inspect.add_argument("--memory")
    for command in ("plan","run"):
        p=sub.add_parser(command,help="Validate" if command=="plan" else "Execute a JSON recipe")
        p.add_argument("recipe");p.add_argument("--backend",choices=["native","numpy"]);p.add_argument("--threads",type=int);p.add_argument("--memory")
        if command=="plan":p.add_argument("--explain",action="store_true",help="JSON plan always includes explanations")
    conv=sub.add_parser("convert",help="Preflight or convert with explicit loss permission")
    conv.add_argument("input");conv.add_argument("output");conv.add_argument("--input-format");conv.add_argument("--format")
    conv.add_argument("--dry-run",action="store_true");conv.add_argument("--allow-loss",action="append",default=[]);conv.add_argument("--memory")
    compare=sub.add_parser("compare",help="Compare coordinates and numeric arrays without alignment")
    compare.add_argument("candidate");compare.add_argument("reference");compare.add_argument("--json",action="store_true")
    compare.add_argument("--rtol",type=float,default=1e-5);compare.add_argument("--atol",type=float,default=1e-5);compare.add_argument("--memory")
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
