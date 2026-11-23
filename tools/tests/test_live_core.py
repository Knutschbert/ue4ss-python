"""Contract tests for the packaged execution core, independent of file transport."""

from __future__ import annotations

import math
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python" / "lib"))
from ue4ss.live.core import LiveCore  # noqa: E402
from ue4ss.live.protocol import ProtocolError, make_request, validate_request  # noqa: E402


class FakeUe4ss:
    class UEError(Exception):
        pass

    def __init__(self):
        self.scheduled = []
        self.reloads = 0

    @staticmethod
    def is_game_thread():
        return True

    def execute_with_delay(self, delay, callback):
        self.scheduled.append((delay, callback))

    def reload(self):
        self.reloads += 1

    def find_first_of(self, _name):
        return None


def core_with_events():
    fake = FakeUe4ss()
    events = []
    core = LiveCore(events.append, ue_module=fake, world_name=lambda: "/Game/Test")
    return core, fake, events


def request(operation="execute", *, code="result = True", request_id="r1", timeout=30, args=None):
    return make_request(operation, code=code, request_id=request_id, timeout=timeout, args=args)


def responses(events):
    return [event["record"] for event in events if event["event"] == "response"]


def test_core_executes_persistent_scope_and_emits_transport_neutral_events():
    core, _fake, events = core_with_events()
    first = core.dispatch(request(code="answer = 40\nprint('ok')\nresult = answer + 2"))
    second = core.dispatch(request(code="result = answer + 2", request_id="r2"))
    assert first["status"] == "ok" and first["result"] == 42
    assert first["stdout"] == "ok\n"
    assert second["result"] == 42
    assert [event["event"] for event in events].count("response") == 2
    assert events[0]["event"] == "state" and events[0]["state"] == "running"


def test_core_busy_admission_and_reset_cleanup_are_scoped():
    core, _fake, _events = core_with_events()
    core.busy = True
    rejected = core.dispatch(request(request_id="busy"))
    assert rejected["status"] == "rejected"
    assert rejected["error"]["type"] == "Busy"
    core.busy = False

    cleaned = []
    core.scope["cleaned"] = cleaned
    core.dispatch(request(code="register_cleanup('owner', 'one', lambda: cleaned.append(1))"))
    result = core.dispatch(request("reset", code="", request_id="reset"))
    assert cleaned == [1]
    assert result["result"]["generation"] == 2
    assert core.scope["result"] is None


def test_reload_keeps_legacy_reload_all_semantics_and_stop_cleans_live_resources():
    core, fake, _events = core_with_events()
    reloaded = core.dispatch(request("reload", code="", request_id="reload"))
    assert reloaded["result"] == {"reloading": True}
    assert fake.reloads == 1

    cleaned = []
    core.scope["cleaned"] = cleaned
    core.dispatch(request(code="register_cleanup('live', 'resource', lambda: cleaned.append('closed'))"))
    stopped = core.dispatch(request("stop", code="", request_id="stop"))
    assert stopped["result"]["stopping"] is True
    assert stopped["result"]["cleanup"][0]["ok"] is True
    assert cleaned == ["closed"]
    assert core.stopping is True


def test_pending_wait_advances_only_in_maintenance_and_finishes_once():
    core, _fake, events = core_with_events()
    core.scope["ready"] = False
    wait_request = make_request(
        "wait_until", code="ready", timeout=30, request_id="wait-1", now=time.time()
    )
    assert core.dispatch(wait_request) is None
    assert core.pending_wait is not None
    core.maintenance()
    assert responses(events) == []
    core.scope["ready"] = "ready"
    core.maintenance()
    assert responses(events)[0]["result"] == "ready"
    assert core.pending_wait is None
    core.maintenance()
    assert len(responses(events)) == 1
    assert core.reserve_foreground("other") is True
    core._release_foreground("other")


def test_pending_wait_holds_shared_foreground_admission_until_terminal_completion():
    core, _fake, events = core_with_events()
    core.scope["ready"] = False
    wait_request = make_request(
        "wait_until", code="ready", timeout=30, request_id="held-wait", now=time.time()
    )
    assert core.dispatch(wait_request) is None
    busy = core.dispatch(request(code="result = 'should not run'", request_id="competing"))
    assert busy["status"] == "rejected"
    assert busy["error"]["type"] == "Busy"
    core.scope["ready"] = True
    core.maintenance()
    assert core.reserve_foreground("after-wait") is True
    core._release_foreground("after-wait")


