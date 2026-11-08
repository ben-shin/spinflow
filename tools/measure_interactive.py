"""Measure fixed installed Qt endpoints; no product imports in the controller.

This engineering tool requires explicit execution and existing Linux installs.
It does not supply scientific or human acceptance. See docs/stage21-protocol.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import time
import traceback


THREAD_ENV = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
ENDPOINTS = ("warm_full_processing", "process_to_painted_current", "zoom_to_paint", "pan_to_paint")
WORKER_SECONDS = 180
COMPARISON_SECONDS = 60
TERMINATION_GRACE_SECONDS = 5
EXPECTED_SHAPES = ((4,166,1500),(4,166,4096),(4,166,4096),(4,166,4096),(4,166,4096),
                   (2,166,4096),(2,4096,166),(2,4096,166),(2,4096,2048),(2,4096,2048),
                   (2,4096,2048),(2,4096,2048),(1,4096,2048),(1,2048,4096))


def pin(path):
    path = Path(path)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest}


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


class Journal:
    """An exclusively created, flushed raw record survives subsequent failure."""
    def __init__(self, path, **context):
        self.path = Path(path)
        self.stream = self.path.open("x", encoding="utf-8", newline="\n")
        self.context = context

    def emit(self, event, **fields):
        if os.name == "posix":
            import resource
        self.context.update({key: fields[key] for key in ("role", "endpoint") if key in fields})
        record = {"event": event, **self.context, **fields, "monotonic": time.perf_counter(),
                  "process_lifetime_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss *
                      (1024 if sys.platform.startswith("linux") else 1)} if os.name == "posix" else {
                          "event": event, **self.context, **fields, "monotonic": time.perf_counter(),
                          "process_lifetime_peak_rss_bytes": None}
        self.stream.write(json.dumps(record, allow_nan=False)+"\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def close(self):
        self.stream.close()


def read_journal(path):
    records = []
    if Path(path).is_file():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"event": "INCOMPLETE_RAW_JOURNAL_LINE", "raw": line})
    return records


def guarded_worker(config, function):
    journal = Journal(config["journal"], lane=config.get("lane"), pair=config.get("pair"))
    try:
        journal.emit("worker_started", endpoint="imports_and_setup")
        result = function(config, journal)
        journal.emit("worker_complete", endpoint="final_result")
        return result
    except BaseException as exc:
        journal.emit("worker_failed", error=repr(exc), traceback=traceback.format_exc())
        raise
    finally:
        journal.close()


def _owned_group_members(group):
    """Linux metadata census of only the freshly owned process group/session."""
    members = []
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        try:
            raw = (directory/"stat").read_bytes()
        except (FileNotFoundError, ProcessLookupError):
            continue
        fields = raw[raw.rfind(b")")+2:].split()
        if int(fields[2]) != group:
            continue
        if int(fields[3]) != group:
            raise RuntimeError("Owned process group/session identity mismatch")
        members.append({"pid": int(directory.name), "state": fields[0].decode("ascii"),
                        "pgrp": int(fields[2]), "session": int(fields[3]),
                        "start_ticks": int(fields[19])})
    return members


def run_bounded(command, stem, cwd, env, *, timeout, grace=TERMINATION_GRACE_SECONDS, role="worker"):
    """Bound the owned group's live lifetime; reap its direct leader without blocking."""
    import resource
    started = time.perf_counter()
    floor = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    stdout_path, stderr_path = Path(str(stem)+".stdout.json"), Path(str(stem)+".stderr.txt")
    usage = status = None
    timed_out = False
    signals = []
    members = None
    wait_error = observation_error = None
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        child = subprocess.Popen(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
                                 start_new_session=True)
        assert os.getpgid(child.pid) == child.pid and os.getsid(child.pid) == child.pid, "Owned session not established"
        def reap_until(deadline):
            nonlocal status, usage, members, wait_error, observation_error
            while True:
                if status is None:
                    try:
                        pid, result, observed = os.wait4(child.pid, os.WNOHANG)
                        if pid:
                            status, usage = result, observed
                            child.returncode = os.waitstatus_to_exitcode(status)
                    except InterruptedError:
                        pass
                    except OSError as exc:
                        wait_error = repr(exc)
                try:
                    members = _owned_group_members(child.pid)
                except Exception as exc:
                    observation_error = repr(exc)
                    members = None
                if status is not None and members is not None and not any(p["state"] not in ("Z", "X") for p in members):
                    return True
                remaining = deadline-time.perf_counter()
                if remaining <= 0:
                    return False
                time.sleep(min(.05, remaining))
        if not reap_until(started+timeout):
            timed_out = True
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    # Never signal an unverified or unrelated group/session.
                    current = _owned_group_members(child.pid)
                    if any(p["state"] not in ("Z", "X") for p in current):
                        os.killpg(child.pid, sig)
                        signals.append(sig.name)
                except ProcessLookupError:
                    pass
                except Exception as exc:
                    observation_error = repr(exc)
                # Reaping the leader cannot skip escalation for live members.
                if reap_until(time.perf_counter()+grace):
                    break
    live = None if members is None else [p for p in members if p["state"] not in ("Z", "X")]
    cleanup_complete = status is not None and live == [] and wait_error is None and observation_error is None
    return {"role": role, "command": command, "pid": child.pid,
            "owned_process_group": child.pid, "owned_session": child.pid,
            "timeout_seconds": timeout,
            "termination_grace_seconds": grace, "signals": signals,
            "timed_out": timed_out, "reaped": status is not None,
            "exit_code": child.returncode, "elapsed_seconds": time.perf_counter()-started,
            "lifetime_peak_rss_bytes": usage.ru_maxrss*1024 if usage is not None else None,
            "controller_pre_spawn_lifetime_peak_bytes": floor,
            "cleanup_complete": cleanup_complete, "owned_group_members_after": members,
            "owned_live_members_after": live, "wait_error": wait_error,
            "observation_error": observation_error,
            "stdout": pin(stdout_path), "stderr": pin(stderr_path)}


