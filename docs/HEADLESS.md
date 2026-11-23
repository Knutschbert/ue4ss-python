# Live bridge on headless and containerized games

The live bridge depends on the Python game-thread executor being drained. Its transport worker can accept TCP
connections independently, but that does not prove the game thread is progressing.

## Load UE4SS under Wine/Proton

When the installed UE4SS proxy is `dwmapi.dll`, Wine/Proton may need to prefer the native proxy over its built-in
implementation:

```sh
WINEDLLOVERRIDES="dwmapi=n,b"
```

Set this in the game process environment, such as the container service environment. Use the override matching the
proxy DLL actually installed. The game directory must be writable where UE4SS and the bridge write logs and file-IPC
state.

## Executor selection

`DefaultExecuteInGameThreadMethod` is a UE4SS Lua setting. It does **not** select the Python executor. Python uses the
engine-tick callback when available; `PYUE_EXECUTOR=processevent` explicitly forces the fallback, and ProcessEvent is
used automatically only when the tick callback is unavailable. Do not change the Lua setting to diagnose Python live
bridge stalls.

The fallback captures the thread of the **first ProcessEvent callback** as the presumed game thread. ProcessEvent may
also be called from worker threads, so this is a heuristic, not a general guarantee. The fallback logs
`ProcessEvent (fallback, unverified)`. Existing test-game or desktop observations do not establish that a particular
idle dedicated server will call ProcessEvent on its game thread often enough to drain queued Python work.

Verify a target server in its actual idle/headless configuration: start the managed bridge with file transport, confirm
the heartbeat advances while the server is idle, issue a harmless request, then repeat with the match active. Record
the game/UE4SS build and executor log. If the heartbeat stops, that indicates lack of observed bridge progress; it does
not by itself prove which executor setting caused it. Do not infer successful ProcessEvent pumping from a running
socket listener or a successful TCP ping—those happen on a worker thread.

## Container paths and SSH forwarding

File IPC requires the game and client to see the same session directory. Translate the host bind-mounted path into the
path visible inside Wine; do not assume `/host/path` and `Z:\host\path` are interchangeable in the game process.

For TCP, configure the game listener on its loopback address and forward a separate local loopback port:

```sh
# Game host: provision a restricted token, then enable managed socket transport.
python tools/live_token.py create /home/game/.local/share/ue4ss/live.token
python tools/live.py --token-file /home/game/.local/share/ue4ss/live.token install --managed --transport socket --port 9000

# Client: local bind is 127.0.0.1:19000; the final address is loopback on the game host.
ssh -N -L 127.0.0.1:19000:127.0.0.1:9000 user@game-host
python tools/live.py --endpoint tcp://127.0.0.1:19000 --token-file /private/copy/live.token status
```

Transfer the token to the client through a protected channel and retain restrictive permissions. The server bind and
SSH client's local bind are independent: use numeric loopback on both; never use `0.0.0.0` or a LAN address. Each
instance needs its own port and token. A port conflict fails; do not solve it with socket reuse. In a container,
`127.0.0.1` is the container itself, so use host networking or an explicitly configured SSH tunnel to reach the host.

The TCP server ping checks the socket worker only. A separate game-thread request and advancing game-thread heartbeat
are needed to assess Python execution progress. The SSH tunnel encrypts the remote path; loopback plus token
authentication is not protection from privileged local users.
