# Releasing: CRT/ABI checklist and the release zip

`tools/make_release.py` checks an assembled mod folder (`tools/package_mod.py` output, `dist/PythonScripting`) and packs it into
`release/PythonScripting-<version>-win64.zip`. Run it last, on the machine that built `main.dll`:

```
python tools\make_release.py --build --with-ue4ss      # Windows: build everything, package, check, zip with the matching UE4SS
python tools\make_release.py --build                   # the same without the UE4SS files in the zip
python3 -I tools/make_release.py                       # check and zip the dist/PythonScripting that is already there
python3 -I tools/make_release.py --check-only          # only the checks
```

| Option | Effect |
|---|---|
| `--build` | Build UE4SS, the proxy and the mod ([`tools/build.ps1`](../tools/build.ps1)) and package the mod first. |
| `--with-ue4ss` | Add `ue4ss/UE4SS.dll` and `ue4ss/dwmapi.dll` (from `build\Game__Shipping__Win64\bin`) and the UE4SS licence. The zip is named `...-win64-with-ue4ss.zip` and its README tells the user to copy the two files next to the game's executable. |
| `--host-dll <UE4SS.dll>` | The UE4SS.dll the import check treats as present (default: the one of this build). |
| `--ue4ss-ref <text>` | How the README names the UE4SS version. |

Prefer `--with-ue4ss` for a public release: without it every user has to build UE4SS from the same commit themselves
(item 7 of the checklist).

The version comes from `core/include/pyue/version.hpp`. The zip is reproducible (sorted members, fixed timestamps) and is read back
and verified (required members present, `SHA256SUMS.txt` matches) before the script reports success.

## What the zip contains
- `PythonScripting/` the mod folder: `enabled.txt`, `python_scripts/main.py`, `dlls/` (`main.dll`, `python312.dll`,
  `python312.zip`, extension modules, `vcruntime140*.dll`, OpenSSL, libffi, sqlite3, `LICENSE.txt`).
- `README.txt` install steps and requirements, `THIRD_PARTY_NOTICES.txt` (CPython licence text plus what else is bundled),
  `docs/API.md`, `SHA256SUMS.txt`.

## Checklist (what is automated is marked)
1. Dynamic CRT (`/MD`). `main.dll` must import `VCRUNTIME140.dll` / `MSVCP140.dll` rather than carry its own copy, so that it
   shares the heap and `std::` types with UE4SS. Release builds only: a debug runtime import (`MSVCP140D.dll`, `VCRUNTIME140D.dll`,
   `ucrtbased.dll`, `python312_d.dll`) is refused. (automated)
2. Every import resolves on a clean machine. For `main.dll`, `python312.dll`, every `.pyd` and the other DLLs, each import
   must be shipped in `dlls/`, be a Windows system DLL (`api-ms-win-*`, `kernel32.dll`, ...), or be imported by the host
   `UE4SS.dll` as well (then it is there whenever UE4SS loads). Redistributable DLLs (`msvcp140*`, `vcruntime140*`) are never
   accepted just because System32 has them. (automated)
   - `vcruntime140.dll` and `vcruntime140_1.dll` ship with CPython and are copied into `dlls/`.
   - `msvcp140.dll` is not shipped: `main.dll` uses the C++ standard library and UE4SS needs it too, so the game or the
     Visual C++ Redistributable provides it. Decision recorded in `deo.3`; bundling it is possible if a game turns out
     to lack it.
3. One CPython only: exactly one `python3NN.dll`, and it is 3.12 (`cp312`). Wheels for a later packages feature must be
   `cp312-win_amd64`. (automated)
4. The standard library zip (`python312.zip`) and `vcruntime140*.dll` are present. (automated)
5. Licences: `dlls/LICENSE.txt` (CPython) is in the folder and its text is copied into `THIRD_PARTY_NOTICES.txt`. (automated)
6. The VC runtime the process loads is the first `vcruntime140.dll` found, which may be the game's. Compare versions before a
   release and rebuild or re-bundle if the game's is older than the toolset that built `main.dll` (PowerShell:
   `(Get-Item .\vcruntime140.dll).VersionInfo.FileVersion`). Both test games ran with the bundled files (not automated).
7. `UE4SS.dll` of the target game must be the RE-UE4SS source (3.0.1, commit `97adba01` at the time of writing) that `main.dll`
   was built against: `CppUserModBase` and the class layouts are not ABI-stable across commits. `main.dll` imports `UE4SS.dll`,
   which the check lists as a note. Release notes name the commit. (not automated)
8. Smoke test the zip, not the build folder: unpack it into a clean copy of a game's `Mods` folder and run the examples
   (see `docs/TESTGAME.md`), then one pass in the target game. (not automated)
