import json
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live_protocol


class LiveProtocolTest(unittest.TestCase):
    def test_request_has_id_deadline_and_operation(self):
        request = live_protocol.make_request("execute", code="print(1)", timeout=5, request_id="r1", now=100)
        self.assertEqual(request["protocol"], 1)
        self.assertEqual(request["deadline"], 105)
        self.assertEqual(live_protocol.validate_request(request, now=104)["id"], "r1")

    def test_request_rejects_empty_execute_and_unknown_operation(self):
        with self.assertRaises(live_protocol.ProtocolError):
            live_protocol.make_request("execute")
        with self.assertRaises(live_protocol.ProtocolError):
            live_protocol.make_request("shell", code="x")

    def test_request_rejects_expired_deadline(self):
        request = live_protocol.make_request(code="1", timeout=1, now=10)
        with self.assertRaises(live_protocol.ProtocolError):
            live_protocol.validate_request(request, now=12)
        self.assertEqual(live_protocol.validate_request(request, now=12, allow_expired=True)["id"], request["id"])

    def test_request_rejects_bad_deadline_and_oversize_code(self):
        request = live_protocol.make_request(code="x", request_id="r1", now=100)
        request["deadline"] += 1
        with self.assertRaises(live_protocol.ProtocolError):
            live_protocol.validate_request(request, now=100)
        with self.assertRaises(live_protocol.ProtocolError):
            live_protocol.make_request(code="x" * (live_protocol.MAX_CODE_BYTES + 1), request_id="r2")

    def test_response_preserves_error_and_result(self):
        response = live_protocol.make_response("r1", "error", stderr="bad", error={"type": "UEError"}, result={"x": 1}, now=20)
        decoded = live_protocol.validate_response(response)
        self.assertEqual(decoded["status"], "error")
        self.assertEqual(decoded["error"]["type"], "UEError")
        self.assertEqual(decoded["result"]["x"], 1)

    def test_response_rejects_non_json_result(self):
        with self.assertRaises(live_protocol.ProtocolError):
            live_protocol.make_response("r1", "ok", result=object())

    def test_unknown_outcome_is_a_terminal_status(self):
        response = live_protocol.make_response("r1", "unknown", error={"type": "UnknownOutcome"})
        self.assertEqual(live_protocol.validate_response(response)["status"], "unknown")

    def test_atomic_request_and_response_files(self):
        with TemporaryDirectory() as directory:
            inbox, outbox = Path(directory) / "inbox", Path(directory) / "outbox"
            request = live_protocol.make_request(code="print('ok')", request_id="same", now=time.time())
            request_path = live_protocol.write_request(inbox, request)
            self.assertEqual(json.loads(request_path.read_text())["id"], "same")
            with self.assertRaises(live_protocol.DuplicateRequest):
                live_protocol.write_request(inbox, request)
            response_path = live_protocol.write_response(outbox, live_protocol.make_response("same", "ok", result={"ok": True}))
            self.assertTrue(response_path.exists())
            self.assertEqual(live_protocol.read_record(response_path)["result"], {"ok": True})

    def test_partial_record_is_not_accepted(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "partial.json"
            path.write_text('{"protocol": 1', encoding="utf-8")
            with self.assertRaises(live_protocol.ProtocolError):
                live_protocol.read_record(path)

    def test_all_control_operations_are_supported(self):
        for operation in ("reset", "job_start", "job_status", "job_result", "cleanup", "reload", "status", "travel", "stop"):
            request = live_protocol.make_request(operation, request_id=operation, now=1)
            self.assertEqual(live_protocol.validate_request(request, now=1)["operation"], operation)
