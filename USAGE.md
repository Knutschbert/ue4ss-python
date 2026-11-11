# Getting started

This walks a new user from an installed mod to working scripts. The full reference is `docs/API.md`.

## 1. Check the install

After installing (see `README.md`), the mod folder looks like this:

```
<Game>\Binaries\Win64\Mods\PythonScripting\
    enabled.txt
    dlls\               main.dll, python312.dll, python312.zip, ...
    python_scripts\
        main.py         your script
```

Put this in `python_scripts\main.py` and start the game:

```python
import ue4ss

print("hello from Python, generation", ue4ss.script_generation())
```

Open `UE4SS.log` (next to the game's executable) and search for `[Python]`. You should see the line. If not:

- No `PythonScripting` lines at all: the mod is not enabled. Check `Mods\mods.txt` for `PythonScripting : 1`.
- The game crashes at start: the installed `UE4SS.dll` is not the build this mod was made for (see README, Requirements).
- A traceback: Python errors are printed to the same log with the `[Python]` prefix.

The folder must be called `python_scripts`, not `scripts`: UE4SS treats a mod with a `scripts` folder as a Lua mod.

## 2. How scripts run

- **`main.py` is the entry point.** It runs once, on the game thread, at the first engine tick (a few seconds after
  UE4SS reports Unreal as initialised). There is no other file the mod starts by itself.
- **More files are supported as modules.** Anything else in `python_scripts` can be imported from `main.py`:

  ```
  python_scripts\
      main.py           import doors; from mygame import spawn
      doors.py
      mygame\
          __init__.py
          spawn.py
  ```

  The import path is, in this order: the bundled standard library, the mod's `dlls` folder, `python_scripts`. A file
  named like a standard-library module (`json.py`, `random.py`) is therefore never picked up; choose another name.
- **The whole standard library is available** (`json`, `threading`, `socket`, `sqlite3`, `ctypes`, ...). Installing
  third-party packages with pip is not supported yet.
- **`main.py` ends, your registrations stay.** Hooks, key bindings and delayed tasks keep working after the script
  finished. A script is usually: look things up, register callbacks, return.

## 3. Reload without restarting the game

Add one line and you can edit, save and press a key:

```python
import ue4ss
from ue4ss import Key

ue4ss.register_key_down(Key.F5, ue4ss.reload)
```

A reload removes every hook, key binding and pending task, forgets the modules imported from `python_scripts` (so
edited files are read again) and runs `main.py` again. Threads you started yourself are not stopped: let them check
`ue4ss.script_generation()` and return when it changed.

## 4. Find objects and read properties

```python
import ue4ss

controller = ue4ss.find_first_of("PlayerController")      # first live instance, or None
actors = ue4ss.find_all_of("Actor")                       # every live instance, as a list
library = ue4ss.static_find_object("/Script/Engine.KismetMathLibrary")   # by full path

if controller is not None:
    pawn = controller.Pawn                                # a property read; None when there is no pawn
    if pawn is not None:
        print(pawn.get_full_name(), pawn.K2_GetActorLocation())
```

- Class names are the engine's names without the C++ prefix: `Actor`, not `AActor`.
- `obj.Name` reads a reflected property, `obj.Name = value` writes it. Numbers, strings, enums, structs, arrays, maps
  and sets convert to Python values (table in `docs/API.md`, Properties).
- An object you hold can be destroyed by the game. Using it then raises `ue4ss.InvalidObjectError` instead of
  crashing; `obj.is_valid()` tells in advance.

## 5. Call functions

Any function the engine reflects (Blueprint-callable native functions and Blueprint functions) can be called:

```python
math = ue4ss.static_find_object("/Script/Engine.KismetMathLibrary")
print(math.Add_IntInt(2, 3))                   # 5: a static library function, through its class
x, y, z = math.BreakVector({"X": 1, "Y": 2, "Z": 3})   # outputs come back as a tuple

pawn.SetActorHiddenInGame(True)
```

Arguments go by position or by name. A struct argument can be a dict of its fields.

## 6. React to the game

Hook a function (native or Blueprint):

```python
def before(context, params):
    print(context.get_full_name(), "moves to", params.NewLocation.X)

ids = ue4ss.register_hook("/Script/Engine.Actor:K2_SetActorLocation", before)
# later: ue4ss.unregister_hook(ids)
```

`params` is only valid inside the callback; keep values with `params.copy()`.

Bind to an Event Dispatcher (multicast delegate) of one object:

```python
event_id = ue4ss.bind_event(door, "OnOpened", lambda context, params: print("opened"))
```

Be told about new objects, and bind keys:

```python
ue4ss.notify_on_new_object("/Script/Engine.Actor", lambda obj: print("new", obj.get_full_name()))
ue4ss.register_key_down(Key.F6, lambda: print("F6 pressed"))
```

## 7. The game thread

Game objects may only be touched on the game thread. `main.py`, hooks, key callbacks and delayed tasks already run
there. From a thread you started yourself, hand the work over:

```python
ue4ss.execute_in_game_thread(lambda: print(len(ue4ss.find_all_of("Actor"))))   # at a later tick, does not block
ue4ss.execute_with_delay(2000, lambda: print("two seconds later"))
future = ue4ss.call_in_game_thread(lambda: controller.get_full_name())          # a Future with the result
```

Touching an object from the wrong thread raises `ue4ss.UEError`; it does not crash. Keep callbacks short: they run
inside the game's frame.

## 8. Editor completion and type checking (optional)

The base `ue4ss` module is typed out of the box when you open this repository in VS Code (`python/typings`). For engine
and game classes, the game writes typings of all of its classes, and scripts can then use typed imports:

```python
from ue4ss import engine
engine.generate_stubs(r"C:\path\to\stubs")      # run once in the game, again after a game update
```

```python
from ue4ss.engine import PlayerController, Vector

controller = PlayerController.find_first()
```

`docs/VSCODE.md` shows the VS Code setup, `docs/TYPED_API.md` the details.

## 9. Try scripts without the game

`python/mock` in this repository is a pure-Python `ue4ss` with the same functions and a fake object model. It is meant
for unit tests and for checking that a script imports and runs:

```
PYTHONPATH=python/mock python3 my_script.py
```

`python/tests` shows how to build fake objects for a test.

## Where to go next

| Topic | File |
|---|---|
| Every function, type conversion and error | `docs/API.md` |
| Small working scripts for each feature | `python/examples/` |
| Hooking Blueprint functions, what works and what does not | `docs/BLUEPRINT_HOOKS.md` |
| Threads | `docs/THREADING.md` |
| Object lifetime and stale references | `docs/LIFETIME.md` |
| What happens when a call faults | `docs/FAULTS.md` |
| Building the mod yourself | `docs/BUILDING.md` |
