# Checks TMap / TSet properties on real game objects: finds them by scanning, reads them every way, then edits and restores
# them within one tick (no game code runs in between).
import ue4ss

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (" -- " + str(detail)[:220] if detail else ""))


maps = {}
sets = {}
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
        if key in maps or key in sets:
            continue
        try:
            value = getattr(obj, name)
        except (ue4ss.UEError, AttributeError):
            continue
        if isinstance(value, ue4ss.Map):
            maps[key] = obj
        elif isinstance(value, ue4ss.Set):
            sets[key] = obj
print(f"scanned {scanned} objects: {len(maps)} distinct map properties, {len(sets)} set properties")
check("map or set properties exist on game objects", len(maps) + len(sets) > 0, (len(maps), len(sets)))


def plain(value):
    return value.to_dict() if isinstance(value, ue4ss.Struct) else value


def norm_map(m):
    return {(str(k) if isinstance(k, ue4ss.FName) else k): plain(v) for k, v in m.items()}


def norm_set(s):
    return sorted((str(x) if isinstance(x, ue4ss.FName) else x for x in s.to_list()), key=repr)


errors = []
kinds = {}
unsupported = []
for (class_name, name), obj in list(maps.items())[:300]:
    m = getattr(obj, name)
    try:
        n = len(m)
        items = m.items()
        assert len(items) == n and len(m.keys()) == n and len(m.values()) == n, "lengths"
        assert len(list(m)) == n, "iteration"
        d = m.to_dict()
        assert len(d) == n or n == 0 or any(isinstance(k, ue4ss.Struct) for k in m.keys()), "to_dict"
        if n:
            k0, v0 = items[0]
            kinds[type(k0).__name__ + "->" + type(v0).__name__] = kinds.get(type(k0).__name__ + "->" + type(v0).__name__, 0) + 1
            if isinstance(k0, ue4ss.Struct):
                continue  # struct keys: listing only
            assert k0 in m, "contains"
            assert plain(m[k0]) == plain(v0), "getitem"
            assert m.get(k0) is not None or v0 is None, "get"
            before = norm_map(m)
            # edit and restore: remove / re-add, pop / re-add, rewrite with itself, clear / update
            del m[k0]
            assert len(m) == n - 1 and k0 not in m, "delete"
            m[k0] = v0
            assert norm_map(m) == before, "re-add"
            popped = m.pop(k0)
            assert plain(popped) == plain(v0) and len(m) == n - 1, "pop"
            m.update([(k0, v0)])
            assert norm_map(m) == before, "update"
            saved = m.items()
            m.clear()
            assert len(m) == 0, "clear"
            m.update(saved)
            assert norm_map(m) == before, "clear/update restore"
            setattr(obj, name, saved)           # whole-map assignment from pairs
            assert norm_map(m) == before, "assign restore"
    except UnicodeError:
        raise
    except ue4ss.UEError as e:
        if "not supported yet" in str(e):
            unsupported.append((class_name, name, str(e)[:80]))
        else:
            errors.append((class_name, name, "UEError " + str(e)[:100]))
    except Exception as e:
        errors.append((class_name, name, repr(e)[:120]))
print("map key->value kinds:", kinds)
print(len(unsupported), "maps hold types not supported yet, e.g.", unsupported[:2])
check("maps read and survive edit/restore cycles", not errors, errors[:4])

errors = []
set_kinds = {}
for (class_name, name), obj in list(sets.items())[:300]:
    s = getattr(obj, name)
    try:
        n = len(s)
        values = s.to_list()
        assert len(values) == n and len(list(s)) == n, "lengths"
        assert len(s.to_set()) == n or any(isinstance(v, ue4ss.Struct) for v in values), "to_set"
        if n:
            x = values[0]
            set_kinds[type(x).__name__] = set_kinds.get(type(x).__name__, 0) + 1
            if isinstance(x, ue4ss.Struct):
                continue
            assert x in s, "contains"
            before = norm_set(s)
            s.discard(x)
            assert x not in s and len(s) == n - 1, "discard"
            s.discard(x)
            s.add(x)
            s.add(x)
            assert norm_set(s) == before and len(s) == n, "re-add"
            s.remove(x)
            try:
                s.remove(x)
                assert False, "remove of a missing element should raise"
            except KeyError:
                pass
            s.update([x])
            saved = s.to_list()
            s.clear()
            assert len(s) == 0, "clear"
            s.update(saved)
            assert norm_set(s) == before, "clear/update restore"
            setattr(obj, name, saved)
            assert norm_set(s) == before, "assign restore"
    except ue4ss.UEError as e:
        if "not supported yet" in str(e):
            unsupported.append((class_name, name, str(e)[:80]))
        else:
            errors.append((class_name, name, "UEError " + str(e)[:100]))
    except Exception as e:
        errors.append((class_name, name, repr(e)[:120]))
print("set element kinds:", set_kinds)
check("sets read and survive edit/restore cycles", not errors, errors[:4])

# empty containers: add a sample, look it up, remove it again (covers sets, which are mostly empty in the menu)
import re
SAMPLES = {"IntProperty": 7, "UInt32Property": 7, "Int64Property": 7, "ByteProperty": 3, "FloatProperty": 1.5, "DoubleProperty": 1.5,
           "BoolProperty": True, "NameProperty": "PyueSampleName", "StrProperty": "pyue sample"}
