# Observes which OS threads reach which engine entry points. Prints the facts that
# docs/THREADING.md records; the checks only assert what the design relies on.
import threading

import ue4ss

before = ue4ss._selftest_thread_probe(True)
print("python main.py thread:", before["calling_thread"], "init site:", before["sites"]["init"])


def report():
    snap = ue4ss._selftest_thread_probe(False)
    game = snap["game_thread"]
    print("game thread (os id):", game, " callback thread:", snap["calling_thread"])
    for name, site in snap["sites"].items():
        print(f"site {name}: calls={site['calls']} threads={site['threads']}")
    sites = snap["sites"]
    print("tick on one thread which is the game thread:", "PASS" if sites["tick"]["threads"] == [game] else "FAIL")
    print("callback runs on the game thread:", "PASS" if snap["calling_thread"] == game else "FAIL")
    print("ProcessEvent threads:", sites["process_event"]["threads"], "includes game thread:", "PASS" if game in sites["process_event"]["threads"] else "FAIL")
    if sites["object_created"]["calls"]:
        print("object creation threads:", sites["object_created"]["threads"], "game thread only:", "PASS" if sites["object_created"]["threads"] == [game] else "FAIL")
        print("StaticConstructObject threads:", sites["static_construct"]["threads"], "game thread only:", "PASS" if sites["static_construct"]["threads"] == [game] else "FAIL")
    print("init thread is not the game thread:", sites["init"]["threads"] != [game] and "PASS" or "NOTE same thread")
    print("thread ids finished")


def spawn_some():
    # Provoke object creation and deletion on the game thread, then wait a few ticks.
    actor = ue4ss.find_first_of("PyTestActor")
    if actor is not None:  # test game only; other games just skip the creation sites
        actor.SpawnMore(3)
        actor.SpawnAndDestroy(3)
    ue4ss.execute_with_delay(3000, report)


ue4ss.execute_in_game_thread(spawn_some)
