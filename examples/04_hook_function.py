# 04 - Hook a function: be called whenever the game (or a script) calls it.
#
# The path is "<class path>:<function name>". Native functions live under /Script/<Module>, Blueprint functions under
# /Game/... (see docs/BLUEPRINT_HOOKS.md). 02_inspect_game.py shows which functions a class has.
import ue4ss
from ue4ss import Key

ue4ss.register_key_down(Key.F5, ue4ss.reload)

calls = {"count": 0}


def before(context, params):
    """Runs before the function. context is the object it is called on, params its arguments."""
    calls["count"] += 1
    if calls["count"] <= 20:                    # hooks can fire very often: do not flood the log
        where = params.NewLocation
        print(f"{context.get_fname().to_string()} moves to ({where.X:.0f}, {where.Y:.0f}, {where.Z:.0f})")
    # params is only valid inside this function. To keep a value, copy it: saved = params.NewLocation.copy()


def after(context, params, result):
    """Runs after the function; result is its return value. Returning something replaces the result."""
    return result


hook_ids = ue4ss.register_hook("/Script/Engine.Actor:K2_SetActorLocation", before, after)
print("hooked K2_SetActorLocation; F7 removes the hook")


def remove():
    ue4ss.unregister_hook(hook_ids)
    print("hook removed after", calls["count"], "calls")


ue4ss.register_key_down(Key.F7, remove)
