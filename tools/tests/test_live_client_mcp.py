import json
import io
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live_client import LiveClient, LiveClientError, client_from_environment  # noqa: E402
import live_mcp  # noqa: E402
from live_mcp import handle  # noqa: E402


class LiveClientMcpTest(unittest.TestCase):
    def test_client_accepts_an_explicit_session_folder(self):
        self.assertEqual(client_from_environment("/tmp/foreign-session").root, Path("/tmp/foreign-session"))

    def test_mcp_entry_point_accepts_an_explicit_session_folder(self):
        saved = sys.stdin
        try:
            sys.stdin = io.StringIO("")
            self.assertEqual(live_mcp.main(["--session-dir", "/tmp/foreign-session"]), 0)
        finally:
            sys.stdin = saved

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
            (root / "alive").write_text(str(time.time()))          # a running bridge
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
        self.assertEqual(len(listed["result"]["tools"]), 12)
        result = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "nope"}}, client)
        self.assertTrue(result["result"]["isError"])

    def test_mcp_live_wait_until_maps_to_the_wait_operation(self):
        captured = {}

        class RecordingClient(LiveClient):
            def is_alive(self, **kwargs):
                return True

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
            def is_alive(self, **kwargs):
                return True

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
            def is_alive(self, **kwargs):
                return True

            def request(self, *args, **kwargs):
                raise LiveClientError({"type": "ValueError", "message": "bad code", "traceback": "..."}.__repr__())

        result = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "live_execute", "arguments": {"code": "raise ValueError()"}}}, FailingClient())
        self.assertTrue(result["result"]["isError"])
        self.assertIn("bad code", result["result"]["content"][0]["text"])

    def test_client_reports_missing_response(self):
        with TemporaryDirectory() as directory:
            with self.assertRaises(LiveClientError):
                LiveClient(directory, poll=0).request("status", timeout=0.01)


def call(client, name, arguments=None):
    return handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}}, client)["result"]


class ScriptedClient(LiveClient):
    """A client whose bridge is alive and answers from a table; records the requests."""

    def __init__(self, root, answers=None):
        super().__init__(root, poll=0)
        self.answers, self.requests = answers or {}, []

    def is_alive(self, **kwargs):
        return True

    def request(self, operation, *, code="", args=None, timeout=30.0):
        self.requests.append((operation, code, timeout))
        answer = self.answers.get(operation, {"status": "ok", "result": None})
        if isinstance(answer, Exception):
            raise answer
        return answer


