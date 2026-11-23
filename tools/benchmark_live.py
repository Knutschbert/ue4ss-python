#!/usr/bin/env python3
"""Measure the current file bridge against an isolated simulated game thread.

This is a host-side transport/core baseline, NOT a native UE4SS/game-frame
benchmark.  It executes the generated resident bridge's scheduled callbacks on
one dedicated Python thread and uses the real atomic-file LiveClient.

Examples:
    python tools/benchmark_live.py --samples 100 --idle-seconds 5 --output /tmp/live-baseline.json
    python tools/benchmark_live.py --poll-ms 50 --samples 100
"""

from __future__ import annotations

import argparse
import csv
import heapq
import json
import os
import platform
import sys
import subprocess
import tempfile
import threading
import time
import types
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:  # Windows
    resource = None

sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_bridge import build_bridge  # noqa: E402
from live_client import LiveClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class SimulatedGameThread:
    """Single-threaded delayed-task queue approximating the executor scheduler."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._tasks: list[tuple[float, int, Any]] = []
        self._sequence = 0
        self._stopping = False
        self.callback_seconds: list[float] = []
        self.callback_error: tuple[Any, Any, Any] | None = None
        self._thread = threading.Thread(target=self._run, name="simulated-game-thread", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def execute_with_delay(self, milliseconds: int, callback: Any) -> None:
        due = time.monotonic() + max(0, milliseconds) / 1000.0
        with self._condition:
            self._sequence += 1
            heapq.heappush(self._tasks, (due, self._sequence, callback))
            self._condition.notify()

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._stopping and not self._tasks:
                    self._condition.wait()
                if self._stopping:
                    return
                due, _, callback = self._tasks[0]
                remaining = due - time.monotonic()
                if remaining > 0:
                    self._condition.wait(remaining)
                    continue
                heapq.heappop(self._tasks)
            began = time.perf_counter()
            try:
                callback()
            except BaseException:
                # UE4SS logs callback exceptions; the benchmark keeps the scheduler
                # alive and reports the first captured exception below.
                self.callback_error = sys.exc_info()
            finally:
                self.callback_seconds.append(time.perf_counter() - began)

    def stop(self) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        self._thread.join(timeout=2.0)


class FakeUe4ss(types.ModuleType):
    def __init__(self, scheduler: SimulatedGameThread) -> None:
        super().__init__("ue4ss")
        self.execute_with_delay = scheduler.execute_with_delay
        self.reload = lambda: None
        self.find_first_of = lambda _name: None
        self.UEError = type("UEError", (Exception,), {})


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def summary_ms(values_seconds: list[float]) -> dict[str, float | None]:
    values = [value * 1000.0 for value in values_seconds]
    return {
        "min": round(min(values), 3) if values else None,
        "p50": round(percentile(values, 0.50), 3) if values else None,
        "p95": round(percentile(values, 0.95), 3) if values else None,
        "p99": round(percentile(values, 0.99), 3) if values else None,
        "max": round(max(values), 3) if values else None,
        "mean": round(sum(values) / len(values), 3) if values else None,
        "count": len(values),
    }


def memory_peak_bytes() -> int:
    # ru_maxrss is KiB on Linux and bytes on macOS.  This value is a process high
    # water mark, not an incremental bridge allocation measurement.
    if resource is not None:
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak if sys.platform == "darwin" else peak * 1024)
    import ctypes
    from ctypes import wintypes

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    if not ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return int(counters.PeakWorkingSetSize)


def run(args: argparse.Namespace) -> dict[str, Any]:
    scheduler = SimulatedGameThread()
    with tempfile.TemporaryDirectory(prefix="ue4ss-live-baseline-") as temporary:
        root = Path(temporary)
        inbox, outbox = root / "inbox", root / "outbox"
        heartbeat = root / "alive"
        source = build_bridge(str(inbox), str(outbox), str(heartbeat), poll_ms=args.poll_ms)
        fake = FakeUe4ss(scheduler)
        saved = sys.modules.get("ue4ss")
        sys.modules["ue4ss"] = fake
        namespace: dict[str, Any] = {}
        try:
            exec(compile(source, "<generated-live-bridge-baseline>", "exec"), namespace)
            scheduler.start()
            client = LiveClient(root, poll=args.client_poll)
            if args.idle_seconds:
                idle_cpu_start = time.process_time()
                idle_wall_start = time.monotonic()
                callback_start = len(scheduler.callback_seconds)
                time.sleep(args.idle_seconds)
                idle_cpu = time.process_time() - idle_cpu_start
                idle_wall = time.monotonic() - idle_wall_start
                idle_callbacks = len(scheduler.callback_seconds) - callback_start
            else:
                idle_cpu = idle_wall = None
                idle_callbacks = 0

            cases = {
                "tiny_result": "result = 1 + 1",
                "stdout": "print('baseline ' * 32)\nresult = True",
                "structured_result": "result = {'items': list(range(32)), 'ok': True}",
            }
            results: dict[str, Any] = {}
            for case, code in cases.items():
                latencies: list[float] = []
                execution: list[float] = []
                for index in range(args.warmup + args.samples):
                    began = time.perf_counter()
                    response = client.request("execute", code=code, timeout=args.timeout)
                    elapsed = time.perf_counter() - began
                    if response["status"] != "ok":
                        raise RuntimeError(f"{case} request failed: {response!r}")
                    if index >= args.warmup:
                        latencies.append(elapsed)
                        execution.append(float(response.get("duration", 0.0)))
                    if scheduler.callback_error is not None:
                        _, error, traceback = scheduler.callback_error
                        raise error.with_traceback(traceback)
                results[case] = {
                    "round_trip_ms": summary_ms(latencies),
                    "bridge_reported_execution_ms": summary_ms(execution),
                }

            # Measure request-to-callback delay with a marker published just before
            # request submission.  The code itself reads no engine objects.
            scheduler_callback = summary_ms(scheduler.callback_seconds)
            cpu_seconds = time.process_time()
            return {
                "benchmark": "ue4ss-python-file-live-bridge-baseline",
                "scope": "host simulation only; not a native UE4SS or game-frame benchmark",
                "environment": {
                    "platform": platform.platform(),
                    "python": sys.version,
                    "poll_ms": args.poll_ms,
                    "client_poll_seconds": args.client_poll,
                    "samples_per_case": args.samples,
                    "warmup_per_case": args.warmup,
                    "temp_session": True,
                },
                "results": results,
                "simulated_game_thread_callback_ms": scheduler_callback,
                "idle": {
                    "requested_seconds": args.idle_seconds,
                    "process_cpu_seconds": round(idle_cpu, 6) if idle_cpu is not None else None,
                    "wall_seconds": round(idle_wall, 6) if idle_wall is not None else None,
                    "process_cpu_percent": round(100.0 * idle_cpu / idle_wall, 3)
                    if idle_cpu is not None and idle_wall
                    else None,
                    "scheduled_callbacks": idle_callbacks,
                },
                "process": {
                    "cpu_seconds_at_end": round(cpu_seconds, 6),
                    "peak_rss_bytes": memory_peak_bytes(),
                },
                "limitations": [
                    "Python thread is a scheduler stand-in, not the Unreal game thread.",
                    "No Unreal callbacks, frame timing, native bridge startup, reload, or Windows build are measured.",
                    "Sequential requests measure file polling and host client overhead; results do not predict TCP or remote latency.",
                    "The temporary session is isolated and removed after this run.",
                ],
            }
        finally:
            scheduler.stop()
            if saved is None:
                sys.modules.pop("ue4ss", None)
            else:
                sys.modules["ue4ss"] = saved


def windows_process_metrics(process: Any) -> dict[str, int]:
    """Return cumulative CPU time (ns) and peak working set (bytes) for a child."""
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    creation, exit_time, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
    handle = wintypes.HANDLE(process._handle)
    if not ctypes.windll.kernel32.GetProcessTimes(
        handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel), ctypes.byref(user)
    ):
        raise OSError("GetProcessTimes failed")

    def filetime_ns(value: FILETIME) -> int:
        ticks = (value.high << 32) | value.low
        return ticks * 100

    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    if not ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return {
        "cpu_time_ns": filetime_ns(kernel) + filetime_ns(user),
        "peak_working_set_bytes": int(counters.PeakWorkingSetSize),
    }


def instrument_poll_callbacks(source: str) -> str:
    """Add high-resolution resident-poll timing to benchmark-only main.py."""
    start = "def _poll():\n"
    finish = "    finally:\n        if not _stopping:\n            ue4ss.execute_with_delay(POLL_MS, _poll)\n"
    if source.count(start) != 1 or source.count(finish) != 1:
        raise RuntimeError("generated bridge shape changed; cannot safely instrument poll callback timings")
    source = source.replace(
        start,
        "_bench_poll_callback_ms = []\n\n\ndef _poll():\n"
        "    _bench_callback_started_ns = time.perf_counter_ns()\n",
    )
    source = source.replace(
        finish,
        "    finally:\n"
        "        _bench_poll_callback_ms.append((time.perf_counter_ns() - _bench_callback_started_ns) / 1_000_000.0)\n"
        "        if not _stopping:\n"
        "            ue4ss.execute_with_delay(POLL_MS, _poll)\n",
    )
    compile(source, "<instrumented-live-bridge-baseline>", "exec")
    return source


def ensure_testgame_not_running(exe_name: str) -> None:
    result = subprocess.run(
        ["tasklist.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, errors="replace", timeout=15, check=True,
    )
    running = []
    for row in csv.reader(result.stdout.splitlines()):
        if row and row[0].casefold() == exe_name.casefold():
            running.append(row[1] if len(row) > 1 else "unknown PID")
    if running:
        raise RuntimeError(f"refusing to start benchmark: {exe_name} is already running (PID(s): {', '.join(running)})")


def run_testgame(args: argparse.Namespace) -> dict[str, Any]:
    """Benchmark with the staged PyTestGame, restoring its entry script byte-for-byte."""
    if os.name != "nt":
        raise RuntimeError("--testgame requires Windows Python; it starts/stops the owned staged PyTestGame process")
    from live import BRIDGE_MARK, game_path
    from testgame import EXE_NAME, GAME_DIR, Game, HEADLESS_ARGS

    ensure_testgame_not_running(EXE_NAME)
    mod = ROOT / "dist" / "PythonScripting"
    main_py = mod / "python_scripts" / "main.py"
    if not main_py.is_file():
        raise RuntimeError(f"missing packaged entry script: {main_py}")
    original = main_py.read_bytes()
    try:
        original_text = original.decode("utf-8", errors="replace")
        if not original_text.startswith(BRIDGE_MARK):
            raise RuntimeError(
                "testgame mod main.py is not the existing generated live bridge; "
                "refusing to replace a user script for a baseline"
            )
        with tempfile.TemporaryDirectory(prefix="live-baseline-", dir=ROOT / "testgame") as session_name:
            session = Path(session_name)
            inbox, outbox, heartbeat = session / "inbox", session / "outbox", session / "alive"
            inbox.mkdir()
            outbox.mkdir()
            main_py.write_text(
                instrument_poll_callbacks(
                    build_bridge(game_path(inbox), game_path(outbox), game_path(heartbeat), args.poll_ms)
                ),
                encoding="utf-8",
            )
            game = Game(GAME_DIR, EXE_NAME)
            client = LiveClient(session, poll=args.client_poll)
            startup_began = time.monotonic()
            game.start(HEADLESS_ARGS)
            try:
                startup_deadline = startup_began + args.startup_timeout
                while time.monotonic() < startup_deadline:
                    if not game.alive():
                        raise RuntimeError(f"PyTestGame exited early with code {game.process.returncode}")
                    if client.is_alive(max_age=30):
                        break
                    time.sleep(0.05)
                else:
                    tail = ""
                    if game.log.exists():
                        tail = game.log.read_text(encoding="utf-8", errors="replace")[-4000:]
                    raise RuntimeError(f"live bridge did not become ready; UE4SS.log tail:\n{tail}")
                startup_seconds = time.monotonic() - startup_began
                client.request("status", timeout=10)
                before_idle = windows_process_metrics(game.process)
                idle_began = time.monotonic()
                time.sleep(args.idle_seconds)
                idle_elapsed = time.monotonic() - idle_began
                after_idle = windows_process_metrics(game.process)
                idle_cpu_seconds = (after_idle["cpu_time_ns"] - before_idle["cpu_time_ns"]) / 1e9
                results: dict[str, Any] = {}
                for case, code in {
                    "tiny_result": "result = 1 + 1",
                    "stdout": "print('baseline ' * 32)\nresult = True",
                    "structured_result": "result = {'items': list(range(32)), 'ok': True}",
                }.items():
                    latencies: list[float] = []
                    execution: list[float] = []
                    for index in range(args.warmup + args.samples):
                        began = time.perf_counter()
                        response = client.request("execute", code=code, timeout=args.timeout)
                        elapsed = time.perf_counter() - began
                        if response["status"] != "ok":
                            raise RuntimeError(f"{case} request failed: {response!r}")
                        if index >= args.warmup:
                            latencies.append(elapsed)
                            execution.append(float(response.get("duration", 0.0)))
                    results[case] = {
                        "round_trip_ms": summary_ms(latencies),
                        "bridge_reported_execution_ms": summary_ms(execution),
                    }
                callback_result = client.request(
                    "execute",
                    code=(
                        "_v = sorted(__import__('__main__')._bench_poll_callback_ms)\n"
                        "_n = len(_v)\n"
                        "result = {'count': _n, 'min_ms': _v[0], "
                        "'p50_ms': _v[int((_n - 1) * 0.50)], "
                        "'p95_ms': _v[int((_n - 1) * 0.95)], "
                        "'p99_ms': _v[int((_n - 1) * 0.99)], "
                        "'max_ms': _v[-1], 'mean_ms': sum(_v) / _n}\n"
                        "del _v, _n"
                    ),
                    timeout=10,
                )["result"]
                before_reload = int(client.request(
                    "execute", code="result = ue4ss.script_generation()", timeout=10
                )["result"])
                reload_began = time.perf_counter()
                client.request("reload", timeout=15)
                reload_ack_ms = (time.perf_counter() - reload_began) * 1000.0
                reload_deadline = time.monotonic() + args.startup_timeout
                reload_ready_began = time.monotonic()
                while time.monotonic() < reload_deadline:
                    if not game.alive():
                        raise RuntimeError(f"PyTestGame exited during reload with code {game.process.returncode}")
                    try:
                        generation = int(client.request(
                            "execute", code="result = ue4ss.script_generation()", timeout=5
                        )["result"])
                        if generation > before_reload:
                            break
                    except Exception:
                        pass
                    time.sleep(0.05)
                else:
                    raise RuntimeError("bridge did not return after ue4ss.reload()")
                reload_ready_seconds = time.monotonic() - reload_ready_began
                final_metrics = windows_process_metrics(game.process)
                return {
                    "benchmark": "ue4ss-python-file-live-bridge-baseline",
                    "scope": "staged PyTestGame with native UE4SS/CPython; not a Chivalry 2 result",
                    "environment": {
                        "platform": platform.platform(),
                        "python": sys.version,
                        "game_executable": EXE_NAME,
                        "testgame_directory": str(GAME_DIR),
                        "poll_ms": args.poll_ms,
                        "client_poll_seconds": args.client_poll,
                        "samples_per_case": args.samples,
                        "warmup_per_case": args.warmup,
                        "temp_session": True,
                    },
                    "startup_to_heartbeat_seconds": round(startup_seconds, 3),
                    "results": results,
                    "instrumented_bridge_poll_callback_ms": callback_result,
                    "idle": {
                        "requested_seconds": args.idle_seconds,
                        "wall_seconds": round(idle_elapsed, 6),
                        "game_process_cpu_seconds": round(idle_cpu_seconds, 6),
                        "game_process_cpu_percent": round(100.0 * idle_cpu_seconds / idle_elapsed, 3)
                        if idle_elapsed else None,
                        "peak_working_set_bytes": after_idle["peak_working_set_bytes"],
                    },
                    "reload": {
                        "request_ack_ms": round(reload_ack_ms, 3),
                        "time_to_new_script_generation_seconds": round(reload_ready_seconds, 3),
                        "generation_before": before_reload,
                        "generation_after": generation,
                    },
                    "process": {
                        "cpu_seconds_at_end": round(final_metrics["cpu_time_ns"] / 1e9, 6),
                        "peak_working_set_bytes": final_metrics["peak_working_set_bytes"],
                    },
                    "limitations": [
                        "PyTestGame is a controlled UE4.25 test project, not Chivalry 2.",
                        "The timer wraps the live bridge poll callback; it is not a complete engine-frame timing hook.",
                        "Poll instrumentation adds one perf_counter_ns call and list append per scheduled callback.",
                        "Results characterize this machine/build/poll interval, not cross-machine TCP performance.",
                    ],
                }
            finally:
                game.stop()
    finally:
        # The staged game must be stopped before restoring the exact original bytes.
        main_py.write_bytes(original)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poll-ms", type=int, default=250)
    parser.add_argument("--client-poll", type=float, default=0.01)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--idle-seconds", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--testgame", action="store_true",
                        help="run the staged PyTestGame under Windows Python; starts and stops only its owned process")
    parser.add_argument("--startup-timeout", type=float, default=90.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.samples < 1 or args.warmup < 0 or args.idle_seconds < 0:
        parser.error("samples must be positive; warmup and idle-seconds must not be negative")
    if args.startup_timeout <= 0:
        parser.error("startup-timeout must be positive")
    result = run_testgame(args) if args.testgame else run(args)
    encoded = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    sys.stdout.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
