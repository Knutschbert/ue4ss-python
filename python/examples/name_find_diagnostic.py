# Diagnostic: which existing names does FName.find (FNAME_Find) fail to see?
import ue4ss

names = ["Actor", "Engine", "/Script/Engine", "ENetRole", "NetRole", "ROLE_Authority", "Vector", "/Script/CoreUObject", "CoreUObject",
         "Pawn", "GameModeBase", "Object", "Class", "None", "EVectorQuantization", "ENetMode", "ENetDormancy", "Rotator", "URL"]
for n in names:
    print(f"find({n!r}) =", repr(ue4ss.FName.find(n)))
for path in ["/Script/Engine.Actor", "/Script/Engine.ENetRole", "/Script/Engine.ENetMode", "/Script/Engine.ENetDormancy",
             "/Script/Engine.EVectorQuantization", "/Script/CoreUObject.Vector", "/Script/Engine.URL", "/Script/Engine.Pawn"]:
    print(f"static_find_object({path!r}) =", ue4ss.static_find_object(path))
print("diagnostic finished")
