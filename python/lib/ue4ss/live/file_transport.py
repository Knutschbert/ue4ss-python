"""Legacy atomic-file transport adapter for :class:`LiveCore`."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import ue4ss

from .core import LiveCore
from .protocol import DuplicateRequest, make_response, validate_request_id, write_response


class FileTransport:
    def __init__(
        self, core: LiveCore | None, inbox: str, outbox: str, heartbeat: str, poll_ms: int = 250,
        *, ue_module: Any = ue4ss,
    ):
        self.inbox = str(inbox)
        self.outbox = str(outbox)
        self.heartbeat_path = str(heartbeat)
        self.poll_ms = poll_ms
        self.poll_failures = 0
        self.ue = ue_module
        self.core = core or LiveCore(self._emit, ue_module=ue_module)

    def _emit(self, event: dict[str, Any]) -> None:
        if event["event"] == "response":
            try:
                write_response(self.outbox, event["record"])
            except DuplicateRequest:
                pass  # v1 contract: keep the first terminal response for an ID
        elif event["event"] == "state":
            self._write_state(event["id"], event["state"], event.get("detail"))
        # Job updates are queried via job_status/job_result in file protocol v1.

    def _write_state(self, request_id: str, state: str, detail: Any = None) -> None:
        request_id = validate_request_id(request_id)
        os.makedirs(self.outbox, exist_ok=True)
        record = {"protocol": 1, "id": request_id, "state": state, "time": self.core._wall()}
        if detail is not None:
            record["detail"] = self.core._safe(detail)
        temporary = os.path.join(self.outbox, "." + request_id + ".state.tmp")
        target = os.path.join(self.outbox, request_id + ".state.json")
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        os.replace(temporary, target)

    def _heartbeat(self) -> None:
        parent = os.path.dirname(self.heartbeat_path)
        os.makedirs(parent, exist_ok=True)
        temporary = os.path.join(parent, ".alive.tmp")
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(str(self.core._wall()))
        os.replace(temporary, self.heartbeat_path)

    def _write_unknown(self, request_id: str) -> None:
        request_id = validate_request_id(request_id)
        error = {"type": "UnknownOutcome", "message": "bridge restarted while request was running"}
        self._write_state(request_id, "unknown", error)
        record = make_response(request_id, "unknown", error=error, now=self.core._wall())
        self._emit({"event": "response", "record": record})

    def _recover_claims(self) -> None:
        try:
            entries = os.scandir(self.inbox)
        except OSError:
            return
        with entries:
            for entry in entries:
                name = entry.name
                if not name.endswith(".running"):
                    continue
                request_id = name[:-len(".running")]
                try:
                    self._write_unknown(request_id)
                except (OSError, ValueError):
                    pass
                try:
                    os.remove(os.path.join(self.inbox, name))
                except OSError:
                    pass

    def run_record(self, record: dict[str, Any], *, busy: bool = False) -> dict[str, Any] | None:
        """Compatibility entry for direct tests and in-process dispatch."""
        if busy or self.core.busy:
            request_id = record.get("id", "invalid") if isinstance(record, dict) else "invalid"
            try:
                request_id = validate_request_id(request_id)
            except ValueError:
                request_id = "invalid"
            response = make_response(
                request_id, "rejected",
                error={"type": "Busy", "message": "another request is running"},
                now=self.core._wall(),
            )
            self._emit({"event": "response", "record": response})
            return response
        # Older v1 producers may omit optional created/timeout/args fields.
        if isinstance(record, dict) and "deadline" in record:
            normalized = dict(record)
            normalized.setdefault("timeout", 30.0)
            normalized.setdefault("created", float(normalized["deadline"]) - float(normalized["timeout"]))
            normalized.setdefault("args", {})
            record = normalized
        return self.core.dispatch(record)

    def poll(self, heartbeat=None) -> None:
        """Run bounded maintenance and one filesystem claim, then reschedule."""
        self.core._require_game_thread()
        try:
            (heartbeat or self._heartbeat)()
            self._recover_claims()
            self.core.maintenance()
            if not self.core.stopping:
                try:
                    with os.scandir(self.inbox) as entries:
                        name = min(
                            (entry.name for entry in entries if entry.name.endswith(".json")),
                            default=None,
                        )
                except OSError:
                    name = None
                if name is not None and not self.core.busy:
                    path = os.path.join(self.inbox, name)
                    running = path[:-len(".json")] + ".running"
                    try:
                        os.rename(path, running)
                        with open(running, encoding="utf-8") as handle:
                            record = json.load(handle, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                        os.remove(running)
                        self.run_record(record)
                    except Exception as error:
                        request_id = name[:-5]
                        try:
                            self._write_state(request_id, "failed", {"type": type(error).__name__, "message": str(error)[:1000]})
                            self._emit({"event": "response", "record": make_response(
                                request_id, "rejected",
                                error={"type": type(error).__name__, "message": str(error)[:1000]},
                                now=self.core._wall(),
                            )})
                        except Exception:
                            pass
                        try:
                            os.remove(running)
                        except OSError:
                            pass
        except Exception as error:
            self.poll_failures += 1
            if self.poll_failures == 1 or self.poll_failures % 120 == 0:
                print("live bridge poll error (x%d): %s" % (self.poll_failures, repr(error)[:200]))
        else:
            if self.poll_failures:
                print("live bridge poll recovered after %d failure(s)" % self.poll_failures)
            self.poll_failures = 0
        finally:
            if not self.core.stopping:
                self.ue.execute_with_delay(self.poll_ms, self.poll)

    def start(self) -> None:
        print("live bridge ready")
        self.ue.execute_with_delay(0, self.poll)


__all__ = ["FileTransport"]
