import json
import sys
import socket
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python" / "mock"))
if "ue4ss" in sys.modules and not hasattr(sys.modules["ue4ss"], "_mock_reset"):
    sys.modules.pop("ue4ss")
import ue4ss  # noqa: E402
from ue4ss.runtime import bootstrap as bootstrap_module  # noqa: E402
from ue4ss.runtime.bootstrap import ManagedBootstrap, empty_manifest, load_manifest  # noqa: E402


@pytest.fixture(autouse=True)
def reset_ue4ss():
    ue4ss._mock_reset()
    yield
    ue4ss._mock_reset()


def on_game_thread(callback):
    results = []

    def run():
        try:
            results.append((True, callback()))
        except BaseException as error:
            results.append((False, error))

    ue4ss.execute_in_game_thread(run)
    ue4ss._mock_run_pending()
    assert results
    if not results[0][0]:
        raise results[0][1]
    return results[0][1]


def test_missing_manifest_means_no_components(tmp_path):
    assert load_manifest(tmp_path / "missing.json") == empty_manifest()


@pytest.mark.parametrize("manifest", [
    {"schema_version": 2, "startup_order": [], "components": {}},
    {"schema_version": 1, "startup_order": ["A"], "components": {}},
    {
        "schema_version": 1,
        "startup_order": ["A"],
        "components": {"A": {"enabled": "yes", "path": "components/A"}},
    },
    {
        "schema_version": 1,
        "startup_order": ["A"],
        "components": {"A": {"enabled": True, "path": "../outside"}},
    },
])
def test_invalid_manifest_is_rejected(tmp_path, manifest):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        load_manifest(path)


def test_startup_order_and_component_failure_isolation(tmp_path):
    components = tmp_path / "components"
    bad = components / "Bad"
    good = components / "Good"
    bad.mkdir(parents=True)
    good.mkdir(parents=True)
    (bad / "__init__.py").write_text("def start(ctx):\n    broken =\n", encoding="utf-8")
    (good / "__init__.py").write_text(
        "import builtins\n"
        "def start(ctx):\n"
        "    builtins._bootstrap_test_starts.append('Good')\n",
        encoding="utf-8",
    )
    builtins = __import__("builtins")
    builtins._bootstrap_test_starts = []
    manifest = {
        "schema_version": 1,
        "startup_order": ["Bad", "Disabled", "Good"],
        "components": {
            "Bad": {"enabled": True, "path": "components/Bad"},
            "Disabled": {"enabled": False, "path": "components/not-present"},
            "Good": {"enabled": True, "path": "components/Good"},
        },
    }
    bootstrap = ManagedBootstrap(tmp_path, manifest)
    on_game_thread(bootstrap.start_components)
    assert bootstrap.started == ["Good"]
    assert set(bootstrap.errors) == {"Bad"}
    assert bootstrap.runtime.manager.get("Good").state == "running"
    assert builtins._bootstrap_test_starts == ["Good"]
    on_game_thread(bootstrap.runtime.manager.stop_all)
    del builtins._bootstrap_test_starts


def test_stable_entry_resolves_local_manifest_and_starts_enabled_component(tmp_path):
    scripts = tmp_path / "python_scripts"
    scripts.mkdir()
    main = scripts / "main.py"
    main.write_text("# generated bootstrap\n", encoding="utf-8")
    root = tmp_path / "managed_runtime"
    package = root / "components" / "Demo"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "import builtins\n"
        "def start(ctx):\n"
        "    builtins._bootstrap_stable_entry.append('Demo')\n",
        encoding="utf-8",
    )
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "startup_order": ["Demo"],
        "components": {
            "Demo": {
                "enabled": True,
                "path": "components/Demo",
                "config": {"sample": "value"},
            },
        },
    }), encoding="utf-8")
    builtins = __import__("builtins")
    builtins._bootstrap_stable_entry = []
    instance = on_game_thread(lambda: bootstrap_module.start(main))
    assert instance.started == ["Demo"]
    assert builtins._bootstrap_stable_entry == ["Demo"]
    assert instance.runtime.manager.get("Demo").config == {"sample": "value"}
    on_game_thread(instance.runtime.manager.stop_all)
    bootstrap_module._instances.pop(str(root.resolve()), None)
    del builtins._bootstrap_stable_entry


def test_managed_live_file_component_uses_owner_scheduled_heartbeat(tmp_path):
    from ue4ss.live.component import start as start_live

    inbox = tmp_path / "session" / "inbox"
    outbox = tmp_path / "session" / "outbox"
    heartbeat = tmp_path / "session" / "alive"

    class Context:
        config = {
            "transport": "file",
            "file": {
                "inbox": str(inbox),
                "outbox": str(outbox),
                "heartbeat": str(heartbeat),
                "poll_ms": 250,
            },
        }

        def __init__(self):
            self.callbacks = []

        def execute_with_delay(self, milliseconds, callback):
            self.callbacks.append(callback)

        def register_cleanup(self, callback, *, name=None):
            return callback

    context = Context()
    on_game_thread(lambda: start_live(context))
    assert len(context.callbacks) == 1
    poll = context.callbacks.pop()
    on_game_thread(poll)
    assert heartbeat.is_file()
    assert len(context.callbacks) == 1


def test_managed_live_socket_component_uses_a_private_token_and_owned_worker(tmp_path):
    from ue4ss.live.component import start as start_live
    from ue4ss.live.security import provision_token

    token_file = tmp_path / "private" / "live.token"
    provision_token(token_file)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    class Context:
        config = {
            "transport": "socket",
            "socket": {
                "host": "127.0.0.1",
                "port": port,
                "token_file": str(token_file),
            },
        }

        def __init__(self):
            self.cleanups = []
            self.workers = []
            self.callbacks = []

        def register_cleanup(self, callback, *, name=None):
            self.cleanups.append((name, callback))

        def spawn_worker(self, target, *, name=None):
            self.workers.append((name, target))
            stopped = threading.Event()
            stopped.set()
            target(stopped)

        def execute_with_delay(self, milliseconds, callback):
            self.callbacks.append((milliseconds, callback))

    context = Context()
    on_game_thread(lambda: start_live(context))
    assert len(context.workers) == 1
    assert context.workers[0][0] == "ue4ss:live-socket"
    assert context.live_socket_service.bound_address[1] == port
    assert context.live_socket_service.error is None
    assert [name for name, _callback in context.cleanups] == [
        "stop live execution scope",
        "stop live socket listener",
    ]
    on_game_thread(lambda: [callback() for _name, callback in reversed(context.cleanups)])
