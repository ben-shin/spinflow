"""Bounded float32 Varian/Agilent raw profiles, retaining all source headers.

The archived NMRPipe mapping follows nmrglue 0.12 converter.from_varian:
https://github.com/jjhelmus/nmrglue/blob/v0.12/nmrglue/fileio/convert.py
Our negative-DFT convention conjugates each independent NMRPipe quadrature
axis: [R,I] and [even.R,odd.R,even.I,odd.I] retain the original stored signs.
This defines storage quadratures, not a phase or chemical-reference estimate.
"""
import hashlib
import io
import math
from pathlib import Path
import re
import shlex
import struct
import warnings

import numpy as np

from nmrnext.errors import NMRNextError
from nmrnext.model import Axis, Dataset


FILE_KEYS = ('nblocks', 'ntraces', 'np', 'ebytes', 'tbytes', 'bbytes',
             'vers_id', 'status', 'nbheaders')
BLOCK_KEYS = ('scale', 'status', 'index', 'mode', 'ctcount', 'lpval', 'rpval', 'lvl', 'tlt')


def _fail(message, code='UNSUPPORTED_PROFILE'):
    raise NMRNextError(code, message)


def _regular(path):
    if path.is_symlink() or not path.is_file():
        _fail(f'Varian requires a regular {path.name} file')


def _parameters(path):
    _regular(path)
    with path.open('rb') as handle:
        raw = handle.read((1 << 20)+1)
    if len(raw) > 1 << 20:
        _fail('procpar exceeds the 1 MiB limit', 'INVALID_HEADER')
    try:
        text = raw.decode('utf-8')
        lines = iter(text.splitlines())
        result = {}
        for line in lines:
            fields = line.split()
            if len(fields) != 11 or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', fields[0]):
                raise ValueError('parameter header needs a name and ten attributes')
            name = fields[0]
            if name in result:
                raise ValueError('duplicate parameter '+name)
            if fields[2] not in ('1', '2'):
                raise ValueError('unsupported parameter type')
            if not all(math.isfinite(float(v)) for v in fields[1:]):
                raise ValueError('nonfinite parameter attribute')
            value_line = next(lines)
            tokens = shlex.split(value_line)
            count = int(tokens[0])
            if count < 1 or count > 100000:
                raise ValueError('invalid parameter value count')
            values = tokens[1:]
            if fields[2] == '2':
                # VNMR strings occupy one quoted line per value.
                for _ in range(count-1):
                    values.extend(shlex.split(next(lines)))
            if len(values) != count:
                raise ValueError('parameter value count mismatch')
            if fields[2] == '1' and not all(math.isfinite(float(v)) for v in values):
                raise ValueError('nonfinite numeric parameter')
            enums = shlex.split(next(lines))
            enum_count = int(enums[0])
            if enum_count < 0 or enum_count != len(enums)-1:
                raise ValueError('enumeration count mismatch')
            if fields[2] == '1' and not all(math.isfinite(float(v)) for v in enums[1:]):
                raise ValueError('nonfinite numeric enumeration')
            result[name] = {'attributes': fields[1:], 'values': values, 'enumerables': enums[1:]}
        if not result:
            raise ValueError('empty procpar')
    except (ValueError, IndexError, StopIteration, UnicodeError, OverflowError) as exc:
        raise NMRNextError('INVALID_HEADER', f'Malformed procpar: {exc}') from exc
    return result, text, hashlib.sha256(raw).hexdigest(), len(raw)


def _values(params, name, default=None):
    return params[name]['values'] if name in params else default


def _number(params, name, default=None, positive=False, integer=False):
    values = _values(params, name)
    if values is None and default is not None:
        return default
    if values is None or len(values) != 1:
        _fail(f'Varian {name} must be a scalar', 'INVALID_HEADER')
    try:
        value = float(values[0])
    except (ValueError, OverflowError) as exc:
        raise NMRNextError('INVALID_HEADER', f'Invalid Varian {name}') from exc
    if not math.isfinite(value) or (positive and value <= 0) or (integer and value != int(value)):
        _fail(f'Invalid Varian {name}', 'INVALID_HEADER')
    return int(value) if integer else value


