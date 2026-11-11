# The modules the typings declare can be imported in the game: ue4ss.engine.<Package> and ue4ss.engine.Content.<mount>.<folders>.
# What an editor accepts also runs. Wrong names are ImportErrors, not empty modules.
import importlib

import ue4ss
from ue4ss import engine


def check(label, ok, detail=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}{(' (' + str(detail)[:300] + ')') if detail else ''}")


def skip(label, why):
    print(f"{label}: SKIP ({why})")


def raises(label, exc, fn):
    try:
        fn()
    except exc as e:
        print(f"{label}: PASS ({type(e).__name__}: {str(e)[:100]})")
        return
    except BaseException as e:  # noqa: BLE001
        print(f"{label}: FAIL (got {type(e).__name__}: {e})")
        return
    print(f"{label}: FAIL (no exception)")


from ue4ss.engine.Engine import Actor, GameModeBase       # noqa: E402

check("a native package module gives the same class as the flat form", Actor is engine.Actor and GameModeBase is engine.find("/Script/Engine.GameModeBase"))
is_chivalry = ue4ss.find_first_of("TBLPlayerController") is not None
if is_chivalry:
    from ue4ss.engine.TBL import TBLGameMode
    check("the game's own module works", TBLGameMode is engine.find("/Script/TBL.TBLGameMode") and issubclass(TBLGameMode, engine.GameMode))
    from ue4ss.engine.Content.Game.Interactables.World.Seatable import LevelActor_Chair_C, LevelActor_Stocks_C
    check("a Blueprint class is imported by its content folder", LevelActor_Chair_C is engine.find("/Game/Interactables/World/Seatable/LevelActor_Chair.LevelActor_Chair_C"))
    check("a Blueprint class that was not loaded is loaded by the import", LevelActor_Stocks_C is engine.find("/Game/Interactables/World/Seatable/LevelActor_Stocks.LevelActor_Stocks_C"))
    check("the imported class finds its instances", isinstance(LevelActor_Chair_C.find_all(), list))
    folder_module = "ue4ss.engine.Content.Game.Interactables.World.Seatable"
else:
    from ue4ss.engine.PyTestGame import PyTestGameMode
    check("the game's own module works", PyTestGameMode is engine.find("/Script/PyTestGame.PyTestGameMode"))
    from ue4ss.engine.Content.Engine.EngineSky import BP_Sky_Sphere_C
    check("a Blueprint class is imported by its content folder", BP_Sky_Sphere_C is engine.find("/Engine/EngineSky/BP_Sky_Sphere.BP_Sky_Sphere_C"))
    check("the imported class finds its instance", len(BP_Sky_Sphere_C.find_all()) == 1)
    folder_module = "ue4ss.engine.Content.Engine.EngineSky"

# an imported class type can be passed where the engine wants a class object (the AI's first real spawn script failed on this: "not _EngineMeta")
from ue4ss.engine.Engine import ESpawnActorCollisionHandlingMethod, GameplayStatics, PlayerController, Transform, Vector   # noqa: E402
from ue4ss.engine.CoreUObject import Quat   # noqa: E402
chair_type = LevelActor_Chair_C if is_chivalry else BP_Sky_Sphere_C
spawn_transform = Transform(Rotation=Quat(**ue4ss.euler_to_quat(0, 0, 0)), Translation=Vector(X=0.0, Y=0.0, Z=4000.0), Scale3D=Vector(X=1, Y=1, Z=1))
spawned = GameplayStatics.BeginDeferredActorSpawnFromClass(PlayerController.find_first(), chair_type, spawn_transform, ESpawnActorCollisionHandlingMethod.AlwaysSpawn, None)
GameplayStatics.FinishSpawningActor(spawned, spawn_transform)
check("an imported class type is accepted as the class to spawn", spawned is not None and spawned.get_class() == chair_type.static_class(), str(spawned))
if spawned is not None:
    spawned.K2_DestroyActor()

raises("an unknown name in a native module is an ImportError", ImportError, lambda: importlib.import_module("ue4ss.engine.Engine").__getattr__("NoSuchClassXyz") if False else exec("from ue4ss.engine.Engine import NoSuchClassXyz"))
raises("an unknown native module is a ModuleNotFoundError", ModuleNotFoundError, lambda: importlib.import_module("ue4ss.engine.NoSuchPackageXyz"))
raises("an unknown name in a content folder is an ImportError (not an empty module)", ImportError, lambda: exec(f"from {folder_module} import TypoXyz_C"))
raises("an unknown content folder is a ModuleNotFoundError", ModuleNotFoundError, lambda: importlib.import_module(folder_module + ".NoSuchFolderXyz"))
print("engine imports finished")
