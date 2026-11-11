# Checks TArray properties on real game objects: finds array properties by scanning objects, reads them every way, and
# writes elements back with their own value (so the game's data is unchanged).
import ue4ss

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (" -- " + str(detail)[:200] if detail else ""))


found = {}  # (class name, property) -> (owner, kind)
scanned = 0
for obj in ue4ss.find_objects(class_name="Object", banned_flags=ue4ss.ObjectFlags.RF_ClassDefaultObject | ue4ss.ObjectFlags.RF_ArchetypeObject, limit=6000):
    scanned += 1
    try:
        cls = obj.get_class()
        class_name = cls.get_fname().to_string()
        names = obj.get_property_names()
    except ue4ss.UEError:
        continue
    for name in names:
        key = (class_name, name)
        if key in found:
            continue
        try:
            value = getattr(obj, name)
        except ue4ss.UEError:
            continue
        except AttributeError:
            continue
        if isinstance(value, ue4ss.Array):
            found[key] = obj
print(f"scanned {scanned} objects, found {len(found)} distinct array properties")
check("array properties exist on game objects", len(found) > 0, len(found))

kinds = {}
read_ok = 0
write_ok = 0
errors = []
for (class_name, name), obj in list(found.items())[:300]:
    arr = getattr(obj, name)
    try:
        n = len(arr)
        items = arr.to_list()
        assert len(items) == n
        assert list(arr) == items or n == 0 or any(isinstance(i, ue4ss.Struct) for i in items)
        assert arr[:] == arr.to_list() or any(isinstance(i, ue4ss.Struct) for i in items)
        if n:
            first = arr[0]
            last = arr[-1]
            kind = type(first).__name__
            kinds[kind] = kinds.get(kind, 0) + 1
            try:
                arr[n]
                errors.append((class_name, name, "no IndexError"))
            except IndexError:
                pass
        read_ok += 1
    except Exception as e:
        errors.append((class_name, name, repr(e)[:100]))
        continue
    if n and not errors:
        pass
    # write back an element's own value
    try:
        if n:
            before = arr[0]
            arr[0] = before
            if not isinstance(before, ue4ss.Struct):
                same = arr[0] == before or (before != before)  # NaN
                if not same:
                    errors.append((class_name, name, f"write-back changed {before!r} to {arr[0]!r}"))
                    continue
            write_ok += 1
    except ue4ss.UEError as e:
        # an element type that cannot be read or written yet is reported by name, not hidden
        errors.append((class_name, name, "UEError " + str(e)[:80]))

print("element kinds seen:", kinds)
unsupported = [e for e in errors if e[2].startswith("UEError") and "not supported yet" in e[2]]
real = [e for e in errors if e not in unsupported]
print(f"{len(unsupported)} arrays hold element types not supported yet, e.g.", unsupported[:3])
check("every supported array reads (len, to_list, iteration, slice, IndexError)", not real, real[:5])
check("some arrays were read", read_ok > 0, read_ok)
check("write-back of element 0 left values unchanged", write_ok > 0 and not real, write_ok)

# resizing: append/insert/pop/del cycles on real game arrays within one tick (no game code runs in between), restoring them
def snapshot(arr):
    return [i.to_dict() if isinstance(i, ue4ss.Struct) else i for i in arr.to_list()]


cycled = 0
cycle_kinds = {}
cycle_errors = []
for (class_name, name), obj in list(found.items())[:200]:
    arr = getattr(obj, name)
    try:
        n = len(arr)
        if n == 0:
            continue
        before = snapshot(arr)
        first = arr[0]
        arr.append(first)
        assert len(arr) == n + 1, "append length"
        arr.insert(0, first)
        assert len(arr) == n + 2, "insert length"
        arr.extend([first, first])
        assert len(arr) == n + 4, "extend length"
        arr.pop()
        arr.pop()
        del arr[0]
        popped = arr.pop()
        assert len(arr) == n, "length restored"
        assert snapshot(arr) == before, "contents restored"
        arr[1:1] = [first]          # slice insert, then delete the same range
        del arr[1:2]
        assert snapshot(arr) == before, "slice cycle"
        kind = type(first).__name__
        cycle_kinds[kind] = cycle_kinds.get(kind, 0) + 1
        cycled += 1
    except Exception as e:
        cycle_errors.append((class_name, name, repr(e)[:120]))
print("resize cycles by element kind:", cycle_kinds)
check("append/insert/extend/pop/del/slice cycles restore real game arrays", cycled > 0 and not cycle_errors, (cycled, cycle_errors[:3]))

# grow an empty array and shrink it again: AActor.Tags (TArray<FName>) on the game mode
gm = ue4ss.find_first_of("GameModeBase")
if gm is not None:
    tags = gm.Tags
    original = tags.to_list()
    tags.append("PyueTagOne")
    tags.extend(["PyueTagTwo", ue4ss.FName("PyueTagThree")])
    check("tags grew and read back", [str(t) for t in tags.to_list()][-3:] == ["PyueTagOne", "PyueTagTwo", "PyueTagThree"], tags.to_list())
    gm.Tags = original + [ue4ss.FName("PyueTagFour")]
    check("whole-array assignment on Tags", [str(t) for t in gm.Tags.to_list()] == [str(t) for t in original] + ["PyueTagFour"])
    try:
        gm.Tags = [1]
        check("bad item refused", False)
    except TypeError:
        check("bad item refused and array unchanged", len(gm.Tags) == len(original) + 1)
    gm.Tags = original
    check("Tags restored", [str(t) for t in gm.Tags.to_list()] == [str(t) for t in original])
else:
    print("note: no GameModeBase to resize Tags on")

for label, action, exc in (
    ("bad index del", lambda: getattr(obj0, name0).__delitem__(10 ** 6), IndexError),
    ("step slice assign", lambda: getattr(obj0, name0).__setitem__(slice(None, None, 2), []), ValueError),
):
    if found:
        (class_name0, name0), obj0 = next(iter(found.items()))
        try:
            action()
            check(label + " refused", False)
        except exc:
            check(label + " refused", True)

# a view follows the owner's death state: is_valid and repr never raise
if found:
    obj = next(iter(found.values()))
    arr = getattr(obj, next(iter(found))[1])
    check("is_valid on a live array", arr.is_valid())
    check("repr names the property", "Array" in repr(arr), repr(arr))

# off the game thread
import threading
box = []
if found:
    obj = next(iter(found.values()))
    arr = getattr(obj, next(iter(found))[1])

    def other():
        try:
            len(arr)
            box.append("no error")
        except ue4ss.UEError:
            box.append("UEError")
        box.append(repr(arr))

    t = threading.Thread(target=other)
    t.start()
    t.join()
    check("len() off the game thread raises UEError", box[:1] == ["UEError"], box)
    check("repr off the game thread does not raise", len(box) == 2, box)

print(f"arrays finished PASS={sum(results)} FAIL={len(results) - sum(results)}")
