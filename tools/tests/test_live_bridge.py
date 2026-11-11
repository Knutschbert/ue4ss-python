"""Behavioral tests of the generated live bridge: it is executed against a fake
`ue4ss` module instead of being inspected for source-text fragments."""
import json
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live_client as live_client_module  # noqa: E402
from live_bridge import build_bridge  # noqa: E402


class UeFake:
    """The bridge-facing surface of ue4ss: a tick scheduler, reload and travel."""

    def __init__(self):
        self.schedule = []
        self.reloads = 0
        self.opened = []

    def execute_with_delay(self, delay, fn):
        self.schedule.append((time.monotonic() + delay, fn))

    def reload(self):
        self.reloads += 1

    def find_first_of(self, what):
        return True

    def tick(self):
        """Run every callback the scheduler is due to run (jobs, next poll)."""
        now = time.monotonic()
        due = [entry for entry in self.schedule if entry[0] <= now]
        self.schedule = [entry for entry in self.schedule if entry[0] > now]
        for _, fn in due:
            fn()


class BridgeHarness(unittest.TestCase):
    def setUp(self):
        self.fake = UeFake()
        self.modules = {}
        for name in ("ue4ss", "ue4ss.engine", "ue4ss.engine.Engine"):
            self.modules[name] = types.ModuleType(name)
        root = self.modules["ue4ss"]
        root.execute_with_delay = self.fake.execute_with_delay
        root.reload = self.fake.reload
        root.find_first_of = self.fake.find_first_of
        root.UEError = type("UEError", (Exception,), {})
        root.engine = self.modules["ue4ss.engine"]
        root.engine.Engine = self.modules["ue4ss.engine.Engine"]
        root.engine.Engine.GameplayStatics = types.SimpleNamespace(
            OpenLevel=lambda world, path, blocking, options: self.fake.opened.append(path))
        self.saved_modules = {}
        for name, module in self.modules.items():
            self.saved_modules[name] = sys.modules.get(name)
            sys.modules[name] = module

    def tearDown(self):
        for name, module in self.saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def harness(self, directory, poll_ms=200):
        live = Path(directory)
        inbox = live / "inbox"
        outbox = live / "outbox"
        source = build_bridge(str(inbox), str(outbox), str(live / "alive"), poll_ms=poll_ms)
        namespace = {}
        exec(compile(source, "<bridge>", "exec"), namespace)
        return namespace, inbox, outbox

    def send(self, namespace, outbox, request_id, **fields):
        namespace["_run"]({"protocol": 1, "id": request_id, "deadline": time.time() + 120, **fields})
        response_path = Path(outbox) / (request_id + ".json")
        self.assertTrue(response_path.exists(), "no response for " + request_id)
        return json.loads(response_path.read_text(encoding="utf-8"))

    def send_inbox(self, namespace, inbox, outbox, request_id, **fields):
        Path(inbox).mkdir(parents=True, exist_ok=True)
        path = Path(inbox) / (request_id + ".json")
        path.write_text(json.dumps({"protocol": 1, "id": request_id, "deadline": time.time() + 120, **fields}), encoding="utf-8")
        self.fake.tick()  # runs _poll if scheduled; otherwise poll once directly
        if not (Path(outbox) / (request_id + ".json")).exists():
            namespace["_poll"]()
        return json.loads((Path(outbox) / (request_id + ".json")).read_text(encoding="utf-8"))


