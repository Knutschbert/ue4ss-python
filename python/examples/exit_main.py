# Copy over python_scripts/main.py to check that sys.exit() in main.py does not close the game or the scheduler.
# The SystemExit report in the log is intended (manifest: tolerate_tracebacks).
import sys

import ue4ss


def check(label, ok):
    print(f"{label}: {'PASS' if ok else 'FAIL'}")


ue4ss.execute_in_game_thread(lambda: check("a task still runs on the game thread after sys.exit in main.py", ue4ss.is_game_thread()))
ue4ss.execute_with_delay(500, lambda: print("exit example finished"))
print("calling sys.exit(3)")
sys.exit(3)
