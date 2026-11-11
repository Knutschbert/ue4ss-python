# Hooks see and change OUTPUT parameters of the call: they live in the caller's memory (FFrame::OutParms), not in the hook's own
# copy of the parameters. Scalars, strings, structs and arrays; KismetMathLibrary.BreakVector works in every game.
import ue4ss

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + ((" -- " + repr(detail)[:200]) if (detail != "" and not ok) else ""))


math = ue4ss.static_find_object("/Script/Engine.KismetMathLibrary")
BREAK = "/Script/Engine.KismetMathLibrary:BreakVector"

# --- BreakVector (three float outputs, ProcessEvent-called native) ---
seen = {}
ids = ue4ss.register_hook(BREAK, post=lambda context, params, result: seen.update(x=params.X, y=params.Y, z=params.Z))
outputs = math.BreakVector({"X": 1.0, "Y": 2.0, "Z": 3.0})
ue4ss.unregister_hook(ids)
check("a post-hook reads the real output values, not stale ones", (seen.get("x"), seen.get("y"), seen.get("z")) == (1.0, 2.0, 3.0), seen)
check("and the caller's result is unchanged", outputs == (1.0, 2.0, 3.0), outputs)

ids = ue4ss.register_hook(BREAK, post=lambda context, params, result: setattr(params, "Y", 42.0))
outputs = math.BreakVector({"X": 1.0, "Y": 2.0, "Z": 3.0})
ue4ss.unregister_hook(ids)
check("a post-hook can change an output parameter", outputs == (1.0, 42.0, 3.0), outputs)
check("normal again after unregistering", math.BreakVector({"X": 1.0, "Y": 2.0, "Z": 3.0}) == (1.0, 2.0, 3.0))

# --- a function with struct, array, string and int outputs (test game) ---
actor = ue4ss.find_first_of("PyTestActor")
if actor is None:
    print("struct, array and string outputs: SKIP (no PyTestActor in this game)")
else:
    OUT = "/Script/PyTestGame.PyTestActor:OutParts"
    plain = actor.OutParts(3)
    check("OutParts returns struct, array, string and int", isinstance(plain[0], ue4ss.Struct) and plain[1].to_list() == [0, 1, 4] and plain[2] == "parts 3" and plain[3] == 3,
          plain)

    seen = {}
    def look(context, params, result):
        seen["vector"] = (params.OutVector.X, params.OutVector.Y, params.OutVector.Z)
        seen["numbers"] = params.OutNumbers.to_list()
        seen["text"] = params.OutText
        seen["count"] = params.OutCount
    ids = ue4ss.register_hook(OUT, post=look)
    actor.OutParts(4)
    ue4ss.unregister_hook(ids)
    check("a post-hook reads struct, array, string and int outputs", seen == {"vector": (4.0, 8.0, 12.0), "numbers": [0, 1, 4, 9], "text": "parts 4", "count": 4}, seen)

    def change(context, params, result):
        params.OutCount = 99
        params.OutText = "changed text"
        params.OutVector.X = 123.0
        params.OutNumbers.append(7)
    ids = ue4ss.register_hook(OUT, post=change)
    vector, numbers, text, count = actor.OutParts(3)
    ue4ss.unregister_hook(ids)
    check("scalar output changed by a post-hook", count == 99, count)
    check("string output changed by a post-hook", text == "changed text", text)
    check("a field of a struct output changed through the view", (vector.X, vector.Y, vector.Z) == (123.0, 6.0, 9.0), vector.to_dict())
    check("an array output resized by a post-hook", numbers.to_list() == [0, 1, 4, 7], numbers.to_list())

    ids = ue4ss.register_hook(OUT, post=lambda context, params, result: setattr(params, "OutVector", {"X": -1.0, "Y": -2.0, "Z": -3.0}))
    vector = actor.OutParts(2)[0]
    ue4ss.unregister_hook(ids)
    check("a whole struct output assigned from a dict", (vector.X, vector.Y, vector.Z) == (-1.0, -2.0, -3.0), vector.to_dict())

    ids = ue4ss.register_hook(OUT, post=lambda context, params, result: setattr(params, "OutNumbers", [5, 6]))
    numbers = actor.OutParts(2)[1]
    ue4ss.unregister_hook(ids)
    check("a whole array output assigned from a list", numbers.to_list() == [5, 6], numbers.to_list())

    # the hook's view dies with the callback, as before
    kept = []
    ids = ue4ss.register_hook(OUT, post=lambda context, params, result: kept.append(params))
    actor.OutParts(1)
    ue4ss.unregister_hook(ids)
    try:
        kept[0].OutCount
        check("the parameter view is dead after the callback", False, "no exception")
    except ue4ss.InvalidObjectError:
        check("the parameter view is dead after the callback", True)
    check("normal outputs afterwards", actor.OutParts(3)[3] == 3 and actor.OutParts(3)[2] == "parts 3")

print(f"hook out params finished PASS={sum(results)} FAIL={len(results) - sum(results)}")
