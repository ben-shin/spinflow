import unittest
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
