#!/usr/bin/env python3
"""Install a checked SpinFlow release wheel using only the Python standard library.

Consumer target: Ubuntu 24.04 x86_64, ordinary CPython 3.12 / 3.13.
This script never builds source or changes system packages or shell settings.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import urllib.error
import urllib.request

DEFAULT_VERSION = "0.3.0b2"
REPOSITORY = "ben-shin/spinflow"
MARKER = ".spinflow-install.json"


class InstallError(RuntimeError):
    """An actionable consumer-install failure."""


def release_version(value):
    value = value.removeprefix("v")
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:(?:a|b|rc)[0-9]+)?", value):
        raise InstallError("Choose an explicit version such as 0.3.0b1; latest and source refs are unsupported.")
    return value


def runtime_tag():
    message = "Release wheels require Ubuntu 24.04 x86_64 and CPython 3.12 or 3.13 (standard GIL build)."
    if platform.system() != "Linux" or platform.machine().lower() not in ("x86_64", "amd64"):
        raise InstallError(message)
    if platform.python_implementation() != "CPython" or sys.version_info[:2] not in ((3, 12), (3, 13)):
        raise InstallError(message)
    if sysconfig.get_config_var("Py_GIL_DISABLED") or getattr(sys, "abiflags", ""):
        raise InstallError(message)
    try:
        distro = platform.freedesktop_os_release()
    except OSError as exc:
        raise InstallError(message) from exc
    if distro.get("ID") != "ubuntu" or distro.get("VERSION_ID") != "24.04":
        raise InstallError(message + " Other Linux source builds use the developer build guide.")
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def check_prefix(prefix, profile):
    if prefix.is_symlink():
        raise InstallError(f"Refusing a symlink installation prefix: {prefix}")
    if not prefix.exists():
        return None
    if not prefix.is_dir():
        raise InstallError(f"Installation prefix is not a directory: {prefix}")
    marker = prefix / MARKER
    if not marker.exists():
        if any(prefix.iterdir()):
            raise InstallError(f"Refusing unrelated files in an unowned prefix: {prefix}. Choose a new directory.")
        return None
    if marker.is_symlink():
        raise InstallError(f"Refusing a symlink ownership marker: {marker}")
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise InstallError(f"Cannot read installation ownership marker: {marker}") from exc
    if not isinstance(record, dict) or record.get("installer") != "spinflow-release-v1":
        raise InstallError(f"Prefix is not owned by the SpinFlow release installer: {prefix}")
    if any(record.get(key) != value for key, value in profile.items()):
        raise InstallError("Existing installation profile differs (version, Python or prefix). Choose a new --prefix.")
    if not isinstance(record.get("extras"), list) or any(extra not in ("gui", "plot") for extra in record["extras"]):
        raise InstallError("Existing installation ownership marker has an invalid extras profile.")
    return record


def lock_path(prefix):
    return prefix.parent / f".{prefix.name}.spinflow-install.lock"


@contextmanager
def locked_prefix(prefix):
    prefix.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path(prefix)
    try:
        stream = lock.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise InstallError(f"Another install owns {lock}. If an earlier process crashed, remove only this lock after checking it stopped.") from exc
    try:
        with stream:
            stream.write(f"SpinFlow installer PID {os.getpid()}\n")
        yield
    finally:
        lock.unlink()


def read_checksums(path):
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([a-fA-F0-9]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match or match[2] in result:
            raise InstallError("Release SHA256SUMS is malformed or has duplicate filenames.")
        result[match[2]] = match[1].lower()
    return result


def fetch_asset(name, destination, version, asset_dir):
    if asset_dir is not None:
        source = asset_dir / name
        if not source.is_file():
            raise InstallError(f"Required release asset is missing: {source}")
        shutil.copyfile(source, destination)
        return
    url = f"https://github.com/{REPOSITORY}/releases/download/v{version}/{name}"
    print(f"Downloading {name}", flush=True)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "SpinFlow-release-installer"})
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
            shutil.copyfileobj(response, output)
    except (urllib.error.URLError, OSError) as exc:
        raise InstallError(f"Cannot download {url}: {exc}. Check the version and connection; no source build was attempted.") from exc


def run(command, **kwargs):
    return subprocess.run([str(part) for part in command], check=True, **kwargs)


def verify_environment(prefix, version, extras):
    python = prefix / "bin" / "python"
    run([python, "-I", "-m", "pip", "--isolated", "check"])
    # Fail if imports come from a checkout, the native module is missing, or a
    # different package version is installed. -I excludes the current directory.
    probe = """import importlib.metadata as m, json, pathlib, sys
