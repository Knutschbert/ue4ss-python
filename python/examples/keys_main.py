# Key bindings (7q4.5), headless: a key press is simulated with ue4ss._selftest_fire_key, which takes the same path
# as a real one (native handler -> game thread -> callbacks). A real key press needs a focused game window: see keys_manual_main.py.
import threading

import ue4ss
from ue4ss import Key, ModifierKey

log = []


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + str(detail)[:200] + ')') if detail else ''}")


def raises(label, exc, fn):
    try:
        fn()
        check(label, False, "no exception")
    except exc:
        check(label, True)
    except Exception as e:  # noqa: BLE001
        check(label, False, repr(e))


check("Key and ModifierKey values", Key.F6 == 0x75 and Key.A == 0x41 and ModifierKey.CONTROL == 0x11 and "CONTROL" not in Key.__members__)
raises("a modifier is not a key", ValueError, lambda: ue4ss.register_key_down(0x11, lambda: None))
raises("key 0 refused", ValueError, lambda: ue4ss.register_key_down(0, lambda: None))
raises("callback must be callable", TypeError, lambda: ue4ss.register_key_down(Key.F6, 5))
raises("modifiers must be ModifierKey values", ValueError, lambda: ue4ss.register_key_down(Key.F6, lambda: None, modifiers=(Key.A,)))
raises("unknown id", ValueError, lambda: ue4ss.unregister_key_down(987654))

first = ue4ss.register_key_down(Key.F7, lambda: log.append(("plain", ue4ss.is_game_thread())))
ue4ss.register_key_down(Key.F7, lambda: log.append(("ctrl", ue4ss.is_game_thread())), modifiers=(ModifierKey.CONTROL,))
ue4ss.register_key_down(Key.F7, lambda: log.append(("second", ue4ss.is_game_thread())))
ue4ss.register_key_down(Key.F8, lambda: 1 / 0)                       # a failing callback must not stop the next one
ue4ss.register_key_down(Key.F8, lambda: log.append(("after failure", True)))
later = []
ue4ss.register_key_down(Key.F9, lambda: (log.append(("remover", True)), ue4ss.unregister_key_down(later[0])))
later.append(ue4ss.register_key_down(Key.F9, lambda: log.append(("removed", True))))

check("a press reaches both plain bindings", ue4ss._selftest_fire_key(Key.F7) == 2)
check("a press with Ctrl reaches only the Ctrl binding", ue4ss._selftest_fire_key(Key.F7, (ModifierKey.CONTROL,)) == 1)
check("a press with another modifier reaches nothing", ue4ss._selftest_fire_key(Key.F7, (ModifierKey.ALT,)) == 0)
ue4ss._selftest_fire_key(Key.F8)
ue4ss._selftest_fire_key(Key.F9)

worker_result = {}


def from_worker():
    # registering touches no game objects: it works from any thread; the callback still runs on the game thread
    binding = ue4ss.register_key_down(Key.F10, lambda: log.append(("from worker", ue4ss.is_game_thread())))
    worker_result["id"] = binding
    worker_result["fired"] = ue4ss._selftest_fire_key(Key.F10)


thread = threading.Thread(target=from_worker)
thread.start()
thread.join()


def report():
    names = [name for name, _ in log]
    check("callbacks run in registration order, plain before ctrl", names[:3] == ["plain", "second", "ctrl"], names)
    check("every callback ran on the game thread", all(on_thread for _, on_thread in log), log)
    check("a failing callback does not stop the next one", "after failure" in names)
    check("a callback unregistered by an earlier one does not run", "remover" in names and "removed" not in names, names)
    check("a binding registered from a worker thread fires on the game thread", worker_result.get("fired") == 1 and ("from worker", True) in log, (worker_result, log))
    ue4ss.unregister_key_down(first)
    log.clear()
    ue4ss._selftest_fire_key(Key.F7)
    ue4ss.execute_with_delay(300, report_after_unregister)


def report_after_unregister():
    check("an unregistered binding no longer runs", [name for name, _ in log] == ["second"], log)
    print("keys finished")


ue4ss.execute_with_delay(500, report)
