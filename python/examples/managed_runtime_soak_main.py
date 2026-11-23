"""Configurable coexistence soak for managed file live plus an independent component."""

import builtins
import ctypes
import gc
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from ctypes import wintypes

import ue4ss
from ue4ss.runtime import ScriptRuntime


def check(label, condition, detail=""):
    ok = bool(condition)
    print(f"{label}: {'PASS' if ok else 'FAIL'}{f' ({detail})' if detail else ''}")
    return ok


def resource_sample():
    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    kernel32.GetProcessHandleCount.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetProcessHandleCount.restype = wintypes.BOOL
    process = kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
        raise OSError("GetProcessMemoryInfo failed")
    handle_count = wintypes.DWORD()
    if not kernel32.GetProcessHandleCount(process, ctypes.byref(handle_count)):
        raise OSError("GetProcessHandleCount failed")
    return {
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "working_set_bytes": int(counters.WorkingSetSize),
        "peak_working_set_bytes": int(counters.PeakWorkingSetSize),
        "native_handle_count": int(handle_count.value),
        "python_thread_count": threading.active_count(),
        "python_gc_tracked_objects": len(gc.get_objects()),
    }


SECONDS = float(os.environ.get("PYUE_MANAGED_SOAK_SECONDS", "10"))
if not 1 <= SECONDS <= 86_400:
    print(f"FAIL: PYUE_MANAGED_SOAK_SECONDS must be between 1 and 86400, got {SECONDS}")
    print("managed runtime soak finished")
else:
    temporary = tempfile.TemporaryDirectory(prefix="ue4ss-managed-soak-")
    root = Path(temporary.name)
    package_root = root / "components"
    live_package = package_root / "LiveBridge"
    sibling_package = package_root / "Sibling"
    live_package.mkdir(parents=True)
    sibling_package.mkdir(parents=True)
    (live_package / "__init__.py").write_text(
        "from ue4ss.live.component import start\n",
        encoding="utf-8",
    )
    (sibling_package / "__init__.py").write_text(
        "import builtins\n"
        "import threading\n"
        "import time\n"
        "def start(ctx):\n"
        "    state = builtins._managed_soak_state\n"
        "    def worker(stop_event):\n"
        "        while not stop_event.wait(0.05):\n"
        "            with state['lock']:\n"
        "                state['worker_ticks'] += 1\n"
        "    ctx.spawn_worker(worker, name='managed-soak-sibling')\n"
        "    def tick():\n"
        "        if not ctx.is_valid:\n"
        "            return\n"
        "        with state['lock']:\n"
        "            state['component_ticks'] += 1\n"
        "        ctx.execute_with_delay(250, tick)\n"
        "    ctx.execute_with_delay(0, tick)\n",
        encoding="utf-8",
    )
    session = root / "session"
    events = {
        "lock": threading.Lock(),
        "worker_ticks": 0,
        "component_ticks": 0,
        "resource_samples": [],
    }
    builtins._managed_soak_state = events
    runtime = ScriptRuntime(roots=(root,))
    live_context = runtime.load_local(
        "LiveBridge",
        live_package,
        config={
            "transport": "file",
            "file": {
                "inbox": str(session / "inbox"),
                "outbox": str(session / "outbox"),
                "heartbeat": str(session / "alive"),
                "poll_ms": 250,
            },
        },
    )
    sibling_context = runtime.load_local("Sibling", sibling_package)
    started = time.monotonic()

    def sample_resources():
        events["resource_samples"].append(resource_sample())
        if time.monotonic() - started < SECONDS:
            ue4ss.execute_with_delay(60_000, sample_resources)

    ue4ss.execute_with_delay(0, sample_resources)

    def finish():
        elapsed = time.monotonic() - started
        if elapsed < SECONDS:
            ue4ss.execute_with_delay(1000, finish)
            return
        events["resource_samples"].append(resource_sample())
        heartbeat = session / "alive"
        recent = heartbeat.is_file() and time.time() - heartbeat.stat().st_mtime < 3
        with events["lock"]:
            worker_ticks = events["worker_ticks"]
            component_ticks = events["component_ticks"]
        results = [
            check("live file heartbeat remained fresh", recent, heartbeat),
            check("independent component kept ticking", component_ticks >= SECONDS * 2, component_ticks),
            check("independent worker kept progressing", worker_ticks >= SECONDS * 10, worker_ticks),
            check(
                "both managed components remained running",
                live_context.state == "running" and sibling_context.state == "running",
            ),
        ]
        resource_samples = events["resource_samples"]
        rss_delta = (
            resource_samples[-1]["working_set_bytes"] - resource_samples[0]["working_set_bytes"]
            if len(resource_samples) >= 2 else None
        )
        results.append(check("RSS/native/Python resource metrics were sampled", bool(resource_samples)))
        print(
            "managed soak resource samples: "
            + json.dumps(
                {
                    "samples": resource_samples,
                    "rss_first_to_last_delta_bytes": rss_delta,
                    "note": "observe repeated samples for a plateau; no machine-specific RSS threshold is enforced",
                },
                separators=(",", ":"),
            )
        )
        runtime.manager.stop_all()

        def finish_cleanup():
            if any(handle.alive for context in (live_context, sibling_context) for handle in context.workers):
                ue4ss.execute_with_delay(50, finish_cleanup)
                return
            check("managed workers exited after stop", True)
            del builtins._managed_soak_state
            temporary.cleanup()
            print(f"managed runtime soak finished ({sum(results)}/{len(results)} checks passed)")

        ue4ss.execute_with_delay(50, finish_cleanup)

    ue4ss.execute_with_delay(1000, finish)
