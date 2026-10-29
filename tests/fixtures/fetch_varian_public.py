"""Hash-pinned, bounded private cache acquisition; never execute source scripts."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from urllib.request import urlopen
import zipfile


MANIFEST = Path(__file__).with_name('varian_public_manifest.json')


def _safe(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name or str(p) != name:
        raise ValueError('Unsafe manifest path')
    return name


def validate_manifest(manifest):
    paths = set()
    for archive in manifest['archives']:
        if not archive['url'].startswith('https://storage.googleapis.com/google-code-archive-downloads/v2/code.google.com/nmrglue/'):
            raise ValueError('Unexpected public archive origin')
        for item in [archive, *archive['members']]:
            path = _safe(item['path'])
            if path in paths:
                raise ValueError('Duplicate manifest path')
            paths.add(path)
            if 'member' in item:
                _safe(item['member'])
            if type(item['bytes']) is not int or not 0 < item['bytes'] <= 64<<20:
                raise ValueError('Invalid manifest size')
            if len(item['sha256']) != 64 or any(c not in '0123456789abcdef' for c in item['sha256']):
                raise ValueError('Invalid manifest hash')
    return manifest['archives']


def _verify(payload, item):
    if len(payload) != item['bytes'] or hashlib.sha256(payload).hexdigest() != item['sha256']:
        raise RuntimeError('Source checksum mismatch: '+item['path'])


def _target(root, name):
    target = root/_safe(name)
    if any(p.is_symlink() for p in [root, target, *target.parents] if p == root or root in p.parents):
        raise RuntimeError('Symlink in cache path')
    if not target.resolve().is_relative_to(root.resolve()):
        raise RuntimeError('Cache path escapes root')
    return target


def _publish(root, item, payload):
    target = _target(root,item['path'])
    _verify(payload,item)
    if target.exists():
        _verify(target.read_bytes(),item)
        return
    target.parent.mkdir(parents=True,exist_ok=True)
    staging = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent,prefix='.fetch-',delete=False) as handle:
            staging = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == 'nt':
            os.rename(staging,target)
        else:
            os.link(staging,target)
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)


def fetch(root, manifest):
    root = Path(root)
    for archive in validate_manifest(manifest):
        target = _target(root,archive['path'])
        if target.exists():
            if target.stat().st_size != archive['bytes']:
                raise RuntimeError('Existing cache checksum mismatch')
            payload = target.read_bytes()
        else:
            with urlopen(archive['url'],timeout=30) as response:
                if not response.url.startswith('https://storage.googleapis.com/'):
                    raise RuntimeError('Unexpected archive redirect')
                payload = response.read(archive['bytes']+1)
        _verify(payload,archive)
        _publish(root,archive,payload)
        with zipfile.ZipFile(io.BytesIO(payload)) as zipped:
            for item in archive['members']:
                if zipped.getinfo(item['member']).file_size != item['bytes']:
                    raise RuntimeError('Archive member size mismatch')
                _publish(root,item,zipped.read(item['member']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('.artifacts/coverage-formats'))
    args = parser.parse_args()
    fetch(args.root,json.loads(MANIFEST.read_text()))
    print(f'Verified private Varian data and stored references in {args.root}')


if __name__ == '__main__':
    main()
