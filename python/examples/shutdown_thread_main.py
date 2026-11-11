# A non-daemon Python thread that never ends must not keep the game from exiting (ft9.7).
# The example asks the game to quit; the runner then requires the process to be gone in time (manifest: expect_exit_within).
import threading
import time

import ue4ss

stop = threading.Event()


def spin():
    while not stop.wait(0.05):
        pass


worker = threading.Thread(target=spin, name="never-ends", daemon=False)
worker.start()
print("non-daemon thread alive:", "PASS" if worker.is_alive() and not worker.daemon else "FAIL")
probe = ue4ss._selftest_thread_probe(False)
main_ident = threading.main_thread().ident
print("threading.main_thread():", main_ident, "game thread:", probe["game_thread"], "init thread:", probe["sites"]["init"]["threads"])
print("MainThread is the init thread, not the game thread:", "PASS" if main_ident in probe["sites"]["init"]["threads"] and main_ident != probe["game_thread"] else "FAIL")


def quit_game():
    controller = ue4ss.find_first_of("PlayerController")
    kismet = ue4ss.static_find_object("/Script/Engine.KismetSystemLibrary")
    print("quitting the game; the thread keeps running")
    print("shutdown example finished")  # marker; the game exits right after
    kismet.QuitGame(controller, controller, 0, False)


ue4ss.execute_in_game_thread(lambda: ue4ss.execute_with_delay(1000, quit_game))
