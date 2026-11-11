# Copy over python_scripts/main.py to check object and struct properties in the game.
# Every write is restored in a finally block.
import ue4ss

gm = ue4ss.find_first_of("GameModeBase")
pc = ue4ss.find_first_of("PlayerController")
print("targets:", gm.get_fname(), "|", pc.get_fname())


def check(label, ok):
    print(f"{label}: {'PASS' if ok else 'FAIL'}")


def raises(label, exc, fn):
    try:
        fn()
    except exc as e:
        print(f"{label}: PASS ({type(e).__name__}: {e})")
        return
    except BaseException as e:
        print(f"{label}: FAIL (got {type(e).__name__}: {e})")
        return
    print(f"{label}: FAIL (no exception)")


# --- regression: properties of a CLASS object must not use the wrong layout --------------------
actor_class = ue4ss.static_find_object("/Script/Engine.Actor")
raises("class object has no Actor property bHidden", AttributeError, lambda: actor_class.bHidden)

# --- object properties -----------------------------------------------------------------------
print("pc.Pawn:", pc.Pawn)
print("pc.PlayerState:", pc.PlayerState)
print("gm.GameState:", gm.GameState)
print("gm.Owner:", gm.Owner)
print("class property DefaultPawnClass:", gm.DefaultPawnClass)
print("class property PlayerControllerClass:", gm.PlayerControllerClass)
check("object property returns UObject or None",
      all(v is None or isinstance(v, ue4ss.UObject) for v in (pc.Pawn, pc.PlayerState, gm.GameState, gm.Owner)))
check("object read is the same object as found by name", gm.GameState is None or gm.GameState == ue4ss.find_first_of("GameStateBase"))

old_owner = gm.Owner
try:
    gm.Owner = pc
    check("write object property", gm.Owner == pc)
    gm.Owner = None
    check("write None", gm.Owner is None)
finally:
    gm.Owner = old_owner
check("object restored", gm.Owner == old_owner)

raises("object of the wrong class", ue4ss.UEError, lambda: setattr(gm, "GameState", pc))
raises("wrong type for object property", TypeError, lambda: setattr(gm, "Owner", "not an object"))

old_pawn_class = gm.DefaultPawnClass
default_pawn = ue4ss.static_find_object("/Script/Engine.DefaultPawn")
try:
    if default_pawn is not None:
        gm.DefaultPawnClass = default_pawn
        check("write class property", gm.DefaultPawnClass == default_pawn)
    raises("class that does not derive from the meta class", ue4ss.UEError,
           lambda: setattr(gm, "DefaultPawnClass", ue4ss.static_find_object("/Script/Engine.Actor")))
    raises("non-class object for class property", ue4ss.UEError, lambda: setattr(gm, "DefaultPawnClass", pc))
finally:
    gm.DefaultPawnClass = old_pawn_class
check("class property restored", gm.DefaultPawnClass == old_pawn_class)

# --- struct views ----------------------------------------------------------------------------
mv = gm.ReplicatedMovement
print("struct view:", mv, "| name:", mv.get_struct_name(), "| valid:", mv.is_valid())
print("nested:", mv.LinearVelocity, mv.Rotation, "| X =", mv.LinearVelocity.X)
print("to_dict:", mv.to_dict())
names = mv.get_property_names()
print("field names:", names)
check("get_property_names lists fields", "LinearVelocity" in names and "bRepPhysics" in names)
check("object property names", "bHidden" in gm.get_property_names() and len(gm.get_property_names()) > 50)
check("repr never raises", "Struct" in repr(mv))
original = mv.to_dict()

try:
    gm.ReplicatedMovement.LinearVelocity.X = 3.5
    check("write nested float", gm.ReplicatedMovement.LinearVelocity.X == 3.5)
    check("view sees write made through another view", mv.LinearVelocity.X == 3.5)
    check("sibling fields untouched", gm.ReplicatedMovement.LinearVelocity.Y == original["LinearVelocity"]["Y"]
          and gm.ReplicatedMovement.AngularVelocity.X == original["AngularVelocity"]["X"])

    gm.ReplicatedMovement.Rotation = {"Yaw": 45.0}
    rot = gm.ReplicatedMovement.Rotation
    check("assign struct from dict (partial)", rot.Yaw == 45.0 and rot.Pitch == original["Rotation"]["Pitch"] and rot.Roll == original["Rotation"]["Roll"])

    gm.ReplicatedMovement.LinearVelocity.Y = 2.0
    gm.ReplicatedMovement.AngularVelocity = gm.ReplicatedMovement.LinearVelocity
    check("copy struct from another view", gm.ReplicatedMovement.AngularVelocity.Y == 2.0 and gm.ReplicatedMovement.AngularVelocity.X == 3.5)
    gm.ReplicatedMovement.AngularVelocity.Y = 8.0
    check("copied struct is independent", gm.ReplicatedMovement.LinearVelocity.Y == 2.0)

    old_phys, old_sleep = mv.bRepPhysics, mv.bSimulatedPhysicSleep
    mv.bRepPhysics = not old_phys
    check("write bool bit-field inside a struct", mv.bRepPhysics == (not old_phys))
    check("neighbouring bit-field untouched", mv.bSimulatedPhysicSleep == old_sleep)
    mv.bRepPhysics = old_phys
finally:
    gm.ReplicatedMovement.LinearVelocity = original["LinearVelocity"]
    gm.ReplicatedMovement.AngularVelocity = original["AngularVelocity"]
    gm.ReplicatedMovement.Rotation = original["Rotation"]
    gm.ReplicatedMovement.bRepPhysics = original["bRepPhysics"]
check("struct restored", gm.ReplicatedMovement.to_dict() == original)

raises("copy between different struct types", ue4ss.UEError, lambda: setattr(gm.ReplicatedMovement, "LinearVelocity", gm.ReplicatedMovement.Rotation))
raises("struct assigned an int", TypeError, lambda: setattr(gm.ReplicatedMovement, "LinearVelocity", 5))
raises("unknown field in dict", AttributeError, lambda: setattr(gm.ReplicatedMovement, "Rotation", {"Nope": 1}))
raises("wrong type in dict", TypeError, lambda: setattr(gm.ReplicatedMovement, "Rotation", {"Yaw": "x"}))
raises("missing field", AttributeError, lambda: gm.ReplicatedMovement.NoSuchField)
raises("view method is read-only", AttributeError, lambda: setattr(mv, "to_dict", 1))
raises("Struct cannot be constructed", TypeError, lambda: ue4ss.Struct())
raises("UObject cannot be constructed", TypeError, lambda: ue4ss.UObject())
check("views of the same place are equal", gm.ReplicatedMovement == gm.ReplicatedMovement and hash(gm.ReplicatedMovement) == hash(gm.ReplicatedMovement))
check("different places differ", gm.ReplicatedMovement.LinearVelocity != gm.ReplicatedMovement.AngularVelocity)
check("values intact after all errors", gm.ReplicatedMovement.to_dict() == original and gm.Owner == old_owner)
print("structs test finished")
