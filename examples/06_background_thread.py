# 06 - Work in a background thread, and hand results to the game thread.
#
# Game objects may only be touched on the game thread. A Python thread you start yourself may compute, sleep, read
# files or talk to a server, but must pass anything that touches the game to the game thread.
import threading
import time

import ue4ss
from ue4ss import Key

ue4ss.register_key_down(Key.F5, ue4ss.reload)

generation = ue4ss.script_generation()          # threads survive a reload: remember which run started this one


def count_actors():
    # Runs on the game thread: touching objects is allowed here.
    print("actors in the level:", len(ue4ss.find_all_of("Actor")))


def worker():
    for _ in range(5):
        time.sleep(2.0)                                         # slow work that must not block the game
        if ue4ss.script_generation() != generation:
            return                                              # the script was reloaded: this thread is obsolete
        ue4ss.execute_in_game_thread(count_actors)              # queue it for the next tick; does not wait
    # To get a value back into the thread, use a Future:
    future = ue4ss.call_in_game_thread(lambda: len(ue4ss.find_all_of("Pawn")))
    print("pawns:", future.result(timeout=10))


threading.Thread(target=worker, name="example-worker", daemon=True).start()
print("worker started; is this the game thread?", ue4ss.is_game_thread())
