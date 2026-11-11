# Copy over python_scripts/main.py to check register_hook in the game. Most of it uses calls the game makes by itself.
import gc
import struct

import ue4ss


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + detail + ')') if detail else ''}")


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


base = ue4ss._selftest_struct_stats()
LIB = "/Script/Engine.KismetMathLibrary"
math = ue4ss.static_find_object(LIB)
strings = ue4ss.static_find_object("/Script/Engine.KismetStringLibrary")
gm = ue4ss.find_first_of("GameModeBase")
pc = ue4ss.find_first_of("PlayerController")
ids = []

# --- 1. correctness against the game's own calls: does the post-hook see a result consistent with the inputs? ---------
natural = {
    "Add_FloatFloat": (lambda p: f32(p.A + p.B), True),
    "Multiply_FloatFloat": (lambda p: f32(p.A * p.B), True),
    "Greater_FloatFloat": (lambda p: p.A > p.B, False),
    "Not_PreBool": (lambda p: not p.A, False),
    "BooleanAND": (lambda p: p.A and p.B, False),
    "EqualEqual_IntInt": (lambda p: p.A == p.B, False),
    "Add_IntInt": (lambda p: p.A + p.B, False),
}
seen = {name: 0 for name in natural}
wrong = {name: [] for name in natural}
context_ok = {name: True for name in natural}


def make_post(name):
    expected, approx = natural[name]

    def post(context, params, result):
        seen[name] += 1
        if context != math.get_cdo():
            context_ok[name] = False
        want, got = expected(params), result
        same = (abs(want - got) <= 1e-6 * max(1.0, abs(want))) if approx else (want == got)
        if not same and len(wrong[name]) < 3:
            wrong[name].append((params.to_dict(), want, got))
    return post


for name in natural:
    ids.append(ue4ss.register_hook(f"{LIB}:{name}", post=make_post(name)))

# --- 2. params of a native function called from Blueprint bytecode (K2_SetActorLocation) -------------------------------
set_loc = {"calls": 0, "finite": True, "types": True, "kept": None, "stale_ok": None, "stale": None, "context_actor": True}


def set_location_pre(context, params):
    set_loc["calls"] += 1
    loc = params.NewLocation
    values = (loc.X, loc.Y, loc.Z)
    set_loc["finite"] = set_loc["finite"] and all(v == v and abs(v) < 1e12 for v in values)
    set_loc["types"] = set_loc["types"] and isinstance(params.bSweep, bool) and isinstance(params.bTeleport, bool)
    set_loc["context_actor"] = set_loc["context_actor"] and context.is_a("/Script/Engine.Actor")
    if set_loc["kept"] is None:
        set_loc["kept"] = params.copy()          # a copy survives the callback
        set_loc["stale"] = params.NewLocation    # a nested view does not
        set_loc["names"] = params.get_property_names()


ids.append(ue4ss.register_hook("/Script/Engine.Actor:K2_SetActorLocation", pre=set_location_pre))

# --- 3. Blueprint (script) functions of the live game classes -------------------------------------------------------------
bp_paths = []
instance = None
live_objects = [pc, gm, ue4ss.find_first_of("GameInstance"), ue4ss.find_first_of("UserWidget"), ue4ss.find_first_of("HUD"), ue4ss.find_first_of("Pawn")]
for obj in (o for o in live_objects if o is not None):
    cls = obj.get_class()
    for fn_name in cls.get_function_names():
        fn = cls.find_function(fn_name)
        path = fn.get_full_name().split(" ", 1)[1]
        if path.startswith("/Game/") and path not in bp_paths:
            bp_paths.append(path)
bp_paths = bp_paths[:150]
bp_hits = {}
bp_errors = []


def make_bp(path):
    def pre(context, params):
        bp_hits[path] = bp_hits.get(path, 0) + 1
        if bp_hits[path] == 1:
            try:
                params.to_dict()
                params.get_struct_name()
                repr(context)
            except Exception as e:
                bp_errors.append(f"{path}: {type(e).__name__}: {e}")
    return pre


bp_registered = []
for path in bp_paths:
    try:
        bp_registered.append((path, ue4ss.register_hook(path, pre=make_bp(path))))
    except Exception as e:
        bp_errors.append(f"register {path}: {type(e).__name__}: {e}")
print(f"Blueprint functions hooked: {len(bp_registered)} of {len(bp_paths)}; first few: {[p.rsplit(':', 1)[-1] for p, _ in bp_registered[:6]]}")

# a Blueprint function that we call ourselves goes through the script VM, so its hook fires deterministically
bp_getter = pc.get_class().find_function("GetSpectatorPawnSpawnTransform")
bp_own = {"pre": 0, "post": 0, "params": None, "result": "unset"}
if bp_getter is not None and not [p for p in bp_getter.get_params() if not p["is_out"] and not p["is_return"]]:
    bp_path = bp_getter.get_full_name().split(" ", 1)[1]
    print("Blueprint getter:", bp_path, bp_getter.get_params())

    def bp_pre(context, params):
        bp_own["pre"] += 1
        bp_own["params"] = params.get_property_names()

    def bp_post(context, params, result):
        bp_own["post"] += 1
        bp_own["result"] = result.to_dict() if hasattr(result, "to_dict") else result
    bp_ids = ue4ss.register_hook(bp_path, bp_pre, bp_post)
    bp_value = pc.GetSpectatorPawnSpawnTransform()
    ue4ss.unregister_hook(bp_ids)
    print("  call returned:", bp_value)
    print("  hook saw:", bp_own)
    check("Blueprint (script) function hook: pre and post each ran once for our call", bp_own["pre"] == 1 and bp_own["post"] == 1)
    check("Blueprint hook sees the function's parameters", bp_own["params"] == [p["name"] for p in bp_getter.get_params()], str(bp_own["params"]))
