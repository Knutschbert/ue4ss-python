import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gen_base_stubs  # noqa: E402


class ShippedStubsTest(unittest.TestCase):
    def test_the_committed_stubs_are_what_the_tool_writes(self):
        for path, text in gen_base_stubs.files().items():
            self.assertTrue(path.exists(), f"{path.name} is missing: run python3 -I tools/gen_base_stubs.py")
            self.assertEqual(path.read_text(encoding="utf-8"), text, f"{path} is out of date: run python3 -I tools/gen_base_stubs.py")

    def test_the_stubs_parse_and_declare_the_documented_functions(self):
        produced = gen_base_stubs.files()
        base = produced[gen_base_stubs.OUT / "__init__.pyi"]
        names = {node.name for node in ast.parse(base).body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
        for name in ("find_first_of", "find_all_of", "static_find_object", "register_hook", "bind_event", "register_key_down", "execute_in_game_thread", "reload", "UObject", "Key", "UEError"):
            self.assertIn(name, names)
        ast.parse(produced[gen_base_stubs.OUT / "engine" / "__init__.pyi"])

    def test_python_functions_without_a_docstring_signature_keep_their_parameter_names(self):
        base = gen_base_stubs.files()[gen_base_stubs.OUT / "__init__.pyi"]
        line = next(l for l in base.splitlines() if l.startswith("def register_hook("))
        self.assertNotIn("*args", line)
        self.assertIn("path", line)


    def test_well_known_parameters_and_results_have_concrete_types(self):
        base = gen_base_stubs.files()[gen_base_stubs.OUT / "__init__.pyi"]
        lines = {l.split("(")[0][4:]: l for l in base.splitlines() if l.startswith("def ")}
        self.assertEqual(lines["find_first_of"], "def find_first_of(class_name: str) -> UObject | None: ...")
        self.assertEqual(lines["register_hook"], "def register_hook(path: str, pre: Callable[..., Any] | None = ..., post: Callable[..., Any] | None = ..., instance: UObject | None = ...) -> tuple[int, int]: ...")
        self.assertEqual(lines["execute_with_delay"], "def execute_with_delay(milliseconds: float, fn: Callable[..., Any]) -> None: ...")
        self.assertIn("-> Future[Any]", lines["call_in_game_thread"])
        self.assertIn("key: Key | int", lines["register_key_down"])
        self.assertTrue(lines["register_key_down"].endswith("-> int: ..."))
        self.assertTrue(lines["is_game_thread"].endswith("-> bool: ..."))


if __name__ == "__main__":
    unittest.main()
