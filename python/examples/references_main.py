# Checks soft/weak/interface references and delegates on real game objects.
import ue4ss

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (" -- " + str(detail)[:220] if detail else ""))


soft, weak, iface, single, multi = {}, {}, {}, {}, {}
scanned = 0
for obj in ue4ss.find_objects(class_name="Object", banned_flags=ue4ss.ObjectFlags.RF_ClassDefaultObject | ue4ss.ObjectFlags.RF_ArchetypeObject, limit=8000):
    scanned += 1
    try:
        class_name = obj.get_class().get_fname().to_string()
        names = obj.get_property_names()
    except ue4ss.UEError:
        continue
    for name in names:
        key = (class_name, name)
        if any(key in table for table in (soft, weak, iface, single, multi)):
            continue
        try:
            value = getattr(obj, name)
        except (ue4ss.UEError, AttributeError):
            continue
        # tell the kinds apart by what the engine says the property is
        try:
            obj.set_property_value(name, value)  # same value back: a no-op for writable kinds
            writable = True
        except ue4ss.UEError as e:
            writable = False
            reason = str(e)
        except (TypeError, ValueError):
            continue
        if not writable and "interface" in reason:
            iface[key] = obj
        elif not writable and "delegate" in reason:
            (multi if isinstance(value, list) else single)[key] = obj
        elif isinstance(value, str) and ("/" in value or value == "") and name.lower().find("soft") != -1 or (writable and isinstance(value, str) and value.startswith("/")):
            soft[key] = obj
print(f"scanned {scanned}: soft {len(soft)}, interface {len(iface)}, single delegates {len(single)}, multicast {len(multi)}")

# soft references: read, write the same path back, set another path and restore, clear and restore
errors = []
for (class_name, name), obj in list(soft.items())[:100]:
    original = getattr(obj, name)
    try:
        assert isinstance(original, str)
        obj.set_property_value(name, original)
        assert getattr(obj, name) == original, "same value back"
        obj.set_property_value(name, "/Game/PyueTest/Thing.Thing")
        assert getattr(obj, name) == "/Game/PyueTest/Thing.Thing", "new path"
        obj.set_property_value(name, None)
        assert getattr(obj, name) == "", "cleared"
        obj.set_property_value(name, original)
        assert getattr(obj, name) == original, "restored"
    except Exception as e:
        errors.append((class_name, name, repr(e)[:100]))
        try:
            obj.set_property_value(name, original)
        except Exception:
            pass
check("soft references read, write, clear and restore", soft and not errors, (len(soft), errors[:3]))
if soft:
    (class_name, name), obj = next(iter(soft.items()))
    try:
        obj.set_property_value(name, 5)
        check("soft reference rejects an int", False)
    except TypeError:
        check("soft reference rejects an int", True)

# delegates: read and show; writes refused
bound = 0
for table in (single, multi):
    for (class_name, name), obj in list(table.items())[:200]:
        value = getattr(obj, name)
        items = value if isinstance(value, list) else ([] if value is None else [value])
        for target, function in items:
            bound += 1
            if not (isinstance(target, ue4ss.UObject) and isinstance(function, ue4ss.FName)):
                errors.append((class_name, name, "bad binding " + repr((target, function))))
print("delegate bindings seen:", bound)
check("delegate values have the documented shape", not errors, errors[:3])
for table, label in ((single, "single"), (multi, "multicast")):
    if table:
        (class_name, name), obj = next(iter(table.items()))
        try:
            setattr(obj, name, None)
            check(label + " delegate is read-only", False)
        except ue4ss.UEError:
            check(label + " delegate is read-only", True)

# interfaces
for (class_name, name), obj in list(iface.items())[:50]:
    value = getattr(obj, name)
    if value is not None and not isinstance(value, ue4ss.UObject):
        errors.append((class_name, name, repr(value)))
check("interface references read as objects or None", not errors, (len(iface), errors[:3]))

# soft references inside maps and arrays: the test game's actor carries them
holder = ue4ss.find_first_of("PyTestActor")
if holder is not None:
    class_map = holder.SoftClassMap
    check("map keyed by soft class references reads", {k: v for k, v in class_map.items()} == {"/Script/Engine.Actor": 1, "/Script/Engine.Pawn": 2}, class_map.to_dict())
    class_map["/Script/Engine.Character"] = 3
    check("soft class key write", class_map["/Script/Engine.Character"] == 3 and len(class_map) == 3)
    del class_map["/Script/Engine.Character"]
    check("soft class key delete", "/Script/Engine.Character" not in class_map and len(class_map) == 2)
    check("map keyed by soft object references", holder.SoftObjectMap["/Engine/Maps/Entry.Entry"] == "entry map", holder.SoftObjectMap.to_dict())
    soft_array = holder.SoftObjectArray
    check("array of soft object references", soft_array.to_list() == ["/Engine/Maps/Entry.Entry", "/Game/None.None"], soft_array.to_list())
    soft_array.append("/Game/Other.Other")
    soft_array.pop()
    check("soft reference array resize cycle", len(soft_array) == 2)
# Chivalry 2's maps keyed by soft class references (the maps that used to be unsupported)
soft_maps = 0
for obj in ue4ss.find_objects(class_name="Ammo_BallistaBolt_C", banned_flags=ue4ss.ObjectFlags.RF_ClassDefaultObject | ue4ss.ObjectFlags.RF_ArchetypeObject, limit=1):
    for name in ("OverrideMaxStackCount", "OverrideStackCount"):
        try:
            m = getattr(obj, name)
            print(name, "->", m, m.keys()[:3])
            soft_maps += 1
        except ue4ss.UEError as e:
            errors.append((name, str(e)[:100]))
print("maps with soft class keys readable:", soft_maps)
if soft_maps:
    check("maps keyed by soft class references now read", not errors, errors[:3])
else:
    print("maps keyed by soft class references now read (Chivalry 2 maps): SKIP (this game has none)")

print(f"references finished PASS={sum(results)} FAIL={len(results) - sum(results)}")
