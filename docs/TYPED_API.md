# Typed API: engine classes as Python types (design record for ypy.1)

Goal: scripts use engine classes, structs and enums as Python types (`from ue4ss.engine import Actor, GameplayStatics, Vector`),
and editors know them (completion, hover, type checking) **for the user's own game**. Requirement from the owner: everything that can
be generated is generated, **at runtime, inside the game, from the reflection data** the mod already reads. No SDK dump, no external
tool, no AI tool: one call in the game gives a user the typings; the runtime classes need no generation step at all.

## Decisions

1. **Where the types live: `ue4ss.engine`**, a module shipped as `dlls/ue4ss/engine.py` (source `python/lib/ue4ss/engine.py`). `ue4ss` is a
   built-in module; the bootstrap gives it a `__path__` so `import ue4ss.engine` finds the file. Names resolve lazily (module
   `__getattr__`): `engine.Actor` looks `/Script/Engine.Actor`, then `/Script/CoreUObject.<name>`, then any package; a short name that
   exists in several packages raises `EngineError` listing them, and `engine.package("Engine").Actor` / `engine.find(path)` are
   exact (`find` also takes Blueprint classes such as `/Game/Interactables/World/Seatable/LevelActor_Chair.LevelActor_Chair_C`).
2. **Objects keep their native `ue4ss.UObject` wrapper.** The generated classes are not the objects' Python type; they are *views of the
   engine class*. A metaclass makes `isinstance(obj, Actor)` true for any wrapped object whose engine class is an `Actor` (it calls
   `obj.is_a(path)`), and the generated classes mirror the engine's inheritance, so `issubclass(Pawn, Actor)` works. The classes also carry
   the class-level API: static functions (`GameplayStatics.GetPlayerController(world, 0)`, run on the class default object), `find_all()`,
   `find_first()`, `static_class()`, `default_object()`.
3. **Instance functions and properties stay dynamic on the wrapper** (`pawn.K2_GetActorLocation()`, `pawn.Health`). They are typed only in
   the `.pyi` stubs (below), not by runtime descriptors.
4. **Structs** are classes whose metaclass builds values (`Vector(X=1, Y=2, Z=3)` calls `make_struct`) and answers `isinstance` for struct
   values by struct name. **Enums** are `IntEnum`s built from `UEnum.get_names()`.
5. **Everything is created on first use and cached by engine path**, parents first. Blueprint classes work the same way through
   `find(path)` (loaded by `KismetSystemLibrary.LoadClassAsset_Blocking(path)` when needed).
6. **Stubs (`.pyi`) come from the same reflection walk**, written by the mod itself (ticket ypy.3): per package, one file for classes,
   structs and enums, with typed attributes for properties, typed methods for functions (out parameters as tuple results, in/out as in
   `docs/API.md`), `Optional[...]` for object references, `Array`/`Map`/`Set` for containers, `str` for `FText` and soft references. The
   runtime classes of decision 2 are the classes the stubs describe, so what the editor shows is what runs.

## Rejected alternatives

- **Make the generated classes the wrapper types** (one Python/C type per engine class, objects instances of them). Gives normal
  attribute lookup and real descriptors, but every wrapped object would need its class resolved and created at wrap time (thousands of
  classes, Blueprint classes appearing and vanishing), the C++ wrapper (`PyUObject`) is hot and simple today, and instance identity
  (`==`, hashing, validity checks) would have to be re-done per type. Not worth it while the metaclass view costs ~1 us. Revisit only if
  descriptors turn out to be needed at runtime.
- **A big generated module / a generation step before use.** Tens of thousands of classes in Chivalry 2 would mean seconds of import and
  megabytes of source for names a script never touches, and the file would be stale after every game update. Lazy creation costs
  0.19 ms per class including its parents.
- **Offline generators from UE4SS dumps, jmap, USMap or an SDK dump.** Optional extras at best (they depend on external tools and on the
  user having them); the in-game generator needs none, and it sees exactly the game that is running (Blueprint classes, mods).
