# Copy over python_scripts/main.py to check property reads/writes in the game.
# Every write is restored in a finally block.
import ue4ss

gm = ue4ss.find_first_of("GameModeBase")
pc = ue4ss.find_first_of("PlayerController")
print("targets:", gm.get_fname(), "|", pc.get_fname())

# --- reads -----------------------------------------------------------------------------------
print("read bHidden (bool bit-field):", repr(gm.bHidden))
print("read bCanBeDamaged (neighbouring bit-field):", repr(gm.bCanBeDamaged))
print("read NetTag (int32):", repr(gm.NetTag))
print("read InitialLifeSpan (float):", repr(gm.InitialLifeSpan))
print("read NetUpdateFrequency / MinNetUpdateFrequency (adjacent floats):", repr(gm.NetUpdateFrequency), repr(gm.MinNetUpdateFrequency))
print("read Role (byte enum):", repr(gm.Role))
print("read OptionsString (FString):", repr(gm.OptionsString))
print("read StateName (FName):", repr(pc.StateName), "| number", pc.StateName.number)
print("get_property_value:", gm.get_property_value("NetTag") == gm.NetTag)
print("case-insensitive:", gm.nettag == gm.NetTag)


def check(label, ok):
    print(f"{label}: {'PASS' if ok else 'FAIL'}")


# --- writes (restored) -----------------------------------------------------------------------
old = gm.NetTag
try:
    gm.NetTag = 12345
    check("write int32", gm.NetTag == 12345)
    gm.NetTag = -7
    check("write negative int32", gm.NetTag == -7)
finally:
    gm.NetTag = old
check("int32 restored", gm.NetTag == old)

old_freq, old_min = gm.NetUpdateFrequency, gm.MinNetUpdateFrequency
try:
    gm.NetUpdateFrequency = 7.25
    check("write float", gm.NetUpdateFrequency == 7.25)
    check("adjacent float untouched", gm.MinNetUpdateFrequency == old_min)
    gm.NetUpdateFrequency = 0.1
    check("float narrowed to 32 bit", gm.NetUpdateFrequency == 0.10000000149011612)
finally:
    gm.NetUpdateFrequency = old_freq
check("float restored", gm.NetUpdateFrequency == old_freq)

old_hidden, old_damage = gm.bHidden, gm.bCanBeDamaged
try:
    gm.bHidden = not old_hidden
    check("write bool bit-field", gm.bHidden == (not old_hidden))
    check("neighbouring bit-field untouched", gm.bCanBeDamaged == old_damage)
    gm.bHidden = 1 if old_hidden else 0
    check("bool accepts 0/1", gm.bHidden == old_hidden)
finally:
    gm.bHidden = old_hidden
check("bool restored", gm.bHidden == old_hidden and gm.bCanBeDamaged == old_damage)

old_options = gm.OptionsString
try:
    gm.OptionsString = "pyue-test h\u00e9llo \U0001F600 \u2713"
    check("write FString with non-ASCII", gm.OptionsString == "pyue-test h\u00e9llo \U0001F600 \u2713")
finally:
    gm.OptionsString = old_options
check("FString restored", gm.OptionsString == old_options)

old_state = pc.StateName
try:
    pc.StateName = old_state
    check("write FName (same value)", pc.StateName == old_state)
    pc.StateName = "PyueTestState"
    check("write FName from str", pc.StateName == "PyueTestState")
finally:
    pc.StateName = old_state
check("FName restored", pc.StateName == old_state)


# --- errors ----------------------------------------------------------------------------------
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


def setting(name, value):
    return lambda: setattr(gm, name, value)


raises("missing property", AttributeError, lambda: gm.NoSuchPropertyAnywhere)
raises("missing property write", AttributeError, setting("NoSuchPropertyAnywhere", 1))
raises("int32 overflow", OverflowError, setting("NetTag", 2**31))
raises("int32 underflow", OverflowError, setting("NetTag", -2**31 - 1))
raises("float to int property", TypeError, setting("NetTag", 1.5))
raises("str to int property", TypeError, setting("NetTag", "3"))
raises("int to FString property", TypeError, setting("OptionsString", 5))
raises("bool bad int", ValueError, setting("bHidden", 2))
raises("bool bad type", TypeError, setting("bHidden", "yes"))
raises("float overflow", OverflowError, setting("NetUpdateFrequency", 3.5e38))
# Arrays are supported now (see arrays_main.py); look for a property of a type that still is not (map, set, text, ...).
unsupported_name = None
for candidate in gm.get_property_names():
    try:
        getattr(gm, candidate)
    except ue4ss.UEError as e:
        if "not supported yet" in str(e):
            unsupported_name = candidate
            break
    except AttributeError:
        pass
if unsupported_name:
    print("using unsupported property", unsupported_name)
    raises("unsupported property type", ue4ss.UEError, lambda: getattr(gm, unsupported_name))
    raises("unsupported write", ue4ss.UEError, setting(unsupported_name, None))
else:
    print("note: the game mode has no property of an unsupported type; those two checks were skipped")
check("array property reads as an Array", isinstance(gm.Tags, ue4ss.Array))
raises("method is read-only", AttributeError, setting("get_full_name", 1))
raises("delete property", TypeError, lambda: delattr(gm, "NetTag"))
check("hasattr missing is False", not hasattr(gm, "NoSuchPropertyAnywhere"))
check("hasattr dunder probe is False", not hasattr(gm, "__wrapped__"))
check("values intact after all errors", gm.NetTag == old and gm.OptionsString == old_options)
print("properties test finished")
