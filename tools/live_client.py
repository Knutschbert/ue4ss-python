"""Local client for a running live-game bridge."""

from __future__ import annotations

import json
from collections import OrderedDict
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from live_protocol import make_request, read_record, validate_request_id, validate_response, write_request
from ue4ss.live.framing import FrameDecoder, FrameError, encode_frame, validate_bind_host
from ue4ss.live.security import TokenSecurityError, read_token


class LiveClientError(RuntimeError):
    """The live bridge is unavailable or returned a failed operation."""

    def __init__(self, message: str, *, request_id: str | None = None, instance_id: str | None = None):
        super().__init__(message)
        self.request_id = request_id
        self.instance_id = instance_id


def parse_endpoint(endpoint: str) -> tuple[str, int]:
    """Parse tcp://<numeric-loopback>:<port> without treating it as a filesystem path."""
    if not isinstance(endpoint, str) or not endpoint:
        raise ValueError("endpoint must be tcp://127.0.0.1:PORT or tcp://[::1]:PORT")
    try:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "tcp" or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment
        ):
            raise ValueError
        host = parsed.hostname
        port = parsed.port
        if host is None or port is None or not 1 <= port <= 65535:
            raise ValueError
        address = validate_bind_host(host)
    except (ValueError, UnicodeError) as error:
        raise ValueError("endpoint must be tcp://127.0.0.1:PORT or tcp://[::1]:PORT") from error
    return address, port


