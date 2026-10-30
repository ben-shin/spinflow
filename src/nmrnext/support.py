"""Installed copy of the source support registry; status is not a release claim."""

from importlib import resources
import json
from pathlib import Path


def support_capabilities():
    """Return the declared profile/status registry as a fresh JSON value.

    Source checkouts read the canonical file in ``compatibility``. Wheels
    include the identical file as a package resource.
    """
    installed = resources.files("nmrnext").joinpath("support.json")
    if installed.is_file():
        data = installed.read_text(encoding="utf-8")
    else:
        data = (Path(__file__).resolve().parents[2] / "compatibility" / "support.json").read_text(encoding="utf-8")
    return json.loads(data)
