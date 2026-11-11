# Third-party components

Nothing listed here is stored in this repository; the build fetches or links it.

| Component | Used for | Licence |
|---|---|---|
| [RE-UE4SS](https://github.com/UE4SS-RE/RE-UE4SS) | The mod is a UE4SS C++ mod and links `UE4SS.dll` | MIT |
| UEPseudo (`Re-UE4SS/UEPseudo`, submodule of RE-UE4SS) | Unreal type declarations needed to build UE4SS and this mod | Private; needs a GitHub account linked to Epic Games. Not redistributable. |
| [CPython 3.12](https://www.python.org) embeddable distribution | The interpreter shipped in the mod's `dlls` folder (with OpenSSL, libffi, SQLite, VC runtime) | PSF licence; details in the release zip's `THIRD_PARTY_NOTICES.txt` |
| [PolyHook 2](https://github.com/stevemk14ebr/PolyHook_2_0) | Detours (the copy RE-UE4SS builds) | MIT |

This repository contains no Unreal Engine source code. `testgame/PyTestGame` is an own UE 4.25 project and needs an
installed engine, which is subject to the Unreal Engine EULA.
