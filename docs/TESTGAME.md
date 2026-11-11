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
The editor target (`PyTestGameEditor`, needed by the cook) builds with
`Engine\Build\BatchFiles\Build.bat PyTestGameEditor Win64 Development -Project=<uproject> -2019`.

## Installing UE4SS and the mod into the staged game
1. Copy `UE4SS.dll` and the proxy `dwmapi.dll` you built (see `docs/BUILDING.md`) next to the staged game's executable
   (`<staged>\PyTestGame\Binaries\Win64`), together with `testgame/ue4ss/UE4SS-settings.ini` and `testgame/ue4ss/Mods`.
2. Link `dist\PythonScripting` (the output of `tools/package_mod.py`) as `Binaries\Win64\Mods\PythonScripting` with
   `mklink /J`. Use a link, not a copy: `package_testgame.ps1` removes the link before UAT cleans the staging folder and
   refuses to continue when it finds a real folder there.
3. Copy an example to `Mods\PythonScripting\python_scripts\main.py` and start the game.

## Running an example
The game runs headless with `-nullrhi -nosound -nosplash -unattended`; the engine tick, the mod, `find_all_of` and every
example work without an RHI, and start-up takes about 3 seconds. Output goes to `UE4SS.log` next to the executable with the
prefix `[Python]`.

`python/examples/manifest.json` lists every example: the text it prints when done (`marker`), the least number of `PASS`
lines (`min_pass`, so a script that silently stops checking is noticed), and `tolerate_tracebacks` for examples that raise on
purpose. Scripts that only print information for a person are `"kind": "probe"`. A check an example cannot run prints
`name: SKIP (reason)`. An example passed when its marker is in the log, there is no `FAIL` line and the `PASS` count reaches
the floor. Examples with `"startup": true` must be `main.py` themselves; the others expect the three test actors to exist,
which under load can take a few engine ticks after start.

This repository does not contain an automated runner for the set; examples are run one per game process (they leave hooks
and subscriptions behind).

## Trying key bindings by hand (needs a window)
Real key presses need a game window with the focus. Install `python/examples/keys_manual_main.py` as `main.py` and start the
game without `-nullrhi`; click into its window and press F6, Ctrl+F6, Shift+F6, Ctrl+Shift+F6, then F7. Each press prints a
`KEY ...` line to `UE4SS.log`; after F7 the F6 bindings are gone.
