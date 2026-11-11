# Finds engine functions with a non-const by-reference parameter (in/out) so a real one can be called.
import ue4ss

found = []
for cls in ue4ss.find_objects(class_name="Class", name=None, limit=0) if False else []:
    pass
libraries = [p for p in (
    "/Script/Engine.KismetMathLibrary", "/Script/Engine.KismetSystemLibrary", "/Script/Engine.KismetStringLibrary", "/Script/Engine.GameplayStatics",
    "/Script/Engine.KismetTextLibrary", "/Script/Engine.KismetArrayLibrary", "/Script/Engine.KismetGuidLibrary", "/Script/Engine.KismetMaterialLibrary",
    "/Script/Engine.BlueprintMapLibrary", "/Script/Engine.BlueprintSetLibrary", "/Script/Engine.KismetRenderingLibrary", "/Script/Engine.BlueprintPathsLibrary",
    "/Script/Engine.VisualLoggerKismetLibrary", "/Script/Engine.AnalyticsBlueprintLibrary", "/Script/Engine.KismetNodeHelperLibrary") if ue4ss.static_find_object(p)]
for lib_path in libraries:
    lib = ue4ss.static_find_object(lib_path)
    for name in lib.get_function_names():
        try:
            fn = lib.find_function(name)
            for p in fn.get_params():
                if p["is_out"] and p["is_reference"] and not p["is_const"] and not p["is_return"]:
                    found.append((lib_path.rsplit(".", 1)[1], name, p["name"], p["type"], [q["name"] for q in fn.get_params()]))
        except Exception as e:
            pass
print("in/out candidates:", len(found))
for item in found[:40]:
    print("  ", item)
print("in-out candidate listing done")
