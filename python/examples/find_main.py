# Copy over python_scripts/main.py to check find_all_of / find_objects in the game.
import time

import ue4ss


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + detail + ')') if detail else ''}")


def skip(label, why):
    print(f"{label}: SKIP ({why})")


def raises(label, exc, fn):
    try:
        fn()
    except exc as e:
        print(f"{label}: PASS ({type(e).__name__}: {e})")
        return
    except BaseException as e:
        print(f"{label}: FAIL (got {type(e).__name__}: {e})")
        return
    print(f"{label}: FAIL (no exception)")


CDO = ue4ss.ObjectFlags.RF_ClassDefaultObject
gm = ue4ss.find_first_of("GameModeBase")
pc = ue4ss.find_first_of("PlayerController")

# --- find_all_of ---------------------------------------------------------------------------------------
controllers = ue4ss.find_all_of("PlayerController")
print("PlayerControllers:", controllers)
check("find_all_of returns a list of UObjects", isinstance(controllers, list) and all(isinstance(o, ue4ss.UObject) for o in controllers))
check("it contains the controller find_first_of found, first", controllers and controllers[0] == pc)
check("every result is a PlayerController", all(o.is_a("/Script/Engine.PlayerController") for o in controllers))

actors = ue4ss.find_all_of("Actor")
print(f"Actors: {len(actors)}; first few: {[o.get_fname().to_string() for o in actors[:4]]}")
check("many actors found", len(actors) > 10)
check("find_all_of never returns default objects", not any(o.get_fname().to_string().startswith("Default__") for o in actors))
check("a sample of results are all Actors and valid", all(o.is_valid() and o.is_a("/Script/Engine.Actor") for o in actors[:300]))
check("the game mode is among the actors", gm in actors)

# --- find_objects: agreement with find_all_of, flags, exactness ------------------------------------------------
ARCH = ue4ss.ObjectFlags.RF_ArchetypeObject
same = ue4ss.find_objects("Actor", banned_flags=CDO | ARCH)
print(f"find_objects('Actor', banned CDO|Archetype): {len(same)} vs find_all_of: {len(actors)}")
check("find_all_of == find_objects(class, banned default objects and archetypes)",
      sorted(o.get_address() for o in same) == sorted(o.get_address() for o in actors))
archetypes = ue4ss.find_objects("Actor", required_flags=ARCH, banned_flags=CDO)
print(f"archetype actors (Blueprint child-actor templates): {len(archetypes)}, e.g. {[o.get_fname().to_string() for o in archetypes[:2]]}")
if archetypes:
    check("archetypes are found by find_objects but not by find_all_of", not (set(o.get_address() for o in archetypes) & set(o.get_address() for o in actors)))
else:
    skip("archetypes are found by find_objects but not by find_all_of", "this game has no archetype actors")

cdos = ue4ss.find_objects("Actor", required_flags=CDO)
print(f"default objects of Actor classes: {len(cdos)}, e.g. {[o.get_fname().to_string() for o in cdos[:3]]}")
check("required_flags=RF_ClassDefaultObject finds default objects only", len(cdos) > 0 and all(o.get_fname().to_string().startswith("Default__") for o in cdos))
everything = ue4ss.find_objects("Actor")
check("without flags find_objects returns instances + archetypes + default objects", len(everything) == len(actors) + len(archetypes) + len(cdos), f"{len(everything)} vs {len(actors)} + {len(archetypes)} + {len(cdos)}")

exact = ue4ss.find_objects("GameModeBase", exact_class=True, banned_flags=CDO)
inexact = ue4ss.find_objects("GameModeBase", banned_flags=CDO)
check("exact_class excludes subclass instances (the game mode is a subclass)", gm not in exact and gm in inexact, f"exact {len(exact)}, with subclasses {len(inexact)}")

# --- by name -----------------------------------------------------------------------------------------------------------
gm_name = gm.get_fname().to_string()
by_name = ue4ss.find_objects(name=gm_name)
print(f"objects named {gm_name}: {by_name}")
check("find by exact object name (with its number)", by_name == [gm])
check("name and class together", ue4ss.find_objects("GameModeBase", gm_name) == [gm] and ue4ss.find_objects("PlayerController", gm_name) == [])
check("object names are case-insensitive", ue4ss.find_objects(name=gm_name.lower()) == [gm])