def require_completed(receipt):
    if (receipt["timed_out"] or not receipt["reaped"] or receipt["exit_code"] != 0
            or receipt.get("cleanup_complete") is not True):
        raise ValueError("failed/incomplete "+receipt["role"]+"; all original logs and journal retained")


def frame_matches(slices, strides, shape, ranges, expected_slices, expected_ranges):
    return (tuple(tuple(s) for s in slices) == tuple(tuple(s) for s in expected_slices)
            and tuple(strides) == (1, 1) and tuple(shape) == (201, 201)
            and tuple(tuple(r) for r in ranges) == tuple(tuple(r) for r in expected_ranges))


def require_parent(expected, current, before_digest, after_digest):
    if current is not expected or before_digest != after_digest:
        raise ValueError("Applied Current object or numeric digest changed during navigation")


def validate_history(provenance, source_history, operations, version, *, reused_steps=None):
    if reused_steps is not None and reused_steps != 0:
        raise ValueError("GUI cache reused processing steps")
    if provenance[:len(source_history)] != source_history or len(provenance) != len(source_history)+14 or len(operations) != 14:
        raise ValueError("Expected exactly fourteen appended executed operations")
    entries = provenance[len(source_history):]
    prior = (4,166,1500)
    for index, (entry, operation, shape) in enumerate(zip(entries, operations, EXPECTED_SHAPES)):
        expected_op = dict(operation, version=operation.get("version", "1"))
        if expected_op["op"] == "fft":
            expected_op.setdefault("inverse", False)
        backend = entry.get("backend", {})
        if (entry.get("operation") != expected_op or entry.get("dtype") != "float32"
                or tuple(entry.get("input_shape", ())) != prior
                or tuple(entry.get("output_shape", ())) != shape
                or backend.get("name") != "native" or backend.get("version") != version or backend.get("threads") != 1):
            raise ValueError("Wrong executed operation/precision/shape/backend at step "+str(index+1))
        if index in (5, 12):
            before, after, components = ((["a0","a1"],["a0"],[0,1]) if index == 5 else (["a0"],[],[0]))
            discard = entry.get("discard_imaginary", {})
            if (entry.get("losses") != ["discarded_imaginary_quadrature"] or discard.get("axis") != expected_op["axis"]
                    or discard.get("input_complex_axes") != before or discard.get("output_complex_axes") != after
                    or discard.get("retained_input_components") != components):
                raise ValueError("Wrong explicit quadrature discard at step "+str(index+1))
        elif "discard_imaginary" in entry:
            raise ValueError("Unexpected quadrature discard")
        prior = shape
    return {"executed_steps": entries, "appended_steps": 14, "last_reused_steps": reused_steps,
            "precision_shapes_parameters_quadratures_native_one_thread": True}


