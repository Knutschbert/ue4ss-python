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

**Where to go**

| You want to | Read |
|---|---|
| Write your first script | [USAGE.md](USAGE.md) |
| Look up a function | [docs/API.md](docs/API.md) |
| Build the mod | [Build and install from source](#build-and-install-from-source), details in [docs/BUILDING.md](docs/BUILDING.md) |
| Use another UE4SS version | [Using another UE4SS version](#using-another-ue4ss-version) |
| Get editor completion | [docs/VSCODE.md](docs/VSCODE.md) |

## Status

- Windows x64 only. Tested with Unreal Engine 4.25 games; other engine versions are untested.
- Version 0.1: the API can still change.
- There is no prebuilt release yet: build it from source (below).

## Requirements

- **A game in which UE4SS works.** If UE4SS is new to you, start with its
  [installation guide](https://docs.ue4ss.com/dev/installation-guide.html) and, for games that need it,
  [fixing compatibility problems](https://docs.ue4ss.com/dev/guides/fixing-compatibility-problems.html). Get stock UE4SS
  running in your game first; keep the `UE4SS-settings.ini` that works.
- **A UE4SS built from the same RE-UE4SS commit and build configuration as this mod.** The tested commit is
  [`97adba01`](https://github.com/UE4SS-RE/RE-UE4SS/commit/97adba01e9fcb3c208376581e54edb9ef466f233) with
  `Game__Shipping__Win64`. The build steps below produce that UE4SS for you.
- Microsoft Visual C++ Redistributable 2015-2022 (x64).

> [!WARNING]
> A stock UE4SS download does not work with this mod unless it was built from the very same commit. The mod links
> `UE4SS.dll` and shares C++ class layouts with it, and those change between commits; a mismatch crashes the game at
> start. Background: [docs/RELEASING.md](docs/RELEASING.md).

Python itself is bundled with the mod; nothing has to be installed for it.

## Build and install from source

### 1. Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Windows | x64 | The checkout must be on a drive letter, not a network or WSL path |
| Visual Studio 2022 or Build Tools | 17.13 or newer | "Desktop development with C++" workload, including "C++ CMake tools for Windows" (brings Ninja) |
| [CMake](https://cmake.org/download/) | 3.22 or newer | |
| [Rust](https://www.rust-lang.org/tools/install) | 1.73 or newer | UE4SS builds one component with it |
| git | any | |
| Python | 3.10 or newer | Only to run the packaging script |
| GitHub account linked to Epic Games | | UE4SS needs the private `UEPseudo` repository; see [Epic's instructions](https://www.unrealengine.com/en-US/ue-on-github) |

### 2. Get the sources

```powershell
git clone <this repository> C:\dev\ue4ss-python
cd C:\dev\ue4ss-python
powershell -ExecutionPolicy Bypass -File tools\setup_windows.ps1
```

The script checks the tools, downloads the pinned CPython 3.12.10 into `third_party\python`, and clones RE-UE4SS into
`C:\dev\RE-UE4SS` (next to this repository) **at the tested commit**, with its submodules. If it warns that
`deps/first/Unreal` is missing, your GitHub account has no access to `UEPseudo` yet; see
[docs/BUILDING.md](docs/BUILDING.md#one-time-setup).

### 3. Build UE4SS, its proxy and the mod

```powershell
powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Target UE4SS    # UE4SS.dll (the first build takes a while)
powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Target proxy    # dwmapi.dll, the loader
powershell -ExecutionPolicy Bypass -File tools\build.ps1                  # main.dll, this mod
```

All three come from one build tree, so they match:

| File | Where it is built |
|---|---|
| `UE4SS.dll`, `dwmapi.dll` | `build\Game__Shipping__Win64\bin\` |
| `main.dll` | `build\src\` |

### 4. Package the mod

```powershell
python tools\package_mod.py --main-dll build\src\main.dll --runtime-dir third_party\python\runtime --out-dir dist
```

This writes the complete mod folder `dist\PythonScripting`: the DLL, CPython, and `python_scripts\main.py`. Without a
script of your own that is a starter script, which prints one line and binds F5 to reload. Packaging again keeps the
scripts you put into that folder.

### 5. Install into the game

In `<Game>\Binaries\Win64` (the folder with the game's `...-Win64-Shipping.exe`):

1. Replace `UE4SS.dll` and `dwmapi.dll` with the two files from step 3. Keep your `UE4SS-settings.ini` and `Mods` folder.
2. Copy `dist\PythonScripting` to `Mods\PythonScripting`. While developing, a link saves the copying:
   `mklink /J "<Game>\Binaries\Win64\Mods\PythonScripting" C:\dev\ue4ss-python\dist\PythonScripting`.
3. The folder contains `enabled.txt`, which enables the mod. Alternatively add `PythonScripting : 1` to `Mods\mods.txt`.
4. Start the game. The starter script prints `[Python] PythonScripting is running` to `UE4SS.log`; replace
   `Mods\PythonScripting\python_scripts\main.py` with your own script. Tracebacks go to the same log.

Continue with [USAGE.md](USAGE.md): first script, checking the log, reloading, hooks, typings.

## Using another UE4SS version

The mod has to be **rebuilt** against the UE4SS source you want; a `main.dll` built for one commit cannot be used with
another.

```powershell
powershell -ExecutionPolicy Bypass -File tools\setup_windows.ps1 -Ue4ssRef <commit, tag or branch>
powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Reconfigure -Target UE4SS
powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Target proxy
powershell -ExecutionPolicy Bypass -File tools\build.ps1
```

Then package and install as in steps 4 and 5, **all three DLLs together**.

> [!NOTE]
> Only commit `97adba01` is tested. The mod uses UE4SS's C++ mod interface and hooking internals, so a commit far from it
> may not compile until `src/` is adapted. `-Reconfigure` is required after switching: it deletes the build tree and
> compiles everything again. Details: [docs/BUILDING.md](docs/BUILDING.md#building-against-another-ue4ss-commit).

The build configuration must match too: `Game__Shipping__Win64` suits games on engine versions above 4.21; see
[docs/BUILDING.md](docs/BUILDING.md#build-configuration) for the others.

## Install from a release zip

For later, when releases exist. The zip contains the `PythonScripting` folder and names the UE4SS commit it was built
against; that UE4SS has to be in the game (see [Requirements](#requirements)).

1. Copy the `PythonScripting` folder into `<Game>\Binaries\Win64\Mods\`.
2. Add the line `PythonScripting : 1` to `Mods\mods.txt` (or keep `enabled.txt` in the folder).
3. Put your script in `PythonScripting\python_scripts\main.py` and start the game.

How a release is made and checked: [docs/RELEASING.md](docs/RELEASING.md).

## Without a game

[`python/mock`](python/mock) holds a pure-Python `ue4ss` with the same API and a fake object model, for testing scripts and
tools:

```
python3 -m pip install -r requirements-dev.txt    # once: pytest, mypy
python3 -m pytest python/tests tools -q      # Python tests (any OS)
tools/run_host_tests.sh                      # C++ tests of core/ (clang++, any OS)
```

Both run on every push ([workflow](.github/workflows/tests.yml)).

## Layout

| Path | What |
|---|---|
| [`src/`](src) | The UE4SS mod (C++, Windows) |
| [`core/`](core) | Header-only logic without Unreal includes, with host tests |
| [`python/lib/ue4ss/`](python/lib/ue4ss) | Pure-Python modules shipped with the mod: typed layer, stub generator, bytecode reader, call tracer |
| [`python/mock/`](python/mock) | The off-game `ue4ss` module |
| [`python/typings/`](python/typings) | Shipped typings of the base `ue4ss` module ([docs/STUBS.md](docs/STUBS.md)) |
| [`python/examples/`](python/examples) | In-game examples and tests (`manifest.json` lists them) |
| [`testgame/`](testgame) | A small UE 4.25 project used as a test target ([docs/TESTGAME.md](docs/TESTGAME.md)) |
| [`tools/`](tools) | Build, package, release and typecheck scripts |
| [`docs/`](docs) | Reference and design records, listed below |

## Documentation

| File | Content |
|---|---|
| [USAGE.md](USAGE.md) | Getting started with scripts |
| [docs/API.md](docs/API.md) | The `ue4ss` module: every function, type conversion and error |
| [docs/TYPED_API.md](docs/TYPED_API.md) | `ue4ss.engine`: engine classes as Python types, generating typings |
| [docs/STUBS.md](docs/STUBS.md) | Which typings exist and where they come from |
| [docs/VSCODE.md](docs/VSCODE.md) | Editor setup and the edit-deploy-try loop |
| [docs/BLUEPRINT_HOOKS.md](docs/BLUEPRINT_HOOKS.md) | Hooking functions defined in Blueprints: what works |
| [docs/THREADING.md](docs/THREADING.md) | The game thread, Python threads, shutdown |
| [docs/LIFETIME.md](docs/LIFETIME.md) | Object lifetime and stale references |
| [docs/FAULTS.md](docs/FAULTS.md) | What happens when an engine call faults |
| [docs/BUILDING.md](docs/BUILDING.md) | Building in detail |
| [docs/RELEASING.md](docs/RELEASING.md) | Release zip and the binary compatibility checklist |
| [docs/TESTGAME.md](docs/TESTGAME.md) | The test game project |
| [IMPLEMENTATION.md](IMPLEMENTATION.md) | Design overview |

The design records name Chivalry 2 in places: it is the game most measurements were taken in. The mod has no dependency
on it.

## Licence

MIT, see [LICENSE](LICENSE). Third-party components: [THIRD_PARTY.md](THIRD_PARTY.md).
