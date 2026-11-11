# Contributing

## Set up

- Tests without a game need Python 3.12 and a C++20 compiler: `python3 -m pip install -r requirements-dev.txt`.
- Building the mod needs Windows; see [docs/BUILDING.md](docs/BUILDING.md).

## Checks

```
python3 -m pytest python/tests tools -q      # must pass
tools/run_host_tests.sh                      # when core/ changed
```

Both run on every push ([workflow](.github/workflows/tests.yml)). Changes to `src/` cannot be tested there. Run the in-game
test scripts in the test game (`python tools\testgame.py all`, see [docs/TESTGAME.md](docs/TESTGAME.md)) or say in the pull
request what you built and ran, and with which game.

## Where code goes

| Kind of change | Place |
|---|---|
| Logic that does not need Unreal | `core/include/pyue/*.hpp`, header-only, with a host test in `core/tests/` |
| The mod itself | `src/` (a new `.cpp` is added to `src/CMakeLists.txt`) |
| Pure-Python helpers shipped with the mod | `python/lib/ue4ss/` |
| Tools | `tools/`, with tests in `tools/tests/` |

## Changing the Python API

A function of the `ue4ss` module exists in several places, which are kept equal:

1. Implement it in `src/`. The first line of its docstring is its signature, `name(args) -> result: text`; the typings are
   made from it.
2. Mirror the name, signature and errors in the off-game module, [`python/mock/ue4ss/__init__.py`](python/mock/ue4ss/__init__.py),
   with a test in `python/tests/`.
3. Document it in [docs/API.md](docs/API.md) (a test checks that every public name is documented).
4. Regenerate the shipped typings: `python3 -I tools/gen_base_stubs.py` (a test fails when they are out of date).
5. Add or extend an in-game test script in `python/examples/` and its entry in `manifest.json`. Scripts print `PASS name` /
   `FAIL name` / `name: SKIP (reason)` lines.
6. Note the change in [CHANGELOG.md](CHANGELOG.md).

Rules the API follows: anything that touches a game object raises `UEError` off the game thread and never crashes; names
stay close to the UE4SS Lua API; errors are Python exceptions with a message that says what to do.

## Style

- C++23 for `src/`, C++20 for `core/` (it is compiled with clang++ and `-Werror` in the host tests).
- Python: standard library only in `tools/` and in the shipped modules.
- Do not copy code from Epic's PythonScriptPlugin or other Unreal Engine sources; they are under the Unreal Engine EULA.
  This repository contains no engine code ([THIRD_PARTY.md](THIRD_PARTY.md)).

## Licence

Contributions are accepted under the [MIT licence](LICENSE) of the project.
