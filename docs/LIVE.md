# Running Python in the running game from outside

For exploring a game: type an expression in a terminal, see the result from the game, without editing `main.py` and
without reloading. A small resident script in the game, the *bridge*, executes what you send.
Headless/game-server executor caveats are in [HEADLESS.md](HEADLESS.md).

```
python tools/live.py install --managed  # stable bootstrap + separately managed file bridge
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

The old standalone bridge remains available with `python tools/live.py install` (without `--managed`); it temporarily
becomes `main.py` and uses file IPC. Managed installation keeps a stable entry point and lets the live bridge coexist
with other managed scripts. File IPC is the default; socket transports require explicit selection and a private token
file.

## Commands

| Command | Does |
|---|---|
| `install [--managed] [--transport file\|socket\|both]` | Installs the legacy entry bridge by default; `--managed` registers live without replacing `main.py` on updates. |
| `uninstall` | Removes the legacy bridge, or disables only managed live when the stable bootstrap is installed. |
| `status` | Says whether the bridge is alive (it writes a heartbeat file) and shows its state. |
| `exec "<code>"`, `exec -f file.py`, `exec -` | Runs code in the game. An expression shows its value; `print` output and tracebacks are shown. |
| `repl` | A prompt. A line ending in `:` starts a block, an empty line ends it. |
| `reset` | Forgets the variables of the session. |
| `stop` | Ends the bridge in the game. |
| `scripts` | Lists independently managed scripts (requires managed bootstrap). |
| `script-start NAME`, `script-stop NAME` | Starts or stops only one managed script. |
| `script-load NAME -f file.py` | Sends one UTF-8 source file and loads it for this session; it is not persisted. |
| `script-reload NAME [-f file.py]` | Reloads only one managed script, optionally replacing its single-file source. |

`--timeout SECONDS` (before the command) sets how long to wait for the game, default 30. `--mod-dir <PythonScripting>`
and `--session-dir <folder>` select a packaged mod and file session outside this checkout. `--endpoint
tcp://127.0.0.1:PORT` or `--endpoint tcp://[::1]:PORT` selects the authenticated TCP client; pair it with
`--token-file <private-file>`. `PYUE_LIVE_ENDPOINT` and `PYUE_LIVE_TOKEN_FILE` provide the corresponding environment
configuration. Endpoint selection overrides `PYUE_LIVE_DIR`; explicit `--session-dir` selects the legacy file transport,
while explicitly passing both `--endpoint` and `--session-dir` is an error. Install/uninstall change local files and
managed configuration only; they do not launch a game.

## How it works

- **In the game:** the legacy route uses [`tools/live_bridge.py`](../tools/live_bridge.py) to generate its main script.
  Managed mode uses the packaged [`ue4ss.live.component`](../python/lib/ue4ss/live/component.py), which owns its
  callbacks through the component context. File polling runs **on the game thread** and may touch game objects.
- **Requests and responses are files.** The client writes a JSON request into `<session>\inbox`; the bridge runs the
  code with `exec` and writes the printed output, the value of the variable `result` and any traceback into
  `<session>\outbox`. Files are written under a temporary name and renamed, so neither side reads a half-written one.
  The format is in [`tools/live_protocol.py`](../tools/live_protocol.py).
- **One persistent scope.** Variables, functions and object references stay between requests, like at a Python prompt,
  until `reset`. `ue4ss` is already imported.
- **The session folder** is `dist\PythonScripting\live`; the environment variable `PYUE_LIVE_DIR` names another one.
- **One request at a time.** A request that arrives while another runs is rejected. Code that runs long freezes the
  game for that time, like any code on the game thread; start a job for long work (below).
- **Authenticated TCP (opt-in).** The host TCP client uses the same request records and operations. Managed installation
  can explicitly start an authenticated loopback listener; file IPC remains the default. Endpoint client options do not
  themselves install a listener.

Besides `execute`, the protocol has `job_start` / `job_status` (code that runs in a later tick while the bridge keeps
answering), `wait_until` (block until an expression becomes true, re-evaluated across frames), `cleanup` (run callbacks
registered under a group name), `travel` (open a level), `reload` and `stop`.
[`tools/live_client.py`](../tools/live_client.py) is the client library the command-line tool uses.

