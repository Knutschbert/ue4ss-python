#!/usr/bin/env python3
"""Assemble a UE4SS mod folder: main.dll + embedded CPython runtime + scripts.

Output layout (drop the folder into <Game>/Binaries/Win64/Mods/):

    <name>/enabled.txt
    <name>/python_scripts/...   (not "scripts": UE4SS would treat the mod as a Lua mod)
    <name>/python_scripts/<package>/...   (library packages, see --library)
    <name>/dlls/main.dll
    <name>/dlls/python312.dll, python312.zip, vcruntime140*.dll, ...  (runtime dir contents)
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

# Files from the embeddable distribution that are not needed at runtime.
RUNTIME_SKIP = {"python312._pth", "python3.dll", "python.exe", "pythonw.exe", "python.cat"}

KEEP_FOLDERS = ()

# Image names of games that must not be running: replacing the mod's files under a running game is a probable cause of a
# crash, so packaging refuses while one runs. Empty by default; set with --game-process.
DEFAULT_GAME_PROCESSES = ()

# Written as python_scripts/main.py when there is no script yet.
STARTER_MAIN = """# Starter script, written by tools/package_mod.py because there was no main.py. Replace it with your own.
# It runs once on the game thread at the first engine tick; print() goes to UE4SS.log as "[Python] ...". See USAGE.md.
import ue4ss
from ue4ss import Key

