# Hooking functions defined in Blueprint assets (survey for rqf.1)

Measured on 2026-10-23 in Chivalry 2 with `python/examples/bp_hook_survey_main.py`: it loads the Blueprint class
`/Game/Interactables/World/Seatable/LevelActor_Chair.LevelActor_Chair_C` (`engine.find`, loaded on demand), hooks every function the class has
(137, own and inherited, `register_hook` with a pre callback), spawns one chair in the front-end map, destroys it after 2.5 s and counts the calls.

## What a Blueprint class looks like to reflection
- Class chain: `LevelActor_Chair_C` -> `LevelActor_BaseSeatable_C` -> `LevelActor_WorldBase_NoStats_C` -> `TBLActor` (native) -> `Actor` -> `Object`.
  The chair itself declares nothing; the work is in its Blueprint parents.
- Functions declared by the Blueprint classes (flags are the engine's `EFunctionFlags`):

| Function | Flags | Meaning |
|---|---|---|
| `LevelActor_BaseSeatable_C::ReceiveBeginPlay` | `0x8080800` Event, Protected, BlueprintEvent | the BeginPlay override stub |
| `LevelActor_BaseSeatable_C::ExecuteUbergraph_LevelActor_BaseSeatable(EntryPoint: int)` | `0x8001` Final, Ubergraph | the event graph |
| `LevelActor_WorldBase_NoStats_C::OnUsed()` | `0xc004040` Net, NetMulticast, BlueprintCallable, BlueprintEvent | a replicated custom event |
| `LevelActor_WorldBase_NoStats_C::BndEvt__Interactable_..._InteractableComponentOnUseSignature__DelegateSignature(Pawn, Interactable)` | `0x8000000` BlueprintEvent | the handler of the interactable component's "used" delegate |
| `LevelActor_WorldBase_NoStats_C::ExecuteUbergraph_LevelActor_WorldBase_NoStats(EntryPoint: int)` | `0x8001` Final, Ubergraph | its event graph |

- Variables declared by the Blueprint chain: `UberGraphFrame`, `SiegeMountPoint`, `Ak`, `TBLNavModifier`, `Interactable` (the interactable component), `StaticMesh_Door`,
  `DefaultSceneRoot`, `UseSound`.
- A cooked Blueprint puts the logic of all its events into ONE function, `ExecuteUbergraph_<Class>`, and each event is an entry point: the `EntryPoint`
  parameter says which event runs. The event functions themselves (`ReceiveBeginPlay`, custom events) are small stubs that call the ubergraph with their entry number.

## What `register_hook` saw while one chair was spawned and destroyed
- `LevelActor_BaseSeatable_C:ExecuteUbergraph_LevelActor_BaseSeatable`: 1 call. This is the BeginPlay event arriving, so **hooking the ubergraph function and reading
  `EntryPoint` is the way to observe Blueprint events** that reach ProcessEvent.
- `LevelActor_BaseSeatable_C:ReceiveBeginPlay` (the stub): 0 calls, although BeginPlay ran. The stub is not reported by a hook on the UFunction object.
- `OnUsed`, the bound-event handler and the `WorldBase` ubergraph: 0 calls (nobody used the chair; not tested).
- Native functions inherited from `Actor` fired thousands of times (`K2_SetActorRotation` 15215, `GetDistanceTo` 15215, `K2_GetActorLocation` 6086, ...): a hook
  on a function sees the calls of EVERY object, not only of the chair. Hooks need a way to filter by object (`context`), or a hook option for one instance.

## Consequences for the API (decisions for rqf.2 to rqf.5)
1. Hooks on Blueprint events should be registered on the event's UFunction and ALSO give access to the ubergraph entry: provide
   `register_hook(path_of_ubergraph_function, ...)` with `params.EntryPoint` and a helper that maps entry points to the stub names (rqf.4 reads the ubergraph's script to
   find which stub calls which entry point, or the stubs are hooked by their own call).
2. `register_hook` needs an instance filter (`register_hook(path, pre, post, instance=obj)` or filtering by the class of `context`) so a script can watch one actor.
3. Hooks on Blueprint classes must be registered before/without the class being loaded and survive reloads of the class (rqf.2).
4. Calls from inside a Blueprint graph to its own functions or to native functions are local script calls; this survey did not produce one that was visible.
   Whether the UE4SS process-local-script-function callbacks see them is the content of rqf.3.

Not yet measured: using the chair (OnUsed, the bound event), local script calls, parameters of ubergraph entry points, other Blueprints (weapons, game mode).


## Matrix: what can be hooked (measured in Chivalry 2, front-end map, 2026-10-23)
Probes: `bp_events_probe_main.py` (the seatable chair: hooks registered before the chair is spawned, then BeginPlay by the engine, then direct calls from Python),
`bp_functions_probe_main.py` (plain Blueprint functions on live instances), `bp_internal_calls_probe_main.py` (hundreds of plain functions, 3 seconds, no Python calls).

| Kind of Blueprint function | Hook registers | Fires | Parameters |
|---|---|---|---|
| Plain Blueprint function (BlueprintCallable, pure or not; 1376 in the 1256 loaded `/Game` classes) | yes | for calls from Python, from native code AND from other Blueprint code (local calls): 10 of 191 hooked functions fired on their own in 3 s, up to 19305 calls (`Frontend_C::GetTBLSpectatorPawn`) | readable; pre and post both fire |
| Event graph function `ExecuteUbergraph_<Class>` | yes | for every event of the class: BeginPlay arrived as `EntryPoint` 10 of `LevelActor_BaseSeatable_C`, a direct `OnUsed()` as `EntryPoint` 126 of `LevelActor_WorldBase_NoStats_C` | `EntryPoint` says which event |
| Event stub of an event graph custom event (`ReceiveBeginPlay`, `OnUsed` of the chair) | yes | was never reported before rqf.11 fixed the null-frame skip (those stubs have no parameters). Re-measured 2026-10-24 on the spawned chair (`bp_events_probe_main.py`): `ReceiveBeginPlay` of `LevelActor_BaseSeatable_C` pre and post 1/1 for the engine call, `OnUsed` 1/1 for the direct call, each next to its `ExecuteUbergraph_...` | none (`EntryPoint` only on the ubergraph) |
| Event stub of an event (`SetMapNameText(Text)`, `EnabledScreenInputBlocking(Enable)`, parameterless `FadeInMapTexture` of `DefaultLoadingScreenPanelWidget_C`, measured 2026-10-24 on a widget created from Python) | yes | pre and post fire for the stub, nested around `ExecuteUbergraph_<Class>`. A parameterless stub used to be missed: the VM runs a function without parameters and locals with a null frame and the hook wrapper skipped null frames (fixed in rqf.11: it hands out an empty block) | readable; a pre-hook can change bool, text and struct fields and the Blueprint acts on the new value (`bp_event_params_main.py`); object inputs are readable; replacements of events work too |
| Bound-event handler `BndEvt__...` | yes | not triggered yet (needs the interactable to be used) | not measured |
| Native base function (`Actor:ReceiveBeginPlay`) | yes | not reported for a Blueprint override | n/a |
| Array input of a Blueprint function (`BaseCharacter_C:SetGameTimeOnMeshComponents(MeshComponents)`, called by the game itself; Chivalry 2 2026-10-24) | yes | pre and post | the array is the caller's own (an in/out reference): a pre-hook can read it and remove elements, the Blueprint loop then runs only for the rest (2 instead of 6 calls of `SetScalarParameterValueOnMaterials`), and a post-hook can append them back (`bp_array_input_main.py`) |
| Struct input of a Blueprint function (`GamepadCalloutsEntry_C:SetColor(Color)`, 23 calls by the game) | yes | pre and post | fields are readable and writable; the changed colour reached `Image.SetColorAndOpacity` in all calls (`bp_struct_input_main.py`) |
| Output of a Blueprint function that another Blueprint function calls (`Frontend_C:DistanceScaling` calls `GetTBLSpectatorPawn`) | yes | post-hook and replacement of the callee | overriding the callee's object output with NULL, or replacing the callee, changes the caller's result (1.276 to 0.75), `bp_caller_override_main.py` |
| Local variables of a Blueprint function | not exposed | n/a | hooks see parameters, outputs and the result only; locals live in the frame, are not part of the function's parameter list and are not readable by name (decision of rqf.13: not supported) |
| `register_replacement` of a Blueprint function (`BP_Sky_Sphere:UpdateSunDirection`, `RefreshMaterial`; test game, 2026-10-24) | yes | runs INSTEAD of the function for Python calls and for calls made by other Blueprint code (the construction script calling `RefreshMaterial`); returning `CALL_ORIGINAL` runs it; calling the function inside the replacement runs the original | parameters and outputs as in hooks; the result is written to the VM's result slot (return value) or the single output parameter; `bp_sky_replacement_main.py` |
| Native function with out parameters called FROM Blueprint code (`KismetMathLibrary:BreakRotator` inside `BP_Sky_Sphere.UpdateSunDirection`, a CallMath instruction; test game, 2026-10-24) | yes | pre and post fire although the call does not go through ProcessEvent | inputs readable and writable in the pre-hook; the post-hook reads the real outputs; outputs written in the post-hook are used by the Blueprint (the sky's cloud colour changed), `bp_native_out_params_main.py` |

Not yet in the table: replicated events when used by a player, results of Blueprint functions.

## Gaps found
1. (Fixed in rqf.8.) Blueprint functions return through output pins, which are out parameters without a return flag, so a post-hook saw `result` None for `Frontend_C::IsAtTitleScreen` (its value was readable as `params.IsAtTitleScreen`). A function with a `ReturnValue` always worked. Now `result` is the only pure output when there is no return parameter, and a post-hook can replace it (`bp_results_main.py`); with two or more outputs use `params.<Name>`.
2. `Class.find_all()` for `BlueprintGeneratedClass` returns nothing (`find_all_of` gives 0 while `find_objects(class_name="BlueprintGeneratedClass", exact_class=True)` gives 1258): class objects are not found as instances; typed `find_all()` should use the second form for class-of-class types.
3. To hook one Blueprint EVENT by name, hook its `ExecuteUbergraph_<Class>` function and compare `EntryPoint` with the entry number that the event stub passes; the numbers come from the stub's bytecode (ticket rqf.7), or from one probe call as above.


## Reading what a Blueprint function calls (`ue4ss.bytecode`)

A Blueprint graph is compiled to bytecode. `UFunction.get_script()` returns it as `bytes` (empty for a native function) and `ue4ss.bytecode` walks it, read-only:

```python
from ue4ss import bytecode
func = ue4ss.static_find_object("/Engine/EngineSky/BP_Sky_Sphere.BP_Sky_Sphere_C").find_function("UserConstructionScript")
for call in bytecode.scan_calls(func):
    print(call["offset"], call["kind"], call["local"], call["target"])   # 0 final True /Script/Engine.Actor:K2_GetActorRotation ...
```

- `scan_calls(function)` lists the call instructions in stream order: `kind` is `final` (a function known at compile time, including Blueprint functions of the same class), `virtual` (called by name), `math` (a function of a function library) or `delegate`; `local` is True for the calls the compiler marks as staying inside Blueprint code; `target` is the called function's full path (or just its name for a virtual call) and `None` if it cannot be resolved.
- `parse(script)` returns the instruction tree (`Node`: `offset`, `name`, `values`, `children`) for your own analysis; `calls_in(nodes)` extracts the calls. Both raise `bytecode.BytecodeError` unless the stream is well formed and ends with `EndOfScript` on its last byte, so a layout the walker does not know is reported, never guessed.
- `ue4ss.object_at_address(address)` turns a pointer from the stream into the object; it never faults, whatever the number.
- Event graphs: the calls of `ExecuteUbergraph_<Class>` are all events of the class together; the entry points (`EntryPoint` of the hook) are offsets into the same stream.
- Names in the stream are 12 bytes in the builds measured here (a display index is kept); `parse_auto` detects 8 byte names too. The token layout is that of Unreal Engine 4.25.

Measured (Chivalry 2, front-end map, 2026-10-24): all 7051 functions of the 1491 loaded Blueprint classes parse to their `EndOfScript` with no failure, 36923 calls found (15029 final, 14355 math, 7294 virtual, 245 delegate), every target resolved. `ExecuteUbergraph_LevelActor_BaseSeatable` calls `SiegeMountPoint:SetInteractableComponent`. Example: `bytecode_main.py`.

## Binding a Python callback to a multicast delegate (event dispatcher) — `7q4.14`

Survey notes (design; implementation + in-game verification pending the game gate). The goal is the `Bind Event-to` / `BindDynamic` /
`AttachEventTo` counterpart: register a fresh Python function on a delegate *property*, not hook a function the delegate already invokes.

**Engine broadcast path** (confirmed from the P: engine source, `Engine/Source/Runtime/Core/Public/UObject/ScriptDelegates.h` ~230-248):
`FMulticastScriptDelegate::Broadcast` iterates the bound list; for each entry `TScriptDelegate::ProcessDelegate(params)` does
`ObjectPtr->FindFunctionChecked(FunctionName)` then `ObjectPtr->ProcessEvent(Function, params)`. So a bound entry is just `(UObject*, FName)`.

**Why binding to the signature function works:** a delegate property's parameter layout is its `__DelegateSignature` `UFunction`, and that
signature function *is* a member function of the owning class (the kismet catalog lists `NewEventDispatcher_0__DelegateSignature`,
`BndEvt__...__DelegateSignature` etc. as functions of their Blueprint classes; UEPseudo `UObject::GetFunctionByName(FName)` walks the class
field list and resolves it). So `{obj, SignatureFunction}` added to the `FMulticastScriptDelegate::InvocationList` makes a broadcast land in
`ProcessEvent(SignatureFunction, params)` on `obj` — exactly where the mod's hook/`ScopedParams` machinery reads params.

**UEPseudo API available** (`UEPseudo/include/Unreal/CoreUObject/UObject/UnrealType.hpp`): `FScriptDelegate = TScriptDelegate<>` with public
`Object`+`FunctionName` and `BindUFunction()`; `FMulticastScriptDelegate` with a public `InvocationList` (`TArray<TScriptDelegate>`) and
`ProcessMulticastDelegate` (copies the list before invoking); `FMulticastDelegateProperty::GetSignatureFunction()` (4.25 member-layout present).

**Plan (scope: multicast only; single-cast/sparse documented not-yet-supported):**
1. `bind_event(obj, name, callback)`: resolve the multicast delegate property on `obj` (category `MulticastDelegate`), get its in-memory
   `FMulticastScriptDelegate*` and `SignatureFunction` (`UFunction*`).
2. Append `FScriptDelegate{obj, SignatureFunction->GetFName()}` to `InvocationList` (so broadcast reaches it).
3. Hook the `SignatureFunction` (reuse `HookApi::register_hook`/`wrap` + `ScopedParams`), multiplexing per-instance by the broadcast's context
   object, and route to the Python callback with `(context, params)` on the game thread.
4. `unbind_event(id)`: remove the `FScriptDelegate` entry and drop the hook once no binding remains for that `SignatureFunction`.
- Both entry points require the game thread; delegates broadcast on the game thread. Follow the public-API checklist (core logic + host test,
  src impl, mock mirror, `docs/API.md`, thread-guard entry, PASS/FAIL example + manifest floor).

**Measured (2026-11-01, Chivalry 2 UE4SS 3.0.1): the global ProcessEvent / ProcessLocalScriptFunction pre-callback
dispatch works in this game.** An earlier probe revision slept on the game thread (`main.py` runs as a game-thread task)
and froze the game for its whole 120 s measurement window, which produced false 0-dispatch results (our own
`SlowCallback` machinery flagged it: "slow callback: game-thread task took 120050 ms"). With the probe rewritten to bind +
arm and return, doing its checks in delayed tasks (`ue4ss.execute_with_delay`), a live `TDM_Wardenglade` session showed
`process_events_seen = 14.3M`, `script_calls_seen = 2.4M` (~34 ns/event, bound-signature set lookup included - the
"how much do our hooks slow the game" answer: negligible). UnchainedPlugin's ProcessEvent `retour` hook was ruled out by
a control rebuild (it needs `--desync-patch`, never passed, and `retour.initialize()` alone does not rewrite the target).

**Still open for bind_event (`7q4.14`): delivery on a broadcast of the bound signature.** The channels, the
registration, and the InvocationList injection are proven; a wildcard capture (`_selftest_delegate_broadcast_diag`)
confirms delegate broadcasts dispatch and are capturable - but every observed name is a BP-bound handler
(`..._C:BndEvt__...__DelegateSignature`); a native C++ multicast delegate's `__DelegateSignature` injected into the
InvocationList never dispatched (in both games). Two candidate causes: the broadcast never ran (an authority check, e.g.
`SetCinematicMode`'s `if (!HasAuthority()) return;` in a client session), or a native delegate's signature UFunction is
not a callable member. Tracked as `bxc`; a BP Event Dispatcher (whose `__DelegateSignature` is a class
member per the kismet catalog) or a listen-server session would distinguish the two. All experiment changes were
reverted (plugin source + owner settings restored).

**Discovery pass (2026-11-01, Chivalry 2 Frontend, non-blocking):** the global
ProcessEvent/local-script collector saw 204 unique function names, 3,172,671
ProcessEvent calls and 2,680,557 local-script calls in about 20 seconds. The
Frontend inventory contained no `MatchStateChanged`, `OnMatchStateChanged` or
`__DelegateSignature` function name. This is an inventory fact, not evidence
that delegate dispatch is broken. The kismet catalog identifies
`StagedGameModeGameState.Current Stage Changed` as a Blueprint multicast
property whose `OnRep_Stage Actor` function broadcasts its
`Current Stage Changed__DelegateSignature`; that dispatcher must be tested on a
map/session that instantiates the staged game state, not on the Frontend or a
TDM map.

**Dynamic delegate delivery verified (2026-11-01, Wardenglade):** the greedy
probe discovered `TDMStage_C.TicketsComponent`, bound its `On Tickets Changed`
property, and received 16 Python callbacks. The callbacks carried valid
`Delta`, `Faction` and `New Tickets` values, including repeated `Delta=-1`
updates. The same session captured both
`TicketsComponent_C:On Tickets Changed__DelegateSignature` and the TDMStage
bound-handler signature through the ProcessEvent/local-script channels. This
proves `bind_event` delivery in Chivalry; `TBLGameState.OnMatchStateChanged`
still needs a real match-state transition for its specific callback to be
verified. An initial version delivered each broadcast through both global hook
channels; routing Blueprint signatures through the local-script channel only
fixed the duplicate callback and was verified by the seven one-for-one control
callbacks in the final pass.

**Match-state boundary measured (2026-11-01, Wardenglade):** the greedy probe
observed the live `TBLGameState.MatchState` property change from
`WaitingToStart` to `InProgress`. During the same window,
`K2_OnSetMatchState`, `UTBLMatchesSubsystem.OnMatchStateChanged`, and
`TBLGameState.OnMatchStateChanged` all produced zero callbacks, while the
TicketsComponent control delivered seven callbacks. The generic binding path
is therefore healthy; the TBL match-state delegate is not proven to be part of
the engine `AGameMode::SetMatchState`/`AGameState::SetMatchState` path and needs
its own broadcast/consumer-source investigation.

**Engine-owned control selected for the broad audit (2026-11-01):** UE 4.25's
`ACharacter::MovementModeChangedDelegate` is a regular
`UPROPERTY(BlueprintAssignable)` dynamic multicast delegate declared in
`Engine/Source/Runtime/Engine/Classes/GameFramework/Character.h`. The engine
broadcasts it from `ACharacter::OnMovementModeChanged` in
`Engine/Source/Runtime/Engine/Private/Character.cpp`. The next greedy probe
binds this delegate on every live character exposing the property, alongside
the Blueprint TicketsComponent control and the Chivalry match-state delegate,
to separate generic engine behavior from TBL-specific behavior.

**Target clarification (2026-11-01):** the supplied map Blueprint graph uses a
`K2Node_Event` with `bOverrideFunction=True` for
`/Script/TBL.TBLLevelScriptActor:OnMatchStateChanged(FName NewState)`. This is a
level-script actor override, not the `TBLGameState.OnMatchStateChanged`
multicast property. The correct Python observation path for this graph is
`register_hook` on the live level-script actor with an instance filter; it is
not `bind_event`.

**Client-cooked level-script event verified (2026-11-01, Courtyard):** hooking
the generated class function
`/Game/Maps/Courtyard/LTS_Courtyard.LTS_Courtyard_C:OnMatchStateChanged` delivered
`PreparingMatch`, `FirstRoundStartCountdown`, `InProgress`,
`WaitingBetweenRounds`, `RoundStartCountdown`, and `InProgress`. The matching
`LTSGameState.MatchState` property changes were observed in parallel. This is
the correct runtime path for the supplied Blueprint graph: `register_hook` on
the generated level-script UFunction, not `bind_event` on the game-state
delegate property.
