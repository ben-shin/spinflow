"""Build the native PocketFFT extension into src/nmrnext for source-tree tests.

On Windows without Visual Studio, use the verified workspace-local Zig archive.
Elsewhere, use the portable CMake project. Installed wheels already contain
the extension; this helper is for tests that import directly from ``src``.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig

import pybind11


ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "src" / "nmrnext"


def main() -> None:
    MODULE_DIR.mkdir(parents=True, exist_ok=True)
    temp = ROOT / ".tmp"
    temp.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["TEMP"] = env["TMP"] = str(temp)
    env["ZIG_LOCAL_CACHE_DIR"] = str(temp / "zig-local")
    env["ZIG_GLOBAL_CACHE_DIR"] = str(temp / "zig-global")

    zig = ROOT / ".tools" / "zig-x86_64-windows-0.17.0" / "zig.exe"
    if sys.platform == "win32" and zig.is_file():
        lib_name = f"python{sys.version_info.major}{sys.version_info.minor}.lib"
        source_lib = Path(sys.base_prefix) / "libs" / lib_name
        local_lib_dir = ROOT / ".tools"
        local_lib_dir.mkdir(exist_ok=True)
        local_lib = local_lib_dir / lib_name
        if not source_lib.is_file():
            raise RuntimeError(f"Python import library missing: {source_lib}")
        shutil.copy2(source_lib, local_lib)
        suffix = sysconfig.get_config_var("EXT_SUFFIX")
        if not suffix:
            raise RuntimeError("Python extension suffix unavailable")
        command = [
            str(zig), "c++", "-std=c++20", "-shared", "-O2",
            "-I", sysconfig.get_path("include"),
            "-I", pybind11.get_include(),
            "-I", str(ROOT / "cpp"),
            str(ROOT / "cpp" / "native.cpp"),
            "-L", str(local_lib_dir), f"-lpython{sys.version_info.major}{sys.version_info.minor}",
            "-o", str(MODULE_DIR / ("_native" + suffix)),
        ]
        subprocess.run(command, check=True, cwd=ROOT, env=env)
        return

    # Keep the venv path: on Linux its python executable may be a symlink.
    scripts_dir = Path(sys.executable).parent
    local_cmake = scripts_dir / ("cmake.exe" if sys.platform == "win32" else "cmake")
    cmake = str(local_cmake) if local_cmake.is_file() else shutil.which("cmake")
    if cmake is None:
        raise RuntimeError("CMake missing; install CMake >=3.20 or run tools/bootstrap.sh")
    # Calling .venv/bin/python directly does not activate the environment's PATH.
    # Include its scripts directory so CMake can also find pip-installed Ninja.
    env["PATH"] = str(scripts_dir) + os.pathsep + env.get("PATH", "")
    build_dir = ROOT / ".build" / "native"
    stage_dir = build_dir / "stage"
    subprocess.run(
        [cmake, "-S", str(ROOT), "-B", str(build_dir),
         f"-DPython_EXECUTABLE={sys.executable}",
         f"-Dpybind11_DIR={pybind11.get_cmake_dir()}"],
        check=True, cwd=ROOT, env=env,
    )
    subprocess.run([cmake, "--build", str(build_dir), "--config", "Release"],
                   check=True, cwd=ROOT, env=env)
    subprocess.run([cmake, "--install", str(build_dir), "--config", "Release",
                    "--prefix", str(stage_dir)], check=True, cwd=ROOT, env=env)
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    if not suffix:
        raise RuntimeError("Python extension suffix unavailable")
    extension = stage_dir / "nmrnext" / ("_native" + suffix)
    if not extension.is_file():
        raise RuntimeError(f"Native extension not installed at expected path: {extension}")
    shutil.copy2(extension, MODULE_DIR / extension.name)


if __name__ == "__main__":
    main()
