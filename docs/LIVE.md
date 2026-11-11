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

## For AI assistants: the MCP adapter

[`tools/live_mcp.py`](../tools/live_mcp.py) offers the same operations as MCP tools over stdio (`live_status`,
`live_execute`, `live_job_start`, `live_wait_until`, ...). Register it in an MCP client as the command
`python tools/live_mcp.py` with this repository as working directory. It never starts a game: without a running bridge
every tool reports that.

## Safety

The bridge executes whatever is written into its inbox folder. Nothing listens on the network, so this is as open as the
folder is: anyone who can write files there can run code in the game with your rights. Keep the session folder on your
own machine, and uninstall the bridge when you are done exploring ([SECURITY.md](../SECURITY.md)).

## Verified

The bridge and the client are tested against a simulated engine tick, and the command-line tool was used against a real
game (2026-11-11).
