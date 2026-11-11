# SetGameTimeOnMeshComponents(MeshComponents: array) of BaseCharacter_C is called by the game itself (rqf.13). What does it do with the array?
import ue4ss
from ue4ss import bytecode

found = [c for c in ue4ss.find_all_of("BlueprintGeneratedClass") if c.get_fname().to_string() == "BaseCharacter_C"]
cls = found[0]
path = cls.get_full_name().split(" ", 1)[1]
print("class:", path)
func = cls.find_function("SetGameTimeOnMeshComponents")
print("params:", [(p["name"], p["type"], p["is_out"], p["is_reference"]) for p in func.get_params()], "flags", hex(func.get_function_flags()))
for call in bytecode.scan_calls(func):
    print("  call", call["offset"], call["token"], call["target"])
nodes, _ = bytecode.parse_auto(func.get_script())
print("tokens:", [n.name for top in nodes for n in top.walk()][:60])
print("array probe finished")
