# Copy over python_scripts/main.py to check that a traceback in main.py reaches the UE4SS log and that
# the game and the scheduler keep working afterwards. The traceback is intended (manifest: tolerate_tracebacks).
import ue4ss


def check(label, ok):
    print(f"{label}: {'PASS' if ok else 'FAIL'}")


ue4ss.execute_in_game_thread(lambda: check("a task still runs on the game thread after main.py raised", ue4ss.is_game_thread()))
ue4ss.execute_with_delay(500, lambda: print("error example finished"))


def fail():
    raise ValueError("deliberate test error")


print("about to raise")
fail()
