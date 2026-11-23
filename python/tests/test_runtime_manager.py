import gc
import sys
import threading
import time
import weakref
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python" / "mock"))
import ue4ss  # noqa: E402
from ue4ss.runtime import (  # noqa: E402
    CleanupError,
    ComponentStartError,
    ComponentState,
    RuntimeManager,
)


@pytest.fixture(autouse=True)
def reset_ue4ss():
    ue4ss._mock_reset()
    yield
    ue4ss._mock_reset()


def on_game_thread(callback):
    result = []
    ue4ss.execute_in_game_thread(lambda: result.append(callback()))
    ue4ss._mock_run_pending()
    return result[0] if result else None


def test_partial_start_rolls_back_in_reverse_order_and_can_retry():
    manager = RuntimeManager()
    disposed = []

    def failed_start(ctx):
        ctx.register_cleanup(lambda: disposed.append("first"))
        ctx.register_cleanup(lambda: disposed.append("second"))
        raise ValueError("startup broke")

    error = on_game_thread(lambda: _raises(ComponentStartError, manager.start, "component", failed_start))
    assert "startup broke" in str(error)
    assert disposed == ["second", "first"]
    assert on_game_thread(lambda: manager.start("component", lambda _ctx: None)).state == ComponentState.RUNNING


def test_cleanup_errors_are_aggregated_and_stop_is_idempotent():
    manager = RuntimeManager()
    order = []

    def start(ctx):
        ctx.register_cleanup(lambda: (_ for _ in ()).throw(ValueError("one")))
        ctx.register_cleanup(lambda: order.append("cleaned"))
        ctx.register_cleanup(lambda: (_ for _ in ()).throw(RuntimeError("two")))

    ctx = on_game_thread(lambda: manager.start("A", start))
    error = on_game_thread(lambda: _raises(CleanupError, manager.stop, "A"))
    assert len(error.errors) == 2
    assert order == ["cleaned"]
    on_game_thread(lambda: manager.stop("A"))


def test_two_components_only_dispose_their_own_resources():
    manager = RuntimeManager()
    disposed = []

    a = on_game_thread(lambda: manager.start("A", lambda ctx: ctx.register_cleanup(lambda: disposed.append("A"))))
    b = on_game_thread(lambda: manager.start("B", lambda ctx: ctx.register_cleanup(lambda: disposed.append("B"))))
    on_game_thread(lambda: manager.stop("A"))
    assert disposed == ["A"]
    assert b.state == ComponentState.RUNNING
    on_game_thread(lambda: manager.stop("B"))
    assert disposed == ["A", "B"]


def test_child_scope_has_independent_cleanup_and_stops_with_parent():
    manager = RuntimeManager()
    disposed = []
    child_holder = []

    def start(ctx):
        ctx.register_cleanup(lambda: disposed.append("parent"))
        child = ctx.child_scope("script")
        child.register_cleanup(lambda: disposed.append("child"))
        child_holder.append(child)

    on_game_thread(lambda: manager.start("overlay", start))
    child = child_holder[0]
    on_game_thread(lambda: child.stop())
    assert disposed == ["child"]
    assert child.state == ComponentState.STOPPED
    assert manager.components["overlay"].state == ComponentState.RUNNING
    on_game_thread(lambda: manager.stop("overlay"))
    assert disposed == ["child", "parent"]


def test_guarded_stale_nested_callback_is_skipped_and_runs_in_owner_context():
    manager = RuntimeManager()
    from ue4ss.runtime import current_context, run_with_context
    seen = []

    def start(ctx):
        def first():
            seen.append(current_context())
            run_with_context(None, lambda: seen.append(current_context()))

            def raising():
                assert current_context() is None
                raise ValueError("expected")

            try:
                run_with_context(None, raising)
            except ValueError:
                pass
            seen.append(current_context())
            ctx.register_cleanup(lambda: seen.append("nested cleanup"))
            ctx.execute_in_game_thread(lambda: seen.append("nested callback"))
            manager.stop("A")
        ctx.execute_in_game_thread(first)
        return ctx

    ctx = on_game_thread(lambda: manager.start("A", start))
    ue4ss._mock_run_pending()
    assert seen == [ctx, None, ctx, "nested cleanup"]
    ue4ss._mock_run_pending()
    assert seen == [ctx, None, ctx, "nested cleanup"]
    assert current_context() is None


def test_invalidating_delayed_callback_releases_user_closure():
    manager = RuntimeManager()
    released = []

    class Payload:
        pass

    payload = Payload()
    reference = weakref.ref(payload)
    ctx = on_game_thread(lambda: manager.start("A", lambda ctx: ctx.execute_with_delay(
        60_000, lambda value: released.append(value), payload
    )))
    del payload
    gc.collect()
    assert reference() is not None
    on_game_thread(lambda: manager.stop("A"))
    gc.collect()
    assert reference() is None
    ue4ss._mock_run_pending()
    assert released == []


