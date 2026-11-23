"""Smoke checks for the reproducible file/socket comparison harness."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import benchmark_live_transports as benchmark  # noqa: E402


def test_smoke_benchmark_compares_file_and_real_loopback_socket_without_timing_gates():
    import argparse

    result = benchmark.run(argparse.Namespace(
        poll_ms=50,
        client_poll=0.005,
        samples=1,
        warmup=0,
        idle_seconds=0,
        timeout=10,
        repeat=1,
        tcp_nodelay="on",
    ))
    repeat = result["runs"][0]
    assert repeat["file"]["scope"].startswith("host simulation only")
    socket_result = repeat["socket"]["tcp_nodelay_on"]
    assert socket_result["results"]["tiny_result"]["round_trip_ms"]["count"] == 1
    assert socket_result["cold_handshake_status_ms"] > 0
    assert socket_result["game_thread_queue_delay_ms"]["count"] > 0
    assert socket_result["results"]["tiny_result"]["estimated_non_execution_rtt_ms"]["count"] == 1
    assert socket_result["resources_after_stop"] == {
        "worker_alive": False,
        "service_closed": True,
        "connections": 0,
    }
    assert repeat["process_resources_after"]["python_thread_count"] >= 1
