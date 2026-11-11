"""The local, git-ignored settings of this checkout (local.config.json in the repository root): which game the mod is installed
into. Written by tools/install.py, read by the other tools and the VS Code tasks.

    {"game_dir": "C:\\\\Games\\\\MyGame\\\\MyGame\\\\Binaries\\\\Win64", "game_exe": "MyGame-Win64-Shipping.exe"}
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "local.config.json"


class ConfigError(Exception):
    pass


def load(path: Path = CONFIG) -> dict:
    """The settings, {} when the file does not exist."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ConfigError(f"{path} is not valid JSON: {error}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must hold a JSON object")
    return data


def save(data: dict, path: Path = CONFIG) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def game(path: Path = CONFIG) -> tuple:
    """(game_dir, game_exe) of the configured game; raises ConfigError with what to do when there is none."""
    data = load(path)
    if not data.get("game_dir") or not data.get("game_exe"):
        raise ConfigError("no game is configured yet: run  python tools/install.py --game <path to the game>  once")
    return Path(data["game_dir"]), data["game_exe"]


def paths(data: dict, key: str, path: Path, *, many: bool = False) -> list[Path]:
    """Paths from a config value, relative to the config file when they are not absolute."""
    value = data.get(key)
    if value is None:
        return []
    values = value if many else [value]
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        expected = "an array of strings" if many else "a string"
        raise ConfigError(f"{key} in {path} must be {expected}")
    base = Path(path).parent
    return [(item if item.is_absolute() else base / item).resolve() for item in map(Path, values)]
