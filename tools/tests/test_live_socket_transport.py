"""Real loopback tests for the selector worker and game-thread handoff."""

from __future__ import annotations

import concurrent.futures
import queue
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python" / "lib"))
from ue4ss.live.core import LiveCore  # noqa: E402
from ue4ss.live.framing import FrameDecoder, encode_frame  # noqa: E402
from ue4ss.live.protocol import make_request  # noqa: E402
from ue4ss.live.socket_transport import LiveSocketService, _EventHandoff  # noqa: E402


class QueuedUe:
    class UEError(Exception):
        pass

    def __init__(self):
        self.game_thread = threading.get_ident()
        self.pending = queue.Queue()
        self.reloads = 0
        self.calls = 0
        self.futures = []

    def is_game_thread(self):
        return threading.get_ident() == self.game_thread

    def call_in_game_thread(self, callback, *args, **kwargs):
        self.calls += 1
        future = concurrent.futures.Future()
        self.futures.append(future)

        def run():
            if not future.set_running_or_notify_cancel():
                return
            try:
                future.set_result(callback(*args, **kwargs))
            except BaseException as error:
                future.set_exception(error)

        self.pending.put(run)
        return future

    def execute_with_delay(self, _delay, callback):
        self.pending.put(callback)

    def reload(self):
        self.reloads += 1

    def find_first_of(self, _name):
        return None

    def pump_one(self):
        try:
            callback = self.pending.get_nowait()
        except queue.Empty:
            return False
        callback()
        return True


def receive_envelope(sock, decoder, *, timeout=1.0, predicate=lambda _item: True):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            data = sock.recv(65536)
        except socket.timeout:
            continue
        if not data:
            raise ConnectionError("server closed the socket")
        for item in decoder.feed(data):
            if predicate(item):
                return item
    raise TimeoutError("expected frame was not received")


