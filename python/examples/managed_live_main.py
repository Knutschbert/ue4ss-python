"""Verify the packaged managed live component and its file transport in-game."""

import json
import tempfile
from pathlib import Path

import ue4ss
from ue4ss.live.protocol import make_request
from ue4ss.runtime import ScriptRuntime


def check(label, condition, detail=""):
    ok = bool(condition)
    print(f"{label}: {'PASS' if ok else 'FAIL'}{f' ({detail})' if detail else ''}")
    return ok


temporary = tempfile.TemporaryDirectory(prefix="ue4ss-managed-live-")
root = Path(temporary.name)
package = root / "components" / "LiveBridge"
package.mkdir(parents=True)
(package / "__init__.py").write_text(
    "from ue4ss.live.component import start\n",
    encoding="utf-8",
)
session = root / "session"
inbox, outbox, heartbeat = session / "inbox", session / "outbox", session / "alive"
runtime = ScriptRuntime(roots=(root,))
context = runtime.load_local(
    "ManagedLive",
    package,
    config={
        "transport": "file",
        "file": {
            "inbox": str(inbox),
            "outbox": str(outbox),
            "heartbeat": str(heartbeat),
            "poll_ms": 100,
        },
    },
)
print("managed live component started; waiting for its first file poll")


def finish():
    results = []
    try:
        status_request = make_request("status", request_id="managed-live-check")
        response = context.live_core.dispatch(status_request)
        results.append(check("managed context retained its script runtime", context.script_runtime is runtime))
        results.append(check("managed component shares the normal live core", response["status"] == "ok"))
        results.append(check(
            "file transport wrote its heartbeat",
            heartbeat.is_file(),
            "missing heartbeat indicates no managed poll",
        ))
    except Exception as error:
        results.append(check("managed live integration", False, f"{type(error).__name__}: {error}"))
    finally:
        runtime.manager.stop("ManagedLive")
        runtime._refresh()
        temporary.cleanup()
    print(f"managed live integration finished ({sum(results)}/{len(results)} checks passed)")


ue4ss.execute_with_delay(350, finish)
