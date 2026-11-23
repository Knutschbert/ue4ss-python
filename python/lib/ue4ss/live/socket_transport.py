"""Authenticated loopback TCP worker; all listener/peer I/O stays on one worker."""

from __future__ import annotations

import collections
import itertools
import selectors
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .framing import (
    FrameDecoder,
    FrameError,
    authenticate_envelope,
    create_listener,
    encode_frame,
    validate_envelope,
)
from .ledger import LedgerError, OutcomeLedger
from .protocol import ProtocolError, make_response, validate_request, validate_request_id

MAX_CONNECTIONS = 32
MAX_PENDING_CALLBACKS = 32
MAX_PEER_WRITE_BYTES = 2 * 1024 * 1024
MAX_EVENT_QUEUE_ITEMS = 256
MAX_EVENT_QUEUE_BYTES = 4 * 1024 * 1024
MAX_RELIABLE_EVENTS = 64
MAX_RELIABLE_BYTES = 16 * 1024 * 1024
MAX_HANDSHAKE_SECONDS = 5.0
MAX_PARTIAL_FRAME_SECONDS = 10.0
MAX_WRITE_NO_PROGRESS_SECONDS = 10.0
MAX_IDLE_SECONDS = 300.0
SELECT_TIMEOUT_SECONDS = 0.25
RECV_BYTES = 64 * 1024


def _rough_size(value: Any, budget: list[int] | None = None) -> int:
    """Cheap bounded queue accounting; deliberately does not serialize."""
    if budget is None:
        budget = [8192]
    if budget[0] <= 0:
        return 64
    budget[0] -= 1
    if value is None:
        return 4
    if isinstance(value, bool):
        return 5
    if isinstance(value, (int, float)):
        return 32
    if isinstance(value, str):
        return min(len(value) * 4, 1024 * 1024)
    if isinstance(value, dict):
        return 8 + sum(
            min(len(str(key)), 256) + _rough_size(item, budget)
            for key, item in itertools.islice(value.items(), 2048)
        )
    if isinstance(value, (list, tuple)):
        return 8 + sum(_rough_size(item, budget) for item in value[:2048])
    return 128


class _EventHandoff:
    """Nonwaiting bounded event queue shared by game thread and socket worker."""

    def __init__(self, wake, *, max_items=MAX_EVENT_QUEUE_ITEMS, max_bytes=MAX_EVENT_QUEUE_BYTES):
        self._wake = wake
        self._max_items = max_items
        self._max_bytes = max_bytes
        self._items = collections.deque()
        self._bytes = 0
        self._reliable = collections.deque()
        self._reliable_bytes = 0
        self._lock = threading.Lock()
        self.dropped = 0
        self.overflowed = False

    def put(self, event: dict[str, Any]) -> bool:
        size = _rough_size(event)
        with self._lock:
            if event.get("event") == "response":
                if (len(self._reliable) >= MAX_RELIABLE_EVENTS
                        or self._reliable_bytes + size > MAX_RELIABLE_BYTES):
                    self.dropped += 1
                    self.overflowed = True
                    accepted = False
                else:
                    self._reliable.append((size, event))
                    self._reliable_bytes += size
                    accepted = True
            elif len(self._items) >= self._max_items or self._bytes + size > self._max_bytes:
                self.dropped += 1
                accepted = False
            else:
                self._items.append((size, event))
                self._bytes += size
                accepted = True
        try:
            self._wake()
        except OSError:
            pass
        return accepted

    def drain(self, limit: int = 128) -> list[dict[str, Any]]:
        result = []
        with self._lock:
            while len(result) < limit and (self._reliable or self._items):
                if self._reliable:
                    size, event = self._reliable.popleft()
                    self._reliable_bytes -= size
                else:
                    size, event = self._items.popleft()
                    self._bytes -= size
                result.append(event)
        return result


@dataclass
class _Peer:
    sock: socket.socket
    address: Any
    decoder: FrameDecoder = field(default_factory=FrameDecoder)
    authenticated: bool = False
    accepted_at: float = field(default_factory=time.monotonic)
    last_progress: float = field(default_factory=time.monotonic)
    partial_since: float | None = None
    write_progress_at: float | None = None
    output: bytearray = field(default_factory=bytearray)
    request_ids: set[str] = field(default_factory=set)


