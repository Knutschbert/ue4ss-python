# Copy over python_scripts/main.py to check that Python threads keep running after startup
# (the GIL was released) and are not blocked by the game thread.
import threading
import time

import ue4ss

ticks = []


def worker():
    for i in range(3):
        time.sleep(0.5)
        ticks.append((i, threading.current_thread().name))


started_from = threading.current_thread().name
threading.Thread(target=worker, name="pyue-test", daemon=True).start()


def report():
    print("background thread ticks:", ticks, "| started from", started_from)
    print("background thread ran 3 times while the game kept ticking:", "PASS" if len(ticks) == 3 and ticks[0][1] == "pyue-test" else "FAIL")
    print("thread example finished")


ue4ss.execute_with_delay(3000, report)
