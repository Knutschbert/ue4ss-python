"""Verify repeated managed worker cleanup and handover across native reloads."""

import os
import threading
import ue4ss
from ue4ss.runtime import ScriptRuntime, wait_for_global_handover

STATE_NAME = "_managed_reload_handover_test"
LAST_CYCLE = int(os.environ.get("PYUE_MANAGED_RELOAD_CYCLES", "3"))
state = getattr(ue4ss, STATE_NAME, None)
generation = ue4ss.script_generation()


def check(label, condition, detail=""):
    ok = bool(condition)
    print(f"{label}: {'PASS' if ok else 'FAIL'}{f' ({detail})' if detail else ''}")
    return ok


if not 2 <= LAST_CYCLE <= 10_000:
    print(f"FAIL: PYUE_MANAGED_RELOAD_CYCLES must be between 2 and 10000, got {LAST_CYCLE}")
    print("managed reload handover finished")
    raise SystemExit(0)


def worker_source(cycle):
    return f"""
import ue4ss

def start(ctx):
    state = getattr(ue4ss, {STATE_NAME!r})
    current = state["cycles"][{cycle}]
    state["events"].append("component-start:{cycle}")
    ctx.register_cleanup(
        lambda: state["events"].append("component-cleanup:{cycle}")
    )

    def worker(stop_event):
        current["started"].set()
        stop_event.wait(15)
        state["events"].append("worker-exit:{cycle}")
        current["exited"].set()

    state["context"] = ctx
    ctx.spawn_worker(worker, name="managed-reload-handover-{cycle}")
"""


def request_reload_when_worker_started(current, context):
    if not current["started"].is_set():
        ue4ss.execute_with_delay(
            25, lambda: request_reload_when_worker_started(current, context)
        )
        return
    # The callback queued after reload must be discarded with the old native
    # generation and must not execute in the reloaded main.py.
    ue4ss.reload()
    context.execute_in_game_thread(lambda: current["stale"].append("ran"))


if generation == 1:
    state = {
        "events": [],
        "failures": [],
        "cycle": 1,
        "cycles": {
            1: {
                "started": threading.Event(),
                "exited": threading.Event(),
                "stale": [],
            },
        },
    }
    setattr(ue4ss, STATE_NAME, state)
    current = state["cycles"][1]
    runtime = ScriptRuntime()
    state["runtime"] = runtime
    context = runtime.load_source("worker", worker_source(1))
    state["context"] = context
    ue4ss.execute_with_delay(
        25, lambda: request_reload_when_worker_started(current, context)
    )

elif state is not None and generation == state["cycle"] + 1:
    cycle = state["cycle"]
    current = state["cycles"][cycle]

    def on_ready(status):
        events = state["events"]
        events.append(f"handover-ready:{cycle}")
        checks = []

        def record(label, condition, detail=""):
            checks.append((label, check(f"cycle {cycle}: {label}", condition, detail)))

        record(
            "managed cleanup ran once",
            events.count(f"component-cleanup:{cycle}") == 1,
            events,
        )
        record("old worker started", current["started"].is_set())
        record(
            "old worker exited before handover",
            current["exited"].is_set(),
            events,
        )
        record(
            "old queued callback was discarded",
            current["stale"] == [],
            current["stale"],
        )
        record(
            "handover reports no pending workers",
            status["pending_workers"] == 0,
            status,
        )
        state["failures"].extend(label for label, ok in checks if not ok)

        # The old manager/context can be released only after the handover
        # registry has observed that its worker stopped.
        state.pop("context", None)
        state.pop("runtime", None)
        if cycle == LAST_CYCLE:
            if state["failures"]:
                print("managed reload handover failures:", state["failures"])
            else:
                print("managed reload handover checks passed")
            delattr(ue4ss, STATE_NAME)
            print("managed reload handover finished")
            return

        next_cycle = cycle + 1
        state["cycle"] = next_cycle
        state["cycles"][next_cycle] = {
            "started": threading.Event(),
            "exited": threading.Event(),
            "stale": [],
        }
        replacement_runtime = ScriptRuntime()
        state["runtime"] = replacement_runtime
        replacement = replacement_runtime.load_source(
            "worker", worker_source(next_cycle)
        )
        state["context"] = replacement
        record(
            "replacement starts after old worker exit",
            events.index(f"worker-exit:{cycle}")
            < events.index(f"component-start:{next_cycle}"),
            events,
        )
        state["failures"].extend(
            label for label, ok in checks[-1:] if not ok
        )
        ue4ss.execute_with_delay(
            25,
            lambda: request_reload_when_worker_started(
                state["cycles"][next_cycle], replacement
            ),
        )

    def on_timeout(status):
        print(f"FAIL: cycle {cycle} handover timed out", status)
        print("managed reload handover finished")

    wait_for_global_handover(
        on_ready,
        timeout_ms=5000,
        poll_ms=25,
        on_timeout=on_timeout,
    )
else:
    print(f"FAIL: unexpected managed reload test generation {generation}, state={state!r}")
    print("managed reload handover finished")
