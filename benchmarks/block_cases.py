"""Derive honest acquired subcubes and a fixed operator-control recipe.

Run with the frozen baseline installation. Full inputs/recipes remain unchanged.
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path

import h5py

from nmrnext.io import read_dataset, write_dataset
from nmrnext.operations import run_operations

from block_prepare import record


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--native-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir()
    full = args.native_root
    derived = {}
    source = read_dataset(full / "wr41-4d.nmrh5", format="native", max_bytes=8 * 1024 ** 3)
    for name, stop in [("4d-quarter", 4), ("4d-half", 8)]:
        result = run_operations(source, [{"op": "crop", "axis": "a0", "start": 0, "stop": stop}],
                                backend="native", threads=1, memory_bytes=8 * 1024 ** 3)
        path = args.output / (name + ".nmrh5")
        write_dataset(result, path, format="native")
        derived[name] = {"input": record(path), "selection": {"a0": [0, stop]},
                         "relationship": "actual initial acquired time increments; every channel and all other axes retained; original acquired_size/provenance retained"}
        del result
    del source
    source = read_dataset(full / "wr41-3d.nmrh5", format="native", max_bytes=8 * 1024 ** 3)
    result = run_operations(source, [{"op": "crop", "axis": f"a{i}", "start": 0, "stop": stop}
                                     for i, stop in enumerate([12, 16, 128])],
                            backend="native", threads=1, memory_bytes=8 * 1024 ** 3)
    path = args.output / "3d-control.nmrh5"
    write_dataset(result, path, format="native")
    derived["3d-control"] = {"input": record(path), "selection": {"a0": [0, 12], "a1": [0, 16], "a2": [0, 128]},
                             "relationship": "actual acquired subcube, no repetition/interpolation; changed processing sampling extent is explicit in original crop provenance"}
    original = json.loads((full / "recipe-3d.json").read_text())["operations"]
    chain = [original[0], {"op": "zero_fill", "axis": "a2", "size": 256}, *original[1:],
             {"op": "reverse", "axis": "a0"}, {"op": "transpose", "order": ["a2", "a0", "a1"]},
             {"op": "crop", "axis": "a1", "start": 2, "stop": 14},
             {"op": "discard_imaginary", "axis": "a2", "version": "1"}]
    assert len(chain) == 14
    recipe = args.output / "recipe-3d-control.json"
    recipe.write_text(json.dumps({"operations": chain}, indent=2) + "\n", encoding="utf-8")
    cases = []
    for name, input_path, recipe_path, cap, coverage in [
        ("real-3d-full-64m", full / "wr41-3d.nmrh5", full / "recipe-3d.json", 64, "all acquired3D channels;9fixed stages"),
        ("real-4d-quarter-64m", args.output / "4d-quarter.nmrh5", full / "recipe-4d.json", 64, "first4 of16 genuine a0 time increments; all16channels"),
        ("real-4d-half-64m", args.output / "4d-half.nmrh5", full / "recipe-4d.json", 64, "first8 of16 genuine a0 time increments; all16channels"),
        ("real-4d-full-64m", full / "wr41-4d.nmrh5", full / "recipe-4d.json", 64, "all acquired4D channels;12fixed stages"),
        ("real-4d-full-128m", full / "wr41-4d.nmrh5", full / "recipe-4d.json", 128, "same full4D case, independently paired128MiB blockcap"),
        ("real-3d-control-64m", path, recipe, 64, "14stage actual acquiredsubcube operatorcontrol;directZF,frequencycrop,reverse,transpose,explicitoneaxisquadratureprojection"),
    ]:
        with h5py.File(input_path, "r") as h:
            shape, dtype = list(h["data"].shape), str(h["data"].dtype)
        cases.append({"name": name, "input": record(input_path), "recipe": record(recipe_path),
                      "shape": shape, "dtype": dtype, "block_memory_bytes": cap * 1024 ** 2,
                      "eager_memory_bytes": 8 * 1024 ** 3, "coverage": coverage})
    manifest = {"cases": cases, "derived": derived,
                "frozen_full_case_rule": "Original normalization/fullcase recipes remain unchanged from science-freeze-v1",
                "measurements": {"warmups_per_case_lane": 1, "measured_pairs": 7,
                                 "pair_order": ["BC", "CB", "BC", "CB", "BC", "CB", "BC"],
                                 "boundary": "fresh installed directCLI spawn through exit;wait4actualchildpeakRSS; unmodified product IO/hashes/finite/provenance included; independent comparisons outside timed process",
                                 "retention": "all observations, trialoutputbinaries and failed/preflight products retained; no outlier exclusions"},
                "science": {"full_processed_relative_max": 2e-5, "isolated_fft_relative_max": 2e-6,
                            "metadata_axes_history": "exact frozenbaseline equality", "absolute_ppm": "unknown; no fabricatedanchor; relativeHzgrids exact",
                            "prefix_audit": "all14prefix outputs of the acquired3Dcontrol outside timing; full final allchannel tensors for everypair",
                            "rois": "Allcomponents: origin corner [0:3 peraxis], terminalcorner[-3: peraxis], centered3binsperaxis, plus3strongest signed reference component0 bins with3binneighborhoods; coordinates/signs/indices directly compared, no alignment/rescaling/fitting"}}
    target = args.output / "cases.json"
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "manifest": record(target), "cases": len(cases)}))


if __name__ == "__main__":
    main()
