# Typings (`.pyi` stubs): where they come from

There are two sets, and an editor uses both.

| Set | Folder | Contains | Made by |
|---|---|---|---|
| Base module | `python/typings/ue4ss` (committed) | The `ue4ss` module itself: `find_*`, hooks, keys, `UObject`, `Struct`, containers, errors. `ue4ss.engine` is declared with every name as `Any`. | `tools/gen_base_stubs.py`, from the off-game module `python/mock/ue4ss` |
| Game typings | `stubs/` (git-ignored) | The same base module, built from the native module's docstrings, plus every engine and game class, struct and enum, optionally Blueprint classes | The running game: `ue4ss.engine.generate_stubs(folder)` (`docs/TYPED_API.md`) |

`pyrightconfig.json` names `stubs` as the `stubPath` and `python/typings` as an extra path. Pyright and Pylance look in the
`stubPath` first, so once a game has written `stubs/`, those typings replace the committed ones; until then the base module
still completes.

Both sets are written by the same code, `python/lib/ue4ss/basestubs.py`: a signature is read from the first line of a
docstring (`name(args) -> result: text`); a Python function without such a line contributes its parameter names; a built-in
without one gets `(*args, **kwargs)`. Docstrings name parameters without types, so two small tables in that file give the
parameter names the module uses everywhere (`path`, `callback`, `key`, `milliseconds`, ...) and the results of its functions
their types; everything else is `Any`.

`tools/tests/test_gen_base_stubs.py` fails when the committed base typings differ from what the tool writes. After changing
the API (and the off-game module with it) run `python3 -I tools/gen_base_stubs.py`.

Limits of the committed set: values that depend on the game (properties, call results, struct fields) are `Any`, and it
describes the off-game module, which is kept equal to the native one by name and signature but is not the native one.
