import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deploy_script  # noqa: E402


class DeployScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"
        (self.root / "dist" / "PythonScripting" / "python_scripts").mkdir(parents=True)
        self.source = Path(self.tmp.name) / "my test script.py"
        self.source.write_text("print('hello')\n", encoding="utf-8")

    def test_copies_to_installed_packager_and_kept_copy(self):
        stamp = time.strptime("2026-10-24 09:08:07", "%Y-%m-%d %H:%M:%S")
        written = deploy_script.deploy(self.source, "try this", self.root, stamp)
        for key in ("installed", "packager", "kept"):
            self.assertEqual(written[key].read_text(encoding="utf-8"), "print('hello')\n", key)
        self.assertEqual(written["installed"], self.root / "dist" / "PythonScripting" / "python_scripts" / "main.py")
        self.assertEqual(written["packager"], self.root / "python" / "python_scripts" / "main.py")
        self.assertEqual(written["kept"].name, "try_this_20261024-090807.py")
        self.assertEqual(written["kept"].parent, self.root / "scripts" / "user")

    def test_no_name_keeps_no_copy(self):
        written = deploy_script.deploy(self.source, "  ", self.root)
        self.assertIsNone(written["kept"])
        self.assertFalse((self.root / "scripts").exists())
        self.assertTrue(written["installed"].is_file())

    def test_a_syntax_error_copies_nothing(self):
        self.source.write_text("def broken(:\n", encoding="utf-8")
        with self.assertRaises(SyntaxError):
            deploy_script.deploy(self.source, "x", self.root)
        self.assertFalse((self.root / "python").exists())
        self.assertFalse((self.root / "scripts").exists())
        self.assertEqual(list((self.root / "dist" / "PythonScripting" / "python_scripts").iterdir()), [])

    def test_missing_package_or_file_is_a_clear_error(self):
        with self.assertRaises(FileNotFoundError):
            deploy_script.deploy(self.root / "nope.py", "", self.root)
        with self.assertRaises(FileNotFoundError):
            deploy_script.deploy(self.source, "", self.root / "elsewhere")

    def test_deploying_the_installed_file_to_itself_is_fine(self):
        installed = self.root / "dist" / "PythonScripting" / "python_scripts" / "main.py"
        installed.write_text("x = 1\n", encoding="utf-8")
        written = deploy_script.deploy(installed, "again", self.root)
        self.assertEqual(written["packager"].read_text(encoding="utf-8"), "x = 1\n")

    def test_names_are_made_safe(self):
        self.assertEqual(deploy_script.safe_name("a/b\\c: d?"), "a_b_c_d")
        self.assertEqual(deploy_script.safe_name("***"), "script")

    def test_main_reports_a_syntax_error_with_the_line(self):
        self.source.write_text("x = 1\ndef broken(:\n", encoding="utf-8")
        code = deploy_script.main([str(self.source)])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
