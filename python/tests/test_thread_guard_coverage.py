"""python/examples/thread_guard_main.py calls every entry point from a worker thread. A new public function or method of the
ue4ss module must appear in it, so that the game-thread rule is checked for it too (ticket ft9.4)."""
import inspect
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python" / "mock"))
import ue4ss  # noqa: E402

SOURCE = (ROOT / "python" / "examples" / "thread_guard_main.py").read_text(encoding="utf-8")

# Not Unreal-touching and not worth a worker-thread call: internal self-test helpers, and the constants.
EXEMPT_FUNCTIONS = {"_selftest_raise", "_selftest_fstring", "_selftest_objects", "_selftest_thread_probe", "_selftest_poison_pointer", "_selftest_call_with_block",
                    "_selftest_faults_survived", "_selftest_struct_stats", "_selftest_fire_key"}
# reload() restarts the script under test (covered by reload_main.py); it is thread safe by construction (it only queues a task).
EXEMPT_FUNCTIONS.add("reload")
# Methods that only exist to satisfy Python protocols or are covered through the operation that uses them.
EXEMPT_METHODS = {"update"}  # Map.update / Set.update are covered as "Map update"; the word is generic


def mentioned(name: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", SOURCE) is not None


class ThreadGuardCoverageTest(unittest.TestCase):
    def test_every_public_function_is_exercised_off_the_game_thread(self):
        for name, obj in vars(ue4ss).items():
            if inspect.isfunction(obj) and obj.__module__ == "ue4ss" and not name.startswith("_") and name not in EXEMPT_FUNCTIONS:
                with self.subTest(function=name):
                    self.assertTrue(mentioned(name), f"thread_guard_main.py does not call ue4ss.{name}() from a worker thread")

    def test_every_public_method_is_exercised_off_the_game_thread(self):
        for cls_name, cls in vars(ue4ss).items():
            if not (inspect.isclass(cls) and cls.__module__ == "ue4ss") or cls_name.startswith("_") or cls_name in {"UEError", "InvalidObjectError", "ObjectFlags", "NullType", "CallOriginalType"}:
                continue
            for attr, value in vars(cls).items():
                if attr.startswith("_") or not inspect.isfunction(value) or attr in EXEMPT_METHODS:
                    continue
                with self.subTest(cls=cls_name, method=attr):
                    self.assertTrue(mentioned(attr), f"thread_guard_main.py does not call {cls_name}.{attr}() from a worker thread")


if __name__ == "__main__":
    unittest.main()
