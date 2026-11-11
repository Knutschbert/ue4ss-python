# Copy over python_scripts/main.py to check overriding results and inputs from hooks in the game.
# Own calls give definitive proof; for calls the game makes by itself a tiny, recognisable 2**-10 shift is used for
# a few seconds, and later hooks must see exactly the shifted values.
import struct

import ue4ss


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + detail + ')') if detail else ''}")


math = ue4ss.static_find_object("/Script/Engine.KismetMathLibrary")
strings = ue4ss.static_find_object("/Script/Engine.KismetStringLibrary")
gameplay = ue4ss.static_find_object("/Script/Engine.GameplayStatics")
gm = ue4ss.find_first_of("GameModeBase")
pc = ue4ss.find_first_of("PlayerController")
LIB, STR, GP = "/Script/Engine.KismetMathLibrary", "/Script/Engine.KismetStringLibrary", "/Script/Engine.GameplayStatics"

# --- 1. overriding the result of our own calls ---------------------------------------------------------------
ids = ue4ss.register_hook(f"{STR}:Concat_StrStr", post=lambda context, params, result: "OVERRIDE h\u00e9llo \U0001F600")
check("string result overridden (non-ASCII)", strings.Concat_StrStr("a", "b") == "OVERRIDE h\u00e9llo \U0001F600")
ue4ss.unregister_hook(ids)
check("and the real result returns once unregistered", strings.Concat_StrStr("a", "b") == "ab")

ids = ue4ss.register_hook(f"{STR}:Concat_StrStr", post=lambda context, params, result: None)
check("returning None keeps the result", strings.Concat_StrStr("a", "b") == "ab")
ue4ss.unregister_hook(ids)

ids = ue4ss.register_hook(f"{LIB}:Add_IntInt", post=lambda context, params, result: result * 10)
check("int result overridden using the original result", math.Add_IntInt(2, 3) == 50)
ue4ss.unregister_hook(ids)

ids = ue4ss.register_hook(f"{LIB}:Add_VectorVector", post=lambda context, params, result: {"X": 9.0, "Y": 8.0, "Z": 7.0})
check("struct result overridden from a dict", math.Add_VectorVector({"X": 1.0}, {"X": 1.0}).to_dict() == {"X": 9.0, "Y": 8.0, "Z": 7.0})
ue4ss.unregister_hook(ids)
owned = ue4ss.make_struct("/Script/CoreUObject.Vector", X=5.0, Y=6.0, Z=7.0)
ids = ue4ss.register_hook(f"{LIB}:Add_VectorVector", post=lambda context, params, result: owned)
check("struct result overridden from a Struct", math.Add_VectorVector({}, {}).to_dict() == {"X": 5.0, "Y": 6.0, "Z": 7.0})
ue4ss.unregister_hook(ids)

check("object result before", gameplay.GetGameMode(gm) == gm)
ids = ue4ss.register_hook(f"{GP}:GetGameMode", post=lambda context, params, result: ue4ss.NULL)
check("object result overridden with ue4ss.NULL", gameplay.GetGameMode(gm) is None)
ue4ss.unregister_hook(ids)
ids = ue4ss.register_hook(f"{GP}:GetGameMode", post=lambda context, params, result: pc)  # a controller is not a game mode
check("an object of the wrong class is rejected and the original result stays", gameplay.GetGameMode(gm) == gm)
ue4ss.unregister_hook(ids)
check("after unregistering, the real object is back", gameplay.GetGameMode(gm) == gm)

ids = ue4ss.register_hook(f"{LIB}:Add_IntInt", post=lambda context, params, result: 2**31)
check("an out-of-range override is reported and the original result stays", math.Add_IntInt(2, 3) == 5)
ue4ss.unregister_hook(ids)
ids = ue4ss.register_hook(f"{LIB}:Add_IntInt", post=lambda context, params, result: "text")
check("a wrongly typed override is reported and the original result stays", math.Add_IntInt(2, 3) == 5)
ue4ss.unregister_hook(ids)

