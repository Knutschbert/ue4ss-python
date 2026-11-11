# Replacements of Blueprint functions (7q4.10), on the engine Blueprint BP_Sky_Sphere in the test game. A Blueprint function runs in the script
# VM, so a replacement is seen for a Python call as well as for a call made by other Blueprint code (the construction script calls RefreshMaterial).
import ue4ss

PATH = "/Engine/EngineSky/BP_Sky_Sphere.BP_Sky_Sphere_C"


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + str(detail)[:300] + ')') if detail else ''}")


def skip(label, why):
    print(f"{label}: SKIP ({why})")


cls = ue4ss.static_find_object(PATH)
sky = ue4ss.find_first_of("BP_Sky_Sphere_C") if cls is not None else None
if sky is None:
    skip("blueprint replacements", "this game has no BP_Sky_Sphere")
    print("blueprint replacements finished")
    raise SystemExit

material = getattr(sky, "Sky material")


def cloud():
    value = material.K2_GetVectorParameterValue("Cloud color")
    return (round(value.R, 3), round(value.G, 3), round(value.B, 3))


def blank():
    material.SetVectorParameterValue("Cloud color", {"R": 0.0, "G": 0.0, "B": 0.0, "A": 1.0})


blank()
check("the material parameter can be blanked", cloud() == (0.0, 0.0, 0.0), str(cloud()))
sky.UpdateSunDirection()
real = cloud()
check("the function normally changes the parameter", real != (0.0, 0.0, 0.0), str(real))

# 1. a replacement that skips the original, Python call
calls = []
rid = ue4ss.register_replacement(f"{PATH}:UpdateSunDirection", lambda context, params: calls.append(context) or None)
blank()
sky.UpdateSunDirection()
check("the replacement ran for a Python call", len(calls) == 1 and calls[0] == sky, str(len(calls)))
check("the original did not run", cloud() == (0.0, 0.0, 0.0), str(cloud()))
ue4ss.unregister_replacement(rid)
sky.UpdateSunDirection()
check("after unregistering the original runs again", cloud() == real, str(cloud()))

# 2. CALL_ORIGINAL runs the original
calls.clear()
rid = ue4ss.register_replacement(f"{PATH}:UpdateSunDirection", lambda context, params: calls.append(1) or ue4ss.CALL_ORIGINAL)
blank()
sky.UpdateSunDirection()
check("CALL_ORIGINAL runs the original once", len(calls) == 1 and cloud() == real, f"{len(calls)} {cloud()}")
ue4ss.unregister_replacement(rid)

# 3. a function that Blueprint code calls: UserConstructionScript calls RefreshMaterial. Evidence that the original runs: it calls BreakRotator.
breaks = []
spy = ue4ss.register_hook("/Script/Engine.KismetMathLibrary:BreakRotator", pre=lambda c, p: breaks.append(1))
sky.UserConstructionScript()
plain_breaks = len(breaks)
check("normally the construction script reaches BreakRotator through RefreshMaterial", plain_breaks >= 1, str(plain_breaks))
calls.clear()
breaks.clear()
rid = ue4ss.register_replacement(f"{PATH}:RefreshMaterial", lambda context, params: calls.append(context) or None)
sky.UserConstructionScript()
check("the replacement ran for a call made by Blueprint code", len(calls) >= 1 and calls[0] == sky, str(len(calls)))
check("the original did not run for it", len(breaks) < plain_breaks, f"{len(breaks)} of {plain_breaks} BreakRotator calls")
ue4ss.unregister_replacement(rid)
breaks.clear()
sky.UserConstructionScript()
check("after unregistering the original runs for the Blueprint call", len(breaks) == plain_breaks, f"{len(breaks)} of {plain_breaks}")

# 4. calling the replaced function inside the replacement runs the original (the way to wrap it)
inner = []
breaks.clear()
rid = ue4ss.register_replacement(f"{PATH}:RefreshMaterial", lambda context, params: (inner.append(1), context.RefreshMaterial(), None)[-1])
sky.RefreshMaterial()
ue4ss.unregister_replacement(rid)
check("a replacement can call the original through the object", len(inner) == 1 and len(breaks) >= 1, f"{len(inner)} replacement calls, {len(breaks)} BreakRotator calls")
ue4ss.unregister_hook(spy)
print("blueprint replacements finished")