def distribution(values):
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return {"raw": list(values), "count": len(values), "min": min(values),
            "max": max(values), "median": statistics.median(values), "q1": q1,
            "q3": q3, "iqr": q3-q1}


def numerical_metadata(value):
    """Only executed-operation backend version is allowed to differ by release."""
    result = json.loads(json.dumps(value))
    for record in result.get("provenance", []):
        backend = record.get("backend")
        if isinstance(backend, dict) and "version" in backend:
            backend["version"] = "<explicit backend software version>"
    return result


def installed_imports(prefix):
    records = []
    for name, module in sorted(sys.modules.items()):
        if name == "nmrnext" or name.startswith("nmrnext."):
            path = getattr(module, "__file__", None)
            if path:
                assert Path(path).resolve().is_relative_to(prefix), "Checkout import: "+str(path)
                records.append({"module": name, **pin(path)})
    return records


def qt_runtime_identity(prefix):
    """Post-timing evidence: module version is not a fabricated full-wheel name."""
    import importlib.metadata as metadata
    import PySide6
    from PySide6 import QtCore
    distributions = {}
    for name in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "PySide6_WebEngine", "PySide6_Pdf", "shiboken6", "pyqtgraph"):
        try:
            distribution = metadata.distribution(name)
            distributions[name] = {"installed_name": distribution.metadata["Name"], "version": distribution.version}
        except metadata.PackageNotFoundError:
            distributions[name] = None
    modules = {}
    for name, module in sorted(sys.modules.copy().items()):
        if name == "PySide6" or name.startswith("PySide6.Qt"):
            path = getattr(module, "__file__", None)
            if path:
                assert Path(path).resolve().is_relative_to(prefix), "Qt module leaked outside selected prefix"
                modules[name] = pin(path)
    loaded = set()
    maps = Path("/proc/self/maps")
    if maps.is_file():
        for line in maps.read_text().splitlines():
            fields = line.split(maxsplit=5)
            if len(fields) == 6 and fields[5].startswith("/"):
                path = Path(fields[5])
                if "libqt6" in path.name.lower() or "pyside6" in str(path).lower() or "shiboken" in str(path).lower():
                    assert path.is_file(), "Loaded Qt binary disappeared"
                    assert path.resolve().is_relative_to(prefix), "Qt library leaked outside selected prefix"
                    loaded.add(path.resolve())
    return {"module_version": PySide6.__version__, "qt_runtime_version": QtCore.qVersion(),
            "distributions": distributions, "imported_modules": modules,
            "loaded_library_files": [pin(path) for path in sorted(loaded)],
            "environment": {name: os.environ.get(name) for name in
                ("DISPLAY", "QT_QPA_PLATFORM", "QT_SCALE_FACTOR", "QT_PLUGIN_PATH", "LD_LIBRARY_PATH", "XDG_SESSION_TYPE")},
            "scope": "Collected only after all warmup/measured clocks, equality checks and output writing; full/optional distribution absence is explicit"}


