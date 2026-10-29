import contextlib
import io
import json
import tempfile
import subprocess
import sys
import os
from pathlib import Path
import unittest
from unittest import mock

try:
    from nmrnext.cli import main
    from nmrnext.demo import generate_demo
except ImportError:
    main = generate_demo = None


class CLITests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(main,'The executable command API is not implemented')

    def invoke(self,args):
        out,err=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            code=main(args)
        return code,out.getvalue(),err.getvalue()

    def test_doctor_reports_actual_runtime_and_experimental_status(self):
        code,out,err=self.invoke(['doctor'])
        self.assertEqual(code,0,err)
        report=json.loads(out)
        self.assertTrue(report['python']['executable'])
        self.assertEqual(report['status'],'experimental')
        self.assertIn('native',report['backends'])

    def test_demo_contains_all_independent_quadratures_and_synthetic_origin(self):
        d=generate_demo(dimensions=2,points=(4,8))
        self.assertEqual(d.data.shape,(4,4,8))
        self.assertEqual(d.complex_axes,('t1','t2'))
        self.assertTrue(d.metadata['synthetic'])
        self.assertTrue(all((d.data[c]!=0).any() for c in range(4)))
        self.assertEqual(d.axis('t1').role,'spectral')

    def test_demo_invalid_dimensions_and_sizes_rejected(self):
        from nmrnext import NMRNextError
        for kwargs in ({'dimensions':3,'points':(4,4,4)}, {'dimensions':2,'points':(4,)},
                       {'dimensions':1,'points':(0,)}, {'dimensions':True,'points':(4,)}):
            with self.assertRaises(NMRNextError):
                generate_demo(**kwargs)

    def test_invalid_recipe_rejected_before_input_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            recipe=Path(tmp)/'bad.json'
            recipe.write_text(json.dumps({'schema_version':'999','input':'missing.h5','output':'out.h5','operations':[]}))
            code,out,err=self.invoke(['run',str(recipe)])
            self.assertEqual(code,2)
            self.assertEqual(json.loads(err)['error']['code'],'UNSUPPORTED_RECIPE_SCHEMA')
            self.assertFalse((Path(tmp)/'out.h5').exists())

    def test_plan_and_rejected_run_never_read_samples(self):
        from nmrnext.io import write_dataset
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            write_dataset(generate_demo(dimensions=1, points=[64]), folder/'raw.h5', format='native')
            recipe={'schema_version':'1','input':'raw.h5','output':'result.h5','backend':'numpy',
                    'operations':[{'op':'fft','axis':'t2'}]}
            path=folder/'recipe.json'
            path.write_text(json.dumps(recipe))
            with mock.patch('nmrnext.io.read_dataset', side_effect=AssertionError('numeric read')):
                code,out,err=self.invoke(['plan',str(path)])
                self.assertEqual(code,0,err)
                self.assertEqual(json.loads(out)['input_validation'],'header_only')
                code,out,err=self.invoke(['run',str(path),'--memory','1MiB'])
                self.assertEqual(code,2)
                self.assertEqual(json.loads(err)['error']['code'],'MEMORY_BUDGET_EXCEEDED')
            self.assertFalse((folder/'result.h5').exists())

    def test_cli_explicit_discard_imaginary_and_schema(self):
        import numpy as np
        from nmrnext.io import read_dataset, write_dataset
        from nmrnext.operations import run_operations
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            source=generate_demo(dimensions=1, points=[64])
            write_dataset(source, folder/'raw.h5', format='native')
            ops=[{'op':'fft','axis':'t2'}, {'op':'discard_imaginary','axis':'t2'}]
            recipe={'schema_version':'1','input':'raw.h5','output':'result.h5','backend':'numpy','operations':ops}
            path=folder/'recipe.json'; path.write_text(json.dumps(recipe))
            code,out,err=self.invoke(['plan',str(path)])
            self.assertEqual(code,0,err)
            self.assertEqual(json.loads(out)['output_complex_axes'],[])
            code,out,err=self.invoke(['run',str(path)])
            self.assertEqual(code,0,err)
            expected=run_operations(source,ops,backend='numpy')
            result=read_dataset(folder/'result.h5')
            np.testing.assert_array_equal(result.data,expected.data)
            self.assertEqual(result.complex_axes,())
        schema=json.loads((Path(__file__).resolve().parents[2]/'schemas/recipe.schema.json').read_text())
        variants=schema['properties']['operations']['items']['oneOf']
        matches=[v for v in variants if v.get('properties',{}).get('op',{}).get('const')=='discard_imaginary']
        self.assertEqual(len(matches),1)
        self.assertIn('axis',matches[0]['required'])

    def test_bad_memory_value_produces_structured_error(self):
        code,out,err=self.invoke(['plan','missing.json','--memory','nan'])
        self.assertEqual(code,2)
        self.assertEqual(json.loads(err)['error']['code'],'INVALID_MEMORY')

    def test_demo_plan_run_inspect_compare_and_conversion_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            code,out,err=self.invoke(['demo',str(folder/'raw.h5'),'--dimensions','2','--points','8','16'])
            self.assertEqual(code,0,err)
            recipe={'schema_version':'1','input':'raw.h5','output':'processed.h5','backend':'numpy',
                    'operations':[{'op':'fft','axis':'t2'},{'op':'fft','axis':'t1'}]}
            (folder/'recipe.json').write_text(json.dumps(recipe))
            for command in ('plan','run'):
                code,out,err=self.invoke([command,str(folder/'recipe.json')])
                self.assertEqual(code,0,err)
                self.assertIsInstance(json.loads(out),dict)
            code,out,err=self.invoke(['inspect',str(folder/'processed.h5'),'--json'])
            self.assertEqual(code,0,err)
            self.assertTrue(json.loads(out))
            code,out,err=self.invoke(['compare',str(folder/'processed.h5'),str(folder/'processed.h5'),'--json'])
            self.assertEqual(code,0,err)
            self.assertTrue(json.loads(out)['match'])
            code,out,err=self.invoke(['convert',str(folder/'processed.h5'),str(folder/'view.ucsf'),'--dry-run'])
            self.assertEqual(code,1,err)
            self.assertFalse(json.loads(out)['ok'])
            self.assertFalse((folder/'view.ucsf').exists())
            code,out,err=self.invoke(['run',str(folder/'recipe.json')])
            self.assertEqual(code,2)
            self.assertTrue(json.loads(err)['error']['code'])

    def test_compare_reports_coordinate_mismatch_without_alignment(self):
        from nmrnext.io import write_dataset
        from nmrnext import Axis,Dataset
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'a.h5';b=Path(tmp)/'b.h5'
            write_dataset(Dataset(np.zeros((1,4)),(Axis('x',4,start=0),)),a)
            write_dataset(Dataset(np.zeros((1,4)),(Axis('x',4,start=.5),)),b)
            code,out,err=self.invoke(['compare',str(a),str(b)])
            self.assertEqual(code,1,err)
            self.assertFalse(json.loads(out)['match'])
            self.assertTrue(json.loads(out)['coordinate_mismatches'])

    def test_compare_near_zero_uses_absolute_reference_floor(self):
        from nmrnext.io import write_dataset
        from nmrnext import Axis,Dataset
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'a.h5';b=Path(tmp)/'b.h5'
            write_dataset(Dataset(np.array([[1e-40]]),(Axis('x',1),)),a)
            write_dataset(Dataset(np.zeros((1,1)),(Axis('x',1),)),b)
            code,out,err=self.invoke(['compare',str(a),str(b),'--rtol','1e-9','--atol','1e-39'])
            self.assertEqual(code,0,err)
            self.assertAlmostEqual(json.loads(out)['metrics']['relative_l2'],1e-10,delta=1e-20)

    def test_installed_cli_runs_in_isolated_python_outside_source_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=subprocess.run([sys.executable,'-I','-m','nmrnext','doctor'],cwd=tmp,
                                  text=True,capture_output=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(json.loads(result.stdout)['backends']['numpy']['available'])

    def test_gui_dispatch_starts_real_workbench_and_exits_cleanly(self):
        script='''from PySide6 import QtCore, QtWidgets
from shiboken6 import isValid
from nmrnext.cli import main
app = QtWidgets.QApplication([])
caller_widget = QtWidgets.QWidget()
QtCore.QTimer.singleShot(250, app.quit)
code = main(['gui'])
from nmrnext.gui import Workbench
assert not any(isinstance(w, Workbench) for w in app.topLevelWidgets())
assert QtWidgets.QApplication.instance() is app
assert isValid(caller_widget)
raise SystemExit(code)
'''
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen')
        result=subprocess.run([sys.executable,'-I','-c',script],env=env,text=True,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_gui_owned_application_disposes_window_on_normal_close(self):
        script='''from PySide6 import QtCore, QtWidgets
from shiboken6 import isValid
from nmrnext import gui
from nmrnext.cli import main
windows = []
class ClosingWorkbench(gui.Workbench):
    def __init__(self):
        super().__init__()
        windows.append(self)
        QtCore.QTimer.singleShot(0, self.close)
gui.Workbench = ClosingWorkbench
assert QtWidgets.QApplication.instance() is None
code = main(['gui'])
assert windows and all(not isValid(w) for w in windows)
raise SystemExit(code)
'''
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen')
        result=subprocess.run([sys.executable,'-I','-c',script],env=env,text=True,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_gui_explicit_exit_waits_for_worker_and_preserves_code(self):
        script='''import threading
from PySide6 import QtCore, QtWidgets
from shiboken6 import isValid
from nmrnext import gui
from nmrnext.cli import main
app = QtWidgets.QApplication([])
started, release, finished = (threading.Event() for _ in range(3))
windows = []
def work():
    started.set()
    assert release.wait(10)
    finished.set()
class WorkingWorkbench(gui.Workbench):
    def __init__(self):
        super().__init__()
        windows.append(self)
        self._start_job(work, lambda value: None, 'Working')
        QtCore.QTimer.singleShot(0, self.request_exit)
    def request_exit(self):
        if not started.is_set():
            QtCore.QTimer.singleShot(0, self.request_exit)
            return
        assert self._busy
        app.exit(7)
gui.Workbench = WorkingWorkbench
app.aboutToQuit.connect(release.set)
code = main(['gui'])
assert finished.is_set()
assert windows and all(not isValid(w) for w in windows)
assert QtWidgets.QApplication.instance() is app
raise SystemExit(code)
'''
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen')
        result=subprocess.run([sys.executable,'-I','-c',script],env=env,text=True,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,7,result.stderr)

    def test_missing_qt_has_structured_remedy_and_headless_commands_still_work(self):
        script='''import importlib.abc,sys
class BlockQt(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.split('.')[0] in ('PySide6','pyqtgraph'): raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0,BlockQt())
from nmrnext.cli import main
assert main(['doctor']) == 0
assert 'PySide6' not in sys.modules
raise SystemExit(main(['gui']))
'''
        result=subprocess.run([sys.executable,'-I','-c',script],text=True,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,2)
        error=json.loads(result.stderr)['error']
        self.assertEqual(error['code'],'GUI_UNAVAILABLE')
        self.assertIn('gui',error['message'].lower())
        expected = 'bash tools/bootstrap.sh --gui' if sys.platform.startswith('linux') else 'tools/bootstrap.ps1 -WithGui' if sys.platform == 'win32' else "pip install -e '.[gui]'"
        self.assertIn(expected,error['message'])

    def test_missing_gui_remedy_matches_linux_and_windows_setup(self):
        for platform, command in (('linux', 'bash tools/bootstrap.sh --gui'),
                                  ('win32', 'tools/bootstrap.ps1 -WithGui')):
            with self.subTest(platform=platform):
                with mock.patch.object(sys, 'platform', platform), mock.patch.dict(sys.modules, {'nmrnext.gui': None}):
                    code, out, err = self.invoke(['gui'])
                self.assertEqual(code, 2)
                self.assertEqual(out, '')
                error = json.loads(err)['error']
                self.assertEqual(error['code'], 'GUI_UNAVAILABLE')
                self.assertIn(command, error['message'])

    def test_native_commit_is_recorded_in_processing_provenance(self):
        from nmrnext.operations import apply_operation
        d=generate_demo(dimensions=1,points=(8,))
        result=apply_operation(d,{'op':'fft','axis':'t2'},backend='native')
        self.assertEqual(result.provenance[-1]['backend']['pocketfft_commit'],
                         '4d865cba24359b05b37da4e75aee24498c3b78e7')

    def test_plan_and_run_report_oversized_threads_without_traceback_or_output(self):
        from nmrnext.io import write_dataset
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            write_dataset(generate_demo(dimensions=1,points=(8,)),folder/'raw.h5')
            recipe={'schema_version':'1','input':'raw.h5','output':'out.h5','backend':'native',
                    'threads':sys.maxsize+1,'operations':[{'op':'fft','axis':'t2'}]}
            path=folder/'recipe.json';path.write_text(json.dumps(recipe))
            for command in ('plan','run'):
                with self.subTest(command=command):
                    result=subprocess.run([sys.executable,'-I','-m','nmrnext',command,str(path)],
                                          text=True,capture_output=True,timeout=30)
                    self.assertEqual(result.returncode,2,result.stderr)
                    self.assertEqual(json.loads(result.stderr)['error']['code'],'INVALID_THREADS')
                    self.assertNotIn('Traceback',result.stderr)
                    self.assertFalse((folder/'out.h5').exists())


if __name__=='__main__': unittest.main()
