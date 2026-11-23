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
| UE 4.25 | PyTestGame, the test project in this repository ([TESTGAME.md](TESTGAME.md)) | Full in-game suite: 54 of 55 passed, with the sole `bind_event_main.py` failure marked expected for a known defect (2026-11-23). Focused `managed_reload_main.py`: three global-reload handovers passed (17 checks, 2026-11-23). |
| UE 4.25 (modified) | Chivalry 2 | The in-game test scripts that do not need the test project's own classes; most measurements in the design records |

Other engine versions (4.26, 4.27, 5.x) are untried; what to expect and how to add one is in
[ENGINE_VERSIONS.md](ENGINE_VERSIONS.md).

## Tools of this repository

| Tool | Checked |
|---|---|
| [`tools/testgame.py`](../tools/testgame.py) | Full suite: 55 scripts, zero unexpected failures and one expected known defect; focused managed global-reload test: 3 handovers passed (2026-11-23) |
| [`tools/install.py`](../tools/install.py) | Installed into a game, 2026-11-11 |
| [`tools/live.py`](../tools/live.py) | Used against a running game, 2026-11-11 |
| [`tools/live_mcp.py`](../tools/live_mcp.py) | Driven over stdio against a running game, 2026-11-11: status, readiness (with and without a pawn), execute, an error, a job, wait, cleanup, sleep, reset. Untried: travel, reload, stop; being started by an MCP client from `.mcp.json` |
| [`tools/make_release.py`](../tools/make_release.py) | `--build --with-ue4ss` produced a zip, 2026-11-11; installing that zip on another machine is untried |

## Toolchain

| Tool | Version used |
|---|---|
| CPython (bundled) | 3.12.10, embeddable distribution |
| MSVC | Visual Studio 2022, C++23 |
| Test project compiler | Visual Studio 2019 (cl 14.29), as UE 4.25 requires |

## Reporting a combination

Tried another UE4SS commit, engine version or game? Add a row with what you built, what you ran and what happened.
