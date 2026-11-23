"""Bounded length-prefixed JSON framing and authenticated envelope validation."""

from __future__ import annotations

import hmac
import ipaddress
import json
import socket
import struct
import time
import math
import select
from typing import Any

from .protocol import ProtocolError, validate_request_id

FRAME_VERSION = 1
MAX_FRAME_BYTES = 1024 * 1024
HEADER = struct.Struct("!I")
KINDS = frozenset(("hello", "auth", "request", "event", "error", "ping", "pong", "outcome"))
PRE_AUTH_KINDS = frozenset(("hello", "auth"))
_PRE_AUTH_ALLOWED = frozenset(("hello", "auth"))


class FrameError(ValueError):
    """A wire frame is malformed, oversized, or not allowed in this state."""


def encode_frame(envelope: dict[str, Any], *, max_bytes: int = MAX_FRAME_BYTES) -> bytes:
    """Encode one strict UTF-8 JSON envelope with a 4-byte network-order length."""
    validate_envelope(envelope)
    try:
        body = json.dumps(
            envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError) as error:
        raise FrameError("envelope is not strict JSON") from error
    if not body or len(body) > max_bytes:
        raise FrameError(f"frame body must be between 1 and {max_bytes} bytes")
    return HEADER.pack(len(body)) + body


def _reject_constant(value: str):
    raise ValueError(f"non-finite JSON number: {value}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def validate_envelope(value: Any, *, authenticated: bool | None = None) -> dict[str, Any]:
    """Validate the outer wire envelope without changing the inner protocol record."""
    if not isinstance(value, dict):
        raise FrameError("envelope must be a JSON object")
    if value.get("version") != FRAME_VERSION or isinstance(value.get("version"), bool):
        raise FrameError("unsupported frame version")
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in KINDS:
        raise FrameError("unknown frame kind")
    if authenticated is False and kind not in _PRE_AUTH_ALLOWED:
        raise FrameError("operation is not allowed before authentication")
    request_id = value.get("id")
    if request_id is not None:
        try:
            validate_request_id(request_id)
        except ProtocolError as error:
            raise FrameError(str(error)) from error
    if kind in ("request", "event", "outcome") and request_id is None:
        raise FrameError(f"{kind} envelope requires an id")
    payload = value.get("payload", {})
    if not isinstance(payload, dict):
        raise FrameError("payload must be an object")
    # Enforce finite, JSON-safe payload values at the boundary.
    try:
        json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise FrameError("payload must contain finite JSON-safe values") from error
    return value


class FrameDecoder:
    """Incrementally decode frames, bounding retained bytes before body parsing.

    ``feed`` accepts fragmented headers/bodies and coalesced frames. The maximum
    unconsumed buffer is one maximum-sized frame; callers should feed data from
    fixed, bounded receive buffers rather than passing arbitrary streams at once.
    """

    def __init__(self, *, max_bytes: int = MAX_FRAME_BYTES, max_buffer: int | None = None):
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        self.max_bytes = max_bytes
        self.max_buffer = max_buffer if max_buffer is not None else max_bytes + HEADER.size
        if self.max_buffer < HEADER.size:
            raise ValueError("max_buffer must hold a frame header")
        self._buffer = bytearray()

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, data: bytes | bytearray | memoryview) -> list[dict[str, Any]]:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("frame input must be bytes-like")
        view = memoryview(data)
        output: list[dict[str, Any]] = []
        offset = 0
        while offset < len(view):
            available = self.max_buffer - len(self._buffer)
            if available <= 0:
                raise FrameError("incomplete frame exceeds the configured buffer limit")
            take = min(available, len(view) - offset)
            self._buffer.extend(view[offset:offset + take])
            offset += take
            while True:
                if len(self._buffer) < HEADER.size:
                    break
                (size,) = HEADER.unpack_from(self._buffer)
                if size == 0 or size > self.max_bytes:
                    self._buffer.clear()
                    raise FrameError(f"invalid frame length: {size}")
                total = HEADER.size + size
                if len(self._buffer) < total:
                    break
                body = bytes(self._buffer[HEADER.size:total])
                del self._buffer[:total]
                try:
                    value = json.loads(
                        body.decode("utf-8", errors="strict"),
                        parse_constant=_reject_constant,
                        object_pairs_hook=_unique_object,
                    )
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                    self._buffer.clear()
                    raise FrameError("frame body is not strict UTF-8 JSON") from error
                output.append(validate_envelope(value))
        return output

    def finish(self) -> None:
        """Signal EOF; partial headers or bodies are protocol errors."""
        if self._buffer:
            self._buffer.clear()
            raise FrameError("connection ended with a partial frame")


