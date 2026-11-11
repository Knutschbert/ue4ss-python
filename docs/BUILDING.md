# Building (Windows only)

The mod is a Windows x64 DLL built with MSVC. WSL is for editing and for host tests of `core/` only
(`tools/run_host_tests.sh`).

## Prerequisites
- Visual Studio 2022 (or Build Tools) with the "Desktop development with C++" workload (C++23 capable MSVC)
- CMake 3.22+
- Rust 1.73+ (RE-UE4SS builds `patternsleuth`)
- git, and access to the private `Re-UE4SS/UEPseudo` submodule (`deps/first/Unreal`)

## Where to put the checkouts
Use a Windows drive, not a `\\wsl.localhost\...` path (MSVC, CMake and git handle UNC paths badly), with the
three repositories side by side, e.g.
```
C:\dev\ue4ss-python     (this repository)
C:\dev\RE-UE4SS          (cloned by the setup script)
C:\dev\UEPseudo          (your private checkout, passed with -UEPseudoDir)
```
The setup script refuses to run from a UNC path.

## One-time setup
```powershell
powershell -ExecutionPolicy Bypass -File tools\setup_windows.ps1
```
This checks the toolchain, downloads the pinned CPython 3.12.10 (SHA-256 verified) into
`third_party/python` (`runtime/` for the DLL and stdlib zip, `sdk/` for headers and import libs) and
clones RE-UE4SS with submodules to `..\RE-UE4SS`.

If the private submodule cannot be cloned, pass a local checkout:
`tools\setup_windows.ps1 -UEPseudoDir C:\path\to\UEPseudo`.

## Building UE4SS itself
Building RE-UE4SS from source produces `UE4SS.dll` and a proxy DLL (`dwmapi.dll` by default) in
`<BuildMode>/bin/`. Install those into the game's `Binaries\Win64` (replacing the stock UE4SS) so that
the mod and UE4SS are built from the same source and config. Keep your `UE4SS-settings.ini`, `Mods`
folder and any custom signature or layout files from the working install.
```powershell
cd ..\RE-UE4SS
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Game__Shipping__Win64
cmake --build build --target UE4SS
```
Run these from an **x64 Native Tools / Developer PowerShell for Visual Studio** so `cl.exe` and Ninja are on
PATH. Ninja is used because it works with any Visual Studio version (VS 18 needs a newer CMake than 3.30 for its
own generator); this matches upstream's `docs/guides/creating-a-c++-mod.md`.

## Build configuration
UE4SS exports classes that pass STL types across the DLL boundary, so the mod must be built with
**the same `<Target>__<Config>__Win64` as the UE4SS you install into the game**, e.g.
`Game__Shipping__Win64` for a UE 4.25 game (not `LessEqual421`, which is for UE <= 4.21).
Both sides use the dynamic CRT (`/MD`, `/MDd` in Debug).

## Layout in the game
```
<Game>\Binaries\Win64\Mods\<Name>\
    enabled.txt            (or add "<Name> : 1" to Mods\mods.txt)
    python_scripts\main.py
    dlls\main.dll          (this mod)
    dlls\python312.dll     (from third_party\python\runtime)
    dlls\python312.zip
    dlls\vcruntime140*.dll
```
UE4SS calls `AddDllDirectory(<mod>\dlls)` before loading `main.dll`, so `python312.dll` next to it resolves.

## Building the mod
From a normal PowerShell (no developer prompt needed; the script loads the MSVC environment itself):
```powershell
powershell -ExecutionPolicy Bypass -File tools\build.ps1            # first run configures, later runs are incremental
powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Target UE4SS   # build UE4SS itself
powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Reconfigure    # only after changing the build type or UE4SS_ROOT; it wipes the build and recompiles UE4SS
powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Jobs 4        # if cl.exe runs out of heap (C1060)
```
The output is `main.dll`. Package with `python tools\package_mod.py --main-dll <path to main.dll>`.
The mod layout follows upstream's `MyMods` guide (`docs/guides/creating-a-c++-mod.md` in RE-UE4SS).
