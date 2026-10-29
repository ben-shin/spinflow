"""Read-only same-lane identity, excluding only root-approved creation timestamps."""
import hashlib,struct
from pathlib import Path

def identity(path,endpoint):
    data=Path(path).read_bytes()
    if endpoint=='raw_to_nmrpipe_real':
        header_bytes=2048;ranges=[(i*4,(i+1)*4) for i in [283,284,285,294,295,296]]
        assert len(data)>=header_bytes
    elif endpoint=='raw_to_ucsf_real':
        assert data.startswith(b'UCSF NMR') and data[10]==2
        header_bytes=180+2*128;ranges=[(23,49)]
    else:raise ValueError(endpoint)
    canonical=bytearray(data)
    timestamps=[]
    for start,stop in ranges:
        timestamps.append({'start':start,'stop_exclusive':stop,'raw_hex':data[start:stop].hex()})
        canonical[start:stop]=b'\0'*(stop-start)
    sha=lambda b:hashlib.sha256(b).hexdigest()
    return {'bytes':len(data),'raw_sha256':sha(data),'canonical_excluding_only_creation_timestamp_sha256':sha(canonical),
            'raw_header_sha256':sha(data[:header_bytes]),'canonical_header_sha256':sha(canonical[:header_bytes]),
            'payload_sha256':sha(data[header_bytes:]),'header_bytes':header_bytes,'excluded_timestamp_bytes':timestamps}

def assert_same(actual,expected):
    for key in ['bytes','canonical_excluding_only_creation_timestamp_sha256','canonical_header_sha256','payload_sha256','header_bytes']:
        assert actual[key]==expected[key],key
