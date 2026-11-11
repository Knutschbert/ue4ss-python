# ue4ss.bytecode on real Blueprint functions: every function with bytecode must parse to its EndOfScript, and the calls inside
# must resolve. In the test game the Blueprint is the engine's BP_Sky_Sphere; Chivalry 2 has over a thousand.
import collections

import ue4ss
from ue4ss import bytecode


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + str(detail)[:300] + ')') if detail else ''}")


def skip(label, why):
    print(f"{label}: SKIP ({why})")


classes = [c for c in ue4ss.find_all_of("BlueprintGeneratedClass") if not c.get_full_name().split(" ", 1)[1].startswith(("/Script/", "/Engine/Transient"))]
total = parsed = empty = 0
failures = collections.Counter()
examples = []
name_sizes = collections.Counter()
call_kinds = collections.Counter()
unresolved = resolved = 0
for cls in classes:
    path = cls.get_full_name().split(" ", 1)[1]
    for fname in cls.get_function_names():
        func = cls.find_function(fname)
        if func is None or func.get_outer() != cls:
            continue
        script = func.get_script()
        total += 1
        if not script:
            empty += 1
            continue
        try:
            nodes, size = bytecode.parse_auto(script)
        except bytecode.BytecodeError as e:
            failures[str(e).split(" at ")[0].split(":")[0]] += 1
            if len(examples) < 5:
                examples.append((path.rsplit(".", 1)[-1], fname, str(e)))
            continue
        parsed += 1
        name_sizes[size] += 1
        for call in bytecode.scan_calls(func):
            call_kinds[call["kind"]] += 1
            if call["target"]:
                resolved += 1
            else:
                unresolved += 1
print(f"Blueprint functions: {total} ({empty} without bytecode, {parsed} parsed, {sum(failures.values())} failed); name sizes {dict(name_sizes)}")
print("failure kinds:", dict(failures), examples)
print(f"calls: {dict(call_kinds)}; targets resolved {resolved}, unresolved {unresolved}")
if total - empty == 0:
    skip("bytecode", "this game has no Blueprint functions with bytecode")
else:
    check("every function with bytecode parses to its EndOfScript", not failures, f"{sum(failures.values())} failed of {total - empty}")
    check("calls were found", sum(call_kinds.values()) > 0, str(dict(call_kinds)))
    check("most call targets resolve to a function", resolved >= 0.8 * max(1, resolved + unresolved), f"{resolved} of {resolved + unresolved}")

sky = ue4ss.static_find_object("/Engine/EngineSky/BP_Sky_Sphere.BP_Sky_Sphere_C")
if sky is not None:
    construction = sky.find_function("UserConstructionScript")
    targets = [c["target"] for c in bytecode.scan_calls(construction)]
    print("UserConstructionScript calls:", targets)
    check("the construction script of BP_Sky_Sphere calls RefreshMaterial", any(t and (t == "RefreshMaterial" or t.endswith(":RefreshMaterial")) for t in targets), str(targets[:6]))
for graph_name, class_path in (("ExecuteUbergraph_LevelActor_BaseSeatable", "/Game/Interactables/World/Seatable/LevelActor_BaseSeatable.LevelActor_BaseSeatable_C"),):
    owner = ue4ss.static_find_object(class_path)
    if owner is None:
        continue
    targets = [(c["kind"], c["target"] or c["name"]) for c in bytecode.scan_calls(owner.find_function(graph_name))]
    print(f"{graph_name} calls:", targets[:30])
    check(f"the event graph {graph_name} calls functions", len(targets) > 0, str(targets[:5]))
noStats = [c for c in classes if c.get_full_name().split(" ", 1)[1].endswith(".LevelActor_WorldBase_NoStats_C")]
if noStats:
    graph = noStats[0].find_function("ExecuteUbergraph_LevelActor_WorldBase_NoStats")
    targets = [(c["kind"], c["target"] or c["name"]) for c in bytecode.scan_calls(graph)] if graph is not None else []
    print("ExecuteUbergraph_LevelActor_WorldBase_NoStats calls:", targets[:30])
    check("the event graph ExecuteUbergraph_LevelActor_WorldBase_NoStats calls functions", len(targets) > 0, str(targets[:5]))
print("bytecode finished")
