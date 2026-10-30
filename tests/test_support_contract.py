"""Keep the installed support declaration aligned with public inventories."""

import json
import hashlib
from pathlib import Path
import re
import tomllib

import nmrnext
from nmrnext.io import capabilities
from nmrnext.support import support_capabilities


ROOT = Path(__file__).resolve().parents[1]


def _inventory_ids(name):
    text = (ROOT / "compatibility" / name).read_text(encoding="utf-8")
    return re.findall(r"^  - id: ([^\s#]+)", text, re.MULTILINE)


def test_canonical_registry_matches_runtime_and_profile_inventories():
    declared = support_capabilities()
    canonical = json.loads((ROOT / "compatibility" / "support.json").read_text(encoding="utf-8"))
    assert declared == canonical
    assert declared["schema_version"] == "1"

    profile_ids = [item["id"] for item in declared["profiles"]]
    workflow_ids = [item["id"] for item in declared["workflows"]]
    runtime_ids = [item["format"] for item in capabilities()]
    assert len(profile_ids) == len(set(profile_ids))
    assert len(workflow_ids) == len(set(workflow_ids))
    assert set(profile_ids) == set(_inventory_ids("formats.yaml"))
    assert set(workflow_ids) == set(_inventory_ids("workflows.yaml"))
    assert set(runtime_ids) == {
        item["id"] for item in declared["profiles"]
        if item["source_status"] == "implemented_experimental"
    }
    assert all(item["source_status"] in {"implemented_experimental", "unsupported"}
               for item in declared["profiles"])
    assert all(item["source_status"] in {"experimental", "unsupported"}
               for item in declared["workflows"])
    block = next(item for item in declared["workflows"] if item["id"] == "bounded-native-file-processing")
    assert block["source_status"] == "experimental"
    assert block["processing_v1_target"] is None
    distribution = declared["distribution"]
    assert distribution == {
        "version": "0.3.0b2",
        "release_status": "experimental beta",
        "previous_release": "v0.3.0b1",
        "qualification": "scientific and independent researcher acceptance unverified",
    }
    assert "unreleased" not in json.dumps(distribution).lower()
    assert "latest published" not in json.dumps(distribution).lower()
    assert all(item["candidate_status"] == (
        f'{distribution["release_status"]}; {distribution["qualification"]}'
    ) for item in capabilities())


def test_public_status_docs_link_to_registry_and_do_not_hide_block_mode():
    for name in ("README.md", "ROADMAP.md", "docs/scope.md", "docs/architecture.md",
                 "docs/formats.md", "docs/support-contract.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "support.json" in text, name
    for name in ("README.md", "ROADMAP.md", "docs/architecture.md"):
        text = (ROOT / name).read_text(encoding="utf-8").lower()
        assert "bounded" in text and "native" in text, name
        assert "all arrays remain in memory" not in text, name


def test_source_candidate_identity_and_notice_packaging_are_consistent():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    assert version == nmrnext.__version__ == support_capabilities()["distribution"]["version"]
    assert f'VERSION_INFO="{version}"' in (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    assert f'DEFAULT_VERSION = "{version}"' in (ROOT / "tools" / "install.py").read_text(encoding="utf-8")
    assert "THIRD_PARTY_NOTICES" in project["project"]["license-files"]
    assert "/THIRD_PARTY_NOTICES" in project["tool"]["scikit-build"]["sdist"]["include"]
    assert "/compatibility/support.json" in project["tool"]["scikit-build"]["sdist"]["include"]
    assert 'install(FILES compatibility/support.json DESTINATION nmrnext)' in (
        ROOT / "CMakeLists.txt").read_text(encoding="utf-8")


def test_pinned_pybind11_license_is_complete_and_packaged():
    license_path = ROOT / "third_party" / "pybind11" / "LICENSE"
    expected_sha256 = "83965b843b98f670d3a85bd041ed4b372c8ec50d7b4a5995a83ac697ba675dcb"
    assert license_path.is_file()
    assert hashlib.sha256(license_path.read_bytes()).hexdigest() == expected_sha256
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "third_party/pybind11/LICENSE" in project["project"]["license-files"]
    assert "/third_party/pybind11/LICENSE" in project["tool"]["scikit-build"]["sdist"]["include"]
    notice = (ROOT / "THIRD_PARTY_NOTICES").read_text(encoding="utf-8")
    assert "pybind11 3.1.0" in notice
    assert "compiled" in notice.lower() and "third_party/pybind11/LICENSE" in notice
