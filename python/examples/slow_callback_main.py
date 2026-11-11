# set_slow_callback_warning: a callback on the game thread that takes too long is reported (to sys.stderr, so into the log), at most
# 3 times per callback; the threshold is configurable and 0 switches it off. Test game: PyTestActor gives hooks, replacements
# and new objects to time.
import io
import sys
import time
import ue4ss

results = []
real_stderr = sys.stderr


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + ((" -- " + repr(detail)[:240]) if (detail != "" and not ok) else ""))


actor = ue4ss.find_first_of("PyTestActor")
if actor is None:
    print("slow callbacks: SKIP (needs the PyTestActor of the test game)")
    print("slow callback finished PASS=0 FAIL=0")
else:
    for bad, exc in ((-1, ValueError), (1.5, TypeError), ("5", TypeError)):
        try:
            ue4ss.set_slow_callback_warning(bad)
            check(f"set_slow_callback_warning({bad!r}) is refused", False)
        except exc:
            check(f"set_slow_callback_warning({bad!r}) is refused", True)

    capture = io.StringIO()
    sys.stderr = capture
    ue4ss.set_slow_callback_warning(20)
    slow = lambda: time.sleep(0.06)
    fast = lambda: None

    def phase_tasks():
        capture.truncate(0); capture.seek(0)
        ue4ss.execute_in_game_thread(fast)
        ue4ss.execute_in_game_thread(slow)
        ue4ss.execute_with_delay(300, phase_hook)

    def phase_hook():
        text = capture.getvalue()
        check("a slow game-thread task is reported", "slow callback: game-thread task took" in text and "(limit 20 ms)" in text, text)
        check("a fast one is not (exactly one line)", text.count("slow callback") == 1, text)
        capture.truncate(0); capture.seek(0)
        ids = ue4ss.register_hook("/Script/PyTestGame.PyTestActor:AddInts", pre=lambda c, p: time.sleep(0.06), post=lambda c, p, r: None)
        actor.AddInts(1, 2)
        ue4ss.unregister_hook(ids)
        text = capture.getvalue()
        check("a slow hook is reported with the function name", "pre-hook: AddInts took" in text, text)
        check("the fast post-hook is not", "post-hook" not in text, text)
        capture.truncate(0); capture.seek(0)
        rid = ue4ss.register_replacement("/Script/PyTestGame.PyTestActor:AddInts", lambda c, p: time.sleep(0.06) or 7)
        actor.AddInts(1, 2)
        ue4ss.unregister_replacement(rid)
        text = capture.getvalue()
        check("a slow replacement is reported with the function name", "replacement: AddInts took" in text, text)
        capture.truncate(0); capture.seek(0)
        nid = ue4ss.notify_on_new_object("/Script/Engine.Actor", lambda o: time.sleep(0.06))
        actor.SpawnMore(1)
        state["nid"] = nid
        ue4ss.execute_with_delay(500, phase_new_object)

    def phase_new_object():
        ue4ss.unregister_notify_on_new_object(state["nid"])
        text = capture.getvalue()
        check("a slow new-object callback is reported", "new-object callback took" in text, text)
        # rate limit: 6 slow tasks, 3 lines, the last says it is muted
        capture.truncate(0); capture.seek(0)
        for _ in range(6):
            ue4ss.execute_in_game_thread(lambda: time.sleep(0.03))
        ue4ss.execute_with_delay(600, phase_rate)

    def phase_rate():
        text = capture.getvalue()
        lines = [l for l in text.splitlines() if "slow callback" in l]
        # an earlier phase already used one of this label's 3 reports: at most 2 more lines, the last one saying it is muted
        check("six slow tasks of an already reported label log at most 2 more lines, the last saying so (rate limit)",
              len(lines) <= 2 and (not lines or "not logged" in lines[-1]), text)
        # a different callback kind (a hook on another function) still gets reported
        capture.truncate(0); capture.seek(0)
        ids = ue4ss.register_hook("/Script/PyTestGame.PyTestActor:SumSet", pre=lambda c, p: time.sleep(0.06))
        for _ in range(5):
            actor.SumSet({1, 2})
        ue4ss.unregister_hook(ids)
        lines = [l for l in capture.getvalue().splitlines() if "slow callback" in l]
        check("a new label is reported 3 times, the last says further reports are muted", len(lines) == 3 and "not logged" in lines[-1] and "not logged" not in lines[0], lines)
        # threshold raised: nothing; switched off: nothing
        capture.truncate(0); capture.seek(0)
        ue4ss.set_slow_callback_warning(1000)
        ue4ss.execute_in_game_thread(slow)
        ue4ss.execute_with_delay(300, phase_off)

    def phase_off():
        check("a threshold above the callback's time reports nothing", capture.getvalue() == "", capture.getvalue())
        ue4ss.set_slow_callback_warning(0)
        ue4ss.execute_in_game_thread(slow)
        ue4ss.execute_with_delay(300, phase_done)

    def phase_done():
        check("0 switches the warnings off", capture.getvalue() == "", capture.getvalue())
        ue4ss.set_slow_callback_warning(50)
        sys.stderr = real_stderr
        print(f"slow callback finished PASS={sum(results)} FAIL={len(results) - sum(results)}")

    state = {}
    ue4ss.execute_with_delay(100, phase_tasks)