def post_timing_worker_state(prefix, window):
    """Read actual geometry and already-loaded pool counters after every clock."""
    import ctypes
    from PySide6 import QtCore
    normal = not (window.isMaximized() or window.isMinimized() or window.isFullScreen())
    assert (window.width(), window.height()) == (1320, 840) and normal, "Worker geometry differs from frozen task"
    screen = window.screen()
    paths = set()
    for line in Path("/proc/self/maps").read_text().splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and fields[5].startswith("/"):
            path = Path(fields[5])
            if any(name in path.name.lower() for name in ("openblas", "mkl_rt", "libgomp", "libomp")):
                assert path.is_file(), "Loaded numerical pool binary disappeared"
                paths.add(path.resolve())
    pools = []
    for path in sorted(paths):
        library = ctypes.CDLL(str(path), mode=os.RTLD_NOLOAD | os.RTLD_NOW)
        counters = {}
        for name in ("scipy_openblas_get_num_threads64_", "scipy_openblas_get_num_threads_64_",
                     "scipy_openblas_get_num_threads", "openblas_get_num_threads64_",
                     "openblas_get_num_threads_64_", "openblas_get_num_threads",
                     "omp_get_max_threads", "MKL_Get_Max_Threads", "mkl_get_max_threads"):
            try:
                function = getattr(library, name)
            except AttributeError:
                continue
            function.argtypes = []
            function.restype = ctypes.c_int
            counters[name] = function()
        assert counters and all(value == 1 for value in counters.values()), "Numerical pool is not verified at one thread"
        pools.append({"library": pin(path), "actual_exported_thread_counters": counters})
    assert pools, "No actual numerical thread-pool counter was captured"
    return {"window_logical_geometry": list(window.geometry().getRect()),
            "window_frame_geometry": list(window.frameGeometry().getRect()), "normal_state": normal,
            "device_pixel_ratio": window.devicePixelRatioF(),
            "screen": {"name": screen.name(), "geometry": list(screen.geometry().getRect()),
                       "available_geometry": list(screen.availableGeometry().getRect()),
                       "device_pixel_ratio": screen.devicePixelRatio(), "logical_dpi": screen.logicalDotsPerInch()},
            "display": os.environ.get("DISPLAY"), "qt_platform": QtCore.QCoreApplication.instance().platformName(),
            "cpu_affinity": sorted(os.sched_getaffinity(0)),
            "linux_process_thread_count_snapshot": len(list(Path("/proc/self/task").iterdir())),
            "actual_numerical_pools": pools,
            "qt_global_pool": {"max_threads": QtCore.QThreadPool.globalInstance().maxThreadCount(),
                               "active_threads": QtCore.QThreadPool.globalInstance().activeThreadCount()},
            "gui_dedicated_processing_thread_finished": window._thread is None and not window._busy,
            "scope": "Post-all-clocks read-only snapshots; OS/Qt auxiliary threads are distinct from one-thread numerical pools and native history; not a peak thread trace"}


def compare_worker(config):
    import numpy as np
    from nmrnext.io import read_dataset
    left, right = [read_dataset(path, format="native") for path in config["paths"]]
    assert left.data.dtype == right.data.dtype
    np.testing.assert_array_equal(left.data, right.data)
    assert numerical_metadata(left.to_metadata()) == numerical_metadata(right.to_metadata())
    return {"all_samples_axes_quadratures_exact": True,
            "metadata_equal_except_explicit_backend_version": True,
            "files": [pin(path) for path in config["paths"]]}


