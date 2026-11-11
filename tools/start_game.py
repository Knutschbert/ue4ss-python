#!/usr/bin/env python3
"""Start the configured game (local.config.json, written by tools/install.py), unless it already runs.

    python tools/start_game.py [arguments for the game ...]
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_config  # noqa: E402
import package_mod  # noqa: E402


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        game_dir, game_exe = local_config.game()
    except local_config.ConfigError as error:
        print("start_game: " + str(error), file=sys.stderr)
        return 1
    if package_mod.running_game([game_exe]):
        print(f"{game_exe} is already running; reload your script in the game (the key bound to ue4ss.reload)")
        return 0
    executable = game_dir / game_exe
    if not executable.exists():
        print(f"start_game: {executable} does not exist; run tools/install.py --game <path> again", file=sys.stderr)
        return 1
    subprocess.Popen([str(executable)] + arguments, cwd=str(game_dir))
    print(f"started {executable}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
