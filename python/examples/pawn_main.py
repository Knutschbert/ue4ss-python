# Reads and writes properties of the live player pawn: scalars, enums, structs, arrays, sets, object links and calls.
# Everything that is changed is restored. Needs a game with a possessed pawn (the test game has a DefaultPawn).
import enum
import ue4ss

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + ((" -- " + repr(detail)[:200]) if (detail != "" and not ok) else ""))


pc = ue4ss.find_first_of("PlayerController")
pawn = pc.Pawn if pc is not None else None
if pawn is None:
    print("pawn checks: SKIP (no possessed pawn in this game yet)")
    print("pawn checks finished PASS=0 FAIL=0")
else:
    print("pawn:", pawn.get_full_name())
    check("controller and pawn point at each other", pawn.Controller == pc and pc.Pawn == pawn)
    check("the pawn is an Actor and a Pawn", pawn.is_a("/Script/Engine.Pawn") and pawn.is_a("/Script/Engine.Actor"))

    # scalars: float, bool, int, enum
    eye = pawn.BaseEyeHeight
    pawn.BaseEyeHeight = eye + 12.5
    check("float write", abs(pawn.BaseEyeHeight - (eye + 12.5)) < 1e-4, pawn.BaseEyeHeight)
    pawn.BaseEyeHeight = eye
    check("float restored", pawn.BaseEyeHeight == eye)

    yaw = pawn.bUseControllerRotationYaw
    pawn.bUseControllerRotationYaw = not yaw
    check("bool write", pawn.bUseControllerRotationYaw is (not yaw))
    pawn.bUseControllerRotationYaw = yaw
    check("bool restored", pawn.bUseControllerRotationYaw is yaw)

    life = pawn.InitialLifeSpan
    pawn.InitialLifeSpan = 123.5
    check("float property InitialLifeSpan", pawn.InitialLifeSpan == 123.5)
    pawn.InitialLifeSpan = life

    role = pawn.Role
    check("replication role reads as an enum member", isinstance(role, enum.IntEnum) and role.name.startswith("ROLE_"), role)
    auto = pawn.AutoPossessAI
    check("enum property reads as a member", isinstance(auto, enum.IntEnum), auto)

    # structs: nested views and owned copies
    movement = pawn.ReplicatedMovement
    check("struct view reads", isinstance(movement, ue4ss.Struct) and hasattr(movement, "LinearVelocity"), movement)
    velocity = movement.LinearVelocity
    old = (velocity.X, velocity.Y, velocity.Z)
    velocity.X = 11.0
    velocity.Z = -3.5
    check("nested struct write goes through the view", pawn.ReplicatedMovement.LinearVelocity.X == 11.0 and pawn.ReplicatedMovement.LinearVelocity.Z == -3.5)
    snapshot = pawn.ReplicatedMovement.copy()
    velocity.X, velocity.Z = old[0], old[2]
    check("an owned copy keeps the old values", snapshot.LinearVelocity.X == 11.0 and pawn.ReplicatedMovement.LinearVelocity.X == old[0])
    pawn.ReplicatedMovement.LinearVelocity = {"X": 1.0, "Y": 2.0, "Z": 3.0}
    check("struct assignment from a dict", (pawn.ReplicatedMovement.LinearVelocity.X, pawn.ReplicatedMovement.LinearVelocity.Y, pawn.ReplicatedMovement.LinearVelocity.Z) == (1.0, 2.0, 3.0))
    pawn.ReplicatedMovement.LinearVelocity = {"X": old[0], "Y": old[1], "Z": old[2]}

    # the root component's location, as a struct in another object
    root = pawn.RootComponent
    check("object property: the root component", root is not None and root.is_a("/Script/Engine.SceneComponent"), root)
    where = root.RelativeLocation
    start = (where.X, where.Y, where.Z)
    where.X = start[0] + 40.0
    check("a struct write through the view changes the component's property", abs(root.RelativeLocation.X - (start[0] + 40.0)) < 1e-4, root.RelativeLocation.to_dict())
    # documented sharp edge: a direct write skips the engine's setters, so the cached world transform does not follow
    check("a direct write does not run the engine setter (the world location did not follow)", abs(pawn.K2_GetActorLocation().X - start[0]) < 1e-3, pawn.K2_GetActorLocation().to_dict())
    where.X = start[0]
    check("property restored", root.RelativeLocation.X == start[0])

    # arrays and sets
    tags = pawn.Tags
    before = tags.to_list()
    tags.append("PawnTagOne")
    tags.extend(["PawnTagTwo", "PawnTagThree"])
    check("array of names grows on the live pawn", [str(t) for t in tags.to_list()][-3:] == ["PawnTagOne", "PawnTagTwo", "PawnTagThree"])
    tags.pop()
    del tags[-1]
    tags.pop()
    check("array restored", tags.to_list() == before)
    components = pawn.K2_GetComponentsByClass(ue4ss.static_find_object("/Script/Engine.ActorComponent"))
    check("function returning an array of components", isinstance(components, ue4ss.Array) and len(components) >= 1 and root in components.to_list(), len(components))
    check("components are real component objects", all(c.is_valid() and c.is_a("/Script/Engine.ActorComponent") for c in components))
    ids = pawn.Tags
    check("TArray<FName> property on the pawn is an Array", isinstance(ids, ue4ss.Array))
    instance = pawn.InstanceComponents
    check("array of object references reads", len(instance) >= 0 and all(c.is_valid() for c in instance))

    # calls with structs in and out
    moved, hit = pawn.K2_SetActorLocation({"X": start[0] + 5.0, "Y": start[1], "Z": start[2]}, False, True)
    check("K2_SetActorLocation: result and out struct", moved is True and isinstance(hit, ue4ss.Struct), (moved, hit))
    check("the pawn moved", abs(pawn.K2_GetActorLocation().X - (start[0] + 5.0)) < 1e-3)
    pawn.K2_SetActorLocation({"X": start[0], "Y": start[1], "Z": start[2]}, False, True)
    check("moved back", abs(pawn.K2_GetActorLocation().X - start[0]) < 1e-3)
    check("class query through the object model", pawn.get_class().get_fname().to_string() == "DefaultPawn" and pawn.get_class().is_child_of("/Script/Engine.Pawn"))

    print(f"pawn checks finished PASS={sum(results)} FAIL={len(results) - sum(results)}")
