# Compatibility

What the mod has been built and run with. A row is only added for a combination someone actually tried; everything else
is unknown, not unsupported.

## UE4SS

| RE-UE4SS commit | Build configuration | Mod builds | Runs | Notes |
|---|---|---|---|---|
| [`97adba01`](https://github.com/UE4SS-RE/RE-UE4SS/commit/97adba01e9fcb3c208376581e54edb9ef466f233) (after 3.0.1) | `Game__Shipping__Win64` | yes | yes | The pinned commit: [`tools/setup_windows.ps1`](../tools/setup_windows.ps1) checks it out by default |

Other commits: see [BUILDING.md](BUILDING.md#building-against-another-ue4ss-commit). `CasePreserving` builds are not
supported; `LessEqual421` is untried.

## Engine versions and games

| Engine | Game | What was checked |
|---|---|---|
| UE 4.25 | PyTestGame, the test project in this repository ([TESTGAME.md](TESTGAME.md)) | Full in-game suite: 60 scripts started, 0 unexpected script failures, 1 expected known-defect script (`bind_event_main.py`); 1,079 PASS, 14 expected FAIL-marker lines, 5 SKIP-marker lines (2026-11-23). Focused `managed_reload_main.py`: three global-reload handovers passed (17 checks). |
| UE 4.25 (modified) | Chivalry 2 | Owned local stable-bootstrap run: 16/16 authenticated TCP coexistence checks passed, covering A/B reload, managed AI child-scope undo, overlay restart/stale-answer rejection, global reload and reconnect. No AI host/model was called (2026-11-23). |

Other engine versions (4.26, 4.27, 5.x) are untried; what to expect and how to add one is in
[ENGINE_VERSIONS.md](ENGINE_VERSIONS.md).

## Tools of this repository

| Tool | Checked |
|---|---|
| [`tools/build.ps1`](../tools/build.ps1) | `PythonScripting` built successfully for `Game__Shipping__Win64` against the pinned RE-UE4SS checkout (2026-11-23) |
| [`tools/testgame.py`](../tools/testgame.py) | Full suite: 60 scripts started, zero unexpected failures and one expected known defect; focused managed global-reload test: 3 handovers passed (2026-11-23) |
| [`tools/install.py`](../tools/install.py) | Installed into a game, 2026-11-11 |
| [`tools/live.py`](../tools/live.py) | Used against a running game, 2026-11-11 |
| [`tools/live_mcp.py`](../tools/live_mcp.py) | OpenCode V2 local MCP client registered this server from the project config and tested it against owned UE 4.25 PyTestGame (2026-11-23): status, execute (`42`), job (`42`), `live_wait_ready(require_pawn=false)`, travel to `/Engine/Maps/Entry`, then readiness again. LiveBridge ran; the unrelated Chivalry AIOverlay component failed to start because PyTestGame has no `ClientReceiveChat` function. Earlier direct stdio/JSON-RPC replay (2026-11-11) is recorded separately and is not counted as client verification. |
| [`tools/make_release.py`](../tools/make_release.py) | `--build --with-ue4ss` produced a zip, 2026-11-11; installing that zip on another machine is untried |

## Toolchain

| Tool | Version used |
|---|---|
| CPython (bundled) | 3.12.10, embeddable distribution |
| MSVC | Visual Studio 2022, C++23 |
| Test project compiler | Visual Studio 2019 (cl 14.29), as UE 4.25 requires |

## Managed live-runtime measurements

Measurements are bounded samples, not a frame-time guarantee. The file bridge
and loopback socket comparison uses a queued Python scheduler stand-in; it is
not an Unreal game-thread or remote-network measurement. A repeated 1,000-
sample-per-case host comparison (10 warmups, 50 ms file poll) measured ordinary tiny
request round-trip p50 at 54.05–54.13 ms for file IPC and 1.12–1.14 ms for
authenticated loopback TCP. Immediate waits remained poll-bound at about
54–55 ms on both transports. These results describe only the measured host and
transport setup.

A separate native PyTestGame benchmark (UE 4.25 Shipping, Windows 11, CPython
3.12.4; 100 requests per case, 3 warmups, 250 ms poll) measured tiny-result,
stdout and structured-result round-trip p50 at 256.5, 257.1 and 256.9 ms.
The instrumented resident poll callback was p50 5.514 ms and p95 7.208 ms;
this includes benchmark instrumentation and is not a full engine-frame
measurement. The controlled headless test game's five-second idle sample used
105% of one logical CPU; that is not an estimate of the bridge's marginal cost.

Managed lifecycle qualification also passed 100 reload cycles (599 checks),
a 30-minute staged coexistence soak (6 checks), and the owned Chivalry 2
TCP/A-B/AI lifecycle scenario (16 checks). Dedicated idle-server and
Wine/Proton behavior, cross-machine SSH latency, and actual MCP use against
Chivalry 2 remain unverified. Raw benchmark artifacts are local test
artifacts and are not distributed with this repository.

## Reporting a combination

Tried another UE4SS commit, engine version or game? Add a row with what you built, what you ran and what happened.
