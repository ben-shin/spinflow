"""Build evidence and aggregate honest Ubuntu-specific release assets.

This helper never creates tags, publishes releases, or changes remote state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def version():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checksums(directory):
    paths = sorted(path for path in directory.iterdir() if path.is_file() and path.name != "SHA256SUMS")
    (directory / "SHA256SUMS").write_text("".join(f"{sha256(path)}  {path.name}\n" for path in paths), encoding="utf-8")


def checked_run(command, **kwargs):
    return subprocess.run([str(part) for part in command], check=True, **kwargs)


def smoke(prefix, gui=False, plot=False):
    python = prefix / "bin" / "python"
    executable = prefix / "bin" / "spinflow"
    with tempfile.TemporaryDirectory(prefix="spinflow-installed-smoke-") as temporary:
        directory = Path(temporary)
        checked_run([python, "-I", ROOT / "tools" / "ci_smoke.py"], cwd=directory)
        checked_run([executable, "first-spectrum", directory / "first spectrum"], cwd=directory)
        # Exercise the installed console entry point, and its ordinary refusal
        # to overwrite the tutorial directory, without importing a source tree.
        refused = subprocess.run([str(executable), "first-spectrum", str(directory / "first spectrum")], cwd=directory, capture_output=True)
        if refused.returncode == 0:
            raise RuntimeError("Installed first-spectrum unexpectedly overwrote an existing directory")
        if plot:
            checked_run([python, "-I", "-c", "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as p; p.plot([0,1]); p.savefig('optional-plot.png'); p.close()"], cwd=directory)
        if gui:
            environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
            probe = "from PySide6.QtWidgets import QApplication; from PySide6.QtCore import QTimer; from nmrnext.cli import main; app=QApplication([]); QTimer.singleShot(250, app.closeAllWindows); raise SystemExit(main(['gui']))"
            checked_run([python, "-I", "-c", probe], cwd=directory, env=environment, timeout=60)


def record(output, prefixes, commit):
    probe = """import importlib.metadata as m, json, platform, sys
import nmrnext, nmrnext._native as native
print(json.dumps({'python': sys.version, 'python_tag': 'cp%d%d' % sys.version_info[:2],
'platform': platform.platform(), 'distribution': platform.freedesktop_os_release(),
'dependencies': {d.metadata['Name']: d.version for d in m.distributions()},
'package_version': nmrnext.__version__, 'native_version': native.__version__}))"""
    environments = []
    for prefix in prefixes:
        result = checked_run([prefix / "bin" / "python", "-I", "-c", probe], capture_output=True, text=True)
        environments.append(json.loads(result.stdout))
    if not environments or any(item["package_version"] != version() or item["native_version"] != version() for item in environments):
        raise ValueError("Installed evidence does not match the release version")
    tag = environments[0]["python_tag"]
    if any(item["python_tag"] != tag for item in environments):
        raise ValueError("One build record must describe a single Python ABI")
    payload = {"commit": commit, "version": version(), "python_tag": tag, "tested_environments": environments}
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def assemble(source, output, commit):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Release source must be an exact 40-character Git commit")
    release_version = version()
    required = [f"spinflow-{release_version}-{tag}-{tag}-linux_x86_64.whl" for tag in ("cp312", "cp313")]
    required.append(f"spinflow-{release_version}.tar.gz")
    inputs = []
    for name in required:
        matches = list(source.rglob(name))
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one release asset {name}; found {len(matches)}")
        inputs.append(matches[0])
    records = []
    for tag in ("cp312", "cp313"):
        matches = list(source.rglob(f"build-{tag}.json"))
        if len(matches) != 1:
            raise ValueError(f"Expected one build record for {tag}")
        item = json.loads(matches[0].read_text(encoding="utf-8"))
        if item.get("commit") != commit or item.get("version") != release_version or item.get("python_tag") != tag:
            raise ValueError(f"Build record for {tag} does not match the frozen source/version")
        records.append(item)
    output.mkdir(parents=True, exist_ok=False)
    for path in inputs:
        shutil.copyfile(path, output / path.name)
    shutil.copyfile(ROOT / "tools" / "install.py", output / "install-spinflow.py")
    manifest = {
        "schema_version": "1", "version": release_version, "commit": commit,
        "supported_platform": "Ubuntu 24.04 x86_64", "python_abis": ["cp312", "cp313"],
        "binary_compatibility": "Ubuntu-specific linux_x86_64 wheels; no manylinux or other-distribution claim",
        "dependency_policy": "Direct runtime dependencies are pinned in wheel metadata. Binary-only package resolution; transitive dependencies are recorded below, not locked.",
        "scientific_status": "experimental", "builds": records,
        "assets": {path.name: {"sha256": sha256(path), "bytes": path.stat().st_size} for path in sorted(output.iterdir())},
    }
    (output / "build-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checksums(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sums = commands.add_parser("checksums")
    sums.add_argument("directory", type=Path)
    check = commands.add_parser("smoke")
    check.add_argument("--prefix", type=Path, required=True)
    check.add_argument("--gui", action="store_true")
    check.add_argument("--plot", action="store_true")
    build = commands.add_parser("record")
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--prefix", type=Path, action="append", required=True)
    build.add_argument("--commit", required=True)
    collect = commands.add_parser("assemble")
    collect.add_argument("--input", type=Path, required=True)
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--commit", required=True)
    args = parser.parse_args()
    if args.command == "checksums":
        checksums(args.directory)
    elif args.command == "smoke":
        smoke(args.prefix, args.gui, args.plot)
    elif args.command == "record":
        record(args.output, args.prefix, args.commit)
    else:
        assemble(args.input, args.output, args.commit)


if __name__ == "__main__":
    main()
