"""Offline optional acquired-data gates; fixture fetch is always explicit."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import zipfile

import pytest


REPO=Path(__file__).resolve().parents[2]


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fetcher=module('varian_fetch',REPO/'tests/fixtures/fetch_varian_public.py')
validator=module('varian_validate',REPO/'tools/validate_public_varian.py')
MANIFEST=json.loads(fetcher.MANIFEST.read_text())


def test_manifest_has_independent_stored_references_and_bounded_scope():
    archives=fetcher.validate_manifest(MANIFEST)
    assert len(archives)==6
    assert len(MANIFEST['cases'])==2
    assert MANIFEST['protocol']['raw_tolerance']=={'rtol':0,'atol':0}
    assert MANIFEST['protocol']['declared_before_candidate_comparison']
    assert 'No explicit redistribution license' in MANIFEST['access_terms']
    assert all(case['raw_reference']!=case['raw'] for case in MANIFEST['cases'])


@pytest.mark.parametrize('path',['../escape','/absolute','C:/escape','a\\escape'])
def test_fetch_manifest_refuses_unsafe_paths(path):
    manifest=copy.deepcopy(MANIFEST);manifest['archives'][0]['members'][0]['path']=path
    with pytest.raises(ValueError,match='path'):fetcher.validate_manifest(manifest)


def _tiny_archive():
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:z.writestr('fid',b'original data')
    data=buffer.getvalue()
    return data,{'archives':[{'url':'https://storage.googleapis.com/google-code-archive-downloads/v2/code.google.com/nmrglue/test.zip',
             'path':'tiny.zip','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
             'members':[{'member':'fid','path':'source/fid','bytes':13,'sha256':hashlib.sha256(b'original data').hexdigest()}]}]}


def test_fetch_preserves_existing_and_reuses_offline(tmp_path,monkeypatch):
    data,manifest=_tiny_archive()
    response=io.BytesIO(data);response.url=manifest['archives'][0]['url']
    monkeypatch.setattr(fetcher,'urlopen',lambda *a,**k:response)
    fetcher.fetch(tmp_path,manifest)
    monkeypatch.setattr(fetcher,'urlopen',lambda *a,**k:pytest.fail('unexpected network'))
    fetcher.fetch(tmp_path,manifest)
    (tmp_path/'source/fid').write_bytes(b'preserve user data')
    with pytest.raises(RuntimeError,match='checksum'):fetcher.fetch(tmp_path,manifest)
    assert (tmp_path/'source/fid').read_bytes()==b'preserve user data'


def test_fetch_bad_hash_never_publishes(tmp_path,monkeypatch):
    data,manifest=_tiny_archive();manifest['archives'][0]['sha256']='0'*64
    response=io.BytesIO(data);response.url=manifest['archives'][0]['url']
    monkeypatch.setattr(fetcher,'urlopen',lambda *a,**k:response)
    with pytest.raises(RuntimeError,match='checksum'):fetcher.fetch(tmp_path,manifest)
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(not os.environ.get('SPINFLOW_VARIAN_ROOT'),reason='Set SPINFLOW_VARIAN_ROOT to verified private cache')
@pytest.mark.parametrize('backend',['numpy','native'])
def test_real_acquired_all_channels_and_fft(backend):
    result=validator.validate(Path(os.environ['SPINFLOW_VARIAN_ROOT']),backend)
    assert len(result['cases'])==2
    assert all(c['source_preserved'] and c['exact_archived_quadrature_match'] for c in result['cases'])
