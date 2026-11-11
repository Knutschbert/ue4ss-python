# The PyTestGame test game

A small UE 4.25 C++ project (`testgame/PyTestGame`) packaged as a Shipping Win64 game. It exists so the Python bindings can
be tested quickly and unattended against data we control, instead of only against a shipped game.

## What is in it
* `APyTestActor`: one property of every kind the bindings handle: scalars and bit-fields, three enum flavours, `FName`,
  `FString`, `FText`, object / class / weak / lazy / soft / interface references, structs, `TArray`, `TMap` and `TSet` of
  several element types, fixed-size arrays, single- and multicast delegates. Functions with in/out and array parameters, and
  `FaultNow()` which writes through a null pointer on purpose.
* `APyTestGameMode`: on `BeginPlay` it spawns three `APyTestActor` and calls `Populate`, so they reference each other
  (weak, lazy, set and map members point at real actors) and delegates are bound.
* The map is the engine's own `/Engine/Maps/Entry`; the game mode comes from `DefaultEngine.ini`.

## Toolchain
* Engine: an *installed* UE 4.25 build (pass its folder with `-Engine`). It ships prebuilt Shipping libraries, so a project
  builds only its own module, in seconds; `BuildEnvironment = Unique` is refused on an installed engine and
  `bUseLoggingInShipping` cannot be changed (so the game writes no UE log; UE4SS logs as usual).
* Compiler: Visual Studio 2019 (cl 14.29.30151) with Windows SDK 10.0.18362. UBT keys toolchains by the compiler's *product
  version* (`WindowsPlatform.CompilerVersion = "14.29.30151"` in the `.Target.cs` files); without it UBT 4.25 picks the newest
  Visual Studio on the machine.
* Run from a drive-letter checkout; cmd.exe cannot use UNC working directories.

## Recipe
```
tools\package_testgame.ps1 -Engine <path to UE_4.25>     # build + cook + stage, ~1-2 minutes (first cook compiles shaders: ~9 minutes)
```
The script first builds the project's editor module (`PyTestGameEditor`: the cook runs the editor, which must be able to
load it), then builds, cooks and stages the game. Without that module the cook fails with
`Unable to load module 'PyTestGame'`.

## Installing UE4SS and the mod into the staged game
```
python tools\testgame.py install            # add --build to build UE4SS, the proxy and the mod first
```
[`tools/testgame.py`](../tools/testgame.py) packages the mod, copies `UE4SS.dll` and the proxy `dwmapi.dll` from `build\`
next to the staged executable, writes the test game's own UE4SS settings ([`testgame/ue4ss`](../testgame/ue4ss), UE4SS
windows off) and links `Mods\PythonScripting` to `dist\PythonScripting`. Use a link, not a copy: `package_testgame.ps1`
removes the link before UAT cleans the staging folder and refuses to continue when it finds a real folder there.

## Running the test scripts
```
python tools\testgame.py run arrays_main                    # one script
python tools\testgame.py all                                # every test script; exit code 0 only if all pass
python tools\testgame.py all --only arrays_main,enums_main
```
Run it with a Windows Python (it starts and ends the game process). For each script the runner writes it as the mod's
`main.py`, starts the game headless (`-nullrhi -nosound -nosplash -unattended`; start-up takes about 3 seconds), reads
`UE4SS.log` until the script's finish marker appears, and ends exactly the process it started. One game process per script,
one after another, because scripts leave hooks and subscriptions behind. The `main.py` that was there before is put back.

| Result | Where |
|---|---|
| What each script printed | `testgame\runs\<name>.txt` |
| Verdicts of the last pass | `testgame\last_results.json` |

[`python/examples/manifest.json`](../python/examples/manifest.json) lists every script:

| Field | Meaning |
|---|---|
| `marker` | The text the script prints when it is done. It must not occur in UE4SS's own log lines. |
| `kind` | `test` is run; `probe` only prints information for a person and is not run. |
| `min_pass` | The least number of `PASS` lines, so a script that silently stops checking is noticed. |
| `tolerate_tracebacks` | For scripts that raise on purpose: a traceback neither ends the wait nor fails the script. |
| `startup` | The script must be `main.py` itself. The others are started by a small wrapper once the three test actors exist, because under load the first engine tick can come before the game mode has spawned them. |
| `env` | Environment variables for the game process. |
| `expect_exit_within` | The script quits the game; the process must be gone within this many seconds. |
| `expected_failure` | The reason a script fails because of a known defect. It is reported as `KNOWN` and does not fail the pass; when it passes after all, the pass fails until the entry is removed. |

A script passed when its marker is in the log, there is no `FAIL` line (and no traceback, unless tolerated) and the `PASS`
count reaches the floor. A check a script cannot run prints `name: SKIP (reason)`; skips are counted, not failures. A
timeout, a new crash dump (`crash_*.dmp`) or the game exiting before the marker is reported as the reason.

## Trying key bindings by hand (needs a window)
Real key presses need a game window with the focus. Install `python/examples/keys_manual_main.py` as `main.py` and start the
game without `-nullrhi` (or `python tools\testgame.py run keys_manual_main --windowed --timeout 300`); click into its window and press F6, Ctrl+F6, Shift+F6, Ctrl+Shift+F6, then F7. Each press prints a
`KEY ...` line to `UE4SS.log`; after F7 the F6 bindings are gone.
