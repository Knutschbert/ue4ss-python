# Runs with PYUE_EXECUTOR=processevent (manifest "env"): the game-thread executor uses the ProcessEvent fallback instead of the
# engine tick. Checks that tasks still run and whether the thread it captured is the one that really ticks the engine.
import ue4ss

probe = ue4ss._selftest_thread_probe(True)   # starts the probe's own, independent engine tick hook
seen = {}
ue4ss.execute_in_game_thread(lambda: seen.update(queued=ue4ss.is_game_thread(), thread=ue4ss._selftest_thread_probe(False)["calling_thread"]))
ue4ss.execute_with_delay(1000, lambda: seen.update(delayed=ue4ss.is_game_thread()))


def report():
    snap = ue4ss._selftest_thread_probe(False)
    tick_threads = snap["sites"]["tick"]["threads"]
    pe_threads = snap["sites"]["process_event"]["threads"]
    print("captured game thread:", snap["game_thread"], "| engine tick threads:", tick_threads, "| ProcessEvent threads:", pe_threads)
    print("queued task ran on the captured thread:", "PASS" if seen.get("queued") is True else "FAIL")
    print("delayed task ran on the captured thread:", "PASS" if seen.get("delayed") is True else "FAIL")
    print("captured thread is the engine tick thread:", "PASS" if tick_threads == [snap["game_thread"]] else "FAIL")
    print("executor fallback finished")


ue4ss.execute_with_delay(3000, report)
