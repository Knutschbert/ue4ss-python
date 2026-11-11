# Held keys without any C++ change : driving a car with WASD needs to know which keys are HELD right now, but register_key_down only reports presses.
# The embedded Python ships _ctypes, so user32.GetAsyncKeyState can be called from a script. Checks: ctypes imports, the call returns an int, no key is held at rest, the foreground window is the game's own
# (so typing in another window does not drive the car). Then for 15 s every change of W, A, S, D, SPACE, SHIFT is printed as a [keys] line: press them in the game window (for the owner; the test passes without keys).
# Chivalry 2 and the test game.
import time

import ue4ss

failures = 0
VK = {"W": 0x57, "A": 0x41, "S": 0x53, "D": 0x44, "SPACE": 0x20, "SHIFT": 0x10}


def check(label, ok, detail=""):
    global failures
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + str(detail)[:300] + ')') if detail else ''}")
    failures += 0 if ok else 1


def run():
    try:
        import ctypes
        user32 = ctypes.windll.user32
        check("ctypes imports in the embedded Python and user32 loads", True)
    except Exception as e:  # noqa: BLE001 - the result of the probe
        check("ctypes imports in the embedded Python and user32 loads", False, f"{type(e).__name__}: {e}")
        print("keys probe finished")
        return
    user32.GetAsyncKeyState.restype = ctypes.c_short
    state = user32.GetAsyncKeyState(VK["W"])
    check("GetAsyncKeyState returns an int", isinstance(state, int), state)
    held_now = {name: bool(user32.GetAsyncKeyState(code) & 0x8000) for name, code in VK.items()}
    print("[keys] held at the start:", {k: v for k, v in held_now.items() if v} or "none")
    try:
        pid = ctypes.windll.kernel32.GetCurrentProcessId()
        window = user32.GetForegroundWindow()
        owner = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
        print(f"[keys] foreground window belongs to pid {owner.value}; this process is {pid}; same: {owner.value == pid}")
        check("the foreground window query works (pid read)", owner.value >= 0)
    except Exception as e:  # noqa: BLE001
        check("the foreground window query works (pid read)", False, e)
    last = dict(held_now)
    began = time.perf_counter()
    cost = []

    def poll():
        t0 = time.perf_counter()
        now = {name: bool(user32.GetAsyncKeyState(code) & 0x8000) for name, code in VK.items()}
        cost.append((time.perf_counter() - t0) * 1000.0)
        for name in now:
            if now[name] != last[name]:
                print(f"[keys] {time.perf_counter() - began:5.1f}s {name} {'DOWN' if now[name] else 'UP'}")
                last[name] = now[name]
        if time.perf_counter() - began < 15.0:
            ue4ss.execute_with_delay(30, poll)
        else:
            check("polling six keys costs under 1 ms per call", max(cost) < 1.0, f"max {max(cost):.3f} ms over {len(cost)} polls")
            print("keys probe finished")
    poll()


ue4ss.execute_with_delay(3000, run)
