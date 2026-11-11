"""The scripts in examples/ run against the off-game module: in a level without a player (every script must cope) and with a player
controller and pawn (the key toggle and the hook are exercised)."""
import contextlib
import io
import runpy
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python" / "mock"))
import ue4ss  # noqa: E402
from ue4ss import engine  # noqa: E402

EXAMPLES = sorted((ROOT / "examples").glob("[0-9][0-9]_*.py"))
OBJECT, ACTOR, PAWN = "/Script/CoreUObject.Object", "/Script/Engine.Actor", "/Script/Engine.Pawn"
CONTROLLER, PLAYER = "/Script/Engine.Controller", "/Script/Engine.PlayerController"
VECTOR = "/Script/CoreUObject.Vector"


def build_engine():
    """The few engine classes the examples name; no instances."""
    ue4ss._mock_reset()
    ue4ss._mock_reset_objects()
    engine._classes.clear()
    engine._classes[OBJECT] = engine.EngineObject
    engine._enums.clear()
    ue4ss._mock_define_class(ACTOR, OBJECT, fields=["bHidden"])
    ue4ss._mock_define_class(PAWN, ACTOR)
    ue4ss._mock_define_class(CONTROLLER, ACTOR, fields=["Pawn"])
    ue4ss._mock_define_class(PLAYER, CONTROLLER)
    ue4ss._mock_define_class("/Script/Engine.ActorComponent", OBJECT)
    ue4ss._mock_define_struct_type(VECTOR, types={"X": ("float", 0.0), "Y": ("float", 0.0), "Z": ("float", 0.0)})
    ue4ss._mock_define_struct_type("/Script/Engine.HitResult", types={"bBlockingHit": ("bool", False)})

    def hide(block):                # stands in for the engine: the pawn of add_player takes the new value
        ue4ss._mock_define_property(hide.target, "bHidden", "bool", bool(block.bNewHidden))

    ue4ss._mock_define_function(ACTOR, "SetActorHiddenInGame", flags=0x04000400, impl=hide, params=[("bNewHidden", "BoolProperty", False, False)])
    ue4ss._mock_define_function(ACTOR, "K2_SetActorLocation", flags=0x04020400, impl=lambda block: setattr(block, "ReturnValue", True), params=[
        {"name": "NewLocation", "type": "StructProperty", "is_out": False, "is_return": False, "mock_type": "struct:" + VECTOR},
        ("bSweep", "BoolProperty", False, False),
        {"name": "SweepHitResult", "type": "StructProperty", "is_out": True, "is_return": False, "mock_type": "struct:/Script/Engine.HitResult"},
        ("bTeleport", "BoolProperty", False, False),
        ("ReturnValue", "BoolProperty", True, True)])
    return hide


def add_player(hide):
    pawn = ue4ss._mock_create_object("/Game/Map.Map:Pawn_0", PAWN)
    ue4ss._mock_define_property(pawn, "bHidden", "bool", False)
    controller = ue4ss._mock_create_object("/Game/Map.Map:PlayerController_0", PLAYER)
    ue4ss._mock_define_property(controller, "Pawn", "object:" + PAWN, pawn)
    hide.target = pawn
    return controller, pawn


def run(path):
    """Run one example as main.py would run; returns what it printed."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), mock.patch("threading.Thread.start"):     # 06 starts a worker: not in a unit test
        runpy.run_path(str(path), run_name="__main__")
    return out.getvalue()


class UserExamplesTest(unittest.TestCase):
    def tearDown(self):
        ue4ss._mock_reset()
        ue4ss._mock_reset_objects()

    def test_there_are_examples_and_the_index_lists_each(self):
        self.assertGreaterEqual(len(EXAMPLES), 5)
        index = (ROOT / "examples" / "README.md").read_text(encoding="utf-8")
        for path in EXAMPLES:
            self.assertIn(path.name, index)
            self.assertTrue(path.read_text(encoding="utf-8").startswith("# "), f"{path.name} starts with a comment that says what it shows")

    def test_every_example_runs_in_a_level_without_a_player(self):
        for path in EXAMPLES:
            with self.subTest(path.name):
                build_engine()
                self.assertTrue(run(path).strip(), "an example prints something")

    def test_every_example_runs_with_a_player(self):
        for path in EXAMPLES:
            if path.name.startswith("07"):
                continue        # reads the pawn's location: needs engine functions this small world does not have
            with self.subTest(path.name):
                add_player(build_engine())
                run(path)

    def test_the_key_toggle_hides_and_shows_the_pawn(self):
        _, pawn = add_player(build_engine())
        run(ROOT / "examples" / "03_key_toggle.py")
        ue4ss._selftest_fire_key(ue4ss.Key.F6)
        self.assertTrue(pawn.bHidden)
        ue4ss._selftest_fire_key(ue4ss.Key.F6)
        self.assertFalse(pawn.bHidden)

    def test_the_hook_sees_a_call_and_can_be_removed(self):
        _, pawn = add_player(build_engine())
        out = io.StringIO()
        run(ROOT / "examples" / "04_hook_function.py")
        with contextlib.redirect_stdout(out):
            pawn.K2_SetActorLocation({"X": 1, "Y": 2, "Z": 3}, False, False)
            ue4ss._selftest_fire_key(ue4ss.Key.F7)
            pawn.K2_SetActorLocation({"X": 4, "Y": 5, "Z": 6}, False, False)
        text = out.getvalue()
        self.assertIn("moves to (1, 2, 3)", text)
        self.assertIn("hook removed after 1 calls", text)
        self.assertNotIn("(4, 5, 6)", text)


if __name__ == "__main__":
    unittest.main()
