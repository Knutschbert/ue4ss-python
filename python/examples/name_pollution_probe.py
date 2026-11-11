# Probe: does looking up a property that does not exist add its name to the engine's global name table?
import ue4ss

gm = ue4ss.find_first_of("GameModeBase")
actor_cls = ue4ss.static_find_object("/Script/Engine.Actor")
probes = ["PyueProbePropertyNeverUsed1", "PyueProbeFunctionNeverUsed2", "PyueProbeAttrNeverUsed3", "PyueProbeWriteNeverUsed4",
          "PyueProbeFindFirstOfNeverUsed5", "PyueProbePathNeverUsed6", "PyueProbeIsAPathNeverUsed7", "PyueProbeIsChildOfNeverUsed8",
          "PyueProbeMakeStructNeverUsed9", "PyueProbeStructFieldNeverUsed10", "PyueProbeEnumNeverUsed11"]
print("before:", [ue4ss.FName.find(p) for p in probes])
print("hasattr(obj, probe):", hasattr(gm, probes[0]))
print("find_function(probe):", actor_cls.find_function(probes[1]))
try:
    gm.get_property_value(probes[2])
except AttributeError:
    pass
try:
    setattr(gm, probes[3], 1)
except AttributeError:
    pass
print("find_first_of(probe):", ue4ss.find_first_of(probes[4]))
print("static_find_object(probe path):", ue4ss.static_find_object("/Script/Engine." + probes[5]), ue4ss.static_find_object("/Game/Maps/" + probes[5] + "." + probes[5]))
for call in (lambda: gm.get_class().is_a("/Script/Engine." + probes[6]), lambda: actor_cls.is_child_of("/Script/Engine." + probes[7]),
             lambda: ue4ss.make_struct("/Script/Engine." + probes[8]), lambda: ue4ss.make_struct("/Script/CoreUObject.Vector", **{probes[9]: 1})):
    try:
        call()
    except (ue4ss.UEError, AttributeError) as e:
        print("  raised", type(e).__name__)
net_role = ue4ss.static_find_object("/Script/Engine.ENetRole")
print("enum lookups:", net_role.get_value_by_name(probes[10]), net_role.get_name_by_value(99999))
after = [ue4ss.FName.find(p) for p in probes]
print("after: ", after)
added = [p for p, f in zip(probes, after) if f is not None]
print("names added by lookups:", added)
# --- the fix must not break lookups of things that DO exist ---------------------------------------------------
existing = ["/Script/Engine.Actor", "/Script/Engine.Pawn", "/Script/CoreUObject.Vector", "/Script/Engine.URL", "/Script/Engine.ENetRole",
            "/Script/CoreUObject.Object", "/Script/Engine.DefaultPawn"]
missing = [p for p in existing if ue4ss.static_find_object(p) is None]
print("existing paths not found:", missing)
print("existing lookups still work:", "PASS" if not missing else "FAIL")
print("find_first_of existing:", "PASS" if ue4ss.find_first_of("GameModeBase") is not None and ue4ss.find_first_of("PlayerController") is not None else "FAIL")
print("find_function existing:", "PASS" if actor_cls.find_function("SetActorHiddenInGame") is not None else "FAIL")
print("property existing:", "PASS" if gm.NetTag is not None and gm.bHidden is not None else "FAIL")
print("sub-object path (outer:name) still found:", "PASS" if ue4ss.static_find_object(gm.get_full_name().split(" ", 1)[1]) == gm else "FAIL")
print("no names added:", "PASS" if not added else "FAIL")
print("probe finished")
