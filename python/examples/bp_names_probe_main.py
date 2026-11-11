# Do the identifiers in the generated typings exist at runtime? Blueprint variables can have spaces in their names. No pass/fail.
import ue4ss

sky = ue4ss.find_first_of("BP_Sky_Sphere_C")
for name in ("Sun height", "Sun_height", "Sky material", "Sky_material", "sun height"):
    try:
        print(f"getattr(sky, {name!r}) ->", getattr(sky, name))
    except Exception as e:  # noqa: BLE001
        print(f"getattr(sky, {name!r}) raised {type(e).__name__}: {e}")

import re
for cls in ue4ss.find_all_of("BlueprintGeneratedClass"):
    path = cls.get_full_name().split(" ", 1)[1]
    if path.startswith("/Script/"):
        continue
    props = [p["name"] for p in cls.get_properties(inherited=False)]
    funcs = [n for n in cls.get_function_names() if cls.find_function(n) is not None and cls.find_function(n).get_outer() == cls]
    odd = [n for n in props + funcs if re.sub(r"\W", "_", n) != n]
    print(f"{path.rsplit('.', 1)[-1]}: {len(props)} variables, {len(funcs)} functions, {len(odd)} with a name that is not a Python identifier: {odd[:8]}")
print("names probe finished")
