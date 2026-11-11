#!/usr/bin/env python3
"""Build the mod and install it into a game, in one command (Windows).

    python tools\\install.py --game "C:\\Games\\MyGame"     # the first time: the game's folder (or its Binaries\\Win64)
    python tools\\install.py                               # later: the game is remembered in local.config.json
    python tools\\install.py --skip-build                  # package and install what is already built
    python tools\\install.py --dry-run                     # only say what would be done

What it does, in this order:
1. finds the game's Binaries\\Win64 folder (the one with <Name>-Win64-Shipping.exe) and remembers it in local.config.json;
2. refuses while that game is running;
3. builds UE4SS.dll, the proxy dwmapi.dll and the mod's main.dll (tools\\build.ps1), unless --skip-build;
4. packages the mod into dist\\PythonScripting (tools\\package_mod.py);
5. copies UE4SS.dll and dwmapi.dll next to the game's executable. Files it replaces are kept once as
   <name>.before-ue4ss-python. An existing UE4SS-settings.ini and Mods folder are left alone; when there is no
   UE4SS-settings.ini yet, the default one of RE-UE4SS is copied;
6. links Mods\\PythonScripting to dist\\PythonScripting (a directory junction, so later packaging needs no copying), or copies
   the folder with --copy.
"""
import argparse
import filecmp
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_config  # noqa: E402
import package_mod  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "PythonScripting"
BACKUP_SUFFIX = ".before-ue4ss-python"
BUILD_TARGETS = ("UE4SS", "proxy", MOD_NAME)
UE4SS_FILES = ("UE4SS.dll", "dwmapi.dll")


class InstallError(Exception):
    pass


def find_game(path: Path) -> tuple:
    """(Binaries/Win64 folder, executable name) for a game folder, its Binaries/Win64, or the executable itself."""
    path = Path(path)
    if path.is_file():
        path = path.parent
    if not path.is_dir():
        raise InstallError(f"{path} does not exist")
    found = sorted(path.glob("*-Win64-Shipping.exe"))
    if not found:
        found = sorted(path.glob("Binaries/Win64/*-Win64-Shipping.exe")) + sorted(path.glob("*/Binaries/Win64/*-Win64-Shipping.exe"))
    if not found:
        raise InstallError(f"no *-Win64-Shipping.exe in {path}, its Binaries\\Win64 or <subfolder>\\Binaries\\Win64; pass the folder that holds the game's executable")
    if len(found) > 1:
        raise InstallError("more than one game executable found, pass the Binaries\\Win64 folder itself: " + ", ".join(str(f) for f in found))
    return found[0].parent, found[0].name


def is_link(path: Path) -> bool:
    """A symbolic link or a Windows directory junction (os.path.islink is False for junctions before Python 3.12)."""
    if os.path.islink(path) or getattr(os.path, "isjunction", lambda p: False)(path):
        return True
    try:
        os.readlink(path)
        return True
    except (OSError, ValueError):
        return False


def make_junction(link: Path, target: Path) -> None:
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    if done.returncode != 0:
        raise InstallError(f"mklink /J failed: {(done.stderr or done.stdout).strip()}")


def run_build(target: str, config: str, ue4ss_root) -> None:
    command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "tools" / "build.ps1"), "-Target", target, "-Config", config]
    if ue4ss_root:
        command += ["-Ue4ssRoot", str(ue4ss_root)]
    if subprocess.run(command).returncode != 0:
        raise InstallError(f"building {target} failed (see the output above)")


