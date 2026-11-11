"""Local client for a running live-game bridge."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from live_protocol import make_request, read_record, validate_response, write_request


class LiveClientError(RuntimeError):
    """The live bridge is unavailable or returned a failed operation."""


class LiveClient:
    def __init__(self, root: str | Path, *, poll: float = 0.1):
        self.root = Path(root)
        self.inbox = self.root / "inbox"
        self.outbox = self.root / "outbox"
        self.heartbeat = self.root / "alive"
        self.poll = poll

    def game_clock_skew(self, *, now: float | None = None, max_skew: float = 3600.0,
                        attempts: int = 3) -> float | None:
        """Seconds by which the bridge's machine clock is ahead of ours, from its heartbeat.

        The bridge writes its own ``time.time()`` into the heartbeat every poll, so
        reading it just after gives the offset between the two machines' clocks. The
        request deadline must carry this offset: the bridge validates the deadline on
        its own clock, and a host clock behind the game's eats the validity window
        (a 21 s offset once turned a 15 s timeout into one that had already expired
        at send). The heartbeat is rewritten every poll, so a read can catch
        it mid-write; failed reads are retried before giving up. Returns None when
        the heartbeat stays missing or unparseable, or the offset is implausible, so
        the caller can fall back to the local clock.
        """
        local = time.time() if now is None else float(now)
        for attempt in range(attempts):
            try:
                game_at_write = float(self.heartbeat.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                if attempt + 1 < attempts:
                    time.sleep(0.025)
                continue
            skew = game_at_write - local
            if -max_skew <= skew <= max_skew:
                return skew
            return None
        return None

    def is_alive(self, *, max_age: float = 6.0, now: float | None = None) -> bool:
        # The heartbeat mtime is on the bridge's own clock, so with the game's clock
        # ahead of ours the age reads negative for a live bridge and a dead bridge
        # reads alive for the offset plus max_age - tolerated, never the reverse.
        try:
            age = (time.time() if now is None else now) - self.heartbeat.stat().st_mtime
        except OSError:
            return False
        return age <= max_age

    def request(self, operation: str, *, code: str = "", args: dict[str, Any] | None = None,
                timeout: float = 30.0) -> dict[str, Any]:
        skew = self.game_clock_skew()
        base = time.time() + (skew if skew is not None else 0.0)
        request = make_request(operation, code=code, args=args, timeout=timeout, now=base)
        write_request(self.inbox, request)
        response_path = self.outbox / f"{request['id']}.json"
        deadline = time.monotonic() + timeout + 5.0
        while time.monotonic() < deadline:
            if response_path.exists():
                try:
                    response = validate_response(read_record(response_path))
                except (OSError, json.JSONDecodeError, ValueError) as error:
                    raise LiveClientError(f"invalid response for {request['id']}: {error}") from error
                if response["status"] != "ok":
                    detail = response.get("error") or response.get("stderr") or response["status"]
                    if isinstance(detail, dict) and detail.get("message"):      # an exception in the game: its type, message and traceback as text
                        detail = f"{detail.get('type', 'Error')}: {detail['message']}" + ("\n" + detail["traceback"].rstrip() if detail.get("traceback") else "")
                    raise LiveClientError(f"live operation {operation} failed: {detail}")
                return response
            time.sleep(self.poll)
        raise LiveClientError(f"no response for {request['id']} before timeout")


def client_from_environment() -> LiveClient:
    import os
    return LiveClient(os.environ.get("PYUE_LIVE_DIR", str(Path(__file__).resolve().parents[1] / "dist" / "PythonScripting" / "live")))
