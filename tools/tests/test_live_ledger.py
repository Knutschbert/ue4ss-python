"""Bounds, deduplication, conflict and expiry behavior for reconnect outcomes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python" / "lib"))
from ue4ss.live.ledger import LedgerError, OutcomeLedger  # noqa: E402


def test_same_id_and_payload_returns_retained_state_without_reexecution():
    ledger = OutcomeLedger()
    status, created = ledger.begin("r1", {"code": "result = 1"})
    assert status == "new"
    ledger.update("r1", "running")
    duplicate_status, duplicate = ledger.begin("r1", {"code": "result = 1"})
    assert duplicate_status == "duplicate"
    assert duplicate is created
    assert duplicate.state == "running"


def test_same_id_with_different_payload_is_rejected():
    ledger = OutcomeLedger()
    ledger.begin("r1", {"code": "result = 1"})
    with pytest.raises(LedgerError, match="different payload"):
        ledger.begin("r1", {"code": "result = 2"})


def test_terminal_response_is_available_after_disconnect_and_conflicts_are_explicit():
    ledger = OutcomeLedger()
    ledger.begin("r1", {"code": "result = 1"})
    response = {"id": "r1", "status": "ok", "result": 1}
    ledger.complete("r1", response)
    assert ledger.lookup("r1") == {"state": "terminal", "response": response}
    fingerprint = ledger.fingerprint({"code": "different"})
    assert ledger.lookup("r1", fingerprint)["state"] == "conflict"
    assert ledger.lookup("missing")["state"] == "unknown"


def test_active_entries_are_pinned_when_count_or_bytes_are_full():
    ledger = OutcomeLedger(max_entries=1, max_bytes=128)
    ledger.begin("r1", {"code": "first"})
    with pytest.raises(LedgerError, match="active"):
        ledger.begin("r2", {"code": "second"})
    with pytest.raises(LedgerError, match="exceeds ledger byte capacity"):
        ledger.complete("r1", {"id": "r1", "result": "x" * 256})


def test_old_terminal_entries_are_evicted_for_capacity_and_ttl():
    now = [0.0]
    ledger = OutcomeLedger(max_entries=1, max_bytes=512, ttl_seconds=5, clock=lambda: now[0])
    ledger.begin("r1", {"code": "first"})
    ledger.complete("r1", {"id": "r1", "status": "ok"})
    ledger.begin("r2", {"code": "second"})
    assert ledger.lookup("r1")["state"] == "unknown"
    ledger.complete("r2", {"id": "r2", "status": "ok"})
    now[0] = 10
    assert ledger.lookup("r2")["state"] == "unknown"
    assert ledger.stats["entries"] == 0


def test_fingerprint_requires_strict_finite_json():
    with pytest.raises(LedgerError):
        OutcomeLedger.fingerprint({"x": float("nan")})
