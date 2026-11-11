# Calls engine functions whose parameters or results are arrays and structs (static library functions through their class).
import ue4ss

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (" -- " + str(detail)[:220] if detail else ""))


def attempt(name, fn):
    try:
        return fn()
    except Exception as e:
        check(name, False, repr(e))
        return None


strings = ue4ss.static_find_object("/Script/Engine.KismetStringLibrary")
math = ue4ss.static_find_object("/Script/Engine.KismetMathLibrary")
statics = ue4ss.static_find_object("/Script/Engine.GameplayStatics")
check("libraries found", None not in (strings, math, statics))

# array result (return value)
parts = attempt("ParseIntoArray", lambda: strings.ParseIntoArray("a,b,,c", ",", True))
if parts is not None:
    print("ParseIntoArray ->", parts)
    check("array return value is an Array", isinstance(parts, ue4ss.Array), type(parts))
    check("array return value reads", parts is not None and parts.to_list() == ["a", "b", "c"], parts.to_list())
    kept = attempt("ParseIntoArray keep empties", lambda: strings.ParseIntoArray("a,b,,c", ",", False))
    check("culling flag respected", kept is not None and kept.to_list() == ["a", "b", "", "c"], kept and kept.to_list())
    # the returned array owns its memory: usable and resizable after the call
    parts.append("d")
    check("returned array can be resized", parts.to_list() == ["a", "b", "c", "d"])

# array argument
joined = attempt("JoinStringArray from a list", lambda: strings.JoinStringArray(["x", "y", "z"], "-"))
check("array argument converted", joined == "x-y-z", joined)
joined2 = attempt("JoinStringArray from a returned Array", lambda: strings.JoinStringArray(parts, "+") if parts is not None else None)
check("an Array can be passed back as an argument", joined2 == "a+b+c+d", joined2)
joined3 = attempt("JoinStringArray empty", lambda: strings.JoinStringArray([], "-"))
check("empty array argument", joined3 == "", repr(joined3))
for label, bad, exc in (("not iterable", 5, TypeError), ("wrong element type", [1, 2], TypeError)):
    try:
        strings.JoinStringArray(bad, "-")
        check("bad array argument: " + label, False, "no exception")
    except exc:
        check("bad array argument: " + label, True)
    except Exception as e:
        check("bad array argument: " + label, False, repr(e))

# array of structs as an argument, struct as the result
vectors = [ue4ss.make_struct("/Script/CoreUObject.Vector", X=float(i), Y=2.0 * i, Z=0.0) for i in range(1, 4)]
average = attempt("GetVectorArrayAverage", lambda: math.GetVectorArrayAverage(vectors))
if average is not None:
    check("array of structs in, struct out", isinstance(average, ue4ss.Struct) and (average.X, average.Y, average.Z) == (2.0, 4.0, 0.0), average and average.to_dict())
    check("result struct is owned", average.is_owned())

# output array: all actors of a class in a world
world = ue4ss.find_first_of("World")
if world is not None:
    actors = attempt("GetAllActorsOfClass", lambda: statics.GetAllActorsOfClass(world, ue4ss.static_find_object("/Script/Engine.Actor")))
    print("GetAllActorsOfClass ->", actors)
    check("output array is an Array", isinstance(actors, ue4ss.Array), type(actors))
    if isinstance(actors, ue4ss.Array):
        items = actors.to_list()
        check("actors are objects", all(isinstance(a, ue4ss.UObject) for a in items), len(items))
        check("every actor is an Actor", all(a.is_a("/Script/Engine.Actor") for a in items), len(items))
        direct = ue4ss.find_all_of("Actor")
        check("agrees with find_all_of", len(items) <= len(direct) + 1 and len(items) > 0, (len(items), len(direct)))
else:
    print("note: no World found")

# a call that fails must not leak or corrupt: repeat many times, memory stays stable
stats_before = ue4ss._selftest_struct_stats()
for i in range(300):
    strings.JoinStringArray(["a"] * 20, ",")
    strings.ParseIntoArray("a,b,c,d,e,f", ",", True)
stats_after = ue4ss._selftest_struct_stats()
live_before = stats_before["created"] - stats_before["destroyed"]
live_after = stats_after["created"] - stats_after["destroyed"]
ue4ss.execute_with_delay(500, lambda: print("live blocks", live_before, "->", ue4ss._selftest_struct_stats()["created"] - ue4ss._selftest_struct_stats()["destroyed"]))
check("600 calls with array arguments and results ran", True, (stats_before, stats_after))
check("no owned-struct leaks reported", stats_after["leaked_block"] == 0 and stats_after["leaked_inner"] == 0, stats_after)

print(f"call containers finished PASS={sum(results)} FAIL={len(results) - sum(results)}")
