# Does the sky Blueprint use an output that a post-hook of BreakRotator changed? Reads the material parameters it sets. No pass/fail.
import ue4ss

sky = ue4ss.find_first_of("BP_Sky_Sphere_C")
sky.K2_SetActorRotation({"Roll": 10.0, "Pitch": 20.0, "Yaw": 30.0}, False)
print("actor rotation:", sky.K2_GetActorRotation())
mid = getattr(sky, "Sky material")
print("material:", mid)
names = ["Light direction", "Light colour", "Cloud colour", "Sun vector", "SunVector", "Light Direction", "Overall Color", "Cloud color"]


def read():
    out = {}
    for n in names:
        try:
            v = mid.K2_GetVectorParameterValue(n)
            out[n] = (round(v.R, 3), round(v.G, 3), round(v.B, 3))
        except Exception as e:  # noqa: BLE001
            out[n] = type(e).__name__
    return out


seen = []
ids = ue4ss.register_hook("/Script/Engine.KismetMathLibrary:BreakRotator", post=lambda c, p, r: seen.append((p.InRot.Roll, p.InRot.Pitch, p.InRot.Yaw, p.Roll, p.Pitch, p.Yaw)))
sky.UpdateSunDirection()
ue4ss.unregister_hook(ids)
print("break rotator calls (in roll,pitch,yaw, out roll,pitch,yaw):", seen)
print("plain      :", read())
ids = ue4ss.register_hook("/Script/Engine.KismetMathLibrary:BreakRotator", post=lambda c, p, r: (setattr(p, "Pitch", 80.0), setattr(p, "Yaw", 5.0)))
sky.UpdateSunDirection()
ue4ss.unregister_hook(ids)
print("overridden :", read())
print("native out probe finished")
