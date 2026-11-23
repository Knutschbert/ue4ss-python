import sys
import concurrent.futures
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python" / "mock"))
import ue4ss  # noqa: E402
from ue4ss.runtime import ComponentState, ScriptRuntime  # noqa: E402


@pytest.fixture(autouse=True)
def reset_ue4ss():
    ue4ss._mock_reset()
    yield
    ue4ss._mock_reset()


def on_game_thread(callback):
    result = []

    def run():
        try:
            result.append((True, callback()))
        except BaseException as error:
            result.append((False, error))

    ue4ss.execute_in_game_thread(run)
    ue4ss._mock_run_pending()
    assert result, "game-thread callback did not run"
    if not result[0][0]:
        raise result[0][1]
    return result[0][1]


def test_session_source_load_is_ephemeral_and_reload_is_independent():
    runtime = ScriptRuntime()
    events = []
    # The source executes with its own module globals; use builtins as a stable
    # test-side event sink without creating shared files or editing a manifest.
    builtins = __import__("builtins")
    builtins._managed_test_events = events
    a1 = "import builtins\ndef start(ctx):\n    ctx.register_cleanup(lambda: builtins._managed_test_events.append('A-old'))\n"
    b = "import builtins\ndef start(ctx):\n    ctx.register_cleanup(lambda: builtins._managed_test_events.append('B'))\n"
    on_game_thread(lambda: runtime.load_source("alpha", a1))
    on_game_thread(lambda: runtime.load_source("beta", b))
    alpha_prefix = runtime._scripts["alpha"].prefix
    beta_prefix = runtime._scripts["beta"].prefix
    assert alpha_prefix != beta_prefix

    replacement = "import builtins\ndef start(ctx):\n    ctx.register_cleanup(lambda: builtins._managed_test_events.append('A-new'))\n"
    on_game_thread(lambda: runtime.reload("alpha", replacement))
    assert events == ["A-old"]
    assert runtime._scripts["beta"].prefix == beta_prefix
    assert runtime.manager.get("beta").state == ComponentState.RUNNING
    assert all(not name.startswith(alpha_prefix) for name in sys.modules)
    assert any(name.startswith(beta_prefix) for name in sys.modules)

    on_game_thread(lambda: runtime.stop("alpha"))
    on_game_thread(lambda: runtime.stop("beta"))
    assert events == ["A-old", "A-new", "B"]
    del builtins._managed_test_events


def test_syntax_error_during_reload_keeps_old_instance_and_modules():
    runtime = ScriptRuntime()
    on_game_thread(lambda: runtime.load_source("alpha", "def start(ctx):\n    pass\n"))
    old_context = runtime.manager.get("alpha")
    old_prefix = runtime._scripts["alpha"].prefix
    error = on_game_thread(lambda: _capture(
        lambda: runtime.reload("alpha", "def start(ctx):\n    if:\n")
    ))
    assert isinstance(error, SyntaxError)
    assert runtime.manager.get("alpha") is old_context
    assert runtime._scripts["alpha"].prefix == old_prefix
    assert any(name.startswith(old_prefix) for name in sys.modules)
    assert "SyntaxError" in runtime.list()[0]["error"]


def test_local_package_helpers_reload_from_fresh_source_even_same_size(tmp_path):
    package = tmp_path / "demo"
    package.mkdir()
    entry = package / "__init__.py"
    helper = package / "helper.py"
    entry.write_text(
        "from .helper import VALUE\n"
        "def start(ctx):\n"
        "    ctx.register_cleanup(lambda: __import__('builtins')._managed_values.append(VALUE))\n",
        encoding="utf-8",
    )
    helper.write_text("VALUE = 'one'\n", encoding="utf-8")
    builtins = __import__("builtins")
    builtins._managed_values = []
    runtime = ScriptRuntime(roots=(tmp_path,))
    on_game_thread(lambda: runtime.load_local("local", package))
    first_prefix = runtime._scripts["local"].prefix
    helper.write_text("VALUE = 'two'\n", encoding="utf-8")
    assert len(helper.read_text(encoding="utf-8")) == len("VALUE = 'one'\n")
    on_game_thread(lambda: runtime.reload("local"))
    assert builtins._managed_values == ["one"]
    assert runtime._scripts["local"].prefix != first_prefix
    on_game_thread(lambda: runtime.stop("local"))
    assert builtins._managed_values == ["one", "two"]
    del builtins._managed_values