def test_component_registration_and_lifecycle_refuse_off_thread_calls():
    manager = RuntimeManager()
    ctx = on_game_thread(lambda: manager.start("A", lambda _ctx: None))
    outcomes = []

    def outside():
        for call in (lambda: manager.stop("A"), lambda: ctx.register_cleanup(lambda: None)):
            try:
                call()
            except BaseException as error:
                outcomes.append(error)

    thread = threading.Thread(target=outside)
    thread.start()
    thread.join()
    assert len(outcomes) == 2
    assert all(isinstance(error, ue4ss.UEError) for error in outcomes)
    on_game_thread(lambda: manager.stop("A"))


def test_worker_is_signalled_without_join_and_blocks_restart_until_exit():
    manager = RuntimeManager()
    entered = threading.Event()
    release = threading.Event()
    exited = threading.Event()

    def start(ctx):
        def worker(_stop_event):
            entered.set()
            release.wait()
            exited.set()
        return ctx.spawn_worker(worker)

    handle = on_game_thread(lambda: manager.start("A", start).workers[0])
    assert entered.wait(1)
    ctx = manager.components["A"]
    on_game_thread(lambda: manager.stop("A"))
    assert handle.stop_event.is_set()
    assert ctx.state == ComponentState.STOPPING
    error = on_game_thread(lambda: _raises(RuntimeError, manager.start, "A", lambda _ctx: None))
    assert "still stopping" in str(error)
    release.set()
    assert exited.wait(1)
    handle.thread.join(timeout=1)
    assert ctx.state == ComponentState.STOPPED
    assert on_game_thread(lambda: manager.start("A", lambda _ctx: None)).state == ComponentState.RUNNING


def test_worker_failures_are_available_as_non_traceback_diagnostics():
    manager = RuntimeManager()

    def start(ctx):
        def worker(_stop_event):
            raise ValueError("worker failed")
        return ctx.spawn_worker(worker)

    handle = on_game_thread(lambda: manager.start("A", start).workers[0])
    deadline = time.monotonic() + 1
    while handle.alive and time.monotonic() < deadline:
        time.sleep(0.001)
    assert not handle.alive
    assert handle.error == "ValueError: worker failed"
    assert manager.components["A"].diagnostics["workers"][0]["error"] == handle.error
    on_game_thread(lambda: manager.stop("A"))


def test_owned_key_callbacks_are_guarded_and_unregistered():
    manager = RuntimeManager()
    calls = []
    key = int(ue4ss.Key.F6)
    ctx = on_game_thread(lambda: manager.start("A", lambda ctx: ctx.register_key_down(key, lambda: calls.append("A"))))
    ue4ss._selftest_fire_key(key)
    assert calls == ["A"]
    on_game_thread(lambda: manager.stop("A"))
    ue4ss._selftest_fire_key(key)
    assert calls == ["A"]
    assert ctx.state == ComponentState.STOPPED


def test_all_owned_registration_adapters_guard_callbacks_and_unregister(monkeypatch):
    manager = RuntimeManager()
    observed = []
    callbacks = {}
    removed = []

    def register(identifier, *args, **kwargs):
        callback = kwargs.get("pre") or kwargs.get("callback")
        if callback is None and len(args) > 0:
            callback = args[-1]
        callbacks[identifier] = callback
        return identifier

    def unregister(identifier):
        removed.append(identifier)

    for register_name, unregister_name in (
        ("register_key_down", "unregister_key_down"),
        ("register_hook", "unregister_hook"),
        ("bind_event", "unbind_event"),
        ("notify_on_new_object", "unregister_notify_on_new_object"),
        ("register_replacement", "unregister_replacement"),
    ):
        monkeypatch.setattr(ue4ss, register_name, lambda *a, _name=register_name, **kw: register(_name, *a, **kw))
        monkeypatch.setattr(ue4ss, unregister_name, lambda *a, _name=unregister_name, **kw: unregister(_name))

    def callback(*_args):
        observed.append(manager.current_context())

    def start(ctx):
        ctx.register_key_down(int(ue4ss.Key.F6), callback)
        ctx.register_hook("/mock/path", pre=callback)
        ctx.bind_event(object(), "Changed", callback)
        ctx.notify_on_new_object("/mock/Class", callback)
        ctx.register_replacement("/mock/Function", callback)
        return ctx

    ctx = on_game_thread(lambda: manager.start("A", start))
    for guarded in callbacks.values():
        guarded()
    assert observed == [ctx] * 5
    on_game_thread(lambda: manager.stop("A"))
    assert len(removed) == 5
    for guarded in callbacks.values():
        guarded()
    assert observed == [ctx] * 5


def test_adopted_resource_cleanup_is_supported():
    manager = RuntimeManager()
    removed = []
    ctx = on_game_thread(lambda: manager.start("A", lambda ctx: ctx.adopt(lambda: removed.append("external"))))
    on_game_thread(lambda: manager.stop("A"))
    assert removed == ["external"]


def _raises(exception, function, *args):
    try:
        function(*args)
    except exception as error:
        return error
    raise AssertionError(f"expected {exception.__name__}")