def test_wait_timeout_and_reset_emit_terminal_state():
    core, _fake, events = core_with_events()
    now = time.time()
    wait_request = make_request("wait_until", code="False", timeout=0.01, request_id="wait", now=now)
    core.dispatch(wait_request)
    core._wall = lambda: now + 1
    core.maintenance()
    assert responses(events)[0]["status"] == "timeout"
    assert core.pending_wait is None


def test_jobs_are_deferred_and_emit_completion_events_with_bounded_output():
    core, fake, events = core_with_events()
    accepted = core.dispatch(request("job_start", code="print('x' * 70000)\nresult = 42"))
    job = accepted["result"]
    assert job["state"] == "queued"
    assert fake.scheduled
    delay, callback = fake.scheduled.pop(0)
    assert delay == 0
    callback()
    event = [item for item in events if item["event"] == "job"][-1]
    assert event["job"]["state"] == "completed"
    assert event["job"]["result"] == 42
    assert len(event["job"]["stdout"]) == 64 * 1024


def test_stdout_and_huge_result_are_bounded():
    core, _fake, _events = core_with_events()
    output = core.dispatch(request(code="print('x' * 100000)\nresult = None"))
    assert len(output["stdout"]) == 64 * 1024
    huge = (
        "result = {str(i): {str(j): {str(k): 'x' * 500 for k in range(32)} "
        "for j in range(32)} for i in range(32)}"
    )
    response = core.dispatch(request(code=huge, request_id="huge"))
    assert response["status"] == "error"
    assert response["error"]["type"] == "ResultTooLarge"


def test_nonfinite_values_and_deadlines_are_rejected():
    with pytest.raises(ProtocolError):
        make_request(timeout=math.inf)
    with pytest.raises(ProtocolError):
        validate_request({
            "protocol": 1, "id": "r", "operation": "execute", "code": "x",
            "timeout": 1, "created": 0, "deadline": math.nan,
        }, now=0)
    core, _fake, _events = core_with_events()
    response = core.dispatch(request(code="result = float('nan')"))
    assert response["status"] == "ok"
    assert response["result"] == "nan"


def test_core_refuses_off_game_thread_operations():
    core, fake, _events = core_with_events()
    fake.is_game_thread = lambda: False
    failures = []

    def call():
        try:
            core.dispatch(request())
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=call)
    thread.start()
    thread.join(timeout=1)
    assert len(failures) == 1 and isinstance(failures[0], fake.UEError)


def test_managed_script_operations_are_transport_neutral_and_source_stays_session_only():
    class ScriptRuntime:
        def __init__(self):
            self.loaded = {}

        def list(self):
            return list(self.loaded.values())

        def load_source(self, name, source):
            self.loaded[name] = {"name": name, "source_kind": "session", "state": "running", "source": source}

        def start(self, name):
            self.loaded[name]["state"] = "running"

        def stop(self, name):
            self.loaded[name]["state"] = "stopped"

        def reload(self, name, source=None):
            if source is not None:
                self.loaded[name]["source"] = source
            self.loaded[name]["state"] = "running"

    fake = FakeUe4ss()
    events = []
    scripts = ScriptRuntime()
    core = LiveCore(events.append, ue_module=fake, script_runtime=scripts)
    loaded = core.dispatch(request("script_load", code="def start(ctx):\n    pass", args={"name": "Example"}))
    assert loaded["status"] == "ok"
    assert loaded["result"]["source_kind"] == "session"
    assert scripts.loaded["Example"]["source"].startswith("def start")
    core.dispatch(request("script_stop", code="", request_id="script-stop", args={"name": "Example"}))
    assert scripts.loaded["Example"]["state"] == "stopped"
    restarted = core.dispatch(request("script_reload", code="", request_id="script-reload", args={"name": "Example"}))
    assert restarted["result"]["state"] == "running"


def test_packaged_live_package_imports_from_the_mod_layout(tmp_path):
    package = tmp_path / "dlls" / "ue4ss" / "live"
    shutil.copytree(ROOT / "python" / "lib" / "ue4ss" / "live", package)
    code = (
        "import time, ue4ss; ue4ss.is_game_thread = lambda: True; "
        "ue4ss.UEError = RuntimeError; now = time.time(); "
        "from ue4ss.live import LiveCore; "
        "core = LiveCore(lambda event: None, ue_module=ue4ss); "
        "assert core.dispatch({'protocol': 1, 'id': 'q', 'operation': 'execute', "
        "'code': 'result = 7', 'args': {}, 'timeout': 5, 'created': now, 'deadline': now + 5})['result'] == 7"
    )
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env={
        "PYTHONPATH": str(tmp_path / "dlls"),
    }, check=True, timeout=10)
