# Python API (`import ue4ss`)

Python scripts run inside the game (Unreal Engine 4.25, through UE4SS; other versions: `docs/ENGINE_VERSIONS.md`) and can find objects, read and write their
properties, call engine functions and hook them. This is the user-facing reference; `IMPLEMENTATION.md` has the design
and `docs/THREADING.md` / `docs/LIFETIME.md` the rules behind the safety behaviour described below.

Everything here was run against a real 4.25 game. Things that do not
exist yet are listed under [Not available yet](#not-available-yet).

## Running scripts

* `Mods/<Name>/python_scripts/main.py` runs once, on the **game thread**, from the first engine tick (about 5 seconds
  after UE4SS reports Unreal as initialised). `print()` and tracebacks go to `UE4SS.log` as `[Python] ...`.
* The folder must not be called `scripts`: UE4SS would treat the mod as a Lua mod.
* Off-game, `PYTHONPATH=python/mock` gives a pure-Python `ue4ss` with the same API and a fake object model, so scripts
  and tools can be tested without the game (`python/tests` shows how).

## The game thread

Unreal objects may only be touched on the game thread. Every call that touches one raises `UEError` from any other
thread (`is_valid()` and `repr()` never raise). To get there from another thread:

* `execute_in_game_thread(fn)`: run `fn()` on the game thread at a later tick. Never blocks.
* `execute_with_delay(milliseconds, fn)`: the same, after at least that delay (accurate to one frame).
* `call_in_game_thread(fn, *args, **kwargs)`: returns a `concurrent.futures.Future` for the result.
* `is_game_thread()`: where am I?

Tasks run in order; one that raises is logged with its traceback and does not stop the rest. `sys.exit()` in a callback
is ignored. Python threads you start yourself run normally while the game runs.

## Finding objects

* `static_find_object(path)`: the object at a full path such as `/Script/Engine.Actor`, or `None`.
* `find_first_of(class_name)`: the first live instance of the class or a subclass, or `None` (the same rule for class-like types).
* `object_at_address(address)`: the live object at an address (for example a pointer read from Blueprint bytecode, see `ue4ss.bytecode`), or `None`. Never faults; any int is safe.
* `find_all_of(class_name)`: every live instance of the class or a subclass, as a list. Default objects and archetypes
  are not instances and are left out; so are objects that are pending kill. Classes, functions, enums, structs and
  packages are only returned when the class you ask for is such a type: `find_all_of("BlueprintGeneratedClass")` lists the
  Blueprint classes, `find_all_of("Class")` all classes, `find_all_of("Actor")` no classes.
* `find_objects(class_name=None, name=None, required_flags=0, banned_flags=0, exact_class=False, limit=0)`: objects
  matching the class (and subclasses unless `exact_class`), the object name, or both. Unlike `find_all_of` it includes
  default objects and archetypes unless `banned_flags` has `ObjectFlags.RF_ClassDefaultObject` / `RF_ArchetypeObject`.
  `limit=0` means no limit.
* `ObjectFlags`: an `IntFlag` of the common engine object flags.

An unknown class or object name gives `None` / `[]` and never adds a name to the engine's name table.

## Names that are not Python identifiers

Blueprint variables, functions and parameters may contain spaces (`Sun height`, `Get Total Duration Seconds`). `getattr(obj, "Sun height")` always works.
The generated typings declare such a name under its **identifier form** (`Sun_height`): every character that is not a letter, digit or `_` becomes `_`, a leading
digit gets a `_` in front, a Python keyword gets a `_` behind. The runtime accepts that form too, for properties (`obj.Sun_height`, also when writing), functions
(`obj.Get_Total_Duration_Seconds()`) and keyword arguments of calls, whenever no member has exactly that name and exactly one member has that identifier form.
When two members share a form (`Mix up` and `Mix_up`) the exact name wins; a form that several different names share stays unresolved (`AttributeError`) and the
stub lists those members in a comment instead of declaring them: use `getattr`.


## Objects

`UObject` holds a *checked handle*, not a pointer. Once the object is destroyed, using it raises `InvalidObjectError`;
`is_valid()` tells you. Wrappers compare and hash by identity of the object (a destroyed object never equals a new one that
reuses its slot). You cannot construct one; you get them from the functions above, from properties and from calls.

Methods: `get_full_name()`, `get_fname()`, `get_class()`, `get_outer()`, `get_address()`, `is_valid()`,
`is_a(class_or_path)`, `get_property_names()`, `get_property_value(name)`, `set_property_value(name, value)`,
`call_function(function, *args, **kwargs)`.

The most specific type is chosen when an object is wrapped:

| type | for | extra methods |
|---|---|---|
| `UStruct` | classes, struct types and functions (base) | `get_super()`, `get_property_names()` (the fields instances have, parents included), `get_properties(inherited=True)` (the same with the type of each: see below), `get_function_names()`, `find_function(name)` |
| `UClass` | classes | `get_cdo()`, `is_child_of(class_or_path)` |
| `UScriptStruct` | struct types | (none) |
| `UFunction` | functions | `get_function_flags()`, `get_params()`, `get_script()`, `make_param_block(*args, **kwargs)` |
| `UEnum` | enums | `get_names()`, `get_name_by_value(value)`, `get_value_by_name(name)` |

`get_properties()` (on classes and struct types) and `UFunction.get_params()` describe types as dicts: `type` (the engine property class,
for example `FloatProperty`, `ObjectProperty`, `ArrayProperty`), `class` (the class of an object, weak, lazy, soft or interface reference, or the meta class
of a class reference; a path or `None`), `struct`, `enum` (paths or `None`), `array_dim` (more than 1 for a fixed-size array) and `inner` (a list of
descriptors: the element of an array or set, key and value of a map). Properties also have `name`, `declared_in` (the class or struct that declares it)
and `flags` (the engine's property flags); parameters have `name`, `is_out`, `is_return`, `is_const`, `is_reference`. The typed API (`docs/TYPED_API.md`) builds
its stubs from these.

Names are matched case-insensitively, like the engine does. Looking a name up never creates it.

## Properties

`obj.Name` reads and `obj.Name = value` writes a reflected property (`get_property_value` / `set_property_value` do the
same by name, for names that clash with a method). A name that is not a property is looked up as a function, then raises
`AttributeError`.

| engine type | Python | writing |
|---|---|---|
| bool (including bit-fields) | `bool` | `True`, `False`, `0`, `1`; any other int is `ValueError` |
| 8 to 64-bit signed / unsigned integers | `int` | an `int`; out of range is `OverflowError`; a float is `TypeError` |
| enums (`FEnumProperty`, enum bytes) | a member of an `IntEnum` named like the engine enum, one class per enum; a value the enum has no name for stays a plain `int` | a member or an `int`, range-checked like integers |
| float, double | `float` | an `int` or `float`; out of range for a 32-bit float is `OverflowError` |
| `FName` | `FName` | an `FName` or a `str` (a new name is added to the name table) |
| `FString` | `str` | a `str` |
| object and class references | `UObject` or `None` | a `UObject` of the right class, or `None`; a wrong class is `UEError` |
| structs | a `Struct` view | a `Struct` of the same type (copied in) or a `dict` of fields (only those are written) |
| `FText` | `str` (the display string) | a `str`; creates plain, non-localized text (the localization key and namespace of the old value are lost) |
| soft object / soft class references | `str` (the path, `""` when null) | a path `str`, or `None` to clear |
| weak object references | `UObject` or `None` (null once the object is gone) | like object references |
| lazy object references (`TLazyObjectPtr`) | `UObject` or `None` | like object references (see the note below) |
| interface references | `UObject` or `None` (the object behind the interface) | not possible: `UEError` |
| single-cast delegates | `None` or an `(object, FName)` tuple naming the bound function | not possible: `UEError` |
| multicast delegates (Event Dispatchers) | a list of `(object, FName)` tuples | bind a callback with `bind_event`; direct assignment not possible: `UEError` |
| `TArray` of any of the above | an `Array` view | list-like (see [Arrays](#arrays)) |
| fixed-size arrays (`int Foo[4]`) | a `FixedArray` view | by index, or an iterable of exactly the same length (see [Fixed-size arrays](#fixed-size-arrays)) |
| `TMap`, `TSet` | a `Map` / `Set` view | dict-like / set-like (see [Maps and sets](#maps-and-sets)) |

Lazy object references read through their in-memory pointer, which is valid for any object that is loaded and was assigned or
resolved by the game; a lazy pointer that still holds only a GUID reads as `None` (the GUID table cannot be reached from here).
Writing sets the in-memory pointer and clears the GUID, so a script-written lazy reference is not saved or replicated by GUID.

Anything else (sparse delegates) raises `UEError` naming
the type. A reference to an object that is pending kill reads as `None`.

## Arrays

`actor.Scores` for a `TArray` property is an `Array`: a live view that looks the array up on every access, so it is always
current and cannot dangle when the game reallocates the array.

```python
scores = actor.Scores
len(scores); scores[0]; scores[-1]; scores[1:]; list(scores); 7 in scores
scores[2] = 99            # one element, converted strictly like a property
scores.append(5); scores.extend([1, 2]); scores.insert(0, 7); scores.pop(); del scores[1:3]; scores.clear()
actor.Scores = [1, 2, 3]  # replaces the whole array
scores.to_list()          # a plain list copy
```

* Elements may be anything a property can be, except nested containers: numbers, `bool`, `FName`, `str`, objects and structs.
* A **struct element is an owned copy** (`is_owned()` is true): changing it does not change the array until you write it back
  with `arr[i] = copy` (or `append`/`insert` it). The struct must be of the element type.
* Resizing uses the engine's own allocator and constructs and destroys elements the way the engine does. Every new item is
  converted before the array changes, so a bad item (`TypeError`, `OverflowError`, ...) leaves the array as it was. Only slices
  with step 1 can be assigned or deleted (`ValueError` otherwise).
* An index out of range is `IndexError`. An array whose owner was destroyed raises `InvalidObjectError`; `is_valid()` tells.
* **Caution:** do not resize an array that the game is walking at that moment (for example from a hook on a function that
  iterates it); the engine does not protect against that.
* `Struct.to_dict()` turns array fields into lists.

## Fixed-size arrays

A property declared `int32 FixedInts[4]` has a fixed number of elements stored inline in the object. `actor.FixedInts` is a
`FixedArray`: a live view like `Array`, but the length never changes.

```python
fixed = actor.FixedInts
len(fixed); fixed[0]; fixed[-1]; fixed[1:3]; list(fixed); 7 in fixed
fixed[2] = 99                 # one element, converted strictly like a property
actor.FixedInts = [1, 2, 3, 4]  # exactly as many items as the array has (ValueError otherwise)
fixed.to_list()               # a plain list copy
```

* Elements are numbers, `bool`, `FName`, `str`, objects or structs. A **struct element is a view into the owner** (its memory
  never moves, unlike an `Array` element), so `fixed[0].X = 1.0` changes the array in place.
* No `append`, `insert`, `pop`, `del` or slice assignment (`TypeError`); an index out of range is `IndexError`.
* Whole assignment converts every item first, then writes them one by one.
* `Struct.to_dict()` turns fixed-size array fields into lists.

## Engine classes as Python types

`from ue4ss.engine import Actor, GameplayStatics, Vector` gives engine classes, structs and enums as Python types, created from the
running game's reflection when first used (`isinstance(obj, Pawn)`, `GameplayStatics.GetPlayerController(world, 0)`, `Actor.find_all()`,
`Vector(X=1, Y=2, Z=3)`), and `engine.generate_stubs(folder)` writes editor typings for the whole game. See `docs/TYPED_API.md`.

Where a function wants an engine class object (a `TSubclassOf` or `UClass` argument, such as the class of `GameplayStatics.BeginDeferredActorSpawnFromClass`), the type itself is accepted:
`BeginDeferredActorSpawnFromClass(pawn, Actor, transform, ...)` means `Actor.static_class()`, and so does the result of `engine.find("/Game/.../Chair.Chair_C")`.
A type that is not an engine class is a `TypeError` as before.

## Maps and sets

`TMap` and `TSet` properties are live views like arrays: every access looks the container up again.

```python
ammo = actor.Ammo                       # TMap<FName, int32>
ammo["Arrow"]; ammo.get("Bolt", 0); "Arrow" in ammo; len(ammo)
ammo["Stone"] = 3; ammo.update({"A": 1}); del ammo["Stone"]; ammo.pop("A", None); ammo.clear()
list(ammo); ammo.keys(); ammo.values(); ammo.items(); ammo.to_dict()
actor.Ammo = {"Arrow": 10}              # replaces the whole map

ids = actor.Ids                         # TSet<int32>
5 in ids; ids.add(5); ids.discard(5); ids.remove(5); ids.update([1, 2]); ids.clear()
ids.to_list(); ids.to_set()
```

* Keys and set elements may be numbers, `bool`, `FName`, `str` and objects, converted strictly like properties. Map values
  may also be structs (owned copies, like array elements: write one back with `m[key] = copy`).
* **Struct keys and struct set elements** can be listed but not looked up, added or removed (`UEError`): the engine's hash
  of a struct is not reliable from here.
* Order is the engine's storage order, not insertion order. `keys()`, `values()`, `items()` and iteration are snapshots.
* The engine hashes and compares keys itself (`FName` keys compare case-insensitively, as in the engine). A missing key is
  `KeyError`; `remove` on a missing set element too, `discard` does not.
* Every mutation converts all new keys and values first, so a bad one leaves the container unchanged. Assigning a whole
  container (`actor.Ammo = {...}`) replaces its contents.
* Do not change a map or set that the game is iterating at that moment.
* `Struct.to_dict()` turns maps into dicts (struct values as dicts) and sets into lists.

## Structs

A `Struct` reads and writes fields like an object and nests: `actor.ReplicatedMovement.LinearVelocity.X = 1.0`. It comes in
three kinds, told apart by `is_owned()` and `repr()`:

* **view** into an object: reads and writes the game's memory directly and is valid while the owner object is. Use
  `copy()` to keep the value.
* **owned**: has its own memory and outlives everything. Create one with `make_struct(type, **fields)` (`type` is a
  `UScriptStruct` or a path such as `/Script/CoreUObject.Vector`) or with `copy()`.
* **hook parameters**: a view of the engine's parameters, valid only inside the hook callback (see below).

Methods: `copy()`, `is_owned()`, `is_valid()`, `get_struct_name()`, `get_property_names()`, `get_property_value(name)`,
`set_property_value(name, value)`, `to_dict()` (every supported field, nested structs as dicts). Two views are equal when they
refer to the same place; compare contents with `to_dict()`.

## Calling functions

```python
math = ue4ss.static_find_object("/Script/Engine.KismetMathLibrary")
math.Add_IntInt(2, 3)                       # 5: a static library function, through its class
math.BreakVector({"X": 1, "Y": 2, "Z": 3})  # (1.0, 2.0, 3.0): outputs come back as a tuple
actor.K2_GetActorLocation()                  # an owned Struct
actor.SetActorHiddenInGame(True)             # None
```

* Arguments go by position or by name (case-insensitive). Outputs and the return value are not passed. Every argument is
  converted by the rules above *before* the game is touched.
* The result is `None`, the value, or a tuple of the return value followed by the outputs, followed by the updated value of
  every **in/out** parameter (a non-const by-reference parameter, `UPARAM(ref)` in C++). An in/out parameter is passed like any
  argument and its new value comes back as a result: `stream = lib.SetRandomStreamSeed(stream, 7)`. A struct you pass is copied
  into the call, so your own `Struct` is not changed in place; use the returned one. Const references are inputs only.
* Arrays, structs, maps and sets work as arguments (a list, a `Struct` or `dict`, a `dict`, a set or any iterable) and as
  results: an array result is an `Array` view of the call's own memory, usable and resizable after the call (and as an
  argument to the next call). `GameplayStatics.GetAllActorsOfClass(world, cls)` returns an `Array` of actors this way.
* An instance function needs an instance of its class; on a class object only static functions work (they run on the class
  default object).
* `obj.call_function(name_or_ufunction, ...)` is the explicit form; it works for names that clash with a property or method.
* `function.make_param_block(*args, **kwargs)` builds the parameter block without calling (an owned `Struct` whose fields
  are the parameters).
* `BoundFunction` is what `obj.Name` returns for a function; `is_valid()` reports whether the object and function are alive.

## Hooks

`register_hook(path, pre=None, post=None, instance=None)`: with `instance=obj` only calls on that object reach the callbacks (other objects cost nothing, which matters for functions every actor calls). A Blueprint function path such as `/Game/Interactables/World/Seatable/LevelActor_BaseSeatable.LevelActor_BaseSeatable_C:ExecuteUbergraph_LevelActor_BaseSeatable` loads its class first when the game has not loaded it (a short hitch). Blueprint events run through the single `ExecuteUbergraph_<Class>` function of their class with the event in the `EntryPoint` parameter: see `docs/BLUEPRINT_HOOKS.md`.

```python
def pre(context, params):          # before the function runs
    print(context, params.NewLocation.X)

def post(context, params, result):  # after it ran; result is the return value (None if it returns nothing)
    return result * 2               # optional: replaces the result

ids = ue4ss.register_hook("/Script/Engine.Actor:K2_SetActorLocation", pre, post)
ue4ss.unregister_hook(ids)
```

* `register_hook(path, pre=None, post=None)` returns `(pre_id, post_id)`. It works for native and Blueprint functions.
  `unregister_hook` takes that tuple or the two ids.
* `params` is a live view of the engine's parameter memory and **dies when the callback returns**; `params.copy()` keeps a value.
  A pre-hook can change inputs: `params.A = 5`.
* In a post-hook read the return value from `result`. `params.ReturnValue` is stale there.
  A function without a return value whose only output is a single plain out parameter (this is how **Blueprint functions**
  return: an output pin such as `IsAtTitleScreen`) has that output as `result` too, and a post-hook can replace it; with two or
  more outputs `result` is `None` and you read and write `params.<Name>`. Replacements (`register_replacement`) follow the same rule.
* A post-hook that **returns a value** replaces the result (strict conversions; a dict or `Struct` for struct results). Return
  `None`, or nothing, to keep it, and `ue4ss.NULL` to set a null object. A bad replacement is reported and ignored.
* Exceptions and `sys.exit()` inside hooks are logged and never reach the engine. Hooks nested more than 8 deep are skipped.
  A hooked function that happens to run off the game thread skips its Python hook (logged once).
* **Output parameters:** in a post-hook `params` addresses the caller's real output storage, so you read the values the
  function produced and can change them (`params.OutCount = 99`, `params.OutVector.X = 1.0`, `params.OutNumbers.append(7)`,
  `params.OutText = "x"`); the caller gets the changed values. Verified for calls routed through `ProcessEvent` (Python calls
  and engine C++ calls) and for a native function that Blueprint code calls (`KismetMathLibrary:BreakRotator` inside the engine's `BP_Sky_Sphere`: the
  Blueprint uses outputs written by a post-hook).
* Cancelling the original call is a separate feature, replacements (next section).

### Binding to a multicast delegate (Event Dispatcher)

```python
def on_opened(context, params):
    print("the door opened", context.get_full_name())

event_id = ue4ss.bind_event(door, "OnOpened", on_opened)
ue4ss.unbind_event(event_id)
```

* `bind_event(object, name, callback, async_=False)` binds a fresh Python callback to the multicast delegate (Event Dispatcher) property
  `name` on the object — the counterpart of the Blueprint **Bind Event to** node and the C++ `BindDynamic` /
  `AddDynamic` / `AttachEventTo`. `callback(context, params)` runs on the game thread, on the object's own thread of
  execution, each time the delegate broadcasts. `context` is the object whose delegate fired; `params` is the delegate's
  parameter block, laid out by its __DelegateSignature function, and is live only inside the callback (`params.copy()`
  keeps a value). Callbacks bound with `bind_event` never replace a broadcast's existing handlers; they run in addition.
* With `async_=True`, the game thread snapshots the parameter block and queues the callback on a worker. Scalar, string and
  owned struct values are readable from that snapshot; Unreal object wrappers remain game-thread-only and raise `UEError`
  when dereferenced from the worker. Async callbacks are ordered through one process worker and an unbound binding's queued
  callback is discarded.
* Returns the binding id; `unbind_event(id)` stops delivery for that callback. Several callbacks on the same delegate each
  run, once per broadcast.
* **Known limit: delegates declared in C++ are not delivered.** Binding works and callbacks arrive for event dispatchers
  declared in a Blueprint (verified in a game). For a multicast delegate declared in native code the binding is accepted
  but the callback never runs; the in-game test `bind_event_main.py` fails on this and is marked as an expected failure.
  To react to such a delegate, hook a function it calls. Background: `docs/BLUEPRINT_HOOKS.md`.
* First supported for **multicast** delegate properties only (the common Event Dispatcher). Single-cast and sparse delegates
  are not bindable yet; a single-cast binding would be a replace, not an append, and is future work. Reading a bound
  multicast delegate still lists its `(object, FName)` bindings as before.
* A delegate of the same type used by two multicast properties on the same object is treated as one event at the broadcast
  (the signature function is per delegate type). See `docs/BLUEPRINT_HOOKS.md`.

### Replacing a function

```python
def fake(context, params):
    if params.A < 0:
        return ue4ss.CALL_ORIGINAL      # let the real function run
    return 99                           # the result; the real function does not run

rid = ue4ss.register_replacement("/Script/Engine.KismetMathLibrary:Add_IntInt", fake)
ue4ss.unregister_replacement(rid)
```

* The callback runs **instead of** the function. It returns the function's result (`None` for none, `ue4ss.NULL` for a null
  object), or `ue4ss.CALL_ORIGINAL` to run the function normally. It can also set output parameters on `params` before
  returning.
* An exception in the callback, or a result that does not fit the return type, is reported and the original runs.
* Calling the replaced function from inside its own replacement runs the original, so a replacement can wrap it:
  `return 10 * context.Add_IntInt(params.A, params.B)`.
* **Reach:** a *native* function is replaced where it is called through the engine's `ProcessEvent`: calls made by C++ code, delegates,
  events, timers, and calls from Python. A Blueprint script calling a *native* function does not go through `ProcessEvent` and cannot
  be replaced (hooks see it, replacements do not). A *Blueprint* function (defined in an asset) is replaced where the script VM runs it,
  so calls made by other Blueprint code are seen as well as Python and C++ calls. Its result is the return value, or, as for hooks, its only
  output parameter. A call from a thread other than the game thread always runs the original.
* One replacement per function. Ordinary hooks do not run for a call that was replaced.

## New objects

```python
def spawned(obj):
    print("new", obj.get_full_name())

sub = ue4ss.notify_on_new_object("/Script/Engine.Actor", spawned)
ue4ss.unregister_notify_on_new_object(sub)
```

* `notify_on_new_object(class_path, callback)` returns an id. `callback(obj)` runs on the game thread for every new instance
  of the class or a subclass, default objects included. An unknown path, or one that is not a class, is `UEError`.
* The call comes **shortly after creation, at a later tick**, not from inside the engine's constructor, so the object is fully
  built. An object destroyed in between is not delivered. Objects created before you subscribed are not reported; use
  `find_all_of` for those.
* Exceptions in the callback are logged and do not stop later deliveries. A loose class such as `/Script/CoreUObject.Object`
  delivers every object the game creates, which can be a lot during level loads. The engine-side queue holds 1,000,000
  notices and drops the rest.
* `unregister_notify_on_new_object(id)` stops it, also for objects already queued.

## Key bindings

```python
from ue4ss import Key, ModifierKey

def toggle():
    print("F6 pressed")

binding = ue4ss.register_key_down(Key.F6, toggle)
ue4ss.register_key_down(Key.F6, lambda: print("Ctrl+F6"), modifiers=(ModifierKey.CONTROL,))
ue4ss.unregister_key_down(binding)
```

* `register_key_down(key, callback, modifiers=())` returns an id. `callback()` runs **on the game thread** each time the key goes
  down. `Key` is an `IntEnum` of the Windows virtual-key codes with the names UE4SS uses (`Key.F1`, `Key.A`, `Key.SPACE`,
  `Key.LEFT_MOUSE_BUTTON`, ...); a plain number works too. `Shift`, `Control` and `Alt` are not keys here: pass them as
  `modifiers=(ModifierKey.SHIFT, ...)`. A binding fires only when exactly those modifiers are held, so `F6` and `Ctrl+F6` are
  different bindings.
* Several callbacks may share a key; they run in registration order. `unregister_key_down(id)` removes one (`ValueError` for an
  unknown id). A callback may unregister others; one that was removed before its turn does not run.
* UE4SS reads keys only while the game window has the focus (it polls the keyboard state), so nothing fires for a headless game
  or one in the background.
* `register_key_down` touches no game objects and may be called from any thread. A callback that raises prints its traceback
  to the log; `sys.exit()` in it is ignored.
* A key press made BEFORE `register_key_down` does not trigger it: registering consumes any stale `GetAsyncKeyState`
  state for that key, so the first poll after the call only fires on a press made after it.
* UE4SS itself cannot remove a key event once registered, so removed bindings leave a native handler that does nothing.

## Reloading scripts

```python
ue4ss.register_key_down(ue4ss.Key.F5, ue4ss.reload)   # edit python_scripts, press F5
print("generation", ue4ss.script_generation())
```

`reload()` can be called from any thread (a key callback, a hook, a worker thread). It does not run at once: at the next game tick,
on the game thread and outside any callback, the module

1. unregisters every hook, replacement, key binding and new-object notification,
2. drops all delayed tasks (`execute_with_delay`) and the tasks queued after the `reload()` call (`execute_in_game_thread`; the
   queue is in order, so tasks queued before it still run first),
3. forgets the modules imported from the `python_scripts` folder (the next import reads the file again) and clears the names
   `main.py` defined,
4. runs `main.py` again.

`script_generation()` is 1 for the first run and grows by one with every reload. Several `reload()` calls before the tick make one
reload. If the new `main.py` raises, the traceback goes to the log; the old registrations are gone anyway. Two things survive a
reload: Python threads you started (they cannot be stopped from outside: check `script_generation()` in their loop and return when
it changed) and objects the old scripts still reference. The interpreter itself stays up (a CPython interpreter cannot be restarted
inside a process); anything an extension module keeps in C state also stays.

## Rotations

Quaternions are what the engine stores, but scripts can give Euler angles instead.

```python
# anywhere a quaternion field or parameter is expected (a transform's Rotation, FQuat properties and arguments):
ue4ss.make_transform(location=(300, 0, 100), rotation=(0, 90, 0), scale=(2, 2, 2))   # pitch, yaw, roll in degrees
transform = {"Rotation": {"Yaw": 90}, "Translation": {"X": 0, "Y": 0, "Z": 0}, "Scale3D": {"X": 1, "Y": 1, "Z": 1}}
actor.SomeQuatProperty = (10, 20, 30)                      # a 3-sequence is (pitch, yaw, roll)
q = ue4ss.euler_to_quat(10, 20, 30)                         # {"X": .., "Y": .., "Z": .., "W": ..}
pitch, yaw, roll = ue4ss.quat_to_euler(q)                   # and back (a Quat struct works too)
q = ue4ss.euler_to_quat(0.1, 0.2, 0.3, order="xyz", degrees=False)   # another order, radians
```

* The default order, `"unreal"`, is `FRotator`: the angles are **(pitch, yaw, roll) in degrees**, pitch about Y, yaw about Z, roll
  about X, applied as yaw, then pitch, then roll. This is exactly `FRotator::Quaternion()` and `FQuat::Rotator()`.
* `order` can also be the three axes in some order (`"xyz"`, `"xzy"`, `"yxz"`, `"yzx"`, `"zxy"`, `"zyx"`): three **intrinsic**
  rotations, the first about the object's own x axis by `a`, then about the new y axis by `b`, then about the new z axis by `c`
  (the angles in the order of the letters). The sign of an angle is that of `FQuat(Axis, Angle)`. `"unreal"` equals `"zyx"` with
  `(yaw, -pitch, -roll)`.
* `degrees=False` takes and returns radians. `quat_to_euler` returns the middle angle within +-90 degrees (pitch for `"unreal"`);
  at the poles (gimbal lock) the other two angles cannot be told apart and the engine's rule is used.
* A quaternion field accepts: a 3-sequence `(pitch, yaw, roll)`, a dict that has only `Pitch` / `Yaw` / `Roll` keys (any case,
  missing ones are 0), a `Rotator` struct, or the usual `X`/`Y`/`Z`/`W` dict or `Quat` struct. Mixing the two key sets is a
  `ValueError`. These conversions need no game thread; `make_transform` builds an engine struct and does.

## Slow callbacks

Python callbacks run on the game thread, so a slow one is a visible hitch. `set_slow_callback_warning(milliseconds)` sets how long
is too long (default 50; `0` switches the warnings off). A game-thread task (`execute_in_game_thread`, `execute_with_delay`,
`call_in_game_thread`), a hook, a replacement or a new-object callback that takes at least that long writes a line such as
`ue4ss: slow callback: pre-hook: AddInts took 62 ms on the game thread (limit 50 ms)` to the log. At most three lines are written
per callback; the last one says that further reports are muted.

## Names

`FName(text)` creates (or finds) a name and **adds it** to the engine's name table if it is new; `FName.find(text)` only looks
and returns `None`. `str(name)`, `name.to_string()`, `name.comparison_index`, `name.number` (0 for none, otherwise N + 1 for
`Name_N`). `FName == str` compares case-insensitively without adding names, but an `FName` does not hash like the equal `str`,
so do not mix them as dict keys.

## Errors

| exception | when |
|---|---|
| `UEError` | base class for the module's own errors, also for an engine-side refusal (wrong class, unsupported type, wrong thread) |
| `InvalidObjectError` | a `UObject`, `Struct` or hook parameter was used after it died |
| `AttributeError` | a name is neither a property nor a function |
| `TypeError`, `ValueError`, `OverflowError` | a value or argument could not be converted; call arguments use Python's messages |

`NullType` is the type of `NULL`, `CallOriginalType` the type of `CALL_ORIGINAL`. `__version__` is the module version.

## Internal

`_selftest_raise`, `_selftest_fstring`, `_selftest_objects`, `_selftest_thread_probe`, `_selftest_fire_key`, `_selftest_poison_pointer`, `_selftest_call_with_block`, `_selftest_faults_survived`, `_selftest_struct_stats`, `_selftest_event_profile`, `_selftest_manual_pe_detour`, `_selftest_manual_pe_detour_calls`, `_selftest_delegate_broadcast_diag`, `_selftest_delegate_broadcasts`, `_selftest_process_event_dump` and `_selftest_process_event_dump_read` exist for the in-game tests and the
startup self check; do not use them in scripts.

## Sharp edges

* **Writes go straight to memory.** No setters, no replication, no `PostEditChange`: the game does not know a property changed.
* **A fault inside a call is survived, best effort.** If the game itself faults while running a function you called (an access
  violation, a divide by zero), the call raises `UEError` describing the fault, instead of ending the process. The engine's
  frames are abandoned without cleanup, so the game may misbehave afterwards: treat it as a reason to stop and fix the call.
  Faults inside hook callbacks and anywhere outside `obj.Func(...)` are not caught. On Chivalry 2 the UnchainedPlugin seals a
  faulting *read* at the instruction (its VEH runs before every frame handler, the only handler that reaches these faults):
  the read is skipped, the call still raises `UEError`, and the game survives where frame SEH alone could not. Works only when
  the plugin is loaded; set `PYUE_SEH_READ_RECOVER=0` in the game's environment to switch the seal off. The mechanism is
  documented in `docs/FAULTS.md`.
* **A pointer the game left dangling reads as `None`.** Object references are checked against the global object array before they
  are used, so a stale or garbage pointer in a property, array element or map never crashes the script; it just looks null.
  On Chivalry 2 the UnchainedPlugin seals a faulting read inside this check too, so the stale pointer reads as
  `None` there as well.
* **Class objects have no instance properties.** `actor_class.bHidden` looks in the metaclass and raises; use
  `actor_class.get_property_names()` to see what instances have.
* **Owned structs** release themselves when Python drops them (on the game thread, queued there if needed). In the rare case
  that this happens before the game thread is known, one block is leaked.
* **Hooks and tasks that run too long freeze the game**; there is no time limit yet; `set_slow_callback_warning` reports slow ones.

## Not available yet

* Installing third-party packages (pip wheels); the standard library is complete.
* More than one entry script: `main.py` is the only file the mod runs by itself (it can import others).
