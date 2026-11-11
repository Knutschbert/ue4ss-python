# Copy over python_scripts/main.py to check parameter blocks (the marshalling half of function calls) in the game.
# Nothing here calls a function; it builds the blocks a call would use and reads them back.
import gc

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


def function(class_path, name):
    cls = ue4ss.static_find_object(class_path)
    fn = cls.find_function(name) if cls is not None else None
    if fn is None:
        print(f"FAIL: function {name} not found on {class_path}")
    return fn


gm = ue4ss.find_first_of("GameModeBase")
base = ue4ss._selftest_struct_stats()

# --- how the engine flags parameters (this decides inputs vs outputs) -------------------------------
set_location = function("/Script/Engine.Actor", "K2_SetActorLocation")
for p in set_location.get_params():
    print("  K2_SetActorLocation param:", p)
flags = {p["name"]: p for p in set_location.get_params()}
check("SweepHitResult is an output, not a by-reference input", flags["SweepHitResult"]["is_out"] and not flags["SweepHitResult"]["is_reference"])
check("NewLocation is an input", not flags["NewLocation"]["is_out"] and not flags["NewLocation"]["is_return"])
check("ReturnValue is the return value", flags["ReturnValue"]["is_return"])

# --- ints and floats -------------------------------------------------------------------------------
add = function("/Script/Engine.KismetMathLibrary", "Add_IntInt")
blk = add.make_param_block(2, 3)
print("Add_IntInt block:", blk, blk.to_dict())
check("positional ints", (blk.A, blk.B, blk.ReturnValue) == (2, 3, 0) and blk.is_owned())
blk = add.make_param_block(B=5, a=1)
check("keywords, any order, case-insensitive", (blk.A, blk.B) == (1, 5))
check("block is named after the function", blk.get_struct_name() == "Add_IntInt")

multiply = function("/Script/Engine.KismetMathLibrary", "Multiply_FloatFloat")
check("float narrowed to 32 bits", multiply.make_param_block(0.1, 2).A == 0.10000000149011612)

# --- name, string, object --------------------------------------------------------------------------
to_string = function("/Script/Engine.KismetStringLibrary", "Conv_NameToString")
check("name param from str", to_string.make_param_block("Spectating").InName == ue4ss.FName("Spectating"))
check("name param from FName", to_string.make_param_block(ue4ss.FName("Object_3")).InName == "Object_3")
check("string return value starts empty", to_string.make_param_block("x").ReturnValue == "")

distance = function("/Script/Engine.Actor", "GetDistanceTo")
pc = ue4ss.find_first_of("PlayerController")
blk = distance.make_param_block(pc)
check("object param", blk.OtherActor == pc and blk.ReturnValue == 0.0)
check("object param accepts None", distance.make_param_block(None).OtherActor is None)
raises("object of the wrong class", ue4ss.UEError, lambda: distance.make_param_block(ue4ss.static_find_object("/Script/Engine.Actor")))

# --- struct inputs and outputs ---------------------------------------------------------------------
blk = set_location.make_param_block({"X": 1.0, "Y": 2.0, "Z": 3.0}, True, bTeleport=False)
print("K2_SetActorLocation block:", blk)
check("struct input from a dict", blk.NewLocation.to_dict() == {"X": 1.0, "Y": 2.0, "Z": 3.0})
check("bool inputs", blk.bSweep is True and blk.bTeleport is False)
check("output struct is default-initialised and owned", blk.SweepHitResult.is_owned() and blk.ReturnValue is False)

vec = ue4ss.make_struct("/Script/CoreUObject.Vector", X=7.0)
blk = set_location.make_param_block(vec, False, False)
vec.X = 8.0
check("struct input from an owned struct is copied", blk.NewLocation.X == 7.0)
blk = set_location.make_param_block(gm.ReplicatedMovement.LinearVelocity, False, False)  # a view into an object
check("struct input from a view", blk.NewLocation.to_dict() == gm.ReplicatedMovement.LinearVelocity.to_dict())

# --- a function with many parameter kinds, built from its own reflection data -----------------------------
print_string = function("/Script/Engine.KismetSystemLibrary", "PrintString")
defaults = {"StrProperty": "hello", "BoolProperty": False, "FloatProperty": 2.5, "ObjectProperty": gm, "StructProperty": {}, "NameProperty": "None"}
inputs = [p for p in print_string.get_params() if not p["is_return"] and not (p["is_out"] and not p["is_reference"])]
print("PrintString inputs:", [(p["name"], p["type"]) for p in inputs])
kwargs = {p["name"]: defaults[p["type"]] for p in inputs}
blk = print_string.make_param_block(**kwargs)
check("every input kind in one call", blk.InString == "hello" and blk.Duration == 2.5 and blk.WorldContextObject == gm)

