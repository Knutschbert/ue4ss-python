# Out parameters of a native function that Blueprint code calls: BP_Sky_Sphere.UpdateSunDirection calls
# KismetMathLibrary:BreakRotator from its bytecode (a CallMath instruction), so the call does not go through ProcessEvent.
import ue4ss
from ue4ss import bytecode

PATH = "/Engine/EngineSky/BP_Sky_Sphere.BP_Sky_Sphere_C"
BREAK = "/Script/Engine.KismetMathLibrary:BreakRotator"


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + str(detail)[:300] + ')') if detail else ''}")


def skip(label, why):
    print(f"{label}: SKIP ({why})")


cls = ue4ss.static_find_object(PATH)
sky = ue4ss.find_first_of("BP_Sky_Sphere_C") if cls is not None else None
if sky is None:
    skip("blueprint native out params", "this game has no BP_Sky_Sphere")
    print("blueprint native out params finished")
    raise SystemExit

update = cls.find_function("UpdateSunDirection")
calls = [c["target"] for c in bytecode.scan_calls(update)]
check("the Blueprint function calls BreakRotator itself (bytecode)", BREAK in calls, str([c for c in calls if c and "Break" in c]))

seen = []


def pre(context, params):
    params.InRot = {"Roll": 11.0, "Pitch": 22.0, "Yaw": 33.0}      # a pre-hook may change the inputs: non-zero values tell real outputs from zeros


def post(context, params, result):
    seen.append({"in": (params.InRot.Roll, params.InRot.Pitch, params.InRot.Yaw), "out": (params.Roll, params.Pitch, params.Yaw)})


ids = ue4ss.register_hook(BREAK, pre=pre, post=post)
sky.UpdateSunDirection()
ue4ss.unregister_hook(ids)
print("calls seen:", len(seen), seen[:2])
check("a hook sees the native function called from Blueprint code", len(seen) >= 1, str(len(seen)))
if seen:
    check("the inputs written by the pre-hook arrive", seen[0]["in"] == (11.0, 22.0, 33.0), str(seen[0]["in"]))
    check("the output parameters hold the real outputs (non-zero, equal to the input components)", seen[0]["out"] == (11.0, 22.0, 33.0), str(seen[0]))

# writing outputs from a post-hook: the Blueprint must use them. It turns the rotator into the sun's light direction and the cloud colour.
sky_material = getattr(sky, "Sky material")


def cloud_color():
    value = sky_material.K2_GetVectorParameterValue("Cloud color")
    return (round(value.R, 3), round(value.G, 3), round(value.B, 3))


sky.UpdateSunDirection()
plain = cloud_color()
ids = ue4ss.register_hook(BREAK, post=lambda c, p, r: (setattr(p, "Pitch", 80.0), setattr(p, "Yaw", 5.0), None)[-1])
sky.UpdateSunDirection()
ue4ss.unregister_hook(ids)
changed = cloud_color()
print("cloud colour plain / with written outputs:", plain, changed)
check("the Blueprint uses outputs written by a post-hook", plain != changed, f"{plain} vs {changed}")
sky.UpdateSunDirection()
check("without the hook the original behaviour is back", cloud_color() == plain, str(cloud_color()))
print("blueprint native out params finished")
