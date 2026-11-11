# From UE4SS Lua to Python

For modders who know the [UE4SS Lua API](https://docs.ue4ss.com/dev/lua-api.html). The left column is the Lua call, the
right column the counterpart in this mod. Every Python name links to its section in [API.md](API.md).

## Finding objects

| Lua | Python | Notes |
|---|---|---|
| `FindFirstOf("Actor")` | `ue4ss.find_first_of("Actor")` | Returns `None` when nothing is found; there is no invalid placeholder object. [Finding objects](API.md#finding-objects) |
| `FindAllOf("Actor")` | `ue4ss.find_all_of("Actor")` | Always a list (empty when nothing is found), default objects left out |
| `StaticFindObject("/Script/Engine.Actor")` | `ue4ss.static_find_object("/Script/Engine.Actor")` | |
| `FindObjects(n, class, name, required, banned)` | `ue4ss.find_objects(class_name=..., name=..., required_flags=..., banned_flags=..., limit=n)` | Keyword arguments |

## Objects and properties

| Lua | Python | Notes |
|---|---|---|
| `obj:IsValid()` | `obj.is_valid()` | A result is `None` or a real object, so `if obj is not None` is the usual first check. [Objects](API.md#objects) |
| `obj:GetFullName()` | `obj.get_full_name()` | |
| `obj:GetFName():ToString()` | `obj.get_fname().to_string()` | |
| `obj:GetClass()` | `obj.get_class()` | |
| `obj:IsA(class)` | `obj.is_a(class_or_path)` | |
| `obj.Health` | `obj.Health` | [Properties](API.md#properties) |
| `obj.Health = 5` | `obj.Health = 5` | Range and type are checked; a wrong value raises instead of being written |
| `obj:GetPropertyValue("Name")` | `obj.get_property_value("Name")` | For names that clash with a method |
| `arr:GetArrayNum()`, `arr[1]` | `len(arr)`, `arr[0]` | Python indexes from 0. [Arrays](API.md#arrays) |
| `arr:ForEach(function(i, elem) ... end)` | `for elem in arr:` | |
| `FName("Text")` | `ue4ss.FName("Text")` or a plain `str` | A `str` is accepted wherever a name is written. [Names](API.md#names) |

## Calling functions

| Lua | Python | Notes |
|---|---|---|
| `obj:SetActorHiddenInGame(true)` | `obj.SetActorHiddenInGame(True)` | [Calling functions](API.md#calling-functions) |
| struct argument as a table `{X=1, Y=2, Z=3}` | a dict `{"X": 1, "Y": 2, "Z": 3}` | |
| out parameters through a table argument | returned: `result, out1, out2 = obj.Func(...)` | Outputs are not passed; they come back as a tuple |

## Hooks and events

| Lua | Python | Notes |
|---|---|---|
| `local pre, post = RegisterHook(path, preFn, postFn)` | `ids = ue4ss.register_hook(path, pre, post)` | [Hooks](API.md#hooks) |
| `UnregisterHook(path, pre, post)` | `ue4ss.unregister_hook(ids)` | |
| `function(Context, Param1, Param2)` with `Param1:get()` | `def pre(context, params):` with `params.Name` | Parameters are fields of one object, by name, already unwrapped |
| `Context:get()` | `context` | Already the object |
| setting a return value in a post hook | `return value` from `post(context, params, result)` | |
| (no direct counterpart) | `ue4ss.register_replacement(path, fn)` | Run Python instead of the function. [Replacing a function](API.md#replacing-a-function) |
| (no direct counterpart) | `ue4ss.bind_event(obj, "OnSomething", fn)` | Bind to an Event Dispatcher of one object. [Binding](API.md#binding-to-a-multicast-delegate-event-dispatcher) |
| `NotifyOnNewObject(classPath, fn)` | `ue4ss.notify_on_new_object(class_path, fn)` | Delivered at a later tick, when the object is fully built. [New objects](API.md#new-objects) |

## Keys, threads, timing

| Lua | Python | Notes |
|---|---|---|
| `RegisterKeyBind(Key.F6, fn)` | `ue4ss.register_key_down(Key.F6, fn)` | The callback runs on the game thread. [Key bindings](API.md#key-bindings) |
| `RegisterKeyBind(Key.F6, {ModifierKey.CONTROL}, fn)` | `ue4ss.register_key_down(Key.F6, fn, modifiers=(ModifierKey.CONTROL,))` | |
| `ExecuteInGameThread(fn)` | `ue4ss.execute_in_game_thread(fn)` | [The game thread](API.md#the-game-thread) |
| `ExecuteWithDelay(ms, fn)` | `ue4ss.execute_with_delay(ms, fn)` | Runs on the game thread |
| `ExecuteAsync(fn)` | `threading.Thread(target=fn).start()` | An ordinary Python thread |
| `LoopAsync(ms, fn)` | a thread with a loop, or a function that calls `execute_with_delay` on itself | No built-in loop helper |
| `print("text")` | `print("text")` | Goes to `UE4SS.log` as `[Python] text` |
| Ctrl+R (reload all Lua mods) | `ue4ss.reload()`, usually bound to a key | [Reloading scripts](API.md#reloading-scripts) |

## Differences that matter

- **Wrong thread raises, it does not crash.** Lua lets a script touch objects from an async callback and the game may
  crash. Here every access from another thread raises `ue4ss.UEError`; move the work with `execute_in_game_thread` or
  `call_in_game_thread`. Background: [THREADING.md](THREADING.md).
- **Dead objects raise.** An object the game destroyed raises `ue4ss.InvalidObjectError` when used, also when its memory
  was reused by a new object. Background: [LIFETIME.md](LIFETIME.md).
- **Values are checked before the game is touched.** A number out of range, a wrong type or an unknown name is a Python
  exception with a message, not a silent wrong write.
- **Hook parameters die with the callback.** `params` is a view of the engine's memory for the duration of the call; keep
  a value with `params.copy()`.
- **One entry script.** The mod runs `python_scripts/main.py`; other files are imported from it
  ([USAGE.md](../USAGE.md#2-how-scripts-run)).
- **Types for the editor.** `ue4ss.engine` and generated typings give completion and checking for the game's classes,
  which Lua mods do not have ([TYPED_API.md](TYPED_API.md)).

The Lua column is written from the UE4SS documentation; check the linked Lua API pages for exact signatures.
