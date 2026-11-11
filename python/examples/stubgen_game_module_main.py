# Typings for the game's own module only (st6.1): generate_stubs(folder, packages=["TBL"]) in Chivalry 2, ["PyTestGame"] in the test game.
# The packages the module needs (Engine, CoreUObject, ...) come along; dependencies=False writes the module alone. Writes to testgame/stubs_*_module (git-ignored).
import ast
import os

import ue4ss
from ue4ss import engine


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + str(detail)[:300] + ')') if detail else ''}")


def repo_root():
    here = os.path.realpath(__file__) if "__file__" in globals() else os.getcwd()
    while here and not os.path.isfile(os.path.join(here, "core", "include", "pyue", "version.hpp")):   # the repository root
        parent = os.path.dirname(here)
        if parent == here:
            raise RuntimeError("cannot find the repository: refusing to write the stubs into the game folder")
        here = parent
    return here


is_chivalry = ue4ss.find_first_of("TBLPlayerController") is not None
module, game_mode, base, kind = ("TBL", "TBLGameMode", "GameMode", "chivalry") if is_chivalry else ("PyTestGame", "PyTestGameMode", "GameModeBase", "testgame")
folder = os.path.join(repo_root(), "testgame", f"stubs_{kind}_module")
summary = engine.generate_stubs(folder, packages=[module])
print("summary:", {k: v for k, v in summary.items() if k not in ("errors", "unresolved_top")}, "| errors:", summary["errors"][:2])
print("most referenced types without a stub:", summary["unresolved_top"][:5])
engine_dir = os.path.join(folder, "ue4ss", "engine")
files = sorted(f for f in os.listdir(engine_dir) if f.endswith(".pyi"))
check("the module's stubs and the packages it needs were written", f"{module}.pyi" in files and "Engine.pyi" in files and "CoreUObject.pyi" in files, str(files[:8]))
check("no class, struct or enum failed", summary["errors"] == [], summary["errors"][:2])
trees = {f: ast.parse(open(os.path.join(engine_dir, f), encoding="utf-8").read()) for f in files}
check("every generated file is valid Python", len(trees) == len(files))
classes = {n.name: n for n in trees[f"{module}.pyi"].body if isinstance(n, ast.ClassDef)}
mode = classes.get(game_mode)
check(f"{game_mode} is stubbed", mode is not None, str(sorted(classes)[:6]))
if mode is not None:
    check(f"{game_mode} derives from the engine's {base}", [ast.unparse(b) for b in mode.bases] == [base], str([ast.unparse(b) for b in mode.bases]))
    members = [m.name if isinstance(m, ast.FunctionDef) else m.target.id for m in mode.body if isinstance(m, (ast.FunctionDef, ast.AnnAssign))]
    check(f"{game_mode} lists its own properties and functions", len(members) >= (20 if is_chivalry else 1), f"{len(members)}: {members[:8]}")
engine_classes = {n.name for n in trees["Engine.pyi"].body if isinstance(n, ast.ClassDef)}
check(f"its base class {base} exists in the Engine stubs that came along", base in engine_classes)
check("few referenced types are missing", summary["unresolved_references"] <= max(5, summary["classes"]), f"{summary['unresolved_types']} types, {summary['unresolved_references']} references")
lonely = engine.generate_stubs(os.path.join(repo_root(), "testgame", f"stubs_{kind}_module_alone"), packages=[module], dependencies=False)
check("dependencies=False writes the module alone", lonely["packages"] == 1 and lonely["unresolved_types"] > summary["unresolved_types"], f"{lonely['packages']} package, {lonely['unresolved_types']} missing types")
print("stubgen game module finished")
