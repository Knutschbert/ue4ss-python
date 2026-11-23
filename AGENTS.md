# Instructions for AI agents

This repository is **ue4ss-python**: Python scripting for Unreal Engine games through UE4SS. A UE4SS C++ mod embeds
CPython and gives scripts an `ue4ss` module. Start with [README.md](README.md) (what it is, build, install) and
[USAGE.md](USAGE.md) (writing scripts); the reference is [docs/API.md](docs/API.md).

Two kinds of work happen here. Find yours.

## A. Helping someone script or explore a game

### Writing scripts

- The mod runs one entry script, `python_scripts/main.py`, once, **on the game thread**; other files in that folder are
  imported from it. Registrations (hooks, keys, delayed tasks) outlive the script.
- Game objects may only be touched on the game thread. From a thread you started, hand work over with
  `ue4ss.execute_in_game_thread` or `ue4ss.call_in_game_thread`. The wrong thread raises `ue4ss.UEError`.
- An object can be destroyed by the game at any time; using it then raises `ue4ss.InvalidObjectError`. Look objects up
  when you need them, or check `obj.is_valid()`.
- Class, property and function names differ per game. **Do not guess them: look them up** (section 5 of USAGE.md, or
  the live tools below). Engine base classes (`Actor`, `Pawn`, `PlayerController`, `GameplayStatics`) exist everywhere.
- Never block a callback: no `time.sleep` and no long loops in `main.py`, hooks, key callbacks or delayed tasks.
- Working examples, one topic each: [examples/](examples/README.md). Coming from UE4SS Lua:
  [docs/LUA_CHEATSHEET.md](docs/LUA_CHEATSHEET.md).
- Check a script without a game: `PYTHONPATH=python/mock python3 script.py` (it imports and runs against a fake
  object model; it finds no game objects).
- Known limit: `bind_event` does not deliver delegates declared in C++ (Blueprint event dispatchers work).

### Working in the running game (MCP server `ue4ss-live`)

The project's [.mcp.json](.mcp.json) registers a server whose `live_*` tools run Python inside the running game
([docs/LIVE.md](docs/LIVE.md)).

This MCP workflow is attach-only: the user starts the game, and these `live_*` tools never start,
stop, install into, or replace files in a game. For a separately requested and explicitly authorized
automated development run, use the repository's documented owned-process runner and its preflight;
never start or stop an arbitrary process, deploy while the game is running, or replace a loaded DLL.
For scripts that coexist with managed components, the user installs the bridge once with
`python tools/live.py install --managed`; this preserves the stable managed bootstrap. The unflagged
`python tools/live.py install` is the legacy standalone route and replaces `main.py`.

1. **Start with `live_status`.** If the bridge is not alive, tell the user what its `what_to_do` says and stop. A
   configured managed bridge that has not run is `not_running`, not an instruction to install the legacy bridge. Do
   not retry in a loop.
2. **Everything runs on the game thread.** A `live_execute` block freezes the game while it runs: no `time.sleep`, no
   long loops. To wait, use `live_wait_until`, `live_wait_ready` or `live_sleep`; for work that spans frames, use
   `live_job_start` and poll `live_job_status`.
3. **The session is persistent.** Variables, functions and object references stay between calls; `ue4ss` is imported.
   Assign to `result` to get a value back. `live_reset` starts over.
4. **One request at a time.** A request sent while another runs is rejected.
5. **Explore before you act:** `obj.get_class().get_property_names()`, `get_function_names()`,
   `find_function(name).get_params()`, `ue4ss.find_all_of("Class")`.
6. **Clean up what you make.** Keep spawned actors and hook ids in session variables and remove them when done, or
   register a callback with `register_cleanup(group, name, callable)` and call `live_cleanup`.
7. **After a level change** (`live_travel`, or the user loading a level) call `live_wait_ready` before touching the world.
8. Writing to game memory is real: tell the user what you are about to change in their game when it is more than
   reading, and prefer single-player or the user's own server (see [SECURITY.md](SECURITY.md)).

## B. Changing this repository

- When unsure about unfamiliar or platform-specific API behavior, search authoritative online documentation first
  (official platform/runtime docs preferred). For a nontrivial or cross-platform question, spawn a research subagent
  to independently find and cite the relevant primary sources; do not delegate local edits or duplicate the full task.
  Summarize the finding and source in the work notes, then use the smallest safe local test needed to verify how this
  project is affected.
- Layout: `src/` the mod (C++, Windows only), `core/` header-only logic with host tests, `python/mock/ue4ss/` the
  off-game twin of the native module, `python/lib/ue4ss/` Python modules shipped with the mod, `python/typings/`
  generated base typings, `python/examples/` in-game test scripts, `tools/` build, install, test and release tools,
  `docs/` reference and design records. [CONTRIBUTING.md](CONTRIBUTING.md) has the rules.
- Checks before you call something done:
  ```
  python3 -m pytest python/tests tools -q      # must pass
  tools/run_host_tests.sh                      # when core/ changed
  ```
- An API change touches several places that must stay equal: `src/`, the off-game module, `docs/API.md`, the base
  typings (`python3 -I tools/gen_base_stubs.py`), an in-game test script and its `manifest.json` entry
  ([CONTRIBUTING.md](CONTRIBUTING.md#changing-the-python-api)).
- The mod and game are Windows x64. From WSL, you may invoke the Windows build tools and tests when available, using a
  drive-letter checkout path (not a `\\wsl.localhost\...` UNC path) and the private UEPseudo dependency. The native
  build uses `tools\build.ps1`; the in-game test runner is `python tools\testgame.py all`. **Say plainly what you could
  not verify** (a Windows build, an in-game run) instead of implying it was done.
- Never start a game, install into one or replace DLLs in a game folder unless the user asked for exactly that.
- Keep the repository game-independent: no game-specific code, no personal paths. Do not copy Unreal Engine source
  ([THIRD_PARTY.md](THIRD_PARTY.md)).
- Add files to a commit by path; do not commit local tool state or `local.config.json`.
