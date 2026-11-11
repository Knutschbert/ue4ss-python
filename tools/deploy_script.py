#!/usr/bin/env python3
"""Deploy a script as the mod's main.py, from VS Code or the command line.

    python tools/deploy_script.py path/to/script.py [--name my_test]

What it does, in this order, and nothing else:
1. compiles the file (a syntax error stops everything, nothing is copied);
2. copies it to python/python_scripts/main.py, the file tools/package_mod.py installs, so a later packaging does not undo the deploy;
3. copies it to the installed main.py (dist/PythonScripting/python_scripts/main.py, which the game's Mods/PythonScripting folder links to);
4. only if --name is given: keeps a copy as scripts/user/<name>_<date>-<time>.py (without a name nothing is saved).
Set the environment variable UE4SS_PY_GAME_PROCESS to the game's image name (for example MyGame-Win64-Shipping.exe) to be
told whether the game is running.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GAME_PROCESS = os.environ.get("UE4SS_PY_GAME_PROCESS", "")


def game_running() -> bool:
    for tool in ("tasklist.exe", "tasklist"):
        try:
            out = subprocess.run([tool, "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace", timeout=30, stdin=subprocess.DEVNULL).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        return GAME_PROCESS.lower() in out.lower()
    return False


def safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")
    return cleaned or "script"


def deploy(source: Path, name: str = "", root: Path = ROOT, now: time.struct_time = None, *, scripts_dir: Path = None, mod_dir: Path = None) -> dict:
    """Do the steps; returns the paths written ("kept" is None without a name). Raises SyntaxError / FileNotFoundError / OSError with a clear message."""
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(f"not a file: {source}")
    text = source.read_text(encoding="utf-8")
    compile(text, str(source), "exec")                               # SyntaxError names the file and the line

    scripts_dir = Path(scripts_dir) if scripts_dir is not None else root / "python" / "python_scripts"
    installed_dir = (Path(mod_dir) if mod_dir is not None else root / "dist" / "PythonScripting") / "python_scripts"
    if not installed_dir.parent.is_dir():
        raise FileNotFoundError(f"{installed_dir.parent} does not exist: package the mod first (tools/package_mod.py)")
    installed_dir.mkdir(exist_ok=True)

    packager_copy = scripts_dir / "main.py"
    packager_copy.parent.mkdir(parents=True, exist_ok=True)
    keep = None
    if name.strip():
        stamp = time.strftime("%Y%m%d-%H%M%S", now or time.localtime())
        keep = root / "scripts" / "user" / f"{safe_name(name)}_{stamp}.py"
        keep.parent.mkdir(parents=True, exist_ok=True)

    installed = installed_dir / "main.py"
    for target in (packager_copy, installed, keep):
        if target is not None and source.resolve() != target.resolve():
            shutil.copyfile(source, target)
    return {"packager": packager_copy, "installed": installed, "kept": keep}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("script", type=Path)
    parser.add_argument("--name", default="", help="keep a copy under this name in scripts/user (default: no copy is kept)")
    parser.add_argument("--scripts-dir", type=Path, help="the game package's python_scripts folder")
    parser.add_argument("--mod-dir", type=Path, help="the packaged PythonScripting folder")
    args = parser.parse_args(argv)
    try:
        written = deploy(args.script, args.name, scripts_dir=args.scripts_dir, mod_dir=args.mod_dir)
    except SyntaxError as e:
        print(f"NOT deployed: syntax error in {e.filename}, line {e.lineno}: {e.msg}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as e:
        print(f"NOT deployed: {e}", file=sys.stderr)
        return 1
    print(f"deployed {args.script.name} as main.py")
    print(f"  installed : {written['installed']}")
    print(f"  packager  : {written['packager']}")
    print(f"  kept copy : {written['kept']}" if written["kept"] else "  kept copy : none (give a name to keep one)")
    print("The game is running: press F5 in the game to reload the script." if GAME_PROCESS and game_running() else "Start the game (or press F5 in a running game) to use it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
