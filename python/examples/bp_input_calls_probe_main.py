# Which Blueprint functions with array or struct INPUTS does the game call by itself within a few seconds, and what do they receive?
# Hooks every Blueprint function with a non-output array/struct/object parameter (pre only) and reports the ones that fire. No pass/fail.
import ue4ss

SKIP = 0x8000 | 0x400 | 0x100000
candidates = []
for cls in ue4ss.find_all_of("BlueprintGeneratedClass"):
    path = cls.get_full_name().split(" ", 1)[1]
    if not path.startswith("/Game/"):
        continue
    for name in cls.get_function_names():
        func = cls.find_function(name)
        if func is None or func.get_outer() != cls or name.startswith(("ExecuteUbergraph", "BndEvt")):
            continue
        flags = func.get_function_flags()
        params = func.get_params()
        inputs = [p for p in params if not p["is_out"] or p["is_reference"]]
        if flags & SKIP or not any(p["type"] in ("ArrayProperty", "StructProperty") for p in inputs):
            continue
        candidates.append((path, name, inputs))
print("Blueprint functions with array or struct inputs:", len(candidates))
counts = {}
samples = {}
ids = []


def make(path, name, inputs):
    key = f"{path.rsplit('.', 1)[-1]}::{name}"

    def pre(context, params):
        counts[key] = counts.get(key, 0) + 1
        if key not in samples:
            sample = {}
            for p in inputs:
                try:
                    value = getattr(params, p["name"])
                    sample[p["name"]] = f"{p['type']}[{len(value)}]" if p["type"] == "ArrayProperty" else f"{p['type']}"
                except Exception as e:  # noqa: BLE001
                    sample[p["name"]] = f"<{type(e).__name__}>"
            samples[key] = sample
    return pre


for path, name, inputs in candidates:
    try:
        ids.append(ue4ss.register_hook(f"{path}:{name}", pre=make(path, name, inputs)))
    except Exception:  # noqa: BLE001
        pass
print("hooks registered:", len(ids))


def report():
    for pair in ids:
        ue4ss.unregister_hook(pair)
    print("functions called within the window:", len(counts))
    for key, n in sorted(counts.items(), key=lambda item: -item[1])[:25]:
        print(f"CALLED {key} x{n} {samples.get(key)}")
    print("input calls probe finished")


ue4ss.execute_with_delay(5000, report)
