"""Exercise the stable runtime bootstrap and manifest against the real engine."""

import builtins
import json
import tempfile
from pathlib import Path

import ue4ss
from ue4ss.runtime import bootstrap


def check(label, condition, detail=""):
    ok = bool(condition)
    print(f"{label}: {'PASS' if ok else 'FAIL'}{f' ({detail})' if detail else ''}")
    return ok


builtins._managed_bootstrap_test_events = []
with tempfile.TemporaryDirectory(prefix="ue4ss-managed-bootstrap-") as temporary:
    mod = Path(temporary)
    scripts = mod / "python_scripts"
    scripts.mkdir()
    main = scripts / "main.py"
    main.write_text("# temporary bootstrap location\n", encoding="utf-8")
    root = mod / "managed_runtime"
    components = root / "components"

    def package(name, source):
        directory = components / name
        directory.mkdir(parents=True)
        (directory / "__init__.py").write_text(source, encoding="utf-8")

    package(
        "First",
        "import builtins\n"
        "def start(ctx): builtins._managed_bootstrap_test_events.append('First')\n",
    )
    package("Broken", "def start(ctx):\n    incomplete =\n")
    package(
        "Last",
        "import builtins\n"
        "def start(ctx): builtins._managed_bootstrap_test_events.append('Last')\n",
    )
    (components / "Disabled").mkdir()
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "startup_order": ["First", "Broken", "Disabled", "Last"],
        "components": {
            name: {"enabled": enabled, "path": f"components/{name}", "config": {}}
            for name, enabled in (
                ("First", True),
                ("Broken", True),
                ("Disabled", False),
                ("Last", True),
            )
        },
    }), encoding="utf-8")

    instance = bootstrap.start(main)
    check(
        "enabled components start in declared order despite a sibling failure",
        instance.started == ["First", "Last"]
        and builtins._managed_bootstrap_test_events == ["First", "Last"],
        (instance.started, instance.errors.keys(), builtins._managed_bootstrap_test_events),
    )
    check("failed component is isolated and reported", set(instance.errors) == {"Broken"})
    check("disabled component is not started", "Disabled" not in instance.started)
    instance.runtime.manager.stop_all()
    instance.runtime._refresh()
    bootstrap._instances.pop(str(root.resolve()), None)

del builtins._managed_bootstrap_test_events
print("managed bootstrap finished")
