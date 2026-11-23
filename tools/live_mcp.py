"""Minimal stdio MCP adapter for a running live-game bridge.

Exposes the operations of a running bridge (tools/live.py install, then start the game) as MCP tools over stdio, so an
AI assistant can run Python in the game. It does not start a game: without a live session every tool reports that.
The session folder is PYUE_LIVE_DIR, default dist/PythonScripting/live.
"""

from __future__ import annotations

import json
import argparse
import sys
from typing import Any

from live_client import LiveClient, LiveClientError, client_from_environment


INSTRUCTIONS = (
    "Runs Python inside a running Unreal Engine game through the ue4ss-python mod. This server never starts, stops or installs "
    "into a game: the user configures/installs the bridge and starts the game themselves. File IPC is the default; an explicitly "
    "configured TCP endpoint uses PYUE_LIVE_ENDPOINT with a separate restricted PYUE_LIVE_TOKEN_FILE. Call live_status first; "
    "when no bridge is alive, tell the user what it says instead of retrying. All code runs on the game thread in one "
    "persistent session (variables stay between calls, `ue4ss` is imported): keep each call short, use live_job_start for "
    "work that spans frames and live_wait_until / live_wait_ready to wait, and never sleep or loop for long inside "
    "live_execute, which freezes the game. Assign to `result` to return a value. Managed-script tools require managed bootstrap; "
    "live_script_load transmits one bounded UTF-8 source file and is session-only."
)

