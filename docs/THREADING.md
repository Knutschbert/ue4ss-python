# Threading and the GIL

Decision record. "Facts" were read from RE-UE4SS 97adba0 / UEPseudo
fbb4fdd and from your game's UE4SS.log; "Observed" results come from `python/examples/thread_ids_main.py`.

## Threads
| Thread | What runs there | Python allowed |
|---|---|---|
| **Game thread** | Engine tick and `ProcessEvent` hook callbacks; GC normally | yes, and the only place UE objects may be touched |
| **UE4SS event loop** (`UE4SSProgram::update`, a `jthread`, 5 ms sleep) | `on_update`, and `on_unreal_init` (via `fire_unreal_init_for_cpp_mods`, read from source) | startup only, see "main.py" below |
| Engine worker threads (async loading, GC helpers, ...) | may reach `StaticConstructObject` and possibly `ProcessEvent` callbacks | never run Python user code here; queue instead |
| Python-created threads (`threading.Thread`) | pure Python | yes, but UE calls raise (thread affinity) |

## Facts
- The update loop is `m_event_loop = std::jthread{&UE4SSProgram::update, this}` and records
  `m_event_loop_thread_id`. It is **not** the game thread.
- Game-thread entry points UEPseudo offers: `Hook::RegisterEngineTickPostCallback` /
  Pre (`UEngine::Tick`), `RegisterProcessEventPre/PostCallback`, `RegisterStaticConstructObject*`,
  all taking `FCallbackOptions{bOnce, bReadonly, OwnerModName, HookName}`.
- `UE4SSRuntime::IsEngineTickAvailable()` reflects whether the tick AOB was found. In your game it
  was: `GameEngineTick` found by scan (with a warning that the vtable address differs, so the scan
  address is used), and UE4SS itself registers `UE4SS.EngineTick.GameThreadInitializer` as an
  engine-tick prehook.
- Lua's `ExecuteInGameThread` queues actions under a recursive mutex and drains them from the engine
  tick (default, setting `DefaultExecuteInGameThreadMethod = EngineTick`) or from a `ProcessEvent`
  hook, and refuses to start an action while another is executing.
- The UnchainedPlugin read-fault seal is per-thread: `call_guarded` opens the plugin
  read-guard on the calling thread for the duration of one guarded engine call, the VEH seals on
  the faulting thread (the same thread while the guard is open), and the sealed count is consumed
  on the same thread after the call. No cross-thread state, nothing held across a GIL boundary.

## Observed
Measured with `ue4ss._selftest_thread_probe()` and `python/examples/thread_ids_main.py` (the probe counts
calls and distinct Windows thread ids per entry point; it keeps at most 8 ids per site).

| Entry point | Test game (idle 10 s) | Chivalry 2 (about 20 s) |
|---|---|---|
| `on_unreal_init` | 1 call, event-loop thread | 1 call, event-loop thread |
| Engine tick (`UEngine::Tick`) | 1 thread, the game thread | 1 thread, the game thread |
| `ProcessEvent` | 1 thread, the game thread | 8 or more threads (list is capped at 8), game thread included |
| `StaticConstructObject` | game thread only | game thread only (3066 calls) |
| Object creation listener | game thread only | game thread only (3066 calls) |
| Object deletion listener | not triggered | game thread only (4884 calls) |

- `on_unreal_init` never runs on the game thread, so startup must not touch UE objects directly; the
  entry script is run from the game thread and `main.py` sees the game thread id.
- The thread that runs the first engine tick is the game thread and it stays the only tick thread.
- Assumption refuted: `ProcessEvent` is not game-thread only. Chivalry 2 calls it from many threads, so a
  hook trampoline must check the thread before running Python (hooks and replacements already do).
- Assumption not confirmed: `StaticConstructObject` and creation/deletion never left the game thread in these
  runs (no async loading happened), so the off-thread safety rules in `docs/LIFETIME.md` stay.

## GIL policy
1. **One interpreter, one process-lifetime.** Initialise once (done), then **release the GIL**
   (`PyEval_SaveThread`) at the end of startup so no thread owns it permanently.
2. **Every entry from the engine takes the GIL with an RAII `GilGuard`** (`PyGILState_Ensure` /
   `Release`): hook trampolines, tick callbacks, queued actions, key callbacks. Nesting is fine.
3. **Release the GIL around calls into the engine** (`ProcessEvent`, spawn, load, ...). This prevents
   the classic deadlock: the game thread holds the GIL while waiting on thread X, and X runs a hook that
   wants the GIL. A hook that fires on the same thread simply re-takes it through `GilGuard`.
4. **Never hold the table mutex or any native lock while calling Python**, and never call Python from
   `FUObjectDeleteListener` (see docs/LIFETIME.md): those run inside engine locks.
5. **Every `PyObject` reference a native object keeps is a `PyObjectRef` that takes the GIL in its
   destructor.** Native destructors (hook removal, std::function destruction) can run on any thread.
6. **A Python exception never crosses into the engine**: callbacks are wrapped, the traceback goes to
   the log, the hook returns as if unhooked (return-value overrides are discarded on error).
7. **Re-entrancy.** Python -> `ProcessEvent` -> hook -> Python is allowed. A thread-local depth counter
   skips (with a one-time log) hook callbacks nested deeper than 8 levels, to stop a hook that calls the
   function it hooks from recursing forever.

