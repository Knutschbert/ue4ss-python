# Example scripts

Short scripts to learn from, one topic each. To try one, copy it to
`<Game>\Binaries\Win64\Mods\PythonScripting\python_scripts\main.py` and start the game; each binds **F5** to reload, so
you can edit and try again without restarting. Start with [USAGE.md](../USAGE.md).

| Script | Shows |
|---|---|
| [01_hello_reload.py](01_hello_reload.py) | Where output goes, reloading with a key, a delayed task |
| [02_inspect_game.py](02_inspect_game.py) | Finding objects and listing the classes, properties and functions of a game |
| [03_key_toggle.py](03_key_toggle.py) | A key binding that reads a property and calls a function |
| [04_hook_function.py](04_hook_function.py) | Hooking a function before and after it runs, removing the hook |
| [05_spawn_actor.py](05_spawn_actor.py) | Spawning an actor, structs as dicts, arrays, destroying it |
| [06_background_thread.py](06_background_thread.py) | A Python thread that hands work to the game thread |
| [07_typed.py](07_typed.py) | Typed imports from `ue4ss.engine` and generating typings |

The scripts use only engine classes that every Unreal game has (`Actor`, `Pawn`, `PlayerController`,
`GameplayStatics`). They are run against the off-game module in the tests
([`tools/tests/test_user_examples.py`](../tools/tests/test_user_examples.py)); the test scripts for the mod itself are in
[`python/examples`](../python/examples).
