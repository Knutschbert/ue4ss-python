# Game packages

Keep code that belongs to one game in its own repository. This repository remains the game-independent UE4SS mod,
native `ue4ss` module, off-game mock, base typings and packaging tools.

## Layout

The recommended layout has this repository as a submodule and keeps game code in a normal Python package:

```
my-game/
    ue4ss-python/                 # git submodule
    python/
        mygame/                   # game-specific library
            __init__.py
        python_scripts/
            main.py               # the one mod entry script
    examples/
    tests/
    local.config.json             # git-ignored: game location and package sources
```

`main.py` imports the game package and makes registrations. It is the only file the mod starts; all other modules are
ordinary imports. Do not add game helpers to the `ue4ss` namespace or copy files into the submodule.

## Development and installation

Install the base checkout editable in the game package's virtual environment, so tests use the off-game `ue4ss` mock
and editors see the base typings:

```powershell
python -m pip install -e ue4ss-python
```

Package the game scripts with the base installer. Run this command from the game repository; the build, runtime and
output remain in the submodule:

```powershell
python ue4ss-python\tools\install.py --local-config local.config.json `
    --scripts-dir python\python_scripts --library python\mygame --dry-run
```

Remove `--dry-run` only after reviewing the target. `scripts_dir` and `libraries` can instead be stored in the calling
repository's git-ignored `local.config.json`; see [Building](BUILDING.md#scripts-from-a-game-package).

Check the base version when a game package needs a feature introduced by a newer base:

```python
import ue4ss

if tuple(map(int, ue4ss.__version__.split("."))) < (0, 1):
    raise RuntimeError("this package needs ue4ss-python 0.1 or later")
```

## Typing and reload

The editable base install includes the mock, `ue4ss.engine` and the shipped base stubs. Generate game-specific engine
stubs in the running game with `ue4ss.engine.generate_stubs(...)`, configure that generated folder as the game
repository's Pyright `stubPath`, and keep it out of version control.

Reloading removes registrations and forgets modules imported from `python_scripts`, then runs `main.py` again. Keep
reload-safe setup in `main.py` or package modules; threads started by a package are not stopped automatically and must
notice `ue4ss.script_generation()` themselves.

## Current limits

- One `python_scripts/main.py` entry script is supported.
- Packages installed with pip are for development only; third-party wheels are not available to the embedded runtime.
- Game packages are copied next to `main.py` by `--library`; a separate runtime library folder is not supported yet.
