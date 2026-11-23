"""Transport-neutral live execution core; all methods run on the game thread."""

from __future__ import annotations

import contextlib
import io
import itertools
import json
import math
import threading
import time
import traceback
from typing import Any, Callable

import ue4ss

from .protocol import (
    MAX_CODE_BYTES,
    MAX_OUTPUT_CHARS,
    ProtocolError,
    make_response,
    validate_request,
    validate_request_id,
)

MAX_RESULT_BYTES = 256 * 1024
MAX_CLEANUPS = 256
MAX_JOBS = 128
MAX_JOB_CODE_BYTES = MAX_CODE_BYTES
MAX_SAFE_ITEMS = 2048
_job_sequences = itertools.count(1)


class _RawJson:
    """An already-safe value returned by internal conversion."""

    def __init__(self, value: Any):
        self.value = value


class BoundedTextIO(io.TextIOBase):
    """A stdout/stderr capture that never stores more than its configured cap."""

    def __init__(self, limit: int = MAX_OUTPUT_CHARS):
        self.limit = limit
        self._parts: list[str] = []
        self._size = 0

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("write() argument must be str")
        length = len(text)
        remaining = self.limit - self._size
        if remaining > 0:
            accepted = text[:remaining]
            self._parts.append(accepted)
            self._size += len(accepted)
        return length

    def getvalue(self) -> str:
        return "".join(self._parts)