_NO_ARGUMENTS = {"type": "object", "properties": {}}
TOOLS = [
    {"name": "live_status",
     "description": "Whether the bridge in the game is alive, and if so the current level, the session generation and the jobs. "
                    "When it is not alive, says why (game not running, or the bridge is not installed) and what the user has to do. Call this first.",
     "inputSchema": _NO_ARGUMENTS},
    {"name": "live_execute",
     "description": "Run a Python block on the game thread in the persistent session and return what it printed, the value of the variable "
                    "`result`, and any traceback. Blocks the game while it runs: keep it short. Limits: 128 KiB of code, 64 KiB of output.",
     "inputSchema": {"type": "object", "required": ["code"], "properties": {
         "code": {"type": "string", "description": "Python source. `ue4ss` is already imported; assign to `result` to return a value."},
         "timeout": {"type": "number", "description": "Seconds to wait for the game to answer (default 30, at most 600)."}}}},
    {"name": "live_reset",
     "description": "Forget the variables of the session (after running every registered cleanup) and start a new generation.",
     "inputSchema": _NO_ARGUMENTS},
    {"name": "live_job_start",
     "description": "Queue a Python block to run at a later engine tick and return a job id at once. For work that should not hold up the "
                    "request; read the outcome with live_job_status.",
     "inputSchema": {"type": "object", "required": ["code"], "properties": {"code": {"type": "string"}}}},
    {"name": "live_job_status",
     "description": "The state of a job (queued, running, completed, failed), with its output, `result` and error.",
     "inputSchema": {"type": "object", "required": ["job_id"], "properties": {"job_id": {"type": "string"}}}},
    {"name": "live_cleanup",
     "description": "Run the cleanup callbacks that session code registered under a group name (all groups when none is given), "
                    "for example to remove actors or hooks an experiment made.",
     "inputSchema": {"type": "object", "properties": {"group": {"type": "string"}}}},
    {"name": "live_wait_until",
     "description": "Wait until a Python expression becomes true or the timeout passes. The expression is evaluated in the session at "
                    "every engine tick without blocking the game (for example a variable that a job or a hook sets). Without `check` it "
                    "waits for the whole timeout.",
     "inputSchema": {"type": "object", "required": ["timeout"], "properties": {
         "check": {"type": "string", "description": "A Python expression, evaluated in the session scope."},
         "timeout": {"type": "number", "description": "Seconds (at most 600)."}}}},
    {"name": "live_wait_ready",
     "description": "Wait until the game is ready for work: a world exists and, unless require_pawn is false, a player controller that "
                    "has a pawn. Use it after the user loaded a level or after live_travel. On timeout it says what is still missing.",
     "inputSchema": {"type": "object", "properties": {
         "timeout": {"type": "number", "description": "Seconds (default 60, at most 600)."},
         "require_pawn": {"type": "boolean", "description": "Also wait for a player controller with a pawn (default true). False suits menus."}}}},
    {"name": "live_sleep",
     "description": "Wait a fixed number of seconds without blocking the game.",
     "inputSchema": {"type": "object", "required": ["seconds"], "properties": {"seconds": {"type": "number"}}}},
    {"name": "live_travel",
     "description": "Open another level inside the game that is already running (the engine's OpenLevel). It does not start a game. "
                    "Level paths are specific to the game; follow it with live_wait_ready.",
     "inputSchema": {"type": "object", "required": ["map"], "properties": {
         "map": {"type": "string", "description": "A level path or name the game knows, for example /Game/Maps/MyLevel."},
         "timeout": {"type": "number"}}}},
    {"name": "live_reload",
     "description": "Run the mod's main.py again in the game (ue4ss.reload). The bridge is that main.py, so it restarts with an empty session.",
     "inputSchema": _NO_ARGUMENTS},
    {"name": "live_stop",
     "description": "End the bridge inside the game. The game keeps running; afterwards no live_* tool works until the user reloads the script "
                    "or restarts the game.",
      "inputSchema": _NO_ARGUMENTS},
    {"name": "live_script_list",
     "description": "List managed scripts with lifecycle state, generation and cleanup/worker diagnostics.",
     "inputSchema": _NO_ARGUMENTS},
    {"name": "live_script_start",
     "description": "Start a previously loaded managed script as a fresh instance.",
     "inputSchema": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}},
    {"name": "live_script_stop",
     "description": "Stop and clean up only the named managed script; sibling scripts keep running.",
     "inputSchema": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}},
    {"name": "live_script_load",
     "description": "Transmit one bounded UTF-8 source file as an ephemeral session-only managed script. No remote file or startup manifest is written.",
     "inputSchema": {"type": "object", "required": ["name", "source"], "properties": {
         "name": {"type": "string"}, "source": {"type": "string", "description": "One Python file, at most 128 KiB."}}}},
    {"name": "live_script_reload",
     "description": "Reload only the named managed script; optionally send replacement single-file source.",
     "inputSchema": {"type": "object", "required": ["name"], "properties": {
         "name": {"type": "string"}, "source": {"type": "string", "description": "Optional replacement source, at most 128 KiB."}}}},
    {"name": "live_outcome",
     "description": "Look up a previously submitted request by ID without replaying it. Returns unknown if no retained outcome is available.",
     "inputSchema": {"type": "object", "required": ["request_id"], "properties": {"request_id": {"type": "string"}}}},
]

BRIDGE_MARK = "# generated by tools.live_bridge"
MANAGED_BOOTSTRAP_MARK = "# generated by tools.managed_runtime"
READY_CHECK = ("ue4ss.find_first_of('World') is not None and "
               "(not {require_pawn} or getattr(ue4ss.find_first_of('PlayerController'), 'Pawn', None) is not None)")
READY_REPORT = ("_c = ue4ss.find_first_of('PlayerController')\n"
                "result = {'world': ue4ss.find_first_of('World') is not None, 'player_controller': _c is not None, "
                "'pawn': getattr(_c, 'Pawn', None) is not None}\ndel _c")


def _result(value: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=repr)}], "isError": False}


