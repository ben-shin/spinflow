"""Fetch bounded hash-pinned public 2D acquisitions into ignored private storage.

Run: python tests/fixtures/fetch_bruker2d_public.py
No archive extraction or acquisition scripts are executed. Original files and
access terms are recorded in bruker2d_public_manifest.json; no redistribution
license is assumed. Existing mismatched files are never overwritten.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from urllib.parse import urlsplit
from urllib.request import urlopen

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def validate_manifest(manifest):
    items = manifest["files"]
    total = 0
    seen = set()
    for item in items:
        path = PurePosixPath(item["path"])
        url = urlsplit(item["url"])
        if (not path.parts or path.is_absolute() or ".." in path.parts or
                "\\" in item["path"] or ":" in item["path"] or str(path) in seen):
            raise ValueError("Unsafe or duplicate manifest path")
        if (url.scheme != "https" or url.netloc != "bmrb.io" or url.query or url.fragment or
                not url.path.startswith("/ftp/pub/bmrb/metabolomics/entry_directories/bmse000001/")):
            raise ValueError("Unexpected archive origin")
        n = item["bytes"]
        if isinstance(n, bool) or not isinstance(n, int) or not 0 < n <= MAX_FILE_BYTES:
            raise ValueError("Invalid bounded source size")
        digest = item["sha256"]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid SHA256")
        total += n
        seen.add(str(path))
    if total > MAX_TOTAL_BYTES:
        raise ValueError("Manifest exceeds total download budget")
    return items


def fetch_file(root, item):
    root = Path(root).resolve()
    target = root.joinpath(*PurePosixPath(item["path"]).parts)
    # Resolve parents before writing so local symlinks cannot escape the cache.
    if not target.parent.resolve().is_relative_to(root):
        raise RuntimeError("Cache parent escapes destination")
    if target.is_symlink():
        raise RuntimeError("Refusing a symlink destination")
    exists = target.exists()
    if exists:
        if target.stat().st_size != item["bytes"]:
            raise RuntimeError(f"Existing file size mismatch: {target}")
        payload = target.read_bytes()
    else:
        with urlopen(item["url"], timeout=60) as response:
            final = urlsplit(response.url)
            if final.scheme != "https" or final.netloc != "bmrb.io":
                raise RuntimeError("Unexpected download redirect origin")
            payload = response.read(item["bytes"] + 1)
    if len(payload) != item["bytes"] or hashlib.sha256(payload).hexdigest() != item["sha256"]:
        raise RuntimeError(f"Source checksum mismatch: {item['path']}")
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
        if os.name == "nt":
            os.rename(staging, target)
        else:
            os.link(staging, target)
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    manifest = json.loads(Path(__file__).with_name("bruker2d_public_manifest.json").read_text())
    items = validate_manifest(manifest)
    root = args.destination or Path(__file__).resolve().parents[2]/".artifacts/bruker2d-realdata/bmse000001"
    for item in items:
        fetch_file(root, item)
    print(f"Verified {len(items)} public BMRB files ({sum(x['bytes'] for x in items)} bytes) in {root}")


if __name__ == "__main__":
    main()
