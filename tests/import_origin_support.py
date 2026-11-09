"""Test-only checkout or reviewed installed-wheel origin assertion."""
import hashlib
import importlib.metadata
from pathlib import Path
import sysconfig
import zipfile


REVIEWED_WHEEL_SHA256 = "836b50d4dcda5f5b543256a20aae5edaa46c7172de37da8da108f4e333058cba"
REVIEWED_WHEEL_NAME = "spinflow-0.3.0b3-cp312-cp312-linux_x86_64.whl"
_authority = None


def _require(condition, message):
    if not condition:
        raise AssertionError(message)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify(authority):
    wheel, expected, pins = authority
    _require(wheel.name == REVIEWED_WHEEL_NAME, "Wrong reviewed wheel filename")
    _require(expected == REVIEWED_WHEEL_SHA256 and _sha256(wheel) == expected,
             "Reviewed wheel SHA differs")
    purelib = Path(sysconfig.get_path("purelib")).resolve(strict=True)
    package = purelib / "nmrnext"
    _require(package.is_dir() and package.resolve(strict=True) == package,
             "Installed package is outside canonical purelib")
    distribution = importlib.metadata.distribution("spinflow")
    _require(distribution.metadata["Name"].lower() == "spinflow", "Wrong distribution")
    _require(Path(distribution.locate_file("nmrnext")).resolve(strict=True) == package,
             "Distribution package location differs")
    _require(len(pins) == 31, "Incomplete installed product witness")
    members = set()
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        for original, length, digest in pins:
            path = Path(original)
            _require(path.is_absolute() and path.is_file() and not path.is_symlink(),
                     "Missing or noncanonical product file")
            _require(path.resolve(strict=True) == path and path.is_relative_to(package),
                     "Product file is outside installed package")
            member = "nmrnext/" + path.relative_to(package).as_posix()
            _require(member not in members and names.count(member) == 1,
                     "Duplicate or missing wheel member")
            members.add(member)
            _require(path.stat().st_size == length and _sha256(path) == digest,
                     "Installed product file differs from witness")
            _require(hashlib.sha256(archive.read(member)).hexdigest() == digest,
                     "Wheel member differs from witness")
        _require({name for name in names if name.startswith("nmrnext/") and not name.endswith("/")} == members,
                 "Wheel product inventory differs from 31-file witness")
    return package


def register_installed_origin(wheel_path, expected_wheel_sha256, installed_pins):
    """Reviewed launcher registers an immutable copied wheel/31-file witness."""
    global _authority
    _require(_authority is None, "Installed origin authority is already registered")
    _require(isinstance(installed_pins, (list, tuple)), "Installed witness must be a sequence")
    pins = []
    for row in installed_pins:
        _require(isinstance(row, dict) and set(row) == {"path", "bytes", "sha256"},
                 "Malformed installed product witness row")
        _require(isinstance(row["path"], str) and type(row["bytes"]) is int and row["bytes"] >= 0,
                 "Malformed installed path or byte count")
        _require(isinstance(row["sha256"], str) and len(row["sha256"]) == 64,
                 "Malformed installed product digest")
        pins.append((row["path"], row["bytes"], row["sha256"]))
    candidate = (Path(wheel_path).resolve(strict=True), expected_wheel_sha256, tuple(pins))
    _verify(candidate)
    _authority = candidate


def assert_package_origin(module_file, test_file, *, mode=None):
    """Default remains exact checkout; installed requires registered authority."""
    if mode is None:
        mode = "installed" if _authority is not None else "source"
    _require(mode in ("source", "installed"), "Unknown test origin mode")
    observed = Path(module_file).resolve(strict=True).parent
    if mode == "source":
        checkout = Path(test_file).resolve(strict=True).parents[1] / "src" / "nmrnext"
        _require(observed == checkout, "Imported package is not this checkout")
        return
    _require(_authority is not None, "Installed origin requires reviewed launcher authority")
    _require(observed == _verify(_authority), "Imported package is not the reviewed installed wheel")
