# generate_stubs(blueprints=True): the loaded Blueprint classes get typings too, one module per content folder (rqf.5, st6.2).
# Writes to testgame/stubs_chivalry_bp or testgame/stubs_testgame_bp (git-ignored). The test game has the engine Blueprint BP_Sky_Sphere (rqf.6), Chivalry 2 has its own.
import ast
import os
import time

import ue4ss
from ue4ss import engine


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + str(detail)[:300] + ')') if detail else ''}")


def skip(label, why):
    print(f"{label}: SKIP ({why})")


def repo_root():
    here = os.path.realpath(__file__) if "__file__" in globals() else os.getcwd()
    while here and not os.path.isfile(os.path.join(here, "core", "include", "pyue", "version.hpp")):   # the repository root
        parent = os.path.dirname(here)
        if parent == here:
            raise RuntimeError("cannot find the repository: refusing to write the stubs into the game folder")
        here = parent
    return here


is_chivalry = ue4ss.find_first_of("TBLPlayerController") is not None
folder = os.path.join(repo_root(), "testgame", "stubs_chivalry_bp" if is_chivalry else "stubs_testgame_bp")
# a folder of Blueprints that may not be loaded yet is loaded by the load option (asset registry): Chivalry 2 seatable furniture, the test game's engine sky
load_folder = "/Game/Interactables/World/Seatable" if is_chivalry else "/Engine/EngineSky"
started = time.perf_counter()
summary = engine.generate_stubs(folder, blueprints=True, load=[load_folder], load_limit=60)
elapsed = time.perf_counter() - started
print("most referenced types without a stub:", summary["unresolved_top"][:6])
print("summary:", {k: v for k, v in summary.items() if k not in ("errors", "unresolved_top")}, "| errors:", summary["errors"][:3], f"| {elapsed:.1f} s")
loaded = len([c for c in ue4ss.find_all_of("BlueprintGeneratedClass") if not c.get_full_name().split(" ", 1)[1].startswith(("/Script/", "/Engine/Transient"))])
print("loaded Blueprint classes right now:", loaded)

content_dir = os.path.join(folder, "ue4ss", "engine", "Content")
modules = {}
for directory, _, names in os.walk(content_dir):
    if "__init__.pyi" in names:
        relative = os.path.relpath(directory, content_dir)
        modules[relative.replace(os.sep, ".")] = ast.parse(open(os.path.join(directory, "__init__.pyi"), encoding="utf-8").read())
check("every generated Blueprint module is valid Python", len(modules) > 0, f"{len(modules)} modules")
classes = {}
for module, tree in modules.items():
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            classes[node.name] = (module, node)
check("the load option found the Blueprints of the folder", summary["load"] is not None and summary["load"]["found"] >= 1, str(summary["load"]))
if is_chivalry:
    check("and loaded those that were missing", summary["load"]["loaded"] + summary["load"]["already"] >= summary["load"]["found"] - summary["load"]["failed"] - summary["load"]["skipped"], str(summary["load"]))
    check("a Blueprint that the menu never loads is typed now", "LevelActor_Stocks_C" in classes, str(sorted(n for n in classes if n.startswith("LevelActor"))[:8]))
total = sum(1 for tree in modules.values() for node in tree.body if isinstance(node, ast.ClassDef))
check("the Blueprint classes are in the typings", total >= 0.9 * loaded, f"{total} of {loaded}")
check("class names are unique inside every module", all(len([n for n in tree.body if isinstance(n, ast.ClassDef)]) == len({n.name for n in tree.body if isinstance(n, ast.ClassDef)}) for tree in modules.values()))


def members_of(cls):
    return [m.name if isinstance(m, ast.FunctionDef) else m.target.id for m in cls.body if isinstance(m, (ast.FunctionDef, ast.AnnAssign))]


if "BP_Sky_Sphere_C" in classes:
    module, sky = classes["BP_Sky_Sphere_C"]
    members = members_of(sky)
    print("BP_Sky_Sphere_C in module", module, "bases:", [ast.unparse(b) for b in sky.bases], "members:", members[:12])
    check("the module path mirrors the content path", module == "Engine.EngineSky", module)
    check("BP_Sky_Sphere_C lists its functions and variables", {"UpdateSunDirection", "RefreshMaterial", "Sun_height"} <= set(members), members[:12])
    check("it inherits from the stub of its base class", len(sky.bases) == 1 and ast.unparse(sky.bases[0]).endswith("Actor"), [ast.unparse(b) for b in sky.bases])
if "LevelActor_Chair_C" in classes:
    module, chair = classes["LevelActor_Chair_C"]
    base_name = ast.unparse(chair.bases[0]) if chair.bases else ""
    print("LevelActor_Chair_C in module", module, "base:", base_name)
    check("the chair's module path mirrors the content path", module == "Game.Interactables.World.Seatable", module)
    base = classes.get(base_name.rsplit(".", 1)[-1])
    check("the chair has a base class that is stubbed too", base is not None, base_name)
    if base is not None:
        members = members_of(base[1])
        print(base_name, "members:", members[:12])
        check("its events, functions and variables are listed", len(members) > 3, members[:10])
print("stubgen blueprints finished")
