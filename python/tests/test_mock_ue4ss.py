import contextlib
import inspect
import io
import re
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python" / "mock"))
import ue4ss  # noqa: E402  (the mock)


def cpp_version() -> str:
    text = (ROOT / "core" / "include" / "pyue" / "version.hpp").read_text()
    major = re.search(r"version_major\s*=\s*(\d+)", text).group(1)
    minor = re.search(r"version_minor\s*=\s*(\d+)", text).group(1)
    return f"{major}.{minor}"


class MockModuleTest(unittest.TestCase):
    def test_version_matches_cpp(self):
        self.assertEqual(ue4ss.__version__, cpp_version())

    def test_exception_hierarchy(self):
        self.assertTrue(issubclass(ue4ss.UEError, Exception))
        self.assertTrue(issubclass(ue4ss.InvalidObjectError, ue4ss.UEError))

    def test_selftest_kinds_match_real_module_mapping(self):
        # Same expectations as check_error_mapping() in src/PythonRuntime.cpp.
        self.assertIsNone(ue4ss._selftest_raise("none"))
        for kind, expected in (
            ("ue", ue4ss.UEError),
            ("invalid", ue4ss.InvalidObjectError),
            ("property", AttributeError),
            ("std", ue4ss.UEError),
            ("unknown", ue4ss.UEError),
            ("memory", MemoryError),
            ("nonsense", ValueError),
        ):
            with self.subTest(kind=kind):
                with self.assertRaises(expected):
                    ue4ss._selftest_raise(kind)

    def test_selftest_requires_str(self):
        with self.assertRaises(TypeError):
            ue4ss._selftest_raise(1)

    def test_kinds_are_the_ones_the_in_game_self_check_uses(self):
        # check_error_mapping() in src/PythonRuntime.cpp exercises these; keep the mock's list in step.
        source = (ROOT / "src" / "PythonRuntime.cpp").read_text()
        self.assertEqual(set(re.findall(r'expect\("(\w+)"', source)), {"ue", "invalid", "property", "std", "unknown", "memory", "nonsense"})