import nmrnext, nmrnext._native as native
root = pathlib.Path(sys.prefix).resolve()
assert all(pathlib.Path(p).resolve().is_relative_to(root) for p in (nmrnext.__file__, native.__file__))
assert m.version('spinflow') == sys.argv[1] == nmrnext.__version__ == native.__version__
for name in sys.argv[2:]:
    __import__({'gui': 'PySide6', 'plot': 'matplotlib'}[name])
"""
    run([python, "-I", "-c", probe, version, *extras])
    doctor = run([python, "-I", "-m", "nmrnext", "doctor"], capture_output=True, text=True)
    report = json.loads(doctor.stdout)
    if report.get("version") != version or report.get("backends", {}).get("native", {}).get("available") is not True:
        raise InstallError("Installed doctor did not confirm the expected version and native backend.")


def install(version, prefix, extras, asset_dir):
    version = release_version(version)
    tag = runtime_tag()
    extras = sorted(set(extras))
    if any(extra not in ("gui", "plot") for extra in extras):
        raise InstallError("Only gui and plot extras are supported.")
    prefix = Path(prefix).expanduser().absolute()
    profile = {"version": version, "python": tag, "prefix": str(prefix)}
    check_prefix(prefix, profile)
    wheel_name = f"spinflow-{version}-{tag}-{tag}-linux_x86_64.whl"
    with tempfile.TemporaryDirectory(prefix="spinflow-release-") as temporary:
        cache = Path(temporary)
        checksums = cache / "SHA256SUMS"
        fetch_asset("SHA256SUMS", checksums, version, asset_dir)
        expected = read_checksums(checksums).get(wheel_name)
        if expected is None:
            raise InstallError(f"Release does not list the required binary wheel: {wheel_name}. No source build was attempted.")
        wheel = cache / wheel_name
        fetch_asset(wheel_name, wheel, version, asset_dir)
        with wheel.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise InstallError(f"SHA-256 mismatch for {wheel_name}; installation stopped before modifying the prefix.")
        with locked_prefix(prefix):
            previous = check_prefix(prefix, profile)
            retained = previous["extras"] if previous else []
            extras = sorted(set(extras) | set(retained))
            if previous and previous.get("complete") is True and set(extras) <= set(retained):
                verify_environment(prefix, version, extras)
                return prefix
            prefix.mkdir(parents=True, exist_ok=True)
            marker = prefix / MARKER
            record = {"installer": "spinflow-release-v1", **profile, "extras": extras, "complete": False}
            marker.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            python = prefix / "bin" / "python"
            # venv creates its interpreter before ensurepip. An interrupted
            # owned bootstrap must be completed even when bin/python exists.
            # Reusing venv without --clear preserves installed extras and files.
            if not previous or previous.get("complete") is not True or not python.is_file():
                run([sys.executable, "-I", "-m", "venv", prefix])
            requirement = str(wheel) + ("[" + ",".join(extras) + "]" if extras else "")
            run([python, "-I", "-m", "pip", "--isolated", "install", "--disable-pip-version-check", "--only-binary=:all:", requirement])
            verify_environment(prefix, version, extras)
            record["complete"] = True
            marker.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return prefix


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=DEFAULT_VERSION, help="Exact release version (default: %(default)s)")
    parser.add_argument("--prefix", type=Path, help="New isolated directory (default: ~/.local/share/spinflow/VERSION)")
    parser.add_argument("--gui", action="store_true", help="Include the optional Qt viewer")
    parser.add_argument("--plot", action="store_true", help="Include optional Matplotlib figure export")
    parser.add_argument("--asset-dir", type=Path, help="Use downloaded release assets from this directory; dependencies still need package downloads")
    args = parser.parse_args(argv)
    try:
        version = release_version(args.version)
        prefix = args.prefix or Path.home() / ".local" / "share" / "spinflow" / version
        extras = [name for name in ("gui", "plot") if getattr(args, name)]
        installed = install(version, prefix, extras, args.asset_dir)
    except (InstallError, OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"SpinFlow installation stopped: {exc}", file=sys.stderr)
        print("Existing files were retained. For an interrupted owned install, retry the same command. Python needs its venv/ensurepip support installed.", file=sys.stderr)
        return 1
    command = shlex.quote(str(installed / "bin" / "spinflow"))
    print(f"SpinFlow {version} is ready (experimental). No activation is required.\n{command} doctor\n{command} first-spectrum ./my-first-spectrum")
    if args.gui:
        print(f"{command} gui")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