## Game-thread affinity
- The game-thread id is captured in the first engine-tick callback (the same trick UE4SS's
  `GameThreadInitializer` uses). Until it is known, nothing counts as "on the game thread".
- Any UE-touching call (`get_property`, `call`, `find_*`, ...) checks the id and raises
  `ue4ss.UEError("... must run on the game thread; use ue4ss.execute_in_game_thread")` otherwise.
- Hooks that fire off the game thread **do not run Python** synchronously. They are skipped with a
  rate-limited log line (pre-hooks cannot wait for a result). `notify_on_new_object` callbacks fired off
  the game thread are queued with a handle only and run at the next tick.

## Work queue (`execute_in_game_thread`)
- MPSC queue in `core/`, drained from the engine tick **post** callback on the game thread, with a
  per-tick time budget so a burst cannot freeze a frame; leftovers run next tick.
- Always queued, even when called on the game thread, so ordering is consistent (like Lua), except from
  inside the drain itself, which appends to the same queue.
- If the tick hook is unavailable (`IsEngineTickAvailable()` false), drain from a `ProcessEvent` post
  callback instead. If neither exists, `execute_in_game_thread` raises at registration time.
- Python threads wait for results with futures; `Future.result()` releases the GIL while waiting, so the
  game thread can run the action. **Never** make the game thread wait on a Python thread.

## Startup and shutdown
- `main.py` currently runs on the event-loop thread. It may use `print`, imports, and register hooks and
  callbacks, but UE-touching calls will raise until the script runs on the game thread. Decision: run
  `main.py` from the queue on the first engine tick.
- Shutdown policy: **nothing is finalized when the game exits.** UE4SS's `DllMain` skips the
  program destructor when the process terminates (`lpReserved` is non-null), so `~PythonScriptingMod` and `Py_FinalizeEx`
  never run, and Windows ends every Python thread with the process. A non-daemon thread therefore cannot block the exit.
  Observed in the test game: `python/examples/shutdown_thread_main.py` starts a non-daemon thread that never ends and quits the
  game; the process was gone about 5 s later and no destructor message reached the log. Scripts that must save state at exit
  cannot rely on a Python shutdown hook (`atexit` does not run); save when the state changes.
- The destructor path only runs if UE4SS unloads the mod with `FreeLibrary` (hot reload). Not
  exercised yet; it would need a bounded wait for non-daemon threads before `Py_FinalizeEx`.
- `threading.main_thread()` is the init thread (the `ue4ss` module imports `threading` while it initialises there), not the game
  thread; checked by the same example.

## ProcessEvent fallback
When the engine-tick hook is missing the executor drains from a `ProcessEvent` post callback and takes the thread of the first
call as the game thread. Set the environment variable `PYUE_EXECUTOR=processevent` to force this path for a test
(`python/examples/executor_fallback_main.py`, manifest `env`). Observed: in the test game and in 3 of 3 Chivalry 2 runs the
captured thread was the engine tick thread (checked against the probe's own tick hook), and queued and delayed tasks ran there,
although Chivalry 2 calls `ProcessEvent` from 8 or more threads later on. The capture is still a heuristic (the first caller
after the hook is installed happens to be the game thread at that moment), so the fallback is kept as a last resort and logs
`ProcessEvent (fallback, unverified)`; a game whose first call comes from another thread would run Python on the wrong thread.

## Soak test
`python/examples/soak_main.py` (test game): for `PYUE_SOAK_SECONDS` (default 45) four Python threads schedule work on the game
thread (`call_in_game_thread` with futures, and `execute_in_game_thread` with events) while the game thread runs a hook on
`AddInts` that calls `SwapInts` (a hook inside a hook), spawns and destroys actors every 200 ms and receives new-object
notifications. It checks that a 500 ms heartbeat never stalls beyond 2 s, every result is right, nothing raises, every spawned
actor is notified, and the game still answers. Measured: 600 s run, 417 k worker calls, 447 k nested hook pairs, 2952 spawned
actors all notified, 1200 heartbeats, longest gap 0.52 s, no error. The finish step must not join worker threads on the game
thread (they may be waiting for a task only that thread can run): it polls instead.

## Not decided here
- Per-callback time limit / slow-callback warning (nice to have).
- Sub-interpreters: rejected for now. They isolate scripts but most C extensions and our own static
  module state assume a single interpreter.

## Thread-affinity guard

Every call that reads or writes Unreal data, or calls into the engine, begins with `game_thread_executor::require_game_thread()`
and raises `UEError` ("... must run on the game thread; use ue4ss.execute_in_game_thread or ue4ss.call_in_game_thread") from any
other thread. The refusal happens **before** anything is changed: a refused `unregister_hook` leaves the hook registered.

What still works from any thread: `is_valid()` of every wrapper and `repr()` (they look at wrapper state only, never at
Unreal memory), `==`/hash of objects, `is_game_thread()`, `execute_in_game_thread`, `execute_with_delay`,
`call_in_game_thread`, and creating or reading an `FName` (the name table is thread-safe).

Checked by `python/examples/thread_guard_main.py` (test game, 98 PASS): about 90 entry points are called from a worker thread,
each must raise `UEError`, the wrapper-state ones must keep working, the game must answer afterwards and nothing may have
changed. A unit test (`python/tests/test_thread_guard_coverage.py`) fails when a public function or method of `ue4ss` is not
mentioned in that example, so a new API cannot skip the check. The example found two real gaps when it was written:
`unregister_replacement` worked from a worker thread, and `unregister_hook` forgot its record before refusing.

