# Do hooks see Blueprint functions that the game's own Blueprint code calls (no Python call involved)? Hook the plain functions of every Blueprint
# class that has live instances (up to a few hundred), wait 3 seconds in the front-end map and report which ones fired by themselves (rqf.3).
import ue4ss

UBERGRAPH, NATIVE, DELEGATE, EVENT, CALLABLE = 0x8000, 0x400, 0x100000, 0x800, 0x4000000
LIMIT = 500

classes = [c for c in ue4ss.find_objects(class_name="BlueprintGeneratedClass", exact_class=True) if c.get_full_name().split(" ", 1)[1].startswith("/Game/")]
hooked = {}
for cls in classes:
    path = cls.get_full_name().split(" ", 1)[1]
    if len(hooked) >= LIMIT:
        break
    live = [o for o in ue4ss.find_all_of(cls.get_fname().to_string()) if o.get_class() == cls]
    if not live:
        continue
    for name in cls.get_function_names():
        func = cls.find_function(name)
        if func is None or func.get_outer().get_full_name().split(" ", 1)[1] != path:
            continue
        flags = func.get_function_flags()
        if flags & (UBERGRAPH | NATIVE | DELEGATE | EVENT) or not flags & CALLABLE or name.startswith(("ExecuteUbergraph", "BndEvt", "UserConstructionScript")):
            continue
        counter = {"calls": 0, "flags": flags, "instances": len(live)}
        try:
            ids = ue4ss.register_hook(f"{path}:{name}", pre=lambda c, p, counter=counter: counter.__setitem__("calls", counter["calls"] + 1))
        except Exception:  # noqa: BLE001
            continue
        hooked[f"{path.rsplit('.', 1)[-1]}::{name}"] = (ids, counter)
        if len(hooked) >= LIMIT:
            break
print(f"hooked {len(hooked)} plain Blueprint functions of classes with live instances")


def report():
    fired = sorted(((c["calls"], k, c["flags"], c["instances"]) for k, (i, c) in hooked.items() if c["calls"]), reverse=True)
    print(f"{len(fired)} of {len(hooked)} fired on their own within 3 seconds (no Python call involved):")
    for calls, key, flags, instances in fired[:25]:
        print(f"  {calls:6d}  {key}  flags={flags:#x}  live instances={instances}")
    for ids, _ in hooked.values():
        ue4ss.unregister_hook(ids)
    print("internal calls probe finished")


ue4ss.execute_with_delay(3000, report)
