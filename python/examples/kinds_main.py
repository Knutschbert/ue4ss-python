# Copy over python_scripts/main.py to check the UStruct/UClass/UScriptStruct/UFunction/UEnum wrappers in the game.
import ue4ss


def check(label, ok):
    print(f"{label}: {'PASS' if ok else 'FAIL'}")


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


# --- the right Python type for each kind of object --------------------------------------------
actor_cls = ue4ss.static_find_object("/Script/Engine.Actor")
pawn_cls = ue4ss.static_find_object("/Script/Engine.Pawn")
vector = ue4ss.static_find_object("/Script/CoreUObject.Vector")
net_role = ue4ss.static_find_object("/Script/Engine.ENetRole")
gm = ue4ss.find_first_of("GameModeBase")
set_hidden = actor_cls.find_function("SetActorHiddenInGame")

print("types:", type(actor_cls).__name__, type(vector).__name__, type(set_hidden).__name__, type(net_role).__name__, type(gm).__name__)
check("class -> UClass", type(actor_cls) is ue4ss.UClass and type(pawn_cls) is ue4ss.UClass)
check("script struct -> UScriptStruct", type(vector) is ue4ss.UScriptStruct)
check("function -> UFunction", type(set_hidden) is ue4ss.UFunction)
check("enum -> UEnum", type(net_role) is ue4ss.UEnum)
check("plain instance stays UObject", type(gm) is ue4ss.UObject)
check("get_class() is a UClass", type(gm.get_class()) is ue4ss.UClass)
check("hierarchy", issubclass(ue4ss.UClass, ue4ss.UStruct) and issubclass(ue4ss.UFunction, ue4ss.UStruct)
      and issubclass(ue4ss.UStruct, ue4ss.UObject) and issubclass(ue4ss.UEnum, ue4ss.UObject) and not issubclass(ue4ss.UEnum, ue4ss.UStruct))
check("same object, different wrapper types still equal", actor_cls == ue4ss.static_find_object("/Script/Engine.Actor"))

# --- UStruct / UClass --------------------------------------------------------------------------
chain = []
cls = gm.get_class()
while cls is not None:
    chain.append(cls.get_fname().to_string())
    cls = cls.get_super()
print("class chain of the game mode:", " > ".join(chain))
check("chain ends at Object", chain[-1] == "Object" and len(chain) >= 4)

fields = actor_cls.get_property_names()
print("Actor has", len(fields), "properties, e.g.", fields[:6])
check("Actor properties", "bHidden" in fields and "ReplicatedMovement" in fields and "NetTag" in fields)
pawn_fields = pawn_cls.get_property_names()
check("subclass lists parent properties too", "bHidden" in pawn_fields and len(pawn_fields) > len(fields))
check("vector fields", vector.get_property_names() == ["X", "Y", "Z"])

functions = actor_cls.get_function_names()
print("Actor has", len(functions), "functions, e.g.", functions[:5])
check("Actor functions", "SetActorHiddenInGame" in functions and "K2_GetActorLocation" in functions)
check("find_function through a parent", pawn_cls.find_function("SetActorHiddenInGame") == set_hidden)
check("find_function misses give None", actor_cls.find_function("NoSuchFunctionAnywhere") is None)

cdo = actor_cls.get_cdo()
print("cdo:", cdo)
check("cdo", cdo is not None and cdo.get_full_name().endswith("Default__Actor"))
check("is_child_of(UClass)", pawn_cls.is_child_of(actor_cls))
check("is_child_of(path)", pawn_cls.is_child_of("/Script/Engine.Actor"))
check("a class is a child of itself", pawn_cls.is_child_of(pawn_cls))
check("parent is not a child of its subclass", not actor_cls.is_child_of(pawn_cls))
check("game mode class derives from GameModeBase", gm.get_class().is_child_of("/Script/Engine.GameModeBase"))

# --- UFunction ---------------------------------------------------------------------------------
print("SetActorHiddenInGame:", set_hidden, "| flags", hex(set_hidden.get_function_flags()), "| params", set_hidden.get_params())
check("SetActorHiddenInGame params", [(p["name"], p["type"]) for p in set_hidden.get_params()] == [("bNewHidden", "BoolProperty")])
FUNC_Native, FUNC_BlueprintCallable = 0x400, 0x04000000
check("function flags", bool(set_hidden.get_function_flags() & FUNC_BlueprintCallable))
get_location = actor_cls.find_function("K2_GetActorLocation")
params = get_location.get_params()
print("K2_GetActorLocation params:", params)
check("return value param", len(params) == 1 and params[0]["is_return"] and params[0]["type"] == "StructProperty")
check("function has no super", get_location.get_super() is None)
check("function outer is its class", get_location.get_outer() == actor_cls)

# --- UEnum -------------------------------------------------------------------------------------
names = net_role.get_names()
print("ENetRole:", names)
check("enum names without prefix", names.get("ROLE_Authority") == 3 and names.get("ROLE_None") == 0)
check("enum lookups", net_role.get_name_by_value(3) == "ROLE_Authority" and net_role.get_value_by_name("ROLE_Authority") == 3)
check("enum lookup with prefix", net_role.get_value_by_name("ENetRole::ROLE_Authority") == 3)
check("enum misses give None", net_role.get_name_by_value(12345) is None and net_role.get_value_by_name("ROLE_Nope") is None)
check("Role property value is a known enum value", net_role.get_name_by_value(gm.Role) is not None)

# --- errors ------------------------------------------------------------------------------------
raises("find_function wrong type", TypeError, lambda: actor_cls.find_function(5))
raises("is_child_of wrong type", TypeError, lambda: pawn_cls.is_child_of(5))
raises("is_child_of an instance", TypeError, lambda: pawn_cls.is_child_of(gm))
raises("is_child_of missing class", ue4ss.UEError, lambda: pawn_cls.is_child_of("/Script/Engine.NoSuchClass"))
raises("enum name lookup wrong type", TypeError, lambda: net_role.get_value_by_name(3))
raises("enum value lookup wrong type", TypeError, lambda: net_role.get_name_by_value("3"))
for kind in (ue4ss.UStruct, ue4ss.UClass, ue4ss.UScriptStruct, ue4ss.UFunction, ue4ss.UEnum):
    raises(f"{kind.__name__} cannot be constructed", TypeError, kind)
raises("class method on a plain instance does not exist", AttributeError, lambda: gm.get_cdo())
print("kinds test finished")