def qt_worker(config, journal):
    import importlib.metadata as metadata
    import numpy as np
    from PySide6 import QtCore, QtWidgets
    import nmrnext
    from nmrnext import _native
    from nmrnext.analysis import dataset_digest
    from nmrnext.gui import Workbench
    from nmrnext.io import read_dataset, write_dataset
    from nmrnext.operations import run_operations

    prefix = Path(config["prefix"]).resolve()
    assert Path(sys.prefix).resolve() == prefix
    assert metadata.version("spinflow") == nmrnext.__version__ == _native.__version__ == config["version"]
    recipe = config["recipe"]
    assert recipe["threads"] == 1 and recipe["memory_bytes"] == 1073741824
    assert len(recipe["operations"]) == 14 and recipe["backend"] == "native"
    source = read_dataset(config["input"], format=recipe["input_format"], max_bytes=recipe["memory_bytes"])
    assert source.data.dtype == np.dtype("float32") and source.data.shape == (4, 166, 1500)
    assert source.complex_axes == ("a0", "a1")
    source_digest = dataset_digest(source)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    assert app.platformName() == config.get("qt_platform", "xcb")
    window = Workbench()
    window.resize(1320, 840)
    window.show()

    class Painted(QtCore.QObject):
        """Finish after matching paint dispatch returns, not at filter entry."""
        def __init__(self):
            super().__init__()
            self.count = 0
            self.active = False
            self.timer = QtCore.QTimer(self)
            self.timer.setInterval(1)
            self.timer.timeout.connect(self.poll)
            window.plot.viewport().installEventFilter(self)

        def ready(self):
            return (not window._busy and window._viewport_frame is not None
                    and window._viewport_pending is None and not window._viewport_timer.isActive()
                    and self.predicate())

        def eventFilter(self, watched, event):
            if event.type() == QtCore.QEvent.Type.Paint:
                self.count += 1
                if self.active and self.ready():
                    frame = window._viewport_frame
                    serial = self.count
                    QtCore.QTimer.singleShot(0, lambda: self.after_dispatch(frame, serial))
            return False

        def after_dispatch(self, frame, serial):
            if (self.active and self.ready() and window._viewport_frame is frame
                    and serial > self.initial_paints):
                self.finished = time.perf_counter()
                self.active = False
                self.loop.quit()

        def poll(self):
            if time.perf_counter() > self.deadline:
                self.active = False
                self.timed_out = True
                self.loop.quit()
            elif self.ready():
                # A worker-finished state can follow an earlier render paint.
                # Request its final paint rather than timestamping that state.
                window.plot.viewport().update()

        def measure(self, action, predicate):
            self.predicate = predicate
            self.loop = QtCore.QEventLoop()
            self.initial_paints = self.count
            self.timed_out = False
            self.active = True
            started = time.perf_counter()
            self.deadline = started + 30
            self.timer.start()
            action()
            self.loop.exec()
            self.timer.stop()
            assert not self.timed_out, "Endpoint failed to finish in 30 seconds: "+window.status.text()
            return {"elapsed_seconds": self.finished-started,
                    "paint_dispatches": self.count-self.initial_paints,
                    "completion": "queued callback after matching Qt viewport paint dispatch returned; not compositor/physical display"}

    painted = Painted()

    def load_original():
        window.load_dataset_now(source, "Fixed acquired Varian source")
        window._install_ordered(recipe["operations"], backend="native", memory_bytes=recipe["memory_bytes"])
        painted.measure(lambda: window.plot.viewport().update(),
                        lambda: window._rendered_dataset is source)

    def semantic_frame(bounds, expected_ranges, expected_current):
        frame = window._viewport_frame
        expected_slices = tuple((start, stop+1, 1) for start, stop in bounds)
        return (window.current is expected_current and window._rendered_dataset is expected_current
                and frame is not None and not frame.sampled
                and frame_matches([(s.start,s.stop,s.step) for s in frame.slices], frame.strides,
                                  frame.values.shape, window.plot.getPlotItem().getViewBox().viewRange(),
                                  expected_slices, expected_ranges))

    def frame_record(bounds=None, expected_ranges=None, expected_current=None):
        frame = window._viewport_frame
        assert frame is not None
        np.testing.assert_array_equal(frame.values, window.current.data[(0, *frame.slices)])
        for axis, coordinates, selection in zip(window.current.axes, frame.coordinates, frame.slices):
            unit = frame.units[list(window.current.axes).index(axis)]
            expected = window.current.coordinates(axis.id, unit)[selection]
            np.testing.assert_array_equal(coordinates, expected)
        if bounds is not None:
            assert semantic_frame(bounds, expected_ranges, expected_current), "Wrong requested parent region"
        return {"shape": list(frame.values.shape), "strides": list(frame.strides),
                "sampled": frame.sampled, "slices": [[s.start, s.stop, s.step] for s in frame.slices],
                "ranges": window.plot.getPlotItem().getViewBox().viewRange(),
                "signed_levels": list(window._viewport_levels), "footer": window.plot_note.text(),
                "parent_samples_and_coordinates_exact": True}

    def box_ranges(bounds):
        ys, xs = [window.current.coordinates(axis.id, "ppm") for axis in window.current.axes]
        return [sorted([float(xs[bounds[1][0]]), float(xs[bounds[1][1]])]),
                sorted([float(ys[bounds[0][0]]), float(ys[bounds[0][1]])])]

    def sequence(role):
        def raw(endpoint, observation):
            journal.emit("elapsed_captured_unvalidated", role=role, endpoint=endpoint, **observation)
        journal.emit("endpoint_started", role=role, endpoint="warm_full_processing")
        started = time.perf_counter()
        engine = run_operations(source, recipe["operations"], backend="native", threads=1,
                                memory_bytes=recipe["memory_bytes"])
        result = {"warm_full_processing": {"elapsed_seconds": time.perf_counter()-started}}
        raw("warm_full_processing", result["warm_full_processing"])
        engine_history = validate_history(engine.provenance, source.provenance, recipe["operations"], config["version"])
        journal.emit("validation_passed", role=role, endpoint="warm_full_processing", history=engine_history)
        window._recipe_cache.clear()
        journal.emit("endpoint_started", role=role, endpoint="process_to_painted_current", cache_cleared=True)
        result["process_to_painted_current"] = painted.measure(window.process_button.click,
            lambda: window.current is not source and window._rendered_dataset is window.current
                    and len(window.last_operations) == 14)
        raw("process_to_painted_current", result["process_to_painted_current"])
        history = validate_history(window.current.provenance, source.provenance, recipe["operations"],
                                   config["version"], reused_steps=window._recipe_cache.last_reused_steps)
        assert window.last_operations == recipe["operations"]
        assert window.current.data.dtype == engine.data.dtype == np.dtype("float32")
        assert window.current.data.shape == engine.data.shape == (1, 2048, 4096)
        np.testing.assert_array_equal(window.current.data, engine.data)
        assert window.current.to_metadata() == engine.to_metadata()
        result["process_to_painted_current"]["frame"] = frame_record()
        result["process_to_painted_current"]["history"] = history
        applied_parent = window.current
        applied_digest = dataset_digest(applied_parent)
        journal.emit("validation_passed", role=role, endpoint="process_to_painted_current", history=history,
                     applied_parent_id=id(applied_parent), applied_parent_digest=applied_digest)
        levels = tuple(window._viewport_levels)
        for name, bounds in (("zoom_to_paint", ((700, 900), (1600, 1800))),
                             ("pan_to_paint", ((720, 920), (1620, 1820)))):
            ranges = box_ranges(bounds)  # coordinate preparation outside timing
            journal.emit("endpoint_started", role=role, endpoint=name, requested_parent_bounds=bounds, requested_ranges=ranges)
            result[name] = painted.measure(lambda: window.plot.getPlotItem().getViewBox().setRange(
                xRange=ranges[0], yRange=ranges[1], padding=0),
                lambda: semantic_frame(bounds, ranges, applied_parent))
            raw(name, result[name])
            result[name]["frame"] = frame_record(bounds, ranges, applied_parent)
            assert tuple(window._viewport_levels) == levels
            after_digest = dataset_digest(window.current)
            require_parent(applied_parent, window.current, applied_digest, after_digest)
            np.testing.assert_array_equal(window.current.data, engine.data)
            result[name]["parent"] = {"id_before": id(applied_parent), "id_after": id(window.current),
                                      "digest_before": applied_digest, "digest_after": after_digest}
            journal.emit("validation_passed", role=role, endpoint=name, frame=result[name]["frame"],
                         parent=result[name]["parent"])
        assert dataset_digest(source) == source_digest
        return result

    load_original()
    warmup = sequence("warmup")
    load_original()
    measured = sequence("measured")
    target = Path(config["output_native"])
    journal.emit("phase_started", endpoint="native_output_write")
    write_dataset(window.current, target, format="native", overwrite=False)
    journal.emit("validation_passed", endpoint="native_output_write", output=pin(target))
    journal.emit("phase_started", endpoint="post_timing_runtime_pins")
    record = {"warmup_same_process": warmup, "measured": measured,
              "source": {"shape": list(source.data.shape), "dtype": str(source.data.dtype),
                         "complex_axes": list(source.complex_axes), "digest": source_digest},
              "result": {"shape": list(window.current.data.shape), "dtype": str(window.current.data.dtype),
                         "numeric_digest": dataset_digest(window.current), "metadata": window.current.to_metadata()},
              "output": pin(target), "python": sys.version, "prefix": str(prefix),
              "version": nmrnext.__version__, "native": pin(_native.__file__),
              "qt_platform": app.platformName(), "window_size": [window.width(), window.height()],
              "cpu_affinity": sorted(os.sched_getaffinity(0)),
              "thread_environment": {key: os.environ.get(key) for key in THREAD_ENV},
              "installed_imports": installed_imports(prefix), "qt_identity": qt_runtime_identity(prefix),
              "post_timing_worker_state": post_timing_worker_state(prefix, window),
              "gui_engine_exact": True}
    window.close()
    return record


