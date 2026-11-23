"""Endpoint parsing, environment selection and real socket-client coverage."""

from __future__ import annotations

import concurrent.futures
import os
import queue
import sys
import threading
import time
from types import SimpleNamespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "python" / "lib"))
from live_client import (  # noqa: E402
    LiveClient,
    LiveClientError,
    SocketLiveClient,
    client_from_environment,
    parse_endpoint,
)
import live_client as live_client_module  # noqa: E402
from ue4ss.live.core import LiveCore  # noqa: E402
from ue4ss.live.security import provision_token  # noqa: E402
from ue4ss.live.socket_transport import LiveSocketService  # noqa: E402


@pytest.mark.parametrize("endpoint,expected", [
    ("tcp://127.0.0.1:12345", ("127.0.0.1", 12345)),
    ("tcp://[::1]:12345", ("::1", 12345)),
])
def test_endpoint_parser_accepts_only_numeric_loopback_tcp(endpoint, expected):
    assert parse_endpoint(endpoint) == expected


@pytest.mark.parametrize("endpoint", [
    "127.0.0.1:1234", "tcp://localhost:1234", "tcp://0.0.0.0:1234",
    "tcp://192.0.2.1:1234", "tcp://127.0.0.1", "tcp://127.0.0.1:0",
    "tcp://127.0.0.1:1234/path", "tcp://user@127.0.0.1:1234",
    "tcp://[::]:1234", "tcp://127.0.0.1:65536",
])
def test_endpoint_parser_rejects_unsafe_or_malformed_endpoints(endpoint):
    with pytest.raises(ValueError):
        parse_endpoint(endpoint)


def test_environment_precedence_keeps_file_default_and_rejects_explicit_conflicts(tmp_path, monkeypatch):
    session = tmp_path / "legacy-session"
    endpoint = "tcp://127.0.0.1:1234"
    token_file = tmp_path / "token"
    token_file.write_text("x" * 43 + "\n", encoding="ascii")
    token_file.chmod(0o600)

    monkeypatch.delenv("PYUE_LIVE_ENDPOINT", raising=False)
    monkeypatch.delenv("PYUE_LIVE_TOKEN_FILE", raising=False)
    assert isinstance(client_from_environment(session), LiveClient)

    monkeypatch.setenv("PYUE_LIVE_ENDPOINT", endpoint)
    monkeypatch.setenv("PYUE_LIVE_TOKEN_FILE", str(token_file))
    selected = client_from_environment()
    assert isinstance(selected, SocketLiveClient)
    assert selected.endpoint == endpoint
    assert isinstance(client_from_environment(session), LiveClient)  # explicit legacy dir wins
    with pytest.raises(LiveClientError, match="mutually exclusive"):
        client_from_environment(session, endpoint=endpoint)


class GameQueue:
    class UEError(Exception):
        pass

    def __init__(self):
        self.game_thread = threading.get_ident()
        self.queue = queue.Queue()

    def is_game_thread(self):
        return threading.get_ident() == self.game_thread

    def call_in_game_thread(self, callback, *args, **kwargs):
        future = concurrent.futures.Future()

        def run():
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(callback(*args, **kwargs))
                except BaseException as error:
                    future.set_exception(error)

        self.queue.put(run)
        return future

    def execute_with_delay(self, _delay, callback):
        self.queue.put(callback)

    def find_first_of(self, _name):
        return None

    def reload(self):
        pass

    def pump(self):
        try:
            self.queue.get_nowait()()
        except queue.Empty:
            pass


