import numpy as np
import pytest

from nmrnext.errors import NMRNextError
from nmrnext.io.jcamp import inspect_jcamp, read_jcamp


TEXT = """##TITLE= Synthetic spectrum
##JCAMP-DX= 5.00
##DATA TYPE= NMR SPECTRUM
##DATA CLASS= XYDATA
##.OBSERVE FREQUENCY= 400
##.OBSERVE NUCLEUS= 1H
##XUNITS= HZ
##YUNITS= ARBITRARY UNITS
##XFACTOR= 2
##YFACTOR= .25
##FIRSTX= 10
##LASTX= 4
##NPOINTS= 4
##FIRSTY= .5
##CUSTOM= retained
##XYDATA= (X++(Y..Y))
5 2 4
3 -8 12
##END=
"""


def test_literal_grid_and_values_preserve_unknown_reference(tmp_path):
    path = tmp_path/'spectrum.jdx'
    path.write_text(TEXT, encoding='ascii')
    header = inspect_jcamp(path, 4096)
    result = read_jcamp(path, header)
    np.testing.assert_array_equal(result.data, [[.5, 1., -2., 3.]])
    np.testing.assert_array_equal(result.coordinates('a0', 'Hz'), [10, 8, 6, 4])
    assert result.axes[0].reference_ppm is None
    assert result.metadata['import']['source_text'] == path.read_bytes().decode('ascii')
    assert result.complex_axes == ()


@pytest.mark.parametrize('modified', [
    TEXT.replace('##XUNITS= HZ', '##XUNITS= PPM'),
    TEXT.replace('##DATA TYPE= NMR SPECTRUM', '##DATA TYPE= NMR FID'),
    TEXT.replace('3 -8 12', '4 -8 12'),
    TEXT.replace('3 -8 12', '3 -8'),
    TEXT.replace('5 2 4', '5 A B'),
    TEXT.replace('##END=', '##NTUPLES= NMR SPECTRUM\n##END='),
    TEXT.replace('##END=', '##END=\n##TITLE= second'),
])
def test_unsupported_or_invalid_inputs_are_rejected(tmp_path, modified):
    path = tmp_path/'spectrum.jdx'
    path.write_text(modified, encoding='ascii')
    with pytest.raises(NMRNextError):
        inspect_jcamp(path, 4096)


def test_memory_limit_and_changed_input_are_rejected(tmp_path):
    path = tmp_path/'spectrum.jdx'
    path.write_text(TEXT, encoding='ascii')
    with pytest.raises(NMRNextError):
        inspect_jcamp(path, 24)
    header = inspect_jcamp(path, 4096)
    path.write_text(TEXT.replace('CUSTOM= retained', 'CUSTOM= changed'), encoding='ascii')
    with pytest.raises(NMRNextError, match='changed'):
        read_jcamp(path, header)


def test_auto_dispatch_and_read_only_export_preflight(tmp_path):
    from nmrnext.io import inspect_dataset, read_dataset, export_preflight
    path = tmp_path/'spectrum.jdx'
    path.write_text(TEXT, encoding='ascii')
    assert inspect_dataset(path)['format'] == 'jcamp-real-1d'
    dataset = read_dataset(path)
    np.testing.assert_array_equal(dataset.data, [[.5, 1., -2., 3.]])
    report = export_preflight(dataset, 'jcamp-real-1d')
    assert not report['ok'] and 'read_only_profile' in report['errors']


def _grid_text(first, last, factor, rows):
    return (TEXT.replace('##FIRSTX= 10', f'##FIRSTX= {first}')
            .replace('##LASTX= 4', f'##LASTX= {last}')
            .replace('##XFACTOR= 2', f'##XFACTOR= {factor}')
            .replace('5 2 4\n3 -8 12', rows))


@pytest.mark.parametrize('first,last,factor,rows', [
    ('1000000000', '1000000003', '1', '1000000000 2 4\n1000000003 -8 12'),
    ('1000000003', '1000000000', '1', '1000000003 2 4\n1000000000 -8 12'),
])
def test_large_origin_one_point_row_displacement_rejected(tmp_path, first, last, factor, rows):
    path = tmp_path/'bad.jdx'
    path.write_text(_grid_text(first, last, factor, rows), encoding='ascii')
    with pytest.raises(NMRNextError, match='inconsistent'):
        inspect_jcamp(path, 4096)


@pytest.mark.parametrize('first,last,factor,rows', [
    ('0', '1', '2', '0 2 4\n0.3333333 -8 12'),
    ('1', '0', '2', '0.5 2 4\n0.1666667 -8 12'),
])
def test_rounded_scaled_row_coordinates_within_subpoint_tolerance(tmp_path, first, last, factor, rows):
    path = tmp_path/'rounded.jdx'
    path.write_text(_grid_text(first, last, factor, rows), encoding='ascii')
    assert inspect_jcamp(path, 4096)['shape'] == [1, 4]


def test_grid_unresolvable_in_float64_is_rejected(tmp_path):
    path = tmp_path/'unresolvable.jdx'
    path.write_text(_grid_text('1e16', '10000000000000004', '1',
                               '1e16 2 4\n10000000000000002 -8 12'), encoding='ascii')
    with pytest.raises(NMRNextError, match='resolv'):
        inspect_jcamp(path, 4096)
