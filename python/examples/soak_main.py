# Threading soak test (ft9.5): for PYUE_SOAK_SECONDS (default 45) four Python threads schedule work on the game
# thread while the game thread itself runs nested hooks, spawns and destroys actors and receives new-object notifications.
# Passes only if nothing hangs (a heartbeat every 500 ms never stalls), every result is right and nothing raised.
import os
import random
import threading
import time

import ue4ss

SECONDS = float(os.environ.get("PYUE_SOAK_SECONDS", "45"))
LIB = "/Script/PyTestGame.PyTestActor"


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + str(detail)[:200] + ')') if detail else ''}")


actors = sorted(ue4ss.find_all_of("PyTestActor"), key=lambda a: a.get_fname().to_string())
check("three test actors", len(actors) == 3, len(actors))
a0, a1, a2 = actors

stats = {"worker_calls": [0, 0, 0, 0], "worker_errors": [], "hook_add": 0, "hook_swap": 0, "nested_max": 0, "depth": 0, "driver_rounds": 0,
         "spawned": 0, "new_actors": 0, "beats": [], "driver_errors": [], "wrong": 0}
stop = threading.Event()


# --- nested hooks: the AddInts hook calls SwapInts, whose own hook counts (hook inside hook, on the game thread)
def add_pre(context, params):
    stats["hook_add"] += 1
    stats["depth"] += 1
    stats["nested_max"] = max(stats["nested_max"], stats["depth"])
    a, b = 3, 4
    out = a0.SwapInts(a, b)
    if tuple(out) != (b, a):
        stats["wrong"] += 1


def add_post(context, params, result):
    stats["depth"] -= 1


def swap_pre(context, params):
    stats["hook_swap"] += 1
    stats["nested_max"] = max(stats["nested_max"], stats["depth"] + 1)


hook_ids = [ue4ss.register_hook(f"{LIB}:AddInts", pre=add_pre, post=add_post), ue4ss.register_hook(f"{LIB}:SwapInts", pre=swap_pre)]
new_id = ue4ss.notify_on_new_object("/Script/Engine.Actor", lambda obj: stats.__setitem__("new_actors", stats["new_actors"] + 1))


# --- game-thread driver: calls, spawns, container edits every 20 ms
def driver():
    if stop.is_set():
        return
    stats["driver_rounds"] += 1
    n = stats["driver_rounds"]
    try:
        if a1.AddInts(n % 100, 7) != n % 100 + 7:
            stats["wrong"] += 1
        if n % 10 == 0:
            a0.SpawnMore(1)
            stats["spawned"] += 1             # lives on, so its notification must arrive
            a0.SpawnAndDestroy(1)             # gone before delivery: never notified (docs/API.md), only churn here
        if n % 7 == 0:
            a2.IntArray.append(n)
            if len(a2.IntArray) > 50:
                del a2.IntArray[:25]
    except Exception as e:  # noqa: BLE001
        stats["driver_errors"].append(repr(e))
    ue4ss.execute_with_delay(20, driver)


# --- worker threads: results come back through futures, plus fire-and-forget tasks
def worker(index):
    rng = random.Random(index)
    try:
        while not stop.is_set():
            x, y = rng.randint(-1000, 1000), rng.randint(-1000, 1000)
            if index % 2 == 0:
                got = ue4ss.call_in_game_thread(lambda: a1.AddInts(x, y)).result(timeout=20)
                if got != x + y:
                    stats["wrong"] += 1
            else:
                done = threading.Event()
                ue4ss.execute_in_game_thread(lambda: (a2.AddInts(x, y), done.set()))
                if not done.wait(20):
                    raise TimeoutError("task never ran")
            stats["worker_calls"][index] += 1
            time.sleep(rng.random() * 0.01)
    except Exception as e:  # noqa: BLE001
        stats["worker_errors"].append(f"worker {index}: {e!r}")


threads = [threading.Thread(target=worker, args=(i,), name=f"soak-{i}", daemon=True) for i in range(4)]
for t in threads:
    t.start()
ue4ss.execute_with_delay(0, driver)

started = time.monotonic()


def heartbeat():
    stats["beats"].append(time.monotonic() - started)
    if time.monotonic() - started < SECONDS:
        ue4ss.execute_with_delay(500, heartbeat)
    else:
        finish()


def finish():
    stop.set()
    wait_for_workers(time.monotonic() + 30)


def wait_for_workers(deadline):
    # never block the game thread on a Python thread: the workers may be waiting for a task that only this thread can run
    if any(t.is_alive() for t in threads) and time.monotonic() < deadline:
        ue4ss.execute_with_delay(100, lambda: wait_for_workers(deadline))
        return
    report()


def report():
    for ids in hook_ids:
        ue4ss.unregister_hook(ids)
    ue4ss.unregister_notify_on_new_object(new_id)
    beats = stats["beats"]
    gaps = [b - a for a, b in zip(beats, beats[1:])]
    print(f"soak {SECONDS:.0f} s: worker calls {stats['worker_calls']}, driver rounds {stats['driver_rounds']}, AddInts hook {stats['hook_add']}, "
          f"SwapInts hook {stats['hook_swap']}, nested depth {stats['nested_max']}, spawned {stats['spawned']}, new actors seen {stats['new_actors']}, "
          f"heartbeats {len(beats)}, max gap {max(gaps) if gaps else 0:.2f} s")
    check("no heartbeat stalled longer than 2 s (nothing hung)", bool(gaps) and max(gaps) < 2.0, max(gaps) if gaps else None)
    check("heartbeats ran for the whole soak", len(beats) >= SECONDS * 2 * 0.9, len(beats))
    check("every worker thread finished", all(not t.is_alive() for t in threads))
    check("every worker made progress", all(c > 20 for c in stats["worker_calls"]), stats["worker_calls"])
    check("no worker raised or timed out", not stats["worker_errors"], stats["worker_errors"][:2])
    check("no driver round raised", not stats["driver_errors"], stats["driver_errors"][:2])
    check("every call result was correct", stats["wrong"] == 0, stats["wrong"])
    check("the hook inside a hook ran (nested)", stats["hook_swap"] > 0 and stats["nested_max"] >= 2, stats["nested_max"])
    check("new-object notifications arrived for spawned actors", stats["new_actors"] >= stats["spawned"] * 0.9 and stats["spawned"] > 0, (stats["new_actors"], stats["spawned"]))
    check("the game still answers", a0.AddInts(2, 3) == 5)
    print("soak finished")


ue4ss.execute_with_delay(500, heartbeat)