added = {}
add_errors = []


def object_sample(owner, hint):
    # an object of the class the property wants, if the game has one right now
    return ue4ss.find_first_of(hint) if hint else owner


def cycle_set(owner, name):
    sv = getattr(owner, name)
    element_type = repr(sv).rsplit(" ", 1)[-1].rstrip(">")
    hint = None
    for attempt in range(3):
        sample = object_sample(owner, hint) if element_type == "ObjectProperty" else SAMPLES.get(element_type)
        if sample is None:
            return "no sample available for " + element_type + (" (" + hint + ")" if hint else "")
        try:
            sv.add(sample)
        except ue4ss.UEError as e:
            m = re.search(r"property's class (\w+)", str(e))
            if m and hint is None and len(sv) == 0:
                hint = m.group(1)
                continue
            raise
        assert len(sv) == 1 and sample in sv, "added"
        assert len(sv.to_list()) == 1, "listed"
        sv.add(sample)
        assert len(sv) == 1, "adding twice keeps one"
        sv.discard(sample)
        assert len(sv) == 0 and sample not in sv, "removed"
        sv.update([sample])
        sv.clear()
        assert len(sv) == 0, "cleared"
        return "ok"
    return "gave up"


def cycle_map(owner, name):
    mv = getattr(owner, name)
    parts = repr(mv).rsplit(" of 0 ", 1)
    key_type, _, value_type = parts[1].rstrip(">").partition(" -> ")
    hints = {}
    for attempt in range(3):
        key = object_sample(owner, hints.get("key")) if key_type == "ObjectProperty" else SAMPLES.get(key_type)
        value = object_sample(owner, hints.get("value")) if value_type == "ObjectProperty" else SAMPLES.get(value_type)
        if key is None or value is None:
            return "no sample available for " + key_type + "->" + value_type
        try:
            mv[key] = value
        except ue4ss.UEError as e:
            m = re.search(r"property's class (\w+)", str(e))
            if m and len(mv) == 0 and len(hints) < 2:
                # the message does not say whether the key or the value was refused: try the key first, then the value
                hints["key" if "key" not in hints and key_type == "ObjectProperty" else "value"] = m.group(1)
                continue
            raise
        assert len(mv) == 1 and key in mv, "added"
        assert mv[key] == value or value != value, "read back"
        del mv[key]
        assert len(mv) == 0, "deleted"
        mv.update({key: value})
        mv.clear()
        assert len(mv) == 0, "cleared"
        return "ok"
    return "gave up"


for table, cycle, label in ((sets, cycle_set, "set"), (maps, cycle_map, "map")):
    for (class_name, name), owner in list(table.items()):
        view = getattr(owner, name)
        if len(view):
            continue
        shape = repr(view).rsplit(" of 0 ", 1)[-1].rstrip(">")
        try:
            outcome = cycle(owner, name)
        except Exception as e:
            add_errors.append((class_name, name, shape, repr(e)[:100]))
            try:
                view.clear()
            except Exception:
                pass
            continue
        key = label + ":" + shape + " -> " + outcome
        added[key] = added.get(key, 0) + 1
print("add/remove cycles on empty containers:")
for key, count in sorted(added.items()):
    print("   ", count, key)
if any(k.endswith("-> ok") for k in added) or add_errors:
    check("empty sets and maps accept and release samples", not add_errors and any(k.endswith("-> ok") for k in added), (add_errors[:4]))
else:
    print("empty sets and maps accept and release samples: SKIP (no empty container with a usable element type in this game)")

# bad input changes nothing, and the error types are right
tested_bad = 0
for (class_name, name), obj in list(maps.items())[:300]:
    m = getattr(obj, name)
    if len(m) == 0:
        continue
    k0 = m.keys()[0]
    if isinstance(k0, ue4ss.Struct):
        continue
    before = norm_map(m)
    try:
        m.update([(k0, object())])
        check("bad value refused (" + name + ")", False)
    except (TypeError, ue4ss.UEError, ValueError):
        check("bad value refused and map unchanged (" + name + ")", norm_map(m) == before)
    try:
        m[object()]
        check("bad key refused", False)
    except (TypeError, ue4ss.UEError):
        check("bad key refused", True)
    tested_bad += 1
    break
if not tested_bad:
    print("note: no non-empty map with a non-struct key was found for the bad-input check")

# thread rule and repr
import threading
if maps or sets:
    obj = next(iter((maps or sets).values()))
    name = next(iter(maps or sets))[1]
    view = getattr(obj, name)
    box = []

    def other():
        try:
            len(view)
            box.append("no error")
        except ue4ss.UEError:
            box.append("UEError")
        box.append(repr(view))

    t = threading.Thread(target=other)
    t.start()
    t.join()
    check("len() off the game thread raises UEError", box[:1] == ["UEError"], box)
    check("repr off the game thread does not raise", len(box) == 2, box)
    check("is_valid on a live container", view.is_valid())

print(f"mapsets finished PASS={sum(results)} FAIL={len(results) - sum(results)}")
