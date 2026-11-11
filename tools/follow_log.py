#!/usr/bin/env python3
"""Show what the scripts print, live: follows UE4SS.log of the configured game and prints the [Python] lines.

    python tools/follow_log.py                 # the game of local.config.json (written by tools/install.py)
    python tools/follow_log.py --log <path>    # another UE4SS.log
    python tools/follow_log.py --all           # every line, not only the Python ones
    python tools/follow_log.py --once          # print what is there and stop

The game writes a new log at every start; the follower notices and starts again from the top. Stop with Ctrl+C.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_config  # noqa: E402

MARK = "[Python]"


def wanted(line: str, everything: bool = False) -> bool:
    return everything or MARK in line


def clean(line: str) -> str:
    """The text after the [Python] marker (a traceback line keeps its indentation); other lines unchanged."""
    line = line.rstrip("\r\n")
    position = line.find(MARK)
    if position < 0:
        return line
    rest = line[position + len(MARK):]
    return rest[1:] if rest.startswith(" ") else rest


class Follower:
    """Reads the complete new lines of a file that grows and may be replaced by a shorter one."""

    def __init__(self, path: Path):
        self.path, self.offset, self.partial = Path(path), 0, ""

    def poll(self) -> list:
        try:
            size = self.path.stat().st_size
        except OSError:
            return []                       # no log yet: the game has not started
        if size < self.offset:              # a new, shorter file: the game was started again
            self.offset, self.partial = 0, ""
        if size == self.offset:
            return []
        with open(self.path, "rb") as stream:
            stream.seek(self.offset)
            data = stream.read()
        self.offset += len(data)
        text = self.partial + data.decode("utf-8", errors="replace")
        lines = text.split("\n")
        self.partial = lines.pop()          # the last piece has no newline yet
        return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", type=Path, help="the UE4SS.log to follow (default: the configured game's)")
    parser.add_argument("--local-config", type=Path, help="the local.config.json to read (default: this repository's)")
    parser.add_argument("--all", action="store_true", help="show every line")
    parser.add_argument("--once", action="store_true", help="print the lines that exist and stop")
    parser.add_argument("--interval", type=float, default=0.3, help="seconds between looks at the file (default: %(default)s)")
    args = parser.parse_args(argv)
    try:
        log = args.log or local_config.game(args.local_config or local_config.CONFIG)[0] / "UE4SS.log"
    except local_config.ConfigError as error:
        print("follow_log: " + str(error) + " (or pass --log)", file=sys.stderr)
        return 1
    follower = Follower(log)
    print(f"following {log}" + ("" if log.exists() else " (not there yet: start the game)"), file=sys.stderr)
    try:
        while True:
            for line in follower.poll():
                if wanted(line, args.all):
                    print(clean(line), flush=True)
            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
