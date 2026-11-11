# Python scripting for UE 4.25 games via UE4SS

## Goal
An injectable Python scripting layer for Unreal Engine games (primary target: UE 4.25), equivalent in spirit to the Lua scripting in [RE-UE4SS](https://github.com/UE4SS-RE/RE-UE4SS).

## Verification status
Checked against RE-UE4SS at commit `97adba01e9fcb3c208376581e54edb9ef466f233` (2026-10-18), cloned to `../RE-UE4SS`.

**UEPseudo** (`deps/first/Unreal`, `Re-UE4SS/UEPseudo`) is a private repository. A local checkout at the pinned commit `fbb4fdd3fffc59d5b5268b23a643f500f702115e` is in use, and the following API names are verified from it: `FUObjectDeleteListener::NotifyUObjectDeleted(const UObjectBase*, int32 Index)` and `FUObjectCreateListener::NotifyUObjectCreated(...)` (both also `OnUObjectArrayShutdown()`), `UObjectArray::Add/RemoveUObject{Create,Delete}Listener`, `UObjectArray::AllocateSerialNumber(int32)`, `Hook::RegisterEngineTickPostCallback(callback, FCallbackOptions)`. ProcessEvent/RegisterHook/FindObjects names are still to be read from `include/Unreal/` when those tickets are worked.

## Verdict
Feasible. The hard parts (injection, signature scanning, access to UE reflection) are already solved by UE4SS. Python is a different binding layer on top.

## Recommended architecture
Write a **UE4SS C++ mod** (UE4SS loads C++ mods as DLLs) that embeds CPython and exposes the UE4SS Unreal wrapper API to Python. Do not fork UE4SS unless core internals must change.

## How UE4SS C++ mods work (verified)
- **Layout and loading.** `Mods/<Name>/dlls/main.dll` (fallback `<Name>.dll`). `CppMod.cpp` calls `AddDllDirectory(<mod>/dlls)` and then `LoadLibraryExW(LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS)`. `python3XX.dll` placed next to `main.dll` therefore resolves without extra work.
- **Exports.** `extern "C" __declspec(dllexport)` `start_mod()` returning a `CppUserModBase*`, and `uninstall_mod(CppUserModBase*)`.
- **Enabling.** A line `Name : 1` in `Mods/mods.txt` (placed above the `Keybinds` entry), or an empty `enabled.txt` in the mod folder (bypasses load order).
- **Mod class.** Subclass `RC::CppUserModBase` (`UE4SS/include/Mod/CppUserModBase.hpp`). Virtual callbacks: `on_update`, `on_unreal_init`, `on_ui_init`, `on_program_start`, `on_dll_load`, `on_cpp_mods_loaded`, `render_tab`, and the `on_lua_start/stop` family. Protected helpers: `register_keydown_event`, `register_tab`, `register_on_*`. Metadata fields: `ModName`, `ModVersion`, `ModDescription`, `ModAuthors`.
- **No reload path.** `CppMod::uninstall()` calls `uninstall_mod` but does not free the DLL. Reload has to be implemented inside our mod.
- **Examples.** `cppmods/KismetDebuggerMod`, `cppmods/EventViewerMod`. The upstream template repo is `UE4SS-RE/UE4SSCPPTemplate`.

## Threading facts (verified)
- `on_update` is dispatched from UE4SS's own update loop (`UE4SSProgram.cpp`, sleeps 5 ms per iteration). It is **not** the game thread.
- Game-thread entry points: `Unreal::Hook::RegisterEngineTickPostCallback` (guard with `UE4SSRuntime::IsEngineTickAvailable()`) and a ProcessEvent hook. Lua's `ExecuteInGameThread` queues actions and drains them from one of these (`GameThreadExecutionMethod::{ProcessEvent, EngineTick}`, default from settings `DefaultExecuteInGameThreadMethod`). Our executor must do the same.

## Platform
- The game and the injected DLL are Windows x64. The mod must be a Windows PE DLL, and the embedded Python must be the Windows build (`python3XX.dll`, embeddable distribution).
- **Build system.** RE-UE4SS has both CMake (3.22+, the default; xmake may be deprecated) and xmake. Needs MSVC with C++23 and Rust 1.73+ (patternsleuth). A C++ mod is a `SHARED` library linking the `UE4SS` target (xmake: `ue4ss.mod` rule).
- **Build configuration must match.** Configs are `<Target>__<Config>__Win64` (targets `Game`, `LessEqual421`, `CasePreserving`; configs `Dev`, `Debug`, `Shipping`, `Test`). UE4SS exports classes that pass STL types, so our mod must be built with the same config as the installed UE4SS. UE4SS uses the dynamic CRT (`/MD`, `/MDd` in Debug). A UE 4.25 game uses the `Game` target.
- **Cross-compiling.** Upstream documents Linux cross-builds (xwin or msvc-wine). This project still builds only on Windows with MSVC, by decision. WSL is for editing code, Python and host-side unit tests of UE-free logic.
- Submodule URLs in `.gitmodules` are SSH (`git@github.com:...`).
- Pick one Python version (3.12) and use it for both compile and runtime.

## Build steps
1. **Baseline.** Confirm UE4SS works on the target 4.25 game: live view, object dump, a trivial Lua hook. Read the Lua binding layer, which is the spec to port: `UE4SS/include/LuaType/` (34 headers: `LuaUObject`, `LuaUClass`, `LuaUFunction`, `LuaUStruct`, `LuaUScriptStruct`, `LuaUEnum`, `LuaFName`, `LuaFString`, `LuaFText`, `LuaTArray`, `LuaTMap`, `LuaTSet`, `LuaX*Property` for FProperty wrappers, ...) and `UE4SS/include/LuaLibrary.hpp` (global functions).
2. **Skeleton mod.** C++ mod that embeds CPython, initialises the interpreter (`PyConfig`, isolated), runs `Mods/<name>/python_scripts/main.py` (not `scripts/`: UE4SS treats a mod folder containing `scripts` as a Lua mod), and redirects stdout/stderr to the UE4SS log. Use the raw C API: reflection-driven dynamic attribute access maps directly onto `tp_getattro`/`tp_setattro`, and it avoids tying us to pybind11's RTTI/exception settings.
3. **Core type bindings.** UObject, UClass, UFunction, FName, FString, TArray, TMap, structs. Property access via reflection (`obj.Health`, `obj.Health = 5`) over the UE4SS Unreal wrapper. The Lua UObject wrapper exposes `GetFullName/GetFName/GetClass/GetOuter/IsA/IsValid/GetPropertyValue/SetPropertyValue/CallFunction`, with `__index/__newindex` resolving custom properties and then reflected FProperties.
4. **Object lifetime.** UE4SS's Lua layer keys live wrappers by object address and drops them in `FUObjectDeleteListener::NotifyUObjectDeleted`; that cannot detect slot or address reuse. Our design (details in `docs/LIFETIME.md`): a handle table with a per-index generation bumped by the same delete listener, plus `IsUnreachable`/pending-kill checks on every access. The engine serial number is not used (it only exists after an engine call allocates it).
5. **Function calls.** `obj.Func(args)` through ProcessEvent with parameter-struct marshalling (in, out, and return params). Mirror `LuaUObject::CallFunction`.
6. **Hooks and events.** `register_hook(path, pre, post)` mirroring Lua `RegisterHook`: returns `(pre_id, post_id)`, callbacks get `self` then the params, the function must already be loaded, and for `/Script/` paths pre and post callbacks both run while other paths get a post callback only. Also `notify_on_new_object`, key bindings, `find_first_of`, `find_all_of`, `execute_in_game_thread`.
7. **Threading.** Hooks and engine-tick callbacks run on the game thread; `on_update` and `on_unreal_init` run on UE4SS's own event-loop thread, which is not the game thread. Policy (details in `docs/THREADING.md`): release the GIL after startup, take it with an RAII guard on every engine entry, release it around engine calls, and queue work from Python threads to the engine tick. UE calls raise off the game thread.
8. **Packaging.** Bundle `pythonXY.dll` and the stdlib zip next to `main.dll` in `dlls/` (DLL directory is added by UE4SS). Hot reload is ours: purge script modules, unregister hooks, re-run `main.py`. Do not `Py_Finalize` and re-initialise.
9. **Optional.** Generate `.pyi` stubs from the dumped SDK so IDEs can autocomplete game classes. Candidate inputs: UE4SS `SDKGenerator`, `JMapGenerator`, `USMapGenerator` outputs.

## UE 4.25 support (verified)
- UE4SS covers 4.7 to 5.8. `assets/MemberVarLayoutTemplates/MemberVariableLayout_4_25_Template.ini` exists, with matching VTable layout templates. Per-game signatures (`UE4SS_Signatures`) and layout overrides may still be needed per game.
- 4.25 is where UProperty became FField/FProperty; the `LuaX*` wrappers target FProperty, so the port uses the same model. `CasePreserving` is a 4.27 concern and not relevant to 4.25.

## Editor Python plugin (PythonScriptPlugin)
- Cannot be reused directly: editor-only, compiled into the engine, absent from shipped games, depends on engine modules and headers.
- Useful only as a design reference: it builds Python types dynamically from UClass reflection. It is under the Unreal EULA, so do not copy its code into an open-source project.
- Permissively licensed reference: 20tab/UnrealEnginePython (runtime embed, MIT, old and unmaintained).

## Effort
- MVP (run a script, find objects, read/write properties, call functions, one hook): roughly 2-4 weeks for someone comfortable with C++ and UE internals.
- Parity with the Lua API: months.

## Risks
- Per-game struct/property layout quirks.
- GIL and threading deadlocks (hooks on the game thread, `on_update` on another thread).
- UObject lifetime crashes.
- ABI: mod must match the UE4SS build config exactly.
- No upstream reload for C++ mods, and CPython cannot be cleanly re-initialised.
- Access to the Unreal wrapper source (private submodule).
- Anti-cheat interaction.
- Python runtime size and CRT conflicts (both sides use `/MD`, which helps).

## Verification
Load in a 4.25 test game and run a script that finds the player pawn, reads and writes a property, hooks a function, and survives a level change and a GC pass.
