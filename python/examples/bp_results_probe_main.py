# Where does the value a plain Blueprint function returns live when a post hook runs?
# Hooks pure Blueprint functions without inputs on live instances, calls them from Python and prints, per function: the parameter list
# (name, type, is_out, is_return), what the post hook saw as `result`, and what it could read from each parameter. No pass/fail.
import ue4ss

PURE = 0x10000000
CALLABLE = 0x4000000
SKIP = 0x8000 | 0x400 | 0x100000 | 0x800

classes = [c for c in ue4ss.find_all_of("BlueprintGeneratedClass") if c.get_full_name().split(" ", 1)[1].startswith("/Game/")]
print("loaded Blueprint classes under /Game:", len(classes))
shown = 0
for cls in classes:
    if shown >= 12:
        break
    path = cls.get_full_name().split(" ", 1)[1]
    instances = [i for i in ue4ss.find_all_of(cls.get_fname().to_string()) if i.get_class() == cls]
    if not instances:
        continue
    for name in cls.get_function_names():
        func = cls.find_function(name)
        if func is None or func.get_outer().get_full_name().split(" ", 1)[1] != path:
            continue
        flags = func.get_function_flags()
        params = func.get_params()
        if flags & SKIP or not flags & PURE or not params or any(not p["is_out"] and not p["is_return"] for p in params):
            continue
        seen = {}

        def post(c, p, r, seen=seen, params=params):
            seen["result"] = repr(r)
            for q in params:
                try:
                    seen[q["name"]] = repr(getattr(p, q["name"]))
                except Exception as e:  # noqa: BLE001
                    seen[q["name"]] = f"<{type(e).__name__}: {e}>"
        ids = ue4ss.register_hook(f"{path}:{name}", post=post, instance=instances[0])
        try:
            value = getattr(instances[0], name)()
        except Exception as e:  # noqa: BLE001
            value = f"call failed {type(e).__name__}: {e}"
        ue4ss.unregister_hook(ids)
        sig = ", ".join(f"{q['name']}:{q['type']}{'(out)' if q['is_out'] else ''}{'(ret)' if q['is_return'] else ''}" for q in params)
        print(f"{path.rsplit('.', 1)[-1]}::{name} ({sig}) -> call returned {value!r}; post saw {seen}")
        shown += 1
        break
print("results probe finished")
