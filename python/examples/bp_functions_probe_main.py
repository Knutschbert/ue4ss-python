# Plain Blueprint functions (not events, not the ubergraph): find loaded Blueprint classes that declare some, hook a few on live instances, call them
# from Python and see whether pre/post fire and what the parameters and the result look like (rqf.4). Prints a report; no pass/fail.
import ue4ss
from ue4ss import engine

EVENT = 0x800          # FUNC_Event: set on event stubs; every Blueprint-defined function also has FUNC_BlueprintEvent (0x8000000), which is not a marker
UBERGRAPH = 0x8000
NATIVE = 0x400
PURE = 0x10000000
CALLABLE = 0x4000000
DELEGATE = 0x100000

candidates = []
everything = ue4ss.find_objects(class_name="BlueprintGeneratedClass", exact_class=True)
print("BlueprintGeneratedClass objects (find_objects):", len(everything), "| find_all_of:", len(ue4ss.find_all_of("BlueprintGeneratedClass")))
classes = [c for c in everything if c.get_full_name().split(" ", 1)[1].startswith("/Game/")]
print("loaded Blueprint classes under /Game:", len(classes))
for cls in classes:
    path = cls.get_full_name().split(" ", 1)[1]
    for name in cls.get_function_names():
        func = cls.find_function(name)
        if func is None or func.get_outer().get_full_name().split(" ", 1)[1] != path:
            continue
        flags = func.get_function_flags()
        if flags & (UBERGRAPH | NATIVE | DELEGATE | EVENT) or not flags & CALLABLE:
            continue
        if name.startswith(("ExecuteUbergraph", "BndEvt", "OnRep_", "UserConstructionScript")):
            continue
        params = func.get_params()
        candidates.append((path, name, flags, params, cls))
print("plain Blueprint functions (not events, not ubergraph):", len(candidates))
for path, name, flags, params, cls in candidates[:25]:
    sig = ", ".join(f"{p['name']}:{p['type']}{'(out)' if p['is_out'] else ''}" for p in params)
    print(f"  {path.rsplit('.', 1)[-1]}::{name}  flags={flags:#x}  ({sig})")

tried = 0
report = []
for path, name, flags, params, cls in candidates:
    if tried >= 6:
        break
    if not flags & PURE or any(not p["is_out"] for p in params):
        continue                                   # a pure function without inputs: safe to call
    instances = ue4ss.find_all_of(cls.get_fname().to_string())
    instances = [i for i in instances if i.get_class() == cls]
    if not instances:
        continue
    tried += 1
    counts = {"pre": 0, "post": 0, "result": None}
    target = instances[0]
    ids = ue4ss.register_hook(f"{path}:{name}", pre=lambda c, p, counts=counts: counts.__setitem__("pre", counts["pre"] + 1),
                              post=lambda c, p, r, counts=counts: (counts.__setitem__("post", counts["post"] + 1), counts.__setitem__("result", r)), instance=target)
    try:
        value = getattr(target, name)()
        report.append((f"{path.rsplit('.', 1)[-1]}::{name}", counts["pre"], counts["post"], counts["result"], value))
    except Exception as e:  # noqa: BLE001
        report.append((f"{path.rsplit('.', 1)[-1]}::{name}", counts["pre"], counts["post"], "call failed", f"{type(e).__name__}: {e}"))
    ue4ss.unregister_hook(ids)
print("direct calls of pure Blueprint functions on a live instance with an instance-filtered hook:")
for row in report:
    print("  ", row)
print("functions probe finished")
