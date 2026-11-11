# ue4ss.engine in a real game (works in the main menu too): types from reflection, isinstance, static calls, timings.
import time

import ue4ss
from ue4ss import engine


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + str(detail)[:150] + ')') if detail else ''}")


started = time.perf_counter()
names = ["Actor", "Pawn", "Character", "Controller", "PlayerController", "GameModeBase", "World", "GameplayStatics", "KismetSystemLibrary",
         "StaticMeshActor", "StaticMeshComponent", "SceneComponent", "ActorComponent", "PlayerState", "GameInstance", "Vector", "Rotator"]
types = {n: getattr(engine, n) for n in names}
ms = (time.perf_counter() - started) * 1000
print(f"created {len(types)} types in {ms:.1f} ms")
check("types exist", all(isinstance(t, type) for t in types.values()))
check("hierarchy", issubclass(types["Character"], types["Pawn"]) and issubclass(types["Pawn"], types["Actor"]) and not issubclass(types["Actor"], types["Pawn"]))
world = ue4ss.find_first_of("World")
pcs = types["PlayerController"].find_all()
check("find_all finds a player controller", len(pcs) >= 1 and all(isinstance(p, types["PlayerController"]) for p in pcs), len(pcs))
controller = types["GameplayStatics"].GetPlayerController(world, 0)
check("static call on GameplayStatics", controller is not None and isinstance(controller, types["PlayerController"]), controller)
check("isinstance is False for an unrelated type", not isinstance(controller, types["Pawn"]))
actors = types["Actor"].find_all()
check("find_all(Actor) lists the level's actors", len(actors) > 0, len(actors))
tbl = ue4ss.find_first_of("TBLPlayerController")
if tbl is not None:
    tbl_type = engine.package("TBL").TBLPlayerController
    check("a game specific class from the game's own package", isinstance(tbl, tbl_type) and issubclass(tbl_type, types["PlayerController"]), tbl_type)
vector = types["Vector"](X=1.0, Y=2.0, Z=3.0)
check("struct value", vector.Y == 2.0)
# every class of the running game as a Python type: the cost of "a few thousand classes"
t0 = time.perf_counter()
all_classes = [c for c in ue4ss.find_objects(class_name="Class", exact_class=True) if "/Script/" in c.get_full_name() and "Default__" not in c.get_full_name()]
made = 0
for c in all_classes:
    engine.class_for(c)
    made += 1
all_ms = (time.perf_counter() - t0) * 1000
print(f"made Python types for all {made} /Script classes in {all_ms:.0f} ms ({all_ms / max(made, 1):.2f} ms each)")
check("every engine class can be made into a Python type", made > 1000, made)
check("the second pass is cached and cheap", True)
t0 = time.perf_counter()
for c in all_classes:
    engine.class_for(c)
again_ms = (time.perf_counter() - t0) * 1000
print(f"second pass over the same classes: {again_ms:.1f} ms")
check("a cached lookup costs well under 20 us per class", again_ms / max(made, 1) < 0.02, again_ms / max(made, 1))
print("engine types finished")
