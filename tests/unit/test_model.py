import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

try:
    from nmrnext.model import Axis, Dataset
    from nmrnext.errors import NMRNextError
except ImportError:
    Axis = Dataset = NMRNextError = None


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(Axis, 'The validated axis/dataset API is not implemented')

    def test_invalid_sizes_duplicate_axes_and_components_rejected(self):
        for bad in (0, -1, True, 1.5):
            with self.assertRaises(NMRNextError):
                Axis('x', bad)
        a = Axis('x', 2)
        for data, axes, bits in ((np.zeros((1,2,2)), (a,a), ()),
                                 (np.zeros((1,2)), (a,), ('x',)),
                                 (np.zeros((2,2)), (a,), ('missing',))):
            with self.assertRaises(NMRNextError):
                Dataset(data, axes, bits)

    def test_metadata_input_and_array_cannot_mutate_dataset(self):
        raw = np.array([[1.,2.],[3.,4.]])
        meta = {'vendor': {'unknown': [12]}}
        d = Dataset(raw, (Axis('x',2),), ('x',), metadata=meta)
        raw[0,0] = 77
        meta['vendor']['unknown'][0] = 99
        exposed = d.metadata
        exposed['vendor']['unknown'][0] = 100
        self.assertEqual(d.data[0,0],1)
        self.assertEqual(d.metadata['vendor']['unknown'],[12])
        with self.assertRaises(ValueError):
            d.data[0,0] = 33

    def test_time_coordinates_and_explicit_reference_ppm(self):
        a = Axis('h',3,domain='frequency',unit='Hz',start=-10,step=5,
                 observation_mhz=500,reference_ppm=4.7,reference_hz=0)
        d = Dataset(np.zeros((1,3)),(a,))
        np.testing.assert_allclose(d.coordinates('h'),[-10,-5,0])
        np.testing.assert_allclose(d.coordinates('h','ppm'),[4.68,4.69,4.7])
        with self.assertRaises(NMRNextError):
            Dataset(np.zeros((1,3)),(Axis('x',3),)).coordinates('x','ppm')

    def test_serialization_preserves_quadrature_and_opaque_fields(self):
        d = Dataset(np.arange(24,dtype=np.float32).reshape(4,2,3),
                    (Axis('h1',2,encoding='states'),Axis('h2',3)),('h1','h2'),
                    metadata={'opaque':{'field':'?'}},provenance=[{'source':'synthetic'}])
        e = Dataset.from_metadata(d.data,d.to_metadata())
        np.testing.assert_array_equal(e.data,d.data)
        self.assertEqual(e.to_metadata(),d.to_metadata())
        self.assertEqual(e.complex_axes,('h1','h2'))
        bad=d.to_metadata();bad['schema_version']='99'
        with self.assertRaises(NMRNextError):
            Dataset.from_metadata(d.data,bad)

    def test_nonfinite_coordinates_and_data_are_rejected(self):
        for key in ('start','step','time_origin_seconds','reference_hz'):
            with self.assertRaises(NMRNextError):
                Axis('x',2,**{key:float('nan')})
        with self.assertRaises(NMRNextError):
            Dataset(np.array([[1.,float('inf')]]),(Axis('x',2),))
        with self.assertRaises(NMRNextError):
            Dataset(np.array([[1,2]]),(Axis('x',2),))

    def test_optional_packer_errors_follow_sample_validation_and_precede_metadata(self):
        raw = np.arange(12, dtype=np.float32).reshape(1, 4, 3).transpose(0, 2, 1)
        axes = (Axis("x", 3), Axis("y", 4))
        def allocation_failure(array):
            raise MemoryError("sample packing")
        module = SimpleNamespace(_try_pack_transposed=allocation_failure)
        with patch.dict(sys.modules, {"nmrnext._native": module}):
            for value in (np.nan, np.inf, -np.inf):
                raw[0, -1, -1] = value
                with self.assertRaises(NMRNextError) as error:
                    Dataset(raw, axes, metadata=object())
                self.assertEqual(error.exception.code, "NONFINITE_DATA")
            with self.assertRaises(NMRNextError) as error:
                Dataset(raw, axes[::-1])
            self.assertEqual(error.exception.code, "INVALID_SHAPE")
            with self.assertRaises(NMRNextError) as error:
                Dataset(raw.astype(raw.dtype.newbyteorder("S")), axes)
            self.assertEqual(error.exception.code, "INVALID_DTYPE")
            raw[0, -1, -1] = 5
            with self.assertRaisesRegex(MemoryError, "sample packing"):
                Dataset(raw, axes, metadata=object())

    def test_optional_packer_capability_is_checked_when_module_becomes_available(self):
        raw = np.arange(12, dtype=np.float64).reshape(1, 4, 3).transpose(0, 2, 1)
        axes = (Axis("x", 3), Axis("y", 4))
        expected = raw.tobytes(order="C")
        with patch.dict(sys.modules, {"nmrnext._native": SimpleNamespace()}):
            self.assertEqual(Dataset(raw, axes).data.tobytes(), expected)
            def decline(array):
                return None
            def cancel(array):
                raise KeyboardInterrupt
            module = sys.modules["nmrnext._native"]
            module._try_pack_transposed = decline
            self.assertEqual(Dataset(raw, axes).data.tobytes(), expected)
            module._try_pack_transposed = cancel
            with self.assertRaises(KeyboardInterrupt):
                Dataset(raw, axes)

    def test_cold_dataset_does_not_attempt_native_import(self):
        program = """
import sys
class ForbidNative:
    def find_spec(self, fullname, path, target=None):
        if fullname == 'nmrnext._native':
            raise AssertionError('cold constructor requested a native extension')
sys.meta_path.insert(0, ForbidNative())
import numpy as np
from nmrnext import Axis, Dataset
assert 'nmrnext._native' not in sys.modules
raw = np.arange(12, dtype=np.float32).reshape(1, 4, 3).transpose(0, 2, 1)
result = Dataset(raw, (Axis('x', 3), Axis('y', 4)))
assert result.data.tobytes() == raw.tobytes(order='C')
assert 'nmrnext._native' not in sys.modules
"""
        command = "import sys\nsys.path[:] = " + repr(sys.path) + "\n" + program
        completed = subprocess.run([sys.executable, "-c", command], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_malformed_enum_and_component_inputs_are_structured_errors(self):
        for field in ('role','domain','encoding'):
            with self.assertRaises(NMRNextError):
                Axis('x',2,**{field:[]})
        with self.assertRaises(NMRNextError):
            Dataset(np.zeros((1,2)),(Axis('x',2),),complex_axes=None)
        with self.assertRaises(NMRNextError):
            Dataset.from_metadata(np.zeros((1,2)),{'schema_version':'1','axes':None,
                'complex_axes':[],'metadata':{},'provenance':[]})


if __name__ == '__main__':
    unittest.main()
