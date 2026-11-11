# For a person: run the test game (or any game) WITH a window, give it the focus and press the keys below. Every press prints one line
# to UE4SS.log (and the UE4SS console if enabled). Not run by the test runner (kind probe).
#
#   python3 -I tools/testgame.py run python/examples/keys_manual_main.py "no such marker" --windowed --timeout 300
#
# Keys: F6, Ctrl+F6, Shift+F6, Ctrl+Shift+F6 (each is a separate binding: only the exact combination fires), and F7 which
# unregisters F6 so that F6 stops answering.
import ue4ss
from ue4ss import Key, ModifierKey

print("keys_manual: press F6, Ctrl+F6, Shift+F6, Ctrl+Shift+F6, F7 (the game window needs the focus)")


def press(label):
    return lambda: print(f"KEY {label} pressed (on game thread: {ue4ss.is_game_thread()})")


ids = [
    ue4ss.register_key_down(Key.F6, press("F6")),
    ue4ss.register_key_down(Key.F6, press("Ctrl+F6"), modifiers=(ModifierKey.CONTROL,)),
    ue4ss.register_key_down(Key.F6, press("Shift+F6"), modifiers=(ModifierKey.SHIFT,)),
    ue4ss.register_key_down(Key.F6, press("Ctrl+Shift+F6"), modifiers=(ModifierKey.CONTROL, ModifierKey.SHIFT)),
]


def stop_f6():
    for binding in ids:
        try:
            ue4ss.unregister_key_down(binding)
        except ValueError:
            pass
    print("KEY F7 pressed: F6 bindings removed")


ue4ss.register_key_down(Key.F7, stop_f6)
