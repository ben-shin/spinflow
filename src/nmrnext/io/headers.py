"""Scalar axis normalization and strictly sample-free file descriptions."""
import hashlib
import json
from pathlib import Path

from nmrnext.errors import NMRNextError
from nmrnext.model import Axis, DatasetHeader


def normalized_description(head, source_hashes=None):
    """Normalize inspected headers identically for planning and real ingestion."""
    from . import _nmrglue, _axis_from_uc
    fmt = head['format']
    if fmt == 'nmrnext-hdf5':
        record = head['metadata']
        return tuple(Axis(**a) for a in record['axes']), tuple(record['complex_axes']), record['metadata'], record['provenance']
    h = head['header']
    if fmt in {'nmrpipe-real', 'ucsf-real'}:
        ng = _nmrglue()
        shape = head['shape'][1:]
        axes = []
        for i, size in enumerate(shape):
            if fmt == 'nmrpipe-real':
                dim = 'FDF2' if len(shape) == 1 else ('FDF1' if i == 0 else 'FDF2')
                sw, obs, label = h[dim+'SW'], h[dim+'OBS'], h[dim+'LABEL']
                carrier = h[dim+'ORIG'] + sw/2. - sw/float(size)
            else:
                axis = h[f'w{i+1}']
                sw, obs, label = axis['spectral_width'], axis['spectrometer_freq'], axis['nucleus']
                carrier = axis['xmtr_freq']*obs
            uc = ng.fileiobase.unit_conversion(float(size), False, sw, obs, carrier)
            axes.append(_axis_from_uc(i, size, uc, label, obs=obs, sw=sw, car=carrier))
        imported = {'format':fmt, 'source_header':h}
        if source_hashes is not None:
            imported['source_sha256'] = source_hashes
        return tuple(axes), (), {'import':imported}, []
    if fmt == 'bruker-raw-1d':
        size = head['shape'][1]
        axis = Axis(id='a0', size=size, domain='time', unit='s', start=0., step=1/h['SW_h'],
                    observation_mhz=h['SFO1'], reference_ppm=None, label=h['NUC1'], nucleus=h['NUC1'],
                    encoding='complex', dwell_seconds=1/h['SW_h'], acquired_size=size)
        imported = {'format':fmt, 'raw_acqus_text':h['raw_acqus_text'],
                    'acquisition_parameters':h['acquisition_parameters'],
                    'padding_complex_points':h['padded_real_points']//2-size, 'digital_filter':h['digital_filter']}
        if source_hashes is not None:
            imported['source_sha256'] = source_hashes
        return (axis,), ('a0',), {'import':imported}, []
    if fmt == 'bruker-raw-2d':
        axes = tuple(Axis(id=f'a{i}', size=head['shape'][i+1], domain='time', unit='s', start=0.,
                         step=1/width, dwell_seconds=1/width, observation_mhz=frequency, reference_ppm=None,
                         nucleus=nucleus, label=nucleus, encoding='states' if i == 0 else 'complex')
                     for i,(width,frequency,nucleus) in enumerate(((h['indirect_SW_h'],h['indirect_SFO1'],h['indirect_NUC1']),
                                                                   (h['SW_h'],h['SFO1'],h['NUC1']))))
        mapping = 'C=even*(-1)^increment; S=odd*(-1)^increment' if h['FnMODE'] == 5 else 'C=even-odd; S=i*(even+odd); scale=1 (no averaging)'
        imported = {'format':fmt, 'profile_version':'1', 'direct_axis':'a1', 'indirect_axis':'a0',
                    'raw_acqus_text':h['raw_acqus_text'], 'raw_acqu2s_text':h['raw_acqu2s_text'],
                    'acquisition_parameters':h['acquisition_parameters'],
                    'indirect_acquisition_parameters':h['indirect_acquisition_parameters'],
                    'quadrature':{'source_FnMODE':h['FnMODE'], 'row_mapping':mapping,
                                  'component_order':['RR','IR','RI','II'], 'component_label_axis_order':['a0','a1'],
                                  'complex_bit_axes':['a0','a1'], 'imaginary_negation':False},
                    'padding_real_points_per_trace':h['padded_real_points']-h['TD'], 'digital_filter':h['digital_filter']}
        if source_hashes is not None:
            imported['source_sha256'] = source_hashes
        return axes, ('a0','a1'), {'sampling':'fully_sampled', 'import':imported}, []
    if fmt in {'varian-raw-1d','varian-raw-2d'}:
        axes = tuple(Axis(id=f'a{i}', size=a['size'], start=a['origin'], step=1/a['width'],
                          dwell_seconds=1/a['width'], observation_mhz=a['obs'], reference_ppm=None,
                          nucleus=a['nucleus'], label=a['nucleus'],
                          encoding='states' if len(h['axes']) == 2 and i == 0 else 'complex')
                     for i,a in enumerate(h['axes']))
        imported = {'format':fmt, 'profile_version':'1', 'byte_order':'big',
                    'source_bytes':h['source_bytes'], 'file_header':h['file_header'],
                    'block_headers':h['block_headers'], 'raw_procpar_text':h['raw_procpar_text'], 'procpar':h['procpar'],
                    'quadrature':{'mapping':'R=stored.real; I=stored.imag' if len(axes)==1 else 'RR=even.real; IR=odd.real; RI=even.imag; II=odd.imag',
                                  'complex_bit_axes':[a.id for a in axes],
                                  'component_order':['R','I'] if len(axes)==1 else ['RR','IR','RI','II'],
                                  'source_convention':'nmrglue 0.12 converter.from_varian followed by independent-axis conjugation for negative DFT',
                                  'archived_nmrpipe_to_native_component_signs':[1,-1] if len(axes)==1 else [1,-1,-1,1]},
                    'reference_policy':'No ppm reference inferred; apply explicit reference after FFT',
                    'frequency_direction_policy':'Bare FFT coordinates are mathematical offsets. Physical calibration requires explicit reference frequency_scale=-1 on every physical axis for this retained-pair-sign profile.',
                    'amplitude_policy':'Stored amplitudes; no scan normalization, block scaling, drift or phase correction'}
        if source_hashes is not None:
            imported['source_sha256'] = source_hashes
        return axes, tuple(a.id for a in axes), {'sampling':'fully_sampled', 'import':imported}, []
    raise NMRNextError('HEADER_PLANNING_UNSUPPORTED', f'No sample-free planner for {fmt}; explicitly convert to native HDF5 before planning')


def _stat_records(paths):
    result=[]
    for path in paths:
        stat=path.stat()
        result.append({'path':str(path.resolve()), 'size_bytes':stat.st_size, 'mtime_ns':stat.st_mtime_ns,
                       'device':stat.st_dev, 'inode':stat.st_ino})
    return result


def check_source_stat(header):
    identity=header.source_identity
    records=identity.get('files')
    if not isinstance(records,list) or not records:
        raise NMRNextError('INVALID_HEADER_IDENTITY', 'Expected a file-derived DatasetHeader identity')
    try:
        current=_stat_records([Path(record['path']) for record in records])
    except (OSError,KeyError,TypeError) as exc:
        raise NMRNextError('INPUT_CHANGED', 'Input source changed since header inspection') from exc
    if current!=records:
        raise NMRNextError('INPUT_CHANGED', 'Input source changed since header inspection')


def describe_dataset(path, *, format=None):
    """Read validated binary headers only, without sample decoding or hashing.

    Metadata APIs may cause filesystem readahead. No sample selection is made.
    Identity covers parsed header facts and file stats, not payload contents.
    """
    from . import _format, inspect_dataset
    path=Path(path)
    if not path.exists():
        raise NMRNextError('INPUT_NOT_FOUND', f'Input does not exist: {path}')
    try:
        fmt=_format(format,path,reading=True,header_only=True)
    except OSError as exc:
        raise NMRNextError('INVALID_FILE', f'Cannot inspect input signature: {exc}') from exc
    if fmt=='jcamp-real-1d':
        raise NMRNextError('HEADER_PLANNING_UNSUPPORTED', 'JCAMP header-only planning is not supported; explicitly convert the spectrum to native HDF5, then plan or run that file')
    members={'bruker-raw-1d':['acqus','fid'], 'bruker-raw-2d':['acqus','acqu2s','ser'],
             'varian-raw-1d':['procpar','fid'], 'varian-raw-2d':['procpar','fid']}
    paths=[path/name for name in members[fmt]] if fmt in members else [path]
    try:
        before=_stat_records(paths)
        # This is a layout limit, not an allocation budget. Planning calculates
        # working-array bytes without allocating any of these declared samples.
        head=inspect_dataset(path,format=fmt,max_bytes=(1<<63)-1)
        after=_stat_records(paths)
        if before!=after:
            raise NMRNextError('INPUT_CHANGED', 'Input changed during header inspection')
    except OSError as exc:
        raise NMRNextError('INVALID_FILE', f'Cannot inspect input headers: {exc}') from exc
    axes,bits,metadata,provenance=normalized_description(head)
    digest=hashlib.sha256(json.dumps(head,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    identity={'scope':'header_and_stat_only', 'payload_validated':False, 'format':fmt,
              'header_facts_sha256':digest, 'files':after}
    return DatasetHeader(axes,bits,head['dtype'],metadata,provenance,identity)
