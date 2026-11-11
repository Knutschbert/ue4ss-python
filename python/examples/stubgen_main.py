# ue4ss.engine.generate_stubs in a real game: writes the typings of the running game and checks what came out.
import ast
import os
import time

import ue4ss
from ue4ss import engine


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + str(detail)[:200] + ')') if detail else ''}")


def repo_root():
    # the mod folder of a game is a link to dist/ inside the repository: realpath follows it
    here = os.path.realpath(__file__) if "__file__" in globals() else os.getcwd()
    while here and not os.path.isfile(os.path.join(here, "core", "include", "pyue", "version.hpp")):   # the repository root
        parent = os.path.dirname(here)
        if parent == here:
            raise RuntimeError("cannot find the repository from " + os.path.realpath(__file__) + ": refusing to write the stubs into the game folder")
        here = parent
    return here


is_chivalry = ue4ss.find_first_of("TBLPlayerController") is not None
folder = os.path.join(repo_root(), "testgame", "stubs_chivalry" if is_chivalry else "stubs_testgame")   # inside the repository, git-ignored
started = time.perf_counter()
summary = engine.generate_stubs(folder)
print("summary:", {k: v for k, v in summary.items() if k != "errors"}, "| errors:", summary["errors"][:3])
check("no class, struct or enum failed", summary["errors"] == [], summary["errors"][:2])
check("a useful amount was written", summary["classes"] > 100 and summary["structs"] > 20 and summary["enums"] > 10, {k: v for k, v in summary.items() if k != "errors"})

root = os.path.join(folder, "ue4ss")
files = [os.path.join(d, f) for d, _, names in os.walk(root) for f in names if f.endswith(".pyi")]
bad = []
for path in files:
    try:
        ast.parse(open(path, encoding="utf-8").read(), filename=path)
    except SyntaxError as e:
        bad.append((os.path.basename(path), str(e)))
check("every .pyi file is valid Python", not bad, bad[:3])
total = sum(os.path.getsize(p) for p in files)
print(f"{len(files)} files, {total / 1024:.0f} KiB, {summary['seconds']} s")

test_game = os.path.join(root, "engine", "PyTestGame.pyi")
if os.path.exists(test_game):
    text = open(test_game, encoding="utf-8").read()
    check("PyTestActor is a class of the game's package", "class PyTestActor(Actor):" in text)
    check("its int array property is typed", "IntArray: _ue.Array[int]" in text, [l for l in text.splitlines() if "IntArray" in l][:1])
    check("its fixed array property is typed", "FixedInts: _ue.FixedArray[int]" in text, [l for l in text.splitlines() if "FixedInts" in l][:1])
    check("its map property is typed", "NameIntMap: _ue.Map[_ue.FName, int]" in text, [l for l in text.splitlines() if "NameIntMap" in l][:1])
    check("its weak object property is optional", "WeakActor: Actor | None" in text or "WeakActor: PyTestActor | None" in text, [l for l in text.splitlines() if "WeakActor" in l][:1])
    check("its function has typed parameters and a result", "def AddInts(self, A: int, B: int) -> int: ..." in text, [l for l in text.splitlines() if "AddInts" in l][:1])
    check("an in/out function returns its outputs", "def SwapInts(self, A: int, B: int) -> tuple[int, int]: ..." in text, [l for l in text.splitlines() if "SwapInts" in l][:1])
    check("the game's enum is an IntEnum", "class EPyColor(IntEnum):" in text and "Red = 0" in text)
elif not is_chivalry:
    check("the test game's package file exists", False, os.listdir(os.path.join(root, "engine")))
if is_chivalry:
    tbl = open(os.path.join(root, "engine", "TBL.pyi"), encoding="utf-8").read()
    check("the game's own package is there with its player controller", "class TBLPlayerController(" in tbl)
    check("its chat function has typed parameters", "def ClientReceiveLocalizedChat(self, FormatText: str, Type:" in tbl, [l for l in tbl.splitlines() if "def ClientReceiveLocalizedChat" in l][:1])
    check("a Blueprint class can be made into a Python type on demand", engine.find("/Game/Interactables/World/Seatable/LevelActor_Chair.LevelActor_Chair_C").__name__ == "LevelActor_Chair_C")
engine_text = open(os.path.join(root, "engine", "Engine.pyi"), encoding="utf-8").read()
check("a static engine function is a classmethod", "@classmethod\n    def GetPlayerController(cls, WorldContextObject" in engine_text)
print("stubgen finished")