class Installer:
    """The steps; `say` reports, `dry_run` only reports. link / build / package are injectable for tests."""

    def __init__(self, root: Path = ROOT, *, config: str = "Game__Shipping__Win64", ue4ss_root=None, dry_run: bool = False, say=print,
                 link=make_junction, build=run_build, package=None, running=package_mod.running_game):
        self.root, self.config, self.dry_run, self.say = Path(root), config, dry_run, say
        self.ue4ss_root = Path(ue4ss_root) if ue4ss_root else self.root.parent / "RE-UE4SS"
        self.explicit_ue4ss_root = ue4ss_root
        self.link, self.build, self.running = link, build, running
        self.package = package or self._package

    # --- steps ---
    def check_not_running(self, game_exe: str) -> None:
        if self.running([game_exe]):
            raise InstallError(f"{game_exe} is running; close the game first (its DLLs cannot be replaced while it runs)")

    def build_all(self) -> None:
        for target in BUILD_TARGETS:
            self.say(f"build {target} ({self.config})")
            if not self.dry_run:
                self.build(target, self.config, self.explicit_ue4ss_root)

    def _package(self) -> None:
        code = package_mod.main(["--main-dll", str(self.root / "build" / "src" / "main.dll"), "--runtime-dir", str(self.root / "third_party" / "python" / "runtime"),
                                 "--scripts-dir", str(self.root / "python" / "python_scripts"), "--out-dir", str(self.root / "dist"), "--name", MOD_NAME])
        if code:
            raise InstallError("packaging failed (see the output above)")

    def package_mod(self) -> None:
        self.say(f"package the mod into {self.root / 'dist' / MOD_NAME}")
        if not self.dry_run:
            self.package()

    def install_ue4ss(self, game_dir: Path) -> None:
        built = self.root / "build" / self.config / "bin"
        for name in UE4SS_FILES:
            source, target = built / name, game_dir / name
            if not source.exists():
                if self.dry_run:
                    self.say(f"copy {name} to {game_dir} (not built yet)")
                    continue
                raise InstallError(f"{source} does not exist; build first (run without --skip-build)")
            if target.exists() and filecmp.cmp(source, target, shallow=False):
                self.say(f"{name} is up to date")
                continue
            backup = game_dir / (name + BACKUP_SUFFIX)
            if target.exists() and not backup.exists():
                self.say(f"keep the game's current {name} as {backup.name}")
                if not self.dry_run:
                    shutil.copy2(target, backup)
            self.say(f"copy {name} to {game_dir}")
            if not self.dry_run:
                shutil.copy2(source, target)
        settings = game_dir / "UE4SS-settings.ini"
        if not settings.exists():
            default = self.ue4ss_root / "assets" / "UE4SS-settings.ini"
            if default.exists():
                self.say("no UE4SS-settings.ini in the game folder: copy the default one of RE-UE4SS (see the UE4SS installation guide if the game needs other settings)")
                if not self.dry_run:
                    shutil.copy2(default, settings)
            else:
                self.say(f"WARNING: no UE4SS-settings.ini in {game_dir} and none found in {default.parent}; UE4SS needs one")

    def install_mod(self, game_dir: Path, copy: bool) -> None:
        source, target = self.root / "dist" / MOD_NAME, game_dir / "Mods" / MOD_NAME
        if not self.dry_run:
            target.parent.mkdir(exist_ok=True)
        if copy:
            if target.exists() and is_link(target):
                raise InstallError(f"{target} is a link (from an earlier install without --copy); remove it or run without --copy")
            self.say(f"copy the mod to {target} (scripts already there are kept)")
            if not self.dry_run:
                keep_scripts = (target / "python_scripts" / "main.py").exists()
                shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("python_scripts") if keep_scripts else None)
            return
        if target.exists() or is_link(target):
            if not is_link(target):
                raise InstallError(f"{target} is a real folder, not a link. Move your scripts out of it and delete it, or run with --copy to copy into it")
            if os.path.realpath(target) == os.path.realpath(source):
                self.say(f"{target} already links to {source}")
                return
            self.say(f"re-point the link {target}")
            if not self.dry_run:
                os.rmdir(target) if os.name == "nt" else os.unlink(target)      # removes the link, never what it points to
        self.say(f"link {target} -> {source}")
        if not self.dry_run:
            self.link(target, source)

    # --- the whole run ---
    def run(self, game_dir: Path, game_exe: str, *, skip_build: bool = False, copy: bool = False) -> None:
        self.say(f"game: {game_dir / game_exe}")
        self.check_not_running(game_exe)
        if not skip_build:
            self.build_all()
        self.package_mod()
        self.install_ue4ss(game_dir)
        self.install_mod(game_dir, copy)
        self.say("dry run: nothing was changed" if self.dry_run else
                 f"installed. Start the game; script output is in {game_dir / 'UE4SS.log'} with the prefix [Python] (python tools\\follow_log.py shows it live).")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--game", type=Path, help="the game's folder, its Binaries\\Win64, or its executable (remembered afterwards)")
    parser.add_argument("--skip-build", action="store_true", help="do not build; package and install what build\\ holds")
    parser.add_argument("--copy", action="store_true", help="copy the mod folder into the game instead of linking it")
    parser.add_argument("--config", default="Game__Shipping__Win64", help="build configuration (default: %(default)s)")
    parser.add_argument("--ue4ss-root", type=Path, help="the RE-UE4SS checkout (default: next to this repository)")
    parser.add_argument("--dry-run", action="store_true", help="only print what would be done")
    args = parser.parse_args(argv)
    try:
        if args.game:
            game_dir, game_exe = find_game(args.game)
            if not args.dry_run:
                settings = local_config.load()
                settings.update({"game_dir": str(game_dir), "game_exe": game_exe})
                local_config.save(settings)
        else:
            game_dir, game_exe = local_config.game()
        if os.name != "nt" and not args.dry_run:
            raise InstallError("run this with a Windows Python (the game, the build and the directory link are Windows things); --dry-run works anywhere")
        Installer(config=args.config, ue4ss_root=args.ue4ss_root, dry_run=args.dry_run).run(game_dir, game_exe, skip_build=args.skip_build, copy=args.copy)
    except (InstallError, local_config.ConfigError) as error:
        print("install: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
