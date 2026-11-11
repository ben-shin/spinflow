"""Capture four real Workbench states on an isolated X11 desktop using synthetic inputs."""
import csv
import re
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time
import subprocess
import shutil
from zipfile import ZipFile


REVIEWED_GUI_SHA256 = "c5dde2dddbd5543257cc33418bed582056db68433be865b4f63525ac7b740b2f"
REVIEWED_VIEWPORT_SHA256 = "0dfc4a63c634c2b5d72521bd8a21ccc3631081def5368ee4692322c0dc5f5d8e"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()



def check_provenance(actual, source_prefix):
    expected_operations = [
        {"op": "fft", "axis": "n", "inverse": False, "version": "1"},
        {"op": "phase", "axis": "n", "p0": 90.0, "p1": 0.0, "pivot": 0.0, "version": "1"},
    ]
    prefix_size = len(source_prefix)
    assert len(actual) == prefix_size + len(expected_operations)
    assert actual[:prefix_size] == source_prefix
    assert [entry["operation"] for entry in actual[prefix_size:]] == expected_operations


def main(checkout, wheel, root):
    assert re.fullmatch(r"[0-9a-f]{64}", REVIEWED_GUI_SHA256), "Bind the reviewed Batch presentation source first"
    checkout, wheel, root = map(Path, (checkout, wheel, root))
    assert __debug__ and sys.flags.isolated and sys.dont_write_bytecode
    assert checkout.is_absolute() and not Path.cwd().resolve().is_relative_to(checkout)
    assert not Path(sys.prefix).resolve().is_relative_to(checkout)
    assert os.environ["QT_QPA_PLATFORM"] == "xcb"
    assert all(os.environ.get(k) == "1" for k in
               ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"))
    commit = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    assert commit == os.environ["GITHUB_SHA"] and re.fullmatch(r"[0-9a-f]{40}", commit)
    files = subprocess.check_output(["git", "-C", str(checkout), "ls-tree", "-r", "--name-only",
                                     commit, "src/nmrnext"], text=True).splitlines()
    expected = {path.removeprefix("src/"): path for path in files if path.endswith(".py")}
    dist = importlib.metadata.distribution("spinflow")
    pkg = Path(dist.locate_file("nmrnext")).resolve(strict=True)
    assert pkg.is_relative_to(Path(sys.prefix).resolve()) and dist.version == "0.4.0b1"
    with ZipFile(wheel) as archive:
        members = [name for name in archive.namelist() if name.startswith("nmrnext/") and not name.endswith("/")]
        assert len(members) == len(set(members))
        assert {name for name in members if name.endswith(".py")} == set(expected)
        for name,path in expected.items():
            committed = subprocess.check_output(["git","-C",str(checkout),"show",f"{commit}:{path}"])
            assert archive.read(name) == committed
        pins = {name.removeprefix("nmrnext/"):hashlib.sha256(archive.read(name)).hexdigest() for name in members}
    def verify():
        for path, digest in pins.items():
            target = (pkg/path).resolve(strict=True)
            assert target.is_relative_to(pkg) and sha(target) == digest
        assert pins["gui.py"] == REVIEWED_GUI_SHA256
        assert pins["_viewport.py"] == REVIEWED_VIEWPORT_SHA256
    verify()
    from PySide6 import QtCore, QtTest, QtWidgets
    import numpy as np
    import nmrnext
    from nmrnext import _native
    from nmrnext.analysis import dataset_digest
    from nmrnext.assignments import load_assignment_template, save_assignment_template
    from nmrnext.batch import load_batch_manifest, save_batch_manifest
    from nmrnext.gui import Workbench, _PhysicalContourPathItem
    from nmrnext.io import read_dataset, write_dataset
    from nmrnext.model import Axis, Dataset
    from nmrnext.operations import quadrature_index_orientation
    assert nmrnext.__version__==_native.__version__==dist.version and Path(nmrnext.__file__).parent==pkg
    assert Path(_native.__file__).resolve().parent==pkg
    def origins():
        present={}
        for name,m in sys.modules.items():
            if (name=="nmrnext" or name.startswith("nmrnext.")) and getattr(m,"__file__",None):
                path=Path(m.__file__).resolve()
                rel=path.relative_to(pkg).as_posix()
                assert sha(path)==pins[rel]
                present[name]=rel
        return present
    origins()
    assert root.is_absolute() and root.resolve()==root and not root.exists()
    root.mkdir(mode=0o700)
    os.chdir(root)
    tag="Synthetic demonstration"
    raw=np.zeros((4,65,64),dtype=np.float32)
    for y,x,amp in ((15,16,4.),(18,49,-5.),(48,15,6.),(48,47,-3.)):
        ramp=np.array([1,2,3,2,1],dtype=np.float32)/3
        raw[0,y-2:y+3,x-2:x+3]=amp*np.outer(ramp,ramp)
    raw[0,32,32]=7
    for ch in range(1,4): raw[ch,4,ch]=ch
    # Design in the frequency domain; synthesize the n-axis traces by inverting
    # the declared centered forward transform and +90 degree phase. Channel
    # bits [h,n] pair n channels (0,2) and (1,3); h is already in Hz.
    traces=np.empty_like(raw)
    for lo,hi in ((0,2),(1,3)):
        spectrum=raw[lo].astype(np.float64)+1j*raw[hi]
        trace=np.fft.ifft(np.fft.ifftshift(-1j*spectrum,axes=1),axis=1)
        traces[lo],traces[hi]=trace.real,trace.imag
    time_axis=Axis("n",64,unit="s",step=1/192,nucleus="15N",encoding="complex")
    axes=(Axis("h",65,domain="frequency",unit="Hz",step=-2,nucleus="1H",encoding="complex"),
          time_axis)
    source=Dataset(traces,axes,("h","n"),metadata={"description":tag})
    items=[]
    for name in ("sample-A","sample-B"):
        path=Path(name+".nmrh5")
        write_dataset(source,path,format="native")
        items.append({"item_id":name,"input":str(path),"output":name+"-result.nmrh5","axis_map":{"h":"h","n":"n"}})
    app=QtWidgets.QApplication([])
    app.setApplicationName("SpinFlow");app.setStyle("Fusion")  # matches installed GUI main()
    assert app.platformName()=="xcb" and len(app.screens())==1
    win=Workbench()
    screen=app.primaryScreen()
    images=[]
    deadline=time.monotonic()+85
    def wait(predicate):
        while not predicate():
            if time.monotonic()>=deadline: raise TimeoutError("GUI condition did not complete")
            QtTest.QTest.qWait(25)
    def click(button):
        assert button.isEnabled() and button.isVisible()
        QtTest.QTest.mouseClick(button,QtCore.Qt.MouseButton.LeftButton)
    def idle():
        return not win._busy and win._thread is None
    def capture(name):
        wait(lambda: idle() and win._viewport_pending is None)
        app.processEvents();win.repaint();app.sync()
        frame=win.frameGeometry()
        assert screen.geometry().contains(frame) and win.isActiveWindow() and win.windowHandle().isExposed()
        assert not app.activeModalWidget() and tag in win.dataset_title.text()
        pix=screen.grabWindow(0)
        assert not pix.isNull() and pix.devicePixelRatio()==1
        assert pix.size()==screen.geometry().size()==QtCore.QSize(1600,1200)
        target=Path(name)
        assert not target.exists() and pix.save(str(target),"PNG")
        images.append({"name":name,"sha256":sha(target),"screen":screen.geometry().getRect(),
                       "window_frame":frame.getRect(),"window_id":int(win.winId()),"dpr":pix.devicePixelRatio()})
    try:
        ops=[{"op":"fft","axis":"n","inverse":False},
             {"op":"phase","axis":"n","p0":90.0,"p1":0.0,"pivot":0.0}]
        j=np.arange(64)
        pole=0.96*np.exp(2j*np.pi*5/64)
        signal=pole**j
        fid=Dataset(np.array([signal.real,signal.imag],dtype=np.float32),(time_axis,),("n",),
                    metadata={"description":tag})
        write_dataset(fid,root/"fid-input.nmrh5",format="native")
        win.load_dataset_now(fid,tag+" · damped 1D FID")
        win.showMaximized();win.activateWindow()
        wait(lambda: win.isActiveWindow() and win.windowHandle().isExposed())
        tabs=win.findChild(QtWidgets.QTabWidget)
        assert tabs.tabText(0)=="Processing"
        assert win.backend_combo.currentData()=="native"
        win.set_ordered_recipe_now(ops)
        assert win.current.axes[0].domain=="time"
        def fid_visible():
            trace=win._viewport_item
            if trace not in win.plot.listDataItems() or not trace.isVisible():
                return False
            _, values=trace.getData()
            return values is not None and len(values)>1 and np.ptp(values)>0
        wait(fid_visible)
        capture("01-fid-processing.png")
        click(win.process_button)
        wait(lambda: idle() and win.last_operations==ops)
        # Finite geometric series gives the expected complex 1D spectrum.
        poles=pole*np.exp(-2j*np.pi*(j-32)/64)
        expected_1d=1j*(1-poles**64)/(1-poles)
        write_dataset(win.current,root/"fid-result.nmrh5",format="native")
        observed=win.current.data
        assert observed.shape==(2,64) and observed.dtype==np.float32
        assert np.max(np.abs(observed[0]+1j*observed[1]-expected_1d))/np.max(abs(expected_1d))<=2e-6
        win.load_dataset_now(read_dataset(items[0]["input"]),tag+" · 2D synthetic series")
        win.set_ordered_recipe_now(ops)
        click(win.process_button)
        wait(lambda: idle() and win.last_operations==ops)
        applied=win.current
        assert applied.data.dtype==raw.dtype and applied.data.shape==raw.shape
        assert tuple(applied.complex_axes)==("h","n")
        assert applied.axes[0]==axes[0] and applied.axes[1].domain=="frequency"
        assert (applied.axes[1].unit,applied.axes[1].size,applied.axes[1].start,applied.axes[1].step)==("Hz",64,-96,3)
        check_provenance(applied.provenance, source.provenance)
        for actual,expected in zip(applied.data,raw):
            assert np.max(abs(actual-expected))/np.max(abs(expected))<=2e-6
        write_dataset(applied,root/"applied-reference.nmrh5",format="native")
        np.save(root/"designed-spectrum.npy",raw,allow_pickle=False)
        saved=Path(win.export_workflow_now(root/"Applied recipe"))
        recipe=json.loads(saved.read_bytes())
        assert recipe["backend"]=="native" and recipe["operations"]==ops
        snapshot=read_dataset(saved.parent/recipe["input"])
        assert snapshot.data.tobytes()==traces.tobytes() and snapshot.to_metadata()==source.to_metadata()
        assert win.plot_mode_combo.currentData()=="contours" and win.contour_sign_combo.currentData()=="both"
        def visible_signs():
            bounds=win.plot.getPlotItem().getViewBox().sceneBoundingRect()
            return {int(np.sign(curve.level)) for curve in win._viewport_contours
                    if isinstance(curve,_PhysicalContourPathItem) and curve.scene() is win.plot.scene()
                    and curve.isVisible() and curve.path().elementCount()>1
                    and not curve.path().boundingRect().isEmpty()
                    and bounds.intersects(curve.sceneBoundingRect())}
        wait(lambda: visible_signs()=={-1,1})
        capture("02-signed-contours.png")
        view=win.plot.getPlotItem().getViewBox()
        viewport=win.plot.viewport()
        rect=view.sceneBoundingRect()
        def position(fraction):
            return win.plot.mapFromScene(rect.topLeft()+QtCore.QPointF(rect.width()*fraction,rect.height()*fraction))
        before=np.asarray(view.viewRange())
        button=QtCore.Qt.MouseButton.RightButton
        QtTest.QTest.mousePress(viewport,button,pos=position(.2))
        QtTest.QTest.mouseMove(viewport,position(.45))
        QtTest.QTest.mouseMove(viewport,position(.8))
        wait(lambda: view._zoom_box is not None and view._zoom_box.isVisible() and view._zoom_box.rect().width()>0)
        assert view._zoom_box.rect().height()>0 and view._zoom_box.pen().color().lightness()>180
        capture("04-right-drag-box.png")
        QtTest.QTest.mouseRelease(viewport,button,pos=position(.8))
        wait(lambda: view._zoom_box is None)
        after=np.asarray(view.viewRange())
        assert np.all(after[:,0]>before[:,0]) and np.all(after[:,1]<before[:,1])
        bounds={"h":[31,33],"n":[31,33]}
        template_path=Path("Synthetic labels.json")
        template={"schema_version":"1","kind":"assignment_template","analysis_channel":0,
                  "reference":{"axes":[a.to_dict() for a in applied.axes],"complex_axes":["h","n"],
                               "dataset_digest":dataset_digest(applied),
                               "quadrature_index_orientation":quadrature_index_orientation(applied)},
                  "assignments":[{"assignment_id":"center","label":"Center signal","sign":"positive",
                                  "unit":"points","threshold":1.0,"search_bounds":bounds,"integration_bounds":bounds}]}
        save_assignment_template(template,template_path)
        planpath=Path("Synthetic series.json")
        save_batch_manifest({"schema_version":"1","kind":"batch_manifest","operations":recipe["operations"],"backend":recipe["backend"],
                             "threads":1,"memory_bytes":recipe["memory_bytes"],"assignment_template":str(template_path),"items":items},planpath)
        tabs.setCurrentIndex(win.batch_tab_index)
        # Use the installed Open manifest loader's acceptance callback and service
        # readers. File chooser interaction is outside this smoke's scope.
        win._batch_accept_manifest(planpath,load_batch_manifest(planpath),
                                   load_assignment_template(template_path),template_path)
        click(win.batch_plan_button)
        wait(lambda: idle() and win._batch_plan_result is not None)
        assert not win._batch_plan_result["refusals"]
        click(win.batch_apply_button)
        wait(lambda: idle() and win._batch_csv_path is not None)
        with win._batch_csv_path.open(encoding="utf-8-sig",newline="") as f:
            rows=list(csv.DictReader(f))
        assert len(rows)==2
        for i,(row,item) in enumerate(zip(rows,items)):
            assert row["item_id"]==item["item_id"] and row["assignment_id"]=="center"
            assert row["status"]=="measured" and int(row["candidate_count"])==1
            assert float(row["height"])==7 and float(row["volume"])==42
            assert row["volume_unit"]=="intensity*Hz^2" and int(row["analysis_channel"])==0
            assert win.batch_items_table.item(i,4).text().startswith("completed")
            back=read_dataset(item["output"])
            assert back.data.dtype==raw.dtype and back.data.shape==raw.shape
            assert back.data.tobytes()==applied.data.tobytes() and back.to_metadata()==applied.to_metadata()
        # Preserve the actual last committed generation under stable export names.
        csv_path=win._batch_csv_path.resolve(strict=True)
        assert csv_path.parent==root and re.fullmatch(r"Synthetic series\.batch-results(?:\.[0-9]{4,})?\.csv",csv_path.name)
        json_path=csv_path.with_suffix(".json").resolve(strict=True)
        assert json_path.is_relative_to(root)
        result=json.loads(json_path.read_bytes())
        assert result["kind"]=="batch_results" and len(result["rows"])==2
        assert [(r["item_id"],r["assignment_id"],r["status"],r["height"],r["volume"]) for r in result["rows"]]==[
            (item["item_id"],"center","measured",7,42) for item in items]
        for origin,name in ((csv_path,"batch-results.csv"),(json_path,"batch-results.json")):
            with origin.open("rb") as inp, (root/name).open("xb") as out:
                shutil.copyfileobj(inp,out)
            assert sha(origin)==sha(root/name)
        wait(lambda: idle() and not win._batch_running
             and win._viewport_pending is None
             and win.batch_progress_label.text().startswith("Batch finished")
             and win.status.text()==win.batch_progress_label.text())
        capture("03-batch-results.png")
        actual=origins();verify()
        assert win.close()
        wait(lambda: not win.isVisible())
        proof_paths=["fid-input.nmrh5","fid-result.nmrh5","designed-spectrum.npy","applied-reference.nmrh5",
                     "Applied recipe/source.nmrh5","Applied recipe/workflow.json",template_path.name,planpath.name,
                     "batch-results.json","batch-results.csv"]
        proof_paths.extend(item[key] for item in items for key in ("input","output"))
        receipt={"proof_sha256":{name:sha(root/name) for name in proof_paths},"scope":"Automated synthetic Linux CI X11 desktop; images require visual review",
                 "commit":commit,"version":dist.version,"wheel_sha256":sha(wheel),"native_sha256":sha(_native.__file__),
                 "runtime":{name:importlib.metadata.version(name) for name in ("numpy","PySide6-Essentials","pyqtgraph","h5py")},
                 "python":sys.version.split()[0],"qt":QtCore.qVersion(),"gui_sha256":pins["gui.py"],
                 "viewport_sha256":pins["_viewport.py"],
                 "wheel_members_verified":len(pins),"right_drag_ranges":[before.tolist(),after.tolist()],"platform":app.platformName(),
                 "style":app.style().objectName(),"screens":images,"modules":actual,"csv_sha256":sha(win._batch_csv_path),
                 "applied_workflow_sha256":sha(saved),"input_sample_sha256":hashlib.sha256(traces.tobytes()).hexdigest(),
                 "measured_rows":2,"height":7,"volume":42,"all_four_component_bytes_verified":True,
                 "normal_workbench_close":True}
        with Path("receipt.json").open("x",encoding="utf-8") as f:json.dump(receipt,f,indent=2)
    finally:
        if win._thread is not None:
            if win._batch_stop_event is not None:
                win._batch_stop_event.set()
            win._thread.quit()
            if not win._thread.wait(3000):
                print("Workbench worker did not stop during bounded cleanup",file=sys.stderr,flush=True)
                os._exit(41)
            app.processEvents()
        win.close();win.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(win,QtCore.QEvent.Type.DeferredDelete)
        app.quit()

if __name__=="__main__":
    main(*sys.argv[1:])
