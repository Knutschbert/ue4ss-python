# Building (Windows only)

The mod is a Windows x64 DLL built with MSVC, together with the UE4SS it runs in. The short version is in the
[README](../README.md#build-and-install-from-source); this file has the details. Linux or WSL is only for editing, the
Python tests (`python3 -m pip install -r requirements-dev.txt`, then `python3 -m pytest python/tests tools -q`) and the
host tests of `core/` ([`tools/run_host_tests.sh`](../tools/run_host_tests.sh)).

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Visual Studio 2022 or Build Tools | 17.13 or newer (MSVC toolset 14.43, C++23) | "Desktop development with C++" with "C++ CMake tools for Windows", which brings Ninja |
| CMake | 3.22 or newer | |
| Rust | 1.73 or newer | RE-UE4SS builds `patternsleuth` with it |
| git | any | |
| Python | 3.10 or newer | Only for [`tools/package_mod.py`](../tools/package_mod.py) and [`tools/make_release.py`](../tools/make_release.py) |
| Access to `Re-UE4SS/UEPseudo` | | A private repository, the submodule `deps/first/Unreal` of RE-UE4SS |

`UEPseudo` holds Unreal type declarations and is only readable by GitHub accounts that are
[linked to an Epic Games account](https://www.unrealengine.com/en-US/ue-on-github). It is not part of this repository
and must not be redistributed ([THIRD_PARTY.md](../THIRD_PARTY.md)).

## Where to put the checkouts

Use a Windows drive, not a `\\wsl.localhost\...` or other UNC path (MSVC, CMake and git handle those badly; the scripts
refuse to run from one). The repositories sit side by side:

```
C:\dev\ue4ss-python      this repository
C:\dev\RE-UE4SS          cloned by the setup script
```

## One-time setup

```powershell
powershell -ExecutionPolicy Bypass -File tools\setup_windows.ps1
```

[`tools/setup_windows.ps1`](../tools/setup_windows.ps1) does three things and builds nothing:

1. checks git, CMake, Rust and MSVC;
2. downloads the pinned CPython 3.12.10 (SHA-256 verified) into `third_party\python`: `runtime\` for the DLL and the
   standard-library zip, `sdk\` for headers and import libraries;
3. clones RE-UE4SS to `..\RE-UE4SS`, checks out the commit this mod is tested against
   (`97adba01e9fcb3c208376581e54edb9ef466f233`) and fetches its submodules at the commits it pins.

| Parameter | Effect |
|---|---|
| `-Ue4ssRef <commit, tag or branch>` | Check out another UE4SS version (see [below](#building-against-another-ue4ss-commit)). `-Ue4ssRef ''` leaves an existing checkout where it is. |
| `-Ue4ssDir <path>` | Use a RE-UE4SS checkout somewhere else. Pass the same path to `build.ps1 -Ue4ssRoot`. |
| `-UEPseudoDir <path>` | Fill the private submodule from a local `UEPseudo` checkout when it cannot be cloned. |
| `-SkipUe4ss` | Only the toolchain check and CPython. |

An existing RE-UE4SS checkout with local changes is never switched to another commit: the script warns and keeps it.

> [!NOTE]
> "`deps/first/Unreal` is missing" at the end means the private submodule could not be cloned: the GitHub account git
> uses has no access. Link it to Epic Games, or pass `-UEPseudoDir`, and run the script again.

## Building

[`tools/build.ps1`](../tools/build.ps1) runs from a normal PowerShell; it loads the MSVC environment itself. The first
run configures the build tree `build\`, later runs are incremental.

```powershell
powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Target UE4SS    # UE4SS.dll
powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Target proxy    # dwmapi.dll, the proxy that loads UE4SS.dll
powershell -ExecutionPolicy Bypass -File tools\build.ps1                  # main.dll, the mod
```

| Output | Path |
|---|---|
| `UE4SS.dll`, `dwmapi.dll` | `build\Game__Shipping__Win64\bin\` |
| `main.dll` | `build\src\` |

UE4SS is compiled inside this repository's build tree (the top-level [`CMakeLists.txt`](../CMakeLists.txt) adds the
RE-UE4SS checkout as a subdirectory), so the three DLLs always come from the same source and configuration. Install
all three together; do not combine them with DLLs from a separate UE4SS build or download.

| Parameter | Effect |
|---|---|
| `-Target <name>` | `PythonScripting` (default), `UE4SS`, `proxy` |
| `-Config <mode>` | The build configuration, default `Game__Shipping__Win64` ([below](#build-configuration)) |
| `-Ue4ssRoot <path>` | The RE-UE4SS checkout, default `..\RE-UE4SS` |
| `-Reconfigure` | Delete `build\` and configure again. Needed after changing `-Config`, `-Ue4ssRoot` or the UE4SS commit; everything is compiled again. |
| `-Jobs <n>` | Fewer parallel compile jobs, if `cl.exe` runs out of heap (error C1060) |

## Build configuration

UE4SS exports classes that pass STL types across the DLL boundary, so the mod and UE4SS must be built with the same
`<Target>__<Config>__Win64`. Building both through `build.ps1` guarantees that.

| Target | For |
|---|---|
| `Game` | Games on engine versions above 4.21 (the tested one) |
| `LessEqual421` | Engine 4.21 and older |
| `CasePreserving` | Games built with case-preserving names; not supported by this mod yet |

`Config` is `Shipping` for normal use (`Dev` and `Debug` exist). Both sides use the dynamic CRT (`/MD`).

## Building against another UE4SS commit

```powershell
powershell -ExecutionPolicy Bypass -File tools\setup_windows.ps1 -Ue4ssRef <commit, tag or branch>
powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Reconfigure -Target UE4SS
powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Target proxy
powershell -ExecutionPolicy Bypass -File tools\build.ps1
```

- The setup script checks out the commit and moves the submodules (including `UEPseudo`) to what that commit pins.
  Doing it by hand: `git checkout <ref>` and `git submodule update --init --recursive` in the RE-UE4SS folder, without
  `--remote`.
- `-Reconfigure` is required: the build tree still holds objects of the old commit.
- Install the new `UE4SS.dll`, `dwmapi.dll` and `main.dll` together.
- Only the pinned commit is tested. The mod is written against UE4SS's C++ mod interface (`CppUserModBase`), its
  Unreal wrapper and its hook callbacks; when those changed, `src/` has to be adapted before it compiles. Which
  compatibility has to hold between the DLLs is listed in [RELEASING.md](RELEASING.md).

## Packaging

```powershell
python tools\package_mod.py --main-dll build\src\main.dll --runtime-dir third_party\python\runtime --out-dir dist
```

[`tools/package_mod.py`](../tools/package_mod.py) assembles `dist\PythonScripting`:

```
PythonScripting\
    enabled.txt              enables the mod (or add "PythonScripting : 1" to Mods\mods.txt)
    python_scripts\          main.py goes here; the folder python\python_scripts is copied if it exists
    dlls\main.dll            this mod
    dlls\python312.dll       CPython, from third_party\python\runtime
    dlls\python312.zip       the standard library
    dlls\vcruntime140*.dll
    dlls\ue4ss\              the pure-Python modules from python\lib\ue4ss
```

UE4SS calls `AddDllDirectory(<mod>\dlls)` before loading `main.dll`, so `python312.dll` next to it resolves. The layout
follows the C++ mod guide of UE4SS (`docs/guides/creating-a-c++-mod.md` in RE-UE4SS). `--library <folder>` ships an
own Python package next to `main.py`; `--game-process <image name>` makes the script refuse while that game runs.

## Installing

In `<Game>\Binaries\Win64`:

1. replace `UE4SS.dll` and `dwmapi.dll` with the built ones; keep `UE4SS-settings.ini`, `Mods` and any custom signature
   or layout files of the working install;
2. copy or link `dist\PythonScripting` to `Mods\PythonScripting`.

Then continue with [USAGE.md](../USAGE.md). A release zip for other people is made with
[`tools/make_release.py`](../tools/make_release.py), see [RELEASING.md](RELEASING.md).