class LiveSocketService:
    """Selector-based, authenticated loopback server for one shared LiveCore.

    Construct on the game thread, then invoke ``run(stop_event)`` as a managed
    worker target. The constructor creates only a local wake socketpair; listener
    binding, accepting, framing and all peer reads/writes occur on the worker.
    The worker never waits for the game thread: it schedules a Future and handles
    its completion callback without blocking.
    """

    def __init__(
        self,
        core,
        token: str,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        max_connections: int = MAX_CONNECTIONS,
        max_pending_callbacks: int = MAX_PENDING_CALLBACKS,
        maintenance_interval_ms: int = 100,
        ledger: OutcomeLedger | None = None,
        ue_module: Any | None = None,
    ):
        if not isinstance(token, str) or not 32 <= len(token.encode("utf-8")) <= 256:
            raise ValueError("token must be 32-256 UTF-8 bytes")
        if isinstance(max_connections, bool) or not 1 <= max_connections <= MAX_CONNECTIONS:
            raise ValueError(f"max_connections must be between 1 and {MAX_CONNECTIONS}")
        if isinstance(max_pending_callbacks, bool) or not 1 <= max_pending_callbacks <= MAX_PENDING_CALLBACKS:
            raise ValueError(f"max_pending_callbacks must be between 1 and {MAX_PENDING_CALLBACKS}")
        if isinstance(maintenance_interval_ms, bool) or not 25 <= maintenance_interval_ms <= 2000:
            raise ValueError("maintenance_interval_ms must be between 25 and 2000")
        self.core = core
        self.token = token
        self.host = host
        self.port = port
        self.max_connections = max_connections
        self.max_pending_callbacks = max_pending_callbacks
        self.maintenance_interval_ms = maintenance_interval_ms
        self.ledger = ledger or OutcomeLedger()
        self.ue = ue_module or core.ue
        self.instance_id = uuid.uuid4().hex
        self.capabilities = ("request", "ping", "outcome", "jobs", "wait_until")
        self.error: str | None = None
        self.bound_address: tuple[Any, ...] | None = None
        self._selector: selectors.BaseSelector | None = None
        self._peers: dict[socket.socket, _Peer] = {}
        self._peers_by_request: dict[str, set[socket.socket]] = {}
        self._pending_lock = threading.Lock()
        self._pending_callbacks = 0
        self._shutdown_peer: socket.socket | None = None
        self._shutdown_deadline = 0.0
        self._wake_read, self._wake_write = socket.socketpair()
        self._wake_read.setblocking(False)
        self._wake_write.setblocking(False)
        self._handoff: _EventHandoff | None = None
        self._stop_event: threading.Event | None = None
        self._closed = False
        self._maintenance_stopped = threading.Event()
        self._maintenance_started = False
        self._detach_sink = self.core.add_event_sink(self._emit_from_game_thread)

    @property
    def health(self) -> dict[str, Any]:
        with self._pending_lock:
            pending = self._pending_callbacks
        return {
            "running": self._selector is not None and not self._closed,
            "error": self.error,
            "instance_id": self.instance_id,
            "address": self.bound_address,
            "connections": len(self._peers),
            "pending_callbacks": pending,
            "ledger": self.ledger.stats,
        }

    def request_stop(self) -> None:
        """Signal stop without joining; socketpair wakes a blocked selector."""
        self._maintenance_stopped.set()
        if self._stop_event is not None:
            self._stop_event.set()
        try:
            self._wake_write.send(b"x")
        except (OSError, BlockingIOError):
            pass

    def _maintenance_tick(self) -> None:
        """Run wait/deadline maintenance on the game thread without file polling."""
        if self._maintenance_stopped.is_set():
            return
        try:
            self.core.maintenance()
        except Exception as error:
            self.error = f"maintenance {type(error).__name__}: {error}"
        if not self._maintenance_stopped.is_set():
            try:
                self.ue.execute_with_delay(self.maintenance_interval_ms, self._maintenance_tick)
            except Exception as error:
                self.error = f"maintenance scheduling {type(error).__name__}: {error}"

    def _emit_from_game_thread(self, event: dict[str, Any]) -> None:
        handoff = self._handoff
        if handoff is not None:
            handoff.put(event)

    def _wake_worker(self) -> None:
        try:
            self._wake_write.send(b"x")
        except (OSError, BlockingIOError):
            pass

    def _queue_frame(self, peer: _Peer, envelope: dict[str, Any]) -> bool:
        try:
            frame = encode_frame(envelope)
        except (FrameError, ValueError):
            self._close_peer(peer)
            return False
        if len(peer.output) + len(frame) > MAX_PEER_WRITE_BYTES:
            self._close_peer(peer)
            return False
        if not peer.output:
            peer.write_progress_at = time.monotonic()
        peer.output.extend(frame)
        self._set_peer_events(peer)
        return True

    def _queue_error(self, peer: _Peer, message: str, *, request_id: str | None = None) -> None:
        envelope = {"version": 1, "kind": "error", "payload": {"message": message[:1000]}}
        if request_id is not None:
            envelope["id"] = request_id
        self._queue_frame(peer, envelope)

    def _set_peer_events(self, peer: _Peer) -> None:
        if self._selector is None or peer.sock.fileno() < 0:
            return
        events = selectors.EVENT_READ
        if peer.output:
            events |= selectors.EVENT_WRITE
        try:
            self._selector.modify(peer.sock, events, peer)
        except (KeyError, OSError, ValueError):
            self._close_peer(peer)

    def _close_peer(self, peer: _Peer) -> None:
        sock = peer.sock
        if self._selector is not None:
            try:
                self._selector.unregister(sock)
            except Exception:
                pass
        self._peers.pop(sock, None)
        for request_id in peer.request_ids:
            peers = self._peers_by_request.get(request_id)
            if peers is not None:
                peers.discard(sock)
                if not peers:
                    self._peers_by_request.pop(request_id, None)
        try:
            sock.close()
        except OSError:
            pass

    def _accept(self, listener: socket.socket) -> None:
        for _ in range(16):
            try:
                sock, address = listener.accept()
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            if len(self._peers) >= self.max_connections:
                sock.close()
                continue
            sock.setblocking(False)
            peer = _Peer(sock, address)
            self._peers[sock] = peer
            assert self._selector is not None
            self._selector.register(sock, selectors.EVENT_READ, peer)
            self._queue_frame(peer, {
                "version": 1,
                "kind": "hello",
                "payload": {
                    "instance_id": self.instance_id,
                    "capabilities": list(self.capabilities),
                    "server_time": time.time(),
                },
            })

    def _handle_auth(self, peer: _Peer, envelope: dict[str, Any]) -> None:
        if not authenticate_envelope(envelope, self.token):
            self._close_peer(peer)
            return
        peer.authenticated = True
        self._queue_frame(peer, {
            "version": 1,
            "kind": "auth",
            "payload": {"authenticated": True, "instance_id": self.instance_id},
        })

    def _handle_request(self, peer: _Peer, envelope: dict[str, Any]) -> None:
        request_id = envelope["id"]
        payload = envelope.get("payload", {}).get("request")
        if not isinstance(payload, dict) or payload.get("id") != request_id:
            self._queue_error(peer, "request payload ID must match envelope ID", request_id=request_id)
            return
        try:
            request = validate_request(payload, now=time.time())
        except ProtocolError as error:
            self._queue_error(peer, str(error), request_id=request_id)
            return
        try:
            disposition, entry = self.ledger.begin(request_id, request)
        except (LedgerError, ProtocolError) as error:
            self._queue_error(peer, str(error), request_id=request_id)
            return
        self._peers_by_request.setdefault(request_id, set()).add(peer.sock)
        peer.request_ids.add(request_id)
        if disposition == "duplicate":
            self._queue_frame(peer, {
                "version": 1,
                "kind": "outcome",
                "id": request_id,
                "payload": {"state": entry.state, "response": entry.response},
            })
            return
        if not self.core.reserve_foreground(
            request_id, allow_wait_reset=request["operation"] == "reset"
        ):
            response = make_response(
                request_id, "rejected",
                error={"type": "Busy", "message": "another foreground request is active"},
            )
            self.ledger.complete(request_id, response)
            self._queue_frame(peer, {
                "version": 1, "kind": "event", "id": request_id,
                "payload": {"response": response},
            })
            self._peers_by_request.pop(request_id, None)
            peer.request_ids.discard(request_id)
            return
        with self._pending_lock:
            capacity_full = self._pending_callbacks >= self.max_pending_callbacks
            if not capacity_full:
                self._pending_callbacks += 1
        if capacity_full:
            self.core._release_foreground(request_id)
            response = make_response(
                request_id, "rejected",
                error={"type": "Busy", "message": "game-thread admission capacity is full"},
            )
            self.ledger.complete(request_id, response)
            self._queue_frame(peer, {
                "version": 1, "kind": "event", "id": request_id,
                "payload": {"response": response},
            })
            self._peers_by_request.pop(request_id, None)
            peer.request_ids.discard(request_id)
            return
        try:
            future = self.ue.call_in_game_thread(self.core.dispatch, request)
            future.add_done_callback(lambda completed, rid=request_id: self._future_done(rid, completed))
        except BaseException as error:
            with self._pending_lock:
                self._pending_callbacks -= 1
            self.core._release_foreground(request_id)
            response = make_response(
                request_id, "rejected",
                error={"type": type(error).__name__, "message": str(error)[:1000]},
            )
            self._emit_from_game_thread({"event": "response", "record": response})

    def _future_done(self, request_id: str, future) -> None:
        """Future callback is completion-only; it never waits on the game thread."""
        with self._pending_lock:
            self._pending_callbacks = max(0, self._pending_callbacks - 1)
        if future.cancelled():
            self.core._release_foreground(request_id)
            response = make_response(
                request_id, "unknown",
                error={"type": "UnknownOutcome", "message": "queued game-thread callback was cancelled"},
            )
            self._emit_from_game_thread({"event": "response", "record": response})
            return
        error = future.exception()  # callback only runs after completion; this cannot wait
        if error is not None:
            self.core._release_foreground(request_id)
            response = make_response(
                request_id, "unknown",
                error={"type": type(error).__name__, "message": str(error)[:1000]},
            )
            self._emit_from_game_thread({"event": "response", "record": response})

    def _handle_outcome(self, peer: _Peer, envelope: dict[str, Any]) -> None:
        request_id = envelope["id"]
        result = self.ledger.lookup(request_id)
        self._queue_frame(peer, {
            "version": 1, "kind": "outcome", "id": request_id, "payload": result,
        })

    def _handle_envelope(self, peer: _Peer, envelope: dict[str, Any]) -> None:
        if not peer.authenticated:
            validate_envelope(envelope, authenticated=False)
            if envelope["kind"] != "auth":
                self._queue_error(peer, "authentication required")
                return
            self._handle_auth(peer, envelope)
            return
        validate_envelope(envelope, authenticated=True)
        kind = envelope["kind"]
        if kind == "ping":
            self._queue_frame(peer, {
                "version": 1, "kind": "pong",
                "payload": {"server_time": time.time(), "instance_id": self.instance_id},
            })
        elif kind == "request":
            self._handle_request(peer, envelope)
        elif kind == "outcome":
            self._handle_outcome(peer, envelope)
        else:
            self._queue_error(peer, f"unsupported authenticated frame kind: {kind}")

    def _read_peer(self, peer: _Peer) -> None:
        try:
            chunk = peer.sock.recv(RECV_BYTES)
        except (BlockingIOError, InterruptedError):
            return
        if not chunk:
            peer.decoder.finish()
            self._close_peer(peer)
            return
        now = time.monotonic()
        peer.last_progress = now
        was_partial = peer.decoder.buffered_bytes > 0
        envelopes = peer.decoder.feed(chunk)
        if peer.decoder.buffered_bytes and not was_partial:
            peer.partial_since = now
        elif not peer.decoder.buffered_bytes:
            peer.partial_since = None
        for envelope in envelopes:
            self._handle_envelope(peer, envelope)

    def _write_peer(self, peer: _Peer) -> None:
        if not peer.output:
            self._set_peer_events(peer)
            return
        try:
            sent = peer.sock.send(peer.output)
        except (BlockingIOError, InterruptedError):
            return
        if sent <= 0:
            self._close_peer(peer)
            return
        del peer.output[:sent]
        peer.last_progress = time.monotonic()
        peer.write_progress_at = peer.last_progress if peer.output else None
        self._set_peer_events(peer)
        if not peer.output and peer.sock is self._shutdown_peer:
            self.request_stop()

    def _process_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("event")
        if event_type == "state":
            request_id = event.get("id")
            state = event.get("state")
            if isinstance(request_id, str) and state in ("queued", "running", "waiting"):
                self.ledger.update(request_id, state)
            if isinstance(request_id, str):
                envelope = {
                    "version": 1, "kind": "event", "id": request_id,
                    "payload": {
                        "state": state,
                        "time": event.get("time"),
                        **({"detail": event["detail"]} if "detail" in event else {}),
                    },
                }
                for sock in tuple(self._peers_by_request.get(request_id, ())):
                    peer = self._peers.get(sock)
                    if peer is not None and peer.authenticated:
                        self._queue_frame(peer, envelope)
        elif event_type == "response":
            record = event["record"]
            request_id = record["id"]
            try:
                self.ledger.complete(request_id, record)
            except LedgerError:
                # Core bounds each response; a saturated ledger still reports this
                # connected terminal result, while later outcome lookup is unknown.
                pass
            targets = set(self._peers_by_request.get(request_id, ()))
            envelope = {"version": 1, "kind": "event", "id": request_id, "payload": {"response": record}}
            for sock in targets:
                peer = self._peers.get(sock)
                if peer is not None and peer.authenticated:
                    self._queue_frame(peer, envelope)
            self._peers_by_request.pop(request_id, None)
            for peer in self._peers.values():
                peer.request_ids.discard(request_id)
            result = record.get("result")
            if isinstance(result, dict) and result.get("stopping"):
                live_targets = [self._peers.get(sock) for sock in targets]
                live_targets = [peer for peer in live_targets if peer is not None]
                if live_targets:
                    self._shutdown_peer = live_targets[0].sock
                    self._shutdown_deadline = time.monotonic() + 1.0
                else:
                    self.request_stop()
        elif event_type == "job":
            job = event.get("job", {})
            try:
                job_id = validate_request_id(job.get("id"))
            except ProtocolError:
                return
            envelope = {"version": 1, "kind": "event", "id": job_id, "payload": {"job": job}}
            for peer in tuple(self._peers.values()):
                if peer.authenticated:
                    self._queue_frame(peer, envelope)

    def _drain_handoff(self) -> None:
        if self._handoff is None:
            return
        for event in self._handoff.drain():
            self._process_event(event)
        if self._handoff.overflowed:
            self.error = "terminal event handoff capacity exhausted"

    def _sweep(self, now: float) -> None:
        for peer in tuple(self._peers.values()):
            if not peer.authenticated and now - peer.accepted_at > MAX_HANDSHAKE_SECONDS:
                self._close_peer(peer)
            elif peer.partial_since is not None and now - peer.partial_since > MAX_PARTIAL_FRAME_SECONDS:
                self._close_peer(peer)
            elif peer.output and peer.write_progress_at is not None and now - peer.write_progress_at > MAX_WRITE_NO_PROGRESS_SECONDS:
                self._close_peer(peer)
            elif now - peer.last_progress > MAX_IDLE_SECONDS:
                self._close_peer(peer)
        if self._shutdown_peer is not None and now >= self._shutdown_deadline:
            self.request_stop()

    def _handle_wakeup(self) -> None:
        try:
            while self._wake_read.recv(4096):
                pass
        except (BlockingIOError, OSError):
            pass
        self._drain_handoff()

    def run(self, stop_event: threading.Event) -> None:
        """Worker entry point; returns after closing every owned socket."""
        self._stop_event = stop_event
        self._handoff = _EventHandoff(self._wake_worker)
        selector = selectors.DefaultSelector()
        self._selector = selector
        listener = None
        try:
            if not self._maintenance_started:
                self._maintenance_started = True
                self.ue.execute_with_delay(
                    self.maintenance_interval_ms, self._maintenance_tick
                )
            listener = create_listener(self.host, self.port, timeout=SELECT_TIMEOUT_SECONDS)
            listener.setblocking(False)
            self.bound_address = listener.getsockname()
            selector.register(listener, selectors.EVENT_READ, "listener")
            selector.register(self._wake_read, selectors.EVENT_READ, "wakeup")
            while not stop_event.is_set():
                for key, mask in selector.select(SELECT_TIMEOUT_SECONDS):
                    if key.data == "listener":
                        self._accept(listener)
                    elif key.data == "wakeup":
                        self._handle_wakeup()
                    else:
                        peer: _Peer = key.data
                        try:
                            if mask & selectors.EVENT_READ:
                                self._read_peer(peer)
                            if peer.sock in self._peers and mask & selectors.EVENT_WRITE:
                                self._write_peer(peer)
                        except (FrameError, OSError, ValueError):
                            self._close_peer(peer)
                self._drain_handoff()
                self._sweep(time.monotonic())
                if self._handoff.overflowed:
                    self.error = "terminal event handoff capacity exhausted"
        except BaseException as error:
            self.error = f"{type(error).__name__}: {error}"
        finally:
            self._maintenance_stopped.set()
            for peer in tuple(self._peers.values()):
                self._close_peer(peer)
            if listener is not None:
                try:
                    selector.unregister(listener)
                except Exception:
                    pass
                listener.close()
            try:
                selector.unregister(self._wake_read)
            except Exception:
                pass
            selector.close()
            self._selector = None
            self._closed = True
            self._detach_sink()
            try:
                self._wake_read.close()
                self._wake_write.close()
            except OSError:
                pass


__all__ = [
    "LiveSocketService", "MAX_CONNECTIONS", "MAX_PENDING_CALLBACKS",
    "MAX_PEER_WRITE_BYTES",
]
