"""Exercise an installed SpinFlow wheel or sdist from outside the checkout."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np

import nmrnext
from nmrnext import _native


def _inside(path: str, root: Path) -> bool:
    return Path(path).resolve().is_relative_to(root.resolve())


def _cli(*args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-I", "-m", "nmrnext", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(proc.stdout)


def main() -> None:
    prefix = Path(sys.prefix)
    assert _inside(nmrnext.__file__, prefix), nmrnext.__file__
    assert _inside(_native.__file__, prefix), _native.__file__

    impulse = np.array([1, 0, 0, 0], dtype=np.complex128)
    transformed = _native.fft_complex(impulse, 0)
    np.testing.assert_allclose(transformed, np.ones(4), atol=1e-12, rtol=0)

    doctor = _cli("doctor")
    assert doctor["backends"]["native"]["available"] is True, doctor

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        raw = root / "raw.h5"
        processed = root / "processed.h5"
        recipe = root / "recipe.json"
        recipe.write_text(
            json.dumps(
                {
                    "schema_version": "1",
                    "input": "raw.h5",
                    "output": "processed.h5",
                    "backend": "native",
                    "operations": [
                        {"op": "fft", "axis": "t2"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        _cli("demo", str(raw), "--dimensions", "1", "--points", "16")
        _cli("plan", str(recipe))
        _cli("run", str(recipe), "--backend", "native")
        _cli("inspect", str(processed), "--json")
        result = _cli("compare", str(processed), str(processed), "--json")
        assert result, "comparison returned no result"

    print("Installed native extension and CLI workflow passed")


if __name__ == "__main__":
    main()
