# Engine versions

Which Unreal Engine versions the mod supports, what that depends on, and how to add one.

## Status

| Engine | Status | Basis |
|---|---|---|
| 4.25 | **Supported** | Built and run; the in-game tests pass in the test game ([COMPATIBILITY.md](COMPATIBILITY.md)) |
| 4.26, 4.27 | **Expected to work, not verified** | Same object model as 4.25 (see below); nobody has built a test game or run the tests on them |
| 5.x | **Unknown** | UE4SS supports it, but the engine changed in ways the mod has not been checked against (below) |
| 4.24 and older | **Unknown** | Older property system; engine 4.21 and older also needs the `LessEqual421` build configuration ([BUILDING.md](BUILDING.md#build-configuration)) |

"Supported" means: the in-game test scripts were run on that engine and pass. Nothing else earns the word.

## Why the engine version matters less than the UE4SS version

The mod's C++ code contains **no engine-version checks of its own** and defines no engine memory layouts. What it knows
about objects, properties, functions and the script VM comes through UE4SS's Unreal wrapper, and UE4SS carries the
per-version layouts (member offsets, virtual tables, signatures). So:

- **One `main.dll` serves every engine version the UE4SS build serves.** The engine version is chosen at run time by
  UE4SS (it detects it, or takes `[EngineVersionOverride]` from `UE4SS-settings.ini`), not when the mod is compiled.
- **The binding that is compiled in is the UE4SS commit and build configuration**, not the engine:
  [README, Using another UE4SS version](../README.md#using-another-ue4ss-version).
- **Whether a game works is decided first by UE4SS:** if UE4SS finds its signatures and objects in the game, most of the
  mod works; if it does not, nothing does. See UE4SS's
  [fixing compatibility problems](https://docs.ue4ss.com/dev/guides/fixing-compatibility-problems.html).

## What in this repository is tied to an engine version

| Place | Tied to | Effect on another version |
|---|---|---|
| [`python/lib/ue4ss/bytecode.py`](../python/lib/ue4ss/bytecode.py) | The script token table of 4.25 | 4.26 and 4.27 are believed to use the same tokens (not checked against their sources). UE 5 added and changed tokens: `ue4ss.bytecode` would stop with `BytecodeError` on functions that use them (it never guesses). Only `ue4ss.bytecode` is affected, not hooks or calls. |
| [`testgame/PyTestGame`](../testgame/PyTestGame) | A 4.25 project; compiler pinned to Visual Studio 2019 in the `.Target.cs` files | Must be built with the other engine to test on it (below) |
| [`testgame/ue4ss/UE4SS-settings.ini`](../testgame/ue4ss/UE4SS-settings.ini) | `[EngineVersionOverride]` 4.25 | Must name the other version |
| [`tools/testgame.py`](../tools/testgame.py), [`tools/package_testgame.ps1`](../tools/package_testgame.ps1) | The staging folder name `WindowsNoEditor` | UE 5 stages into `Windows` |
| Design records and measurements | Taken on 4.25 | Facts such as which thread calls what may differ |

Known engine changes the mod has not been checked against:

- **UE 5:** vectors and rotators hold doubles (the mod reads field types from reflection, so this should follow, but it
  is untested); object references in properties became `TObjectPtr`; new script tokens (above).
- **4.24 and older:** properties are `UProperty` objects, not `FProperty` fields. UE4SS is meant to present both through one
  interface, but no part of the mod has been run there.

## Adding a version: what it takes

For 4.26 or 4.27 the expectation is **no change to `src/`**; the work is building a test game on that engine and running
the tests. Needed: that engine installed, and ideally one real game on it.

1. Build UE4SS and the mod as usual ([README](../README.md#build-and-install-from-source)); nothing changes here.
2. Stage the test game with the other engine: `tools\package_testgame.ps1 -Engine <that engine's folder>`. Check the
   compiler pin in `testgame/PyTestGame/Source/*.Target.cs` against what that engine requires.
3. Set `[EngineVersionOverride]` in `testgame/ue4ss/UE4SS-settings.ini` to the version.
4. `python tools\testgame.py install`, then `python tools\testgame.py all`.
5. Look at every failure. Typical causes, in order of likelihood: UE4SS itself lacks a layout or signature for that
   version (fix or report upstream); a test assumes a 4.25 detail (class or function that moved); the mod relies on
   behaviour that changed.
6. Run a real game of that engine version with a few of the [example scripts](../examples/README.md).
7. Record the result: a row in [COMPATIBILITY.md](COMPATIBILITY.md) and the status table above.

One staged test game exists per checkout, so testing two engines side by side currently means two checkouts or
re-staging; making the tools take the engine version as a parameter is the natural next step when a second version is
really added.

For UE 5 expect real work: the bytecode table, the staging folder name, and whatever the tests reveal.