- **Per-package submodules only (`ue4ss.engine.Engine.Actor`).** Safe against clashes but unpleasant to write; kept as the exact form
  `package("Engine").Actor` next to the flat names.
- **Runtime `__slots__`/descriptor objects for every property.** Cost on every class creation and nothing the stubs do not already give.

## Measured (test game, spike `python/examples/typed_spike_main.py`, 19 checks)

- 20 types (classes with their parents, two structs) created in 3.7 ms (0.19 ms each).
- `GameplayStatics.GetPlayerController(world, 0)`: 8.3 us against 6.3 us for `statics_object.GetPlayerController(world, 0)` today.
- `isinstance(obj, Actor)`: 1.2 us against 1.0 us for `obj.is_a("/Script/Engine.Actor")`.
- `Actor.find_all()`, static calls, structs, enums (`EPyColor` as `IntEnum`), `dir(GameplayStatics)` listing the engine functions,
  unknown names (`EngineError`), missing functions (`AttributeError`) behave as designed.

## What the generator needs from the mod (ypy.3 work list)

The reflection data the stubs need beyond what `UStruct`/`UFunction` expose now:
- `UStruct.get_properties()`: for each property its name, a type descriptor (category, engine property class, the class/struct/enum path for
  object, struct and enum properties, element/key/value descriptors for `TArray`/`TMap`/`TSet`, `ArrayDim` for fixed arrays), flags
  (blueprint read-only, config, ...), and the doc string/category metadata when the game has it.
- `UFunction.get_params()`: the same type descriptors for parameters and the return value (today only the engine property class name).
- Listing every class, struct and enum of a package (`/Script/<Package>`), and the Blueprint classes that are loaded.
- Hidden/internal names to skip (`EnumName_MAX`, generated `K2Node` helpers) and Python keyword clashes (a function named `print` or
  `import` stays reachable by `getattr`; the stub gives it a trailing underscore and a comment).

## Using it (as built)

Runtime types: `from ue4ss.engine import Actor, Pawn, GameplayStatics, Vector`; `engine.package("TBL").TBLPlayerController` for a
name that exists in several packages; `engine.find("/Game/Folder/BP_Thing.BP_Thing_C")` loads a Blueprint class when needed.

Step-by-step setup for VS Code (typings, deploy button): `docs/VSCODE.md`.

Editor typings, generated from your running game with one call:

```python
from ue4ss import engine
engine.generate_stubs(r"C:\typings")        # all /Script packages; packages=["Engine", "TBL"], blueprints=True and dependencies=False are options
```

### The game's own module

A game's native classes (`ATBLGameMode` in Chivalry 2) are reflected like engine classes, so `generate_stubs(folder)` already writes them: `ue4ss/engine/TBL.pyi` (class `TBLGameMode(GameMode)`; the `A`/`U`/`F` prefix of the C++ name is not part of the reflected name). To write only the module you work with:

```python
engine.generate_stubs(r"C:\typings", packages=["TBL"])    # TBL, plus every package it imports (Engine, CoreUObject, ...): 39 packages, 0 missing types in Chivalry 2
engine.generate_stubs(r"C:\typings", packages=["TBL"], dependencies=False)    # the module alone; references to other packages become Any
```

The returned summary has `unresolved_types`, `unresolved_references` and `unresolved_top`: the types the stubs refer to but do not contain (a class that is not loaded, a package you left out). `Any` also appears for multicast delegates and in `dict[str, Any]`, where a struct argument may be given as a dict.

### Blueprint classes

`generate_stubs(folder, blueprints=True)` adds the Blueprint classes that are **loaded right now**, and the structs and enums made in the Blueprint editor, laid out like the content browser: one module per content folder under `ue4ss.engine.Content`:

```python
from ue4ss.engine.Content.Game.Interactables.World.Seatable import LevelActor_Chair_C    # /Game/Interactables/World/Seatable/LevelActor_Chair
from ue4ss.engine.Content.Engine.EngineSky import BP_Sky_Sphere_C                       # /Engine/EngineSky/BP_Sky_Sphere
```

