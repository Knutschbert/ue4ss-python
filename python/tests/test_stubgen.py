"""The in-game stub generator against the mock: files, syntax, and the typing of properties, functions, structs and enums."""
import ast
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python" / "mock"))
import ue4ss  # noqa: E402
from ue4ss import engine  # noqa: E402

OBJECT, ACTOR, PAWN, STATICS = "/Script/CoreUObject.Object", "/Script/Engine.Actor", "/Script/Engine.Pawn", "/Script/Engine.GameplayStatics"
VECTOR, MODE = "/Script/CoreUObject.Vector", "/Script/Engine.EMode"


class StubGenTest(unittest.TestCase):
    def setUp(self):
        ue4ss._mock_reset_objects()
        engine._classes.clear()
        engine._classes[OBJECT] = engine.EngineObject
        engine._enums.clear()
        ue4ss._mock_define_struct_type(VECTOR, types={"X": ("float", 0.0), "Y": ("float", 0.0), "Z": ("float", 0.0)})
        ue4ss._mock_define_enum(MODE, {"EMode::Walk": 0, "EMode::Run": 1, "EMode::EMode_MAX": 2})
        ue4ss._mock_define_class(ACTOR, OBJECT, fields=["Health", "Owner", "Tags"],
                                 field_types={"Health": "float", "Owner": "object:" + ACTOR, "Tags": "array:name"})
        ue4ss._mock_define_class(PAWN, ACTOR, fields=["Mode", "Origin", "Scores"],
                                 field_types={"Mode": "enum:" + MODE, "Origin": "struct:" + VECTOR, "Scores": "map:name=>int32"})
        ue4ss._mock_define_class(STATICS, OBJECT)
        obj = lambda mock_type: {"mock_type": mock_type}  # noqa: E731
        ue4ss._mock_define_function(ACTOR, "GetOwnerOf", flags=0, params=[
            dict(name="Other", type="ObjectProperty", is_out=False, is_return=False, **obj("object:" + ACTOR)),
            dict(name="ReturnValue", type="ObjectProperty", is_out=True, is_return=True, **obj("object:" + ACTOR))])
        ue4ss._mock_define_function(ACTOR, "SplitVec", flags=0, params=[
            dict(name="In", type="StructProperty", is_out=False, is_return=False, **obj("struct:" + VECTOR)),
            dict(name="X", type="FloatProperty", is_out=True, is_return=False, **obj("float")),
            dict(name="Y", type="FloatProperty", is_out=True, is_return=False, **obj("float"))])
        ue4ss._mock_define_function(STATICS, "Twice", flags=0x2000, params=[
            ("A", "IntProperty", False, False), ("ReturnValue", "IntProperty", True, True)])
        ue4ss._mock_define_struct_type("/Script/CoreUObject.FrameNumber", types={"Value": ("int32", 0)})
        ue4ss._mock_define_struct_type("/Script/CoreUObject.FrameTime", types={"FrameNumber": ("struct:/Script/CoreUObject.FrameNumber", {})})
        ue4ss._mock_define_function(ACTOR, "Print", params=[("class", "IntProperty", False, False)])
        ue4ss._mock_define_class("/Script/Engine.Spaced", OBJECT, fields=["Sun height", "Mix up", "Mix_up", "Clean"],
                                 field_types={"Sun height": "float", "Mix up": "int32", "Mix_up": "int32", "Clean": "int32"})
        ue4ss._mock_define_function("/Script/Engine.Spaced", "Get Total", params=[("First value", "IntProperty", False, False), ("ReturnValue", "IntProperty", True, True)])
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.summary = engine.generate_stubs(self.folder.name)
        root = pathlib.Path(self.folder.name) / "ue4ss"
        self.engine_text = (root / "engine" / "Engine.pyi").read_text(encoding="utf-8")
        self.core_text = (root / "engine" / "CoreUObject.pyi").read_text(encoding="utf-8")

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_files_exist_and_every_stub_is_valid_python(self):
        root = pathlib.Path(self.folder.name) / "ue4ss"
        for name in ("__init__.pyi", "py.typed", "engine/__init__.pyi", "engine/Engine.pyi", "engine/CoreUObject.pyi"):
            self.assertTrue((root / name).exists(), name)
        for path in root.rglob("*.pyi"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        self.assertEqual(self.summary["errors"], [])
        self.assertGreaterEqual(self.summary["classes"], 4)
        self.assertEqual((self.summary["structs"], self.summary["enums"]), (3, 1))

    def test_classes_follow_the_hierarchy_and_list_their_own_properties(self):
        self.assertIn("class Pawn(Actor):", self.engine_text)
        self.assertIn("class Actor(Object):", self.engine_text)
        self.assertIn("    Health: float", self.engine_text)
        self.assertIn("    Owner: Actor | None", self.engine_text)
        self.assertIn("    Tags: _ue.Array[_ue.FName]", self.engine_text)
        pawn = self.engine_text.split("class Pawn(Actor):")[1].split("\n\n\n")[0]
        self.assertNotIn("Health", pawn)                       # inherited, not repeated
        self.assertIn("    Scores: _ue.Map[_ue.FName, int]", pawn)
        self.assertIn("    Mode: EMode", pawn)
        self.assertIn("    Origin: Vector", pawn)
        self.assertIn("from ue4ss.engine.CoreUObject import Object, Vector", self.engine_text)
        self.assertIn("class Object(_ue.UObjectBase):", self.core_text)

    def test_functions_results_and_statics(self):
        self.assertIn("def GetOwnerOf(self, Other: Actor | _ue.UObject | None) -> Actor | None: ...", self.engine_text)
        self.assertIn("def SplitVec(self, In: Vector | dict[str, Any]) -> tuple[float, float]: ...", self.engine_text)     # outputs become the result
        self.assertIn("@classmethod\n    def Twice(cls, A: int) -> int: ...", self.engine_text)
        self.assertIn("def find_all(cls) -> list[Self]: ...", self.core_text)

    def test_engine_names_with_spaces_are_declared_by_their_identifier_form(self):
        self.assertIn("    Sun_height: float", self.engine_text)
        self.assertIn("def Get_Total(self, First_value: int) -> int: ...", self.engine_text)

    def test_two_members_with_the_same_identifier_form_declare_only_the_exact_one(self):
        self.assertIn("    Mix_up: int", self.engine_text)
        self.assertNotIn("Mix up:", self.engine_text)
        self.assertIn("not declared, their identifier form is ambiguous", self.engine_text)
        self.assertIn("'Mix up'", self.engine_text)

    def test_python_keywords_get_a_trailing_underscore(self):
        self.assertIn("def Print(self, class_: int) -> None: ...", self.engine_text)

    def test_a_member_named_like_its_type_uses_the_qualified_name(self):
        frame_time = self.core_text.split("class FrameTime(_ue.Struct):")[1].split("\n\n\n")[0]
        self.assertIn("    FrameNumber: _CoreUObject.FrameNumber", frame_time)
        self.assertIn("import ue4ss.engine.CoreUObject as _CoreUObject", self.core_text)

    def test_internal_default_objects_are_not_types(self):
        self.assertNotIn("Default__", self.core_text + self.engine_text)

    def test_structs_and_enums(self):
        self.assertIn("class Vector(_ue.Struct):", self.core_text)
        self.assertIn("    X: float", self.core_text)
        self.assertIn("def __init__(self, *, X: float = ..., Y: float = ..., Z: float = ...) -> None: ...", self.core_text)
        self.assertIn("class EMode(IntEnum):", self.engine_text)
        self.assertIn("    Walk = 0", self.engine_text)
        self.assertIn("    Run = 1", self.engine_text)

    def test_the_base_stub_describes_the_ue4ss_module(self):
        base = (pathlib.Path(self.folder.name) / "ue4ss" / "__init__.pyi").read_text(encoding="utf-8")
        self.assertIn("class UObjectBase(object):", base)
        self.assertIn("class UObject(UObjectBase):", base)
        self.assertIn("class Array(Generic[T]):", base)
        self.assertIn("def find_objects(class_name: str | None = ...", base)
        self.assertIn("class Key(IntEnum):", base)

    def test_package_filter(self):
        with tempfile.TemporaryDirectory() as other:
            summary = engine.generate_stubs(other, packages=["CoreUObject"])
            self.assertEqual(summary["packages"], 1)
            self.assertFalse((pathlib.Path(other) / "ue4ss" / "engine" / "Engine.pyi").exists())

    def test_a_package_brings_the_packages_it_needs(self):
        with tempfile.TemporaryDirectory() as other:
            summary = engine.generate_stubs(other, packages=["Engine"])
            written = sorted(p.name for p in (pathlib.Path(other) / "ue4ss" / "engine").glob("*.pyi"))
            self.assertEqual(written, ["CoreUObject.pyi", "Engine.pyi", "__init__.pyi"])      # Engine imports the base class Object and Vector from CoreUObject
            self.assertEqual(summary["packages"], 2)

    def test_without_dependencies_only_the_requested_packages_are_written(self):
        with tempfile.TemporaryDirectory() as other:
            summary = engine.generate_stubs(other, packages=["Engine"], dependencies=False)
            written = sorted(p.name for p in (pathlib.Path(other) / "ue4ss" / "engine").glob("*.pyi"))
            self.assertEqual(written, ["Engine.pyi", "__init__.pyi"])
            self.assertEqual(summary["packages"], 1)
            self.assertGreater(summary["unresolved_types"], 0)                                 # Object, Vector... have no stub now

    def test_the_summary_counts_types_without_a_stub(self):
        self.assertEqual(self.summary["unresolved_types"], 0)
        self.assertEqual(self.summary["unresolved_references"], 0)


class BlueprintLayoutTest(unittest.TestCase):
    """Blueprint classes get a module per content folder, laid out like the content browser."""

    def setUp(self):
        ue4ss._mock_reset_objects()
        engine._classes.clear()
        engine._classes[OBJECT] = engine.EngineObject
        ue4ss._mock_define_class("/Script/Engine.BlueprintGeneratedClass", "/Script/CoreUObject.Class")
        ue4ss._mock_define_class(ACTOR, OBJECT, fields=["Health"], field_types={"Health": "float"})

        def blueprint(path, parent, fields=(), types=None):
            cls = ue4ss._mock_define_class(path, parent, fields=fields, field_types=types)
            cls._data.class_path = "/Script/Engine.BlueprintGeneratedClass"
            return cls
        blueprint("/Game/Weapons/BP_Sword.BP_Sword_C", ACTOR, ["Sharp edge", "Damage"], {"Sharp edge": "bool", "Damage": "float"})
        blueprint("/Game/Weapons/Melee/BP_Axe.BP_Axe_C", "/Game/Weapons/BP_Sword.BP_Sword_C")
        blueprint("/Game/Weapons/A.Twin_C", ACTOR)
        blueprint("/Game/Weapons/B.Twin_C", ACTOR)
        blueprint("/Game/Menu/BP_Button.BP_Button_C", ACTOR)
        blueprint("/Game/BP_Root.BP_Root_C", ACTOR)
        ue4ss._mock_define_function("/Game/Weapons/BP_Sword.BP_Sword_C", "Get Damage", params=[("Damage", "FloatProperty", True, False)])
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.summary = engine.generate_stubs(self.folder.name, blueprints=True)
        self.root = pathlib.Path(self.folder.name) / "ue4ss" / "engine"

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def read(self, *parts):
        return (self.root.joinpath(*parts)).read_text(encoding="utf-8")

    def test_the_folders_become_packages(self):
        for parts in (("Content", "__init__.pyi"), ("Content", "Game", "__init__.pyi"), ("Content", "Game", "Weapons", "__init__.pyi"),
                      ("Content", "Game", "Weapons", "Melee", "__init__.pyi"), ("Content", "Game", "Menu", "__init__.pyi")):
            self.assertTrue(self.root.joinpath(*parts).exists(), "/".join(parts))
            ast.parse(self.read(*parts))
        self.assertEqual(self.summary["errors"], [])

    def test_classes_sit_in_the_module_of_their_folder_and_inherit_across_modules(self):
        weapons = self.read("Content", "Game", "Weapons", "__init__.pyi")
        self.assertIn("class BP_Sword_C(Actor):", weapons)
        self.assertIn("    Sharp_edge: bool", weapons)
        self.assertIn("def Get_Damage(self) -> float: ...", weapons)
        melee = self.read("Content", "Game", "Weapons", "Melee", "__init__.pyi")
        self.assertIn("from ue4ss.engine.Content.Game.Weapons import BP_Sword_C", melee)
        self.assertIn("class BP_Axe_C(BP_Sword_C):", melee)
        self.assertIn("class BP_Root_C(Actor):", self.read("Content", "Game", "__init__.pyi"))

    def test_same_names_in_one_folder_say_which_asset_defines_them(self):
        weapons = self.read("Content", "Game", "Weapons", "__init__.pyi")
        self.assertIn("class Twin_C__A(Actor):", weapons)
        self.assertIn("class Twin_C__B(Actor):", weapons)

    def test_the_engine_package_does_not_star_import_blueprints(self):
        self.assertNotIn("Content", self.read("__init__.pyi"))

    def test_load_asks_the_asset_registry_and_loads_what_is_missing(self):
        from ue4ss import stubgen
        calls = []

        class FakeLibrary:
            @staticmethod
            def LoadClassAsset_Blocking(path):
                calls.append(path)
                return object() if "Spear" in path else None

        original_assets, original_find = stubgen._blueprint_assets, stubgen._ue.static_find_object
        stubgen._blueprint_assets = lambda folder: ["/Game/Weapons/BP_Sword.BP_Sword", "/Game/Weapons/Melee/BP_Spear.BP_Spear", "/Game/Weapons/Melee/BP_Mace.BP_Mace"]
        stubgen._ue.static_find_object = lambda path: FakeLibrary if path == "/Script/Engine.KismetSystemLibrary" else original_find(path)
        self.addCleanup(lambda: (setattr(stubgen, "_blueprint_assets", original_assets), setattr(stubgen._ue, "static_find_object", original_find)))
        result = stubgen.load_blueprints(["/Game/Weapons"], limit=2)
        self.assertEqual(result, {"found": 3, "already": 1, "loaded": 1, "failed": 1, "skipped": 0})     # Sword is loaded already, Spear loads, Mace fails
        self.assertEqual(calls, ["/Game/Weapons/Melee/BP_Spear.BP_Spear_C", "/Game/Weapons/Melee/BP_Mace.BP_Mace_C"])
        calls.clear()
        again = stubgen.load_blueprints(["/Game/Weapons"], limit=1)
        self.assertEqual((again["loaded"] + again["failed"], again["skipped"]), (1, 1))                    # the limit caps the loading

    def test_a_folder_can_be_requested_by_name(self):
        with tempfile.TemporaryDirectory() as other:
            engine.generate_stubs(other, packages=["Game.Weapons"], blueprints=True, dependencies=False)
            root = pathlib.Path(other) / "ue4ss" / "engine" / "Content" / "Game"
            self.assertTrue((root / "Weapons" / "__init__.pyi").exists())
            self.assertTrue((root / "Weapons" / "Melee" / "__init__.pyi").exists())      # a folder selects its subfolders
            self.assertFalse((root / "Menu" / "__init__.pyi").exists())


if __name__ == "__main__":
    unittest.main()