def send_frame(sock: socket.socket, envelope: dict[str, Any], *, deadline: float,
               max_bytes: int = MAX_FRAME_BYTES, clock=time.monotonic) -> int:
    """Write a complete frame, accounting for short writes and a monotonic deadline."""
    frame = encode_frame(envelope, max_bytes=max_bytes)
    sent = 0
    while sent < len(frame):
        if clock() >= deadline:
            raise TimeoutError("frame write deadline expired")
        try:
            count = sock.send(frame[sent:])
        except InterruptedError:
            continue
        except BlockingIOError:
            remaining = deadline - clock()
            if remaining <= 0 or not select.select([], [sock], [], remaining)[1]:
                raise TimeoutError("frame write made no progress before deadline")
            continue
        if count <= 0:
            raise ConnectionError("socket closed during frame write")
        sent += count
    return sent


def verify_token(candidate: str | bytes, token: str | bytes) -> bool:
    """Constant-time compare for a bearer token; malformed inputs fail closed."""
    try:
        candidate_bytes = candidate.encode("utf-8") if isinstance(candidate, str) else bytes(candidate)
        token_bytes = token.encode("utf-8") if isinstance(token, str) else bytes(token)
    except (TypeError, UnicodeError, ValueError):
        return False
    if not 32 <= len(token_bytes) <= 256 or len(candidate_bytes) != len(token_bytes):
        # Do a compare even on length mismatch to avoid a cheap distinguishable path.
        hmac.compare_digest(candidate_bytes[:256].ljust(32, b"\0"), token_bytes[:256].ljust(32, b"\0"))
        return False
    return hmac.compare_digest(candidate_bytes, token_bytes)


def validate_bind_host(host: str) -> str:
    """Accept numeric loopback addresses only; hostnames and wildcard binds fail closed."""
    if not isinstance(host, str):
        raise ValueError("bind host must be a numeric loopback address")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise ValueError("bind host must be a numeric loopback address") from error
    if not address.is_loopback:
        raise ValueError("live TCP listener may bind only to loopback")
    return str(address)


def create_listener(host: str = "127.0.0.1", port: int = 0, *, backlog: int = 16,
                    timeout: float = 0.25) -> socket.socket:
    """Bind a loopback-only TCP listener without SO_REUSEADDR.

    Windows uses SO_EXCLUSIVEADDRUSE where available to prevent another process
    from sharing the endpoint. The returned socket is already listening.
    """
    address = validate_bind_host(host)
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be an integer from 0 through 65535")
    if isinstance(backlog, bool) or not isinstance(backlog, int) or backlog < 1:
        raise ValueError("backlog must be a positive integer")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be positive")
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    listener = socket.socket(family, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        if family == socket.AF_INET6:
            listener.bind((address, port, 0, 0))
        else:
            listener.bind((address, port))
        listener.listen(backlog)
        listener.settimeout(timeout)
        return listener
    except BaseException:
        listener.close()
        raise


def authenticate_envelope(envelope: dict[str, Any], token: str | bytes) -> bool:
    """Accept only a valid auth envelope with a constant-time token match."""
    try:
        validate_envelope(envelope, authenticated=False)
    except FrameError:
        return False
    if envelope["kind"] != "auth":
        return False
    candidate = envelope["payload"].get("token")
    return verify_token(candidate, token)


__all__ = [
    "FRAME_VERSION", "MAX_FRAME_BYTES", "FrameDecoder", "FrameError",
    "authenticate_envelope", "create_listener", "encode_frame", "send_frame",
    "validate_bind_host", "validate_envelope", "verify_token",
]
