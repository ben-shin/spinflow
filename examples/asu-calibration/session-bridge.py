"""UNEXECUTED installed-only acceptance appendix, not a new CLI feature."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys

import nmrnext
import nmrnext.analysis
import nmrnext.io
import nmrnext.operations
import nmrnext.session
from nmrnext.analysis import dataset_digest
from nmrnext.io import read_dataset
from nmrnext.session import load_session, save_session

parser = argparse.ArgumentParser()
parser.add_argument("--diagnostic-only", action="store_true")
args = parser.parse_args()
prefix = Path(sys.prefix).resolve()
assert sys.flags.isolated == 1 and sys.dont_write_bytecode
origins = {}
for module in (nmrnext, nmrnext.analysis, nmrnext.io, nmrnext.operations, nmrnext.session):
    path = Path(module.__file__).resolve()
    assert path.is_relative_to(prefix), "Installed-only prefix required"
    origins[module.__name__] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
assert nmrnext.__version__ == importlib.metadata.version("spinflow") == "0.3.0b3"
suffix = "-diagnostic" if args.diagnostic_only else ""
with Path(f"outputs/session-origin{suffix}.json").open("x", encoding="utf-8") as stream:
    json.dump({"prefix": str(prefix), "python": sys.executable, "version": sys.version,
               "isolated": True, "bytecode_disabled": True, "module_origins": origins,
               "versions": {name: importlib.metadata.version(name) for name in
                            ("spinflow", "numpy", "scipy", "h5py", "nmrglue")}}, stream, indent=2)

recipe = json.loads(Path("preprocess.recipe.json").read_text())
operations = recipe["operations"]
reports = {}
result_file = "outputs/unphased.nmrh5"
if not args.diagnostic_only:
    phase = json.loads(Path("outputs/phase.json").read_text())
    analysis = json.loads(Path("outputs/analysis.json").read_text())
    operations = operations + [phase["operation"]]
    reports = {"autophase": phase, "peaks": analysis["peaks"], "integrate": analysis["integrals"]}
    result_file = "outputs/phased.nmrh5"
source = read_dataset("inputs/acq", format="bruker-raw-1d", max_bytes=67108864)
applied = read_dataset(result_file, max_bytes=67108864)
save_session(source, operations, f"session{suffix}", backend="native", memory_bytes=67108864,
             reports=reports, result=applied, source_name="ASU provisional engineering illustration" + suffix)
reopened = load_session(f"session{suffix}", max_bytes=67108864, replay=True)
assert dataset_digest(reopened["current"]) == dataset_digest(applied)
assert [a.to_dict() for a in reopened["current"].axes] == [a.to_dict() for a in applied.axes]
assert reopened["current"].to_metadata() == applied.to_metadata()
shutil.copytree(f"session{suffix}", f"session-cli{suffix}")
with Path(f"outputs/session-check{suffix}.json").open("x", encoding="utf-8") as stream:
    json.dump({"result_digest": reopened["result_digest"], "exact_axes_metadata": True,
               "operations": reopened["operations"], "reports": sorted(reopened["reports"]),
               "diagnostic_only": args.diagnostic_only, "scientific_acceptance": False}, stream, indent=2)
