"""The optional public-data fetcher must never overwrite or stage bad bytes."""
import hashlib
import importlib.util
from pathlib import Path
import pytest


spec = importlib.util.spec_from_file_location("fetch_public", Path(__file__).parents[1]/"fixtures/fetch_bruker_public.py")
fetcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetcher)


def test_fetch_verifies_before_publish_reuses_identical_and_refuses_modified(tmp_path):
    source = tmp_path/"source"
    source.write_bytes(b"raw acquisition fixture")
    target = tmp_path/"destination"/"fid"
    item = {"url":source.as_uri(),"bytes":len(source.read_bytes()),"sha256":hashlib.sha256(source.read_bytes()).hexdigest()}
    fetcher.fetch_file(target,item)
    assert target.read_bytes() == source.read_bytes()
    source.unlink()  # proves reuse works offline
    fetcher.fetch_file(target,item)
    target.write_bytes(b"user file")
    with pytest.raises(RuntimeError,match="checksum"):
        fetcher.fetch_file(target,item)
    assert target.read_bytes() == b"user file"


def test_fetch_bad_checksum_leaves_no_destination_or_staging_file(tmp_path):
    source = tmp_path/"source"
    source.write_bytes(b"incorrect acquisition")
    target = tmp_path/"destination"/"fid"
    item = {"url":source.as_uri(),"bytes":len(source.read_bytes()),"sha256":"0"*64}
    with pytest.raises(RuntimeError,match="checksum"):
        fetcher.fetch_file(target,item)
    assert not target.exists()
    assert not target.parent.exists() or list(target.parent.iterdir()) == []
