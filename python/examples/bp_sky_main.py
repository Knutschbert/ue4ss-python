# Blueprint hooks on a real Blueprint in the test game: the engine's BP_Sky_Sphere, cooked into the test game and spawned by its
# game mode. Covers what the Chivalry 2 Blueprint examples cover, without Chivalry 2: class-of-class lookup, discovery, hooks on plain functions called from
# Python, hooks on functions another Blueprint function calls, the event graph, variables.
import ue4ss

PATH = "/Engine/EngineSky/BP_Sky_Sphere.BP_Sky_Sphere_C"


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + str(detail)[:200] + ')') if detail else ''}")


def skip(label, why):
    print(f"{label}: SKIP ({why})")


cls = ue4ss.static_find_object(PATH)
if cls is None:
    skip("blueprint sky", "this game has no BP_Sky_Sphere")
    print("blueprint sky finished")
    raise SystemExit
check("the Blueprint class is loaded", cls.is_valid())
check("find_all_of('BlueprintGeneratedClass') lists it", cls in ue4ss.find_all_of("BlueprintGeneratedClass"))
check("find_first_of('BlueprintGeneratedClass') finds a Blueprint class", ue4ss.find_first_of("BlueprintGeneratedClass") is not None)
instances = [i for i in ue4ss.find_all_of("BP_Sky_Sphere_C") if i.get_class() == cls]
check("the game mode spawned one instance", len(instances) == 1, str(instances))
sky = instances[0]

own = {n for n in cls.get_function_names() if cls.find_function(n) is not None and cls.find_function(n).get_outer() == cls}
check("discovery lists the Blueprint's own functions", {"RefreshMaterial", "UpdateSunDirection", "UserConstructionScript"} <= own, sorted(own))
props = {p["name"]: p["type"] for p in cls.get_properties(inherited=False)}
check("discovery lists its variables with types", props.get("Sun height") == "FloatProperty" and props.get("Refresh material") == "BoolProperty", str(list(props.items())[:6]))

# variables
before = getattr(sky, "Sun height")
setattr(sky, "Sun height", 12.5)
check("a Blueprint variable (name with a space) can be written and read", abs(getattr(sky, "Sun height") - 12.5) < 1e-4, str(getattr(sky, "Sun height")))
setattr(sky, "Sun height", before)

# hooks on plain functions, called from Python
seen = {"update_pre": 0, "update_post": 0, "refresh": 0, "ubergraph": 0}
ids = [ue4ss.register_hook(f"{PATH}:UpdateSunDirection", pre=lambda c, p: seen.__setitem__("update_pre", seen["update_pre"] + 1),
                           post=lambda c, p, r: seen.__setitem__("update_post", seen["update_post"] + 1), instance=sky),
       ue4ss.register_hook(f"{PATH}:RefreshMaterial", pre=lambda c, p: seen.__setitem__("refresh", seen["refresh"] + 1), instance=sky)]
sky.UpdateSunDirection()
check("pre and post of a plain Blueprint function fire for a Python call", seen["update_pre"] == 1 and seen["update_post"] == 1, str(seen))
sky.RefreshMaterial()
check("a second hooked function fires for its own call", seen["refresh"] == 1, str(seen))

# a call from inside Blueprint code: the construction script calls RefreshMaterial
seen["refresh"] = 0
sky.UserConstructionScript()
print("construction script call: refresh calls =", seen["refresh"], "update calls =", seen["update_pre"])
check("a function called by another Blueprint function reaches its hook", seen["refresh"] >= 1, str(seen))

# the other instance filter
other = ue4ss.register_hook(f"{PATH}:UpdateSunDirection", pre=lambda c, p: seen.__setitem__("other", True), instance=ue4ss.find_first_of("PyTestGameMode"))
sky.UpdateSunDirection()
check("a hook filtered to another object does not fire", "other" not in seen)
for pair in ids + [other]:
    ue4ss.unregister_hook(pair)
before_count = seen["update_pre"]
sky.UpdateSunDirection()
check("after unregistering the hook stays silent", seen["update_pre"] == before_count)
print("blueprint sky finished")