In Chivalry 2 on the front-end map: 1506 Blueprint classes in 325 modules, plus 40 structs and 63 enums, all packages in about 2 s. Each class lists its own variables, functions and events (an event is a method; the event graph also shows as `ExecuteUbergraph_<Class>`) and inherits from the stub of its base class, so a Blueprint class gets the whole engine API (`LevelActor_Chair_C.find_first()`, `register_hook(..., instance=chair)`).

- Generate all packages (the default) together with `blueprints=True`, or ask for what you need: `packages=["Game.Interactables"]` selects a content folder and its subfolders, and with the default `dependencies=True` the engine packages they need come along.
- **Unloaded Blueprints:** the typings only contain classes the game has loaded. `load=["/Game/Interactables/World/Seatable"]` first loads the Blueprint classes of the assets under those folders (found through the engine's asset registry; at most `load_limit`, default 1000, per call; loading takes a moment and memory) and returns the counts in `summary["load"]`. Chivalry 2 lists 3769 Blueprint assets under `/Game`; do not load them all unless you need them.
- Blueprint variables, functions and parameters whose engine names contain spaces are declared under their identifier form (`Sun height` is `Sun_height`); the runtime accepts that form (see "Names that are not Python identifiers" in `docs/API.md`). Two members that share a form are not declared (a comment lists them); use `getattr`.
- Two Blueprint classes of the same name in one folder are called `Name__Asset` (the asset that defines them). The same name in different folders is no clash: the modules differ.

Then tell the editor where they are: VS Code (Pylance) `"python.analysis.stubPath": "C:/typings"` (or `extraPaths`), pyright
`stubPath`, mypy `MYPYPATH=C:\typings`. The call runs on the game thread and takes about a second; run it from a key press or
`main.py`, not from a hook. Output: `ue4ss/__init__.pyi` (the base API), `ue4ss/engine/<Package>.pyi` (one file per engine package)
and `ue4ss/engine/__init__.pyi`.

What the stubs say: engine classes derive from `UObjectBase` (unknown attributes are errors), objects returned by `find_*` and
`static_find_object` are plain `UObject` (any attribute allowed) and are accepted wherever a typed object is asked for; properties are
attributes (`Optional` for object references, `Array`/`Map`/`Set`/`FixedArray` for containers, `str` for text and soft references), functions
are methods whose outputs and return value become the result (a tuple for several), static functions are classmethods,
`Class.find_all()/find_first()/static_class()/default_object()` return `Self`.

Checked: `tools/check_typed_usage.py` runs mypy over `python/typed_checks/usage_ok.py` (must give no error) and `usage_errors.py` (every
line ends with `# E: <message>` and must give exactly that error); pyright gives the same verdicts (`python/pyrightconfig.json`,
`python/typed_checks/pyrightconfig.json`). Both tools are installed into `third_party/typecheck` (git-ignored) with
`c:\python312\python.exe -m pip install --target third_party\typecheck mypy pyright`; `tools/typecheck.ps1` runs mypy.

## Measured (as built)

| | test game | Chivalry 2 |
|---|---|---|
| packages / classes / structs / enums | 126 / 2210 / 1602 / 924 | 109 / 2928 / 2647 / 1217 |
| stub files / size | 128 / 2.4 MB | 111 / 3.2 MB |
| `generate_stubs` | 0.4 s | 1.2 s |
| Python types for every class (`engine.class_for`) | 19 ms (0.01 ms each) | 29 ms |
| mypy over `ue4ss.engine` (all packages) | 0 errors | 0 errors |

The sizes are small enough that one file per package needs no further splitting. The generator runs in one go on the game thread (about a
second); it logs a slow-callback warning but needs no chunking at these sizes.

## Open points (decided later, tracked in the tickets)

- Name clashes between engine functions and the wrapper's own methods (`get_class`, `is_valid`, ...): the wrapper's methods win; the stub
  lists the engine function under the wrapper's prefix rule decided in ypy.3.
- Whether `engine.<Name>` should also accept the `U`/`A`/`F`/`E` prefixed C++ names (`AActor`): not now.
- Sizes and load time for Chivalry 2 (tens of thousands of classes): measured in ypy.4.