ids = ue4ss.register_hook(f"{LIB}:Add_IntInt", pre=lambda context, params: 123)
check("a value returned from a pre-hook is ignored", math.Add_IntInt(2, 3) == 5)
ue4ss.unregister_hook(ids)
old_hidden = gm.bHidden
ids = ue4ss.register_hook("/Script/Engine.Actor:SetActorHiddenInGame", post=lambda context, params, result: 5)
check("returning a value from a function with no result is ignored", gm.SetActorHiddenInGame(old_hidden) is None)
ue4ss.unregister_hook(ids)

# --- 2. inputs and output parameters on our own calls --------------------------------------------------------------
ids = ue4ss.register_hook(f"{LIB}:Add_IntInt", pre=lambda context, params: setattr(params, "A", 100))
check("a pre-hook can change an input", math.Add_IntInt(1, 2) == 102)
ue4ss.unregister_hook(ids)

ids = ue4ss.register_hook(f"{LIB}:BreakVector", post=lambda context, params, result: setattr(params, "X", 42.0))
outputs = math.BreakVector({"X": 1.0, "Y": 2.0, "Z": 3.0})
check("a post-hook can change an output parameter (BreakVector X)", outputs == (42.0, 2.0, 3.0), str(outputs))
ue4ss.unregister_hook(ids)

# --- 3. the game's own calls: tiny recognisable perturbations ----------------------------------------------------------------
SHIFT = 2.0 ** -10
mul = {"calls": 0, "bad": []}
add = {"calls": 0, "bad": []}


def multiply_shift(context, params, result):
    return result + SHIFT                      # hook 1: override the real result


def multiply_check(context, params, result):   # hook 2, registered after hook 1: sees what hook 1 wrote
    mul["calls"] += 1
    want = f32(f32(params.A * params.B) + SHIFT)
    if result != want and len(mul["bad"]) < 3:
        mul["bad"].append((params.A, params.B, result, want))


def add_shift_input(context, params):
    params.B = params.B + SHIFT                # hook 3: change an input before the native function runs


def add_check(context, params, result):        # hook 4: the result must match the CHANGED input
    add["calls"] += 1
    want = f32(params.A + params.B)
    if abs(result - want) > 1e-6 * max(1.0, abs(want)) and len(add["bad"]) < 3:
        add["bad"].append((params.A, params.B, result, want))


natural_ids = [
    ue4ss.register_hook(f"{LIB}:Multiply_FloatFloat", post=multiply_shift),
    ue4ss.register_hook(f"{LIB}:Multiply_FloatFloat", post=multiply_check),
    ue4ss.register_hook(f"{LIB}:Add_FloatFloat", pre=add_shift_input),
    ue4ss.register_hook(f"{LIB}:Add_FloatFloat", post=add_check),
]


def finish():
    for ids_ in natural_ids:
        ue4ss.unregister_hook(ids_)
    print(f"natural calls: Multiply_FloatFloat {mul['calls']}, Add_FloatFloat {add['calls']}")
    if mul["calls"]:
        check("a post-hook override reaches later hooks on the game's own calls", not mul["bad"], str(mul["bad"][:1]))
    else:
        print("Multiply_FloatFloat: INCONCLUSIVE (the game did not call it in this window)")
    if add["calls"]:
        check("a pre-hook input change reaches the native function on the game's own calls", not add["bad"], str(add["bad"][:1]))
    else:
        print("Add_FloatFloat: INCONCLUSIVE (the game did not call it in this window)")
    check("the game is back to normal after unregistering", math.Multiply_FloatFloat(2.0, 3.0) == 6.0 and math.Add_FloatFloat(1.0, 2.0) == 3.0)
    check("game object untouched", gm.bHidden == old_hidden)
    print("override test finished")


ue4ss.execute_with_delay(4000, finish)