class SocketLiveClient:
    """One-connection-per-operation authenticated TCP client.

    Ambiguous disconnects are never replayed. Each operation error includes its
    request ID so callers can explicitly query :meth:`outcome`.
    """

    is_socket = True

    def __init__(
        self,
        endpoint: str,
        token_file: str | Path,
        *,
        connect_timeout: float = 3.0,
        poll: float = 0.02,
        tcp_nodelay: bool = True,
    ):
        self.endpoint = endpoint
        self.host, self.port = parse_endpoint(endpoint)
        try:
            self.token = read_token(token_file)
        except (OSError, TokenSecurityError) as error:
            raise LiveClientError(f"cannot use TCP token file: {error}") from error
        self.connect_timeout = connect_timeout
        self.poll = poll
        self.tcp_nodelay = bool(tcp_nodelay)
        self.instance_id: str | None = None
        self.clock_offset: float | None = None
        self._request_instances: OrderedDict[str, str] = OrderedDict()

    def _connect(self):
        before = time.time()
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, int(self.tcp_nodelay))
            sock.settimeout(self.connect_timeout)
            decoder = FrameDecoder()
            hello = self._receive(sock, decoder, time.monotonic() + self.connect_timeout)
            after = time.time()
            if hello.get("kind") != "hello":
                raise LiveClientError("TCP bridge did not send a hello handshake")
            payload = hello.get("payload", {})
            instance_id = payload.get("instance_id")
            if not isinstance(instance_id, str) or not instance_id:
                raise LiveClientError("TCP bridge hello is missing its instance ID")
            server_time = payload.get("server_time")
            if isinstance(server_time, bool) or not isinstance(server_time, (int, float)):
                raise LiveClientError("TCP bridge hello has an invalid server clock sample")
            self.instance_id = instance_id
            # Estimate host->server offset at the midpoint of the measured RTT.
            self.clock_offset = float(server_time) - (before + after) / 2.0
            self._send(sock, {
                "version": 1, "kind": "auth", "payload": {"token": self.token},
            })
            auth = self._receive(sock, decoder, time.monotonic() + self.connect_timeout)
            if auth.get("kind") != "auth" or not auth.get("payload", {}).get("authenticated"):
                raise LiveClientError("TCP bridge rejected authentication")
            if auth.get("payload", {}).get("instance_id") != instance_id:
                raise LiveClientError("TCP bridge instance changed during authentication")
            return sock, decoder
        except (OSError, TimeoutError, FrameError) as error:
            raise LiveClientError(f"cannot connect to {self.endpoint}: {error}") from error

    @staticmethod
    def _send(sock: socket.socket, envelope: dict[str, Any]) -> None:
        sock.sendall(encode_frame(envelope))

    @staticmethod
    def _receive(
        sock: socket.socket, decoder: FrameDecoder, deadline: float, pending: list | None = None
    ) -> dict[str, Any]:
        if pending:
            return pending.pop(0)
        while time.monotonic() < deadline:
            remaining = max(0.001, deadline - time.monotonic())
            sock.settimeout(remaining)
            chunk = sock.recv(64 * 1024)
            if not chunk:
                decoder.finish()
                raise LiveClientError("bridge closed the TCP connection")
            frames = decoder.feed(chunk)
            if frames:
                if pending is not None:
                    pending.extend(frames[1:])
                return frames[0]
        raise TimeoutError("TCP receive deadline expired")

    def request(
        self,
        operation: str,
        *,
        code: str = "",
        args: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        request = None
        sock = None
        try:
            # Handshake sample is needed for absolute file-compatible deadlines.
            sock, decoder = self._connect()
            base = time.time() + (self.clock_offset or 0.0)
            request = make_request(operation, code=code, args=args, timeout=timeout, now=base)
            if self.instance_id is not None:
                self._request_instances[request["id"]] = self.instance_id
                self._request_instances.move_to_end(request["id"])
                while len(self._request_instances) > 256:
                    self._request_instances.popitem(last=False)
            self._send(sock, {
                "version": 1, "kind": "request", "id": request["id"],
                "payload": {"request": request},
            })
            deadline = time.monotonic() + timeout + self.connect_timeout + 5.0
            pending = []
            while time.monotonic() < deadline:
                envelope = self._receive(sock, decoder, deadline, pending)
                if envelope.get("kind") == "error":
                    raise LiveClientError(envelope.get("payload", {}).get("message", "TCP request rejected"))
                if envelope.get("id") != request["id"]:
                    continue
                if envelope.get("kind") == "event":
                    response = envelope.get("payload", {}).get("response")
                    if response is None:
                        continue
                    response = validate_response(response)
                    if response["status"] != "ok":
                        detail = response.get("error") or response.get("stderr") or response["status"]
                        if isinstance(detail, dict) and detail.get("message"):
                            detail = f"{detail.get('type', 'Error')}: {detail['message']}"
                        raise LiveClientError(
                            f"live operation {operation} failed: {detail} "
                            f"(request ID {request['id']}; reconnect and query outcome)",
                            request_id=request["id"], instance_id=self.instance_id,
                        )
                    return response
            raise TimeoutError("response deadline expired")
        except (OSError, TimeoutError, FrameError, ValueError) as error:
            request_id = request["id"] if request else "not-admitted"
            message = (
                f"TCP operation {operation} failed without replay: {error} "
                f"(request ID {request_id}; query outcome before retrying)"
            )
            raise LiveClientError(
                message, request_id=request_id,
                instance_id=self._request_instances.get(request_id),
            ) from error
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def outcome(self, request_id: str, *, timeout: float = 5.0) -> dict[str, Any]:
        expected_instance = self._request_instances.get(request_id)
        try:
            sock, decoder = self._connect()
        except LiveClientError:
            raise
        try:
            if expected_instance is not None and self.instance_id != expected_instance:
                return {
                    "state": "unknown",
                    "reason": "bridge instance changed since the request",
                    "instance_id": self.instance_id,
                }
            self._send(sock, {"version": 1, "kind": "outcome", "id": request_id, "payload": {}})
            deadline = time.monotonic() + timeout
            pending = []
            while time.monotonic() < deadline:
                envelope = self._receive(sock, decoder, deadline, pending)
                if envelope.get("kind") == "outcome" and envelope.get("id") == request_id:
                    return envelope["payload"]
            raise TimeoutError("outcome deadline expired")
        except (OSError, TimeoutError, FrameError, ValueError) as error:
            raise LiveClientError(f"cannot query outcome {request_id}: {error}") from error
        finally:
            sock.close()

    def ping(self, *, timeout: float = 2.0) -> dict[str, Any]:
        try:
            sock, decoder = self._connect()
            try:
                self._send(sock, {"version": 1, "kind": "ping", "payload": {}})
                envelope = self._receive(sock, decoder, time.monotonic() + timeout, [])
                if envelope.get("kind") != "pong":
                    raise LiveClientError("bridge did not answer ping")
                return envelope.get("payload", {})
            finally:
                sock.close()
        except (OSError, TimeoutError, FrameError) as error:
            raise LiveClientError(f"TCP bridge unavailable at {self.endpoint}: {error}") from error

    def is_alive(self, **_kwargs) -> bool:
        try:
            self.ping(timeout=self.connect_timeout)
            return True
        except LiveClientError:
            return False


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

    def outcome(self, request_id: str, *, timeout: float = 5.0) -> dict[str, Any]:
        """Inspect a file transport's current state/terminal record without replay."""
        try:
            validate_request_id(request_id)
        except ValueError as error:
            raise LiveClientError(str(error)) from error
        response_path = self.outbox / f"{request_id}.json"
        state_path = self.outbox / f"{request_id}.state.json"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if response_path.exists():
                try:
                    response = validate_response(read_record(response_path))
                except (OSError, ValueError) as error:
                    raise LiveClientError(f"invalid outcome for {request_id}: {error}") from error
                return {"state": "terminal", "response": response}
            if state_path.exists():
                try:
                    state = read_record(state_path)
                except (OSError, ValueError) as error:
                    raise LiveClientError(f"invalid state for {request_id}: {error}") from error
                if state.get("protocol") != 1 or state.get("id") != request_id:
                    raise LiveClientError(f"state record does not match request {request_id}")
                return {"state": state.get("state", "unknown"), "detail": state.get("detail")}
            time.sleep(min(self.poll, max(0, deadline - time.monotonic())))
        return {"state": "unknown", "reason": "no retained file response or state"}


def client_from_environment(
    session_dir: str | Path | None = None,
    *,
    endpoint: str | None = None,
    token_file: str | Path | None = None,
) -> LiveClient | SocketLiveClient:
    import os
    if endpoint is not None and session_dir is not None:
        raise LiveClientError("--endpoint and --session-dir are mutually exclusive")
    selected_endpoint = endpoint if endpoint is not None else (
        None if session_dir is not None else os.environ.get("PYUE_LIVE_ENDPOINT")
    )
    selected_token = token_file or os.environ.get("PYUE_LIVE_TOKEN_FILE")
    if selected_endpoint is not None:
        if not selected_token:
            raise LiveClientError("TCP endpoint requires --token-file or PYUE_LIVE_TOKEN_FILE")
        try:
            return SocketLiveClient(selected_endpoint, selected_token)
        except ValueError as error:
            raise LiveClientError(str(error)) from error
    if token_file:
        raise LiveClientError("--token-file requires --endpoint")
    root = session_dir or os.environ.get(
        "PYUE_LIVE_DIR",
        str(Path(__file__).resolve().parents[1] / "dist" / "PythonScripting" / "live"),
    )
    return LiveClient(root)
