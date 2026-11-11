# Typed API spike (ypy.1): engine classes, structs and enums as Python types created at runtime from reflection.
# Prints timings that the design record (docs/TYPED_API.md) quotes.
import time

import ue4ss
from ue4ss import engine


def _raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _no_attr(fn):
    return _raises(fn, AttributeError)


def _raises_engine(fn):
    return _raises(fn, engine.EngineError)


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + str(detail)[:160] + ')') if detail else ''}")


NAMES = ["Actor", "Pawn", "Character", "Controller", "PlayerController", "GameModeBase", "World", "GameplayStatics", "KismetMathLibrary",
         "KismetSystemLibrary", "KismetStringLibrary", "StaticMeshActor", "StaticMeshComponent", "SceneComponent", "ActorComponent",
         "PlayerState", "GameInstance", "PyTestActor", "Vector", "Rotator"]

started = time.perf_counter()
types = {name: getattr(engine, name) for name in NAMES}
elapsed_ms = (time.perf_counter() - started) * 1000
print(f"created {len(types)} engine types in {elapsed_ms:.1f} ms ({elapsed_ms / len(types):.2f} ms each, parents included)")
check("every name gave a type", all(isinstance(t, type) for t in types.values()))

Actor, Pawn, PlayerController, GameplayStatics, StaticMeshActor, PyTestActor = (types[n] for n in ("Actor", "Pawn", "PlayerController", "GameplayStatics", "StaticMeshActor", "PyTestActor"))
check("classes mirror the engine inheritance", issubclass(Pawn, Actor) and issubclass(types["Character"], Pawn) and issubclass(PlayerController, types["Controller"]) and not issubclass(Actor, Pawn))
check("every generated class derives from the root", all(issubclass(t, engine.EngineObject) for n, t in types.items() if n not in ("Vector", "Rotator")))
check("a generated class is not constructible", _raises(lambda: Actor(), TypeError))

world = ue4ss.find_first_of("World")
pc = ue4ss.find_first_of("PlayerController")
pawn = pc.Pawn
check("isinstance on wrapped objects follows the engine hierarchy", isinstance(pawn, Pawn) and isinstance(pawn, Actor) and not isinstance(pawn, PlayerController))
check("isinstance on non-objects is False", not isinstance(5, Actor) and not isinstance(None, Actor))
actors = PyTestActor.find_all()
check("find_all() lists the live instances", len(actors) == 3 and all(isinstance(a, PyTestActor) for a in actors), len(actors))
check("find_first()", isinstance(PyTestActor.find_first(), PyTestActor))

controller = GameplayStatics.GetPlayerController(world, 0)
check("static function call on the class", controller == pc, controller)
check("static_class() and default_object()", PyTestActor.static_class().get_full_name().endswith("PyTestActor") and PyTestActor.default_object().is_valid())
check("a missing function is an AttributeError", _no_attr(lambda: GameplayStatics.NoSuchFunction))
check("dir() lists the engine functions", "GetPlayerController" in dir(GameplayStatics))

vector = types["Vector"](X=1.0, Y=2.0, Z=3.0)
check("a struct type builds values", isinstance(vector, types["Vector"]) and (vector.X, vector.Y, vector.Z) == (1.0, 2.0, 3.0) and not isinstance(vector, types["Rotator"]), vector)

color = engine.package("PyTestGame").EPyColor
check("an enum type is an IntEnum", issubclass(color, __import__("enum").IntEnum) and len(color) >= 2, list(color))
check("package lookup is exact", engine.package("Engine").Actor is Actor)
check("the same class object every time", getattr(engine, "Actor") is Actor and engine.find("/Script/Engine.Actor") is Actor)
check("an unknown name raises EngineError", _raises_engine(lambda: engine.NoSuchEngineThing))

# --- cost ---------------------------------------------------------------------------------------------------------------------------------
statics_object = ue4ss.static_find_object("/Script/Engine.GameplayStatics")
N = 3000


def timed(fn):
    t0 = time.perf_counter()
    for _ in range(N):
        fn()
    return (time.perf_counter() - t0) / N * 1e6


old_call = timed(lambda: statics_object.GetPlayerController(world, 0))
new_call = timed(lambda: GameplayStatics.GetPlayerController(world, 0))
old_is = timed(lambda: pawn.is_a("/Script/Engine.Actor"))
new_is = timed(lambda: isinstance(pawn, Actor))
print(f"call: statics_object.GetPlayerController {old_call:.1f} us | GameplayStatics.GetPlayerController {new_call:.1f} us")
print(f"type test: obj.is_a(path) {old_is:.1f} us | isinstance(obj, Actor) {new_is:.1f} us")
check("the class-level call costs about the same as the object call", new_call < old_call * 3 + 20, (old_call, new_call))
check("isinstance costs about the same as is_a", new_is < old_is * 3 + 5, (old_is, new_is))
print("typed spike finished")