class BridgeHostTest(BridgeHarness):
    def test_generated_source_compiles(self):
        source = build_bridge("P:\\in", "P:\\out", "P:\\alive")
        compile(source, "<live-bridge>", "exec")

    def test_poll_bounds_are_rejected(self):
        with self.assertRaises(ValueError):
            build_bridge("in", "out", "alive", poll_ms=0)
        with self.assertRaises(ValueError):
            build_bridge("in", "out", "alive", poll_ms=2001)

    def test_namespaces_are_persistent_between_requests(self):
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            first = self.send(namespace, outbox, "r1", operation="execute", code="x = 41\nresult = x")
            second = self.send(namespace, outbox, "r2", operation="execute", code="result = x + 1")
            self.assertEqual(first["result"], 41)
            self.assertEqual(second["result"], 42)

    def test_duplicate_request_keeps_first_response(self):
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            outbox_path = Path(outbox)
            outbox_path.mkdir(parents=True, exist_ok=True)
            (outbox_path / "dup.json").write_text(json.dumps({"protocol": 1, "id": "dup", "status": "ok",
                                                             "result": {"first": True}}), encoding="utf-8")
            namespace["_run"]({"protocol": 1, "id": "dup", "operation": "execute", "code": "result = 9",
                               "deadline": time.time() + 120})
            self.assertEqual(json.loads((outbox_path / "dup.json").read_text(encoding="utf-8"))["result"], {"first": True})

    def test_stdout_is_truncated_to_the_output_cap(self):
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            response = self.send(namespace, outbox, "r3", operation="execute", code="print('x' * 70000)\nresult = True")
            self.assertEqual(len(response["stdout"]), 64 * 1024)

    def test_execution_exception_reports_the_traceback(self):
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            response = self.send(namespace, outbox, "r4", operation="execute", code="raise ValueError('bad code')")
            self.assertEqual(response["status"], "error")
            self.assertEqual(response["error"]["type"], "ValueError")
            self.assertIn("<live-execute>", response["error"]["traceback"])

    def test_busy_session_rejects_a_request(self):
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            namespace["_busy"] = True
            response = self.send(namespace, outbox, "r5", operation="execute", code="result = 1")
            self.assertEqual(response["status"], "rejected")
            self.assertEqual(response["error"]["type"], "Busy")
            self.assertEqual(namespace["_busy"], True, "busy state must not be cleared by the rejected request")

    def test_overlong_execution_reports_timeout(self):
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            real_time = namespace["time"]
            fake_time = types.SimpleNamespace(monotonic=(lambda c=iter(range(0, 1000, 100)): next(c)), time=real_time.time)
            namespace["time"] = fake_time
            response = self.send(namespace, outbox, "r6", operation="execute", code="result = 1", timeout=30)
            self.assertEqual(response["status"], "timeout")
            self.assertEqual(response["error"]["type"], "Timeout")

    def test_jobs_run_on_the_scheduler_and_survive_until_read(self):
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            setup = self.send(namespace, outbox, "r6b", operation="execute", code="x = 40\nresult = x")
            self.assertEqual(setup["result"], 40)
            queued = self.send(namespace, outbox, "r7", operation="job_start", code="result = x + 2")
            self.assertEqual(queued["result"]["state"], "queued")
            self.fake.tick()
            done = self.send(namespace, outbox, "r8", operation="job_result", args={"job_id": queued["result"]["id"]})
            self.assertEqual(done["result"]["state"], "completed")
            self.assertEqual(done["result"]["result"], 42)
            stale = self.send(namespace, outbox, "r9", operation="job_result", args={"job_id": "job-none"})
            self.assertEqual(stale["status"], "error")

    def test_reset_runs_cleanups_and_invalidates_jobs(self):
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            queued = self.send(namespace, outbox, "r10", operation="job_start", code="result = 1")
            setup = ("def bad():\n    raise RuntimeError('cleanup exploded')\n"
                     "register_cleanup('res', 'bad', bad)\nresult = True\n")
            self.send(namespace, outbox, "r11", operation="execute", code=setup)
            response = self.send(namespace, outbox, "r12", operation="reset")
            self.assertEqual(response["result"]["generation"], 2)
            outcomes = response["result"]["cleanup"]
            self.assertEqual(len(outcomes), 1)
            self.assertFalse(outcomes[0]["ok"])
            self.assertEqual(outcomes[0]["error"]["type"], "RuntimeError")  # kept structured as JSON
            fresh = self.send(namespace, outbox, "r13", operation="execute",
                              code="register_cleanup('res2', 'ok2', lambda: 1)\nresult = True")
            self.assertEqual(fresh["result"], True)  # register_cleanup is available in the fresh scope
            stale = self.send(namespace, outbox, "r14", operation="job_result", args={"job_id": queued["result"]["id"]})
            self.assertEqual(stale["status"], "error")

    def test_recover_marks_running_requests_as_unknown(self):
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            Path(inbox).mkdir(parents=True, exist_ok=True)
            Path(inbox, "lost.running").write_text("{}", encoding="utf-8")  # orphaned claim of an older bridge run
            namespace["_poll"]()
            response = json.loads((Path(outbox) / "lost.json").read_text(encoding="utf-8"))
            self.assertEqual(response["status"], "unknown")
            self.assertIn("restarted", response["error"]["message"])
            self.assertFalse(list(Path(inbox).glob("*.running")))

    def test_poll_claims_the_first_inbox_request(self):
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            response = self.send_inbox(namespace, inbox, outbox, "r15", operation="execute", code="result = 'from inbox'")
            self.assertEqual(response["result"], "from inbox")
            self.assertEqual([p.name for p in Path(inbox).iterdir()], [])

    def test_stop_blocks_further_polling(self):
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            self.send(namespace, outbox, "r16", operation="stop")
            self.assertTrue(namespace["_stopping"])
            Path(inbox).mkdir(parents=True, exist_ok=True)
            Path(inbox, "r17.json").write_text(json.dumps({"protocol": 1, "id": "r17", "operation": "execute",
                                                           "code": "result = 1", "deadline": time.time() + 120}), encoding="utf-8")
            self.fake.tick()
            self.assertEqual([p.name for p in Path(inbox).iterdir()], ["r17.json"])

    def test_reload_requests_reload(self):
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            self.send(namespace, outbox, "r18", operation="reload")
            self.assertEqual(self.fake.reloads, 1)

    def test_travel_opens_the_requested_level(self):
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            response = self.send(namespace, outbox, "r19", operation="travel", args={"map": "/Game/Mods/Example/Map"})
            self.assertEqual(response["result"], {"map": "/Game/Mods/Example/Map", "requested": True})
            self.assertEqual(self.fake.opened, ["/Game/Mods/Example/Map"])

    def test_expired_request_is_rejected(self):
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            response = self.send(namespace, outbox, "r21", operation="execute", code="result = 1",
                                 deadline=time.time() - 1)
            self.assertEqual(response["status"], "timeout")
            self.assertEqual(response["error"]["type"], "Timeout")

    def test_poll_survives_a_failing_heartbeat_and_keeps_serving(self):
        # a transient transport error (the heartbeat and the inbox live on the slow
        # network drive) must not kill the poll chain silently
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)

            def bad():
                raise OSError("the network drive hiccuped")

            namespace["_heartbeat"] = bad
            before = len(self.fake.schedule)
            namespace["_poll"]()  # must not raise
            self.assertEqual(len(self.fake.schedule), before + 1, "the poll must reschedule after a failure")
            Path(inbox).mkdir(parents=True, exist_ok=True)
            Path(inbox, "r20.json").write_text(json.dumps({"protocol": 1, "id": "r20", "operation": "execute",
                                                           "code": "result = 1", "deadline": time.time() + 120}), encoding="utf-8")
            namespace["_heartbeat"] = lambda: None
            namespace["_poll"]()
            response = json.loads((Path(outbox) / "r20.json").read_text(encoding="utf-8"))
            self.assertEqual(response["status"], "ok")

    def test_client_deadline_survives_an_ahead_game_clock(self):
        # the bridge validates the deadline on its own clock; the game's clock runs
        # 21 s ahead of the host's, so the client derives the offset from the
        # heartbeat (a 15 s timeout used to expire before its pickup)
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            offset = 21.0
            real_time = namespace["time"]
            fake_time = types.SimpleNamespace(
                time=lambda: real_time.time() + offset,
                monotonic=real_time.monotonic)
            namespace["time"] = fake_time
            namespace["_poll"]()  # writes the heartbeat on the game's clock
            stop = threading.Event()

            def drive():
                while not stop.is_set():
                    namespace["_poll"]()
                    time.sleep(0.01)

            worker = threading.Thread(target=drive, daemon=True)
            worker.start()
            try:
                client = live_client_module.LiveClient(directory, poll=0.01)
                response = client.request("execute", code="result = 1", timeout=15)
            finally:
                stop.set()
                worker.join(timeout=2.0)
            self.assertEqual(response["status"], "ok")

    def _register_wait(self, namespace, request_id, *, code, timeout):
        namespace["_run"]({"protocol": 1, "id": request_id, "operation": "wait_until",
                           "code": code, "timeout": timeout, "deadline": time.time() + timeout,
                           "args": {}})

    def test_wait_until_resolves_when_the_check_becomes_truthy(self):
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            self.send(namespace, outbox, "w0", operation="execute", code="ready = False\nresult = True")
            self._register_wait(namespace, "w1", code="ready", timeout=30)
            path = Path(outbox) / "w1.json"
            self.assertFalse(path.exists(), "a pending wait must not reply yet")
            self.send(namespace, outbox, "w2", operation="execute", code="ready = True\nresult = True")
            namespace["_poll"]()
            response = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(response["status"], "ok")
            self.assertEqual(response["result"], True)

    def test_wait_until_reports_timeout_when_the_condition_is_never_met(self):
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            self.send(namespace, outbox, "w3", operation="execute", code="ready = False\nresult = True")
            self._register_wait(namespace, "w4", code="ready", timeout=0.05)
            path = Path(outbox) / "w4.json"
            self.assertFalse(path.exists())
            time.sleep(0.1)
            namespace["_poll"]()
            response = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(response["status"], "timeout")
            self.assertEqual(response["error"]["type"], "Timeout")

    def test_wait_with_no_check_sleeps_for_the_full_timeout(self):
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            self._register_wait(namespace, "w5", code="", timeout=0.05)
            path = Path(outbox) / "w5.json"
            self.assertFalse(path.exists())
            time.sleep(0.1)
            namespace["_poll"]()
            response = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(response["status"], "ok")
            self.assertEqual(response["result"], {"slept": True})

    def test_reset_cancels_a_pending_wait(self):
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            self._register_wait(namespace, "w6", code="ready", timeout=30)
            path = Path(outbox) / "w6.json"
            self.assertFalse(path.exists())
            self.send(namespace, outbox, "w7", operation="reset")
            cancelled = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(cancelled["status"], "rejected")
            self.assertEqual(cancelled["error"]["type"], "Reset")
            self.assertIsNone(namespace["_pending_wait"])

    def test_second_concurrent_wait_is_rejected(self):
        with TemporaryDirectory() as directory:
            namespace, inbox, outbox = self.harness(directory)
            self._register_wait(namespace, "w8", code="ready", timeout=30)
            response = self.send(namespace, outbox, "w9", operation="wait_until", code="other", timeout=30)
            self.assertEqual(response["status"], "error")
            self.assertEqual(response["error"]["type"], "ValueError")


if __name__ == "__main__":
    unittest.main()
