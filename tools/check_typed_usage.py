#!/usr/bin/env python3
"""Type-check python/typed_checks against the stubs the game generated. Needs mypy in third_party/typecheck (see docs/TYPED_API.md)
and stubs in testgame/stubs_testgame (run python/examples/stubgen_main.py in the test game, or any game with ue4ss.engine.generate_stubs).

    python3 -I tools/check_typed_usage.py

usage_ok.py must give no errors; every line of usage_errors.py ends with `# E: <message>` and must give exactly that error, nothing else.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_mypy(files, stubs=None):
    """Lines mypy printed (Windows mypy through PowerShell, because the game's Python is Windows Python)."""
    script = str(ROOT / "tools" / "typecheck.ps1")
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", subprocess.run(["wslpath", "-w", script], capture_output=True, text=True).stdout.strip()]
    command += [subprocess.run(["wslpath", "-w", str(f)], capture_output=True, text=True).stdout.strip() for f in files]
    return subprocess.run(command, capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.replace("\r", "").splitlines()


def expected_errors(path):
    result = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = re.search(r"# E: (.+)$", line)
        if match:
            result[number] = match.group(1).strip()
    return result


def parse(lines, name):
    found = {}
    for line in lines:
        match = re.match(r".*" + re.escape(name) + r":(\d+): error: (.*?)(?:  \[[\w-]+\])?$", line)
        if match:
            found.setdefault(int(match.group(1)), []).append(match.group(2).strip())
    return found


def main():
    ok_file, bad_file = ROOT / "python" / "typed_checks" / "usage_ok.py", ROOT / "python" / "typed_checks" / "usage_errors.py"
    problems = []
    got_ok = parse(run_mypy([ok_file]), "usage_ok.py")
    for number, messages in sorted(got_ok.items()):
        problems.append(f"usage_ok.py:{number}: unexpected error: {messages[0]}")
    want, got = expected_errors(bad_file), parse(run_mypy([bad_file]), "usage_errors.py")
    for number, message in sorted(want.items()):
        if number not in got:
            problems.append(f"usage_errors.py:{number}: expected an error, mypy accepted it ({message})")
        elif message not in got[number]:
            problems.append(f"usage_errors.py:{number}: expected '{message}', got '{got[number][0]}'")
    for number in sorted(set(got) - set(want)):
        problems.append(f"usage_errors.py:{number}: unexpected error: {got[number][0]}")
    for problem in problems:
        print(problem)
    print(f"typed usage check: {len(want)} expected errors, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
