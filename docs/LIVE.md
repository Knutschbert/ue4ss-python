# Running Python in the running game from outside

For exploring a game: type an expression in a terminal, see the result from the game, without editing `main.py` and
without reloading. A small resident script in the game, the *bridge*, executes what you send.

```
python tools/live.py install       # once: the bridge becomes the mod's main.py (your main.py is kept)
                                   # start the game, or reload the script in a running game
python tools/live.py status
python tools/live.py exec "len(ue4ss.find_all_of('Actor'))"
python tools/live.py repl
```

```
>>> actors = ue4ss.find_all_of("Actor")
>>> len(actors)
result: 212
>>> for actor in actors[:2]:
...     print(actor.get_full_name())
...
StaticMeshActor /Game/Maps/Example.Example:PersistentLevel.Floor
PlayerStart /Game/Maps/Example.Example:PersistentLevel.PlayerStart_0
```

(The values are an illustration.)

## Commands

| Command | Does |
|---|---|
| `install [--poll-ms N]` | Writes the bridge as `dist\PythonScripting\python_scripts\main.py`. A `main.py` of yours is kept next to it as `main.py.before-live`. |
| `uninstall` | Puts your `main.py` back. |
| `status` | Says whether the bridge is alive (it writes a heartbeat file) and shows its state. |
| `exec "<code>"`, `exec -f file.py`, `exec -` | Runs code in the game. An expression shows its value; `print` output and tracebacks are shown. |
| `repl` | A prompt. A line ending in `:` starts a block, an empty line ends it. |
| `reset` | Forgets the variables of the session. |
| `stop` | Ends the bridge in the game. |

`--timeout SECONDS` (before the command) sets how long to wait for the game, default 30.

## How it works

- **In the game:** [`tools/live_bridge.py`](../tools/live_bridge.py) generates the bridge. It re-arms itself with
  `ue4ss.execute_with_delay` every 250 ms, so it runs **on the game thread** and may touch game objects.
- **Requests and responses are files.** The client writes a JSON request into `<session>\inbox`; the bridge runs the
  code with `exec` and writes the printed output, the value of the variable `result` and any traceback into
  `<session>\outbox`. Files are written under a temporary name and renamed, so neither side reads a half-written one.
  The format is in [`tools/live_protocol.py`](../tools/live_protocol.py).
- **One persistent scope.** Variables, functions and object references stay between requests, like at a Python prompt,
  until `reset`. `ue4ss` is already imported.
- **The session folder** is `dist\PythonScripting\live`; the environment variable `PYUE_LIVE_DIR` names another one.
- **One request at a time.** A request that arrives while another runs is rejected. Code that runs long freezes the
  game for that time, like any code on the game thread; start a job for long work (below).

Besides `execute`, the protocol has `job_start` / `job_status` (code that runs in a later tick while the bridge keeps
answering), `wait_until` (block until an expression becomes true, re-evaluated across frames), `cleanup` (run callbacks
registered under a group name), `travel` (open a level), `reload` and `stop`.
[`tools/live_client.py`](../tools/live_client.py) is the client library the command-line tool uses.

## For AI assistants: the MCP server

[`tools/live_mcp.py`](../tools/live_mcp.py) offers the bridge to an AI assistant as MCP tools over stdio.

> [!IMPORTANT]
> **You start the game; the assistant works with the game that is running.** The server never starts, stops or installs
> into a game: games differ too much in how they are launched (Steam, Epic, extra DLLs). Install the bridge once with
> `python tools/live.py install`, start the game, then let the assistant work. Without a running bridge every tool
> answers at once with what you have to do.

### Registration

The repository carries a project-level [`.mcp.json`](../.mcp.json), which clients that read that file (Claude Code, for
example) pick up when opened on this folder:

```json
{
  "mcpServers": {
    "ue4ss-live": {
      "command": "${UE4SS_PYTHON:-python}",
      "args": ["tools/live_mcp.py"]
    }
  }
}
```