## Opt-in managed bootstrap

To opt into a stable entry point for multiple managed components, first package the mod, then run:

```powershell
python tools\managed.py --dry-run install
python tools\managed.py install
python tools\live.py install --managed
python tools\managed.py enable Demo --source path\to\Demo
python tools\managed.py list
python tools\live.py uninstall
python tools\managed.py uninstall
```

For authenticated TCP, create and validate a private token on the game host:

```powershell
python tools\live_token.py create C:\Users\you\live-secrets\bridge.token
python tools\live_token.py validate C:\Users\you\live-secrets\bridge.token
python tools\live.py --token-file C:\Users\you\live-secrets\bridge.token install --managed --transport socket --port 9000
```

`--transport both` runs both adapters over one shared execution core. Each game instance needs its own port and token.

A component source is a Python package directory containing `__init__.py` and exporting `start(ctx)`. The installer
preserves an existing user `main.py` as `main.py.before-managed`; when migrating the generated legacy bridge, it
preserves that bridge separately and leaves `main.py.before-live` untouched. Ambiguous backup collisions fail instead
of overwriting either file. Repeated install is idempotent. Uninstall restores the previous entry point (or removes the
bootstrap if there was none) and leaves managed sources/configuration intact.

The bootstrap reads `managed_runtime/manifest.json` beside the mod's `python_scripts` folder. The default manifest has
no enabled components. Components start in explicit `startup_order`; a component startup error is logged and does not
prevent later components from starting. After a global UE4SS reload, startup waits asynchronously for old managed
workers to exit and refuses unsafe startup after a bounded timeout. Store managed packages and the manifest under
`managed_runtime/`; package refreshes preserve this local state and owner backups, while release archives exclude the
entire directory so local paths and token-file references are not distributed. Do not put token contents in the manifest.
The installer supports `--dry-run` before each modifying command; it never starts the game or writes a component source
unless `enable` is explicitly invoked.

`live.py install --managed` updates only the `LiveBridge` manifest component. Package refresh preserves the local
settings and source; release archives omit them. The component imports packaged canonical helpers, not `tools/`.
Disabling it leaves the bootstrap and other components unchanged.

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
- For an already configured TCP listener, set `"env": {"PYUE_LIVE_ENDPOINT": "tcp://127.0.0.1:PORT", "PYUE_LIVE_TOKEN_FILE": "<private-token-file>"}`. Endpoint takes precedence over `PYUE_LIVE_DIR`; an explicit `--session-dir` selects files.
- **Game package with this repository as a submodule:** put this in the game repository's `.mcp.json`, substituting the
  Python command when needed:

  ```json
  {"mcpServers": {"ue4ss-live": {"command": "python", "args": ["ue4ss-python/tools/live_mcp.py"], "env": {"PYUE_LIVE_DIR": "dist/PythonScripting/live"}}}}
  ```

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
| `live_script_list` | Lists independently managed scripts and their lifecycle/worker diagnostics. |
| `live_script_start` | Starts a previously loaded managed script as a fresh instance. |
| `live_script_stop` | Stops only the named managed script; siblings continue running. |
| `live_script_load` | Sends one bounded source string for a session-only managed script; it does not persist to disk. |
| `live_script_reload` | Reloads one managed script, optionally with replacement source. |
| `live_outcome` | Looks up a prior request ID without replay; returns `unknown` when no retained result is available. |

Limits of one request: 128 KiB of code, 64 KiB each of captured stdout/stderr,
256 KiB of encoded result data, and 600 seconds. Live output capture temporarily
redirects interpreter-global stdout/stderr; prints from unrelated Python threads
during an execution can be attributed to that request. Capture is bounded but
cannot provide per-thread print isolation.

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

### TCP transport security contract

Managed installation can explicitly enable a TCP listener with `--transport socket` or `--transport both`; file IPC
remains the default. The listener binds numeric loopback only (`127.0.0.1` or `::1`) and requires a random bearer token
in a separate restricted file. For cross-machine use, the SSH client's local bind and the game host's server bind are
separate settings. For example, with a game listener on the host's loopback port 9000, forward a local loopback port
19000 to it:

```sh
ssh -N -L 127.0.0.1:19000:127.0.0.1:9000 user@game-host
python tools/live.py --endpoint tcp://127.0.0.1:19000 --token-file /private/path/live.token status
```

Do not expose either listener to a LAN or public interface. Create and validate the token on the game host with
`tools/live_token.py`; the host client needs a protected copy to authenticate. The manifest stores a path reference,
not token contents. Do not copy the token into generated scripts, command-line arguments, URLs, or logs. TCP itself is
not encrypted and the token does not protect against privileged local users. Give each game instance a distinct port
and token; a port conflict fails instead of sharing/reusing the listener. Container clients need a host-network route
or an explicitly configured SSH tunnel: `127.0.0.1` inside a container is the container, not the host.

The packaged framing/security helpers enforce a 1 MiB whole-frame limit, strict UTF-8 JSON, finite numeric values,
strict request IDs, constant-time token comparison, and pre-authentication message restrictions. The packaged selector
worker keeps socket I/O off the game thread and retains bounded request outcomes across reconnects within one bridge
instance. Token provisioning uses an exclusive file create and fails closed unless POSIX mode/ownership or the current
Windows user ACL can be verified. On WSL/DrvFS, use metadata-backed private permissions; a permissive mode report is
rejected. The endpoint client is available in the CLI/MCP adapter, and the managed socket worker is enabled only by
explicit configuration.

An admitted request is not cancelled when its client disconnects. Reconnect and query its outcome rather than
replaying code; if the response is no longer retained or the bridge instance changed, the outcome is `unknown`.
`live_reload` keeps its reload-all meaning. Its acknowledgement may be lost while the old worker shuts down, so a
disconnect around reload is not proof that the operation did not happen.

## Verified

The bridge and the client are tested against a simulated engine tick. The command-line tool and the MCP server were used
against a real game (2026-11-11); what exactly is in [COMPATIBILITY.md](COMPATIBILITY.md#tools-of-this-repository).

## Performance and soak harnesses

```sh
python tools/benchmark_live_transports.py \
  --samples 1000 --warmup 10 --repeat 3 --poll-ms 50 --tcp-nodelay both \
  --output /tmp/live-transport.json
```

This compares file IPC with real loopback TCP on a host-side queued game-thread stand-in.
It reports cold TCP handshake separately; tiny, representative, near-limit result, immediate-wait and job workloads;
RTT, queued game-thread delay, callback duration and bridge-reported execution percentiles; an estimated
non-execution RTT remainder (not a pure serialization timer); TCP_NODELAY variants; and process RSS/native-handle/
thread/GC-tracked-object snapshots at repeat boundaries. Keep the JSON artifacts with the platform/build and workload
settings. The host benchmark is not a native game-frame or remote SSH measurement. For native startup/reload and
game-process CPU/RSS, use
`tools/benchmark_live.py --testgame` with the staged PyTestGame and its existing guarded main-script backup/restore.
Do not turn these machine-sensitive measurements into CI latency thresholds.

The socket test suite uses a queued game-thread fake and real loopback sockets. Its test cases exercise fragmented
frames, authentication failures, duplicate/conflicting request IDs, disconnect/outcome recovery, slow peers, pending
waits, reload handover and bounded shutdown. For longer native qualification runs, set these variables in the
environment inherited by the Windows test runner:

```powershell
$env:PYUE_MANAGED_RELOAD_CYCLES = "100"
python tools\testgame.py run managed_reload_main --timeout 1800
$env:PYUE_MANAGED_SOAK_SECONDS = "1800"
python tools\testgame.py run managed_runtime_soak_main --timeout 1900
Remove-Item Env:PYUE_MANAGED_RELOAD_CYCLES, Env:PYUE_MANAGED_SOAK_SECONDS
```

The defaults are short smoke checks; these commands opt into 100 full reload handovers and a 30-minute live-file plus
independent-component/worker coexistence soak. `soak_main.py` remains a separate native executor/actor-hook soak
controlled by `PYUE_SOAK_SECONDS`. Neither the host benchmark nor PyTestGame claims idle dedicated-server progress;
see [HEADLESS.md](HEADLESS.md) for target-server verification.