def _error(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _source_text(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 128 * 1024:
        raise LiveClientError("managed source must be UTF-8 text no larger than 128 KiB")
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError as error:
        raise LiveClientError("managed source must be valid UTF-8 text") from error
    if size > 128 * 1024:
        raise LiveClientError("managed source must be UTF-8 text no larger than 128 KiB")
    return value


def bridge_state(client: LiveClient) -> tuple[str, str]:
    """('alive' | 'not_running' | 'not_configured' | 'not_installed', next action). Never waits."""
    if getattr(client, "is_socket", False):
        try:
            client.ping(timeout=min(client.connect_timeout, 2.0))
            return "alive", ""
        except LiveClientError as error:
            return "unavailable", (
                f"TCP endpoint {client.endpoint} is unavailable: {error}. Check the loopback listener, SSH tunnel, "
                "and restricted token file. This server cannot inspect the remote game filesystem."
            )
    if client.is_alive():
        return "alive", ""
    main_py = client.root.parent / "python_scripts" / "main.py"
    try:
        main_source = main_py.read_text(encoding="utf-8", errors="replace")
    except OSError:
        main_source = ""
    if main_source.startswith(BRIDGE_MARK):
        installation = "legacy"
    elif main_source.startswith(MANAGED_BOOTSTRAP_MARK):
        manifest = client.root.parent / "managed_runtime" / "manifest.json"
        try:
            configuration = json.loads(manifest.read_text(encoding="utf-8"))
            live = configuration["components"]["LiveBridge"]
            file_transport = live.get("config", {}).get("transport", "file") in ("file", "both")
            installation = "managed" if live.get("enabled") and file_transport else "managed_unconfigured"
        except (OSError, ValueError, KeyError, TypeError):
            installation = "managed_unconfigured"
    else:
        installation = None
    if installation is None:
        return "not_installed", ("The bridge is not installed: the user has to run `python tools/live.py install` in the ue4ss-python "
                                  "repository and then start the game (or reload the script in a running game). This server does not do that.")
    if installation == "managed_unconfigured":
        return "not_configured", (
            "The managed bootstrap is installed but its file LiveBridge component is not enabled. The user has to run "
            "`python tools/live.py install --managed` (or enable managed LiveBridge with file transport), then start or "
            "reload the game. This server does not change game files."
        )
    if client.heartbeat.exists():
        return "not_running", ("The bridge is installed but its heartbeat is stale: the game is not running, was closed, or the bridge was "
                               "stopped. The user has to start the game (or reload the script in it). This server does not start games.")
    return "not_running", ("The bridge is installed but has never run: the user has to start the game (or reload the script in a running "
                           "game). This server does not start games.")


def _wait_ready(client: LiveClient, arguments: dict[str, Any]) -> dict[str, Any]:
    timeout = float(arguments.get("timeout", 60))
    require_pawn = bool(arguments.get("require_pawn", True))
    try:
        client.request("wait_until", code=READY_CHECK.format(require_pawn=require_pawn), timeout=timeout)
    except LiveClientError as error:
        try:
            seen = client.request("execute", code=READY_REPORT, timeout=10.0).get("result") or {}
        except LiveClientError:
            return _error(f"not ready after {timeout:.0f} s, and the game did not answer afterwards: {error}")
        missing = [name for name in (("world", "player_controller", "pawn") if require_pawn else ("world",)) if not seen.get(name)]
        if not missing:
            return _result({"ready": True, "note": "became ready just after the timeout", **seen})
        return _error(f"not ready after {timeout:.0f} s: missing " + ", ".join(missing).replace("_", " ")
                      + ". The user may still be in a menu or a loading screen" + ("; pass require_pawn=false to accept a level without a player pawn." if "world" not in missing else "."))
    status = client.request("status", timeout=10.0).get("result") or {}
    return _result({"ready": True, "level": status.get("level"), "generation": status.get("generation")})


def handle(message: dict[str, Any], client: LiveClient) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                                                              "serverInfo": {"name": "ue4ss-live", "version": "0.2"}, "instructions": INSTRUCTIONS}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method != "tools/call":
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"method not found: {method}"}}
    params = message.get("params") or {}
    name = params.get("name")
    arguments = params.get("arguments") or {}
    try:
        if name not in {tool["name"] for tool in TOOLS}:
            raise LiveClientError(f"unknown tool: {name}")
        state, advice = bridge_state(client)          # before anything else: without a bridge a request would only time out
        if name == "live_status":
            value = {"alive": state == "alive", "state": state}
            if getattr(client, "is_socket", False):
                value["endpoint"] = client.endpoint
                if state == "alive":
                    worker = client.ping(timeout=min(client.connect_timeout, 2.0))
                    value["transport"] = {
                        "kind": "tcp",
                        "reachable": True,
                        "instance_id": worker.get("instance_id"),
                    }
            else:
                value["directory"] = str(client.root)
            if state == "alive":
                try:
                    status = client.request("status", timeout=10.0).get("result") or {}
                    value.update(status)
                    value["game_thread"] = {"responsive": bool(status.get("game_thread", True))}
                    value["readiness"] = {
                        "world_available": status.get("level") is not None,
                        "player_pawn": "unknown",
                    }
                except LiveClientError as error:
                    if not getattr(client, "is_socket", False):
                        raise
                    value["game_thread"] = {"responsive": False, "diagnostic": str(error)}
                    value["readiness"] = {"state": "unknown", "diagnostic": "game thread did not answer status"}
            else:
                value["what_to_do"] = advice
            response = _result(value)
        elif name == "live_outcome":
            if state == "not_installed":
                response = _error(advice)
            else:
                response = _result(client.outcome(arguments.get("request_id", "")))
        elif state != "alive":
            response = _error(advice)
        elif name == "live_wait_ready":
            response = _wait_ready(client, arguments)
        else:
            if name == "live_execute":
                value = client.request("execute", code=arguments.get("code", ""), timeout=float(arguments.get("timeout", 30)))
            elif name == "live_reset":
                value = client.request("reset")
            elif name == "live_job_start":
                value = client.request("job_start", code=arguments.get("code", ""))
            elif name == "live_job_status":
                value = client.request("job_status", args={"job_id": arguments.get("job_id", "")})
            elif name == "live_cleanup":
                value = client.request("cleanup", args={"group": arguments.get("group", "*")})
            elif name == "live_wait_until":
                value = client.request("wait_until", code=arguments.get("check", ""), timeout=float(arguments.get("timeout", 30)))
            elif name == "live_sleep":
                value = client.request("wait_until", code="", timeout=float(arguments.get("seconds", 1)))
            elif name == "live_travel":
                value = client.request("travel", args={"map": arguments.get("map", "")}, timeout=float(arguments.get("timeout", 60)))
            elif name == "live_reload":
                value = client.request("reload")
            elif name == "live_stop":
                value = client.request("stop")
            elif name == "live_script_list":
                value = client.request("script_list")
            elif name == "live_script_start":
                value = client.request("script_start", args={"name": arguments.get("name", "")})
            elif name == "live_script_stop":
                value = client.request("script_stop", args={"name": arguments.get("name", "")})
            elif name == "live_script_load":
                source = _source_text(arguments.get("source", ""))
                value = client.request(
                    "script_load", code=source, args={"name": arguments.get("name", "")}
                )
            elif name == "live_script_reload":
                source = _source_text(arguments.get("source", ""))
                value = client.request(
                    "script_reload", code=source, args={"name": arguments.get("name", "")}
                )
            else:
                raise LiveClientError(f"unsupported tool operation: {name}")
            response = _result(value)
    except (LiveClientError, ValueError, TypeError) as error:
        response = _error(str(error))
    return {"jsonrpc": "2.0", "id": request_id, "result": response}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--session-dir", help="the live session folder (default: PYUE_LIVE_DIR or the base mod's dist folder)")
    parser.add_argument("--endpoint", help="authenticated TCP endpoint (tcp://127.0.0.1:PORT or tcp://[::1]:PORT); overrides PYUE_LIVE_ENDPOINT")
    parser.add_argument("--token-file", help="restricted bearer-token file (default: PYUE_LIVE_TOKEN_FILE)")
    args = parser.parse_args(argv)
    try:
        client = client_from_environment(
            args.session_dir, endpoint=args.endpoint, token_file=args.token_file
        )
    except LiveClientError as error:
        print(f"ue4ss-live: {error}", file=sys.stderr)
        return 2
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            response = handle(message, client)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except (json.JSONDecodeError, TypeError) as error:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(error)}}) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