def test_socket_client_performs_handshake_exec_and_explicit_outcome_query(tmp_path, monkeypatch):
    ue = GameQueue()
    core = LiveCore(lambda _event: None, ue_module=ue, world_name=lambda: "client-test")
    token = "client-token-" + "z" * 40
    service = LiveSocketService(core, token, port=0, ue_module=ue)
    stop = threading.Event()
    worker = threading.Thread(target=service.run, args=(stop,), daemon=True)
    worker.start()
    deadline = time.monotonic() + 2
    while service.bound_address is None and service.error is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert service.bound_address is not None, service.error
    token_file = tmp_path / "token"
    token_file.write_text(token + "\n", encoding="ascii")
    token_file.chmod(0o600)
    client = SocketLiveClient(
        f"tcp://{service.bound_address[0]}:{service.bound_address[1]}", token_file
    )
    # Model a host clock 90 seconds ahead of the game server. The client must
    # derive an RTT-aware offset and still send a deadline the server accepts.
    wall_time = time.time
    monkeypatch.setattr(
        live_client_module,
        "time",
        SimpleNamespace(time=lambda: wall_time() + 90.0, monotonic=time.monotonic),
    )
    try:
        assert client.ping()["instance_id"] == service.instance_id
        assert client.clock_offset == pytest.approx(-90.0, abs=1.0)
        result = []
        errors = []

        def execute():
            try:
                result.append(client.request(
                    "execute", code="answer = 40\nresult = answer + 2",
                    timeout=3,
                ))
            except BaseException as error:
                errors.append(error)

        requester = threading.Thread(target=execute, daemon=True)
        requester.start()
        until = time.monotonic() + 4
        while requester.is_alive() and time.monotonic() < until:
            ue.pump()
            requester.join(timeout=0.005)
        assert not requester.is_alive()
        assert not errors
        assert result[0]["result"] == 42
        assert client.instance_id == service.instance_id
        outcome = client.outcome(result[0]["id"])
        assert outcome["state"] == "terminal"
        assert outcome["response"]["result"] == 42
    finally:
        service.request_stop()
        worker.join(timeout=2)
        assert not worker.is_alive()


def test_mcp_network_diagnostics_never_read_local_bridge_files():
    import live_mcp

    class EndpointDown:
        is_socket = True
        endpoint = "tcp://127.0.0.1:1234"
        connect_timeout = 1

        def ping(self, **_kwargs):
            raise LiveClientError("connection refused")

    state, advice = live_mcp.bridge_state(EndpointDown())
    assert state == "unavailable"
    assert EndpointDown.endpoint in advice
    assert "cannot inspect" in advice


def test_token_factory_is_used_without_printing_or_cli_token_arguments(tmp_path):
    token_file = tmp_path / "secure" / "live.token"
    token = provision_token(token_file)
    client = client_from_environment(endpoint="tcp://127.0.0.1:1234", token_file=token_file)
    assert isinstance(client, SocketLiveClient)
    assert client.token == token
    assert token not in repr(client.endpoint)


def test_reconnect_after_instance_change_reports_unknown_instead_of_retrying(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("x" * 43 + "\n", encoding="ascii")
    token_file.chmod(0o600)
    client = SocketLiveClient("tcp://127.0.0.1:1234", token_file)
    client._request_instances["prior-request"] = "old-instance"

    class Sock:
        def close(self):
            pass

    client._connect = lambda: (Sock(), None)
    # _connect normally sets this based on the new authenticated hello.
    client.instance_id = "new-instance"
    outcome = client.outcome("prior-request")
    assert outcome["state"] == "unknown"
    assert outcome["instance_id"] == "new-instance"
    assert "changed" in outcome["reason"]


def test_ambiguous_socket_failure_is_not_replayed_and_carries_request_identity(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("x" * 43 + "\n", encoding="ascii")
    token_file.chmod(0o600)
    client = SocketLiveClient("tcp://127.0.0.1:1234", token_file)
    client.instance_id = "instance-a"
    attempts = []

    class BrokenSocket:
        def sendall(self, _data):
            attempts.append("send")
            raise ConnectionResetError("connection reset after partial write")

        def close(self):
            pass

    client._connect = lambda: (BrokenSocket(), None)
    with pytest.raises(LiveClientError) as caught:
        client.request("execute", code="result = 1", timeout=1)
    assert attempts == ["send"]
    assert caught.value.request_id
    assert caught.value.instance_id == "instance-a"
    assert "without replay" in str(caught.value)
