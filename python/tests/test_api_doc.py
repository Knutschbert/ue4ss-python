"""docs/API.md must mention every public name of the ue4ss module, so the document cannot drift from the API."""
import inspect
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python" / "mock"))
import ue4ss  # noqa: E402  (the mock mirrors the real module; test_mock_ue4ss.py keeps them in step)

DOC = (ROOT / "docs" / "API.md").read_text()


def mentioned(name: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", DOC) is not None


def is_public(name: str) -> bool:
    return not name.startswith("_") or name.startswith("_selftest_")


class ApiDocTest(unittest.TestCase):
    def test_every_public_function_is_documented(self):
        for name, obj in vars(ue4ss).items():
            if inspect.isfunction(obj) and obj.__module__ == "ue4ss" and is_public(name):
                with self.subTest(function=name):
                    self.assertTrue(mentioned(name), f"docs/API.md does not mention ue4ss.{name}()")

    def test_every_public_class_and_its_methods_are_documented(self):
        for name, cls in vars(ue4ss).items():
            if not (inspect.isclass(cls) and cls.__module__ == "ue4ss") or name.startswith("_"):
                continue
            with self.subTest(cls=name):
                self.assertTrue(mentioned(name), f"docs/API.md does not mention ue4ss.{name}")
            for attr, value in vars(cls).items():
                if attr.startswith("_") or not (inspect.isfunction(value) or isinstance(value, (property, classmethod))):
                    continue
                with self.subTest(cls=name, member=attr):
                    self.assertTrue(mentioned(attr), f"docs/API.md does not mention {name}.{attr}")

    def test_constants_are_documented(self):
        for name in ("NULL", "__version__"):
            with self.subTest(constant=name):
                self.assertTrue(mentioned(name))


if __name__ == "__main__":
    unittest.main()