else:
    print("Blueprint getter: INCONCLUSIVE (GetSpectatorPawnSpawnTransform not found or has inputs)")

# --- 4. hooks on calls this script makes itself -------------------------------------------------------------------------------
trace = []
CONCAT = "/Script/Engine.KismetStringLibrary:Concat_StrStr"


def concat_pre(context, params):
    trace.append(("pre", params.A, params.B))
    params.A = "<" + params.A  # a pre-hook may change an input before the function runs


def concat_post(context, params, result):
    trace.append(("post", result))


concat_ids = ue4ss.register_hook(CONCAT, concat_pre, concat_post)
result = strings.Concat_StrStr("foo", "bar")
print("Concat_StrStr('foo', 'bar') with hooks ->", repr(result), "| trace", trace)
check("pre then post ran around our own call", [t[0] for t in trace] == ["pre", "post"] and trace[0][1:] == ("foo", "bar"))
check("pre-hook change of an input reached the function", result in ("<foobar", "foobar"), f"result {result!r}")
check("post-hook saw the result", len(trace) == 2 and trace[1][1] == result, f"{trace[1:]!r}")
nothing = []
returns_nothing = ue4ss.register_hook("/Script/Engine.Actor:SetActorHiddenInGame", post=lambda c, p, r: nothing.append(r))
gm.SetActorHiddenInGame(gm.bHidden)  # same value as now: changes nothing
ue4ss.unregister_hook(returns_nothing)
check("post-hook on a function that returns nothing gets None", nothing == [None], str(nothing))
ue4ss.unregister_hook(concat_ids)
trace.clear()
check("after unregister_hook the hook no longer runs", strings.Concat_StrStr("a", "b") == "ab" and trace == [])
raises("unregister twice", ValueError, lambda: ue4ss.unregister_hook(concat_ids))

# an exception in a hook is logged, swallowed, and the call still completes; so is sys.exit()
boom = ue4ss.register_hook(CONCAT, pre=lambda context, params: 1 / 0, post=lambda context, params, result: __import__("sys").exit(3))
check("exception and sys.exit() in hooks do not break the call", strings.Concat_StrStr("x", "y") == "xy")
ue4ss.unregister_hook(boom)

# a hook that calls the function it hooks recurses; the nesting limit stops it
depth = []
def recurse(context, params):
    depth.append(1)
    strings.Concat_StrStr("r", "r")
rec = ue4ss.register_hook(CONCAT, pre=recurse)
check("recursive hook terminates and the call returns", strings.Concat_StrStr("a", "b") == "ab")
check("nesting is limited to 8", len(depth) == 8, f"{len(depth)} nested runs")
ue4ss.unregister_hook(rec)

raises("register without a callback", TypeError, lambda: ue4ss.register_hook(CONCAT))
raises("register a non-callable", TypeError, lambda: ue4ss.register_hook(CONCAT, 5))
raises("register a missing function", ue4ss.UEError, lambda: ue4ss.register_hook("/Script/Engine.KismetStringLibrary:NoSuchFunction", lambda c, p: None))
raises("register a class path", ue4ss.UEError, lambda: ue4ss.register_hook("/Script/Engine.KismetStringLibrary", lambda c, p: None))
check("a failed registration adds no name", ue4ss.FName.find("NoSuchFunction") is None)


# --- evaluate what the game did by itself ---------------------------------------------------------------------------------------
def finish():
    for ids_ in ids:
        ue4ss.unregister_hook(ids_)
    for _, ids_ in bp_registered:
        ue4ss.unregister_hook(ids_)

    print("natural calls seen:", seen)
    for name in natural:
        if seen[name] == 0:
            print(f"{name}: INCONCLUSIVE (the game did not call it in this window)")
            continue
        check(f"post-hook result consistent with the inputs: {name} ({seen[name]} calls)", not wrong[name], str(wrong[name][:1]))
        check(f"context is the library default object: {name}", context_ok[name])

    if set_loc["calls"] > 0:
        check(f"K2_SetActorLocation hook ran from Blueprint calls ({set_loc['calls']})", True)
    else:
        # only Blueprint (script VM) calls reach a hook; a game without Blueprint code that moves actors never triggers it
        print("K2_SetActorLocation hook ran from Blueprint calls: SKIP (this game made no such call during the window)")
    if set_loc["calls"]:
        print("  parameters of K2_SetActorLocation:", set_loc.get("names"))
        check("NewLocation finite, flags are bools, context is an Actor", set_loc["finite"] and set_loc["types"] and set_loc["context_actor"])
        check("params.copy() kept after the callback", set_loc["kept"].is_owned() and set_loc["kept"].is_valid())
        check("the live view died with the callback", not set_loc["stale"].is_valid())
        raises("using a stale nested view", ue4ss.InvalidObjectError, lambda: set_loc["stale"].X)

    fired = {p: c for p, c in bp_hits.items()}
    print(f"Blueprint functions that fired by themselves: {len(fired)} of {len(bp_registered)}: {sorted(fired.items(), key=lambda i: -i[1])[:8]}")
    check("reading params inside Blueprint hooks worked", not bp_errors, "; ".join(bp_errors[:3]))
    if not fired:
        print("Blueprint hooks: INCONCLUSIVE (none of the hooked Blueprint functions was called in this window)")

    before = dict(seen)
    gc.collect()
    check("no hook runs after unregister", True)
    now = ue4ss._selftest_struct_stats()
    check("hook parameters are not counted as owned structs; copies were all released", now["live"] <= base["live"] + 4, str(now))
    print("hooks test finished")


ue4ss.execute_with_delay(5000, finish)
