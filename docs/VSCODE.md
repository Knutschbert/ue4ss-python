# Editing game scripts in VS Code, with typings

This sets up VS Code so that scripts get completion, hover help and error squiggles for the whole game (`GameplayStatics`,
`pawn.K2_GetActorLocation()`, enums, structs, the game's own classes), and so that one key press installs the open file as
the mod's `main.py`.

## One-time setup
1. Install **VS Code** with the **Python** extension (Microsoft). It brings **Pylance**, the part that reads the typings.
   Use a normal Windows Python 3.12.
2. **File > Open Folder** and open this repository. It contains the settings VS Code needs:
   - `.vscode/settings.json` points Pylance at the typings (`stubs/`) and at the helper package (`python/lib`),
   - `pyrightconfig.json` says the same for the command-line checker,
   - `.vscode/tasks.json` and `.vscode/launch.json` hold the deploy buttons (below).
3. Generate the typings once (next section), then check it works: hover over `GameplayStatics` in a script: you should see
   the engine class, not "Unknown". `Vector(Q=1.0)` gets a red squiggle ("No parameter named Q").
   If imports stay unresolved: Command Palette > "Python: Restart Language Server", and check that `stubs/ue4ss` exists.

## Where the typings come from
The typings are written by the game itself from its own reflection data, so they match the game and its version exactly.
Run this once as `main.py` (and again after a game update), then "Developer: Reload Window":

```python
from ue4ss import engine
engine.generate_stubs(r"C:\path\to\this\repo\stubs")
```
See `docs/TYPED_API.md` for the options (Blueprint classes, one package only). `stubs/` is git-ignored.

## The edit - deploy - try loop
1. Edit any `.py` file (for throwaway tests make a new file in `scripts/user/` or anywhere).
2. **Deploy**: press **Ctrl+Shift+B** (task "Deploy current script to the game") or **F5** (launch configuration "Deploy
   current file to the game"). `tools/deploy_script.py` checks the file for syntax errors first, and if it is fine it
   - installs it as `dist/PythonScripting/python_scripts/main.py`; make the game's `Mods\PythonScripting` folder a link to
     `dist\PythonScripting` (`mklink /J`) or copy the folder there,
   - copies it to `python/python_scripts/main.py`, the file the packager installs, so packaging later does not bring back
     an older script,
   - keeps a copy only if you give a name: `scripts/user/<name>_<date>-<time>.py`.
3. In the game: reload the script with a key if your script binds one, for example
   `ue4ss.register_key_down(Key.F5, ue4ss.reload)`. Without such a line the script needs a game restart.

Set the environment variable `UE4SS_PY_GAME_PROCESS` (for example `MyGame-Win64-Shipping.exe`) and the deploy output also
says whether the game is running.

## What the typed code looks like
```python
from ue4ss.engine import GameplayStatics, PlayerController, Vector, StaticMeshActor

controller = PlayerController.find_first()           # PlayerController | None
if controller is not None and controller.Pawn is not None:
    here = controller.Pawn.K2_GetActorLocation()      # Vector
    print(here.X + 100.0)
```
Hover shows the engine class and parameter names; a wrong argument type, an unknown function or a misspelled property is
underlined before you start the game. Objects returned by the path-string functions (`ue4ss.find_first_of`,
`static_find_object`) are plain `UObject`s: any attribute is allowed and not checked.

## Checking from the command line
`tools/check_typed_usage.py` runs mypy over `python/typed_checks` against `testgame/stubs_testgame` (needs mypy in
`third_party/typecheck`, see `docs/TYPED_API.md`).

## Troubleshooting
- *Nothing is completed / `ue4ss` is unresolved*: `stubs/ue4ss` is missing (generate it) or you opened a different folder.
- *Squiggles on correct code after a game update*: the typings are older than the game; regenerate them.
- *"Deploy" says NOT deployed*: the message names the syntax error and line; nothing was copied.
- *"dist\PythonScripting does not exist"*: package the mod once (`tools/package_mod.py`, see `docs/BUILDING.md`).
