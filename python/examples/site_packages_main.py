"""Verify imports from the mod-local site-packages directory."""

import importlib
import shutil
import sys
from pathlib import Path


def check(label, condition, detail=""):
    ok = bool(condition)
    print(f"{label}: {'PASS' if ok else 'FAIL'}{f' ({detail})' if detail else ''}")
    return ok


root = Path(__file__).resolve().parents[1]
site = root / "site-packages"
package = site / "_ue4ss_site_packages_test"
package.mkdir(parents=True, exist_ok=True)
(package / "__init__.py").write_text("value = 'local-site-packages'\n", encoding="utf-8")
try:
    importlib.invalidate_caches()
    imported = importlib.import_module("_ue4ss_site_packages_test")
    normalized_site = site.resolve()
    on_path = any(
        isinstance(entry, str) and Path(entry).resolve() == normalized_site
        for entry in sys.path
    )
    check("site-packages directory is on sys.path", on_path, site)
    check("pure-Python package imports from site-packages", imported.value == "local-site-packages")
    check("module origin is in site-packages", Path(imported.__file__).resolve().is_relative_to(site.resolve()))
finally:
    sys.modules.pop("_ue4ss_site_packages_test", None)
    shutil.rmtree(package, ignore_errors=True)
    print("site-packages import test finished")
