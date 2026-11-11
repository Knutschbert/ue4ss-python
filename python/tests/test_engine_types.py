"""ue4ss.engine against the mock: types from reflection, isinstance, static calls, structs, enums."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python" / "mock"))
import ue4ss  # noqa: E402
from ue4ss import engine  # noqa: E402

OBJECT, ACTOR, PAWN, STATICS = "/Script/CoreUObject.Object", "/Script/Engine.Actor", "/Script/Engine.Pawn", "/Script/Engine.GameplayStatics"
VECTOR, COLOR = "/Script/CoreUObject.Vector", "/Script/Engine.EWidgetColor"


class EngineTypesTest(unittest.TestCase):
    def setUp(self):
        ue4ss._mock_reset_objects()
        engine._classes.clear()
        engine._classes["/Script/CoreUObject.Object"] = engine.EngineObject
        engine._enums.clear()
        ue4ss._mock_define_class(ACTOR, OBJECT)
        ue4ss._mock_define_class(PAWN, ACTOR)
        ue4ss._mock_define_class(STATICS, OBJECT)
        ue4ss._mock_define_function(STATICS, "Twice", flags=0x2000, impl=lambda b: setattr(b, "ReturnValue", b.A * 2), params=[
            ("A", "IntProperty", False, False), ("ReturnValue", "IntProperty", True, True)])
        ue4ss._mock_define_struct_type(VECTOR, types={"X": ("float", 0.0), "Y": ("float", 0.0), "Z": ("float", 0.0)})
        ue4ss._mock_define_enum(COLOR, {"EWidgetColor::Red": 0, "EWidgetColor::Blue": 2, "EWidgetColor::None": 3})
        self.pawn = ue4ss._mock_create_object("/Game/Map.Pawn_1", PAWN)
        self.actor = ue4ss._mock_create_object("/Game/Map.Actor_1", ACTOR)

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_classes_are_created_on_first_use_and_cached(self):
        pawn_type = engine.Pawn
        self.assertIs(pawn_type, engine.Pawn)
        self.assertIs(pawn_type, engine.find(PAWN))
        self.assertIs(engine.package("Engine").Pawn, pawn_type)
        self.assertEqual(repr(pawn_type), "<engine class /Script/Engine.Pawn>")

    def test_hierarchy_mirrors_the_engine(self):
        self.assertTrue(issubclass(engine.Pawn, engine.Actor))
        self.assertTrue(issubclass(engine.Actor, engine.EngineObject))
        self.assertFalse(issubclass(engine.Actor, engine.Pawn))

    def test_isinstance_for_wrapped_objects(self):
        self.assertTrue(isinstance(self.pawn, engine.Pawn))
        self.assertTrue(isinstance(self.pawn, engine.Actor))
        self.assertFalse(isinstance(self.actor, engine.Pawn))
        self.assertFalse(isinstance(5, engine.Actor))
        self.assertFalse(isinstance(None, engine.Actor))

    def test_find_all_and_find_first(self):
        self.assertEqual(set(engine.Actor.find_all()), {self.pawn, self.actor})
        self.assertEqual(engine.Pawn.find_all(), [self.pawn])
        self.assertEqual(engine.Pawn.find_first(), self.pawn)

    def test_an_engine_class_type_is_accepted_where_a_class_object_is_wanted(self):
        ue4ss._mock_define_function(STATICS, "Echo", flags=0x2000, impl=lambda b: setattr(b, "ReturnValue", b.Cls), params=[
            {"name": "Cls", "type": "ObjectProperty", "is_out": False, "is_return": False, "mock_type": "object:/Script/CoreUObject.Class"},
            {"name": "ReturnValue", "type": "ObjectProperty", "is_out": True, "is_return": True, "mock_type": "object:/Script/CoreUObject.Class"}])
        self.assertEqual(engine.GameplayStatics.Echo(engine.Pawn), engine.Pawn.static_class())       # the type stands for its class object
        self.assertEqual(engine.GameplayStatics.Echo(engine.Pawn.static_class()), engine.Pawn.static_class())
        with self.assertRaises(TypeError):
            engine.GameplayStatics.Echo(int)                                                          # a type that is no engine class is refused
        with self.assertRaises(TypeError):
            engine.GameplayStatics.Echo(5)

    def test_static_function_calls_run_on_the_default_object(self):
        self.assertEqual(engine.GameplayStatics.Twice(21), 42)
        with self.assertRaises(AttributeError):
            engine.GameplayStatics.NoSuchFunction
        self.assertIn("Twice", dir(engine.GameplayStatics))

    def test_classes_cannot_be_constructed(self):
        with self.assertRaises(TypeError):
            engine.Actor()

    def test_structs_and_enums(self):
        vector = engine.Vector(X=1.0, Y=2.0)
        self.assertTrue(isinstance(vector, engine.Vector))
        self.assertEqual((vector.X, vector.Y, vector.Z), (1.0, 2.0, 0.0))
        color = engine.EWidgetColor
        self.assertEqual((color.Red, color.Blue), (0, 2))
        self.assertEqual(color.None_, 3)                          # a keyword-named member is also available with the underscore the stubs use
        self.assertEqual(getattr(color, "None"), 3)
        self.assertIs(engine.EWidgetColor, color)
        with self.assertRaises(TypeError):
            engine.Vector(1.0)

    def test_a_blueprint_class_that_was_unloaded_gets_a_new_python_class(self):
        first = engine.Pawn
        ue4ss._mock_destroy_object(ue4ss.static_find_object(PAWN))      # the class object goes away, with its default object
        ue4ss._mock_destroy_object(ue4ss.static_find_object("/Script/Engine.Default__Pawn"))
        ue4ss._mock_define_class(PAWN, ACTOR)                             # and is loaded again as a new object
        again = engine.find(PAWN)
        self.assertIsNot(again, first)
        self.assertTrue(issubclass(again, engine.Actor))
        self.assertTrue(again._uclass.is_valid())

    def test_blueprint_classes_are_loaded_on_demand(self):
        loaded = []

        def load(block):
            loaded.append(block.Asset)
            ue4ss._mock_define_class("/Game/Folder/BP_Thing_C", ACTOR)
            block.ReturnValue = ue4ss.static_find_object("/Game/Folder/BP_Thing_C")

        ue4ss._mock_define_class("/Script/Engine.KismetSystemLibrary", OBJECT)
        ue4ss._mock_define_function("/Script/Engine.KismetSystemLibrary", "LoadClassAsset_Blocking", flags=0x2000, impl=load, params=[
            ("Asset", "StrProperty", False, False), {"name": "ReturnValue", "type": "ObjectProperty", "is_out": True, "is_return": True, "mock_type": "object:/Script/CoreUObject.Object"}])
        thing = engine.find("/Game/Folder/BP_Thing_C")
        self.assertEqual(loaded, ["/Game/Folder/BP_Thing_C"])
        self.assertTrue(issubclass(thing, engine.Actor))
        with self.assertRaises(engine.EngineError):
            engine.find("/Game/Folder/Other_C", load=False)

    def test_unknown_names(self):
        with self.assertRaises(engine.EngineError):
            engine.NoSuchEngineThing
        with self.assertRaises(engine.EngineError):
            engine.find("/Script/Engine.Nothing")
        with self.assertRaises(engine.EngineError):
            engine.from_object(self.actor)


class EngineImportTest(unittest.TestCase):
    """The modules the typings declare can be imported in the game too (ue4ss.engine.<Package>, ue4ss.engine.Content.<folders>)."""

    def setUp(self):
        ue4ss._mock_reset_objects()
        engine._classes.clear()
        engine._classes["/Script/CoreUObject.Object"] = engine.EngineObject
        engine._enums.clear()
        for name in [n for n in sys.modules if n.startswith("ue4ss.engine.")]:
            del sys.modules[name]
        ue4ss._mock_define_class(ACTOR, OBJECT)
        ue4ss._mock_define_class("/Game/Weapons/BP_Sword.BP_Sword_C", ACTOR)
        ue4ss._mock_define_class("/Game/Weapons/Melee/A.Twin_C", ACTOR)
        ue4ss._mock_define_struct_type("/Game/Weapons/SwordStats.SwordStats", types={"Damage": ("float", 0.0)})
        ue4ss._mock_define_enum("/Game/Weapons/EEdge.EEdge", {"EEdge::Sharp": 0})

    def tearDown(self):
        ue4ss._mock_reset_objects()
        for name in [n for n in sys.modules if n.startswith("ue4ss.engine.")]:
            del sys.modules[name]

    def test_native_package_module(self):
        from ue4ss.engine.Engine import Actor
        self.assertIs(Actor, engine.Actor)
        with self.assertRaises(ImportError):
            from ue4ss.engine.Engine import NoSuchClass  # noqa: F401
        with self.assertRaises(ModuleNotFoundError):
            import ue4ss.engine.NoSuchPackage  # noqa: F401

    def test_a_name_from_another_package_is_still_found_when_unique(self):
        ue4ss._mock_define_struct_type(VECTOR, types={"X": ("float", 0.0)})
        from ue4ss.engine.Engine import Vector          # Vector is in CoreUObject; the Engine module finds it by its unique short name
        self.assertIs(Vector, engine.find(VECTOR))

    def test_content_module_mirrors_the_asset_folder(self):
        from ue4ss.engine.Content.Game.Weapons import BP_Sword_C, EEdge, SwordStats
        self.assertIs(BP_Sword_C, engine.find("/Game/Weapons/BP_Sword.BP_Sword_C"))
        self.assertTrue(issubclass(BP_Sword_C, engine.Actor))
        self.assertTrue(issubclass(EEdge, __import__("enum").IntEnum))
        self.assertEqual(SwordStats(Damage=3.0).Damage, 3.0)

    def test_a_class_defined_by_an_asset_of_another_name_uses_the_double_underscore_form(self):
        from ue4ss.engine.Content.Game.Weapons.Melee import Twin_C__A
        self.assertIs(Twin_C__A, engine.find("/Game/Weapons/Melee/A.Twin_C"))

    def test_unknown_content_names_fail_like_imports(self):
        with self.assertRaises(ImportError):
            from ue4ss.engine.Content.Game.Weapons import BP_Axe_C  # noqa: F401
        with self.assertRaises(ImportError):
            from ue4ss.engine.Content.Game import BP_Sword_C  # noqa: F401      # the folder level matters


if __name__ == "__main__":
    unittest.main()
