"""Fetch hash-pinned public BMRB data privately for integration validation.

Run from the repository root: python tests/fixtures/fetch_bruker_public.py
Access terms and original URLs are in bruker_public_manifest.json. No scripts
from the acquisition are executed; original data remain outside version control.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.request import urlopen


def fetch_file(target, item):
    exists = target.exists()
    if exists:
        if target.is_symlink() or target.stat().st_size != item["bytes"]:
            raise RuntimeError(f"Existing file checksum mismatch: {target}")
        payload = target.read_bytes()
    else:
        with urlopen(item["url"], timeout=30) as response:
            payload = response.read(item["bytes"]+1)
    if len(payload) != item["bytes"] or hashlib.sha256(payload).hexdigest() != item["sha256"]:
        raise RuntimeError(f"Source checksum mismatch: {item['url']}")
    if exists:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".fetch-", delete=False) as handle:
            staging = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows rename refuses an existing destination. On POSIX use a hard
        # link for the same atomic no-overwrite publication semantics.
        if os.name == "nt":
            os.rename(staging, target)
        else:
            os.link(staging, target)
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)


def main():
    manifest = json.loads(Path(__file__).with_name("bruker_public_manifest.json").read_text())
    root = Path(__file__).resolve().parents[2]/".artifacts/science11-realdata/bmse000001"
    for item in manifest["files"]:
        fetch_file(root/item["path"], item)
    print(f"Verified {len(manifest['files'])} BMRB files in {root}")


if __name__ == "__main__":
    main()
