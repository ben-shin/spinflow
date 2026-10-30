"""Consumer-install failure cases; real Linux installs run in release.yml."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from unittest import mock

import pytest


def load_installer():
    path = Path(__file__).resolve().parents[2] / "tools" / "install.py"
    if not path.exists():
        pytest.skip("The release installer has not been implemented")
    spec = importlib.util.spec_from_file_location("spinflow_release_installer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def installer():
    return load_installer()


def assets(root, name="spinflow-0.2.0-cp313-cp313-linux_x86_64.whl"):
    root.mkdir()
    payload = b"test wheel bytes"
    (root / name).write_bytes(payload)
    (root / "SHA256SUMS").write_text(hashlib.sha256(payload).hexdigest() + "  " + name + "\n")
    return root


def test_version_validation_rejects_paths_and_accepts_explicit_tag(installer):
    assert installer.release_version("v0.2.0") == "0.2.0"
    assert installer.release_version("v0.3.0b1") == "0.3.0b1"
    assert installer.release_version("v0.3.0b2") == "0.3.0b2"
    assert installer.DEFAULT_VERSION == "0.3.0b2"
    for value in ("latest", "../../other", "0.2.0/thing", "0.2.0;touch x", "0.2"):
        with pytest.raises(installer.InstallError):
            installer.release_version(value)


def test_unsupported_runtime_stops_before_prefix_writes(installer, tmp_path):
    prefix = tmp_path / "new environment"
    with mock.patch.object(installer.platform, "system", return_value="Windows"):
        with pytest.raises(installer.InstallError, match="Ubuntu"):
            installer.install("0.2.0", prefix, [], None)
    assert not prefix.exists()


@pytest.mark.parametrize("change", ["corrupt", "missing", "duplicate"])
def test_bad_release_assets_fail_without_modifying_prefix(installer, tmp_path, change):
    source = assets(tmp_path / "assets")
    wheel = next(source.glob("*.whl"))
    if change == "corrupt":
        wheel.write_bytes(b"altered")
    elif change == "missing":
        (source / "SHA256SUMS").write_text("0" * 64 + "  other.whl\n")
    else:
        sums = source / "SHA256SUMS"
        sums.write_text(sums.read_text() * 2)
    prefix = tmp_path / "consumer"
    with mock.patch.object(installer, "runtime_tag", return_value="cp313"):
        with pytest.raises(installer.InstallError):
            installer.install("0.2.0", prefix, [], source)
    assert not prefix.exists()


def test_existing_user_files_are_preserved(installer, tmp_path):
    prefix = tmp_path / "user data"
    prefix.mkdir()
    original = prefix / "notes.txt"
    original.write_bytes(b"user notes")
    with mock.patch.object(installer, "runtime_tag", return_value="cp313"):
        with pytest.raises(installer.InstallError, match="unrelated|owned"):
            installer.install("0.2.0", prefix, [], tmp_path / "missing assets")
    assert original.read_bytes() == b"user notes"
    assert list(prefix.iterdir()) == [original]


def test_prefix_symlink_is_refused(installer, tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("This host does not permit creating symlinks")
    with pytest.raises(installer.InstallError, match="symlink"):
        installer.check_prefix(link, {"version": "0.2.0"})
    assert list(target.iterdir()) == []


def test_spaced_prefix_binary_only_failure_retry_and_rerun(installer, tmp_path):
    source = assets(tmp_path / "release assets")
    prefix = tmp_path / "Spin Flow environment"
    calls = []
    fail = True

    def run(command, **kwargs):
        nonlocal fail
        calls.append(command)
        if "venv" in command:
            (prefix / "bin").mkdir(exist_ok=True)
            (prefix / "bin" / "python").touch()
        if "install" in command and fail:
            fail = False
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, "", "")

    with mock.patch.object(installer, "runtime_tag", return_value="cp313"), \
         mock.patch.object(installer.subprocess, "run", side_effect=run), \
         mock.patch.object(installer, "verify_environment") as verify:
        with pytest.raises(subprocess.CalledProcessError):
            installer.install("0.2.0", prefix, ["gui", "plot"], source)
        marker = prefix / installer.MARKER
        assert json.loads(marker.read_text())["complete"] is False
        assert not installer.lock_path(prefix).exists()
        installer.install("0.2.0", prefix, ["gui", "plot"], source)
        assert json.loads(marker.read_text())["complete"] is True
        installs = [command for command in calls if "install" in command]
        assert len(installs) == 2
        assert all("--only-binary=:all:" in command for command in installs)
        assert all(command[-1].endswith(".whl[gui,plot]") for command in installs)
        assert all(command[0] == str(prefix / "bin" / "python") for command in installs)
        installer.install("0.2.0", prefix, ["gui", "plot"], source)
        assert len([command for command in calls if "install" in command]) == 2
        assert verify.call_count == 2
        installer.install("0.2.0", prefix, [], source)
        assert len([command for command in calls if "install" in command]) == 2
        assert verify.call_args.args[-1] == ["gui", "plot"]


def test_rerun_can_add_extras_without_replacing_existing_environment(installer, tmp_path):
    source = assets(tmp_path / "assets")
    prefix = tmp_path / "consumer"
    with mock.patch.object(installer, "runtime_tag", return_value="cp313"), \
         mock.patch.object(installer.subprocess, "run") as run, \
         mock.patch.object(installer, "verify_environment") as verify:
        installer.install("0.2.0", prefix, [], source)
        (prefix / "bin").mkdir()
        (prefix / "bin" / "python").touch()
        sentinel = prefix / "keep.txt"
        sentinel.write_text("keep")
        installer.install("0.2.0", prefix, ["plot"], source)
        record = json.loads((prefix / installer.MARKER).read_text())
        assert record["complete"] is True and record["extras"] == ["plot"]
        assert verify.call_args.args[-1] == ["plot"]
        assert sentinel.read_text() == "keep"
        assert len([call for call in run.call_args_list if "venv" in call.args[0]]) == 1


def test_retry_completes_bootstrap_interrupted_after_python_before_pip(installer, tmp_path):
    source = assets(tmp_path / "release assets")
    prefix = tmp_path / "Interrupted Spin Flow"
    sentinel = prefix / "keep.txt"
    calls = []
    bootstrap_attempts = 0
    pip_ready = False

    def run(command, **kwargs):
        nonlocal bootstrap_attempts, pip_ready
        calls.append(command)
        if "venv" in command:
            bootstrap_attempts += 1
            (prefix / "bin").mkdir(exist_ok=True)
            (prefix / "bin" / "python").touch()
            if bootstrap_attempts == 1:
                sentinel.write_bytes(b"Preserve this file across bootstrap recovery")
                raise subprocess.CalledProcessError(1, command, stderr="interrupted before ensurepip")
            assert sentinel.read_bytes() == b"Preserve this file across bootstrap recovery"
            assert "--clear" not in command
            pip_ready = True
        if "pip" in command and not pip_ready:
            raise subprocess.CalledProcessError(1, command, stderr="No module named pip")
        return subprocess.CompletedProcess(command, 0, "", "")

    with mock.patch.object(installer, "runtime_tag", return_value="cp313"), \
         mock.patch.object(installer.subprocess, "run", side_effect=run), \
         mock.patch.object(installer, "verify_environment") as verify:
        with pytest.raises(subprocess.CalledProcessError, match="venv"):
            installer.install("0.2.0", prefix, ["gui", "plot"], source)
        assert (prefix / "bin" / "python").is_file()
        interrupted = json.loads((prefix / installer.MARKER).read_text())
        assert interrupted["complete"] is False
        assert interrupted["extras"] == ["gui", "plot"]
        assert not installer.lock_path(prefix).exists()
        installer.install("0.2.0", prefix, ["gui", "plot"], source)
        recovered = json.loads((prefix / installer.MARKER).read_text())
        assert recovered["complete"] is True
        assert recovered["extras"] == ["gui", "plot"]
        assert sentinel.read_bytes() == b"Preserve this file across bootstrap recovery"
        assert bootstrap_attempts == 2
        installs = [command for command in calls if "install" in command]
        assert len(installs) == 1
        assert "--only-binary=:all:" in installs[0]
        assert installs[0][-1].endswith(".whl[gui,plot]")
        verify.assert_called_once_with(prefix, "0.2.0", ["gui", "plot"])
        assert not installer.lock_path(prefix).exists()


def test_incomplete_native_check_never_marks_ready(installer, tmp_path):
    source = assets(tmp_path / "assets")
    prefix = tmp_path / "consumer"
    with mock.patch.object(installer, "runtime_tag", return_value="cp313"), \
         mock.patch.object(installer.subprocess, "run"), \
         mock.patch.object(installer, "verify_environment", side_effect=installer.InstallError("Native unavailable")):
        with pytest.raises(installer.InstallError, match="Native"):
            installer.install("0.2.0", prefix, [], source)
    assert json.loads((prefix / installer.MARKER).read_text())["complete"] is False


@pytest.mark.parametrize("distro,machine,implementation", [
    ({"ID": "ubuntu", "VERSION_ID": "22.04"}, "x86_64", "CPython"),
    ({"ID": "debian", "VERSION_ID": "24.04"}, "x86_64", "CPython"),
    ({"ID": "ubuntu", "VERSION_ID": "24.04"}, "aarch64", "CPython"),
    ({"ID": "ubuntu", "VERSION_ID": "24.04"}, "x86_64", "PyPy"),
])
def test_release_wheels_do_not_claim_other_platforms(installer, distro, machine, implementation):
    with mock.patch.object(installer.platform, "system", return_value="Linux"), \
         mock.patch.object(installer.platform, "machine", return_value=machine), \
         mock.patch.object(installer.platform, "python_implementation", return_value=implementation), \
         mock.patch.object(installer.platform, "freedesktop_os_release", return_value=distro, create=True):
        with pytest.raises(installer.InstallError, match="Ubuntu"):
            installer.runtime_tag()


def test_missing_wheel_and_active_install_lock_do_not_overwrite(installer, tmp_path):
    source = assets(tmp_path / "assets")
    prefix = tmp_path / "consumer"
    next(source.glob("*.whl")).unlink()
    with mock.patch.object(installer, "runtime_tag", return_value="cp313"):
        with pytest.raises(installer.InstallError, match="missing"):
            installer.install("0.2.0", prefix, [], source)
        assert not prefix.exists()
        source.joinpath("spinflow-0.2.0-cp313-cp313-linux_x86_64.whl").write_bytes(b"test wheel bytes")
        lock = installer.lock_path(prefix)
        lock.write_text("an active installer")
        with pytest.raises(installer.InstallError, match="Another install"):
            installer.install("0.2.0", prefix, [], source)
        assert lock.read_text() == "an active installer"
        assert not prefix.exists()


def test_owned_prefix_rejects_different_python_or_version(installer, tmp_path):
    prefix = tmp_path / "consumer"
    prefix.mkdir()
    profile = {"version": "0.2.0", "python": "cp313", "prefix": str(prefix.absolute())}
    (prefix / installer.MARKER).write_text(json.dumps({"installer": "spinflow-release-v1", **profile, "extras": [], "complete": False}))
    assert installer.check_prefix(prefix, profile)["complete"] is False
    for changed in ({**profile, "version": "0.2.1"}, {**profile, "python": "cp312"}):
        with pytest.raises(installer.InstallError, match="profile"):
            installer.check_prefix(prefix, changed)


def test_doctor_native_refusal_is_an_install_failure(installer, tmp_path):
    result = subprocess.CompletedProcess([], 0, json.dumps({"version": "0.2.0", "backends": {"native": {"available": False}}}), "")
    with mock.patch.object(installer.subprocess, "run", return_value=result):
        with pytest.raises(installer.InstallError, match="native"):
            installer.verify_environment(tmp_path / "prefix", "0.2.0", [])


def test_release_assembler_requires_both_abis_and_records_hashes(tmp_path):
    path = Path(__file__).resolve().parents[2] / "tools" / "release_assets.py"
    assert path.exists(), "The release asset assembler has not been implemented"
    spec = importlib.util.spec_from_file_location("spinflow_release_assets", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    current_version = module.version()
    source = tmp_path / "incoming"
    source.mkdir()
    for name in (f"spinflow-{current_version}-cp312-cp312-linux_x86_64.whl", f"spinflow-{current_version}.tar.gz"):
        (source / name).write_bytes(b"release bytes")
    with pytest.raises(ValueError, match="cp313"):
        module.assemble(source, tmp_path / "missing", "a" * 40)
    (source / f"spinflow-{current_version}-cp313-cp313-linux_x86_64.whl").write_bytes(b"release bytes")
    for tag in ("cp312", "cp313"):
        (source / f"build-{tag}.json").write_text(json.dumps({"commit": "a" * 40, "python_tag": tag, "version": current_version}))
    output = tmp_path / "release"
    module.assemble(source, output, "a" * 40)
    assert (output / "install-spinflow.py").is_file()
    checksums = load_installer().read_checksums(output / "SHA256SUMS")
    assert len(checksums) == 5
    assert all(hashlib.sha256((output / name).read_bytes()).hexdigest() == digest for name, digest in checksums.items())
    manifest = json.loads((output / "build-manifest.json").read_text())
    assert manifest["commit"] == "a" * 40
    assert manifest["supported_platform"] == "Ubuntu 24.04 x86_64"
