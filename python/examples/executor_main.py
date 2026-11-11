# Copy over python_scripts/main.py to check the game-thread scheduling API. Prints PASS/FAIL per check.
import threading

import ue4ss


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + detail + ')') if detail else ''}")


check("main.py runs on the game thread", ue4ss.is_game_thread())

seen = {}
ue4ss.execute_in_game_thread(lambda: seen.update(queued=ue4ss.is_game_thread()))
ue4ss.execute_with_delay(1500, lambda: seen.update(delayed=ue4ss.is_game_thread()))

order = []
for i in range(3):
    ue4ss.execute_in_game_thread(lambda i=i: order.append(i))
ue4ss.execute_in_game_thread(lambda: seen.update(order=list(order)))


def worker():
    future = ue4ss.call_in_game_thread(lambda: (ue4ss.is_game_thread(), 21 * 2))
    seen["future"] = future.result(timeout=30)
    seen["future_thread"] = threading.current_thread().name


threading.Thread(target=worker, name="pyue-worker", daemon=True).start()

failed = ue4ss.call_in_game_thread(lambda: 1 / 0)
failed.add_done_callback(lambda f: seen.update(future_error=type(f.exception()).__name__))

ue4ss.execute_in_game_thread(lambda: 1 / 0)  # traceback in the log; the game keeps running
ue4ss.execute_in_game_thread(lambda: __import__("sys").exit(3))  # logged and ignored
ue4ss.execute_in_game_thread(lambda: seen.update(after_failures=True))


def report():
    check("execute_in_game_thread runs on the game thread", seen.get("queued") is True)
    check("execute_with_delay runs on the game thread", seen.get("delayed") is True)
    check("queued tasks run in order", seen.get("order") == [0, 1, 2], str(seen.get("order")))
    check("call_in_game_thread from a worker returns the result", seen.get("future") == (True, 42) and seen.get("future_thread") == "pyue-worker", str(seen.get("future")))
    check("a failing call_in_game_thread reports the exception on its future", seen.get("future_error") == "ZeroDivisionError")
    check("tasks keep running after a failing task and a sys.exit", seen.get("after_failures") is True)
    print("executor example finished")


ue4ss.execute_with_delay(3000, report)
