"""Run one installed CLI command in this process and report its peak RSS.

The parent times process launch through exit. Keeping this wrapper in the same
process as the CLI avoids excluding imports or adding a second Python child.
"""

from __future__ import annotations

import ctypes
import json
import platform
import sys


PREFIX = "NMRNEXT_BENCH_METRIC="


def peak_rss_bytes() -> tuple[int | None, str]:
    system = platform.system()
    if system == "Windows":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p,
                                                 ctypes.POINTER(ProcessMemoryCounters),
                                                 ctypes.c_ulong]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),
                                          ctypes.byref(counters), counters.cb):
            return None, f"GetProcessMemoryInfo failed: {ctypes.get_last_error()}"
        return int(counters.PeakWorkingSetSize), "Windows process PeakWorkingSetSize"
    if system in {"Linux", "Darwin"}:
        try:
            import resource
            value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return int(value * (1024 if system == "Linux" else 1)), "process ru_maxrss"
        except (ImportError, OSError) as exc:
            return None, f"ru_maxrss unavailable: {exc}"
    return None, f"peak RSS unsupported on {system}"


def main() -> int:
    if len(sys.argv) != 5 or sys.argv[1] != "run":
        raise SystemExit("usage: bench_child.py run RECIPE BACKEND THREADS")
    from nmrnext.cli import main as cli_main
    code = cli_main(["run", sys.argv[2], "--backend", sys.argv[3],
                     "--threads", sys.argv[4]])
    peak, source = peak_rss_bytes()
    print(PREFIX + json.dumps({"peak_rss_bytes": peak, "rss_source": source}),
          file=sys.stderr, flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
