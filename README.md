# ue4ss-python

Python scripting for Unreal Engine games through [UE4SS](https://github.com/UE4SS-RE/RE-UE4SS).

It is a UE4SS C++ mod (`PythonScripting`) that embeds CPython 3.12 and gives scripts an `ue4ss` module: find objects,
read and write their properties, call engine and Blueprint functions, hook functions, bind to event dispatchers, react to
keys, and schedule work on the game thread. An optional typed layer (`ue4ss.engine`) turns the game's reflection data into
Python types and `.pyi` stubs, so an editor completes and checks game code.

```python
import ue4ss
from ue4ss import Key

controller = ue4ss.find_first_of("PlayerController")
if controller is not None and controller.Pawn is not None:
    print("the player stands at", controller.Pawn.K2_GetActorLocation())

print(len(ue4ss.find_all_of("Actor")), "actors in the level")
ue4ss.register_key_down(Key.F5, ue4ss.reload)                 # F5 runs this script again
ue4ss.execute_with_delay(2000, lambda: print("two seconds later, on the game thread"))
```

## Status

- Windows x64 only. Tested with Unreal Engine 4.25 games; other engine versions are untested.
- Version 0.1: the API can still change. `docs/API.md` is the reference.

## Requirements

- A game with UE4SS installed, where **UE4SS is built from the same RE-UE4SS commit and build configuration as this mod**
  (currently commit `97adba0`, `Game__Shipping__Win64`). UE4SS's C++ class layouts are not stable between commits, so a
  different UE4SS build can crash. See `docs/RELEASING.md`.
- Microsoft Visual C++ Redistributable 2015-2022 (x64).

Python itself is bundled with the mod; nothing else has to be installed.

## Install (from a release zip)

1. Copy the `PythonScripting` folder into `<Game>/Binaries/Win64/Mods/`.
2. Add the line `PythonScripting : 1` to `Mods/mods.txt` (or keep `enabled.txt` in the folder).
3. Put your script in `PythonScripting/python_scripts/main.py` and start the game.
   Output and tracebacks go to `UE4SS.log` with the prefix `[Python]`.

## Build

Windows, MSVC (C++23), CMake, Rust, and access to the private UEPseudo repository (a GitHub account linked to Epic Games).
`docs/BUILDING.md` has the steps:

```powershell
powershell -ExecutionPolicy Bypass -File tools\setup_windows.ps1     # toolchain check, pinned CPython, RE-UE4SS checkout
powershell -ExecutionPolicy Bypass -File tools\build.ps1             # main.dll
python tools\package_mod.py --main-dll build\src\main.dll --runtime-dir third_party\python\runtime --out-dir dist
```

## Without a game

`python/mock` holds a pure-Python `ue4ss` with the same API and a fake object model, for testing scripts and tools:

```
python3 -m pytest python/tests tools -q      # Python tests (any OS)
tools/run_host_tests.sh                      # C++ tests of core/ (clang++, any OS)
```

## Layout

| Path | What |
|---|---|
| `src/` | The UE4SS mod (C++, Windows) |
| `core/` | Header-only logic without Unreal includes, with host tests |
| `python/lib/ue4ss/` | Pure-Python modules shipped with the mod: typed layer, stub generator, bytecode reader, call tracer |
| `python/mock/` | The off-game `ue4ss` module |
| `python/examples/` | In-game examples and tests (`manifest.json` lists them) |
| `testgame/` | A small UE 4.25 project used as a test target (`docs/TESTGAME.md`) |
| `tools/` | Build, package, release and typecheck scripts |
| `docs/` | API reference and design records (threading, lifetime, faults, typed API, Blueprint hooks) |

The design records name Chivalry 2 in places: it is the game most measurements were taken in. The mod has no dependency
on it.

## Licence

MIT, see `LICENSE`. Third-party components: `THIRD_PARTY.md`.