class SchedulingTest(unittest.TestCase):
    def setUp(self):
        ue4ss._mock_reset()
        self.now = 1000.0
        ue4ss._mock_now = lambda: self.now

    def tearDown(self):
        import time
        ue4ss._mock_now = time.monotonic
        ue4ss._mock_reset()

    def test_queued_tasks_run_in_order_on_the_game_thread(self):
        seen = []
        ue4ss.execute_in_game_thread(lambda: seen.append(("a", ue4ss.is_game_thread())))
        ue4ss.execute_in_game_thread(lambda: seen.append(("b", ue4ss.is_game_thread())))
        self.assertFalse(ue4ss.is_game_thread())
        self.assertEqual(seen, [])
        self.assertEqual(ue4ss._mock_run_pending(), 2)
        self.assertEqual(seen, [("a", True), ("b", True)])
        self.assertFalse(ue4ss.is_game_thread())

    def test_delay_waits_and_orders_by_due_time(self):
        seen = []
        ue4ss.execute_with_delay(300, lambda: seen.append("late"))
        ue4ss.execute_with_delay(100, lambda: seen.append("early"))
        ue4ss.execute_in_game_thread(lambda: seen.append("now"))
        ue4ss._mock_run_pending()
        self.assertEqual(seen, ["now"])
        self.now += 0.15
        ue4ss._mock_run_pending()
        self.assertEqual(seen, ["now", "early"])
        self.now += 0.2
        ue4ss._mock_run_pending()
        self.assertEqual(seen, ["now", "early", "late"])

    def test_task_queued_while_running_waits_for_next_tick(self):
        seen = []
        ue4ss.execute_in_game_thread(lambda: (seen.append(1), ue4ss.execute_in_game_thread(lambda: seen.append(2))))
        ue4ss._mock_run_pending()
        self.assertEqual(seen, [1])
        ue4ss._mock_run_pending()
        self.assertEqual(seen, [1, 2])

    def test_exceptions_are_logged_and_do_not_stop_later_tasks(self):
        seen = []
        ue4ss.execute_in_game_thread(lambda: 1 / 0)
        ue4ss.execute_in_game_thread(lambda: (_ for _ in ()).throw(SystemExit(3)))
        ue4ss.execute_in_game_thread(lambda: seen.append("ran"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ue4ss._mock_run_pending()
        self.assertEqual(seen, ["ran"])
        self.assertIn("ZeroDivisionError", err.getvalue())
        self.assertIn("sys.exit() inside a callback is ignored", err.getvalue())

    def test_future_gets_result_and_exception(self):
        ok = ue4ss.call_in_game_thread(lambda a, b=1: (ue4ss.is_game_thread(), a + b), 2, b=3)
        bad = ue4ss.call_in_game_thread(lambda: 1 / 0)
        self.assertFalse(ok.done())
        ue4ss._mock_run_pending()
        self.assertEqual(ok.result(timeout=1), (True, 5))
        self.assertIsInstance(bad.exception(timeout=1), ZeroDivisionError)

    def test_future_can_be_awaited_from_another_thread(self):
        import threading
        results = []
        future = ue4ss.call_in_game_thread(lambda: 42)
        waiter = threading.Thread(target=lambda: results.append(future.result(timeout=5)))
        waiter.start()
        ue4ss._mock_run_pending()
        waiter.join(5)
        self.assertEqual(results, [42])

    def test_argument_validation(self):
        with self.assertRaises(TypeError):
            ue4ss.execute_in_game_thread(5)
        with self.assertRaises(TypeError):
            ue4ss.execute_with_delay(10, "nope")
        with self.assertRaises(TypeError):
            ue4ss.call_in_game_thread(None)
        for bad in (-1, float("nan")):
            with self.subTest(delay=bad):
                with self.assertRaises(ValueError):
                    ue4ss.execute_with_delay(bad, lambda: None)


class FNameTest(unittest.TestCase):
    def test_text_and_number(self):
        plain = ue4ss.FName("MockPlainName")
        self.assertEqual(str(plain), "MockPlainName")
        self.assertEqual(plain.number, 0)
        numbered = ue4ss.FName("MockPlainName_7")
        self.assertEqual(str(numbered), "MockPlainName_7")
        self.assertEqual(numbered.number, 8)  # Name_N has number N + 1
        self.assertEqual(numbered.comparison_index, plain.comparison_index)
        self.assertEqual(repr(numbered), "FName('MockPlainName_7')")

    def test_suffix_rules(self):
        # Leading zeros and non-digits stay part of the name; "_0" is a real number.
        self.assertEqual(ue4ss.FName("MockSuffix_0").number, 1)
        self.assertEqual(ue4ss.FName("MockSuffix_01").number, 0)
        self.assertEqual(ue4ss.FName("MockSuffix_x").number, 0)
        self.assertEqual(ue4ss.FName("_5").number, 0)
        self.assertEqual(str(ue4ss.FName("MockSuffix_01")), "MockSuffix_01")

    def test_none(self):
        none = ue4ss.FName("None")
        self.assertEqual((none.comparison_index, none.number), (0, 0))
        self.assertEqual(none, ue4ss.FName("none"))

    def test_equality_is_case_insensitive(self):
        a = ue4ss.FName("MockCaseName")
        self.assertEqual(a, ue4ss.FName("mockcasename"))
        self.assertEqual(a, "MOCKCASENAME")
        self.assertNotEqual(a, "MockCaseName_1")
        self.assertNotEqual(a, "MockNeverCreated")
        self.assertNotEqual(a, 5)
        self.assertEqual(str(ue4ss.FName("mockcasename")), "MockCaseName")  # first spelling wins

    def test_eq_with_str_does_not_add_names(self):
        self.assertIsNone(ue4ss.FName.find("MockNeverAddedByCompare"))
        ue4ss.FName("MockCompareTarget") == "MockNeverAddedByCompare"
        self.assertIsNone(ue4ss.FName.find("MockNeverAddedByCompare"))

    def test_find(self):
        self.assertIsNone(ue4ss.FName.find("MockNotYetCreated"))
        created = ue4ss.FName("MockNotYetCreated_2")
        self.assertEqual(ue4ss.FName.find("MockNotYetCreated_2"), created)
        self.assertEqual(ue4ss.FName.find("MockNotYetCreated_9").comparison_index, created.comparison_index)
        self.assertIsNotNone(ue4ss.FName.find("None"))

    def test_hash_and_copy(self):
        a = ue4ss.FName("MockHashName_3")
        b = ue4ss.FName(a)
        self.assertEqual(a, b)
        self.assertEqual(hash(a), hash(ue4ss.FName("mockhashname_3")))
        self.assertEqual(len({a, b, ue4ss.FName("MockHashName_4")}), 2)

    def test_type_errors(self):
        with self.assertRaises(TypeError):
            ue4ss.FName(5)
        with self.assertRaises(TypeError):
            ue4ss.FName.find(5)

    def test_non_ascii_round_trip(self):
        text = "h\u00e9llo \U0001F600 \u2713"
        self.assertEqual(ue4ss._selftest_fstring(text), text)
        self.assertEqual(str(ue4ss.FName("Mock" + text)), "Mock" + text)


class UObjectTest(unittest.TestCase):
    def setUp(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_class("/Script/Engine.Actor", "/Script/CoreUObject.Object")
        ue4ss._mock_define_class("/Script/Engine.Pawn", "/Script/Engine.Actor")
        self.pawn = ue4ss._mock_create_object("/Game/Maps/Arena.Arena:Pawn_0", "/Script/Engine.Pawn")

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_identity_and_names(self):
        self.assertEqual(self.pawn.get_full_name(), "Pawn /Game/Maps/Arena.Arena:Pawn_0")
        self.assertEqual(self.pawn.get_fname(), "Pawn_0")
        self.assertEqual(self.pawn.get_fname().number, 1)
        self.assertEqual(self.pawn.get_class().get_full_name(), "Class /Script/Engine.Pawn")
        outer = self.pawn.get_outer()
        self.assertEqual(outer.get_fname(), "Arena")
        self.assertEqual(outer.get_full_name(), "Package /Game/Maps/Arena.Arena")
        self.assertIsInstance(self.pawn.get_address(), int)
        self.assertEqual(repr(self.pawn), "<UObject 'Pawn /Game/Maps/Arena.Arena:Pawn_0'>")

    def test_find(self):
        self.assertEqual(ue4ss.static_find_object("/Game/Maps/Arena.Arena:Pawn_0"), self.pawn)
        self.assertIsNone(ue4ss.static_find_object("/Game/Nope"))
        self.assertEqual(ue4ss.find_first_of("Pawn"), self.pawn)
        self.assertEqual(ue4ss.find_first_of("Actor"), self.pawn)  # subclasses count
        self.assertIsNone(ue4ss.find_first_of("NeverDefined"))

    def test_find_first_of_skips_default_objects(self):
        # _mock_define_class already created /Script/Engine.Default__Pawn
        self.assertIsNotNone(ue4ss.static_find_object("/Script/Engine.Default__Pawn"))
        self.assertEqual(ue4ss.find_first_of("Pawn"), self.pawn)

    def test_is_a(self):
        actor = ue4ss.static_find_object("/Script/Engine.Actor")
        self.assertTrue(self.pawn.is_a(actor))
        self.assertTrue(self.pawn.is_a("/Script/Engine.Actor"))
        self.assertTrue(self.pawn.is_a("/Script/CoreUObject.Object"))
        ue4ss._mock_define_class("/Script/Engine.Controller", "/Script/CoreUObject.Object")
        self.assertFalse(self.pawn.is_a("/Script/Engine.Controller"))
        with self.assertRaises(ue4ss.UEError):
            self.pawn.is_a("/Script/Engine.NoSuchClass")
        with self.assertRaises(ue4ss.UEError):
            self.pawn.is_a(self.pawn)  # not a class
        with self.assertRaises(TypeError):
            self.pawn.is_a(5)

    def test_destroyed_object_raises_everywhere_but_is_valid_and_repr(self):
        stale = self.pawn
        ue4ss._mock_destroy_object(self.pawn)
        self.assertFalse(stale.is_valid())
        for call in (stale.get_full_name, stale.get_fname, stale.get_class, stale.get_outer, stale.get_address,
                     lambda: stale.is_a("/Script/Engine.Actor")):
            with self.assertRaises(ue4ss.InvalidObjectError):
                call()
        self.assertIn("destroyed", repr(stale))
        self.assertIsNone(ue4ss.static_find_object("/Game/Maps/Arena.Arena:Pawn_0"))

    def test_slot_reuse_does_not_revive_old_wrapper(self):
        old = self.pawn
        ue4ss._mock_destroy_object(old)
        new = ue4ss._mock_create_object("/Game/Maps/Arena.Arena:Pawn_0", "/Script/Engine.Pawn")
        self.assertFalse(old.is_valid())
        self.assertTrue(new.is_valid())
        self.assertNotEqual(old, new)
        self.assertEqual(len({old, new}), 2)

    def test_equality_and_hash(self):
        again = ue4ss.static_find_object("/Game/Maps/Arena.Arena:Pawn_0")
        self.assertEqual(self.pawn, again)
        self.assertEqual(hash(self.pawn), hash(again))
        self.assertNotEqual(self.pawn, ue4ss.static_find_object("/Script/Engine.Actor"))
        self.assertNotEqual(self.pawn, "Pawn")

    def test_cannot_construct(self):
        with self.assertRaises(TypeError):
            ue4ss.UObject()

    def test_argument_types(self):
        with self.assertRaises(TypeError):
            ue4ss.static_find_object(1)
        with self.assertRaises(TypeError):
            ue4ss.find_first_of(None)

    def test_game_thread_requirement_when_enforced(self):
        ue4ss._mock_enforce_game_thread = True
        with self.assertRaises(ue4ss.UEError) as caught:
            self.pawn.get_full_name()
        self.assertIn("game thread", str(caught.exception))
        with self.assertRaises(ue4ss.UEError):
            ue4ss.find_first_of("Pawn")
        self.assertTrue(self.pawn.is_valid())  # never raises
        self.assertIn("index=", repr(self.pawn))  # repr never raises either
        results = []
        ue4ss.execute_in_game_thread(lambda: results.append(self.pawn.get_full_name()))
        ue4ss._mock_run_pending()
        self.assertEqual(results, ["Pawn /Game/Maps/Arena.Arena:Pawn_0"])


class PropertyTest(unittest.TestCase):
    def setUp(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_class("/Script/Engine.Actor", "/Script/CoreUObject.Object")
        self.actor = ue4ss._mock_create_object("/Game/Arena.Arena:Actor_0", "/Script/Engine.Actor")
        for name, type_name, value in (
            ("bHidden", "bool", False), ("NetTag", "int32", 5), ("Tiny", "int8", -3), ("Small", "uint8", 7),
            ("Big", "uint64", 2**63), ("Signed64", "int64", -5), ("Short", "int16", 1), ("UShort", "uint16", 2),
            ("UInt", "uint32", 3), ("Freq", "float", 100.0), ("Precise", "double", 0.1), ("State", "name", "Playing"),
            ("Options", "str", "?Game=Test"),
        ):
            ue4ss._mock_define_property(self.actor, name, type_name, value)
        ue4ss._mock_define_property(self.actor, "Owner", "unsupported:ObjectProperty")

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_read_each_type(self):
        a = self.actor
        self.assertIs(a.bHidden, False)
        self.assertEqual((a.NetTag, a.Tiny, a.Small, a.Big, a.Signed64), (5, -3, 7, 2**63, -5))
        self.assertEqual(a.Freq, 100.0)
        self.assertEqual(a.Precise, 0.1)
        self.assertEqual(a.State, "Playing")
        self.assertIsInstance(a.State, ue4ss.FName)
        self.assertEqual(a.Options, "?Game=Test")

    def test_write_round_trips(self):
        a = self.actor
        a.bHidden = True
        a.NetTag = -1
        a.Freq = 7.25
        a.Precise = 1e300
        a.State = "Dead"
        a.Options = "h\u00e9llo \U0001F600"
        self.assertEqual((a.bHidden, a.NetTag, a.Freq, a.Precise), (True, -1, 7.25, 1e300))
        self.assertEqual(a.State, ue4ss.FName("Dead"))
        self.assertEqual(a.Options, "h\u00e9llo \U0001F600")
        a.State = ue4ss.FName("Playing")
        self.assertEqual(str(a.State), "Playing")

    def test_float_property_narrows_to_32_bit(self):
        self.actor.Freq = 0.1
        self.assertEqual(self.actor.Freq, 0.10000000149011612)  # nearest float32
        self.assertNotEqual(self.actor.Freq, 0.1)

    def test_integer_ranges(self):
        a = self.actor
        for name, low, high in (("Tiny", -128, 127), ("Small", 0, 255), ("Short", -32768, 32767), ("UShort", 0, 65535),
                                ("NetTag", -2**31, 2**31 - 1), ("UInt", 0, 2**32 - 1), ("Signed64", -2**63, 2**63 - 1),
                                ("Big", 0, 2**64 - 1)):
            with self.subTest(name=name):
                setattr(a, name, low)
                setattr(a, name, high)
                self.assertEqual(getattr(a, name), high)
                with self.assertRaises(OverflowError):
                    setattr(a, name, low - 1)
                with self.assertRaises(OverflowError):
                    setattr(a, name, high + 1)
                self.assertEqual(getattr(a, name), high)  # a failed write changes nothing

    def test_type_errors_and_bool_rules(self):
        a = self.actor
        with self.assertRaises(TypeError):
            a.NetTag = 1.5
        with self.assertRaises(TypeError):
            a.NetTag = "3"
        with self.assertRaises(TypeError):
            a.Freq = "1.0"
        with self.assertRaises(TypeError):
            a.Options = 5
        with self.assertRaises(TypeError):
            a.State = 5
        with self.assertRaises(TypeError):
            a.bHidden = "yes"
        with self.assertRaises(ValueError):
            a.bHidden = 2
        a.bHidden = 1
        self.assertIs(a.bHidden, True)
        a.NetTag = True  # bool is an int
        self.assertEqual(a.NetTag, 1)

    def test_float_overflow(self):
        with self.assertRaises(OverflowError):
            self.actor.Freq = 3.5e38
        with self.assertRaises(OverflowError):
            self.actor.Freq = 10**400
        self.actor.Freq = float("inf")
        self.assertEqual(self.actor.Freq, float("inf"))
        self.actor.Precise = 3.5e38  # fine for a double

    def test_missing_unsupported_and_readonly(self):
        a = self.actor
        with self.assertRaises(AttributeError):
            a.NoSuchProperty
        with self.assertRaises(AttributeError):
            a.NoSuchProperty = 1
        self.assertFalse(hasattr(a, "NoSuchProperty"))
        self.assertFalse(hasattr(a, "__wrapped__"))
        with self.assertRaises(ue4ss.UEError) as caught:
            a.Owner
        self.assertIn("ObjectProperty", str(caught.exception))
        with self.assertRaises(ue4ss.UEError):
            a.Owner = None
        with self.assertRaises(AttributeError):
            a.get_full_name = 1  # methods are read-only
        with self.assertRaises(TypeError):
            del a.NetTag

    def test_names_are_case_insensitive_like_the_engine(self):
        self.assertEqual(self.actor.nettag, 5)
        self.actor.NETTAG = 6
        self.assertEqual(self.actor.NetTag, 6)

    def test_explicit_accessors(self):
        self.assertEqual(self.actor.get_property_value("NetTag"), 5)
        self.actor.set_property_value("NetTag", 9)
        self.assertEqual(self.actor.NetTag, 9)
        with self.assertRaises(AttributeError):
            self.actor.get_property_value("Nope")

    def test_destroyed_object_raises(self):
        stale = self.actor
        ue4ss._mock_destroy_object(self.actor)
        with self.assertRaises(ue4ss.InvalidObjectError):
            stale.NetTag
        with self.assertRaises(ue4ss.InvalidObjectError):
            stale.NetTag = 1

    def test_game_thread_requirement(self):
        ue4ss._mock_enforce_game_thread = True
        with self.assertRaises(ue4ss.UEError):
            self.actor.NetTag
        with self.assertRaises(ue4ss.UEError):
            self.actor.NetTag = 1


class ObjectAndStructTest(unittest.TestCase):
    VECTOR = {"X": ("float", 0.0), "Y": ("float", 0.0), "Z": ("float", 0.0)}

    def setUp(self):
        ue4ss._mock_reset_objects()
        for path, parent in (("/Script/Engine.Actor", "/Script/CoreUObject.Object"), ("/Script/Engine.Pawn", "/Script/Engine.Actor"),
                             ("/Script/Engine.Controller", "/Script/Engine.Actor"), ("/Script/Engine.GameState", "/Script/Engine.Actor")):
            ue4ss._mock_define_class(path, parent)
        self.pawn = ue4ss._mock_create_object("/Game/Arena.Arena:Pawn_0", "/Script/Engine.Pawn")
        self.ctrl = ue4ss._mock_create_object("/Game/Arena.Arena:Controller_0", "/Script/Engine.Controller")
        ue4ss._mock_define_property(self.ctrl, "Pawn", "object:/Script/Engine.Pawn", self.pawn)
        ue4ss._mock_define_property(self.ctrl, "Owner", "object:/Script/Engine.Actor", None)
        ue4ss._mock_define_property(self.ctrl, "GameState", "object:/Script/Engine.GameState", None)
        ue4ss._mock_define_property(self.ctrl, "Movement", "struct:FRepMovement", {
            "LinearVelocity": ("struct:FVector", dict(self.VECTOR, X=("float", 1.5))),
            "AngularVelocity": ("struct:FVector", dict(self.VECTOR)),
            "Rotation": ("struct:FRotator", {"Pitch": ("float", 0.0), "Yaw": ("float", 90.0), "Roll": ("float", 0.0)}),
            "bRepPhysics": ("bool", False),
            "Quantization": ("uint8", 2),
            "Mystery": ("unsupported:ArrayProperty", None),
        })

    def tearDown(self):
        ue4ss._mock_reset_objects()

    # --- object properties ---
    def test_object_read_and_none(self):
        self.assertEqual(self.ctrl.Pawn, self.pawn)
        self.assertIsInstance(self.ctrl.Pawn, ue4ss.UObject)
        self.assertIsNone(self.ctrl.Owner)

    def test_object_write(self):
        self.ctrl.Owner = self.pawn  # a Pawn is an Actor
        self.assertEqual(self.ctrl.Owner, self.pawn)
        self.ctrl.Owner = None
        self.assertIsNone(self.ctrl.Owner)
        self.ctrl.Pawn = None
        self.assertIsNone(self.ctrl.Pawn)

    def test_object_write_checks_class_and_type(self):
        with self.assertRaises(ue4ss.UEError) as caught:
            self.ctrl.GameState = self.pawn  # a Pawn is not a GameState
        self.assertIn("GameState", str(caught.exception))
        with self.assertRaises(TypeError):
            self.ctrl.Pawn = "not an object"
        with self.assertRaises(TypeError):
            self.ctrl.Pawn = 5
        self.assertEqual(self.ctrl.Pawn, self.pawn)  # failed writes change nothing

    def test_object_write_of_destroyed_object_raises(self):
        victim = ue4ss._mock_create_object("/Game/Arena.Arena:Pawn_1", "/Script/Engine.Pawn")
        ue4ss._mock_destroy_object(victim)
        with self.assertRaises(ue4ss.InvalidObjectError):
            self.ctrl.Pawn = victim

    # --- struct views ---
    def test_nested_read(self):
        movement = self.ctrl.Movement
        self.assertIsInstance(movement, ue4ss.Struct)
        self.assertEqual(movement.get_struct_name(), "FRepMovement")
        self.assertEqual(movement.LinearVelocity.X, 1.5)
        self.assertEqual(movement.Rotation.Yaw, 90.0)
        self.assertIs(movement.bRepPhysics, False)
        self.assertEqual(movement.Quantization, 2)
        self.assertEqual(repr(movement), "<Struct 'FRepMovement' view>")

    def test_views_write_through_to_the_owner(self):
        first = self.ctrl.Movement
        first.LinearVelocity.X = 9.5
        first.bRepPhysics = True
        second = self.ctrl.Movement  # a new view sees the write
        self.assertEqual(second.LinearVelocity.X, 9.5)
        self.assertIs(second.bRepPhysics, True)
        self.assertEqual(self.ctrl.Movement.LinearVelocity.Y, 0.0)  # neighbours untouched
        self.assertEqual(first.LinearVelocity.X, 9.5)  # and the old view still sees it

    def test_field_conversion_rules_apply_inside_structs(self):
        movement = self.ctrl.Movement
        with self.assertRaises(OverflowError):
            movement.Quantization = 256
        with self.assertRaises(TypeError):
            movement.LinearVelocity.X = "1"
        with self.assertRaises(ValueError):
            movement.bRepPhysics = 2
        with self.assertRaises(AttributeError):
            movement.NoSuchField
        with self.assertRaises(ue4ss.UEError):
            movement.Mystery

    def test_struct_copy_from_a_view(self):
        movement = self.ctrl.Movement
        movement.LinearVelocity.Z = 4.0
        movement.AngularVelocity = movement.LinearVelocity
        self.assertEqual(movement.AngularVelocity.Z, 4.0)
        self.assertEqual(movement.AngularVelocity.X, 1.5)
        movement.AngularVelocity.Z = 7.0  # the copy is independent of the source
        self.assertEqual(movement.LinearVelocity.Z, 4.0)

    def test_struct_assign_from_dict_updates_only_listed_fields(self):
        movement = self.ctrl.Movement
        movement.Rotation = {"Pitch": 10.0, "Roll": -5.0}
        self.assertEqual((movement.Rotation.Pitch, movement.Rotation.Yaw, movement.Rotation.Roll), (10.0, 90.0, -5.0))
        with self.assertRaises(AttributeError):
            movement.Rotation = {"Nope": 1}
        with self.assertRaises(TypeError):
            movement.Rotation = {"Pitch": "x"}

    def test_struct_assign_type_errors(self):
        movement = self.ctrl.Movement
        with self.assertRaises(ue4ss.UEError):
            movement.LinearVelocity = movement.Rotation  # FRotator is not an FVector
        with self.assertRaises(TypeError):
            movement.LinearVelocity = 5
        with self.assertRaises(TypeError):
            movement.LinearVelocity = [1, 2, 3]

    def test_to_dict_and_property_names(self):
        movement = self.ctrl.Movement
        self.assertEqual(movement.to_dict(), {
            "LinearVelocity": {"X": 1.5, "Y": 0.0, "Z": 0.0},
            "AngularVelocity": {"X": 0.0, "Y": 0.0, "Z": 0.0},
            "Rotation": {"Pitch": 0.0, "Yaw": 90.0, "Roll": 0.0},
            "bRepPhysics": False,
            "Quantization": 2,
        })  # the unsupported field is left out
        self.assertEqual(movement.get_property_names(), ["LinearVelocity", "AngularVelocity", "Rotation", "bRepPhysics", "Quantization", "Mystery"])
        self.assertEqual(self.ctrl.get_property_names(), ["Pawn", "Owner", "GameState", "Movement"])
        self.assertEqual(movement.get_property_value("Quantization"), 2)
        movement.set_property_value("Quantization", 3)
        self.assertEqual(movement.Quantization, 3)

    def test_view_dies_with_its_owner(self):
        view = self.ctrl.Movement.LinearVelocity
        movement = self.ctrl.Movement
        ue4ss._mock_destroy_object(self.ctrl)
        self.assertFalse(view.is_valid())
        self.assertFalse(movement.is_valid())
        for call in (lambda: view.X, lambda: movement.LinearVelocity, lambda: movement.to_dict(),
                     lambda: movement.get_struct_name(), lambda: setattr(view, "X", 1.0)):
            with self.assertRaises(ue4ss.InvalidObjectError):
                call()
        self.assertIn("destroyed", repr(view))

    def test_view_equality_hash_and_construction(self):
        a = self.ctrl.Movement
        b = self.ctrl.Movement
        self.assertEqual(a, b)
        self.assertEqual(hash(a), hash(b))
        self.assertNotEqual(a, a.LinearVelocity)
        self.assertNotEqual(a.LinearVelocity, a.AngularVelocity)
        with self.assertRaises(TypeError):
            ue4ss.Struct()
        with self.assertRaises(AttributeError):
            a.to_dict = 1  # methods are read-only
        with self.assertRaises(TypeError):
            del a.Quantization

    def test_game_thread_requirement(self):
        view = self.ctrl.Movement
        ue4ss._mock_enforce_game_thread = True
        with self.assertRaises(ue4ss.UEError):
            view.Quantization
        with self.assertRaises(ue4ss.UEError):
            view.Quantization = 1
        self.assertTrue(view.is_valid())  # never raises
        self.assertIn("not readable here", repr(view))


class KindsTest(unittest.TestCase):
    def setUp(self):
        ue4ss._mock_reset_objects()
        self.actor = ue4ss._mock_define_class("/Script/Engine.Actor", "/Script/CoreUObject.Object", fields=["bHidden", "NetTag"])
        self.pawn = ue4ss._mock_define_class("/Script/Engine.Pawn", "/Script/Engine.Actor", fields=["Health"])
        self.vector = ue4ss._mock_define_struct_type("/Script/CoreUObject.Vector", fields=["X", "Y", "Z"])
        self.jump = ue4ss._mock_define_function("/Script/Engine.Actor", "SetActorHiddenInGame", flags=0x04000400,
                                                params=[("bNewHidden", "BoolProperty", False, False)])
        self.heal = ue4ss._mock_define_function("/Script/Engine.Pawn", "Heal", flags=0x400,
                                                params=[("Amount", "FloatProperty", False, False), ("Result", "BoolProperty", True, True)])
        self.role = ue4ss._mock_define_enum("/Script/Engine.ENetRole", {"ENetRole::ROLE_None": 0, "ENetRole::ROLE_Authority": 3})
        self.instance = ue4ss._mock_create_object("/Game/Arena.Arena:Pawn_0", "/Script/Engine.Pawn")

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_most_specific_type_is_chosen(self):
        self.assertIs(type(self.pawn), ue4ss.UClass)
        self.assertIs(type(self.vector), ue4ss.UScriptStruct)
        self.assertIs(type(self.heal), ue4ss.UFunction)
        self.assertIs(type(self.role), ue4ss.UEnum)
        self.assertIs(type(self.instance), ue4ss.UObject)
        self.assertIs(type(ue4ss.static_find_object("/Script/Engine.Pawn")), ue4ss.UClass)
        self.assertIs(type(self.instance.get_class()), ue4ss.UClass)
        for wrapper in (self.pawn, self.vector, self.heal, self.role):
            self.assertIsInstance(wrapper, ue4ss.UObject)
        for wrapper in (self.pawn, self.vector, self.heal):
            self.assertIsInstance(wrapper, ue4ss.UStruct)
        self.assertNotIsInstance(self.role, ue4ss.UStruct)
        self.assertNotIsInstance(self.instance, ue4ss.UStruct)

    def test_subtype_equality_with_a_plain_wrapper_of_the_same_object(self):
        self.assertEqual(self.pawn, ue4ss.static_find_object("/Script/Engine.Pawn"))
        self.assertEqual(len({self.pawn, ue4ss.static_find_object("/Script/Engine.Pawn")}), 1)

    def test_super_and_property_names(self):
        self.assertEqual(self.pawn.get_super(), self.actor)
        self.assertEqual(self.actor.get_super().get_full_name(), "Class /Script/CoreUObject.Object")
        self.assertIsNone(ue4ss.static_find_object("/Script/CoreUObject.Object").get_super())
        self.assertEqual(self.pawn.get_property_names(), ["bHidden", "NetTag", "Health"])  # parents first
        self.assertEqual(self.vector.get_property_names(), ["X", "Y", "Z"])
        self.assertIsNone(self.vector.get_super())

    def test_functions(self):
        self.assertEqual(self.pawn.get_function_names(), ["SetActorHiddenInGame", "Heal"])
        self.assertEqual(self.actor.get_function_names(), ["SetActorHiddenInGame"])
        self.assertEqual(self.pawn.find_function("Heal"), self.heal)
        self.assertEqual(self.pawn.find_function("setactorhiddeningame"), self.jump)  # in a parent, case-insensitive
        self.assertIsNone(self.actor.find_function("Heal"))  # not visible from the parent
        self.assertIsNone(self.pawn.find_function("Nope"))
        with self.assertRaises(TypeError):
            self.pawn.find_function(5)

    def test_function_params_and_flags(self):
        self.assertEqual(self.heal.get_function_flags(), 0x400)
        self.assertEqual([(p["name"], p["type"], p["is_out"], p["is_return"]) for p in self.heal.get_params()],
                         [("Amount", "FloatProperty", False, False), ("Result", "BoolProperty", True, True)])
        self.assertEqual(set(self.heal.get_params()[0]), {"name", "type", "is_out", "is_return", "is_const", "is_reference", "class", "struct", "enum", "array_dim", "inner"})
        self.assertIsInstance(self.heal.get_super(), type(None))

    def test_cdo_and_is_child_of(self):
        self.assertEqual(self.pawn.get_cdo().get_full_name(), "Pawn /Script/Engine.Default__Pawn")
        self.assertTrue(self.pawn.is_child_of(self.actor))
        self.assertTrue(self.pawn.is_child_of("/Script/Engine.Pawn"))  # a class is a child of itself
        self.assertTrue(self.pawn.is_child_of("/Script/CoreUObject.Object"))
        self.assertFalse(self.actor.is_child_of(self.pawn))
        with self.assertRaises(ue4ss.UEError):
            self.pawn.is_child_of("/Script/Engine.NoSuchClass")
        with self.assertRaises(TypeError):
            self.pawn.is_child_of(5)
        with self.assertRaises(TypeError):
            self.pawn.is_child_of(self.instance)  # an instance, not a class

    def test_enum(self):
        self.assertEqual(self.role.get_names(), {"ROLE_None": 0, "ROLE_Authority": 3})  # prefix removed
        self.assertEqual(self.role.get_name_by_value(3), "ROLE_Authority")
        self.assertIsNone(self.role.get_name_by_value(99))
        self.assertEqual(self.role.get_value_by_name("ROLE_Authority"), 3)
        self.assertEqual(self.role.get_value_by_name("ENetRole::ROLE_Authority"), 3)
        self.assertIsNone(self.role.get_value_by_name("ROLE_Nope"))
        with self.assertRaises(TypeError):
            self.role.get_name_by_value("3")
        with self.assertRaises(TypeError):
            self.role.get_value_by_name(3)

    def test_kind_types_cannot_be_constructed(self):
        for cls in (ue4ss.UStruct, ue4ss.UClass, ue4ss.UScriptStruct, ue4ss.UFunction, ue4ss.UEnum):
            with self.subTest(cls=cls.__name__):
                with self.assertRaises(TypeError):
                    cls()

    def test_destroyed_wrappers_raise(self):
        stale = self.pawn
        ue4ss._mock_destroy_object(self.pawn)
        for call in (stale.get_super, stale.get_property_names, stale.get_function_names, stale.get_cdo,
                     lambda: stale.find_function("Heal"), lambda: stale.is_child_of("/Script/Engine.Actor")):
            with self.assertRaises(ue4ss.InvalidObjectError):
                call()

    def test_kind_methods_require_the_game_thread_when_enforced(self):
        ue4ss._mock_enforce_game_thread = True
        with self.assertRaises(ue4ss.UEError):
            self.pawn.get_function_names()
        with self.assertRaises(ue4ss.UEError):
            self.role.get_names()


class OwnedStructTest(unittest.TestCase):
    VECTOR = {"X": ("float", 0.0), "Y": ("float", 0.0), "Z": ("float", 0.0)}

    def setUp(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_class("/Script/Engine.Actor", "/Script/CoreUObject.Object")
        self.vector_type = ue4ss._mock_define_struct_type("/Script/CoreUObject.Vector", types=self.VECTOR)
        self.url_type = ue4ss._mock_define_struct_type("/Script/Engine.URL", types={
            "Host": ("str", "localhost"), "Port": ("int32", 7777), "Valid": ("bool", True),
            "Where": ("struct:/Script/CoreUObject.Vector", dict(self.VECTOR, X=("float", 1.0))),
        })
        self.actor = ue4ss._mock_create_object("/Game/Arena.Arena:Actor_0", "/Script/Engine.Actor")
        ue4ss._mock_define_property(self.actor, "Location", "struct:/Script/CoreUObject.Vector", self.VECTOR)
        ue4ss._mock_define_property(self.actor, "Destination", "struct:/Script/CoreUObject.Vector", self.VECTOR)

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_make_struct_with_defaults_and_fields(self):
        url = ue4ss.make_struct("/Script/Engine.URL")
        self.assertTrue(url.is_owned())
        self.assertEqual((url.Host, url.Port, url.Valid, url.Where.X), ("localhost", 7777, True, 1.0))
        url2 = ue4ss.make_struct(self.url_type, Host="example.org", Port=80)
        self.assertEqual((url2.Host, url2.Port), ("example.org", 80))
        self.assertEqual(url.Host, "localhost")  # independent values
        self.assertEqual(repr(url), "<Struct 'URL' owned>")
        self.assertEqual(url.get_struct_name(), "URL")  # the short name, as in the real module

    def test_fields_can_be_nested_dicts(self):
        url = ue4ss.make_struct("/Script/Engine.URL", Where={"Y": 2.5})
        self.assertEqual((url.Where.X, url.Where.Y), (1.0, 2.5))

    def test_nested_views_write_through_to_the_owned_value(self):
        url = ue4ss.make_struct("/Script/Engine.URL")
        where = url.Where
        where.Z = 9.0
        self.assertEqual(url.Where.Z, 9.0)
        self.assertTrue(where.is_owned())
        del url  # the nested view keeps working
        self.assertEqual(where.Z, 9.0)

    def test_conversion_rules_apply(self):
        with self.assertRaises(OverflowError):
            ue4ss.make_struct("/Script/Engine.URL", Port=2**31)
        with self.assertRaises(TypeError):
            ue4ss.make_struct("/Script/Engine.URL", Host=5)
        with self.assertRaises(AttributeError):
            ue4ss.make_struct("/Script/Engine.URL", Nope=1)

    def test_copy_of_a_view_is_independent_and_outlives_the_object(self):
        self.actor.Location.X = 3.0
        copy = self.actor.Location.copy()
        self.assertTrue(copy.is_owned())
        self.assertFalse(self.actor.Location.is_owned())
        self.assertEqual(copy.X, 3.0)
        copy.X = 4.0
        self.assertEqual(self.actor.Location.X, 3.0)  # the copy does not write back
        self.actor.Location.X = 5.0
        self.assertEqual(copy.X, 4.0)  # and the source does not write into the copy
        view = self.actor.Location
        ue4ss._mock_destroy_object(self.actor)
        self.assertFalse(view.is_valid())
        self.assertTrue(copy.is_valid())
        self.assertEqual(copy.X, 4.0)  # an owned struct outlives its source object
        with self.assertRaises(ue4ss.InvalidObjectError):
            view.X

    def test_copy_of_an_owned_struct(self):
        a = ue4ss.make_struct("/Script/CoreUObject.Vector", X=1.0)
        b = a.copy()
        b.X = 2.0
        self.assertEqual((a.X, b.X), (1.0, 2.0))
        self.assertNotEqual(a, b)

    def test_assigning_an_owned_struct_copies_it_into_the_property(self):
        value = ue4ss.make_struct("/Script/CoreUObject.Vector", X=1.0, Y=2.0, Z=3.0)
        self.actor.Destination = value
        self.assertEqual(self.actor.Destination.to_dict(), {"X": 1.0, "Y": 2.0, "Z": 3.0})
        value.X = 99.0  # later changes to the owned value do not reach the property
        self.assertEqual(self.actor.Destination.X, 1.0)
        self.actor.Destination.X = 7.0  # and the property does not reach the value
        self.assertEqual(value.X, 99.0)

    def test_wrong_struct_type_cannot_be_assigned(self):
        url = ue4ss.make_struct("/Script/Engine.URL")
        with self.assertRaises(ue4ss.UEError):
            self.actor.Destination = url

    def test_to_dict_equality_and_hash(self):
        a = ue4ss.make_struct("/Script/CoreUObject.Vector", X=1.0)
        self.assertEqual(a.to_dict(), {"X": 1.0, "Y": 0.0, "Z": 0.0})
        self.assertEqual(a, a)
        self.assertEqual(hash(a), hash(a))
        self.assertNotEqual(a, ue4ss.make_struct("/Script/CoreUObject.Vector", X=1.0))  # identity, not contents
        self.assertEqual(a.to_dict(), a.copy().to_dict())  # contents compare through to_dict

    def test_make_struct_errors(self):
        with self.assertRaises(ue4ss.UEError):
            ue4ss.make_struct("/Script/Nope.Nothing")
        with self.assertRaises(ue4ss.UEError):
            ue4ss.make_struct("/Script/Engine.Actor")  # a class, not a struct type
        with self.assertRaises(ue4ss.UEError):
            ue4ss.make_struct(self.actor)  # an object that is not a struct type
        with self.assertRaises(TypeError):
            ue4ss.make_struct(5)
        with self.assertRaises(TypeError):
            ue4ss.make_struct(None)

    def test_owned_struct_dies_with_its_type_not_with_objects(self):
        value = ue4ss.make_struct("/Script/CoreUObject.Vector")
        ue4ss._mock_destroy_object(self.vector_type)
        self.assertFalse(value.is_valid())
        with self.assertRaises(ue4ss.InvalidObjectError):
            value.X
        self.assertIn("destroyed", repr(value))

    def test_game_thread_requirement(self):
        view = self.actor.Location
        owned = ue4ss.make_struct("/Script/CoreUObject.Vector")
        ue4ss._mock_enforce_game_thread = True
        with self.assertRaises(ue4ss.UEError):
            ue4ss.make_struct("/Script/CoreUObject.Vector")
        with self.assertRaises(ue4ss.UEError):
            owned.copy()
        with self.assertRaises(ue4ss.UEError):
            view.copy()
        self.assertTrue(owned.is_valid())


class ParamBlockTest(unittest.TestCase):
    VECTOR = {"X": ("float", 0.0), "Y": ("float", 0.0), "Z": ("float", 0.0)}

    def setUp(self):
        ue4ss._mock_reset_objects()
        self.actor_class = ue4ss._mock_define_class("/Script/Engine.Actor", "/Script/CoreUObject.Object")
        ue4ss._mock_define_struct_type("/Script/CoreUObject.Vector", types=self.VECTOR)
        ue4ss._mock_define_struct_type("/Script/Engine.HitResult", types={"bBlockingHit": ("bool", False), "Time": ("float", 1.0)})
        vector, hit = "struct:/Script/CoreUObject.Vector", "struct:/Script/Engine.HitResult"
        # K2_SetActorLocation(NewLocation, bSweep, out SweepHitResult, bTeleport) -> bool
        self.set_location = ue4ss._mock_define_function("/Script/Engine.Actor", "K2_SetActorLocation", flags=0x04020400, params=[
            {"name": "NewLocation", "type": "StructProperty", "is_out": False, "is_return": False, "mock_type": vector},
            ("bSweep", "BoolProperty", False, False),
            {"name": "SweepHitResult", "type": "StructProperty", "is_out": True, "is_return": False, "mock_type": hit},
            ("bTeleport", "BoolProperty", False, False),
            ("ReturnValue", "BoolProperty", True, True),
        ])
        self.add = ue4ss._mock_define_function("/Script/Engine.Actor", "Add", params=[
            ("A", "IntProperty", False, False), ("B", "IntProperty", False, False), ("ReturnValue", "IntProperty", True, True)])
        self.const_ref = ue4ss._mock_define_function("/Script/Engine.Actor", "UsesConstRef", params=[
            {"name": "Offset", "type": "StructProperty", "is_out": True, "is_const": True, "is_reference": True, "is_return": False, "mock_type": vector},
            {"name": "Count", "type": "IntProperty", "is_out": True, "is_return": False}])
        self.nothing = ue4ss._mock_define_function("/Script/Engine.Actor", "DoNothing")

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_positional_and_keyword_arguments(self):
        block = self.add.make_param_block(2, 3)
        self.assertTrue(block.is_owned())
        self.assertEqual((block.A, block.B, block.ReturnValue), (2, 3, 0))
        mixed = self.add.make_param_block(1, B=9)
        self.assertEqual((mixed.A, mixed.B), (1, 9))
        by_name = self.add.make_param_block(b=5, A=4)  # names are case-insensitive and may come in any order
        self.assertEqual((by_name.A, by_name.B), (4, 5))
        self.assertEqual(block.get_struct_name(), "Add")  # named after the function

    def test_struct_inputs_outputs_and_the_return_value(self):
        block = self.set_location.make_param_block({"X": 1.0, "Y": 2.0, "Z": 3.0}, True, bTeleport=False)
        self.assertEqual(block.NewLocation.to_dict(), {"X": 1.0, "Y": 2.0, "Z": 3.0})
        self.assertIs(block.bSweep, True)
        self.assertIs(block.bTeleport, False)
        self.assertEqual(block.SweepHitResult.Time, 1.0)  # outputs start default-initialised
        self.assertIs(block.SweepHitResult.bBlockingHit, False)
        self.assertIs(block.ReturnValue, False)
        owned_vector = ue4ss.make_struct("/Script/CoreUObject.Vector", X=7.0)
        again = self.set_location.make_param_block(owned_vector, False, False)
        self.assertEqual(again.NewLocation.X, 7.0)
        owned_vector.X = 8.0
        self.assertEqual(again.NewLocation.X, 7.0)  # the block holds a copy

    def test_argument_errors_match_the_core_rules(self):
        cases = [
            (lambda: self.add.make_param_block(1, 2, 3), "Add() takes 2 positional arguments but 3 were given"),
            (lambda: self.add.make_param_block(1), "Add() missing 1 required argument: 'B'"),
            (lambda: self.add.make_param_block(), "Add() missing 2 required arguments: 'A', 'B'"),
            (lambda: self.add.make_param_block(1, A=2), "Add() got multiple values for argument 'A'"),
            (lambda: self.add.make_param_block(1, 2, C=3), "Add() got an unexpected keyword argument 'C'"),
            (lambda: self.add.make_param_block(1, 2, ReturnValue=3), "Add() 'ReturnValue' is an output of the function and cannot be passed"),
            (lambda: self.set_location.make_param_block({}, True, SweepHitResult=None), "K2_SetActorLocation() 'SweepHitResult' is an output of the function and cannot be passed"),
            (lambda: self.nothing.make_param_block(1), "DoNothing() takes 0 positional arguments but 1 was given"),
        ]
        for call, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(TypeError) as caught:
                    call()
                self.assertEqual(str(caught.exception), message)

    def test_values_are_converted_strictly(self):
        with self.assertRaises(OverflowError):
            self.add.make_param_block(2**31, 1)
        with self.assertRaises(TypeError):
            self.add.make_param_block(1.5, 1)
        with self.assertRaises(TypeError):
            self.set_location.make_param_block({"X": "1"}, True, False)
        with self.assertRaises(ValueError):
            self.set_location.make_param_block({}, 2, False)
        with self.assertRaises(AttributeError):
            self.set_location.make_param_block({"Nope": 1}, True, False)

    def test_const_reference_parameters_are_inputs(self):
        # Offset is a const reference (input); Count is a plain out parameter (output).
        block = self.const_ref.make_param_block({"X": 4.0})
        self.assertEqual(block.Offset.X, 4.0)
        with self.assertRaises(TypeError):
            self.const_ref.make_param_block({"X": 4.0}, 1)  # Count is an output

    def test_function_without_parameters(self):
        block = self.nothing.make_param_block()
        self.assertEqual(block.get_property_names(), [])
        self.assertEqual(block.to_dict(), {})

    def test_block_dies_with_the_function_and_needs_the_game_thread(self):
        block = self.add.make_param_block(1, 2)
        ue4ss._mock_enforce_game_thread = True
        with self.assertRaises(ue4ss.UEError):
            self.add.make_param_block(1, 2)
        ue4ss._mock_enforce_game_thread = False
        ue4ss._mock_destroy_object(self.add)
        self.assertFalse(block.is_valid())
        with self.assertRaises(ue4ss.InvalidObjectError):
            block.A
        with self.assertRaises(ue4ss.InvalidObjectError):
            self.add.make_param_block(1, 2)


class CallTest(unittest.TestCase):
    def setUp(self):
        ue4ss._mock_reset_objects()
        self.library = ue4ss._mock_define_class("/Script/Engine.KismetMathLibrary", "/Script/CoreUObject.Object")
        self.actor_class = ue4ss._mock_define_class("/Script/Engine.Actor", "/Script/CoreUObject.Object")
        self.pawn_class = ue4ss._mock_define_class("/Script/Engine.Pawn", "/Script/Engine.Actor")
        self.other_class = ue4ss._mock_define_class("/Script/Engine.Controller", "/Script/CoreUObject.Object")
        ue4ss._mock_define_struct_type("/Script/CoreUObject.Vector", types={"X": ("float", 0.0), "Y": ("float", 0.0), "Z": ("float", 0.0)})
        vector = "struct:/Script/CoreUObject.Vector"

        def add(block):
            block.ReturnValue = block.A + block.B
        self.add = ue4ss._mock_define_function("/Script/Engine.KismetMathLibrary", "Add_IntInt", flags=0x2000, impl=add, params=[
            ("A", "IntProperty", False, False), ("B", "IntProperty", False, False), ("ReturnValue", "IntProperty", True, True)])

        def break_vector(block):
            block.X, block.Y, block.Z = block.InVec.X, block.InVec.Y, block.InVec.Z
        self.break_vector = ue4ss._mock_define_function("/Script/Engine.KismetMathLibrary", "BreakVector", flags=0x2000, impl=break_vector, params=[
            {"name": "InVec", "type": "StructProperty", "is_out": True, "is_const": True, "is_reference": True, "is_return": False, "mock_type": vector},
            ("X", "FloatProperty", True, False), ("Y", "FloatProperty", True, False), ("Z", "FloatProperty", True, False)])

        self.calls = []

        def set_hidden(block):
            self.calls.append(block.bNewHidden)
        self.set_hidden = ue4ss._mock_define_function("/Script/Engine.Actor", "SetActorHiddenInGame", flags=0x4020400, impl=set_hidden,
                                                       params=[("bNewHidden", "BoolProperty", False, False)])

        def locate(block):
            block.ReturnValue.X, block.ReturnValue.Y, block.ReturnValue.Z = 1.0, 2.0, 3.0
        self.locate = ue4ss._mock_define_function("/Script/Engine.Actor", "K2_GetActorLocation", flags=0x4020401, impl=locate, params=[
            {"name": "ReturnValue", "type": "StructProperty", "is_out": True, "is_return": True, "mock_type": vector}])

        def split(block):
            block.ReturnValue, block.Remainder = block.Value // 2, block.Value % 2
        self.split = ue4ss._mock_define_function("/Script/Engine.Actor", "Split", impl=split, params=[
            ("Value", "IntProperty", False, False), ("Remainder", "IntProperty", True, False), ("ReturnValue", "IntProperty", True, True)])

        self.pawn = ue4ss._mock_create_object("/Game/Arena.Arena:Pawn_0", "/Script/Engine.Pawn")
        self.controller = ue4ss._mock_create_object("/Game/Arena.Arena:Controller_0", "/Script/Engine.Controller")

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_static_function_through_the_class_object(self):
        self.assertEqual(self.library.Add_IntInt(2, 3), 5)
        self.assertEqual(self.library.Add_IntInt(B=1, a=9), 10)
        self.assertIsInstance(self.library.Add_IntInt, ue4ss.BoundFunction)

    def test_instance_function_on_an_instance_and_inherited_from_the_parent(self):
        self.assertIsNone(self.pawn.SetActorHiddenInGame(True))  # no return value, no outputs: None
        self.assertEqual(self.calls, [True])
        self.assertEqual(self.pawn.call_function("SetActorHiddenInGame", False), None)
        self.assertEqual(self.calls, [True, False])

    def test_struct_return_value_and_multiple_results(self):
        location = self.pawn.K2_GetActorLocation()
        self.assertEqual(location.to_dict(), {"X": 1.0, "Y": 2.0, "Z": 3.0})
        self.assertTrue(location.is_owned())
        self.assertEqual(self.pawn.Split(7), (3, 1))  # return value first, then the outputs
        self.assertEqual(self.library.BreakVector({"X": 4.0, "Y": 5.0, "Z": 6.0}), (4.0, 5.0, 6.0))  # outputs only

    def test_call_function_by_name_and_by_ufunction(self):
        self.assertEqual(self.library.call_function("Add_IntInt", 1, 1), 2)
        self.assertEqual(self.library.call_function(self.add, 4, 5), 9)
        with self.assertRaises(AttributeError):
            self.library.call_function("Nope")
        with self.assertRaises(TypeError):
            self.library.call_function(5)
        with self.assertRaises(TypeError):
            self.library.call_function(self.pawn)  # an object, not a function

    def test_instance_function_on_a_class_is_rejected(self):
        with self.assertRaises(ue4ss.UEError) as caught:
            self.actor_class.SetActorHiddenInGame(True)
        self.assertEqual(str(caught.exception), "'SetActorHiddenInGame' is an instance function; call it on an object of the class, not on the class itself")
        self.assertEqual(self.calls, [])

    def test_wrong_class_is_rejected_but_static_functions_run_anywhere(self):
        with self.assertRaises(ue4ss.UEError) as caught:
            self.controller.call_function(self.set_hidden, True)
        self.assertEqual(str(caught.exception), "'SetActorHiddenInGame' belongs to class Actor, which this object is not an instance of")
        self.assertEqual(self.controller.call_function(self.add, 1, 2), 3)  # static: any object will do

    def test_argument_errors_come_before_anything_runs(self):
        with self.assertRaises(TypeError) as caught:
            self.pawn.SetActorHiddenInGame()
        self.assertEqual(str(caught.exception), "SetActorHiddenInGame() missing 1 required argument: 'bNewHidden'")
        with self.assertRaises(ValueError):
            self.pawn.SetActorHiddenInGame(2)
        with self.assertRaises(TypeError):
            self.library.Add_IntInt(1, 2, ReturnValue=5)
        self.assertEqual(self.calls, [])

    def test_missing_names(self):
        with self.assertRaises(AttributeError) as caught:
            self.pawn.NoSuchThing
        self.assertEqual(str(caught.exception), "the object has no property or function named 'NoSuchThing'")
        self.assertFalse(hasattr(self.pawn, "NoSuchThing"))
        self.assertTrue(hasattr(self.pawn, "SetActorHiddenInGame"))
        self.assertFalse(hasattr(self.pawn, "Add_IntInt"))  # a library function is not a method of a pawn

    def test_properties_win_over_functions_and_methods_over_both(self):
        ue4ss._mock_define_property(self.pawn, "Split", "int32", 5)
        self.assertEqual(self.pawn.Split, 5)  # the property
        self.assertEqual(self.pawn.call_function("Split", 7), (3, 1))  # the function, reachable by name
        self.assertTrue(callable(self.pawn.is_valid))  # real methods are found first

    def test_bound_function_repr_validity_and_construction(self):
        bound = self.pawn.SetActorHiddenInGame
        self.assertEqual(repr(bound), "<BoundFunction 'SetActorHiddenInGame'>")
        self.assertTrue(bound.is_valid())
        ue4ss._mock_destroy_object(self.pawn)
        self.assertFalse(bound.is_valid())
        self.assertIn("destroyed", repr(bound))
        with self.assertRaises(ue4ss.InvalidObjectError):
            bound(True)
        with self.assertRaises(TypeError):
            ue4ss.BoundFunction()

    def test_game_thread_requirement(self):
        bound = self.pawn.SetActorHiddenInGame
        ue4ss._mock_enforce_game_thread = True
        with self.assertRaises(ue4ss.UEError):
            bound(True)
        with self.assertRaises(ue4ss.UEError):
            self.pawn.Split
        self.assertTrue(bound.is_valid())  # never raises
        self.assertIn("not readable here", repr(bound))


class NewObjectTest(unittest.TestCase):
    ACTOR = "/Script/Engine.Actor"

    def setUp(self):
        ue4ss._mock_reset()
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_class(self.ACTOR, "/Script/CoreUObject.Object")
        ue4ss._mock_define_class("/Script/Engine.Pawn", self.ACTOR)
        ue4ss._mock_define_class("/Script/Engine.Light", "/Script/CoreUObject.Object")
        self.seen = []

    def tearDown(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_reset()

    def test_callback_comes_later_for_instances_of_the_class_and_subclasses(self):
        ue4ss.notify_on_new_object(self.ACTOR, lambda obj: self.seen.append(obj.get_full_name()))
        ue4ss._mock_create_object("/Game/Map.Pawn_1", "/Script/Engine.Pawn")
        ue4ss._mock_create_object("/Game/Map.Actor_1", self.ACTOR)
        ue4ss._mock_create_object("/Game/Map.Light_1", "/Script/Engine.Light")
        self.assertEqual(self.seen, [])  # not during creation
        ue4ss._mock_run_pending()
        self.assertEqual(self.seen, ["Pawn /Game/Map.Pawn_1", "Actor /Game/Map.Actor_1"])

    def test_object_destroyed_before_delivery_is_not_delivered(self):
        ue4ss.notify_on_new_object(self.ACTOR, self.seen.append)
        obj = ue4ss._mock_create_object("/Game/Map.Actor_1", self.ACTOR)
        ue4ss._mock_destroy_object(obj)
        ue4ss._mock_run_pending()
        self.assertEqual(self.seen, [])

    def test_unregister_stops_delivery_even_for_queued_objects(self):
        subscription = ue4ss.notify_on_new_object(self.ACTOR, self.seen.append)
        ue4ss._mock_create_object("/Game/Map.Actor_1", self.ACTOR)
        ue4ss.unregister_notify_on_new_object(subscription)
        ue4ss._mock_run_pending()
        self.assertEqual(self.seen, [])
        with self.assertRaises(ValueError):
            ue4ss.unregister_notify_on_new_object(subscription)

    def test_two_subscriptions_each_get_the_object(self):
        ue4ss.notify_on_new_object(self.ACTOR, lambda obj: self.seen.append("a"))
        ue4ss.notify_on_new_object("/Script/Engine.Pawn", lambda obj: self.seen.append("b"))
        ue4ss._mock_create_object("/Game/Map.Pawn_1", "/Script/Engine.Pawn")
        ue4ss._mock_run_pending()
        self.assertEqual(self.seen, ["a", "b"])

    def test_argument_errors(self):
        with self.assertRaises(TypeError):
            ue4ss.notify_on_new_object(5, print)
        with self.assertRaises(TypeError):
            ue4ss.notify_on_new_object(self.ACTOR, 5)
        with self.assertRaises(ue4ss.UEError):
            ue4ss.notify_on_new_object("/Script/Engine.Nothing", print)
        with self.assertRaises(ue4ss.UEError):  # exists, but is not a class
            ue4ss.notify_on_new_object("/Script", print)


class PropertyTypesTest(unittest.TestCase):
    ACTOR, PAWN, VECTOR = "/Script/Engine.Actor", "/Script/Engine.Pawn", "/Script/CoreUObject.Vector"

    def setUp(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_struct_type(self.VECTOR, types={"X": ("float", 0.0), "Y": ("float", 0.0)})
        ue4ss._mock_define_class(self.ACTOR, "/Script/CoreUObject.Object", fields=["Health", "Owner"],
                                 field_types={"Health": "float", "Owner": "object:" + self.ACTOR})
        ue4ss._mock_define_class(self.PAWN, self.ACTOR, fields=["Targets", "Scores", "Mode", "Origin"],
                                 field_types={"Targets": "array:object:" + self.ACTOR, "Scores": "map:name=>int32", "Mode": "enum:/Script/Engine.EMode",
                                              "Origin": "struct:" + self.VECTOR})
        self.pawn = ue4ss.static_find_object(self.PAWN)

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_descriptors_cover_scalars_objects_containers_structs_and_enums(self):
        by_name = {p["name"]: p for p in self.pawn.get_properties()}
        self.assertEqual(list(by_name), ["Health", "Owner", "Targets", "Scores", "Mode", "Origin"])
        self.assertEqual(by_name["Health"]["type"], "FloatProperty")
        self.assertEqual((by_name["Owner"]["type"], by_name["Owner"]["class"]), ("ObjectProperty", self.ACTOR))
        self.assertEqual(by_name["Targets"]["type"], "ArrayProperty")
        self.assertEqual((by_name["Targets"]["inner"][0]["type"], by_name["Targets"]["inner"][0]["class"]), ("ObjectProperty", self.ACTOR))
        self.assertEqual([i["type"] for i in by_name["Scores"]["inner"]], ["NameProperty", "IntProperty"])
        self.assertEqual(by_name["Mode"]["enum"], "/Script/Engine.EMode")
        self.assertEqual(by_name["Origin"]["struct"], self.VECTOR)
        self.assertEqual(by_name["Health"]["array_dim"], 1)

    def test_inherited_false_lists_only_declared_properties(self):
        own = self.pawn.get_properties(inherited=False)
        self.assertEqual([p["name"] for p in own], ["Targets", "Scores", "Mode", "Origin"])
        self.assertTrue(all(p["declared_in"] == self.PAWN for p in own))
        self.assertEqual({p["declared_in"] for p in self.pawn.get_properties()}, {self.ACTOR, self.PAWN})

    def test_struct_fields_have_descriptors_too(self):
        fields = ue4ss.static_find_object(self.VECTOR).get_properties()
        self.assertEqual([(f["name"], f["type"]) for f in fields], [("X", "FloatProperty"), ("Y", "FloatProperty")])

    def test_parameters_carry_the_same_descriptor(self):
        func = ue4ss._mock_define_function(self.ACTOR, "SetOwner", params=[
            {"name": "NewOwner", "type": "ObjectProperty", "is_out": False, "is_return": False, "mock_type": "object:" + self.ACTOR},
            ("Count", "IntProperty", False, False)])
        params = func.get_params()
        self.assertEqual((params[0]["type"], params[0]["class"]), ("ObjectProperty", self.ACTOR))
        self.assertEqual((params[1]["type"], params[1]["class"], params[1]["inner"]), ("IntProperty", None, []))


class HookInstanceTest(unittest.TestCase):
    ACTOR, BP = "/Script/Engine.Actor", "/Game/Folder/BP_Thing.BP_Thing_C"

    def setUp(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_class(self.ACTOR, "/Script/CoreUObject.Object")
        ue4ss._mock_define_function(self.ACTOR, "Poke", impl=lambda b: None, params=[("A", "IntProperty", False, False)])
        self.first = ue4ss._mock_create_object("/Game/Map.Actor_1", self.ACTOR)
        self.second = ue4ss._mock_create_object("/Game/Map.Actor_2", self.ACTOR)

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_instance_filter_limits_the_callbacks_to_one_object(self):
        seen = []
        one = ue4ss.register_hook(self.ACTOR + ":Poke", pre=lambda c, p: seen.append(("one", c)), instance=self.second)
        everyone = ue4ss.register_hook(self.ACTOR + ":Poke", pre=lambda c, p: seen.append(("all", c)))
        self.first.Poke(1)
        self.second.Poke(2)
        self.assertEqual([s for s in seen if s[0] == "one"], [("one", self.second)])
        self.assertEqual(len([s for s in seen if s[0] == "all"]), 2)
        ue4ss.unregister_hook(one)
        ue4ss.unregister_hook(everyone)

    def test_instance_must_be_an_object(self):
        with self.assertRaises(TypeError):
            ue4ss.register_hook(self.ACTOR + ":Poke", pre=lambda c, p: None, instance="x")

    def test_a_blueprint_function_path_loads_its_class(self):
        loaded = []

        def load(block):
            loaded.append(block.AssetClass)
            if block.AssetClass == self.BP:               # any other path: the game has no such asset
                ue4ss._mock_define_class(self.BP, self.ACTOR)
                ue4ss._mock_define_function(self.BP, "Event", impl=lambda b: None)
                block.ReturnValue = ue4ss.static_find_object(self.BP)

        ue4ss._mock_define_class("/Script/Engine.KismetSystemLibrary", "/Script/CoreUObject.Object")
        ue4ss._mock_define_function("/Script/Engine.KismetSystemLibrary", "LoadClassAsset_Blocking", flags=0x2000, impl=load, params=[
            ("AssetClass", "StrProperty", False, False), {"name": "ReturnValue", "type": "ObjectProperty", "is_out": True, "is_return": True, "mock_type": "object:/Script/CoreUObject.Object"}])
        ids = ue4ss.register_hook(self.BP + ":Event", pre=lambda c, p: None)
        self.assertEqual(loaded, [self.BP])
        self.assertEqual(len(ids), 2)
        loaded.clear()
        with self.assertRaises(ue4ss.UEError):
            ue4ss.register_hook("/Game/Folder/Missing_C.Missing_C_C:Event", pre=lambda c, p: None)


class ReloadTest(unittest.TestCase):
    def setUp(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_reset()

    def tearDown(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_reset()

    def test_generation_counts_reloads_and_requests_coalesce(self):
        self.assertEqual(ue4ss.script_generation(), 1)
        ue4ss.reload()
        ue4ss.reload()
        ue4ss.reload()
        self.assertEqual(ue4ss.script_generation(), 1)   # nothing happens before the tick
        ue4ss._mock_run_pending()
        self.assertEqual(ue4ss.script_generation(), 2)
        ue4ss.reload()
        ue4ss._mock_run_pending()
        self.assertEqual(ue4ss.script_generation(), 3)

    def test_registrations_are_dropped_and_main_runs_again(self):
        ue4ss._mock_define_class("/Script/Engine.Actor", "/Script/CoreUObject.Object")
        ue4ss._mock_define_function("/Script/Engine.Actor", "Tick")
        calls = []
        ue4ss._in_game_thread = True
        ue4ss.register_hook("/Script/Engine.Actor:Tick", pre=lambda c, p: None)
        ue4ss.register_replacement("/Script/Engine.Actor:Tick", lambda c, p: None)
        ue4ss.notify_on_new_object("/Script/Engine.Actor", lambda o: None)
        ue4ss.register_key_down(ue4ss.Key.F5, lambda: None)
        ue4ss.execute_with_delay(10_000, lambda: calls.append("late task"))
        ue4ss._in_game_thread = False
        ue4ss._mock_main = lambda: calls.append("main")
        ue4ss.reload()
        ue4ss._mock_run_pending()
        self.assertEqual(calls, ["main"])                      # the old delayed task is gone, main.py ran
        self.assertEqual(ue4ss._hook_records, {})
        self.assertEqual(ue4ss._replacements, {})
        self.assertEqual(ue4ss._new_object_subscriptions, {})
        self.assertEqual(ue4ss._key_bindings, {})

    def test_main_may_register_again_after_a_reload(self):
        ue4ss._mock_main = lambda: ue4ss.register_key_down(ue4ss.Key.F5, ue4ss.reload)
        ue4ss._mock_main()
        self.assertEqual(ue4ss._selftest_fire_key(ue4ss.Key.F5), 1)
        ue4ss._mock_run_pending()
        self.assertEqual(ue4ss.script_generation(), 2)
        self.assertEqual(ue4ss._selftest_fire_key(ue4ss.Key.F5), 1)   # registered again by main


class KeyBindingTest(unittest.TestCase):
    def setUp(self):
        ue4ss._mock_reset_objects()

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_key_and_modifier_enums(self):
        self.assertEqual(ue4ss.Key.F1, 0x70)
        self.assertEqual(ue4ss.Key.A, 0x41)
        self.assertEqual(ue4ss.ModifierKey.CONTROL, 0x11)
        self.assertNotIn("SHIFT", ue4ss.Key.__members__)   # modifiers are not keys

    def test_register_fire_unregister(self):
        calls = []
        first = ue4ss.register_key_down(ue4ss.Key.F6, lambda: calls.append("a"))
        second = ue4ss.register_key_down(ue4ss.Key.F6, lambda: calls.append("b"))
        self.assertNotEqual(first, second)
        self.assertEqual(ue4ss._selftest_fire_key(ue4ss.Key.F6), 2)
        self.assertEqual(calls, ["a", "b"])   # registration order
        ue4ss.unregister_key_down(first)
        calls.clear()
        self.assertEqual(ue4ss._selftest_fire_key(ue4ss.Key.F6), 1)
        self.assertEqual(calls, ["b"])
        with self.assertRaises(ValueError):
            ue4ss.unregister_key_down(first)

    def test_modifiers_must_match_exactly(self):
        calls = []
        ue4ss.register_key_down(ue4ss.Key.F6, lambda: calls.append("plain"))
        ue4ss.register_key_down(ue4ss.Key.F6, lambda: calls.append("ctrl"), modifiers=(ue4ss.ModifierKey.CONTROL,))
        ue4ss.register_key_down(ue4ss.Key.F6, lambda: calls.append("ctrl+shift"), modifiers=[ue4ss.ModifierKey.SHIFT, ue4ss.ModifierKey.CONTROL])
        ue4ss._selftest_fire_key(ue4ss.Key.F6, (ue4ss.ModifierKey.CONTROL,))
        ue4ss._selftest_fire_key(ue4ss.Key.F6, (ue4ss.ModifierKey.CONTROL, ue4ss.ModifierKey.SHIFT, ue4ss.ModifierKey.CONTROL))
        ue4ss._selftest_fire_key(ue4ss.Key.F6, (ue4ss.ModifierKey.ALT,))
        ue4ss._selftest_fire_key(ue4ss.Key.F6)
        self.assertEqual(calls, ["ctrl", "ctrl+shift", "plain"])

    def test_a_callback_may_unregister_a_later_one(self):
        calls = []
        later = []
        ue4ss.register_key_down(ue4ss.Key.A, lambda: (calls.append("first"), ue4ss.unregister_key_down(later[0])))
        later.append(ue4ss.register_key_down(ue4ss.Key.A, lambda: calls.append("second")))
        ue4ss._selftest_fire_key(ue4ss.Key.A)
        self.assertEqual(calls, ["first"])

    def test_a_failing_callback_does_not_stop_the_others(self):
        calls = []
        ue4ss.register_key_down(ue4ss.Key.B, lambda: 1 / 0)
        ue4ss.register_key_down(ue4ss.Key.B, lambda: (_ for _ in ()).throw(SystemExit(3)))
        ue4ss.register_key_down(ue4ss.Key.B, lambda: calls.append("ran"))
        import contextlib, io
        with contextlib.redirect_stderr(io.StringIO()):
            ue4ss._selftest_fire_key(ue4ss.Key.B)
        self.assertEqual(calls, ["ran"])

    def test_bad_arguments(self):
        for bad_key in (0, 0xFF, 0x10, 0x11, 0x12, -1, 300):
            with self.assertRaises(ValueError):
                ue4ss.register_key_down(bad_key, lambda: None)
        with self.assertRaises(TypeError):
            ue4ss.register_key_down("F1", lambda: None)
        with self.assertRaises(TypeError):
            ue4ss.register_key_down(ue4ss.Key.A, 5)
        with self.assertRaises(ValueError):
            ue4ss.register_key_down(ue4ss.Key.A, lambda: None, modifiers=(ue4ss.Key.B,))
        with self.assertRaises(TypeError):
            ue4ss.unregister_key_down("x")
        self.assertEqual(ue4ss.register_key_down(0x41, lambda: None) > 0, True)   # a plain number works


class FixedArrayTest(unittest.TestCase):
    VECTOR = "/Script/CoreUObject.Vector"
    ACTOR = "/Script/Engine.Actor"

    def setUp(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_struct_type(self.VECTOR, types={"X": ("float", 0.0), "Y": ("float", 0.0)})
        ue4ss._mock_define_class(self.ACTOR, "/Script/CoreUObject.Object")
        self.actor = ue4ss._mock_create_object("/Game/Map.Actor_1", self.ACTOR)
        ue4ss._mock_define_property(self.actor, "FixedInts", "fixed:int32", [1, 2, 3, 4])
        ue4ss._mock_define_property(self.actor, "FixedNames", "fixed:str", ["a", "b"])
        ue4ss._mock_define_property(self.actor, "FixedPoints", "fixed:struct:" + self.VECTOR,
                                    [{"X": ("float", 1.0), "Y": ("float", 2.0)}, {"X": ("float", 3.0), "Y": ("float", 4.0)}])

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_reading(self):
        ints = self.actor.FixedInts
        self.assertIsInstance(ints, ue4ss.FixedArray)
        self.assertEqual(len(ints), 4)
        self.assertEqual((ints[0], ints[3], ints[-1]), (1, 4, 4))
        self.assertEqual(list(ints), [1, 2, 3, 4])
        self.assertEqual(ints[1:3], [2, 3])
        self.assertEqual(ints.to_list(), [1, 2, 3, 4])
        self.assertIn(3, ints)
        self.assertEqual(repr(ints), "<FixedArray 'FixedInts' of 4 int32>")
        self.assertEqual(self.actor.FixedNames.to_list(), ["a", "b"])

    def test_item_assignment_and_bounds(self):
        ints = self.actor.FixedInts
        ints[2] = 99
        ints[-1] = 7
        self.assertEqual(ints.to_list(), [1, 2, 99, 7])
        with self.assertRaises(IndexError):
            ints[4]
        with self.assertRaises(IndexError):
            ints[4] = 1
        with self.assertRaises(IndexError):
            ints[-5] = 1
        with self.assertRaises(TypeError):
            ints["x"]

    def test_strict_conversion_leaves_the_array_unchanged(self):
        ints = self.actor.FixedInts
        for bad in ("text", 2 ** 31, 1.5):
            with self.assertRaises((TypeError, OverflowError)):
                ints[0] = bad
        self.assertEqual(ints.to_list(), [1, 2, 3, 4])

    def test_cannot_change_length(self):
        ints = self.actor.FixedInts
        with self.assertRaises(TypeError):
            del ints[0]
        with self.assertRaises(TypeError):
            ints[0:2] = [1, 2]
        self.assertFalse(hasattr(ints, "append"))
        self.assertEqual(len(ints), 4)

    def test_whole_assignment_needs_the_exact_length(self):
        self.actor.FixedInts = [5, 6, 7, 8]
        self.assertEqual(self.actor.FixedInts.to_list(), [5, 6, 7, 8])
        self.actor.FixedInts = (9, 9, 9, 9)
        self.assertEqual(self.actor.FixedInts.to_list(), [9, 9, 9, 9])
        for bad in ([1, 2, 3], [1, 2, 3, 4, 5], []):
            with self.assertRaises(ValueError):
                self.actor.FixedInts = bad
        with self.assertRaises(TypeError):
            self.actor.FixedInts = 5
        with self.assertRaises(TypeError):
            self.actor.FixedInts = [1, 2, 3, "x"]   # nothing is written when an item is bad
        self.assertEqual(self.actor.FixedInts.to_list(), [9, 9, 9, 9])

    def test_struct_elements_are_views_into_the_owner(self):
        points = self.actor.FixedPoints
        first = points[0]
        first.X = 10.0
        self.assertEqual(points[0].X, 10.0)
        points[1] = ue4ss.make_struct(self.VECTOR, X=7.0, Y=8.0)
        self.assertEqual((points[1].X, points[1].Y), (7.0, 8.0))
        with self.assertRaises(TypeError):
            points[0] = {"X": 1.0}

    def test_to_dict_lists_fixed_arrays(self):
        data = self.actor.to_dict() if hasattr(self.actor, "to_dict") else None
        if data is not None:
            self.assertEqual(data["FixedInts"], [1, 2, 3, 4])

    def test_destroyed_owner(self):
        ints = self.actor.FixedInts
        ue4ss._mock_destroy_object(self.actor)
        self.assertFalse(ints.is_valid())
        with self.assertRaises(ue4ss.InvalidObjectError):
            len(ints)
        self.assertEqual(repr(ints), "<FixedArray (owner destroyed)>")

    def test_cannot_be_constructed(self):
        with self.assertRaises(TypeError):
            ue4ss.FixedArray()


class ArrayTest(unittest.TestCase):
    VECTOR = "/Script/CoreUObject.Vector"
    ACTOR = "/Script/Engine.Actor"

    def setUp(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_struct_type(self.VECTOR, types={"X": ("float", 0.0), "Y": ("float", 0.0)})
        ue4ss._mock_define_class(self.ACTOR, "/Script/CoreUObject.Object")
        self.actor = ue4ss._mock_create_object("/Game/Map.Actor_1", self.ACTOR)
        self.other = ue4ss._mock_create_object("/Game/Map.Actor_2", self.ACTOR)
        ue4ss._mock_define_property(self.actor, "Scores", "array:int32", [10, 20, 30])
        ue4ss._mock_define_property(self.actor, "Tags", "array:str", ["a", "b"])
        ue4ss._mock_define_property(self.actor, "Friends", "array:object:" + self.ACTOR, [self.other, None])
        ue4ss._mock_define_property(self.actor, "Points", "array:struct:" + self.VECTOR,
                                    [{"X": ("float", 1.0), "Y": ("float", 2.0)}, {"X": ("float", 3.0), "Y": ("float", 4.0)}])
        ue4ss._mock_define_property(self.actor, "Empty", "array:int32", [])

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_length_indexing_negative_indices_and_iteration(self):
        scores = self.actor.Scores
        self.assertIsInstance(scores, ue4ss.Array)
        self.assertEqual(len(scores), 3)
        self.assertEqual((scores[0], scores[2], scores[-1]), (10, 30, 30))
        self.assertEqual(list(scores), [10, 20, 30])
        self.assertEqual(scores[1:], [20, 30])
        self.assertEqual(scores[::-1], [30, 20, 10])
        self.assertEqual(scores.to_list(), [10, 20, 30])
        self.assertIn(20, scores)
        self.assertEqual(len(self.actor.Empty), 0)
        self.assertEqual(list(self.actor.Empty), [])

    def test_out_of_range_and_bad_index(self):
        with self.assertRaises(IndexError):
            self.actor.Scores[3]
        with self.assertRaises(IndexError):
            self.actor.Scores[-4]
        with self.assertRaises(IndexError):
            self.actor.Scores[3] = 1
        with self.assertRaises(TypeError):
            self.actor.Scores["x"]

    def test_assigning_elements_converts_strictly(self):
        scores = self.actor.Scores
        scores[1] = 99
        scores[-1] = 7
        self.assertEqual(self.actor.Scores.to_list(), [10, 99, 7])
        with self.assertRaises(TypeError):
            scores[0] = 1.5
        with self.assertRaises(OverflowError):
            scores[0] = 2 ** 40
        self.assertEqual(scores[0], 10)

    def test_the_view_always_reads_current_data(self):
        scores = self.actor.Scores
        self.actor.get_property_value("Scores")[0] = 5
        self.assertEqual(scores[0], 5)

    def test_list_like_resizing(self):
        scores = self.actor.Scores
        scores.append(40)
        scores.extend([50, 60])
        scores.insert(0, 5)
        scores.insert(-1, 55)
        self.assertEqual(scores.to_list(), [5, 10, 20, 30, 40, 50, 55, 60])
        self.assertEqual(scores.pop(), 60)
        self.assertEqual(scores.pop(0), 5)
        del scores[0]
        del scores[-1]
        self.assertEqual(scores.to_list(), [20, 30, 40, 50])
        scores[1:3] = [1, 2, 3]
        self.assertEqual(scores.to_list(), [20, 1, 2, 3, 50])
        del scores[1:4]
        self.assertEqual(scores.to_list(), [20, 50])
        scores[1:1] = [7]
        scores.extend(scores)
        self.assertEqual(scores.to_list(), [20, 7, 50, 20, 7, 50])
        scores.clear()
        self.assertEqual(len(scores), 0)
        with self.assertRaises(IndexError):
            scores.pop()
        self.assertEqual(len(self.actor.Scores), 0)  # a fresh view sees the same data

    def test_whole_array_assignment(self):
        self.actor.Scores = (1, 2)
        self.assertEqual(self.actor.Scores.to_list(), [1, 2])
        self.actor.Scores = self.actor.Scores  # itself
        self.assertEqual(self.actor.Scores.to_list(), [1, 2])
        self.actor.Scores = iter(range(4))
        self.assertEqual(self.actor.Scores.to_list(), [0, 1, 2, 3])
        self.actor.Scores = []
        self.assertEqual(len(self.actor.Scores), 0)

    def test_a_bad_item_changes_nothing(self):
        scores = self.actor.Scores
        for action in (lambda: scores.extend([1, 2.5]), lambda: scores.append("x"), lambda: scores.insert(0, 2 ** 40),
                       lambda: setattr(self.actor, "Scores", [1, None]), lambda: scores.__setitem__(slice(0, 2), [1, "x"])):
            with self.assertRaises((TypeError, OverflowError)):
                action()
            self.assertEqual(scores.to_list(), [10, 20, 30])
        with self.assertRaises(TypeError):
            self.actor.Scores = 5  # not iterable
        with self.assertRaises(ValueError):
            scores[::2] = [1, 2]
        with self.assertRaises(ValueError):
            del scores[::2]
        self.assertEqual(scores.to_list(), [10, 20, 30])

    def test_struct_and_object_elements_resize(self):
        points = self.actor.Points
        copy = points[0]
        copy.X = 9.0
        points.append(copy)
        self.assertEqual([p.X for p in points], [1.0, 3.0, 9.0])
        popped = points.pop(1)
        self.assertTrue(popped.is_owned())
        self.assertEqual(popped.X, 3.0)
        with self.assertRaises(TypeError):
            points.append({"X": 1.0})
        self.assertEqual(len(points), 2)
        friends = self.actor.Friends
        friends.append(self.actor)
        friends.insert(0, None)
        self.assertEqual(friends.to_list(), [None, self.other, None, self.actor])
        with self.assertRaises(TypeError):
            friends.append("nope")

    def test_dead_owner_blocks_resizing(self):
        scores = self.actor.Scores
        ue4ss._mock_destroy_object(self.actor)
        for action in (lambda: scores.append(1), lambda: scores.clear(), lambda: scores.pop(), lambda: scores.__delitem__(0)):
            with self.assertRaises(ue4ss.InvalidObjectError):
                action()

    def test_strings_and_objects(self):
        self.assertEqual(self.actor.Tags.to_list(), ["a", "b"])
        self.actor.Tags[0] = "z"
        self.assertEqual(self.actor.Tags[0], "z")
        with self.assertRaises(TypeError):
            self.actor.Tags[0] = 5
        friends = self.actor.Friends
        self.assertEqual(friends[0], self.other)
        self.assertIsNone(friends[1])
        friends[1] = self.actor
        friends[0] = None
        self.assertEqual(friends.to_list(), [None, self.actor])
        with self.assertRaises(TypeError):
            friends[0] = "nope"

    def test_struct_elements_are_owned_copies(self):
        points = self.actor.Points
        first = points[0]
        self.assertTrue(first.is_owned())
        self.assertEqual((first.X, first.Y), (1.0, 2.0))
        first.X = 100.0  # changes the copy only
        self.assertEqual(points[0].X, 1.0)
        points[0] = first
        self.assertEqual(points[0].X, 100.0)
        with self.assertRaises(TypeError):
            points[1] = {"X": 1}
        other_type = ue4ss.make_struct(self.VECTOR)
        points[1] = other_type
        self.assertEqual((points[1].X, points[1].Y), (0.0, 0.0))

    def test_dead_owner_invalidates_the_view(self):
        scores = self.actor.Scores
        ue4ss._mock_destroy_object(self.actor)
        self.assertFalse(scores.is_valid())
        with self.assertRaises(ue4ss.InvalidObjectError):
            len(scores)
        with self.assertRaises(ue4ss.InvalidObjectError):
            scores[0]
        self.assertIn("destroyed", repr(scores))

    def test_arrays_in_structs_and_to_dict(self):
        ue4ss._mock_define_property(self.actor, "Box", "struct:" + self.VECTOR, {"X": ("float", 1.0)})
        self.assertEqual(self.actor.Box.to_dict(), {"X": 1.0})
        ue4ss._mock_define_property(self.actor, "Holder", "struct:" + self.VECTOR, {"X": ("float", 1.0), "Items": ("array:int32", [4, 5])})
        self.assertEqual(self.actor.Holder.to_dict(), {"X": 1.0, "Items": [4, 5]})
        self.assertEqual(repr(self.actor.Scores), "<Array 'Scores' of 3 int32>")

    def test_thread_rule(self):
        scores = self.actor.Scores
        ue4ss._mock_enforce_game_thread = True
        try:
            with self.assertRaises(ue4ss.UEError):
                len(scores)
            self.assertIn("not readable", repr(scores))
        finally:
            ue4ss._mock_enforce_game_thread = False


class MapSetTest(unittest.TestCase):
    ACTOR = "/Script/Engine.Actor"
    VECTOR = "/Script/CoreUObject.Vector"

    def setUp(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_struct_type(self.VECTOR, types={"X": ("float", 0.0), "Y": ("float", 0.0)})
        ue4ss._mock_define_class(self.ACTOR, "/Script/CoreUObject.Object")
        self.actor = ue4ss._mock_create_object("/Game/Map.Actor_1", self.ACTOR)
        self.other = ue4ss._mock_create_object("/Game/Map.Actor_2", self.ACTOR)
        d = ue4ss._mock_define_property
        d(self.actor, "Ammo", "map:name=>int32", {"Arrow": 10, "Bolt": 5})
        d(self.actor, "Owners", "map:int32=>object:" + self.ACTOR, {1: self.other, 2: None})
        d(self.actor, "Spots", "map:str=>struct:" + self.VECTOR, {"home": {"X": ("float", 1.0), "Y": ("float", 2.0)}})
        d(self.actor, "Ids", "set:int32", [3, 1, 2])
        d(self.actor, "Labels", "set:name", ["a", "b"])
        d(self.actor, "NoIds", "set:int32", [])

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_map_reads(self):
        ammo = self.actor.Ammo
        self.assertIsInstance(ammo, ue4ss.Map)
        self.assertEqual(len(ammo), 2)
        self.assertEqual(ammo["Arrow"], 10)
        self.assertEqual(ammo[ue4ss.FName("bolt")], 5)  # names compare case-insensitively
        self.assertIn("Arrow", ammo)
        self.assertNotIn("Nope", ammo)
        self.assertEqual(ammo.get("Nope", 0), 0)
        self.assertIsNone(ammo.get("Nope"))
        self.assertEqual([str(k) for k in ammo], ["Arrow", "Bolt"])
        self.assertEqual(ammo.values(), [10, 5])
        self.assertEqual([(str(k), v) for k, v in ammo.items()], [("Arrow", 10), ("Bolt", 5)])
        with self.assertRaises(KeyError):
            ammo["Nope"]

    def test_map_writes(self):
        ammo = self.actor.Ammo
        ammo["Arrow"] = 11
        ammo["Stone"] = 1
        self.assertEqual((ammo["Arrow"], ammo["Stone"], len(ammo)), (11, 1, 3))
        del ammo["Bolt"]
        with self.assertRaises(KeyError):
            del ammo["Bolt"]
        self.assertEqual(ammo.pop("Stone"), 1)
        self.assertEqual(ammo.pop("Stone", -1), -1)
        with self.assertRaises(KeyError):
            ammo.pop("Stone")
        ammo.update({"X": 1, "Y": 2})
        ammo.update([("Z", 3)])
        self.assertEqual(len(ammo), 4)
        ammo.clear()
        self.assertEqual(len(ammo), 0)
        self.actor.Ammo = {"New": 7}
        self.assertEqual(self.actor.Ammo.get("New"), 7)
        self.assertEqual(len(self.actor.Ammo), 1)

    def test_a_bad_item_changes_nothing(self):
        ammo = self.actor.Ammo
        for action in (lambda: ammo.update({"Q": 1, "R": "x"}), lambda: ammo.__setitem__("Q", 2.5), lambda: ammo.__setitem__(5, 1),
                       lambda: setattr(self.actor, "Ammo", {"Q": 1, "R": None}), lambda: ammo.update([("A", 1, 2)])):
            with self.assertRaises((TypeError, ValueError, OverflowError)):
                action()
            self.assertEqual(len(ammo), 2)
        with self.assertRaises(TypeError):
            self.actor.Ammo = 5

    def test_object_and_struct_values(self):
        owners = self.actor.Owners
        self.assertEqual(owners[1], self.other)
        self.assertIsNone(owners[2])
        owners[3] = self.actor
        with self.assertRaises(TypeError):
            owners[4] = "nope"
        spots = self.actor.Spots
        home = spots["home"]
        self.assertTrue(home.is_owned())
        self.assertEqual((home.X, home.Y), (1.0, 2.0))
        home.X = 50.0  # a copy
        self.assertEqual(spots["home"].X, 1.0)
        spots["home"] = home
        self.assertEqual(spots["home"].X, 50.0)
        spots["away"] = ue4ss.make_struct(self.VECTOR)
        self.assertEqual(sorted(spots.keys()), ["away", "home"])
        with self.assertRaises(TypeError):
            spots["x"] = {"X": 1}
        self.assertEqual(spots.to_dict()["home"].X, 50.0)

    def test_set_reads_and_writes(self):
        ids = self.actor.Ids
        self.assertIsInstance(ids, ue4ss.Set)
        self.assertEqual((len(ids), 3 in ids, 9 in ids), (3, True, False))
        self.assertEqual(sorted(ids), [1, 2, 3])
        self.assertEqual(ids.to_set(), {1, 2, 3})
        ids.add(9)
        ids.add(9)
        self.assertEqual(len(ids), 4)
        ids.discard(9)
        ids.discard(9)
        ids.remove(1)
        with self.assertRaises(KeyError):
            ids.remove(1)
        ids.update([5, 6, 5])
        self.assertEqual(sorted(ids.to_list()), [2, 3, 5, 6])
        with self.assertRaises(TypeError):
            ids.update([7, "x"])
        self.assertEqual(sorted(ids), [2, 3, 5, 6])
        self.actor.Ids = {8, 9}
        self.assertEqual(sorted(self.actor.Ids), [8, 9])
        ids.clear()
        self.assertEqual(len(ids), 0)
        self.assertEqual(list(self.actor.NoIds), [])

    def test_name_sets(self):
        labels = self.actor.Labels
        self.assertIn("A", labels)  # case-insensitive
        labels.add("c")
        labels.add("C")
        self.assertEqual(len(labels), 3)

    def test_struct_keys_are_listed_but_not_looked_up(self):
        ue4ss._mock_define_property(self.actor, "ByPoint", "map:struct:" + self.VECTOR + "=>int32", {})
        with self.assertRaises(ue4ss.UEError):
            self.actor.ByPoint[ue4ss.make_struct(self.VECTOR)] = 1

    def test_dead_owner_and_thread_rules(self):
        ammo, ids = self.actor.Ammo, self.actor.Ids
        ue4ss._mock_enforce_game_thread = True
        try:
            with self.assertRaises(ue4ss.UEError):
                len(ammo)
            with self.assertRaises(ue4ss.UEError):
                len(ids)
            self.assertIn("not readable", repr(ammo))
        finally:
            ue4ss._mock_enforce_game_thread = False
        ue4ss._mock_destroy_object(self.actor)
        self.assertFalse(ammo.is_valid())
        for action in (lambda: len(ammo), lambda: ammo["Arrow"], lambda: ids.add(1), lambda: 1 in ids):
            with self.assertRaises(ue4ss.InvalidObjectError):
                action()
        self.assertIn("destroyed", repr(ids))

    def test_to_dict_of_a_struct_with_containers(self):
        ue4ss._mock_define_property(self.actor, "Holder", "struct:" + self.VECTOR,
                                    {"X": ("float", 1.0), "Counts": ("map:str=>int32", {"a": 1}), "Seen": ("set:int32", [4])})
        self.assertEqual(self.actor.Holder.to_dict(), {"X": 1.0, "Counts": {"a": 1}, "Seen": [4]})
        self.assertEqual(repr(self.actor.Ids), "<Set 'Ids' of 3 int32>")
        self.assertEqual(repr(self.actor.Ammo), "<Map 'Ammo' of 2 name -> int32>")


class ReferenceKindsTest(unittest.TestCase):
    ACTOR = "/Script/Engine.Actor"

    def setUp(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_class(self.ACTOR, "/Script/CoreUObject.Object")
        ue4ss._mock_define_class("/Script/Engine.Pawn", self.ACTOR)
        ue4ss._mock_define_class("/Script/Engine.Light", "/Script/CoreUObject.Object")
        self.actor = ue4ss._mock_create_object("/Game/Map.Actor_1", self.ACTOR)
        self.pawn = ue4ss._mock_create_object("/Game/Map.Pawn_1", "/Script/Engine.Pawn")
        self.light = ue4ss._mock_create_object("/Game/Map.Light_1", "/Script/Engine.Light")
        d = ue4ss._mock_define_property
        d(self.actor, "Icon", "soft", "/Game/UI/Icon.Icon")
        d(self.actor, "Nothing", "soft")
        d(self.actor, "Target", "weak:" + self.ACTOR, self.pawn)
        d(self.actor, "Empty", "weak:" + self.ACTOR)
        d(self.actor, "Lazy", "lazy:" + self.ACTOR, self.pawn)
        d(self.actor, "LazyEmpty", "lazy:" + self.ACTOR)
        d(self.actor, "Iface", "interface:" + self.ACTOR, self.pawn)
        d(self.actor, "OnHit", "delegate", [(self.pawn, "Handle")])
        d(self.actor, "OnNone", "delegate")
        d(self.actor, "OnMany", "multicast", [(self.pawn, "A"), (self.light, "B")])
        d(self.actor, "Paths", "array:soft", ["/A.A", "/B.B"])
        d(self.actor, "Weights", "map:soft=>int32", {"/A.A": 1})

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_text_properties_are_strings(self):
        ue4ss._mock_define_property(self.actor, "Label", "text", "Hello")
        ue4ss._mock_define_property(self.actor, "Blank", "text")
        self.assertEqual((self.actor.Label, self.actor.Blank), ("Hello", ""))
        self.actor.Label = "Bye"
        self.assertEqual(self.actor.Label, "Bye")
        with self.assertRaises(TypeError):
            self.actor.Label = 5
        ue4ss._mock_define_property(self.actor, "Lines", "array:text", ["a", "b"])
        self.actor.Lines.append("c")
        self.assertEqual(self.actor.Lines.to_list(), ["a", "b", "c"])

    def test_soft_references_are_path_strings(self):
        self.assertEqual(self.actor.Icon, "/Game/UI/Icon.Icon")
        self.assertEqual(self.actor.Nothing, "")
        self.actor.Icon = "/Game/UI/Other.Other"
        self.assertEqual(self.actor.Icon, "/Game/UI/Other.Other")
        self.actor.Icon = None
        self.assertEqual(self.actor.Icon, "")
        with self.assertRaises(TypeError):
            self.actor.Icon = 5
        with self.assertRaises(TypeError):
            self.actor.Icon = self.pawn

    def test_weak_references_resolve_and_go_null(self):
        self.assertEqual(self.actor.Target, self.pawn)
        self.assertIsNone(self.actor.Empty)
        self.actor.Empty = self.pawn
        self.assertEqual(self.actor.Empty, self.pawn)
        with self.assertRaises(ue4ss.UEError):
            self.actor.Empty = self.light  # wrong class
        self.actor.Target = None
        self.assertIsNone(self.actor.Target)
        self.actor.Target = self.pawn
        ue4ss._mock_destroy_object(self.pawn)
        self.assertIsNone(self.actor.Target)  # the object is gone

    def test_lazy_references_behave_like_weak_ones(self):
        self.assertEqual(self.actor.Lazy, self.pawn)
        self.assertIsNone(self.actor.LazyEmpty)
        self.actor.LazyEmpty = self.pawn
        self.assertEqual(self.actor.LazyEmpty, self.pawn)
        with self.assertRaises(ue4ss.UEError):
            self.actor.LazyEmpty = self.light  # wrong class
        self.actor.Lazy = None
        self.assertIsNone(self.actor.Lazy)
        self.actor.Lazy = self.pawn
        ue4ss._mock_destroy_object(self.pawn)
        self.assertIsNone(self.actor.Lazy)

    def test_interfaces_and_delegates_are_read_only(self):
        self.assertEqual(self.actor.Iface, self.pawn)
        with self.assertRaises(ue4ss.UEError):
            self.actor.Iface = self.pawn
        obj, function = self.actor.OnHit
        self.assertEqual((obj, str(function).lower()), (self.pawn, "handle"))
        self.assertIsNone(self.actor.OnNone)
        self.assertEqual([(o, str(f).lower()) for o, f in self.actor.OnMany], [(self.pawn, "a"), (self.light, "b")])
        for name in ("OnHit", "OnMany"):
            with self.assertRaises(ue4ss.UEError):
                setattr(self.actor, name, None)

    def test_soft_references_in_containers(self):
        self.assertEqual(self.actor.Paths.to_list(), ["/A.A", "/B.B"])
        self.actor.Paths.append("/C.C")
        self.assertEqual(len(self.actor.Paths), 3)
        weights = self.actor.Weights
        self.assertEqual(weights["/A.A"], 1)
        weights["/B.B"] = 2
        self.assertEqual(len(weights), 2)


class BindEventTest(unittest.TestCase):
    """bind_event / unbind_event: binding a Python callback to a multicast delegate (Event Dispatcher) property."""

    def setUp(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_class("/Script/Engine.Actor", "/Script/CoreUObject.Object")
        ue4ss._mock_define_class("/Script/Engine.Light", "/Script/Engine.Actor")
        self.actor = ue4ss._mock_create_object("/Game/Map.Actor_1", "/Script/Engine.Actor")
        self.light = ue4ss._mock_create_object("/Game/Map.Light_1", "/Script/Engine.Light")
        ue4ss._mock_define_property(self.actor, "OnOpened", "multicast", [])

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_bind_receives_broadcast_with_context_and_params(self):
        seen = []
        event_id = ue4ss.bind_event(self.actor, "OnOpened", lambda c, p: seen.append((c, p.Amount)))
        fields = ue4ss._mock_struct_fields({"Amount": ("int32", 5)})
        ue4ss._broadcast_event(self.actor, "OnOpened", fields)
        self.assertEqual(len(seen), 1)
        context, amount = seen[0]
        self.assertEqual(context, self.actor)
        self.assertEqual(amount, 5)
        self.assertIsInstance(event_id, int)
        self.assertEqual(event_id, 1)  # the first binding

    def test_several_bindings_each_run_on_broadcast(self):
        calls = []
        ue4ss.bind_event(self.actor, "OnOpened", lambda c, p: calls.append("first"))
        ue4ss.bind_event(self.actor, "OnOpened", lambda c, p: calls.append("second"))
        ue4ss._broadcast_event(self.actor, "OnOpened", {})
        self.assertEqual(calls, ["first", "second"])

    def test_only_the_same_object_and_property_broadcast_reaches_the_callback(self):
        calls = []
        ue4ss.bind_event(self.actor, "OnOpened", lambda c, p: calls.append("actor"))
        other = ue4ss._mock_create_object("/Game/Map.Actor_2", "/Script/Engine.Actor")
        ue4ss._mock_define_property(other, "OnOpened", "multicast", [])
        ue4ss._mock_define_property(self.light, "OnOpened", "multicast", [])
        ue4ss._broadcast_event(other, "OnOpened", {})
        ue4ss._broadcast_event(self.light, "OnOpened", {})
        self.assertEqual(calls, [])  # no broadcast of the bound object yet
        ue4ss._broadcast_event(self.actor, "OnOpened", {})
        self.assertEqual(calls, ["actor"])

    def test_unbind_stops_delivery(self):
        calls = []
        event_id = ue4ss.bind_event(self.actor, "OnOpened", lambda c, p: calls.append("x"))
        ue4ss._broadcast_event(self.actor, "OnOpened", {})
        ue4ss.unbind_event(event_id)
        ue4ss._broadcast_event(self.actor, "OnOpened", {})
        self.assertEqual(calls, ["x"])
        with self.assertRaises(ValueError):
            ue4ss.unbind_event(event_id)  # idempotent rejection

    def test_type_and_rejection_errors(self):
        with self.assertRaises(TypeError):
            ue4ss.bind_event("not an object", "OnOpened", lambda c, p: None)
        with self.assertRaises(TypeError):
            ue4ss.bind_event(self.actor, "OnOpened", "not callable")
        with self.assertRaises(AttributeError):
            ue4ss.bind_event(self.actor, "Missing", lambda c, p: None)
        ue4ss._mock_define_property(self.actor, "Label", "str", "x")  # not a multicast delegate
        with self.assertRaises(ue4ss.UEError):
            ue4ss.bind_event(self.actor, "Label", lambda c, p: None)
        with self.assertRaises(TypeError):
            ue4ss.unbind_event("not an int")

    def test_game_thread_required(self):
        ue4ss._mock_enforce_game_thread = True
        with self.assertRaises(ue4ss.UEError):
            ue4ss.bind_event(self.actor, "OnOpened", lambda c, p: None)

    def test_async_binding_copies_params_and_runs_off_caller_thread(self):
        finished = threading.Event()
        seen = []
        caller = threading.current_thread()

        def callback(context, params):
            seen.append((context, params.Amount, threading.current_thread() is caller, params.is_owned()))
            finished.set()

        ue4ss.bind_event(self.actor, "OnOpened", callback, async_=True)
        fields = ue4ss._mock_struct_fields({"Amount": ("int32", 9)})
        ue4ss._broadcast_event(self.actor, "OnOpened", fields)
        self.assertTrue(finished.wait(1))
        self.assertEqual(seen, [(self.actor, 9, False, True)])


class PoisonedPointerTest(unittest.TestCase):
    def test_a_bad_pointer_reads_as_none(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_class("/Script/Engine.Actor", "/Script/CoreUObject.Object")
        other = ue4ss._mock_create_object("/Game/Map.Actor_2", "/Script/Engine.Actor")
        holder = ue4ss._mock_create_object("/Game/Map.Actor_1", "/Script/Engine.Actor")
        ue4ss._mock_define_property(holder, "Box", "struct:/Script/CoreUObject.Vector", {"Owner": ("object:/Script/Engine.Actor", other)})
        ue4ss._mock_define_struct_type("/Script/CoreUObject.Vector", types={"Owner": ("object:/Script/Engine.Actor", None)})
        box = holder.Box
        self.assertEqual(box.Owner, other)
        for mode in range(5):
            box.Owner = other
            ue4ss._selftest_poison_pointer(box, "Owner", mode)
            self.assertIsNone(box.Owner)
        with self.assertRaises(ue4ss.UEError):
            ue4ss._selftest_poison_pointer(box, "Owner", 9)
        with self.assertRaises(TypeError):
            ue4ss._selftest_poison_pointer(holder, "Box", 0)
        ue4ss._mock_reset_objects()

    def test_a_call_with_a_poisoned_block_reports_a_fault(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_class("/Script/Engine.Lib", "/Script/CoreUObject.Object")
        lib = ue4ss.static_find_object("/Script/Engine.Lib")
        function = ue4ss._mock_define_function("/Script/Engine.Lib", "Name", flags=0x2000, impl=lambda block: None,
                                               params=[{"name": "Object", "type": "ObjectProperty", "is_out": False, "is_return": False,
                                                       "mock_type": "object:/Script/CoreUObject.Object"}])
        block = function.make_param_block(lib)
        before = ue4ss._selftest_faults_survived()
        ue4ss._selftest_call_with_block(lib, function, block)  # fine while the block is good
        ue4ss._selftest_poison_pointer(block, "Object", 1)
        with self.assertRaises(ue4ss.UEError) as raised:
            ue4ss._selftest_call_with_block(lib, function, block)
        self.assertIn("faulted", str(raised.exception))
        self.assertEqual(ue4ss._selftest_faults_survived(), before + 1)
        ue4ss._mock_reset_objects()


class InOutParameterTest(unittest.TestCase):
    def test_in_out_values_are_returned_after_the_outputs(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_class("/Script/Engine.Lib", "/Script/CoreUObject.Object")

        def impl(block):
            block.Counter = block.Counter + block.Step  # in/out
            block.Doubled = block.Step * 2               # pure output
            block.Seen = block.Seen + 1                  # const reference: input only

        def spec(name, is_out, is_ref, is_const=False, is_return=False):
            return {"name": name, "type": "IntProperty", "is_out": is_out, "is_return": is_return, "is_reference": is_ref,
                    "is_const": is_const, "mock_type": "int32"}

        ue4ss._mock_define_function("/Script/Engine.Lib", "Bump", flags=0x2000, impl=impl, params=[
            spec("Counter", True, True), spec("Step", False, False), spec("Doubled", True, False),
            spec("Seen", True, True, is_const=True), spec("ReturnValue", True, False, is_return=True)])
        lib = ue4ss.static_find_object("/Script/Engine.Lib")
        # return value 0 (never set), the output, then the in/out; the const reference is not returned
        self.assertEqual(lib.Bump(10, 3, 5), (0, 6, 13))
        self.assertEqual(lib.Bump(Counter=1, Step=1, Seen=0), (0, 2, 2))
        with self.assertRaises(TypeError):
            lib.Bump(1, 2)  # Seen is a required input
        ue4ss._mock_reset_objects()


class EnumPropertyTest(unittest.TestCase):
    def setUp(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_class("/Script/Engine.Actor", "/Script/CoreUObject.Object")
        ue4ss._mock_define_enum("/Script/Engine.ENetRole", {"ENetRole::ROLE_None": 0, "ENetRole::ROLE_SimulatedProxy": 1, "ENetRole::ROLE_Authority": 3,
                                                           "ENetRole::ROLE_MAX": 4, "ENetRole::_Hidden_": 9})
        self.actor = ue4ss._mock_create_object("/Game/Map.Actor_1", "/Script/Engine.Actor")
        ue4ss._mock_define_property(self.actor, "Role", "enum:/Script/Engine.ENetRole", 3)
        ue4ss._mock_define_property(self.actor, "Odd", "enum:/Script/Engine.ENetRole", 2)

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_reads_are_int_enum_members_with_the_engines_names(self):
        import enum
        role = self.actor.Role
        self.assertIsInstance(role, enum.IntEnum)
        self.assertEqual((role.name, int(role)), ("ROLE_Authority", 3))
        self.assertEqual(role, 3)
        self.assertEqual(type(role).__name__, "ENetRole")
        self.assertIs(type(self.actor.Role), type(role))  # one class per enum

    def test_unnamed_values_stay_ints(self):
        odd = self.actor.Odd
        self.assertEqual(odd, 2)
        self.assertIs(type(odd), int)

    def test_writes_take_members_and_ints(self):
        role_class = type(self.actor.Role)
        self.actor.Role = role_class.ROLE_None
        self.assertEqual(self.actor.Role.name, "ROLE_None")
        self.actor.Role = 1
        self.assertEqual(self.actor.Role.name, "ROLE_SimulatedProxy")
        with self.assertRaises(OverflowError):
            self.actor.Role = 300
        with self.assertRaises(TypeError):
            self.actor.Role = "ROLE_None"

    def test_reserved_names_are_left_out(self):
        self.assertFalse(hasattr(type(self.actor.Role), "_Hidden_"))
        self.assertTrue(hasattr(type(self.actor.Role), "ROLE_MAX"))


class ReplacementTest(unittest.TestCase):
    PATH = "/Script/Engine.KismetMathLibrary:Add_IntInt"

    def setUp(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_class("/Script/Engine.KismetMathLibrary", "/Script/CoreUObject.Object")
        self.calls = []

        def add(block):
            self.calls.append(("impl", block.A, block.B))
            block.ReturnValue = block.A + block.B

        ue4ss._mock_define_function("/Script/Engine.KismetMathLibrary", "Add_IntInt", flags=0x2000, impl=add, params=[
            ("A", "IntProperty", False, False), ("B", "IntProperty", False, False), ("ReturnValue", "IntProperty", True, True)])
        self.library = ue4ss.static_find_object("/Script/Engine.KismetMathLibrary")

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_the_replacement_runs_instead_of_the_function(self):
        seen = []
        ue4ss.register_replacement(self.PATH, lambda context, params: seen.append((params.A, params.B)) or 99)
        self.assertEqual(self.library.Add_IntInt(2, 3), 99)
        self.assertEqual(seen, [(2, 3)])
        self.assertEqual(self.calls, [])  # the original never ran

    def test_call_original_lets_the_function_run(self):
        ue4ss.register_replacement(self.PATH, lambda context, params: ue4ss.CALL_ORIGINAL if params.A > 0 else -1)
        self.assertEqual(self.library.Add_IntInt(2, 3), 5)
        self.assertEqual(self.library.Add_IntInt(-2, 3), -1)
        self.assertEqual(self.calls, [("impl", 2, 3)])

    def test_none_means_no_result_and_a_bad_result_runs_the_original(self):
        first = ue4ss.register_replacement(self.PATH, lambda context, params: None)
        self.assertEqual(self.library.Add_IntInt(2, 3), 0)  # skipped, the result slot stays at its default
        self.assertEqual(self.calls, [])
        ue4ss.unregister_replacement(first)
        ue4ss.register_replacement(self.PATH, lambda context, params: "not an int")
        self.assertEqual(self.library.Add_IntInt(2, 3), 5)  # the bad value is reported and the original runs

    def test_an_exception_runs_the_original(self):
        def broken(context, params):
            raise RuntimeError("boom")
        ue4ss.register_replacement(self.PATH, broken)
        self.assertEqual(self.library.Add_IntInt(1, 1), 2)

    def test_the_replacement_can_wrap_the_original(self):
        ue4ss.register_replacement(self.PATH, lambda context, params: context.Add_IntInt(params.A, params.B) * 10)
        self.assertEqual(self.library.Add_IntInt(2, 3), 50)  # the nested call ran the original
        self.assertEqual(self.calls, [("impl", 2, 3)])

    def test_unregister_and_errors(self):
        replacement = ue4ss.register_replacement(self.PATH, lambda c, p: 1)
        with self.assertRaises(ue4ss.UEError):
            ue4ss.register_replacement(self.PATH, lambda c, p: 2)  # one per function
        ue4ss.unregister_replacement(replacement)
        self.assertEqual(self.library.Add_IntInt(2, 3), 5)
        with self.assertRaises(ValueError):
            ue4ss.unregister_replacement(replacement)
        with self.assertRaises(TypeError):
            ue4ss.register_replacement(5, print)
        with self.assertRaises(TypeError):
            ue4ss.register_replacement(self.PATH, 5)
        with self.assertRaises(ue4ss.UEError):
            ue4ss.register_replacement("/Script/Engine.Nothing:Nope", print)

    def test_hooks_do_not_run_for_a_replaced_call(self):
        seen = []
        ue4ss.register_hook(self.PATH, lambda c, p: seen.append("pre"), lambda c, p, r: seen.append("post"))
        ue4ss.register_replacement(self.PATH, lambda c, p: 7)
        self.library.Add_IntInt(1, 1)
        self.assertEqual(seen, [])


class SlowCallbackSettingTest(unittest.TestCase):
    def test_accepts_non_negative_ints_only(self):
        ue4ss.set_slow_callback_warning(10)
        ue4ss.set_slow_callback_warning(0)
        ue4ss.set_slow_callback_warning(50)
        for bad, exc in ((-1, ValueError), (1.5, TypeError), ("5", TypeError), (True, TypeError), (None, TypeError)):
            with self.assertRaises(exc):
                ue4ss.set_slow_callback_warning(bad)


class RotationTest(unittest.TestCase):
    SQRT_HALF = 0.7071067811865476

    def assertQuat(self, q, x, y, z, w, places=9):
        for got, want in zip((q["X"], q["Y"], q["Z"], q["W"]), (x, y, z, w)):
            self.assertAlmostEqual(got, want, places=places)

    def test_unreal_order_known_values(self):
        self.assertQuat(ue4ss.euler_to_quat(0, 90, 0), 0, 0, self.SQRT_HALF, self.SQRT_HALF)
        self.assertQuat(ue4ss.euler_to_quat(90, 0, 0), 0, -self.SQRT_HALF, 0, self.SQRT_HALF)
        self.assertQuat(ue4ss.euler_to_quat(0, 0, 90), -self.SQRT_HALF, 0, 0, self.SQRT_HALF)
        self.assertQuat(ue4ss.euler_to_quat(30, 60, 45), -0.2005621211, -0.3919038373, 0.3604234057, 0.8223631719, places=9)
        self.assertQuat(ue4ss.euler_to_quat(-20, 135, 10), -0.1926658635, -0.0130986961, 0.9121731943, 0.3614531129, places=9)
        self.assertQuat(ue4ss.euler_to_quat(0, 0, 0), 0, 0, 0, 1)
        self.assertQuat(ue4ss.euler_to_quat(0, 3.141592653589793 / 2, 0, degrees=False), 0, 0, self.SQRT_HALF, self.SQRT_HALF)

    def test_axis_orders(self):
        self.assertQuat(ue4ss.euler_to_quat(90, 0, 0, order="xyz"), self.SQRT_HALF, 0, 0, self.SQRT_HALF)
        self.assertQuat(ue4ss.euler_to_quat(90, 0, 0, order="zyx"), 0, 0, self.SQRT_HALF, self.SQRT_HALF)
        for p, y, r in ((10, 20, 30), (-45, 170, 80), (60, -100, -10)):
            unreal = ue4ss.euler_to_quat(p, y, r)
            zyx = ue4ss.euler_to_quat(y, -p, -r, order="zyx")   # unreal == zyx(yaw, -pitch, -roll)
            for key in "XYZW":
                self.assertAlmostEqual(unreal[key], zyx[key], places=12)

    def test_round_trips_for_every_order(self):
        import random
        rng = random.Random(3)
        for order in ("unreal", "xyz", "xzy", "yxz", "yzx", "zxy", "zyx"):
            for _ in range(200):
                a, b, c = rng.uniform(-179, 179), rng.uniform(-89, 89), rng.uniform(-179, 179)
                if order == "unreal":
                    a, b = b, a   # pitch is the limited angle
                back = ue4ss.quat_to_euler(ue4ss.euler_to_quat(a, b, c, order=order), order=order)
                self.assertAlmostEqual(back[0], a, places=6)
                self.assertAlmostEqual(back[1], b, places=6)
                self.assertAlmostEqual(back[2], c, places=6)

    def test_quat_to_euler_inputs_radians_and_poles(self):
        self.assertAlmostEqual(ue4ss.quat_to_euler((0, 0, self.SQRT_HALF, self.SQRT_HALF))[1], 90, places=9)
        self.assertAlmostEqual(ue4ss.quat_to_euler({"x": 0, "Y": 0, "z": self.SQRT_HALF, "W": self.SQRT_HALF}, degrees=False)[1], 3.141592653589793 / 2, places=9)
        pole = ue4ss.euler_to_quat(90, 40, 25)
        pitch, yaw, roll = ue4ss.quat_to_euler(pole)
        self.assertAlmostEqual(pitch, 90, places=3)
        again = ue4ss.euler_to_quat(pitch, yaw, roll)
        self.assertAlmostEqual(abs(sum(pole[k] * again[k] for k in "XYZW")), 1.0, places=9)
        self.assertEqual(ue4ss.quat_to_euler((0, 0, 0, 0)), (0.0, 0.0, 0.0))

    def test_bad_arguments(self):
        for call, exc in ((lambda: ue4ss.euler_to_quat(0, 0, 0, order="xxy"), ValueError), (lambda: ue4ss.euler_to_quat(0, 0, 0, order="abc"), ValueError),
                          (lambda: ue4ss.euler_to_quat(0, 0, 0, order=5), TypeError), (lambda: ue4ss.euler_to_quat("1", 0, 0), TypeError),
                          (lambda: ue4ss.euler_to_quat(0, 0, 0, degrees="yes"), TypeError), (lambda: ue4ss.quat_to_euler((0, 0, 1)), ValueError),
                          (lambda: ue4ss.quat_to_euler({"X": 0}), ValueError), (lambda: ue4ss.quat_to_euler(5), TypeError)):
            with self.assertRaises(exc):
                call()

    def test_quaternion_fields_accept_euler_angles(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_class("/Script/Engine.Actor", "/Script/CoreUObject.Object")
        ue4ss._mock_define_struct_type("/Script/CoreUObject.Quat", types={"X": ("float", 0.0), "Y": ("float", 0.0), "Z": ("float", 0.0), "W": ("float", 1.0)})
        ue4ss._mock_define_struct_type("/Script/CoreUObject.Rotator", types={"Pitch": ("float", 0.0), "Yaw": ("float", 0.0), "Roll": ("float", 0.0)})
        ue4ss._mock_define_struct_type("/Script/CoreUObject.Transform", types={"Rotation": ("struct:/Script/CoreUObject.Quat", {}), "Translation": ("float", 0.0)})
        actor = ue4ss._mock_create_object("/Game/Map.Actor_1", "/Script/Engine.Actor")
        ue4ss._mock_define_property(actor, "Facing", "struct:/Script/CoreUObject.Quat", {"X": ("float", 0.0), "Y": ("float", 0.0), "Z": ("float", 0.0), "W": ("float", 1.0)})
        want = ue4ss.euler_to_quat(0, 90, 0)
        for value in ((0, 90, 0), [0, 90, 0], {"Yaw": 90}, {"yaw": 90.0, "PITCH": 0}, {"Pitch": 0, "Yaw": 90, "Roll": 0}):
            actor.Facing = {"X": 0.0, "Y": 0.0, "Z": 0.0, "W": 1.0}
            actor.Facing = value
            for key in "XYZW":
                self.assertAlmostEqual(getattr(actor.Facing, key), want[key], places=6)
        rotator = ue4ss.make_struct("/Script/CoreUObject.Rotator", Pitch=0.0, Yaw=90.0, Roll=0.0)
        actor.Facing = {"X": 0.0, "Y": 0.0, "Z": 0.0, "W": 1.0}
        actor.Facing = rotator
        self.assertAlmostEqual(actor.Facing.Z, self.SQRT_HALF, places=6)
        actor.Facing = {"X": 0.0, "Y": 0.0, "Z": 1.0, "W": 0.0}   # a normal quaternion dict still works
        self.assertEqual((actor.Facing.Z, actor.Facing.W), (1.0, 0.0))
        for bad, exc in (((1, 2), ValueError), ({"Pitch": 1, "X": 2}, ValueError), ({"Pitch": 1, "Banana": 2}, ValueError), (("a", 0, 0), TypeError)):
            with self.assertRaises(exc):
                actor.Facing = bad
        ue4ss._mock_reset_objects()

    def test_make_transform(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_struct_type("/Script/CoreUObject.Vector", types={"X": ("float", 0.0), "Y": ("float", 0.0), "Z": ("float", 0.0)})
        ue4ss._mock_define_struct_type("/Script/CoreUObject.Quat", types={"X": ("float", 0.0), "Y": ("float", 0.0), "Z": ("float", 0.0), "W": ("float", 1.0)})
        vector = {"X": ("float", 0.0), "Y": ("float", 0.0), "Z": ("float", 0.0)}
        quat = {"X": ("float", 0.0), "Y": ("float", 0.0), "Z": ("float", 0.0), "W": ("float", 1.0)}
        ue4ss._mock_define_struct_type("/Script/CoreUObject.Transform", types={"Rotation": ("struct:/Script/CoreUObject.Quat", quat),
                                                                                "Translation": ("struct:/Script/CoreUObject.Vector", vector),
                                                                                "Scale3D": ("struct:/Script/CoreUObject.Vector", vector)})
        transform = ue4ss.make_transform(location=(1, 2, 3), rotation=(0, 90, 0), scale={"X": 2, "Y": 2, "Z": 2})
        self.assertTrue(transform.is_owned())
        self.assertEqual((transform.Translation.X, transform.Translation.Y, transform.Translation.Z), (1.0, 2.0, 3.0))
        self.assertAlmostEqual(transform.Rotation.Z, self.SQRT_HALF, places=6)
        self.assertEqual(transform.Scale3D.X, 2.0)
        ue4ss._mock_reset_objects()


class HookTest(unittest.TestCase):
    PATH = "/Script/Engine.KismetMathLibrary:Add_IntInt"

    def setUp(self):
        ue4ss._mock_reset_objects()
        self.library = ue4ss._mock_define_class("/Script/Engine.KismetMathLibrary", "/Script/CoreUObject.Object")
        self.trace = []

        def add(block):
            self.trace.append(("impl", block.A, block.B))
            block.ReturnValue = block.A + block.B
        self.add = ue4ss._mock_define_function("/Script/Engine.KismetMathLibrary", "Add_IntInt", flags=0x2000, impl=add, params=[
            ("A", "IntProperty", False, False), ("B", "IntProperty", False, False), ("ReturnValue", "IntProperty", True, True)])

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_pre_and_post_run_around_the_function_in_order(self):
        def pre(context, params):
            self.trace.append(("pre", context, params.A, params.B, params.ReturnValue))

        def post(context, params, result):
            self.trace.append(("post", result))
        ids = ue4ss.register_hook(self.PATH, pre, post)
        self.assertEqual(self.library.Add_IntInt(2, 3), 5)
        self.assertEqual(self.trace, [("pre", self.library.get_cdo(), 2, 3, 0), ("impl", 2, 3), ("post", 5)])
        self.assertEqual(len(ids), 2)
        self.assertTrue(all(isinstance(i, int) for i in ids))

    def test_pre_hook_can_change_the_inputs_and_post_hook_the_result(self):
        def pre(context, params):
            params.A = 100

        def post(context, params, result):
            params.ReturnValue = result + 1
        ue4ss.register_hook(self.PATH, pre=pre, post=post)
        self.assertEqual(self.library.Add_IntInt(1, 2), 103)  # (100 + 2) + 1

    def test_only_one_callback_is_needed(self):
        ue4ss.register_hook(self.PATH, post=lambda context, params, result: self.trace.append(("post", result)))
        self.library.Add_IntInt(1, 1)
        self.assertEqual(self.trace, [("impl", 1, 1), ("post", 2)])

    def test_parameters_die_with_the_callback_but_copy_survives(self):
        seen = {}

        def pre(context, params):
            seen["view"] = params
            seen["copy"] = params.copy()
            seen["alive_inside"] = params.is_valid()
        ue4ss.register_hook(self.PATH, pre)
        self.library.Add_IntInt(4, 5)
        self.assertTrue(seen["alive_inside"])
        self.assertFalse(seen["view"].is_valid())
        with self.assertRaises(ue4ss.InvalidObjectError) as caught:
            seen["view"].A
        self.assertIn("only valid inside the hook callback", str(caught.exception))
        with self.assertRaises(ue4ss.InvalidObjectError):
            seen["view"].A = 1
        self.assertEqual(repr(seen["view"]), "<Struct (hook parameters out of scope)>")
        self.assertTrue(seen["copy"].is_owned())
        self.assertEqual((seen["copy"].A, seen["copy"].B), (4, 5))  # the copy outlives the callback
        self.assertFalse(seen["view"].is_owned())

    def test_nested_views_of_the_parameters_die_too(self):
        ue4ss._mock_define_struct_type("/Script/CoreUObject.Vector", types={"X": ("float", 0.0)})
        place = ue4ss._mock_define_function("/Script/Engine.KismetMathLibrary", "Place", flags=0x2000, impl=lambda block: None, params=[
            {"name": "At", "type": "StructProperty", "is_out": False, "is_return": False, "mock_type": "struct:/Script/CoreUObject.Vector"}])
        views = []
        ue4ss.register_hook("/Script/Engine.KismetMathLibrary:Place", lambda context, params: views.append(params.At))
        self.library.Place({"X": 3.0})
        with self.assertRaises(ue4ss.InvalidObjectError):
            views[0].X

    def test_exceptions_in_hooks_are_logged_and_do_not_stop_the_call(self):
        def pre(context, params):
            raise RuntimeError("boom")

        def post(context, params, result):
            raise SystemExit(3)
        ue4ss.register_hook(self.PATH, pre, post)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.library.Add_IntInt(1, 2), 3)  # the function still ran and returned
        self.assertIn("RuntimeError: boom", err.getvalue())
        self.assertIn("sys.exit() inside a hook is ignored", err.getvalue())

    def test_hooks_nested_too_deep_are_skipped(self):
        depth = []

        def pre(context, params):
            depth.append(1)
            self.library.Add_IntInt(0, 0)  # the hooked function calls itself from its own hook
        ue4ss.register_hook(self.PATH, pre)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.library.Add_IntInt(1, 1)
        self.assertEqual(len(depth), 8)  # exactly 8 nested hook runs, then the rest are skipped
        self.assertIn("nested more than 8 deep", err.getvalue())

    def test_unregister_by_tuple_and_by_ids(self):
        calls = []
        first = ue4ss.register_hook(self.PATH, lambda c, p: calls.append("first"))
        second = ue4ss.register_hook(self.PATH, lambda c, p: calls.append("second"))
        self.library.Add_IntInt(1, 1)
        self.assertEqual(calls, ["first", "second"])  # registration order
        ue4ss.unregister_hook(first)
        self.library.Add_IntInt(1, 1)
        self.assertEqual(calls, ["first", "second", "second"])
        ue4ss.unregister_hook(*second)
        self.library.Add_IntInt(1, 1)
        self.assertEqual(len(calls), 3)
        with self.assertRaises(ValueError):
            ue4ss.unregister_hook(first)  # already gone
        with self.assertRaises(ValueError):
            ue4ss.unregister_hook(second[0], second[1] + 99)
        with self.assertRaises(TypeError):
            ue4ss.unregister_hook("x")

    def test_registration_errors(self):
        with self.assertRaises(TypeError):
            ue4ss.register_hook(self.PATH)
        with self.assertRaises(TypeError):
            ue4ss.register_hook(self.PATH, 5)
        with self.assertRaises(TypeError):
            ue4ss.register_hook(self.PATH, None, "x")
        with self.assertRaises(TypeError):
            ue4ss.register_hook(5, lambda c, p: None)
        with self.assertRaises(ue4ss.UEError):
            ue4ss.register_hook("/Script/Engine.KismetMathLibrary:Nope", lambda c, p: None)
        with self.assertRaises(ue4ss.UEError):
            ue4ss.register_hook("/Script/Engine.KismetMathLibrary", lambda c, p: None)  # a class, not a function

    def test_post_hook_gets_the_result_and_none_for_functions_that_return_nothing(self):
        seen = []
        ue4ss.register_hook(self.PATH, post=lambda context, params, result: seen.append(result))
        self.library.Add_IntInt(20, 22)
        self.assertEqual(seen, [42])
        ue4ss._mock_define_function("/Script/Engine.KismetMathLibrary", "Nothing", flags=0x2000, impl=lambda block: None)
        ue4ss.register_hook("/Script/Engine.KismetMathLibrary:Nothing", post=lambda context, params, result: seen.append(("nothing", result)))
        self.library.Nothing()
        self.assertEqual(seen[-1], ("nothing", None))

    def test_blueprint_style_single_output_is_the_result(self):
        # Blueprint functions return through an output pin: a plain out parameter, no return parameter
        def impl(block):
            block.IsReady = True
        ue4ss._mock_define_function("/Script/Engine.KismetMathLibrary", "BpIsReady", flags=0x2000, impl=impl, params=[("IsReady", "BoolProperty", True, False)])
        seen = []
        ids = ue4ss.register_hook("/Script/Engine.KismetMathLibrary:BpIsReady", post=lambda context, params, result: (seen.append((result, params.IsReady)), False)[1])
        self.assertIs(self.library.BpIsReady(), False)      # the post hook replaced the output
        self.assertEqual(seen, [(True, True)])
        ue4ss.unregister_hook(ids)
        self.assertIs(self.library.BpIsReady(), True)

    def test_two_outputs_without_a_return_value_have_no_single_result(self):
        ue4ss._mock_define_function("/Script/Engine.KismetMathLibrary", "TwoOuts", flags=0x2000, impl=lambda block: None, params=[
            ("A", "IntProperty", True, False), ("B", "IntProperty", True, False)])
        seen = []
        ue4ss.register_hook("/Script/Engine.KismetMathLibrary:TwoOuts", post=lambda context, params, result: seen.append(result))
        self.library.TwoOuts()
        self.assertEqual(seen, [None])

    def test_post_hook_can_override_the_result_by_returning_a_value(self):
        ue4ss.register_hook(self.PATH, post=lambda context, params, result: result * 10)
        self.assertEqual(self.library.Add_IntInt(2, 3), 50)
        self.assertEqual(self.trace, [("impl", 2, 3)])  # the original still ran

    def test_returning_none_keeps_the_result_and_later_hooks_see_the_override(self):
        seen = []
        ue4ss.register_hook(self.PATH, post=lambda context, params, result: None)
        ue4ss.register_hook(self.PATH, post=lambda context, params, result: result + 1000)
        ue4ss.register_hook(self.PATH, post=lambda context, params, result: seen.append(result))
        self.assertEqual(self.library.Add_IntInt(1, 1), 1002)
        self.assertEqual(seen, [1002])  # a later hook reads what the earlier one wrote

    def test_override_is_converted_strictly_and_a_bad_one_is_reported_not_applied(self):
        for bad, error in (("not a number", "TypeError"), (2**31, "OverflowError"), (1.5, "TypeError")):
            with self.subTest(bad=bad):
                ids = ue4ss.register_hook(self.PATH, post=lambda context, params, result, bad=bad: bad)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(self.library.Add_IntInt(2, 3), 5)  # the original result stays
                self.assertIn(error, err.getvalue())
                ue4ss.unregister_hook(ids)

    def test_a_value_returned_from_a_pre_hook_is_ignored_with_a_message(self):
        ue4ss.register_hook(self.PATH, pre=lambda context, params: 12345)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.library.Add_IntInt(1, 2), 3)
        self.assertIn("only post-hooks can change the result", err.getvalue())

    def test_returning_a_value_from_a_function_without_a_result_is_ignored_with_a_message(self):
        ue4ss._mock_define_function("/Script/Engine.KismetMathLibrary", "Nothing", flags=0x2000, impl=lambda block: None)
        ue4ss.register_hook("/Script/Engine.KismetMathLibrary:Nothing", post=lambda context, params, result: 7)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(self.library.Nothing())
        self.assertIn("has no result to override", err.getvalue())

    def test_pre_hook_changes_to_outputs_and_inputs(self):
        def double_inputs(context, params):
            params.A, params.B = params.A * 2, params.B * 2
        ue4ss.register_hook(self.PATH, pre=double_inputs)
        self.assertEqual(self.library.Add_IntInt(1, 2), 6)

    def test_object_and_struct_results_can_be_overridden(self):
        actor = ue4ss._mock_define_class("/Script/Engine.Actor", "/Script/CoreUObject.Object")
        pawn = ue4ss._mock_create_object("/Game/A.A:Pawn_0", "/Script/Engine.Actor")
        ue4ss._mock_define_struct_type("/Script/CoreUObject.Vector", types={"X": ("float", 0.0), "Y": ("float", 0.0)})
        ue4ss._mock_define_function("/Script/Engine.KismetMathLibrary", "GetThing", flags=0x2000, impl=lambda block: None, params=[
            {"name": "ReturnValue", "type": "ObjectProperty", "is_out": True, "is_return": True, "mock_type": "object:/Script/Engine.Actor"}])
        ue4ss._mock_define_function("/Script/Engine.KismetMathLibrary", "GetWhere", flags=0x2000, impl=lambda block: None, params=[
            {"name": "ReturnValue", "type": "StructProperty", "is_out": True, "is_return": True, "mock_type": "struct:/Script/CoreUObject.Vector"}])
        hook = ue4ss.register_hook("/Script/Engine.KismetMathLibrary:GetThing", post=lambda c, p, r: pawn)
        self.assertEqual(self.library.GetThing(), pawn)
        ue4ss.unregister_hook(hook)
        ue4ss.register_hook("/Script/Engine.KismetMathLibrary:GetThing", post=lambda c, p, r: ue4ss.NULL)
        self.assertIsNone(self.library.GetThing())  # NULL sets a null reference
        ue4ss.register_hook("/Script/Engine.KismetMathLibrary:GetWhere", post=lambda c, p, r: {"X": 3.0, "Y": 4.0})
        self.assertEqual(self.library.GetWhere().to_dict(), {"X": 3.0, "Y": 4.0})

    def test_null_sentinel(self):
        self.assertFalse(ue4ss.NULL)
        self.assertEqual(repr(ue4ss.NULL), "ue4ss.NULL")
        self.assertIsInstance(ue4ss.NULL, ue4ss.NullType)
        with self.assertRaises(TypeError):
            ue4ss.NullType()

    def test_only_the_hooked_function_triggers_the_hook(self):
        other = ue4ss._mock_define_function("/Script/Engine.KismetMathLibrary", "Other", flags=0x2000, impl=lambda block: None)
        ue4ss.register_hook(self.PATH, lambda c, p: self.trace.append("hooked"))
        self.library.Other()
        self.assertEqual(self.trace, [])

    def test_registering_needs_the_game_thread(self):
        ue4ss._mock_enforce_game_thread = True
        with self.assertRaises(ue4ss.UEError):
            ue4ss.register_hook(self.PATH, lambda c, p: None)


class FindObjectsTest(unittest.TestCase):
    def setUp(self):
        ue4ss._mock_reset_objects()
        ue4ss._mock_define_class("/Script/Engine.Actor", "/Script/CoreUObject.Object")
        ue4ss._mock_define_class("/Script/Engine.Pawn", "/Script/Engine.Actor")
        ue4ss._mock_define_class("/Script/Engine.Controller", "/Script/Engine.Actor")
        self.pawn_a = ue4ss._mock_create_object("/Game/Arena.Arena:Pawn_0", "/Script/Engine.Pawn")
        self.pawn_b = ue4ss._mock_create_object("/Game/Arena.Arena:Pawn_1", "/Script/Engine.Pawn")
        self.controller = ue4ss._mock_create_object("/Game/Arena.Arena:Controller_0", "/Script/Engine.Controller")
        self.actor = ue4ss._mock_create_object("/Game/Arena.Arena:Actor_0", "/Script/Engine.Actor")

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_find_all_of_includes_subclasses_and_skips_default_objects(self):
        self.assertEqual(ue4ss.find_all_of("Pawn"), [self.pawn_a, self.pawn_b])
        actors = ue4ss.find_all_of("Actor")
        self.assertEqual(actors, [self.pawn_a, self.pawn_b, self.controller, self.actor])
        self.assertTrue(all(not o.get_fname().to_string().startswith("Default__") for o in actors))
        self.assertEqual(ue4ss.find_all_of("NeverDefined"), [])
        self.assertIsInstance(ue4ss.find_all_of("Pawn"), list)

    def test_find_all_of_agrees_with_find_first_of(self):
        self.assertEqual(ue4ss.find_all_of("Pawn")[0], ue4ss.find_first_of("Pawn"))

    def test_find_objects_by_class_name_and_both(self):
        no_cdo = ue4ss.ObjectFlags.RF_ClassDefaultObject
        self.assertEqual(ue4ss.find_objects("Pawn", banned_flags=no_cdo), [self.pawn_a, self.pawn_b])
        self.assertEqual(ue4ss.find_objects(name="Pawn_1"), [self.pawn_b])
        self.assertEqual(ue4ss.find_objects("Pawn", "Pawn_1"), [self.pawn_b])
        self.assertEqual(ue4ss.find_objects("Controller", "Pawn_1"), [])
        self.assertEqual(ue4ss.find_objects(class_name="Pawn", name="pawn_0"), [self.pawn_a])  # names are case-insensitive

    def test_unlike_find_all_of_find_objects_includes_default_objects(self):
        names = [o.get_fname().to_string() for o in ue4ss.find_objects("Pawn")]
        self.assertEqual(names, ["Default__Pawn", "Pawn_0", "Pawn_1"])
        self.assertEqual([o.get_fname().to_string() for o in ue4ss.find_all_of("Pawn")], ["Pawn_0", "Pawn_1"])

    def test_exact_class_excludes_subclasses(self):
        no_cdo = ue4ss.ObjectFlags.RF_ClassDefaultObject
        self.assertEqual(ue4ss.find_objects("Actor", exact_class=True, banned_flags=no_cdo), [self.actor])
        self.assertEqual(ue4ss.find_objects("Actor", exact_class=False, banned_flags=no_cdo), [self.pawn_a, self.pawn_b, self.controller, self.actor])
        self.assertEqual([o.get_fname().to_string() for o in ue4ss.find_objects("Actor", exact_class=True)], ["Default__Actor", "Actor_0"])

    def test_default_objects_are_found_unless_banned(self):
        flags = ue4ss.ObjectFlags.RF_ClassDefaultObject
        cdos = ue4ss.find_objects("Pawn", required_flags=flags)
        self.assertEqual([c.get_fname().to_string() for c in cdos], ["Default__Pawn"])
        self.assertEqual(ue4ss.find_objects("Pawn", banned_flags=flags), [self.pawn_a, self.pawn_b])
        self.assertEqual(len(ue4ss.find_objects("Pawn")), 3)  # no filter: the default object too

    def test_find_all_of_is_find_objects_without_default_objects_and_archetypes(self):
        ue4ss._mock_set_flags(self.pawn_b, ue4ss.ObjectFlags.RF_ArchetypeObject)  # a Blueprint child-actor template, not an instance
        not_instances = ue4ss.ObjectFlags.RF_ClassDefaultObject | ue4ss.ObjectFlags.RF_ArchetypeObject
        self.assertEqual(ue4ss.find_all_of("Pawn"), [self.pawn_a])
        self.assertEqual(ue4ss.find_all_of("Pawn"), ue4ss.find_objects("Pawn", banned_flags=not_instances))
        self.assertIn(self.pawn_b, ue4ss.find_objects("Pawn", banned_flags=ue4ss.ObjectFlags.RF_ClassDefaultObject))

    def test_required_and_banned_flags(self):
        ue4ss._mock_set_flags(self.pawn_a, ue4ss.ObjectFlags.RF_Transient | ue4ss.ObjectFlags.RF_Public)
        ue4ss._mock_set_flags(self.pawn_b, ue4ss.ObjectFlags.RF_Transient)
        banned_cdo = ue4ss.ObjectFlags.RF_ClassDefaultObject
        both = ue4ss.ObjectFlags.RF_Transient | ue4ss.ObjectFlags.RF_Public
        self.assertEqual(ue4ss.find_objects("Pawn", required_flags=ue4ss.ObjectFlags.RF_Transient, banned_flags=banned_cdo), [self.pawn_a, self.pawn_b])
        self.assertEqual(ue4ss.find_objects("Pawn", required_flags=both), [self.pawn_a])  # ALL required flags must be set
        self.assertEqual(ue4ss.find_objects("Pawn", banned_flags=ue4ss.ObjectFlags.RF_Public | banned_cdo), [self.pawn_b])  # ANY banned flag excludes

    def test_limit(self):
        self.assertEqual(ue4ss.find_objects("Pawn", banned_flags=ue4ss.ObjectFlags.RF_ClassDefaultObject, limit=1), [self.pawn_a])
        self.assertEqual(len(ue4ss.find_objects("Actor", limit=2)), 2)
        self.assertEqual(ue4ss.find_objects("Pawn", limit=1)[0].get_fname().to_string(), "Default__Pawn")  # creation order
        self.assertEqual(len(ue4ss.find_objects("Actor", limit=0)), len(ue4ss.find_objects("Actor")))

    def test_argument_errors(self):
        with self.assertRaises(ValueError):
            ue4ss.find_objects()
        with self.assertRaises(ValueError):
            ue4ss.find_objects(None, None)
        with self.assertRaises(TypeError):
            ue4ss.find_objects(5)
        with self.assertRaises(TypeError):
            ue4ss.find_objects("Pawn", required_flags="x")
        with self.assertRaises(ValueError):
            ue4ss.find_objects("Pawn", banned_flags=-1)
        with self.assertRaises(ValueError):
            ue4ss.find_objects("Pawn", required_flags=2**31)
        with self.assertRaises(ValueError):
            ue4ss.find_objects("Pawn", limit=-1)
        with self.assertRaises(TypeError):
            ue4ss.find_all_of(5)

    def test_destroyed_objects_are_not_found_and_old_wrappers_stay_invalid(self):
        found = ue4ss.find_all_of("Pawn")
        ue4ss._mock_destroy_object(self.pawn_a)
        self.assertEqual(ue4ss.find_all_of("Pawn"), [self.pawn_b])
        self.assertFalse(found[0].is_valid())

    def test_object_flags_enum(self):
        self.assertEqual(int(ue4ss.ObjectFlags.RF_ClassDefaultObject), 0x10)
        self.assertEqual(int(ue4ss.ObjectFlags.RF_Public | ue4ss.ObjectFlags.RF_Transient), 0x41)
        self.assertEqual(ue4ss.ObjectFlags(0x41), ue4ss.ObjectFlags.RF_Public | ue4ss.ObjectFlags.RF_Transient)

    def test_game_thread_requirement(self):
        ue4ss._mock_enforce_game_thread = True
        with self.assertRaises(ue4ss.UEError):
            ue4ss.find_all_of("Pawn")
        with self.assertRaises(ue4ss.UEError):
            ue4ss.find_objects("Pawn")


class ParityTest(unittest.TestCase):
    def test_mock_has_the_same_functions_and_classes_as_the_real_module(self):
        source = (ROOT / "src" / "UeModule.cpp").read_text()
        table = set(re.findall(r'\{"(\w+)",\s*[^{}]*?METH_', source))
        bootstrap = source[source.index('R"PY('):source.index(')PY"')]
        defined_in_python = set(re.findall(r"^def (\w+)\(", bootstrap, re.M))
        real_functions = {name for name in (table | defined_in_python) if not name.startswith("_") or name.startswith("_selftest_")}
        # Private helpers start with an underscore; only _selftest_* functions are part of the module surface.
        mock_functions = {name for name, obj in vars(ue4ss).items()
                          if inspect.isfunction(obj) and obj.__module__ == "ue4ss"
                          and (not name.startswith("_") or name.startswith("_selftest_"))}
        self.assertEqual(mock_functions, real_functions)
        self.assertIn("execute_in_game_thread", real_functions)  # the regexes found something
        bootstrap_classes = set(re.findall(r"^class (\w+)\(", bootstrap, re.M))

        kinds_source = (ROOT / "src" / "PyKinds.cpp").read_text()
        sources = source + (ROOT / "src" / "PyFName.cpp").read_text() + (ROOT / "src" / "PyUObject.cpp").read_text() + (ROOT / "src" / "PyStruct.cpp").read_text() + (ROOT / "src" / "PyArray.cpp").read_text() + (ROOT / "src" / "PyFixedArray.cpp").read_text() + (ROOT / "src" / "PyMapSet.cpp").read_text() + (ROOT / "src" / "PyCall.cpp").read_text() + (ROOT / "src" / "PyHooks.cpp").read_text()
        real_classes = set(re.findall(r'PyModule_AddObjectRef\(module, "(\w+)"', sources))
        real_classes |= set(re.findall(r'make_subtype\(module, "ue4ss\.(\w+)"', kinds_source))
        real_classes |= bootstrap_classes
        constants = {"NULL", "CALL_ORIGINAL"}  # added with PyModule_AddObjectRef like classes, but values
        real_classes -= constants
        for constant in constants:
            self.assertTrue(hasattr(ue4ss, constant), constant)
        mock_classes = {name for name, obj in vars(ue4ss).items() if inspect.isclass(obj) and obj.__module__ == "ue4ss"
                        and not name.startswith("_")}
        self.assertEqual(mock_classes, real_classes)
        self.assertEqual(real_classes, {"UEError", "InvalidObjectError", "FName", "UObject", "Struct", "UStruct", "UClass", "UScriptStruct", "UFunction", "UEnum", "BoundFunction", "NullType", "CallOriginalType", "ObjectFlags", "Key", "ModifierKey", "Array", "FixedArray", "Map", "Set"})

        uobject_source = (ROOT / "src" / "PyUObject.cpp").read_text()
        real_methods = set(re.findall(r'\{"(\w+)",\s*[^{}]*?METH_', uobject_source))
        mock_methods = {name for name, obj in vars(ue4ss.UObject).items()
                        if inspect.isfunction(obj) and not name.startswith("_")}
        self.assertEqual(mock_methods, real_methods)

        for table, class_name in (("struct", "UStruct"), ("class", "UClass"), ("scriptstruct", "UScriptStruct"),
                                  ("function", "UFunction"), ("enum", "UEnum")):
            block = re.search(r"s_%s_methods\[\] = \{(.*?)\};" % table, kinds_source, re.S).group(1)
            real = set(re.findall(r'\{"(\w+)",\s*[^{}]*?METH_', block))
            mock = {name for name, obj in vars(getattr(ue4ss, class_name)).items() if inspect.isfunction(obj) and not name.startswith("_")}
            with self.subTest(kind=class_name):
                self.assertEqual(mock, real)

        call_source = (ROOT / "src" / "PyCall.cpp").read_text()
        real_bound = set(re.findall(r'\{"(\w+)",\s*[^{}]*?METH_', call_source))
        mock_bound = {name for name, obj in vars(ue4ss.BoundFunction).items() if inspect.isfunction(obj) and not name.startswith("_")}
        self.assertEqual(mock_bound, real_bound)

        struct_source = (ROOT / "src" / "PyStruct.cpp").read_text()
        real_struct_methods = set(re.findall(r'\{"(\w+)",\s*[^{}]*?METH_', struct_source))
        mock_struct_methods = {name for name, obj in vars(ue4ss.Struct).items()
                               if inspect.isfunction(obj) and not name.startswith("_")}
        self.assertEqual(mock_struct_methods, real_struct_methods)

        array_source = (ROOT / "src" / "PyArray.cpp").read_text()
        real_array_methods = set(re.findall(r'\{"(\w+)",\s*[^{}]*?METH_', array_source))
        mock_array_methods = {name for name, obj in vars(ue4ss.Array).items()
                              if inspect.isfunction(obj) and not name.startswith("_")}
        self.assertEqual(mock_array_methods, real_array_methods)

        fixed_source = (ROOT / "src" / "PyFixedArray.cpp").read_text()
        real_fixed_methods = set(re.findall(r'\{"(\w+)",\s*[^{}]*?METH_', fixed_source))
        mock_fixed_methods = {name for name, obj in vars(ue4ss.FixedArray).items()
                              if inspect.isfunction(obj) and not name.startswith("_")}
        self.assertEqual(mock_fixed_methods, real_fixed_methods)

        mapset_source = (ROOT / "src" / "PyMapSet.cpp").read_text()
        for cls_name, table in (("Map", "s_map_methods"), ("Set", "s_set_methods")):
            block = mapset_source[mapset_source.index(f"PyMethodDef {table}[]"):]
            block = block[:block.index("};")]
            real = set(re.findall(r'\{"(\w+)",', block))
            mock = {name for name, obj in vars(getattr(ue4ss, cls_name)).items()
                    if (inspect.isfunction(obj) or name == "is_valid") and not name.startswith("_")}
            self.assertEqual(mock, real, cls_name)


class IdentifierAliasTest(unittest.TestCase):
    """The name a generated stub declares for an engine name with spaces works on the object."""

    def setUp(self):
        ue4ss._mock_reset_objects()
        self.cls = ue4ss._mock_define_class("/Game/Sky.Sky_C", "/Script/CoreUObject.Object")
        self.obj = ue4ss._mock_create_object("/Game/Maps.Map:PersistentLevel.Sky_1", "/Game/Sky.Sky_C")
        for name, kind in (("Sun height", "float"), ("Plain", "int32"), ("A b", "int32"), ("A_b", "int32"), ("from", "int32"), ("3D view", "int32")):
            ue4ss._mock_define_property(self.obj, name, kind)

        def impl(block):
            block.Result_value = block.First_value * 2
        ue4ss._mock_define_function("/Game/Sky.Sky_C", "Get Doubled", impl=impl, params=[("First value", "IntProperty", False, False), ("Result value", "IntProperty", True, False)])

    def tearDown(self):
        ue4ss._mock_reset_objects()

    def test_property_identifier_form_reads_and_writes(self):
        setattr(self.obj, "Sun height", 2.5)
        self.assertEqual(self.obj.Sun_height, 2.5)
        self.obj.Sun_height = 4.0
        self.assertEqual(getattr(self.obj, "Sun height"), 4.0)

    def test_keyword_and_leading_digit_forms(self):
        setattr(self.obj, "from", 7)
        self.assertEqual(self.obj.from_, 7)
        setattr(self.obj, "3D view", 9)
        self.assertEqual(self.obj._3D_view, 9)

    def test_exact_name_wins_and_ambiguous_forms_stay_unresolved(self):
        setattr(self.obj, "A b", 1)
        setattr(self.obj, "A_b", 2)
        self.assertEqual(self.obj.A_b, 2)                 # a member with exactly this name is not an alias target
        with self.assertRaises(AttributeError):
            self.obj.nothing_like_it

    def test_function_and_keyword_forms(self):
        self.assertEqual(self.obj.Get_Doubled(5), 10)
        self.assertEqual(self.obj.Get_Doubled(First_value=4), 8)
        with self.assertRaises(TypeError):
            self.obj.Get_Doubled(Firstvalue=4)

    def test_unknown_underscore_names_still_fail(self):
        with self.assertRaises(AttributeError):
            self.obj.Sun_hight


if __name__ == "__main__":
    unittest.main()