class NoGameTest(unittest.TestCase):
    """The user starts the game; without a running bridge every tool says what to do, at once."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.mod = Path(self._tmp.name) / "PythonScripting"
        (self.mod / "python_scripts").mkdir(parents=True)
        self.client = LiveClient(self.mod / "live", poll=0)

    def tearDown(self):
        self._tmp.cleanup()

    def install_bridge(self):
        (self.mod / "python_scripts" / "main.py").write_text(live_mcp.BRIDGE_MARK + ": resident bridge\n")

    def test_not_installed_is_said_and_no_tool_waits(self):
        (self.mod / "python_scripts" / "main.py").write_text("print('the user own script')")
        self.assertEqual(live_mcp.bridge_state(self.client)[0], "not_installed")
        began = time.monotonic()
        for tool in live_mcp.TOOLS:
            if tool["name"] == "live_status":
                continue
            result = call(self.client, tool["name"], {"code": "1", "timeout": 30, "seconds": 30, "map": "x", "job_id": "j"})
            self.assertTrue(result["isError"], tool["name"])
            self.assertIn("live.py install", result["content"][0]["text"], tool["name"])
        self.assertLess(time.monotonic() - began, 2.0)            # nothing waited for a response
        self.assertFalse((self.mod / "live" / "inbox").exists())  # and nothing was sent

    def test_installed_but_never_run_and_stale_are_told_apart(self):
        self.install_bridge()
        state, advice = live_mcp.bridge_state(self.client)
        self.assertEqual(state, "not_running")
        self.assertIn("has never run", advice)
        (self.mod / "live").mkdir()
        heartbeat = self.mod / "live" / "alive"
        heartbeat.write_text(str(time.time() - 600))
        import os
        os.utime(heartbeat, (time.time() - 600, time.time() - 600))
        state, advice = live_mcp.bridge_state(self.client)
        self.assertEqual(state, "not_running")
        self.assertIn("stale", advice)
        self.assertIn("does not start games", advice)

    def test_status_without_a_bridge_is_not_an_error_and_carries_the_advice(self):
        result = call(self.client, "live_status")
        self.assertFalse(result["isError"])
        value = json.loads(result["content"][0]["text"])
        self.assertEqual((value["alive"], value["state"]), (False, "not_installed"))
        self.assertIn("live.py install", value["what_to_do"])

    def test_status_with_a_bridge_reports_level_and_generation(self):
        client = ScriptedClient(self.mod / "live", {"status": {"status": "ok", "result": {"alive": True, "generation": 3, "level": "World /Game/Maps/A.A", "jobs": []}}})
        value = json.loads(call(client, "live_status")["content"][0]["text"])
        self.assertEqual((value["state"], value["generation"], value["level"]), ("alive", 3, "World /Game/Maps/A.A"))


class ToolSetTest(unittest.TestCase):
    def test_initialize_tells_the_assistant_the_workflow(self):
        result = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, LiveClient("/tmp/no-live-session"))["result"]
        self.assertIn("never starts", result["instructions"])
        self.assertIn("live_status", result["instructions"])

    def test_every_tool_is_described_and_none_starts_or_installs_anything(self):
        names = [tool["name"] for tool in live_mcp.TOOLS]
        self.assertEqual(len(names), len(set(names)))
        for tool in live_mcp.TOOLS:
            self.assertGreater(len(tool["description"]), 40, tool["name"])
            self.assertEqual(tool["inputSchema"]["type"], "object", tool["name"])
            for required in tool["inputSchema"].get("required", []):
                self.assertIn(required, tool["inputSchema"]["properties"], tool["name"])
        self.assertFalse([name for name in names if any(word in name for word in ("start_game", "launch", "install"))])
        by_name = {tool["name"]: tool["description"] for tool in live_mcp.TOOLS}
        self.assertIn("does not start a game", by_name["live_travel"])
        self.assertIn("The game keeps running", by_name["live_stop"])

    def test_the_documentation_lists_exactly_the_tools(self):
        import re
        text = (Path(__file__).resolve().parents[2] / "docs" / "LIVE.md").read_text(encoding="utf-8")
        documented = set(re.findall(r"^\| `(live_[a-z_]+)`", text, flags=re.M))
        self.assertEqual(documented, {tool["name"] for tool in live_mcp.TOOLS})


class WaitReadyTest(unittest.TestCase):
    def test_ready_returns_the_level(self):
        client = ScriptedClient("/tmp/x", {"wait_until": {"status": "ok", "result": True},
                                            "status": {"status": "ok", "result": {"level": "World /Game/Maps/A.A", "generation": 2}}})
        result = call(client, "live_wait_ready", {"timeout": 20})
        self.assertFalse(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"]), {"ready": True, "level": "World /Game/Maps/A.A", "generation": 2})
        operation, check, timeout = client.requests[0]
        self.assertEqual((operation, timeout), ("wait_until", 20.0))
        compile(check, "<check>", "eval")                              # the check is a valid expression
        self.assertIn("PlayerController", check)

    def test_without_a_pawn_requirement_only_the_world_is_checked(self):
        client = ScriptedClient("/tmp/x", {"wait_until": {"status": "ok", "result": True}})
        call(client, "live_wait_ready", {"require_pawn": False})
        self.assertTrue(client.requests[0][1].startswith("ue4ss.find_first_of('World') is not None and (not False or"))

    def test_a_timeout_says_what_is_missing(self):
        client = ScriptedClient("/tmp/x", {"wait_until": LiveClientError("live operation wait_until failed: timeout"),
                                            "execute": {"status": "ok", "result": {"world": True, "player_controller": True, "pawn": False}}})
        result = call(client, "live_wait_ready", {"timeout": 5})
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn("not ready after 5 s: missing pawn", text)
        self.assertIn("require_pawn=false", text)
        compile(client.requests[1][1], "<report>", "exec")             # the diagnostic code is valid Python

    def test_the_check_and_the_report_run_against_objects(self):
        import types
        pawn_owner = types.SimpleNamespace(Pawn=object())
        things = {"World": object(), "PlayerController": pawn_owner}
        fake = types.SimpleNamespace(find_first_of=lambda name: things.get(name))
        self.assertTrue(eval(live_mcp.READY_CHECK.format(require_pawn=True), {"ue4ss": fake}))
        pawn_owner.Pawn = None
        self.assertFalse(eval(live_mcp.READY_CHECK.format(require_pawn=True), {"ue4ss": fake}))
        self.assertTrue(eval(live_mcp.READY_CHECK.format(require_pawn=False), {"ue4ss": fake}))
        things["PlayerController"] = None                              # no controller at all: no exception, just not ready
        self.assertFalse(eval(live_mcp.READY_CHECK.format(require_pawn=True), {"ue4ss": fake}))
        scope = {"ue4ss": fake}
        exec(live_mcp.READY_REPORT, scope)
        self.assertEqual(scope["result"], {"world": True, "player_controller": False, "pawn": False})
