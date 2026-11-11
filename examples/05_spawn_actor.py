# 05 - Spawn an actor in front of the player, change it, and remove it again with a key.
import ue4ss
from ue4ss import Key

ue4ss.register_key_down(Key.F5, ue4ss.reload)

spawned: list = []                      # the actors this script made


def spawn_cube():
    controller = ue4ss.find_first_of("PlayerController")
    pawn = controller.Pawn if controller is not None else None
    if pawn is None:
        print("no pawn: run this in a level")
        return
    statics = ue4ss.static_find_object("/Script/Engine.GameplayStatics")      # a library class: its functions are called on the class
    actor_class = ue4ss.static_find_object("/Script/Engine.StaticMeshActor")
    here = pawn.K2_GetActorLocation()                                          # a struct value with X, Y, Z
    forward = pawn.GetActorForwardVector()
    # Structs are passed as dicts of their fields.
    transform = {"Rotation": {"X": 0, "Y": 0, "Z": 0, "W": 1},
                 "Translation": {"X": here.X + forward.X * 300, "Y": here.Y + forward.Y * 300, "Z": here.Z},
                 "Scale3D": {"X": 1, "Y": 1, "Z": 1}}
    actor = statics.BeginDeferredActorSpawnFromClass(pawn, actor_class, transform, 1, None)   # 1 = always spawn, ignore collisions
    statics.FinishSpawningActor(actor, transform)
    actor.RootComponent.SetMobility(2)                                         # 2 = Movable; a static actor cannot be moved or scaled
    cube = ue4ss.static_find_object("/Engine/BasicShapes/Cube.Cube")            # an engine mesh most games ship
    if cube is not None:
        actor.StaticMeshComponent.SetStaticMesh(cube)
    actor.SetActorScale3D({"X": 0.5, "Y": 0.5, "Z": 0.5})
    actor.Tags.append("SpawnedByPython")                                       # arrays behave like lists
    spawned.append(actor)
    print("spawned", actor.get_full_name())


def remove_all():
    for actor in spawned:
        if actor.is_valid():                    # the game may have destroyed it already (level change)
            actor.K2_DestroyActor()
    print("removed", len(spawned), "actors")
    spawned.clear()


ue4ss.register_key_down(Key.F6, spawn_cube)
ue4ss.register_key_down(Key.F7, remove_all)
print("F6 spawns a cube in front of you, F7 removes the cubes")
