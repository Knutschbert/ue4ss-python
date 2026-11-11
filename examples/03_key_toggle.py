# 03 - A key that changes something in the game: F6 hides and shows the player's pawn.
import ue4ss
from ue4ss import Key

ue4ss.register_key_down(Key.F5, ue4ss.reload)


def toggle_pawn():
    # Look the objects up when the key is pressed, not once at start: the pawn changes on respawn and level change,
    # and a stored object can be destroyed by the game (using it would raise ue4ss.InvalidObjectError).
    controller = ue4ss.find_first_of("PlayerController")
    pawn = controller.Pawn if controller is not None else None
    if pawn is None:
        print("no pawn to hide")
        return
    hidden = not pawn.bHidden                   # read a property
    pawn.SetActorHiddenInGame(hidden)           # call a function
    print("pawn hidden:", hidden)


# The callback runs on the game thread, so it may touch game objects.
ue4ss.register_key_down(Key.F6, toggle_pawn)
print("F6 hides and shows your pawn")
