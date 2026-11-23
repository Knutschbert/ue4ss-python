"""Regression tests for the isolated live-bridge benchmark harness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benchmark_live  # noqa: E402
from live_bridge import build_bridge  # noqa: E402


def test_benchmark_uses_real_file_client_and_returns_labeled_sample_metrics():
    before = sys.modules.get("ue4ss")
    result = benchmark_live.run(argparse.Namespace(
        poll_ms=50, client_poll=0.005, samples=2, warmup=1,
        idle_seconds=0.05, timeout=15.0,
    ))

    assert result["scope"].startswith("host simulation only")
    assert result["environment"]["temp_session"] is True
    assert result["results"]["tiny_result"]["round_trip_ms"]["count"] == 2
    assert result["results"]["tiny_result"]["bridge_reported_execution_ms"]["count"] == 2
    assert result["simulated_game_thread_callback_ms"]["count"] > 0
    assert result["idle"]["process_cpu_percent"] is not None
    assert result["process"]["peak_rss_bytes"] > 0
    assert sys.modules.get("ue4ss") is before


def test_percentile_summary_handles_empty_and_single_value():
    assert benchmark_live.summary_ms([])["p50"] is None
    assert benchmark_live.summary_ms([0.001])["p50"] == 1.0


def test_native_benchmark_poll_instrumentation_compiles_and_isolated():
    original = build_bridge("in", "out", "alive")
    instrumented = benchmark_live.instrument_poll_callbacks(original)
    assert "_bench_poll_callback_ms = []" in instrumented
    assert "_bench_callback_started_ns = time.perf_counter_ns()" in instrumented
