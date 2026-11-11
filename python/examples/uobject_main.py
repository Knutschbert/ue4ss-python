# Copy over python_scripts/main.py to check the UObject wrapper in the game. Prints PASS/FAIL per check.
import threading

import ue4ss


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + detail + ')') if detail else ''}")


PATH = "/Script/CoreUObject.Object"
obj = ue4ss.static_find_object(PATH)
check("static_find_object finds a known class", obj is not None)
check("get_full_name", obj.get_full_name() == "Class " + PATH, obj.get_full_name())
check("get_fname", str(obj.get_fname()) == "Object", repr(obj.get_fname()))
check("get_outer is the package", obj.get_outer() is not None and obj.get_outer().get_full_name().endswith("/Script/CoreUObject"), str(obj.get_outer()))
check("get_class is Class", obj.get_class().get_full_name() == "Class /Script/CoreUObject.Class")
check("is_valid and get_address", obj.is_valid() and isinstance(obj.get_address(), int) and obj.get_address() != 0)
check("same object twice: equal and one hash", obj == ue4ss.static_find_object(PATH) and len({obj, ue4ss.static_find_object(PATH)}) == 1)
check("is_a(Object) by path", obj.is_a(PATH) is True)
check("class is_a Class", obj.get_class().is_a("/Script/CoreUObject.Class") is True)
check("missing path gives None", ue4ss.static_find_object("/Script/DoesNotExist.Nothing") is None)

try:
    obj.is_a("/Script/DoesNotExist.Nothing")
    check("is_a with an unknown class raises UEError", False, "no exception")
except ue4ss.UEError as e:
    check("is_a with an unknown class raises UEError", True, str(e))
try:
    ue4ss.UObject()
    check("UObject() cannot be constructed", False, "no exception")
except TypeError as e:
    check("UObject() cannot be constructed", True, str(e))

# Real instances, found by class name; every game has these.
for class_name in ("GameModeBase", "PlayerController", "World", "GameInstance", "Actor"):
    found = ue4ss.find_first_of(class_name)
    check(f"find_first_of({class_name})", found is not None and found.is_valid(), str(found))

# The game-thread requirement.
seen = {}


def off_thread():
    try:
        obj.get_full_name()
        seen["raised"] = False
    except ue4ss.UEError as e:
        seen["raised"] = "game thread" in str(e)
    seen["valid"] = obj.is_valid()
    seen["repr"] = repr(obj)


worker = threading.Thread(target=off_thread)
worker.start()
worker.join()
check("off-thread get_full_name raises UEError naming the game thread", seen.get("raised") is True)
check("off-thread is_valid and repr keep working", seen.get("valid") is True and isinstance(seen.get("repr"), str))
print("uobject example finished")
