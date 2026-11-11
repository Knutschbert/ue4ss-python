# Demo mod: spawn an actor next to the player's pawn, make it bigger, tag it, teleport it, attach it to the pawn, then remove it.
# Every step is asserted, so this doubles as a test (test game: the DefaultPawn).
import ue4ss

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + ((" -- " + repr(detail)[:200]) if (detail != "" and not ok) else ""))


pawn = ue4ss.find_first_of("Pawn")
world = ue4ss.find_first_of("World")
statics = ue4ss.static_find_object("/Script/Engine.GameplayStatics")
mesh_actor = ue4ss.static_find_object("/Script/Engine.StaticMeshActor")

here = pawn.K2_GetActorLocation()
transform = {"Rotation": {"X": 0, "Y": 0, "Z": 0, "W": 1}, "Translation": {"X": here.X + 300, "Y": here.Y, "Z": here.Z + 100}, "Scale3D": {"X": 1, "Y": 1, "Z": 1}}

actor = statics.BeginDeferredActorSpawnFromClass(world, mesh_actor, transform, 1, None)   # 1 = always spawn
statics.FinishSpawningActor(actor, transform)
check("spawned an actor", actor is not None and actor.is_valid() and actor.is_a("/Script/Engine.StaticMeshActor"), actor)
check("it is where it was asked to be", abs(actor.K2_GetActorLocation().X - (here.X + 300)) < 1e-3, actor.K2_GetActorLocation().to_dict())

actor.RootComponent.SetMobility(2)                                                         # 2 = Movable (static actors cannot be moved)
actor.SetActorScale3D({"X": 2, "Y": 2, "Z": 2})                                            # bigger
scale = actor.GetActorScale3D()
check("scaled up", (scale.X, scale.Y, scale.Z) == (2.0, 2.0, 2.0), scale.to_dict())

actor.Tags.append("MyMod")                                                                 # set a value
check("tagged", [str(t) for t in actor.Tags.to_list()] == ["MyMod"], actor.Tags.to_list())

moved, _hit = actor.K2_SetActorLocation({"X": here.X + 600, "Y": here.Y + 50, "Z": here.Z + 200}, False, True)   # teleport
check("teleported", moved and abs(actor.K2_GetActorLocation().X - (here.X + 600)) < 1e-3, actor.K2_GetActorLocation().to_dict())

actor.K2_AttachToActor(pawn, "None", 1, 1, 1, False)                                       # attach (keep world transform)
check("attached to the pawn", actor.GetAttachParentActor() == pawn, actor.GetAttachParentActor())
check("it kept its world position", abs(actor.K2_GetActorLocation().X - (here.X + 600)) < 1e-3)

actor.K2_DestroyActor()                                                                    # clean up
check("destroyed again", not actor.is_valid() or actor.IsActorBeingDestroyed() if actor.is_valid() else True)
print(f"spawn demo finished PASS={sum(results)} FAIL={len(results) - sum(results)}")