def pump_until(ue, sock, decoder, predicate, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        ue.pump_one()
        try:
            item = receive_envelope(sock, decoder, timeout=0.02, predicate=predicate)
            return item
        except TimeoutError:
            continue
    raise TimeoutError("game-thread work or response did not complete")


@pytest.fixture
def service():
    ue = QueuedUe()
    seen = []
    core = LiveCore(seen.append, ue_module=ue, world_name=lambda: "test-world")
    token = "network-test-token-" + "x" * 32
    live = LiveSocketService(core, token, port=0, ue_module=ue, max_connections=4)
    stop = threading.Event()
    thread = threading.Thread(target=live.run, args=(stop,), daemon=True)
    thread.start()
    limit = time.monotonic() + 2
    while live.bound_address is None and live.error is None and time.monotonic() < limit:
        time.sleep(0.005)
    assert live.bound_address is not None, live.error
    try:
        yield live, core, ue, token
    finally:
        live.request_stop()
        thread.join(timeout=2)
        assert not thread.is_alive(), "socket worker did not stop within test bound"


def connect_authenticated(live, token):
    address = live.bound_address
    client = socket.create_connection((address[0], address[1]), timeout=1)
    client.settimeout(0.05)
    decoder = FrameDecoder()
    hello = receive_envelope(client, decoder)
    assert hello["kind"] == "hello"
    assert hello["payload"]["instance_id"] == live.instance_id
    client.sendall(encode_frame({
        "version": 1, "kind": "auth", "payload": {"token": token},
    }))
    auth = receive_envelope(client, decoder, predicate=lambda item: item.get("payload", {}).get("authenticated"))
    assert auth["payload"]["authenticated"] is True
    return client, decoder


def test_authenticated_loopback_request_runs_only_on_game_thread(service):
    live, _core, ue, token = service
    client, decoder = connect_authenticated(live, token)
    try:
        record = make_request(code="result = 6 * 7", request_id="socket-one", timeout=5)
        client.sendall(encode_frame({
            "version": 1, "kind": "request", "id": record["id"], "payload": {"request": record},
        }))
        terminal = pump_until(
            ue, client, decoder,
            lambda item: item.get("kind") == "event"
            and item.get("payload", {}).get("response", {}).get("id") == "socket-one",
        )
        response = terminal["payload"]["response"]
        assert response["status"] == "ok"
        assert response["result"] == 42
    finally:
        client.close()


def test_ping_and_duplicate_outcome_work_without_reexecution(service):
    live, core, ue, token = service
    client, decoder = connect_authenticated(live, token)
    try:
        record = make_request(
            code="count = globals().get('count', 0) + 1\nresult = count",
            request_id="duplicate", timeout=5,
        )
        request_frame = {
            "version": 1, "kind": "request", "id": record["id"], "payload": {"request": record},
        }
        client.sendall(encode_frame(request_frame))
        first = pump_until(
            ue, client, decoder,
            lambda item: item.get("kind") == "event"
            and item.get("payload", {}).get("response", {}).get("id") == "duplicate",
        )
        assert first["payload"]["response"]["result"] == 1

        client.sendall(encode_frame({"version": 1, "kind": "ping", "payload": {}}))
        pong = receive_envelope(client, decoder, predicate=lambda item: item["kind"] == "pong")
        assert pong["payload"]["instance_id"] == live.instance_id

        client.sendall(encode_frame(request_frame))
        duplicate = receive_envelope(
            client, decoder,
            predicate=lambda item: item["kind"] == "outcome" and item.get("id") == "duplicate",
        )
        assert duplicate["payload"]["state"] == "terminal"
        assert duplicate["payload"]["response"]["result"] == 1
        assert core.scope["count"] == 1
    finally:
        client.close()


def test_conflicting_id_is_rejected_and_unknown_outcome_is_explicit(service):
    live, _core, _ue, token = service
    client, decoder = connect_authenticated(live, token)
    try:
        client.sendall(encode_frame({
            "version": 1, "kind": "outcome", "id": "missing", "payload": {},
        }))
        unknown = receive_envelope(
            client, decoder, predicate=lambda item: item["kind"] == "outcome" and item.get("id") == "missing",
        )
        assert unknown["payload"]["state"] == "unknown"

        first = make_request(code="result = 1", request_id="same", timeout=5)
        client.sendall(encode_frame({
            "version": 1, "kind": "request", "id": "same", "payload": {"request": first},
        }))
        pump_until(
            _ue, client, decoder,
            lambda item: item.get("kind") == "event"
            and item.get("payload", {}).get("response", {}).get("id") == "same",
        )
        conflict = make_request(code="result = 2", request_id="same", timeout=5)
        client.sendall(encode_frame({
            "version": 1, "kind": "request", "id": "same", "payload": {"request": conflict},
        }))
        rejected = receive_envelope(
            client, decoder, predicate=lambda item: item["kind"] == "error" and item.get("id") == "same",
        )
        assert "different payload" in rejected["payload"]["message"]
    finally:
        client.close()


def test_disconnect_after_admission_does_not_cancel_and_reconnect_reads_retained_outcome(service):
    live, core, ue, token = service
    client, decoder = connect_authenticated(live, token)
    request = make_request(
        code="runs = globals().get('runs', 0) + 1\nresult = runs",
        request_id="lost-ack", timeout=5,
    )
    client.sendall(encode_frame({
        "version": 1, "kind": "request", "id": request["id"],
        "payload": {"request": request},
    }))
    end = time.monotonic() + 1
    while ue.calls == 0 and time.monotonic() < end:
        time.sleep(0.005)
    assert ue.calls == 1
    client.close()
    end = time.monotonic() + 1
    while not ue.futures[-1].done() and time.monotonic() < end:
        ue.pump_one()
    end = time.monotonic() + 1
    while live.ledger.lookup("lost-ack")["state"] != "terminal" and time.monotonic() < end:
        time.sleep(0.005)
    assert core.scope["runs"] == 1

    reconnect, reconnect_decoder = connect_authenticated(live, token)
    try:
        reconnect.sendall(encode_frame({
            "version": 1, "kind": "outcome", "id": "lost-ack", "payload": {},
        }))
        outcome = receive_envelope(
            reconnect, reconnect_decoder,
            predicate=lambda item: item["kind"] == "outcome" and item.get("id") == "lost-ack",
        )
        assert outcome["payload"]["state"] == "terminal"
        assert outcome["payload"]["response"]["result"] == 1
        assert core.scope["runs"] == 1
    finally:
        reconnect.close()


def test_slow_peer_is_disconnected_when_reliable_write_backlog_is_full(service, monkeypatch):
    import ue4ss.live.socket_transport as transport

    live, _core, ue, token = service
    monkeypatch.setattr(transport, "MAX_PEER_WRITE_BYTES", 512)
    client, _decoder = connect_authenticated(live, token)
    request = make_request(code="print('x' * 2000)", request_id="slow-peer", timeout=5)
    client.sendall(encode_frame({
        "version": 1, "kind": "request", "id": request["id"],
        "payload": {"request": request},
    }))
    end = time.monotonic() + 1
    while ue.calls == 0 and time.monotonic() < end:
        time.sleep(0.005)
    assert ue.calls == 1
    end = time.monotonic() + 1
    while not ue.futures[-1].done() and time.monotonic() < end:
        ue.pump_one()
    end = time.monotonic() + 1
    while live.ledger.lookup("slow-peer")["state"] != "terminal" and time.monotonic() < end:
        time.sleep(0.005)
    assert live.ledger.lookup("slow-peer")["response"]["status"] == "ok"
    try:
        client.settimeout(1)
        assert client.recv(1) == b"", "oversized reliable backlog should close the slow peer"
    except socket.timeout:
        pytest.fail("slow peer connection remained open")
    except (ConnectionError, OSError):
        pass
    finally:
        client.close()


def test_pending_wait_does_not_block_ping_and_emits_later_terminal_result(service):
    live, core, ue, token = service
    client, decoder = connect_authenticated(live, token)
    try:
        core.scope["ready"] = False
        request = make_request("wait_until", code="ready", request_id="wait-net", timeout=5)
        client.sendall(encode_frame({
            "version": 1, "kind": "request", "id": request["id"], "payload": {"request": request},
        }))
        running = pump_until(
            ue, client, decoder,
            lambda item: item.get("kind") == "event"
            and item.get("id") == "wait-net"
            and item.get("payload", {}).get("state") == "waiting",
        )
        assert running
        competing = make_request(code="result = 99", request_id="competing", timeout=5)
        client.sendall(encode_frame({
            "version": 1, "kind": "request", "id": competing["id"],
            "payload": {"request": competing},
        }))
        busy = receive_envelope(
            client, decoder,
            predicate=lambda item: item.get("id") == "competing"
            and item.get("payload", {}).get("response", {}).get("id") == "competing",
        )
        assert busy["payload"]["response"]["error"]["type"] == "Busy"
        client.sendall(encode_frame({"version": 1, "kind": "ping", "payload": {}}))
        assert receive_envelope(client, decoder, predicate=lambda item: item["kind"] == "pong")
        core.scope["ready"] = True
        terminal = pump_until(
            ue, client, decoder,
            lambda item: item.get("kind") == "event"
            and item.get("payload", {}).get("response", {}).get("id") == "wait-net",
        )
        assert terminal["payload"]["response"]["result"] is True
    finally:
        client.close()


def test_auth_failure_never_schedules_game_thread_work(service):
    live, _core, ue, _token = service
    client = socket.create_connection((live.bound_address[0], live.bound_address[1]), timeout=1)
    client.settimeout(0.05)
    decoder = FrameDecoder()
    receive_envelope(client, decoder)
    client.sendall(encode_frame({
        "version": 1, "kind": "auth", "payload": {"token": "bad-token"},
    }))
    end = time.monotonic() + 1
    while time.monotonic() < end:
        try:
            if not client.recv(1):
                break
        except socket.timeout:
            continue
    assert ue.calls == 0
    client.close()


def test_discarded_native_game_thread_callback_becomes_unknown_not_a_blocking_wait(service):
    live, core, ue, token = service
    client, decoder = connect_authenticated(live, token)
    try:
        request = make_request(code="result = 'must not run'", request_id="dropped", timeout=5)
        client.sendall(encode_frame({
            "version": 1, "kind": "request", "id": request["id"],
            "payload": {"request": request},
        }))
        end = time.monotonic() + 1
        while ue.calls == 0 and time.monotonic() < end:
            time.sleep(0.005)
        assert ue.calls == 1
        assert core.scope.get("result") is None

        # Simulate native clear_pending(): the Future is cancelled and its
        # completion callback runs without waiting for the game-thread queue.
        assert ue.futures[-1].cancel()
        unknown = receive_envelope(
            client, decoder,
            predicate=lambda item: item.get("kind") == "event"
            and item.get("payload", {}).get("response", {}).get("id") == "dropped",
        )
        response = unknown["payload"]["response"]
        assert response["status"] == "unknown"
        assert response["error"]["type"] == "UnknownOutcome"
        assert core.reserve_foreground("after-drop") is True
        core._release_foreground("after-drop")
    finally:
        client.close()


def test_live_stop_flushes_terminal_ack_before_worker_shutdown(service):
    live, _core, ue, token = service
    client, decoder = connect_authenticated(live, token)
    request = make_request("stop", request_id="stop-net", timeout=5)
    client.sendall(encode_frame({
        "version": 1, "kind": "request", "id": request["id"], "payload": {"request": request},
    }))
    try:
        terminal = pump_until(
            ue, client, decoder,
            lambda item: item.get("kind") == "event"
            and item.get("payload", {}).get("response", {}).get("id") == "stop-net",
        )
        assert terminal["payload"]["response"]["result"]["stopping"] is True
        live._stop_event.wait(1)
        assert live._stop_event.is_set()
    finally:
        client.close()


def test_event_handoff_is_bounded_and_reserves_space_for_terminal_responses():
    wake_count = []
    handoff = _EventHandoff(lambda: wake_count.append(1), max_items=1, max_bytes=100)
    assert handoff.put({"event": "state", "state": "running"})
    assert not handoff.put({"event": "job", "job": {"id": "job-1"}})
    assert handoff.put({"event": "response", "record": {"id": "r1", "status": "ok"}})
    events = handoff.drain()
    assert [event["event"] for event in events] == ["response", "state"]
    assert not handoff.overflowed
    assert len(wake_count) == 3
