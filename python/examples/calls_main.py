# Copy over python_scripts/main.py to check calling engine functions from Python in the game.
# Order: pure static functions, read-only getters, one reversible change, then the error paths.
import gc
import threading

import ue4ss


def check(label, ok):
    print(f"{label}: {'PASS' if ok else 'FAIL'}")


def raises(label, exc, fn, message=None):
    try:
        fn()
    except exc as e:
        if message is not None and str(e) != message:
            print(f"{label}: FAIL (message was {str(e)!r}, expected {message!r})")
            return
        print(f"{label}: PASS ({type(e).__name__}: {e})")
        return
    except BaseException as e:
        print(f"{label}: FAIL (got {type(e).__name__}: {e})")
        return
    print(f"{label}: FAIL (no exception)")


def find(path):
    found = ue4ss.static_find_object(path)
    if found is None:
        print(f"FAIL: {path} not found")
    return found


gm = ue4ss.find_first_of("GameModeBase")
pc = ue4ss.find_first_of("PlayerController")
base = ue4ss._selftest_struct_stats()

# --- 1. pure static functions through the class object -------------------------------------------------
math = find("/Script/Engine.KismetMathLibrary")
strings = find("/Script/Engine.KismetStringLibrary")
check("int add", math.Add_IntInt(2, 3) == 5)
check("keywords", math.Add_IntInt(B=1, a=9) == 10)
check("float multiply", math.Multiply_FloatFloat(0.5, 4.0) == 2.0)
check("bool results", math.Greater_IntInt(3, 2) is True and math.Greater_IntInt(2, 3) is False and math.Not_PreBool(True) is False)
check("string result", strings.Concat_StrStr("foo", "bar") == "foobar")
check("int to string", strings.Conv_IntToString(42) == "42")
check("non-ASCII string through a call", strings.Concat_StrStr("h\u00e9llo ", "\U0001F600 \u2713") == "h\u00e9llo \U0001F600 \u2713")
check("name in, string out", strings.Conv_NameToString("Spectating") == "Spectating")
sum_vec = math.Add_VectorVector({"X": 1.0, "Y": 2.0, "Z": 3.0}, {"X": 1.0, "Y": 1.0, "Z": 1.0})
print("Add_VectorVector ->", sum_vec, sum_vec.to_dict())
check("struct in and out", sum_vec.to_dict() == {"X": 2.0, "Y": 3.0, "Z": 4.0} and sum_vec.is_owned())
check("float result from a struct", math.VSize({"X": 3.0, "Y": 4.0, "Z": 0.0}) == 5.0)
parts = math.BreakVector({"X": 1.0, "Y": 2.0, "Z": 3.0})
print("BreakVector ->", parts)
check("output parameters come back as a tuple", parts == (1.0, 2.0, 3.0))
check("through the class default object too", math.get_cdo().Add_IntInt(2, 3) == 5)
check("call_function by name", math.call_function("Add_IntInt", 1, 1) == 2)
check("call_function with a UFunction", math.call_function(math.find_function("Add_IntInt"), 4, 5) == 9)
check("bound function repr", repr(math.Add_IntInt) == "<BoundFunction 'Add_IntInt'>")
check("a bound function can be called repeatedly", all((lambda f: [f(i, 1) for i in range(100)] == [i + 1 for i in range(100)])(math.Add_IntInt) for _ in range(3)))

# --- 2. read-only instance functions -----------------------------------------------------------------
location = gm.K2_GetActorLocation()
print("gm location:", location.to_dict())
check("struct return value from an instance function", set(location.to_dict()) == {"X", "Y", "Z"})
check("bool return value", gm.HasAuthority() is True)
check("object return value None", gm.GetOwner() is None)
check("float return value", isinstance(gm.GetActorTimeDilation(), float))
distance = gm.GetDistanceTo(pc)
print("distance gm -> pc:", distance)
check("object argument", isinstance(distance, float) and distance >= 0.0)
check("None as an object argument", gm.GetDistanceTo(None) == 0.0)

gameplay = find("/Script/Engine.GameplayStatics")
check("object in, object out (GetGameMode)", gameplay.GetGameMode(gm) == gm)
check("object out (GetPlayerController)", gameplay.GetPlayerController(gm, 0) == pc)
instance = gameplay.GetGameInstance(gm)
print("game instance:", instance)
check("object out of another class", instance is not None and instance.is_a("/Script/Engine.GameInstance"))

# --- 3. one reversible change ------------------------------------------------------------------------------
old = gm.bHidden
try:
    check("call with no result returns None", gm.SetActorHiddenInGame(not old) is None)
    check("the call changed the game", gm.bHidden == (not old))
finally:
    gm.SetActorHiddenInGame(old)
check("and it is restored", gm.bHidden == old)

# --- 4. error paths: all of these must fail BEFORE reaching the engine -----------------------------------
actor_class = find("/Script/Engine.Actor")
raises("too many arguments", TypeError, lambda: math.Add_IntInt(1, 2, 3), "Add_IntInt() takes 2 positional arguments but 3 were given")
raises("missing argument", TypeError, lambda: math.Add_IntInt(1), "Add_IntInt() missing 1 required argument: 'B'")
raises("output cannot be passed", TypeError, lambda: math.Add_IntInt(1, 2, ReturnValue=3), "Add_IntInt() 'ReturnValue' is an output of the function and cannot be passed")
raises("bad value", OverflowError, lambda: math.Add_IntInt(2**31, 1))
raises("instance function on a class", ue4ss.UEError, lambda: actor_class.SetActorHiddenInGame(True),
       "'SetActorHiddenInGame' is an instance function; call it on an object of the class, not on the class itself")
gm_only = gm.get_class().find_function("GetNumPlayers")
if gm_only is not None:
    raises("function of another class", ue4ss.UEError, lambda: pc.call_function(gm_only))
raises("unknown function", AttributeError, lambda: gm.NoSuchFunctionAnywhere, "the object has no property or function named 'NoSuchFunctionAnywhere'")
raises("call_function unknown name", AttributeError, lambda: gm.call_function("NoSuchFunctionAnywhere"))
raises("call_function wrong type", TypeError, lambda: gm.call_function(5))
check("hasattr on a missing function is False and adds no name", not hasattr(gm, "PyueCallProbeNeverUsed") and ue4ss.FName.find("PyueCallProbeNeverUsed") is None)
check("static library function is not a method of a game mode", not hasattr(gm, "Add_IntInt"))

failures = []
def from_thread():
    for label, fn in (("lookup", lambda: math.Add_IntInt), ("call_function", lambda: math.call_function("Add_IntInt", 1, 2))):
        try:
            fn()
            failures.append(label + ": no exception")
        except ue4ss.UEError:
            pass
        except BaseException as e:
            failures.append(f"{label}: {type(e).__name__}")
worker = threading.Thread(target=from_thread)
worker.start()
worker.join()
check("calls from a Python thread are refused", not failures)

# --- every block and returned struct is released ---------------------------------------------------------------
for i in range(3000):
    math.Add_IntInt(i, 1)
    math.Add_VectorVector({"X": 1.0}, {"Y": 2.0})
    math.BreakVector({"X": 1.0, "Y": 2.0, "Z": 3.0})
    gm.K2_GetActorLocation()
del sum_vec, location, parts, instance
gc.collect()
now = ue4ss._selftest_struct_stats()
print("owned stats:", now)
check("12000 calls: every block destroyed", now["live"] <= base["live"] + 4 and now["created"] - base["created"] >= 12000)
check("nothing leaked", now["leaked_inner"] == base["leaked_inner"] and now["leaked_block"] == base["leaked_block"])
print("calls test finished")
