# 07 - The typed layer: engine classes as Python types, with editor completion.
#
# `ue4ss.engine` creates Python types from the game's reflection data. Together with generated typings, an editor
# completes members and flags mistakes before the game starts (docs/TYPED_API.md, docs/VSCODE.md).
#
# Generate the typings once (and again after a game update); here on F8, into a folder of your choice:
import ue4ss
from ue4ss import Key, engine
from ue4ss.engine import Actor, PlayerController

ue4ss.register_key_down(Key.F5, ue4ss.reload)

STUBS_FOLDER = r"C:\dev\ue4ss-python\stubs"     # the folder pyrightconfig.json names as stubPath


def write_typings():
    summary = engine.generate_stubs(STUBS_FOLDER)
    print("typings written:", summary["classes"], "classes in", summary["packages"], "packages")


ue4ss.register_key_down(Key.F8, write_typings)

controller = PlayerController.find_first()       # typed: PlayerController | None
if controller is not None and controller.Pawn is not None:
    here = controller.Pawn.K2_GetActorLocation()  # typed: Vector
    print("player at", here.X, here.Y, here.Z)

print(len(Actor.find_all()), "actors;", "F8 writes typings to", STUBS_FOLDER)