def test_package_imports_are_private_and_shared_modules_are_not_removed(tmp_path):
    runtime = ScriptRuntime(roots=(tmp_path,))
    prefixes = []
    for name, value in (("first", "A"), ("second", "B")):
        package = tmp_path / name
        package.mkdir()
        (package / "__init__.py").write_text(
            "from .helper import VALUE\n"
            "def start(ctx):\n"
            "    import json\n"
            "    ctx.register_cleanup(lambda: None)\n",
            encoding="utf-8",
        )
        (package / "helper.py").write_text(f"VALUE = {value!r}\n", encoding="utf-8")
        on_game_thread(lambda name=name, package=package: runtime.load_local(name, package))
        prefixes.append(runtime._scripts[name].prefix)

    assert prefixes[0] != prefixes[1]
    assert any(name.startswith(prefixes[0] + ".helper") for name in sys.modules)
    assert any(name.startswith(prefixes[1] + ".helper") for name in sys.modules)
    shared_json = sys.modules["json"]
    on_game_thread(lambda: runtime.reload("first"))
    assert sys.modules["json"] is shared_json
    assert any(name.startswith(prefixes[1] + ".helper") for name in sys.modules)


def test_startup_failure_rolls_back_and_keeps_attributed_traceback():
    runtime = ScriptRuntime()
    builtins = __import__("builtins")
    builtins._managed_failure_cleanup = []
    source = (
        "import builtins\n"
        "def start(ctx):\n"
        "    ctx.register_cleanup(lambda: builtins._managed_failure_cleanup.append('cleaned'))\n"
        "    raise RuntimeError('bad startup')\n"
    )
    error = on_game_thread(lambda: _capture(lambda: runtime.load_source("broken", source)))
    assert "bad startup" in str(error)
    details = runtime.list()[0]
    assert details["state"] == ComponentState.FAILED
    assert "bad startup" in details["error"]
    assert builtins._managed_failure_cleanup == ["cleaned"]
    del builtins._managed_failure_cleanup


def test_reload_startup_failure_cleans_replacement_without_stopping_peer():
    runtime = ScriptRuntime()
    events = []
    builtins = __import__("builtins")
    builtins._managed_reload_events = events
    good = (
        "import builtins\n"
        "def start(ctx):\n"
        "    ctx.register_cleanup(lambda: builtins._managed_reload_events.append('old'))\n"
    )
    peer = "def start(ctx):\n    pass\n"
    on_game_thread(lambda: runtime.load_source("alpha", good))
    on_game_thread(lambda: runtime.load_source("beta", peer))
    bad = (
        "import builtins\n"
        "def start(ctx):\n"
        "    ctx.register_cleanup(lambda: builtins._managed_reload_events.append('new rollback'))\n"
        "    raise RuntimeError('replacement failed')\n"
    )
    error = on_game_thread(lambda: _capture(lambda: runtime.reload("alpha", bad)))
    assert "replacement failed" in str(error)
    assert events == ["old", "new rollback"]
    assert runtime.manager.get("beta").state == ComponentState.RUNNING
    states = {entry["name"]: entry["state"] for entry in runtime.list()}
    assert states == {"alpha": ComponentState.FAILED, "beta": ComponentState.RUNNING}
    on_game_thread(lambda: runtime.stop("beta"))
    del builtins._managed_reload_events


def test_source_size_utf8_and_name_validation():
    runtime = ScriptRuntime()
    for invalid in ("", "../escape", "contains space", "1start"):
        error = on_game_thread(lambda invalid=invalid: _capture(
            lambda: runtime.load_source(invalid, "def start(ctx): pass\n")
        ))
        assert isinstance(error, ValueError)
    too_large = "x" * (128 * 1024 + 1)
    error = on_game_thread(lambda: _capture(lambda: runtime.load_source("large", too_large)))
    assert isinstance(error, ValueError)
    error = on_game_thread(lambda: _capture(lambda: runtime.load_source("utf8", b"\xff")))
    assert isinstance(error, ValueError)


def test_local_packages_must_be_under_configured_root_and_have_entrypoint(tmp_path):
    runtime = ScriptRuntime(roots=(tmp_path / "allowed",))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "__init__.py").write_text("def start(ctx): pass\n", encoding="utf-8")
    error = on_game_thread(lambda: _capture(lambda: runtime.load_local("outside", outside)))
    assert isinstance(error, ValueError)

    inside = tmp_path / "allowed" / "no_entry"
    inside.mkdir(parents=True)
    error = on_game_thread(lambda: _capture(lambda: runtime.load_local("missing", inside)))
    assert isinstance(error, ValueError)


