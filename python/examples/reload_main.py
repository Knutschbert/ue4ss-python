# Hot reload: main.py reloads itself five times in a row. Generation 1 registers a hook, a key binding, a new-object
# notification, a delayed task, a worker thread and a helper module; generation 2 proves none of the old registrations is alive
# and that the module source and the names in __main__ are fresh, then asks for three reloads at once (which must make one);
# each later generation repeats the check against all older ones; generation 6 prints the verdict. Run as main.py itself (manifest "startup").
import importlib
import os
import sys
import threading

import ue4ss
from ue4ss import Key

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(SCRIPTS, "reload_helper_mod.py")
state = getattr(ue4ss, "_reload_test_state", None)
if state is None:
    state = ue4ss._reload_test_state = {"old_hook": 0, "old_key": 0, "old_new": 0, "old_task": 0, "hits": {}, "results": []}
generation = ue4ss.script_generation()
LAST = 6                                                     # five reloads in a row


def check(label, ok, detail=""):
    state["results"].append((label, bool(ok), str(detail)[:160]))
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + str(detail)[:160] + ')') if detail else ''}")


actors = sorted(ue4ss.find_all_of("PyTestActor"), key=lambda a: a.get_fname().to_string())
a1 = actors[1]


def write_helper(value):
    with open(HELPER, "w", encoding="utf-8") as handle:
        handle.write(f"VALUE = {value}\n")


if generation == 1:
    OLD_MARKER = "defined by generation 1"                 # must be gone after the reload
    write_helper(1)
    sys.path.insert(0, SCRIPTS) if SCRIPTS not in sys.path else None
    import reload_helper_mod
    check("generation 1 starts", ue4ss.script_generation() == 1 and reload_helper_mod.VALUE == 1)
    ue4ss.register_hook("/Script/PyTestGame.PyTestActor:AddInts", pre=lambda c, p: state.__setitem__("old_hook", state["old_hook"] + 1))
    ue4ss.register_key_down(Key.F11, lambda: state.__setitem__("old_key", state["old_key"] + 1))
    ue4ss.notify_on_new_object("/Script/Engine.Actor", lambda o: state.__setitem__("old_new", state["old_new"] + 1))
    ue4ss.execute_with_delay(2500, lambda: state.__setitem__("old_task", state["old_task"] + 1))   # must never run: dropped by the reload

    def old_worker():
        while ue4ss.script_generation() == 1:
            pass
    worker = threading.Thread(target=old_worker, name="generation-1-worker", daemon=True)
    worker.start()
    state["worker"] = worker
    write_helper(2)                                          # the next import must see this
    ue4ss.reload()
    # tasks queued before reload() still run first (the queue is in order); ones queued after it are dropped with the reload
    ue4ss.execute_in_game_thread(lambda: state.__setitem__("old_task", state["old_task"] + 100))

else:
    import reload_helper_mod
    previous = {g: dict(h) for g, h in state["hits"].items()}
    check(f"generation {generation} is running (reload {generation - 1} of {LAST - 1})", ue4ss.script_generation() == generation)
    if generation == 2:
        check("names main.py defined are gone", "OLD_MARKER" not in globals() and "OLD_MARKER" not in sys.modules["__main__"].__dict__)
        check("the helper module was imported again from the new source", reload_helper_mod.VALUE == 2, reload_helper_mod.VALUE)
        check("the old delayed and queued tasks were dropped", state["old_task"] == 0, state["old_task"])
    state["hits"][generation] = {"hook": 0, "key": 0, "new": 0}
    mine = state["hits"][generation]
    ue4ss.register_hook("/Script/PyTestGame.PyTestActor:AddInts", pre=lambda c, p, mine=mine: mine.__setitem__("hook", mine["hook"] + 1))
    ue4ss.register_key_down(Key.F11, lambda mine=mine: mine.__setitem__("key", mine["key"] + 1))
    ue4ss.notify_on_new_object("/Script/Engine.Actor", lambda o, mine=mine: mine.__setitem__("new", mine["new"] + 1))
    a1.AddInts(1, 2)
    check(f"generation {generation}: only its own hook fires", mine["hook"] == 1 and all(state["hits"][g]["hook"] == previous[g]["hook"] for g in previous), state["hits"])
    check(f"generation {generation}: a key press reaches only its own binding", ue4ss._selftest_fire_key(Key.F11) == 1)

    def after_tick():
        check(f"generation {generation}: key callbacks of older generations did not run", mine["key"] == 1 and all(state["hits"][g]["key"] == previous[g]["key"] for g in previous))
        actors[0].SpawnMore(1)
        ue4ss.execute_with_delay(300, after_spawn)

    def after_spawn():
        check(f"generation {generation}: only its own new-object notification fires", mine["new"] >= 1 and all(state["hits"][g]["new"] == previous[g]["new"] for g in previous),
              (mine["new"], {g: h["new"] for g, h in state["hits"].items()}))
        if generation == 2:
            worker = state["worker"]
            worker.join(timeout=3)
            check("a thread of the old generation can see the change and stop", not worker.is_alive())
        if generation < LAST:
            ue4ss.reload()
            ue4ss.reload()
            ue4ss.reload()                                   # three requests before the tick: one reload
            return
        check("every reload made exactly one generation (three reload() calls each)", generation == LAST, generation)
        check("the old delayed task never ran", state["old_task"] == 0)
        check("generation 1's hook, key binding and notification never fired again", state["old_hook"] == 0 and state["old_key"] == 0 and state["old_new"] == 0,
              (state["old_hook"], state["old_key"], state["old_new"]))
        try:
            os.remove(HELPER)
        except OSError:
            pass
        failed = [label for label, ok, _ in state["results"] if not ok]
        check("no check failed in any generation", not failed, failed)
        print("reload finished")

    ue4ss.execute_in_game_thread(after_tick)