ue4ss.register_key_down(Key.F5, ue4ss.reload)      # F5 in the game runs this file again after you edited it
print("PythonScripting is running, generation", ue4ss.script_generation())
"""


class GameRunningError(RuntimeError):
    """The game (or main.dll) is in use: the mod folder must not be replaced now."""


def running_processes() -> set:
    """Lower-cased image names of every running process, via tasklist (tasklist.exe from WSL)."""
    for tool in ("tasklist.exe", "tasklist"):
        try:
            out = subprocess.run([tool, "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace", timeout=30, check=True).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        names = set()
        for line in out.splitlines():
            line = line.strip()
            if line.startswith('"'):
                names.add(line[1:].split('","', 1)[0].lower())
        return names
    raise GameRunningError("cannot list running processes (tasklist not found), so cannot check that the game is closed; use --force to package anyway")


def running_game(names: Iterable[str], lister: Callable[[], set] = running_processes) -> Optional[str]:
    """The first of `names` that is running, or None. `lister` is injectable for tests."""
    wanted = [n for n in names if n]
    if not wanted:
        return None
    current = lister()
    for name in wanted:
        if name.lower() in current:
            return name
    return None


def file_is_locked(path: Path) -> bool:
    """True if `path` exists but cannot be opened for writing (a loaded DLL on Windows)."""
    if not path.exists():
        return False
    try:
        with open(path, "r+b"):
            return False
    except OSError:
        return True


def _copy_with_retry(copy: Callable, source: Path, target: Path, attempts: int, delay: float) -> None:
    for attempt in range(attempts):
        try:
            copy(source, target)
            return
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def _copytree_with_retry(copy: Callable, source: Path, target: Path, attempts: int, delay: float) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.is_dir():
            _copytree_with_retry(copy, item, target / item.name, attempts, delay)
        else:
            _copy_with_retry(copy, item, target / item.name, attempts, delay)


def assemble(main_dll: Path, runtime_dir: Path, scripts_dir: Path, out_dir: Path, name: str, *,
             game_processes: Iterable[str] = (), process_lister: Callable[[], set] = running_processes, libraries: Iterable[Path] = (), lib_dirs: Iterable[Path] = (),
             copy: Callable = shutil.copy2, attempts: int = 3, retry_delay: float = 0.5) -> Path:
    """Build <out_dir>/<name>. The new folder is assembled next to the old one and swapped in only when complete, so a
    failure leaves the previous folder (which the game's Mods link points at) untouched. Raises GameRunningError instead of
    touching anything while one of `game_processes` runs or the current main.dll is locked."""
    for label, path in (("main.dll", main_dll), ("runtime dir", runtime_dir)):
        if not path.exists():
            raise FileNotFoundError(f"{label} not found: {path}")
    if not any(runtime_dir.glob("python3[0-9]*.dll")):
        raise FileNotFoundError(f"no python3NN.dll in runtime dir: {runtime_dir}")

    mod_dir = out_dir / name
    running = running_game(game_processes, process_lister)
    if running:
        raise GameRunningError(f"{running} is running with the mod loaded; close it before packaging (--force skips this check)")
    if file_is_locked(mod_dir / "dlls" / "main.dll"):
        raise GameRunningError(f"{mod_dir / 'dlls' / 'main.dll'} is locked (loaded by a running game?); close the game before packaging")

    staging = out_dir / f".{name}.new"
    retired = out_dir / f".{name}.old"
    for leftover in (staging, retired):
        if leftover.exists():
            shutil.rmtree(leftover)
    dlls = staging / "dlls"
    try:
        dlls.mkdir(parents=True)
        _copy_with_retry(copy, main_dll, dlls / "main.dll", attempts, retry_delay)
        pdb = main_dll.with_suffix(".pdb")   # the plugin's crash backtrace resolves
        if pdb.exists():                     # our frames through this PDB (dbghelp searches the module's
            _copy_with_retry(copy, pdb, dlls / pdb.name, attempts, retry_delay)  # directory by default)
        for item in runtime_dir.iterdir():
            if item.name in RUNTIME_SKIP:
                continue
            if item.is_dir():
                _copytree_with_retry(copy, item, dlls / item.name, attempts, retry_delay)
            else:
                _copy_with_retry(copy, item, dlls / item.name, attempts, retry_delay)

        if scripts_dir.exists():
            _copytree_with_retry(copy, scripts_dir, staging / "python_scripts", attempts, retry_delay)
        elif (mod_dir / "python_scripts").is_dir():     # no scripts folder in the repository: the scripts written into the installed folder are the user's, keep them
            _copytree_with_retry(copy, mod_dir / "python_scripts", staging / "python_scripts", attempts, retry_delay)
        else:
            (staging / "python_scripts").mkdir()
        starter = staging / "python_scripts" / "main.py"
        if not starter.exists():                        # a first package: something that shows the mod works; never replaces a script
            starter.write_text(STARTER_MAIN, encoding="utf-8")
        for lib_dir in lib_dirs:    # pure-Python modules of the mod itself (python/lib/ue4ss/engine.py -> dlls/ue4ss/engine.py)
            if lib_dir.is_dir():
                for item in sorted(lib_dir.iterdir()):
                    if item.name != "__pycache__":
                        if item.is_dir():
                            _copytree_with_retry(copy, item, dlls / item.name, attempts, retry_delay)
                        else:
                            _copy_with_retry(copy, item, dlls / item.name, attempts, retry_delay)
        for library in libraries:   # importable packages shipped next to main.py, e.g. mygame -> python_scripts/mygame
            if library.is_dir():
                _copytree_with_retry(copy, library, staging / "python_scripts" / library.name, attempts, retry_delay)
        (staging / "enabled.txt").write_text("", encoding="utf-8")
        for kept in KEEP_FOLDERS:           # data the mod wrote at run time survives repackaging
            if (mod_dir / kept).is_dir():
                _copytree_with_retry(copy, mod_dir / kept, staging / kept, attempts, retry_delay)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    # Swap: the old folder moves aside, the new one takes its place; if that fails the old one is put back.
    try:
        if mod_dir.exists():
            os.replace(mod_dir, retired)
    except PermissionError:
        # Something has the folder open (an editor with the folder in its workspace, a file watcher): it cannot be renamed. The game is
        # not running (checked above), so bring the files up to date in place instead.
        _sync_in_place(staging, mod_dir, copy, attempts, retry_delay)
        shutil.rmtree(staging, ignore_errors=True)
        return mod_dir
    try:
        os.replace(staging, mod_dir)
    except PermissionError:
        # The folder was free a moment ago and is held now (an editor, a file watcher): put the old one back and update in place instead.
        if retired.exists() and not mod_dir.exists():
            os.replace(retired, mod_dir)
        _sync_in_place(staging, mod_dir, copy, attempts, retry_delay)
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(retired, ignore_errors=True)
        return mod_dir
    except BaseException:
        if retired.exists():
            os.replace(retired, mod_dir)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if retired.exists():
        shutil.rmtree(retired, ignore_errors=True)
    return mod_dir


def _sync_in_place(source: Path, target: Path, copy: Callable, attempts: int, delay: float) -> None:
    """Make `target` hold exactly the files of `source`, without renaming the folder: copy new and changed files, delete the rest.
    (`target` is a real folder here; nothing in it is a link.)"""
    target.mkdir(parents=True, exist_ok=True)
    wanted = set()
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        wanted.add(relative)
        destination = target / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif not destination.exists() or destination.stat().st_size != path.stat().st_size or destination.read_bytes() != path.read_bytes():
            _copy_with_retry(copy, path, destination, attempts, delay)
    for path in sorted(target.rglob("*"), key=lambda p: len(p.parts), reverse=True):   # deepest first
        if path.relative_to(target) not in wanted:
            if path.is_dir() and not path.is_symlink():
                path.rmdir() if not any(path.iterdir()) else shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--main-dll", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, default=Path("third_party/python/runtime"))
    parser.add_argument("--scripts-dir", type=Path, default=Path("python/python_scripts"))
    parser.add_argument("--library", type=Path, action="append", default=None,
                        help="a Python package folder to ship in python_scripts (repeatable; default: none)")
    parser.add_argument("--out-dir", type=Path, default=Path("dist"))
    parser.add_argument("--name", default="PythonScripting")
    parser.add_argument("--game-process", action="append", default=None,
                        help="image name of the game; packaging refuses while it runs (repeatable; default: %s)" % (", ".join(DEFAULT_GAME_PROCESSES) or "none"))
    parser.add_argument("--force", action="store_true", help="skip the running-game and locked-file checks (can crash a running game)")
    args = parser.parse_args(argv)
    games = () if args.force else tuple(args.game_process or DEFAULT_GAME_PROCESSES)
    try:
        mod_dir = assemble(args.main_dll, args.runtime_dir, args.scripts_dir, args.out_dir, args.name, game_processes=games, process_lister=running_processes,
                            libraries=args.library or [], lib_dirs=[Path("python/lib")])
    except GameRunningError as e:
        print(f"refusing to package: {e}", file=sys.stderr)
        return 2
    print(f"assembled {mod_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
