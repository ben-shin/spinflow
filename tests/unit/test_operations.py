import unittest
import sys
import numpy as np
from nmrnext import Axis, Dataset, NMRNextError

try:
    from nmrnext.operations import apply_operation, run_operations
    from nmrnext.planning import plan_operations
except ImportError:
    apply_operation = run_operations = plan_operations = None


def signal(values, *, start=0., step=.25, dtype=np.float64):
    z=np.asarray(values)
    return Dataset(np.stack([z.real,z.imag]).astype(dtype),
                   (Axis('t',len(z),start=start,step=step),),('t',),
                   metadata={'opaque':{'keep':17}})


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(apply_operation, 'The typed operation API is not implemented')

    def test_fft_sign_center_scaling_and_inverse(self):
        d=signal([1,1j,-1,-1j])
        r=apply_operation(d,{'op':'fft','axis':'t'})
        np.testing.assert_allclose(r.data,[[0,0,0,4],[0,0,0,0]],atol=1e-14)
        np.testing.assert_equal(r.coordinates('t'),[-2,-1,0,1])
        self.assertEqual(r.axis('t').domain,'frequency')
        back=apply_operation(r,{'op':'fft','axis':'t','inverse':True})
        np.testing.assert_allclose(back.data,d.data,atol=1e-14)
        self.assertEqual(back.metadata,d.metadata)
        self.assertEqual(d.axis('t').domain,'time')

    def test_time_origin_does_not_trigger_automatic_phase_correction(self):
        for start in (0,.125,.25):
            phase=np.exp(2j*np.pi*start)
            r=apply_operation(signal(phase*np.array([1,1j,-1,-1j]),start=start),{'op':'fft','axis':'t'})
            self.assertAlmostEqual(r.data[0,3],4*phase.real)
            self.assertAlmostEqual(r.data[1,3],4*phase.imag)
            self.assertEqual(r.axis('t').time_origin_seconds,start)

    def test_hypercomplex_axis_pairs_survive_transform_and_transpose(self):
        data=np.zeros((4,2,4)); data[0,0,0]=1;data[1,0,0]=2;data[2,0,0]=3;data[3,0,0]=4
        d=Dataset(data,(Axis('a',2,encoding='states'),Axis('b',4)),('a','b'))
        r=apply_operation(d,{'op':'fft','axis':'b'})
        np.testing.assert_allclose(r.data[:,0,:],np.array([[1]*4,[2]*4,[3]*4,[4]*4]))
        t=apply_operation(r,{'op':'transpose','order':['b','a']})
        self.assertEqual(t.complex_axes,('a','b'))
        self.assertEqual([a.domain for a in t.axes],['frequency','time'])
        restored=apply_operation(t,{'op':'transpose','order':['a','b']})
        np.testing.assert_equal(restored.data,r.data)

    def test_zero_fill_preserves_samples_coordinates_and_acquired_count(self):
        d=signal([1+2j,3+4j],start=.125)
        r=apply_operation(d,{'op':'zero_fill','axis':'t','size':5})
        np.testing.assert_equal(r.data,[[1,3,0,0,0],[2,4,0,0,0]])
        self.assertEqual(r.axis('t').acquired_size,2)
        self.assertEqual(r.axis('t').start,.125)
        with self.assertRaises(NMRNextError):
            apply_operation(d,{'op':'zero_fill','axis':'t','size':1})

    def test_window_and_first_point_weight_are_explicit(self):
        d=signal([1,1,1],step=1)
        r=apply_operation(d,{'op':'window','axis':'t','kind':'exponential','lb_hz':np.log(2)/np.pi,'first_point_scale':.5})
        np.testing.assert_allclose(r.data[0],[.5,.5,.25])
        s=apply_operation(d,{'op':'window','axis':'t','kind':'sine','power':1})
        np.testing.assert_allclose(s.data[0],[0,1,0],atol=1e-15)

    def test_phase_uses_explicit_sign_pivot_and_N_denominator(self):
        d=signal([1,1,1,1])
        r=apply_operation(d,{'op':'phase','axis':'t','p0':90,'p1':360,'pivot':1})
        np.testing.assert_allclose(r.data,[[1,0,-1,0],[0,1,0,-1]],atol=1e-15)

    def test_crop_reverse_coordinates_and_no_input_mutation(self):
        d=signal([1,2,3,4],start=.125)
        r=run_operations(d,[{'op':'crop','axis':'t','start':1,'stop':4},{'op':'reverse','axis':'t'}])
        np.testing.assert_equal(r.data[0],[4,3,2])
        np.testing.assert_allclose(r.coordinates('t'),[.875,.625,.375])
        np.testing.assert_equal(d.data[0],[1,2,3,4])
        self.assertEqual(len(r.provenance),2)

    def test_series_and_missing_samples_cannot_fft(self):
        series=Dataset(np.zeros((2,4)),(Axis('delay',4,role='series'),),('delay',))
        nus=Dataset(np.zeros((2,4)),(Axis('t',4),),('t',),metadata={'sampling':{'mode':'nus','schedule':[0,2]}})
        for d,axis in ((series,'delay'),(nus,'t')):
            with self.assertRaises(NMRNextError):
                apply_operation(d,{'op':'fft','axis':axis})

    def test_all_parameters_are_preflighted_and_invalid_late_op_rejected(self):
        d=signal([1,0,0,0])
        operations=[{'op':'fft','axis':'t'},{'op':'zero_fill','axis':'t','size':8}]
        with self.assertRaises(NMRNextError):
            plan_operations(d,operations)
        with self.assertRaises(NMRNextError):
            run_operations(d,operations)
        self.assertEqual(d.provenance,[])
        for op in ({'op':'magic'}, {'op':'fft','axis':'t','surprise':True},
                   {'op':'phase','axis':'t','p0':float('nan')},
                   {'op':'transpose','order':['t','t']},
                   {'op':'crop','axis':'t','start':-1,'stop':2}):
            with self.assertRaises(NMRNextError):
                apply_operation(d,op)

    def test_memory_and_thread_limits_are_not_silent_noops(self):
        d=signal([1,0,0,0])
        with self.assertRaises(NMRNextError):
            run_operations(d,[{'op':'fft','axis':'t'}],memory_bytes=16)
        for invalid in (True,0,-2,float('nan')):
            with self.assertRaises(NMRNextError):
                plan_operations(d,[],memory_bytes=invalid)
        with self.assertRaises(NMRNextError):
            apply_operation(d,{'op':'fft','axis':'t'},backend='numpy',threads=2)
        with self.assertRaises(NMRNextError):
            apply_operation(d,{'op':'fft','axis':'t'},backend='invented')

    def test_cropped_frequency_grid_cannot_be_inverse_transformed(self):
        f=run_operations(signal([1,0,0,0]),[{'op':'fft','axis':'t'},{'op':'crop','axis':'t','start':1,'stop':4}])
        with self.assertRaises(NMRNextError):
            apply_operation(f,{'op':'fft','axis':'t','inverse':True})

    def test_float32_remains_float32_and_overflow_fails(self):
        d=signal([1,0,0,0],dtype=np.float32)
        self.assertEqual(apply_operation(d,{'op':'fft','axis':'t'}).data.dtype,np.dtype('float32'))
        huge=signal([3e38,3e38,3e38,3e38],dtype=np.float32)
        with self.assertRaises(NMRNextError):
            apply_operation(huge,{'op':'fft','axis':'t'})

    def test_time_crop_retains_acquisition_size_and_origin_through_inverse(self):
        d=signal([1,2,3,4],start=.125)
        cropped=apply_operation(d,{'op':'crop','axis':'t','start':1,'stop':4})
        self.assertEqual(cropped.axis('t').acquired_size,4)
        self.assertEqual(cropped.axis('t').time_origin_seconds,.125)
        back=run_operations(cropped,[{'op':'fft','axis':'t'},{'op':'fft','axis':'t','inverse':True}])
        self.assertEqual(back.axis('t').time_origin_seconds,.125)
        np.testing.assert_allclose(back.coordinates('t'),[.375,.625,.875])

    def test_uncorrected_vendor_filter_cannot_be_silently_transformed(self):
        d=signal([1,0,0,0])
        raw=Dataset(d.data,d.axes,d.complex_axes,metadata={'import':{'digital_filter':{'state':'uncorrected_or_unknown','GRPDLY':3.0}}})
        with self.assertRaises(NMRNextError) as caught:
            apply_operation(raw,{'op':'fft','axis':'t'})
        self.assertEqual(caught.exception.code,'DIGITAL_FILTER_UNRESOLVED')

    def test_native_thread_overflow_is_rejected_by_plan_and_run(self):
        d=signal([1,0,0,0])
        for action in (plan_operations,run_operations):
            with self.subTest(action=action.__name__):
                with self.assertRaises(NMRNextError) as caught:
                    action(d,[{'op':'fft','axis':'t'}],backend='native',threads=sys.maxsize+1)
                self.assertEqual(caught.exception.code,'INVALID_THREADS')

    def test_opaque_import_metadata_is_preserved_and_filter_errors_are_structured(self):
        d=signal([1,0,0,0])
        for imported in ('opaque',['vendor note'],None,{'digital_filter':None}):
            with self.subTest(imported=imported):
                metadata={'import':imported}
                result=apply_operation(Dataset(d.data,d.axes,d.complex_axes,metadata),{'op':'fft','axis':'t'})
                self.assertEqual(result.metadata,metadata)
        for value in ('unknown',[],False,{}):
            with self.subTest(filter=value):
                raw=Dataset(d.data,d.axes,d.complex_axes,{'import':{'digital_filter':value}})
                with self.assertRaises(NMRNextError) as caught:
                    apply_operation(raw,{'op':'fft','axis':'t'})
                self.assertEqual(caught.exception.code,'DIGITAL_FILTER_UNRESOLVED')


if __name__=='__main__':
    unittest.main()
