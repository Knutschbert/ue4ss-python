"""Bounded reconnect outcome ledger for admitted live requests."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from .protocol import validate_request_id

DEFAULT_MAX_ENTRIES = 1024
DEFAULT_MAX_BYTES = 16 * 1024 * 1024
DEFAULT_TTL_SECONDS = 300.0


class LedgerError(ValueError):
    """An ID conflicts with retained request data or the ledger is saturated."""


@dataclass
class LedgerEntry:
    request_id: str
    fingerprint: str
    state: str
    created: float
    updated: float
    response: dict[str, Any] | None = None
    response_bytes: int = 0

    @property
    def active(self) -> bool:
        return self.state in ("queued", "running", "waiting")


class OutcomeLedger:
    """Retain active and recent terminal outcomes with byte/count/TTL bounds.

    Same-ID/same-payload retries return the retained state and never re-execute.
    A reused ID with a different fingerprint is always rejected. Active entries
    are pinned and may not be evicted to admit new work.
    """

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_bytes: int = DEFAULT_MAX_BYTES,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock=time.monotonic,
    ):
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries < 1:
            raise ValueError("max_entries must be a positive integer")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._entries: OrderedDict[str, LedgerEntry] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    @staticmethod
    def fingerprint(payload: dict[str, Any]) -> str:
        try:
            encoded = json.dumps(
                payload, sort_keys=True, ensure_ascii=False, allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8", errors="strict")
        except (TypeError, ValueError, UnicodeError) as error:
            raise LedgerError("request payload is not strict JSON") from error
        return hashlib.sha256(encoded).hexdigest()

    def _prune_locked(self, now: float) -> None:
        for request_id, entry in tuple(self._entries.items()):
            if not entry.active and now - entry.updated >= self.ttl_seconds:
                self._bytes -= entry.response_bytes
                del self._entries[request_id]

    def _evict_terminal_locked(self) -> bool:
        for request_id, entry in self._entries.items():
            if not entry.active:
                self._bytes -= entry.response_bytes
                del self._entries[request_id]
                return True
        return False

    def begin(self, request_id: str, payload: dict[str, Any]) -> tuple[str, LedgerEntry]:
        """Admit a new fingerprint or return ``duplicate``/raise on conflict/full."""
        request_id = validate_request_id(request_id)
        fingerprint = self.fingerprint(payload)
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            existing = self._entries.get(request_id)
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    raise LedgerError("request ID already belongs to a different payload")
                existing.updated = now
                self._entries.move_to_end(request_id)
                return "duplicate", existing
            while len(self._entries) >= self.max_entries:
                if not self._evict_terminal_locked():
                    raise LedgerError("all outcome ledger entries are active")
            entry = LedgerEntry(request_id, fingerprint, "queued", now, now)
            self._entries[request_id] = entry
            return "new", entry

    def update(self, request_id: str, state: str) -> LedgerEntry | None:
        """Record a nonterminal state; unknown or terminal entries are unchanged."""
        now = self._clock()
        with self._lock:
            entry = self._entries.get(request_id)
            if entry is None or not entry.active:
                return entry
            if state not in ("queued", "running", "waiting"):
                raise ValueError("state must be a nonterminal ledger state")
            entry.state = state
            entry.updated = now
            self._entries.move_to_end(request_id)
            return entry

    def complete(self, request_id: str, response: dict[str, Any]) -> LedgerEntry | None:
        """Retain a terminal response, evicting old terminal entries as needed."""
        try:
            encoded = json.dumps(
                response, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode("utf-8", errors="strict")
        except (TypeError, ValueError, UnicodeError) as error:
            raise LedgerError("terminal response is not strict JSON") from error
        if len(encoded) > self.max_bytes:
            raise LedgerError("terminal response exceeds ledger byte capacity")
        now = self._clock()
        with self._lock:
            entry = self._entries.get(request_id)
            if entry is None:
                return None
            if not entry.active:
                return entry
            needed = len(encoded) - entry.response_bytes
            while self._bytes + needed > self.max_bytes:
                if not self._evict_terminal_locked():
                    raise LedgerError("outcome ledger byte capacity is pinned by active requests")
                if request_id not in self._entries:
                    return None
                entry = self._entries[request_id]
                needed = len(encoded) - entry.response_bytes
            self._bytes += needed
            entry.response = response
            entry.response_bytes = len(encoded)
            entry.state = "terminal"
            entry.updated = now
            self._entries.move_to_end(request_id)
            return entry

    def lookup(self, request_id: str, fingerprint: str | None = None) -> dict[str, Any]:
        """Return retained state/response or explicit unknown outcome."""
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            entry = self._entries.get(request_id)
            if entry is None:
                return {"state": "unknown"}
            if fingerprint is not None and fingerprint != entry.fingerprint:
                return {"state": "conflict"}
            self._entries.move_to_end(request_id)
            return {"state": entry.state, "response": entry.response}

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "active": sum(entry.active for entry in self._entries.values()),
                "bytes": self._bytes,
                "max_entries": self.max_entries,
                "max_bytes": self.max_bytes,
            }


__all__ = ["LedgerEntry", "LedgerError", "OutcomeLedger"]