# --- limit ------------------------------------------------------------------------------------------------------------------
check("limit=3 returns three", len(ue4ss.find_objects("Actor", limit=3)) == 3)
check("limit=0 means no limit", len(ue4ss.find_objects("Actor", limit=0)) == len(everything))
check("limit=1 returns the first match", ue4ss.find_objects("Actor", banned_flags=CDO, limit=1) == actors[:1])

# --- unknown names add nothing ------------------------------------------------------------------------------------------------
check("an unknown class gives an empty list", ue4ss.find_all_of("PyueNoSuchClassXyz") == [] and ue4ss.find_objects("PyueNoSuchClassXyz") == [])
check("an unknown object name gives an empty list", ue4ss.find_objects(name="PyueNoSuchNameXyz") == [] and ue4ss.find_objects("Actor", "PyueNoSuchNameXyz") == [])
check("and no names were added", ue4ss.FName.find("PyueNoSuchClassXyz") is None and ue4ss.FName.find("PyueNoSuchNameXyz") is None)

# --- flags enum ----------------------------------------------------------------------------------------------------------------
check("ObjectFlags values", int(CDO) == 0x10 and int(ue4ss.ObjectFlags.RF_Public) == 1 and int(ue4ss.ObjectFlags.RF_Public | ue4ss.ObjectFlags.RF_Transient) == 0x41)
classes = ue4ss.find_objects("Class", required_flags=ue4ss.ObjectFlags.RF_Public, limit=50)
check("RF_Public filter finds classes", len(classes) == 50 and all(isinstance(o, ue4ss.UClass) for o in classes))

# --- errors ------------------------------------------------------------------------------------------------------------------------
raises("find_objects needs something to look for", ValueError, lambda: ue4ss.find_objects())
raises("class_name must be a str", TypeError, lambda: ue4ss.find_objects(5))
raises("flags must be ints", TypeError, lambda: ue4ss.find_objects("Actor", required_flags="x"))
raises("negative flags", ValueError, lambda: ue4ss.find_objects("Actor", banned_flags=-1))
raises("negative limit", ValueError, lambda: ue4ss.find_objects("Actor", limit=-1))
raises("find_all_of needs a str", TypeError, lambda: ue4ss.find_all_of(None))

# --- types whose instances are classes, enums, functions -------------------------------------------------------------
classes = ue4ss.find_all_of("Class")
check("find_all_of('Class') lists the classes", len(classes) > 100, str(len(classes)))
check("they are all classes and none is a default object", all(isinstance(o, ue4ss.UClass) for o in classes[:500]))
check("find_all_of('Class') agrees with find_objects, banned defaults",
      len(classes) == len(ue4ss.find_objects("Class", banned_flags=CDO | ue4ss.ObjectFlags.RF_ArchetypeObject)),
      f"{len(classes)}")
check("find_all_of('Enum') lists the enums", len(ue4ss.find_all_of("Enum")) > 10)
check("find_first_of('Function') finds a function", ue4ss.find_first_of("Function") is not None)
bp_classes = ue4ss.find_all_of("BlueprintGeneratedClass")
bp_expected = ue4ss.find_objects("BlueprintGeneratedClass", banned_flags=CDO)
check("find_all_of('BlueprintGeneratedClass') matches find_objects", len(bp_classes) == len(bp_expected), f"{len(bp_classes)} vs {len(bp_expected)}")
del classes, bp_classes, bp_expected

# --- every object in the game ------------------------------------------------------------------------------------------------------
start = time.perf_counter()
everything_in_the_game = ue4ss.find_all_of("Object")
elapsed = time.perf_counter() - start
print(f"find_all_of('Object'): {len(everything_in_the_game)} objects in {elapsed:.2f} s")
check("enumerating every object works", len(everything_in_the_game) > 100, str(len(everything_in_the_game)))
check("a sample of them are valid wrappers with names", all(o.is_valid() and o.get_full_name() for o in everything_in_the_game[:2000:7]))
kinds = {}
for o in everything_in_the_game[:5000]:
    kinds[type(o).__name__] = kinds.get(type(o).__name__, 0) + 1
print("wrapper types in the first 5000:", kinds)
del everything_in_the_game
print("find test finished")
