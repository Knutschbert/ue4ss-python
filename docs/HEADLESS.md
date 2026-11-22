# Running the live bridge on a headless or containerized game

[`LIVE.md`](LIVE.md) covers the live bridge on an ordinary desktop game. A
**dedicated/headless server** — especially one in a container under Wine/Proton
— needs three extra things. They are game-agnostic; the threading model behind
them is in [`THREADING.md`](THREADING.md).

## 1. Load the UE4SS proxy under Wine/Proton

On Windows the game loads the UE4SS proxy DLL (`dwmapi.dll`, `xinput1_3.dll`, …)
from its executable directory on its own. Under Wine/Proton you have to tell
Wine to prefer the native proxy over its built-in stub:

```sh
WINEDLLOVERRIDES="dwmapi=n,b"   # the proxy you installed: native first, then builtin
```

Set it in the process environment — for a container, the service's
environment. UE4SS loaded this way coexists with other injected code (for
example a server plugin injected separately by a launcher): they do not
conflict as long as each uses its own hook points.

The game directory must be **writable** where UE4SS and the bridge write — the
UE4SS log and the `live/` inbox/outbox. A read-only game mount will not work.

## 2. Pump the game thread with ProcessEvent, not EngineTick

The bridge re-arms itself with `execute_with_delay`, which only runs when the
game-thread queue is drained. By default that drain happens on the **engine
tick** (`DefaultExecuteInGameThreadMethod = EngineTick`). On a headless server
the engine tick can stall *before a match is live* (no players, the map sitting
in `WaitingToStart`), so the queue never drains again: the bridge's heartbeat
freezes and the client reports *"no bridge is running"* even though the game is
up and the bridge printed `live bridge ready`.

Switch the drain to `ProcessEvent`, which fires on ordinary `UFunction` calls
that keep happening on an idle server. In `UE4SS-settings.ini`:

```ini
[General]
DefaultExecuteInGameThreadMethod = ProcessEvent
```

With that, the bridge polls continuously regardless of match state.

## 3. Mind the container / Wine path boundary

The bridge in the game and the client on the host must use the **same** session
folder, but they name it differently:

- The in-game process sees **Wine paths**. Wine maps `Z:\` to the host root
  `/`, so a folder the container sees at `/game/…/PythonScripting/live` is
  `Z:\game\…\PythonScripting\live` inside the game.
- The client on the host uses the **real filesystem path** to the same files
  (through the bind mount), e.g. `/srv/…/PythonScripting/live`.

So point the **in-game** bridge at the `Z:\…` path — regenerate `main.py` with
`live.py install --session-dir`, or set `PYUE_LIVE_DIR` in the game's
environment — and run the **client** against the host path:

```sh
python tools/live.py --session-dir /host/path/to/PythonScripting/live status
python tools/live.py --session-dir /host/path/to/PythonScripting/live exec "1+1"
```

---

A concrete, verified end-to-end example — Chivalry 2 dedicated server in Docker
with GE-Proton and UnchainedLauncher — is in the `chiv-x64-server` repo at
`docs/ue4ss-live-bridge.md`.
