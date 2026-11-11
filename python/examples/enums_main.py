# Enum properties read as IntEnum members named like the engine's enum. AActor.Role is an ENetRole byte property.
import enum
import ue4ss

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (" -- " + repr(detail)[:200] if detail else ""))


actor = ue4ss.find_first_of("Actor")
role = actor.Role
print("Actor.Role =", repr(role), "| class", type(role).__mro__[:2])
check("Role reads as an IntEnum member", isinstance(role, enum.IntEnum), type(role))
check("the class is named after the engine enum", type(role).__name__ == "ENetRole", type(role).__name__)
role_enum = ue4ss.static_find_object("/Script/Engine.ENetRole")
check("member name matches the UEnum", role_enum.get_name_by_value(int(role)) == role.name, (role.name, role_enum.get_name_by_value(int(role))))
check("member equals its integer", role == int(role) and role in (int(role),))
names = role_enum.get_names()
check("class has the engine's names", all(n in type(role).__members__ for n in names if not n.startswith("_")), sorted(type(role).__members__)[:8])

# writes by member and by int, then restore
original = role
cls = type(role)
other = next(m for m in cls if m != original and m.name != "ROLE_MAX")
actor.Role = other
check("write by member", actor.Role == other and actor.Role.name == other.name, actor.Role)
actor.Role = int(original)
check("write by int", actor.Role == original, actor.Role)
for bad, exc in (("ROLE_None", TypeError), (300, OverflowError), (1.5, TypeError)):
    try:
        actor.Role = bad
        check(f"write {bad!r} refused", False)
    except exc:
        check(f"write {bad!r} refused", True)
check("value intact after bad writes", actor.Role == original)

# a value the enum has no name for stays an int
actor.Role = 200
unnamed = actor.Role
check("unnamed value stays a plain int", unnamed == 200 and type(unnamed) is int, (unnamed, type(unnamed)))
actor.Role = int(original)

# scan: every enum-valued property over many objects
classes = {}
bad = []
scanned = 0
for obj in ue4ss.find_objects(class_name="Object", banned_flags=ue4ss.ObjectFlags.RF_ClassDefaultObject | ue4ss.ObjectFlags.RF_ArchetypeObject, limit=6000):
    scanned += 1
    try:
        names_here = obj.get_property_names()
    except ue4ss.UEError:
        continue
    for name in names_here:
        try:
            value = getattr(obj, name)
        except (ue4ss.UEError, AttributeError):
            continue
        if isinstance(value, enum.IntEnum):
            classes.setdefault(type(value).__name__, type(value))
            if value.name not in type(value).__members__:
                bad.append((name, value))
print(f"scanned {scanned} objects; enum classes seen: {len(classes)}", sorted(classes)[:15])
check("many different enums read as IntEnum", len(classes) >= 5, len(classes))
check("members are consistent with their classes", not bad, bad[:3])

# the same engine enum is always the same Python class
second = ue4ss.find_all_of("Actor")[1].Role
check("one class per enum", type(second) is cls, (type(second), cls))

print(f"enums finished PASS={sum(results)} FAIL={len(results) - sum(results)}")