def test_global_reload_stops_managed_components_before_new_main_and_drops_old_work():
    runtime = ScriptRuntime()
    events = []
    source = (
        "import builtins\n"
        "def start(ctx):\n"
        "    ctx.register_cleanup(lambda: builtins._global_reload_events.append('disposed'))\n"
    )
    builtins = __import__("builtins")
    builtins._global_reload_events = events
    on_game_thread(lambda: runtime.load_source("managed", source))
    stale = []

    def queue_old_work_behind_reload():
        ue4ss.reload()
        # This task is queued behind _do_reload and must not execute in the new
        # script generation after reload clears the pending work queue.
        runtime.manager.get("managed").execute_in_game_thread(lambda: stale.append(True))

    on_game_thread(queue_old_work_behind_reload)
    ue4ss._mock_main = lambda: events.append(
        f"main:{runtime.manager.get('managed')}:{runtime.list()[0]['state']}"
    )
    ue4ss._mock_run_pending()
    assert events == ["disposed", "main:None:stopped"]
    assert stale == []
    del builtins._global_reload_events


def test_legacy_global_reload_does_not_import_managed_runtime():
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "python" / "mock")
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, ue4ss; ue4ss.reload(); ue4ss._mock_run_pending(); "
            "assert 'ue4ss.runtime' not in sys.modules",
        ],
        env=environment,
        check=True,
        timeout=10,
    )


def test_global_reload_cancels_future_for_work_removed_from_queue():
    called = []
    future = None
    new_future = []

    def enqueue_reload_then_future():
        nonlocal future
        ue4ss.reload()
        future = ue4ss.call_in_game_thread(lambda: called.append("ran"))

    on_game_thread(enqueue_reload_then_future)
    ue4ss._mock_main = lambda: new_future.append(ue4ss.call_in_game_thread(lambda: called.append("new")))
    ue4ss._mock_run_pending()
    assert future.cancelled()
    assert called == []
    with pytest.raises(concurrent.futures.CancelledError):
        future.result()
    assert len(new_future) == 1 and not new_future[0].done()
    ue4ss._mock_run_pending()
    assert new_future[0].result() is None
    assert called == ["new"]
    assert not ue4ss._pending_futures


def test_global_reload_worker_handover_is_nonblocking_and_gates_restart():
    runtime = ScriptRuntime()
    entered = __import__("threading").Event()
    release = __import__("threading").Event()
    exited = __import__("threading").Event()
    source = (
        "import threading\n"
        "def start(ctx):\n"
        "    def worker(stop_event):\n"
        "        threading.Event().wait(0.01)\n"
        "        import builtins\n"
        "        builtins._managed_worker_entered.set()\n"
        "        builtins._managed_worker_release.wait()\n"
        "        builtins._managed_worker_exited.set()\n"
        "    ctx.spawn_worker(worker)\n"
    )
    builtins = __import__("builtins")
    builtins._managed_worker_entered = entered
    builtins._managed_worker_release = release
    builtins._managed_worker_exited = exited
    on_game_thread(lambda: runtime.load_source("listener", source))
    assert entered.wait(1)
    report = []

    def new_main():
        from ue4ss.runtime import wait_for_global_handover
        wait_for_global_handover(
            lambda status: report.append(("ready", status)),
            timeout_ms=0,
            on_timeout=lambda status: report.append(("timeout", status)),
        )

    ue4ss._mock_main = new_main
    on_game_thread(lambda: ue4ss.reload())
    began = __import__("time").monotonic()
    ue4ss._mock_run_pending()
    assert __import__("time").monotonic() - began < 0.2
    assert report and report[0][0] == "timeout"
    assert report[0][1]["pending_workers"] == 1
    assert report[0][1]["workers"][0]["stop_requested"] is True
    release.set()
    assert exited.wait(1)
    on_game_thread(lambda: __import__("ue4ss.runtime", fromlist=["wait_for_global_handover"])
                   .wait_for_global_handover(lambda status: report.append(("ready", status))))
    assert report[-1][0] == "ready"
    assert report[-1][1]["pending_workers"] == 0
    del builtins._managed_worker_entered
    del builtins._managed_worker_release
    del builtins._managed_worker_exited


def _capture(callback):
    try:
        callback()
    except BaseException as error:
        return error
    raise AssertionError("expected the callback to raise")
