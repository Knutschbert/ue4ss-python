import json
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live_client import LiveClient, LiveClientError  # noqa: E402
from live_mcp import handle  # noqa: E402


class LiveClientMcpTest(unittest.TestCase):
    def test_status_uses_heartbeat(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "alive").write_text(str(time.time()))
            self.assertTrue(LiveClient(root).is_alive())

    def test_game_clock_skew_reads_the_heartbeat(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "alive").write_text(str(time.time() + 21), encoding="utf-8")
            self.assertAlmostEqual(LiveClient(root).game_clock_skew(), 21.0, delta=2.0)

    def test_game_clock_skew_without_a_heartbeat_is_none(self):
        self.assertIsNone(LiveClient("/tmp/no-live-session").game_clock_skew())
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "alive").write_text("not a number", encoding="utf-8")
            self.assertIsNone(LiveClient(root).game_clock_skew())

    def test_is_alive_reports_a_stale_heartbeat_dead(self):
        # Note: the heartbeat mtime is on the bridge's own clock, so when the game's
        # clock is ahead of ours a dead bridge reads alive for the offset plus the
        # max_age window - tolerated; the offset cannot be derived from the same file
        # without canceling the staleness. A falsely-dead live bridge is impossible.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            heartbeat = root / "alive"
            stale = time.time() - 60
            heartbeat.write_text(str(stale), encoding="utf-8")
            os.utime(heartbeat, (stale, stale))
            self.assertFalse(LiveClient(root).is_alive(max_age=6.0))


    def test_initialize_returns_mcp_capabilities(self):
        response = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, LiveClient("/tmp/no-live-session"))
        self.assertEqual(response["result"]["protocolVersion"], "2024-11-05")
        self.assertIn("tools", response["result"]["capabilities"])

    def test_client_reads_terminal_response(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            client = LiveClient(root, poll=0)
            original = client.request

            def fake_request(*args, **kwargs):
                return {"status": "ok", "result": {"value": 3}}

            client.request = fake_request
            result = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "live_execute", "arguments": {"code": "result=3"}}}, client)
            self.assertFalse(result["result"]["isError"])
            self.assertIn('"value": 3', result["result"]["content"][0]["text"])
            client.request = original

    def test_mcp_lists_tools_and_unknown_tool_is_error(self):
        client = LiveClient("/tmp/no-live-session")
        listed = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, client)
        self.assertEqual(len(listed["result"]["tools"]), 11)
        result = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "nope"}}, client)
        self.assertTrue(result["result"]["isError"])

    def test_mcp_live_wait_until_maps_to_the_wait_operation(self):
        captured = {}

        class RecordingClient(LiveClient):
            def request(self, operation, *, code="", args=None, timeout=30.0):
                captured.update(operation=operation, code=code, timeout=timeout)
                return {"status": "ok", "result": {"value": True}}

        result = handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                         "params": {"name": "live_wait_until", "arguments": {"check": "ready", "timeout": 45}}},
                        RecordingClient("/tmp/no-live-session"))
        self.assertFalse(result["result"]["isError"])
        self.assertEqual(captured, {"operation": "wait_until", "code": "ready", "timeout": 45.0})

    def test_mcp_live_sleep_maps_to_a_checkless_wait(self):
        captured = {}

        class RecordingClient(LiveClient):
            def request(self, operation, *, code="", args=None, timeout=30.0):
                captured.update(operation=operation, code=code, timeout=timeout)
                return {"status": "ok", "result": {"slept": True}}

        result = handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                         "params": {"name": "live_sleep", "arguments": {"seconds": 12}}},
                        RecordingClient("/tmp/no-live-session"))
        self.assertFalse(result["result"]["isError"])
        self.assertEqual(captured, {"operation": "wait_until", "code": "", "timeout": 12.0})

    def test_mcp_converts_execution_exception_to_error_result(self):
        class FailingClient:
            def request(self, *args, **kwargs):
                raise LiveClientError({"type": "ValueError", "message": "bad code", "traceback": "..."}.__repr__())

        result = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "live_execute", "arguments": {"code": "raise ValueError()"}}}, FailingClient())
        self.assertTrue(result["result"]["isError"])
        self.assertIn("bad code", result["result"]["content"][0]["text"])

    def test_client_reports_missing_response(self):
        with TemporaryDirectory() as directory:
            with self.assertRaises(LiveClientError):
                LiveClient(directory, poll=0).request("status", timeout=0.01)