- The command is `python`; set the environment variable `UE4SS_PYTHON` when your interpreter has another name (`python3`
  on Linux and WSL, `py` on some Windows setups). A client that does not expand `${...}` needs the plain command name.
- For other clients, register the same command with **this repository as the working directory**, in that client's own
  configuration.
- The session folder is `dist/PythonScripting/live`; add `"env": {"PYUE_LIVE_DIR": "<folder>"}` for another one.
- **Client under WSL, game on Windows:** this works when the repository is on a Windows drive, because both then see the
  same session folder (`/mnt/p/...` and `P:\...`). Run `python tools\live.py install` with either Python; it writes the
  folder for the game as a Windows path.

### Tools

| Tool | Does |
|---|---|
| `live_status` | Whether the bridge is alive; if so the current level, the session generation and the jobs; if not, why and what to do. Call it first. |
| `live_execute` | Runs a Python block on the game thread in the persistent session; returns output, the variable `result` and any traceback. |
| `live_reset` | Runs the registered cleanups and forgets the session's variables. |
| `live_job_start` | Queues a block for a later engine tick and returns a job id at once. |
| `live_job_status` | State, output, result and error of a job. |
| `live_cleanup` | Runs the cleanup callbacks registered under a group name (all groups when none is given). |
| `live_wait_until` | Waits until an expression becomes true, re-evaluated every tick without blocking the game; without an expression waits the whole timeout. |
| `live_wait_ready` | Waits until a world exists and, unless `require_pawn` is false, a player controller with a pawn. On timeout says what is missing. |
| `live_sleep` | Waits a fixed number of seconds without blocking the game. |
| `live_travel` | Opens another level inside the running game (the engine's `OpenLevel`). Level paths are specific to the game. |
| `live_reload` | Runs the mod's `main.py` again; the bridge restarts with an empty session. |
| `live_stop` | Ends the bridge. The game keeps running. |

Limits of one request: 128 KiB of code, 64 KiB of output, 600 seconds.

### Working rules for an assistant

The same text is in [`AGENTS.md`](../AGENTS.md), which agent tools read by themselves.

1. **Start with `live_status`.** If the bridge is not alive, pass its `what_to_do` on to the user and stop; do not retry
   in a loop and do not try to start the game.
2. **Everything runs on the game thread.** A `live_execute` block freezes the game while it runs: no `time.sleep`, no
   long loops. To wait, use `live_wait_until`, `live_wait_ready` or `live_sleep`; for work that spans frames, start a
   job and poll `live_job_status`.
3. **The session is persistent.** Variables, functions and object references stay between calls; `ue4ss` is imported.
   Assign to `result` to get a value back. `live_reset` starts over.
4. **One request at a time.** A request sent while another runs is rejected.
5. **Objects can die.** A stored object may be destroyed by the game (level change, respawn); look it up again or check
   `obj.is_valid()`. Using a dead one raises `InvalidObjectError`, it does not crash.
6. **Explore before you act.** `obj.get_class().get_property_names()`, `get_function_names()`,
   `find_function(name).get_params()`, `ue4ss.find_all_of("Class")`; see [USAGE.md](../USAGE.md#5-find-out-what-your-game-has).
7. **Clean up what you make.** Keep spawned actors and hook ids in session variables and remove them when done, or
   register one with `register_cleanup(group, name, callable)` (available in the session) and call `live_cleanup`. Hooks and key bindings made in the session stay until removed
   or until the script reloads.
8. **After a level change** (`live_travel`, or the user loading a level) call `live_wait_ready` before touching the world.

## Safety

The bridge executes whatever is written into its inbox folder. Nothing listens on the network, so this is as open as the
folder is: anyone who can write files there can run code in the game with your rights. Keep the session folder on your
own machine, and uninstall the bridge when you are done exploring ([SECURITY.md](../SECURITY.md)).

## Verified

The bridge and the client are tested against a simulated engine tick, and the command-line tool was used against a real
game (2026-11-11).