def inspect_varian(path, max_bytes, *, dimensions):
    from . import _check_budget
    path = Path(path)
    if not path.is_dir() or (path/'acqus').exists():
        _fail('Varian input must be an unambiguous procpar/fid directory')
    if any(p.name.lower() in {'nuslist', 'sampling.sch', 'sampling_schedule', 'nus.list'} for p in path.iterdir()):
        _fail('Varian NUS schedules are unsupported', 'UNSUPPORTED_SAMPLING')
    fid = path/'fid'
    _regular(fid)
    params, text, digest, text_size = _parameters(path/'procpar')
    with fid.open('rb') as handle:
        raw = handle.read(32)
        if len(raw) != 32:
            _fail('Truncated Varian file header', 'INVALID_HEADER')
        h = dict(zip(FILE_KEYS, struct.unpack('>6i2hi', raw)))
        if (h['nblocks'] < 1 or h['np'] < 2 or h['np'] % 2 or h['ntraces'] != 1 or
                h['ebytes'] != 4 or h['nbheaders'] != 1 or h['vers_id'] != 0 or
                h['status'] not in (73, 201)):
            _fail('Only version 0 big-endian float32 raw blocks with status 73/201, one trace/header and complex pairs are supported')
        if h['tbytes'] != h['np']*4 or h['bbytes'] != h['tbytes']+28:
            _fail('Varian trace/block byte counts disagree', 'INVALID_SHAPE')
        size = 32+h['nblocks']*h['bbytes']
        if fid.stat().st_size != size:
            _fail('Varian fid length differs from declared blocks', 'INVALID_SHAPE')
        required = h['nblocks']*h['np']*4*4+text_size+28*h['nblocks']
        _check_budget(required, max_bytes)
        blocks = []
        for i in range(h['nblocks']):
            handle.seek(32+i*h['bbytes'])
            b = dict(zip(BLOCK_KEYS, struct.unpack('>4hi4f', handle.read(28))))
            if (b['status'] != h['status'] or b['index'] != i+1 or b['ctcount'] < 1 or
                    any(b[k] != 0 for k in ('scale', 'mode', 'lvl', 'tlt')) or
                    not all(math.isfinite(b[k]) for k in ('lpval', 'rpval', 'lvl', 'tlt'))):
                _fail('Unsupported or inconsistent Varian block header; scaling/drift correction is not guessed')
            blocks.append(b)
    if _number(params, 'np', positive=True, integer=True) != h['np']:
        _fail('procpar np disagrees with fid', 'INVALID_SHAPE')
    if _number(params, 'arraydim', positive=True, integer=True) != h['nblocks']:
        _fail('procpar arraydim disagrees with fid', 'INVALID_SHAPE')
    for key in ('ni2', 'ni3'):
        if _number(params, key, default=0) != 0:
            _fail('Higher-dimensional Varian acquisition is unsupported')
    if _number(params, 'nf', default=1) != 1:
        _fail('Arrayed/multiple Varian traces are unsupported')
    for key in ('nus', 'NUS', 'nusflag'):
        if _values(params, key, ['n']) not in (['n'], ['0'], ['']):
            _fail('Varian NUS metadata is unsupported', 'UNSUPPORTED_SAMPLING')
    sw = _number(params, 'sw', positive=True)
    obs = _number(params, 'sfrq', positive=True)
    nucleus = _values(params, 'tn')
    if nucleus is None or len(nucleus) != 1 or not re.fullmatch(r'(?:[A-Z][a-z]?\d+|\d+[A-Z][a-z]?)', nucleus[0]):
        _fail('Varian tn must identify one nucleus', 'INVALID_HEADER')
    axes = [{'width':sw, 'obs':obs, 'nucleus':nucleus[0], 'origin':0., 'size':h['np']//2}]
    if dimensions == 1:
        if h['nblocks'] != 1 or _number(params, 'ni', default=0) not in (0, 1) or _values(params, 'array', ['']) != ['']:
            _fail('The raw 1D profile requires one unarrayed FID')
    elif dimensions == 2:
        if (_values(params, 'seqfil') != ['cpjdcptan2d'] or _values(params, 'array') != ['phase'] or
                _values(params, 'phase') != ['1', '2'] or nucleus != ['C13'] or _values(params, 'dn2') != ['N15']):
            _fail('The bounded 2D States profile requires cpjdcptan2d, C13/N15, array=phase and phase=[1,2]; other pulse-sequence mappings are unverified')
        ni = _number(params, 'ni', positive=True, integer=True)
        if 2*ni != h['nblocks'] or ni < 2:
            _fail('Varian States requires two phase blocks per indirect increment', 'INVALID_SHAPE')
        origin = _number(params, 'd2', default=0.)
        if origin < 0 or any(_values(params, k, ['n']) != ['n'] for k in ('f1180', 'f2180')):
            _fail('Half-dwell or invalid indirect time origins are unsupported')
        axes.insert(0, {'width':_number(params, 'sw1', positive=True),
                       'obs':_number(params, 'dfrq2', positive=True), 'nucleus':'N15', 'origin':origin, 'size':ni})
    else:
        _fail('Only Varian 1D or bounded 2D States profiles are supported')
    return {'format':f'varian-raw-{dimensions}d', 'version':'1',
            'shape':[2**dimensions, *(a['size'] for a in axes)], 'dtype':'float32',
            'header':{'file_header':h, 'block_headers':blocks, 'axes':axes,
                      'procpar':params, 'raw_procpar_text':text, 'procpar_sha256':digest,
                      'source_bytes':{'fid':size, 'procpar':text_size}, 'byte_order':'big',
                      'read_budget_bytes':required}}


def read_varian(path, head):
    from . import _nmrglue, _hash_file
    path = Path(path)
    h = head['header']
    if _hash_file(path/'procpar') != h['procpar_sha256']:
        _fail('procpar changed between inspection and import', 'INPUT_CHANGED')
    with (path/'fid').open('rb') as handle:
        payload = handle.read(h['source_bytes']['fid']+1)
    if len(payload) != h['source_bytes']['fid']:
        _fail('fid changed length between inspection and import', 'INPUT_CHANGED')
    stream = io.BytesIO(payload)
    ng = _nmrglue()
    actual = ng.varian.fileheader2dic(ng.varian.get_fileheader(stream))
    if any(actual[k] != v for k,v in h['file_header'].items()):
        _fail('fid header changed between inspection and import', 'INPUT_CHANGED')
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        blocks, rows = ng.varian.get_nblocks(stream, actual['nblocks'], actual['np'], 1, np.dtype('>f4'), True)
    if caught or any(any(b[k] != v for k,v in expected.items()) for b,expected in zip(blocks,h['block_headers'])):
        _fail('fid blocks changed between inspection and import', 'INPUT_CHANGED')
    if not np.isfinite(rows).all():
        _fail('Varian fid contains nonfinite samples', 'NONFINITE_DATA')
    if head['format'] == 'varian-raw-1d':
        data = np.stack((rows[0,0::2], rows[0,1::2]))
        mapping = 'R=stored.real; I=stored.imag'
    else:
        data = np.stack((rows[0::2,0::2], rows[1::2,0::2], rows[0::2,1::2], rows[1::2,1::2]))
        mapping = 'RR=even.real; IR=odd.real; RI=even.imag; II=odd.imag'
    axes = tuple(Axis(id=f'a{i}', size=a['size'], start=a['origin'], step=1/a['width'],
                      dwell_seconds=1/a['width'], observation_mhz=a['obs'], reference_ppm=None,
                      nucleus=a['nucleus'], label=a['nucleus'],
                      encoding='states' if len(h['axes']) == 2 and i == 0 else 'complex')
                 for i,a in enumerate(h['axes']))
    metadata = {'sampling':'fully_sampled', 'import':{
        'format':head['format'], 'profile_version':'1', 'byte_order':'big',
        'source_sha256':{'fid':hashlib.sha256(payload).hexdigest(), 'procpar':h['procpar_sha256']},
        'source_bytes':h['source_bytes'], 'file_header':h['file_header'],
        'block_headers':h['block_headers'], 'raw_procpar_text':h['raw_procpar_text'], 'procpar':h['procpar'],
        'quadrature':{'mapping':mapping, 'complex_bit_axes':[a.id for a in axes],
                      'component_order':['R','I'] if len(axes) == 1 else ['RR','IR','RI','II'],
                      'source_convention':'nmrglue 0.12 converter.from_varian followed by independent-axis conjugation for negative DFT',
                      'archived_nmrpipe_to_native_component_signs':[1,-1] if len(axes) == 1 else [1,-1,-1,1]},
        'reference_policy':'No ppm reference inferred; apply explicit reference after FFT',
        'frequency_direction_policy':'Bare FFT coordinates are mathematical offsets. Physical calibration requires explicit reference frequency_scale=-1 on every physical axis for this retained-pair-sign profile.',
        'amplitude_policy':'Stored amplitudes; no scan normalization, block scaling, drift or phase correction'}}
    return Dataset(data.astype(np.float32, copy=False), axes, tuple(a.id for a in axes), metadata)