class LiveCore:
    """Own the persistent live scope and emit plain events to a transport sink.

    The sink is invoked synchronously on the game thread and must be bounded and
    nonblocking. Transport I/O belongs in its worker; the core only emits plain
    Python data. ``maintenance`` is intentionally separate from transport request
    discovery so pending waits continue to advance independently.
    """

    def __init__(
        self,
        event_sink: Callable[[dict[str, Any]], Any],
        *,
        ue_module: Any = ue4ss,
        world_name: Callable[[], Any] | None = None,
        travel: Callable[[str], Any] | None = None,
        script_runtime: Any = None,
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if not callable(event_sink):
            raise TypeError("event_sink must be callable")
        self._event_sinks = [event_sink]
        self._event_sinks_lock = threading.RLock()
        self.event_sink_errors: list[str] = []
        self.ue = ue_module
        self._world_name = world_name or self._level_name
        self._travel = travel or self._travel_to
        self.script_runtime = script_runtime
        self._wall = wall_clock
        self._mono = monotonic
        self.busy = False
        self._admission_lock = threading.Lock()
        self._admitted_request_id: str | None = None
        self.stopping = False
        self.scope_generation = 1
        self.poll_failures = 0
        self.scope = self._new_scope()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.cleanup_groups: dict[str, dict[str, Callable[[], Any]]] = {}
        self.pending_wait: dict[str, Any] | None = None

    def add_event_sink(self, event_sink: Callable[[dict[str, Any]], Any]) -> Callable[[], None]:
        """Attach a transport sink; returns an idempotent detach callback."""
        if not callable(event_sink):
            raise TypeError("event_sink must be callable")
        with self._event_sinks_lock:
            if event_sink not in self._event_sinks:
                self._event_sinks.append(event_sink)
        detached = False
        detach_lock = threading.Lock()

        def detach() -> None:
            nonlocal detached
            with detach_lock:
                if detached:
                    return
                detached = True
            with self._event_sinks_lock:
                try:
                    self._event_sinks.remove(event_sink)
                except ValueError:
                    pass

        return detach

    def _emit(self, event: dict[str, Any]) -> None:
        with self._event_sinks_lock:
            sinks = tuple(self._event_sinks)
        for sink in sinks:
            try:
                sink(event)
            except Exception as error:
                self.event_sink_errors.append(f"{type(error).__name__}: {error}"[:500])
                if len(self.event_sink_errors) > 32:
                    del self.event_sink_errors[:-32]

    def reserve_foreground(self, request_id: str, *, allow_wait_reset: bool = False) -> bool:
        """Reserve the single cross-transport foreground slot without waiting."""
        with self._admission_lock:
            current = self._admitted_request_id
            if current is None or current == request_id:
                self._admitted_request_id = request_id
                return True
            pending = self.pending_wait
            if allow_wait_reset and pending is not None and pending.get("request_id") == current:
                self._admitted_request_id = request_id
                return True
            return False

    def _release_foreground(self, request_id: str) -> None:
        with self._admission_lock:
            if self._admitted_request_id == request_id:
                self._admitted_request_id = None

    def _wait_is_pending(self, request_id: str) -> bool:
        with self._admission_lock:
            return self.pending_wait is not None and self.pending_wait.get("request_id") == request_id

    def _finish_wait(self, request_id: str) -> None:
        with self._admission_lock:
            if self.pending_wait is not None and self.pending_wait.get("request_id") == request_id:
                self.pending_wait = None
            if self._admitted_request_id == request_id:
                self._admitted_request_id = None

    def _require_game_thread(self) -> None:
        if not self.ue.is_game_thread():
            raise self.ue.UEError("live execution core must run on the game thread")

    def _emit_state(self, request_id: str, state: str, detail: Any = None) -> None:
        event: dict[str, Any] = {"event": "state", "id": request_id, "state": state, "time": self._wall()}
        if detail is not None:
            event["detail"] = self._safe(detail)
        self._emit(event)

    def _emit_response(
        self, request_id: str, status: str, *, stdout: str = "", stderr: str = "",
        result: Any = None, error: Any = None, duration: float = 0.0,
    ) -> dict[str, Any]:
        safe_result = self._safe(result)
        safe_error = self._safe(error)
        try:
            encoded = json.dumps(safe_result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, OverflowError):
            safe_result = None
            safe_error = {"type": "ProtocolError", "message": "result is not finite JSON-safe data"}
            status = "error"
        else:
            if len(encoded) > MAX_RESULT_BYTES:
                safe_result = None
                safe_error = {
                    "type": "ResultTooLarge",
                    "message": f"result exceeds {MAX_RESULT_BYTES} encoded bytes",
                }
                status = "error"
        record = make_response(
            request_id, status, stdout=stdout[:MAX_OUTPUT_CHARS], stderr=stderr[:MAX_OUTPUT_CHARS],
            result=safe_result, error=safe_error, duration=max(0.0, duration), now=self._wall(),
        )
        self._emit({"event": "response", "record": record})
        return record

    def _safe(self, value: Any, depth: int = 0, budget: list[int] | None = None) -> Any:
        if budget is None:
            budget = [MAX_SAFE_ITEMS]
        if isinstance(value, _RawJson):
            return value.value
        if value is None or isinstance(value, (bool, str)):
            return value if not isinstance(value, str) else value[:500]
        if isinstance(value, int):
            return value if value.bit_length() <= 4096 else str(value)[:500]
        if isinstance(value, float):
            return value if math.isfinite(value) else repr(value)
        if depth >= 3:
            return f"<{type(value).__name__}>"
        if budget[0] <= 0:
            return "<truncated>"
        try:
            if isinstance(value, dict):
                result = {}
                truncated = False
                for key, item in itertools.islice(value.items(), 32):
                    if budget[0] <= 0:
                        truncated = True
                        break
                    budget[0] -= 1
                    result[str(key)[:80]] = self._safe(item, depth + 1, budget)
                if truncated or budget[0] <= 0:
                    result["<truncated>"] = True
                return result
            if isinstance(value, (list, tuple, set)):
                result = []
                truncated = False
                for item in itertools.islice(iter(value), 32):
                    if budget[0] <= 0:
                        truncated = True
                        break
                    budget[0] -= 1
                    result.append(self._safe(item, depth + 1, budget))
                if truncated or budget[0] <= 0:
                    result.append("<truncated>")
                return result
            to_dict = getattr(value, "to_dict", None)
            if callable(to_dict):
                return self._safe(to_dict(), depth + 1, budget)
            get_fname = getattr(value, "get_fname", None)
            if callable(get_fname):
                return {
                    "name": get_fname().to_string()[:500],
                    "class": value.get_class().get_fname().to_string()[:500],
                }
        except Exception:
            pass
        return f"<{type(value).__name__}>"

    def _new_scope(self) -> dict[str, Any]:
        return {"__name__": "__live_session__", "ue4ss": self.ue, "result": None,
                "register_cleanup": self.register_cleanup}

    def register_cleanup(self, group: str, name: str, callback: Callable[[], Any]) -> dict[str, str]:
        if not isinstance(group, str) or not group or not isinstance(name, str) or not name or not callable(callback):
            raise TypeError("register_cleanup(group, name, callable) requires non-empty names")
        count = sum(len(items) for items in self.cleanup_groups.values())
        if count >= MAX_CLEANUPS:
            raise ValueError(f"cleanup registration limit ({MAX_CLEANUPS}) reached")
        self.cleanup_groups.setdefault(group, {})[name] = callback
        return {"group": group, "name": name}

    def cleanup(self, group: str | None = None) -> list[dict[str, Any]]:
        names = list(self.cleanup_groups) if group in (None, "", "*") else [group]
        outcomes = []
        for group_name in names:
            resources = self.cleanup_groups.pop(group_name, {})
            for name, callback in resources.items():
                try:
                    callback()
                    outcomes.append({"group": group_name, "name": name, "ok": True})
                except Exception as error:
                    outcomes.append({
                        "group": group_name, "name": name, "ok": False,
                        "error": _RawJson({"type": type(error).__name__, "message": str(error)[:2000]}),
                    })
        return outcomes

    def _job_view(self, job: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": job["id"], "state": job["state"], "generation": job["generation"],
            "result": self._safe(job.get("result")), "stdout": job.get("stdout", "")[:MAX_OUTPUT_CHARS],
            "stderr": job.get("stderr", "")[:MAX_OUTPUT_CHARS], "error": self._safe(job.get("error")),
            "duration": job.get("duration", 0.0),
        }

    def _prune_jobs(self) -> None:
        completed = [key for key, job in self.jobs.items() if job["state"] not in ("queued", "running")]
        for key in completed[:-MAX_JOBS]:
            self.jobs.pop(key, None)

    def _start_job(self, job_id: str, code: str) -> None:
        self._require_game_thread()
        job = self.jobs.get(job_id)
        if job is None or job["state"] != "queued" or job["generation"] != self.scope_generation:
            return
        job["state"] = "running"
        began = self._mono()
        stdout, stderr = BoundedTextIO(), BoundedTextIO()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exec(compile(code, "<live-job>", "exec"), self.scope, self.scope)
            result = self._safe(self.scope.get("result"))
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > MAX_RESULT_BYTES:
                raise ValueError(f"job result exceeds {MAX_RESULT_BYTES} encoded bytes")
            job.update(state="completed", result=result, stdout=stdout.getvalue(), stderr=stderr.getvalue())
        except Exception as error:
            job.update(state="failed", stdout=stdout.getvalue(), stderr=stderr.getvalue(),
                       error={"type": type(error).__name__, "message": str(error)[:2000],
                              "traceback": traceback.format_exc()[-16_384:]})
        job["duration"] = self._mono() - began
        self._prune_jobs()
        self._emit({"event": "job", "job": self._job_view(job)})

    def _start_wait(self, request_id: str, record: dict[str, Any]) -> None:
        if self.pending_wait is not None:
            raise ValueError("another wait_until is already pending")
        code = record.get("code", "").strip()
        check = compile(code, "<wait-check>", "eval") if code else None
        with self._admission_lock:
            self.pending_wait = {
                "request_id": request_id, "code": code, "check": check,
                "deadline": record["deadline"], "began_mono": self._mono(),
                "generation": self.scope_generation,
            }
        self._emit_state(request_id, "waiting")

    def maintenance(self) -> None:
        """Advance pending waits on the game thread, independently of file polling."""
        self._require_game_thread()
        wait = self.pending_wait
        if wait is None or self.stopping:
            return
        request_id = wait["request_id"]
        if wait["generation"] != self.scope_generation:
            self.pending_wait = None
            return
        if wait["check"] is not None:
            try:
                final = eval(wait["check"], self.scope, self.scope)
            except Exception as error:
                detail = {"type": type(error).__name__, "message": "wait check raised: " + str(error)[:2000],
                          "traceback": traceback.format_exc()[-16_384:]}
                self._emit_response(request_id, "error", error=detail,
                                    duration=self._mono() - wait["began_mono"])
                self._emit_state(request_id, "failed", detail)
                self._finish_wait(request_id)
                return
            if final:
                self._emit_response(request_id, "ok", result=final,
                                    duration=self._mono() - wait["began_mono"])
                self._emit_state(request_id, "completed")
                self._finish_wait(request_id)
                return
        duration = self._mono() - wait["began_mono"]
        if self._wall() > wait["deadline"]:
            if wait["code"]:
                detail = {"type": "Timeout", "message": "wait condition not met before timeout"}
                self._emit_response(request_id, "timeout", error=detail, duration=duration)
                self._emit_state(request_id, "timeout")
            else:
                self._emit_response(request_id, "ok", result={"slept": True}, duration=duration)
                self._emit_state(request_id, "completed")
            self._finish_wait(request_id)

    def dispatch(self, record: dict[str, Any]) -> dict[str, Any] | None:
        """Validate, admit, execute one request and emit transport-neutral events."""
        self._require_game_thread()
        candidate_id = record.get("id", "invalid") if isinstance(record, dict) else "invalid"
        try:
            request_id = validate_request_id(candidate_id)
        except ProtocolError:
            request_id = "invalid"
        began = self._mono()
        try:
            request = validate_request(record, now=self._wall())
        except ProtocolError as error:
            message = str(error)
            status = "timeout" if "expired" in message else "rejected"
            error_type = "Timeout" if status == "timeout" else "ProtocolError"
            self._emit_state(request_id, status, message)
            return self._emit_response(request_id, status, error={"type": error_type, "message": message})
        request_id = request["id"]
        if self.busy:
            return self._emit_response(
                request_id, "rejected", error={"type": "Busy", "message": "another request is running"}
            )
        if not self.reserve_foreground(request_id, allow_wait_reset=request["operation"] == "reset"):
            return self._emit_response(
                request_id, "rejected", error={"type": "Busy", "message": "another foreground request is active"}
            )
        self.busy = True
        self._emit_state(request_id, "running")
        stdout, stderr = BoundedTextIO(), BoundedTextIO()
        try:
            operation = request["operation"]
            try:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    if operation == "execute":
                        exec(compile(request["code"], "<live-execute>", "exec"), self.scope, self.scope)
                        result = self.scope.get("result")
                    elif operation == "reset":
                        pending = self.pending_wait
                        if pending is not None:
                            self._emit_response(
                                pending["request_id"], "rejected",
                                error={"type": "Reset", "message": "session reset while a wait was pending"},
                            )
                            self._finish_wait(pending["request_id"])
                        cleanup = self.cleanup("*")
                        self.scope_generation += 1
                        self.scope = self._new_scope()
                        self.jobs.clear()
                        result = {"generation": self.scope_generation, "reset": True, "cleanup": cleanup}
                    elif operation == "status":
                        self._prune_jobs()
                        result = {"alive": True, "game_thread": True, "generation": self.scope_generation,
                                  "level": self._world_name(), "jobs": [self._job_view(job) for job in self.jobs.values()]}
                    elif operation == "job_start":
                        code = request["code"]
                        if len(code.encode("utf-8")) > MAX_JOB_CODE_BYTES:
                            raise ValueError("job code exceeds maximum size")
                        active = sum(job["state"] in ("queued", "running") for job in self.jobs.values())
                        if active >= 16:
                            raise ValueError("job capacity reached")
                        job_id = f"job-{request_id}-{next(_job_sequences)}"
                        self.jobs[job_id] = {
                            "id": job_id, "state": "queued", "generation": self.scope_generation,
                            "result": None, "stdout": "", "stderr": "", "error": None, "duration": 0.0,
                        }
                        self.ue.execute_with_delay(0, lambda jid=job_id, body=code: self._start_job(jid, body))
                        result = self._job_view(self.jobs[job_id])
                    elif operation in ("job_status", "job_result"):
                        job_id = request.get("args", {}).get("job_id", "")
                        if not isinstance(job_id, str) or job_id not in self.jobs:
                            raise ValueError("unknown job: " + repr(job_id))
                        result = self._job_view(self.jobs[job_id])
                    elif operation == "cleanup":
                        result = {"outcomes": self.cleanup(request.get("args", {}).get("group"))}
                    elif operation == "travel":
                        map_name = request.get("args", {}).get("map", "")
                        if not isinstance(map_name, str) or not map_name:
                            raise ValueError("travel requires a non-empty map name")
                        result = self._travel(map_name)
                    elif operation == "reload":
                        result = {"reloading": True}
                    elif operation == "wait_until":
                        self._start_wait(request_id, request)
                        return None
                    elif operation == "stop":
                        result = {"stopping": True, "cleanup": self.cleanup("*")}
                        self.stopping = True
                    elif operation == "script_list":
                        result = self._script_runtime().list()
                    elif operation == "script_start":
                        name = self._script_name(request.get("args", {}).get("name"))
                        self._script_runtime().start(name)
                        result = self._script_record(name)
                    elif operation == "script_stop":
                        name = self._script_name(request.get("args", {}).get("name"))
                        self._script_runtime().stop(name)
                        result = self._script_record(name)
                    elif operation == "script_load":
                        name = self._script_name(request.get("args", {}).get("name"))
                        self._script_runtime().load_source(name, request["code"])
                        result = self._script_record(name)
                    elif operation == "script_reload":
                        name = self._script_name(request.get("args", {}).get("name"))
                        source = request["code"] if request["code"] else None
                        self._script_runtime().reload(name, source)
                        result = self._script_record(name)
                    else:
                        raise ValueError("unsupported operation: " + repr(operation))
            except Exception as error:
                duration = self._mono() - began
                detail = {"type": type(error).__name__, "message": str(error)[:2000],
                          "traceback": traceback.format_exc()[-16_384:]}
                self._emit_state(request_id, "failed", detail)
                return self._emit_response(
                    request_id, "error", stdout=stdout.getvalue(), stderr=stderr.getvalue(), duration=duration,
                    error=detail,
                )
            duration = self._mono() - began
            if duration > request["timeout"]:
                response = self._emit_response(
                    request_id, "timeout", stdout=stdout.getvalue(), stderr=stderr.getvalue(), duration=duration,
                    error={"type": "Timeout", "message": "execution exceeded its deadline"},
                )
            else:
                response = self._emit_response(
                    request_id, "ok", stdout=stdout.getvalue(), stderr=stderr.getvalue(),
                    result=result, duration=duration,
                )
            self._emit_state(request_id, "completed" if response["status"] == "ok" else response["status"])
        finally:
            self.busy = False
            if not self._wait_is_pending(request_id):
                self._release_foreground(request_id)
        if operation == "reload" and not self.stopping:
            self.ue.reload()
        return response

    def stop(self) -> None:
        self._require_game_thread()
        self.stopping = True
        self.cleanup("*")
        with self._admission_lock:
            pending = self.pending_wait
        if pending is not None:
            request_id = pending["request_id"]
            error = {"type": "UnknownOutcome", "message": "live bridge stopped while wait was pending"}
            self._emit_response(request_id, "unknown", error=error)
            self._emit_state(request_id, "unknown", error)
        with self._admission_lock:
            self.pending_wait = None
            self._admitted_request_id = None

    def _script_runtime(self):
        if self.script_runtime is None:
            raise RuntimeError("managed script runtime is not enabled")
        return self.script_runtime

    @staticmethod
    def _script_name(value: Any) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("managed script operation requires a non-empty name")
        return value

    def _script_record(self, name: str) -> dict[str, Any]:
        for record in self._script_runtime().list():
            if record["name"] == name:
                return record
        return {"name": name, "state": "stopped"}

    def _level_name(self):
        try:
            world = self.ue.find_first_of("World")
            return world.get_full_name() if world is not None else None
        except Exception:
            return None

    def _travel_to(self, map_name: str):
        from ue4ss.engine.Engine import GameplayStatics
        world = self.ue.find_first_of("World")
        if world is None:
            raise self.ue.UEError("no World is available")
        GameplayStatics.OpenLevel(world, map_name, True, "")
        return {"map": map_name, "requested": True}


__all__ = ["BoundedTextIO", "LiveCore", "MAX_RESULT_BYTES"]