# --- errors: the rules of core/include/pyue/call_binding.hpp, with the engine's real functions ------------
raises("too many positional", TypeError, lambda: add.make_param_block(1, 2, 3), "Add_IntInt() takes 2 positional arguments but 3 were given")
raises("missing one", TypeError, lambda: add.make_param_block(1), "Add_IntInt() missing 1 required argument: 'B'")
raises("missing two", TypeError, lambda: add.make_param_block(), "Add_IntInt() missing 2 required arguments: 'A', 'B'")
raises("duplicate", TypeError, lambda: add.make_param_block(1, A=2), "Add_IntInt() got multiple values for argument 'A'")
raises("unknown keyword", TypeError, lambda: add.make_param_block(1, 2, C=3), "Add_IntInt() got an unexpected keyword argument 'C'")
raises("return value cannot be passed", TypeError, lambda: add.make_param_block(1, 2, ReturnValue=3), "Add_IntInt() 'ReturnValue' is an output of the function and cannot be passed")
raises("output cannot be passed", TypeError, lambda: set_location.make_param_block({}, True, SweepHitResult=None), "K2_SetActorLocation() 'SweepHitResult' is an output of the function and cannot be passed")
raises("int overflow", OverflowError, lambda: add.make_param_block(2**31, 1))
raises("float for an int", TypeError, lambda: add.make_param_block(1.5, 1))
raises("bad struct field", AttributeError, lambda: set_location.make_param_block({"Nope": 1}, True, False))
raises("bad struct field value", TypeError, lambda: set_location.make_param_block({"X": "1"}, True, False))
raises("bad bool", ValueError, lambda: set_location.make_param_block({}, 2, False))

# --- a Blueprint function has local variables as properties; a parameter block must expose only the parameters ---------
pc = ue4ss.find_first_of("PlayerController")
bp_fn = pc.get_class().find_function("GetSpectatorPawnSpawnTransform")
if bp_fn is not None:
    block = bp_fn.make_param_block()
    declared = [p["name"] for p in bp_fn.get_params()]
    print("Blueprint function parameters:", declared, "| names exposed by its block:", block.get_property_names())
    check("a Blueprint function's block lists only its parameters", block.get_property_names() == declared)
    locals_ = [n for n in bp_fn.get_property_names() if n not in declared]
    print("  local variables (must not be reachable):", locals_[:4])
    if locals_:
        raises("a local variable is not reachable through the block", AttributeError, lambda: getattr(block, locals_[0]))
        raises("a local variable cannot be written through the block", AttributeError, lambda: setattr(block, locals_[0], 1))
else:
    print("Blueprint function block check: INCONCLUSIVE (function not found)")

if bp_fn is not None:
    # Allocating only ParmsSize for a Blueprint function overflowed the heap (STATUS_HEAP_CORRUPTION) when the engine
    # initialised its local variables. Churn many blocks so a regression would crash here, not later.
    for i in range(3000):
        bp_fn.make_param_block().copy()
    check("3000 Blueprint-function blocks (with locals) created, copied and freed", True)

# --- a parameter block can be copied like any struct ----------------------------------------------------------
original = add.make_param_block(5, 6)
duplicate = original.copy()
original.A = 99
check("a parameter block copy is owned and independent", duplicate.is_owned() and (duplicate.A, duplicate.B) == (5, 6) and original.A == 99)
check("a parameter block with a struct parameter copies deeply", set_location.make_param_block({"X": 1.0}, True, False).copy().NewLocation.X == 1.0)

# --- every block is released -----------------------------------------------------------------------------
for i in range(5000):
    add.make_param_block(i, i)
    set_location.make_param_block({"X": 1.0}, True, False)
del blk, vec
gc.collect()
now = ue4ss._selftest_struct_stats()
print("owned stats:", now)
check("5000 x 2 blocks created and all destroyed", now["live"] <= base["live"] + 8 and now["created"] - base["created"] >= 10000)
check("nothing leaked", now["leaked_inner"] == base["leaked_inner"] and now["leaked_block"] == base["leaked_block"])
print("params test finished")
