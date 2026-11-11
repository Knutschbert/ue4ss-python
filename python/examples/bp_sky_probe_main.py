# The engine Blueprint BP_Sky_Sphere in the test game (rqf.6): class, instance, functions, variables. No pass/fail.
import ue4ss
from ue4ss import engine

PATH = "/Engine/EngineSky/BP_Sky_Sphere.BP_Sky_Sphere_C"
cls = ue4ss.static_find_object(PATH)
print("class already loaded:", cls)
if cls is None:
    try:
        cls = ue4ss.static_find_object("/Script/Engine.KismetSystemLibrary").LoadClassAsset_Blocking(PATH)
        print("loaded by path:", cls)
    except Exception as e:  # noqa: BLE001
        print("load failed:", type(e).__name__, e)
gm = ue4ss.find_first_of("PyTestGameMode")
print("game mode:", gm, "| SkySphereClass:", getattr(gm, "SkySphereClass", "?") if gm else None, "| SpawnedCount:", gm.SpawnedCount if gm else None)
print("BlueprintGeneratedClass count:", len(ue4ss.find_all_of("BlueprintGeneratedClass")))
if cls is not None:
    print("instances:", ue4ss.find_all_of("BP_Sky_Sphere_C"))
    for name in cls.get_function_names():
        func = cls.find_function(name)
        if func is None:
            continue
        owner = func.get_outer().get_full_name().split(" ", 1)[1].rsplit(".", 1)[-1]
        if not owner.endswith("_C"):
            continue
        sig = ", ".join(f"{p['name']}:{p['type']}{'(out)' if p['is_out'] else ''}{'(ret)' if p['is_return'] else ''}" for p in func.get_params())
        print(f"  {name} flags={func.get_function_flags():#x} ({sig})")
    print("properties:", [(p["name"], p["type"]) for p in cls.get_properties(inherited=False)])
print("sky probe finished")
