"""File protocol shared by the live-game bridge and its local clients.

The protocol is deliberately transport-neutral: the current transport is the
atomic JSON files used by ``dev.py serve``/``dev.py hot``.  MCP and other local
clients can use the same request and response records later.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = 1
MAX_CODE_BYTES = 128 * 1024
MAX_TIMEOUT_SECONDS = 600.0
OPERATIONS = frozenset(("execute", "reset", "job_start", "job_status", "job_result", "cleanup", "reload", "status", "travel", "stop", "wait_until"))
STATUSES = frozenset(("ok", "error", "timeout", "rejected", "unknown"))
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


class ProtocolError(ValueError):
    """The record is malformed or violates the protocol contract."""


class DuplicateRequest(FileExistsError):
    """A request with the same ID is already waiting or has been answered."""


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as error:
        raise ProtocolError("result/args must be JSON serializable") from error
    return value


def _request_id(value: str | None = None) -> str:
    result = value or f"req-{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:10]}"
    if not isinstance(result, str) or not _ID.fullmatch(result):
        raise ProtocolError("id must be 1-96 ASCII characters: letters, digits, '.', '_' or '-'")
    return result


def make_request(operation: str = "execute", *, code: str = "", timeout: float = 30.0,
                 args: dict[str, Any] | None = None, request_id: str | None = None,
                 now: float | None = None) -> dict[str, Any]:
    """Create a validated request record with an absolute deadline."""
    if operation not in OPERATIONS:
        raise ProtocolError(f"unknown operation: {operation!r}")
    if not isinstance(code, str):
        raise ProtocolError("code must be a string")
    if operation == "execute" and not code.strip():
        raise ProtocolError("execute requires non-empty code")
    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        raise ProtocolError(f"code exceeds {MAX_CODE_BYTES} bytes")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise ProtocolError(f"timeout must be greater than zero and at most {MAX_TIMEOUT_SECONDS:g} seconds")
    if args is not None and not isinstance(args, dict):
        raise ProtocolError("args must be an object")
    created = time.time() if now is None else float(now)
    return {
        "protocol": PROTOCOL_VERSION,
        "id": _request_id(request_id),
        "operation": operation,
        "code": code,
        "args": _json_safe(args or {}),
        "timeout": float(timeout),
        "created": created,
        "deadline": created + float(timeout),
    }


def validate_request(record: Any, *, now: float | None = None, allow_expired: bool = False) -> dict[str, Any]:
    """Validate and normalize a decoded request record."""
    if not isinstance(record, dict):
        raise ProtocolError("request must be an object")
    if record.get("protocol") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    expected = make_request(
        record.get("operation"), code=record.get("code", ""), timeout=record.get("timeout"),
        args=record.get("args"), request_id=record.get("id"), now=record.get("created"),
    )
    deadline = record.get("deadline")
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
        raise ProtocolError("deadline must be a number")
    if abs(float(deadline) - expected["deadline"]) > 0.01:
        raise ProtocolError("deadline does not match created plus timeout")
    if not allow_expired and (time.time() if now is None else now) > float(deadline):
        raise ProtocolError("request deadline has expired")
    return expected


def make_response(request_id: str, status: str, *, stdout: str = "", stderr: str = "",
                  result: Any = None, error: dict[str, Any] | None = None,
                  duration: float = 0.0, now: float | None = None) -> dict[str, Any]:
    """Create a terminal response record."""
    if status not in STATUSES:
        raise ProtocolError(f"unknown response status: {status!r}")
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        raise ProtocolError("stdout and stderr must be strings")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0:
        raise ProtocolError("duration must be a non-negative number")
    record = {
        "protocol": PROTOCOL_VERSION,
        "id": _request_id(request_id),
        "status": status,
        "stdout": stdout,
        "stderr": stderr,
        "result": _json_safe(result),
        "error": _json_safe(error),
        "duration": float(duration),
        "completed": time.time() if now is None else float(now),
    }
    return record


def validate_response(record: Any) -> dict[str, Any]:
    """Validate a decoded terminal response."""
    if not isinstance(record, dict):
        raise ProtocolError("response must be an object")
    if record.get("protocol") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    response = make_response(
        record.get("id"), record.get("status"), stdout=record.get("stdout", ""),
        stderr=record.get("stderr", ""), result=record.get("result"), error=record.get("error"),
        duration=record.get("duration"), now=record.get("completed"),
    )
    if not isinstance(record.get("completed"), (int, float)) or isinstance(record.get("completed"), bool):
        raise ProtocolError("completed must be a number")
    return response


def _write_atomic(path: Path, record: dict[str, Any], *, refuse_existing: bool = False) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if refuse_existing and path.exists():
        raise DuplicateRequest(path.name)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    try:
        if refuse_existing and path.exists():
            raise DuplicateRequest(path.name)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return path


def write_request(inbox: str | os.PathLike[str], request: dict[str, Any]) -> Path:
    """Validate and atomically publish a request as ``<id>.json``."""
    validated = validate_request(request, allow_expired=True)
    return _write_atomic(Path(inbox) / f"{validated['id']}.json", validated, refuse_existing=True)


def write_response(outbox: str | os.PathLike[str], response: dict[str, Any]) -> Path:
    """Validate and atomically publish a response as ``<id>.json``."""
    validated = validate_response(response)
    return _write_atomic(Path(outbox) / f"{validated['id']}.json", validated, refuse_existing=True)


def read_record(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read one complete JSON record; malformed/partial files raise ProtocolError."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProtocolError(f"cannot read protocol record: {path}") from error
    if not isinstance(value, dict):
        raise ProtocolError("protocol record must be an object")
    return value