def controller(args):
    assert sys.platform == "linux", "Controller requires Linux wait4 and affinity"
    assert args.execute, "Explicit --execute required after protocol/runtime review"
    assert not args.output.exists(), "Choose a new evidence directory"
    recipe = json.loads(args.recipe.read_text(encoding="utf-8"))
    source = args.input.resolve(strict=True)
    target = args.output.resolve()
    assert target != source and source not in target.parents and target not in source.parents, "Output overlaps input"
    input_files = [pin(path) for path in sorted(source.rglob("*")) if path.is_file()]
    assert input_files and not any(path.is_symlink() for path in source.rglob("*"))
    args.output.mkdir()
    output = args.output.resolve()
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.update({key: "1" for key in THREAD_ENV})
    cpu = min(os.sched_getaffinity(0))
    os.sched_setaffinity(0, {cpu})
    prefixes = {"A": (args.baseline_prefix.resolve(), "0.3.0b2", args.baseline_identity),
                "B": (args.candidate_prefix.resolve(), "0.3.0b3", args.candidate_identity)}
    report = {"status": "INCOMPLETE", "protocol": pin(args.protocol), "harness": pin(__file__),
              "recipe": pin(args.recipe), "input_files_before": input_files,
              "source_identities": {lane: identity for lane, (_, _, identity) in prefixes.items()},
              "deadlines_seconds": {"whole_worker": WORKER_SECONDS, "whole_comparison": COMPARISON_SECONDS,
                                    "term_grace": TERMINATION_GRACE_SECONDS, "kill_reap_grace": TERMINATION_GRACE_SECONDS},
              "rss_scope": "wait4 fresh-worker lifetime high-water; includes setup and same-process warmup and may include inherited controller pre-exec floor; subtract nothing; not incremental endpoint memory or OS cap",
              "observations": [], "pairs": [], "human_acceptance": "UNVERIFIED"}
    journal = Journal(output/"controller.journal.jsonl")
    try:
        for pair in range(5):
            order = ("A", "B") if pair % 2 == 0 else ("B", "A")
            observed = {}
            for lane in order:
                prefix, version, identity = prefixes[lane]
                stem = output / f"pair-{pair+1:02d}-{lane}"
                config = {"prefix": str(prefix), "version": version, "identity": identity,
                          "recipe": recipe, "input": str(source), "output_native": str(stem)+".nmrh5",
                          "journal": str(stem)+".journal.jsonl", "lane": lane, "pair": pair+1}
                config_path = Path(str(stem)+".config.json")
                write_new(config_path, config)
                command = [str(prefix/"bin/python"), "-I", "-B", str(Path(__file__).resolve()), "--worker", str(config_path)]
                item = {"pair": pair+1, "lane": lane, **run_bounded(command, stem, output, env,
                    timeout=WORKER_SECONDS, role="worker")}
                item["raw_journal"] = read_journal(config["journal"])
                item["raw_journal_file"] = pin(config["journal"]) if Path(config["journal"]).is_file() else None
                report["observations"].append(item)
                journal.emit("worker_receipt", pair=pair+1, lane=lane, receipt=item)
                require_completed(item)
                item["worker"] = json.loads(Path(str(stem)+".stdout.json").read_text())
                observed[lane] = item["worker"]
                assert input_files == [pin(path) for path in sorted(source.rglob("*")) if path.is_file()]
            config_path = output / f"pair-{pair+1:02d}-compare.json"
            write_new(config_path, {"paths": [observed[lane]["output"]["path"] for lane in ("A", "B")]})
            comparison = run_bounded([str(prefixes["B"][0]/"bin/python"), "-I", "-B", str(Path(__file__).resolve()),
                "--compare-worker", str(config_path)], output/f"pair-{pair+1:02d}-compare", output, env,
                timeout=COMPARISON_SECONDS, role="comparison")
            report.setdefault("comparison_receipts", []).append(comparison)
            journal.emit("comparison_receipt", pair=pair+1, receipt=comparison)
            require_completed(comparison)
            report["pairs"].append({"pair": pair+1, "order": list(order),
                "comparison": json.loads(Path(comparison["stdout"]["path"]).read_text()),
                                    "ratios_B_over_A": {endpoint: observed["B"]["measured"][endpoint]["elapsed_seconds"] /
                                        observed["A"]["measured"][endpoint]["elapsed_seconds"] for endpoint in ENDPOINTS}})
            journal.emit("pair_complete", pair=pair+1, result=report["pairs"][-1])
        report["distributions"] = {endpoint: {lane: distribution([item["worker"]["measured"][endpoint]["elapsed_seconds"]
            for item in report["observations"] if item["lane"] == lane]) for lane in ("A", "B")} for endpoint in ENDPOINTS}
        report["paired_ratios"] = {endpoint: distribution([pair["ratios_B_over_A"][endpoint] for pair in report["pairs"]]) for endpoint in ENDPOINTS}
        report["gate"] = {endpoint: item["median"] <= 1.20 for endpoint, item in report["paired_ratios"].items()}
        report["status"] = "PASS_SCOPED_ENGINEERING_REGRESSION" if all(report["gate"].values()) else "FAIL_REGRESSION_GATE"
    except Exception as exc:
        report["error"] = str(exc)
        report["status"] = "FAILED_OR_INCOMPLETE"
        journal.emit("controller_failed", error=repr(exc), traceback=traceback.format_exc())
    finally:
        journal.close()
    try:
        report["input_files_after"] = [pin(path) for path in sorted(source.rglob("*")) if path.is_file()]
        if report["input_files_after"] != input_files:
            raise ValueError("Input identity changed after the final comparison")
    except Exception as exc:
        report["status"] = "FAILED_OR_INCOMPLETE"
        report["input_final_check_error"] = str(exc)
    write_new(output/"report.json", report)
    print(output/"report.json")
    return 0 if report["status"] == "PASS_SCOPED_ENGINEERING_REGRESSION" else 1


def main():
    if len(sys.argv) == 3 and sys.argv[1] in ("--worker", "--compare-worker"):
        config = json.loads(Path(sys.argv[2]).read_text())
        result = guarded_worker(config, qt_worker) if sys.argv[1] == "--worker" else compare_worker(config)
        print(json.dumps(result, allow_nan=False))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-prefix", type=Path, required=True)
    parser.add_argument("--candidate-prefix", type=Path, required=True)
    parser.add_argument("--baseline-identity", required=True)
    parser.add_argument("--candidate-identity", required=True)
    parser.add_argument("--recipe", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    return controller(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
