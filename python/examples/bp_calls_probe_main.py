# What do the calls of BP_Sky_Sphere functions look like after bytecode.scan_calls, and which stay unresolved? No pass/fail.
import ue4ss
from ue4ss import bytecode

sky = ue4ss.static_find_object("/Engine/EngineSky/BP_Sky_Sphere.BP_Sky_Sphere_C")
for fname in sky.get_function_names():
    func = sky.find_function(fname)
    if func is None or func.get_outer() != sky:
        continue
    print("FUNCTION", fname, len(func.get_script()), "bytes")
    for call in bytecode.scan_calls(func):
        print("   ", call["offset"], call["token"], call["target"] if call["target"] else ("UNRESOLVED", call["pointer"], call["name"]))
print("calls probe finished")
