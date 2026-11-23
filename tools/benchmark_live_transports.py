#!/usr/bin/env python3
"""Compare file IPC with authenticated loopback TCP using a queued fake game thread.

This is a host transport benchmark, not a native Unreal frame-time benchmark.
It uses the normal LiveClient/SocketLiveClient protocols, real filesystem I/O,
real loopback sockets, and a dedicated queued game-thread stand-in.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import gc
import heapq
import json
import os
import platform
import statistics
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import benchmark_live  # noqa: E402
from live_client import LiveClient, SocketLiveClient  # noqa: E402


class QueuedGameThread:
    """A single-thread scheduler; worker requests queue work and never run it inline."""

    def __init__(self):
        self.condition = threading.Condition()
        self.tasks: list[tuple[float, int, Any]] = []
        self.sequence = 0
        self.stopping = False
        self.thread_id: int | None = None
        self.queue_delay_seconds: list[float] = []
        self.callback_seconds: list[float] = []
        self.thread = threading.Thread(target=self._run, name="benchmark-game-thread", daemon=True)

    def start(self):
        self.thread.start()

    def schedule(self, milliseconds, callback):
        with self.condition:
            self.sequence += 1
            heapq.heappush(
                self.tasks,
                (time.monotonic() + max(0, milliseconds) / 1000.0, self.sequence, callback),
            )
            self.condition.notify()

    def call_in_game_thread(self, callback, *args, **kwargs):
        future = concurrent.futures.Future()
        enqueued = time.perf_counter()

        def run():
            if not future.set_running_or_notify_cancel():
                return
            began = time.perf_counter()
            self.queue_delay_seconds.append(began - enqueued)
            try:
                future.set_result(callback(*args, **kwargs))
            except BaseException as error:
                future.set_exception(error)
            finally:
                self.callback_seconds.append(time.perf_counter() - began)

        self.schedule(0, run)
        return future

    def _run(self):
        self.thread_id = threading.get_ident()
        while True:
            with self.condition:
                while not self.stopping and not self.tasks:
                    self.condition.wait()
                if self.stopping:
                    return
                due, _sequence, callback = self.tasks[0]
                remaining = due - time.monotonic()
                if remaining > 0:
                    self.condition.wait(remaining)
                    continue
                heapq.heappop(self.tasks)
            callback()

    def stop(self):
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise RuntimeError("queued game-thread stand-in did not stop")


class FakeUe4ss(types.ModuleType):
    def __init__(self, scheduler: QueuedGameThread):
        super().__init__("ue4ss")
        self.scheduler = scheduler
        self.UEError = type("UEError", (Exception,), {})
        self.__path__ = [str(ROOT / "python" / "lib" / "ue4ss")]

    def execute_with_delay(self, milliseconds, callback):
        self.scheduler.schedule(milliseconds, callback)

    def call_in_game_thread(self, callback, *args, **kwargs):
        return self.scheduler.call_in_game_thread(callback, *args, **kwargs)

    def is_game_thread(self):
        return threading.get_ident() == self.scheduler.thread_id

    def reload(self):
        return None

    def find_first_of(self, _name):
        return None


def summary_ms(samples: list[float]) -> dict[str, float | int | None]:
    if not samples:
        return {"count": 0, "min": None, "p50": None, "p95": None, "p99": None, "max": None, "mean": None}
    ordered = sorted(samples)

    def at(fraction):
        return round(ordered[round((len(ordered) - 1) * fraction)] * 1000, 3)

    return {
        "count": len(samples),
        "min": round(ordered[0] * 1000, 3),
        "p50": at(0.50),
        "p95": at(0.95),
        "p99": at(0.99),
        "max": round(ordered[-1] * 1000, 3),
        "mean": round(statistics.fmean(samples) * 1000, 3),
    }


def process_resources() -> dict[str, int | None]:
    """Sample current RSS and native handle/thread counts at repeat boundaries."""
    gc.collect()
    rss = handles = None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

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
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            rss = int(counters.WorkingSetSize)
        count = wintypes.DWORD()
        if ctypes.windll.kernel32.GetProcessHandleCount(handle, ctypes.byref(count)):
            handles = int(count.value)
    else:
        statm = Path("/proc/self/statm")
        if statm.exists():
            resident_pages = int(statm.read_text().split()[1])
            rss = resident_pages * os.sysconf("SC_PAGE_SIZE")
        fd_dir = Path("/proc/self/fd")
        if fd_dir.is_dir():
            handles = len(list(fd_dir.iterdir()))
    return {
        "current_rss_bytes": rss,
        "native_handle_count": handles,
        "python_thread_count": threading.active_count(),
        "python_gc_tracked_objects": len(gc.get_objects()),
    }


def run_socket(samples: int, warmup: int, *, tcp_nodelay: bool, poll_ms: int) -> dict[str, Any]:
    scheduler = QueuedGameThread()
    fake = FakeUe4ss(scheduler)
    saved = sys.modules.get("ue4ss")
    sys.modules["ue4ss"] = fake
    service = None
    worker = None
    output = None
    root = Path(tempfile.mkdtemp(prefix="ue4ss-live-socket-bench-"))
    try:
        from ue4ss.live.core import LiveCore
        from ue4ss.live.security import provision_token
        from ue4ss.live.socket_transport import LiveSocketService

        token_path = root / "private" / "token"
        provision_token(token_path)
        token = token_path.read_text(encoding="ascii").strip()
        scheduler.start()
        core = LiveCore(lambda _event: None, ue_module=fake, world_name=lambda: "benchmark")
        service = LiveSocketService(
            core,
            token,
            host="127.0.0.1",
            port=0,
            maintenance_interval_ms=poll_ms,
            ue_module=fake,
        )
        stopped = threading.Event()
        worker = threading.Thread(target=service.run, args=(stopped,), name="benchmark-live-socket", daemon=True)
        worker.start()
        deadline = time.monotonic() + 5
        while service.bound_address is None and service.error is None and time.monotonic() < deadline:
            time.sleep(0.002)
        if service.bound_address is None:
            raise RuntimeError(f"loopback service failed to bind: {service.error}")
        client = SocketLiveClient(
            f"tcp://127.0.0.1:{service.bound_address[1]}",
            token_path,
            connect_timeout=3,
            tcp_nodelay=tcp_nodelay,
        )
        cases = {
            "tiny_result": ("execute", "result = 1 + 1"),
            "representative_result": ("execute", "result = {'items': list(range(128)), 'ok': True}"),
            "near_limit_result": ("execute", "result = 'x' * 240000"),
            "immediate_wait": ("wait_until", "True"),
        }
        results = {}
        cold_started = time.perf_counter()
        cold_response = client.request("status", timeout=10)
        cold_handshake_seconds = time.perf_counter() - cold_started
        if cold_response["status"] != "ok":
            raise RuntimeError(f"cold handshake status failed: {cold_response!r}")
        for name, (operation, code) in cases.items():
            rtt = []
            execution = []
            residual = []
            for index in range(warmup + samples):
                began = time.perf_counter()
                response = client.request(operation, code=code, timeout=30)
                elapsed = time.perf_counter() - began
                if response["status"] != "ok":
                    raise RuntimeError(f"{name} failed: {response!r}")
                if index >= warmup:
                    rtt.append(elapsed)
                    bridge_seconds = float(response.get("duration", 0))
                    execution.append(bridge_seconds)
                    residual.append(max(0.0, elapsed - bridge_seconds))
            results[name] = {
                "round_trip_ms": summary_ms(rtt),
                "bridge_reported_execution_ms": summary_ms(execution),
                "estimated_non_execution_rtt_ms": summary_ms(residual),
            }
        job_rtt = []
        for index in range(min(samples, 100)):
            began = time.perf_counter()
            started = client.request("job_start", code="result = sum(range(64))", timeout=10)
            job_id = started["result"]["id"]
            while time.perf_counter() - began < 15:
                status = client.request("job_status", args={"job_id": job_id}, timeout=10)
                if status["result"]["state"] in ("completed", "failed"):
                    if status["result"]["state"] != "completed":
                        raise RuntimeError(f"job failed: {status!r}")
                    break
            else:
                raise TimeoutError(f"job {job_id} did not complete")
            job_rtt.append(time.perf_counter() - began)
        output = {
            "transport": "socket",
            "tcp_nodelay": tcp_nodelay,
            "endpoint": "numeric loopback (ephemeral port)",
            "cold_handshake_status_ms": round(cold_handshake_seconds * 1000, 3),
            "samples_per_case": samples,
            "warmup_per_case": warmup,
            "results": results,
            "job_start_to_completion_ms": summary_ms(job_rtt),
            "game_thread_queue_delay_ms": summary_ms(scheduler.queue_delay_seconds),
            "game_thread_callback_ms": summary_ms(scheduler.callback_seconds),
            "limits": {
                "scheduler_poll_ms": poll_ms,
                "outcome_samples": len(job_rtt),
                "service_error": service.error,
                "worker_alive_at_measurement_end": worker.is_alive(),
            },
        }
        return output
    finally:
        if service is not None:
            service.request_stop()
        if worker is not None:
            worker.join(timeout=3)
        if worker is not None and worker.is_alive():
            raise RuntimeError("socket benchmark worker did not stop")
        if output is not None:
            output["resources_after_stop"] = {
                "worker_alive": worker.is_alive() if worker is not None else False,
                "service_closed": service._closed if service is not None else True,
                "connections": len(service._peers) if service is not None else 0,
            }
        scheduler.stop()
        if saved is None:
            sys.modules.pop("ue4ss", None)
        else:
            sys.modules["ue4ss"] = saved
        import shutil

        shutil.rmtree(root, ignore_errors=True)


def run(args):
    runs = []
    for index in range(args.repeat):
        before = process_resources()
        file_result = benchmark_live.run(argparse.Namespace(
            poll_ms=args.poll_ms,
            client_poll=args.client_poll,
            samples=args.samples,
            warmup=args.warmup,
            idle_seconds=args.idle_seconds,
            timeout=args.timeout,
        ))
        tcp_results = {}
        for enabled in ((True, False) if args.tcp_nodelay == "both" else (args.tcp_nodelay == "on",)):
            tcp_results["tcp_nodelay_on" if enabled else "tcp_nodelay_off"] = run_socket(
                args.samples,
                args.warmup,
                tcp_nodelay=enabled,
                poll_ms=args.poll_ms,
            )
        runs.append({
            "repeat": index + 1,
            "process_resources_before": before,
            "file": file_result,
            "socket": tcp_results,
            "process_resources_after": process_resources(),
        })
    return {
        "benchmark": "ue4ss-live-file-vs-loopback-socket",
        "scope": "host simulation only; not a native UE4SS/game-frame measurement",
        "environment": {
            "platform": platform.platform(),
            "python": sys.version,
            "poll_ms": args.poll_ms,
            "file_client_poll_seconds": args.client_poll,
            "samples_per_case": args.samples,
            "warmup_per_case": args.warmup,
            "repeat": args.repeat,
            "idle_seconds": args.idle_seconds,
            "wall_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "runs": runs,
        "limitations": [
            "The scheduler is a dedicated Python thread, not an Unreal game thread.",
            "Host timings include local IPC/client overhead; they do not predict a remote SSH tunnel.",
            "No frame hook, native DLL cost, dedicated-server idle progress, or cross-machine path was measured.",
            "Each socket operation establishes a fresh authenticated connection; cold status handshake is reported separately.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poll-ms", type=int, default=50)
    parser.add_argument("--client-poll", type=float, default=0.005)
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--idle-seconds", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--tcp-nodelay", choices=("on", "off", "both"), default="both")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.samples < 1 or args.warmup < 0 or args.repeat < 1:
        parser.error("samples and repeat must be positive; warmup must be non-negative")
    if not 50 <= args.poll_ms <= 2000:
        parser.error("poll-ms must be between 50 and 2000")
    result = run(args)
    encoded = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    sys.stdout.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
