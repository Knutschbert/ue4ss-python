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
    "into a game: the user runs `python tools/live.py install` once and starts the game themselves. Call live_status first; "
    "when no bridge is alive, tell the user what it says instead of retrying. All code runs on the game thread in one "
    "persistent session (variables stay between calls, `ue4ss` is imported): keep each call short, use live_job_start for "
    "work that spans frames and live_wait_until / live_wait_ready to wait, and never sleep or loop for long inside "
    "live_execute, which freezes the game. Assign to `result` to return a value."
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
]

BRIDGE_MARK = "# generated by tools.live_bridge"
READY_CHECK = ("ue4ss.find_first_of('World') is not None and "
               "(not {require_pawn} or getattr(ue4ss.find_first_of('PlayerController'), 'Pawn', None) is not None)")
READY_REPORT = ("_c = ue4ss.find_first_of('PlayerController')\n"
                "result = {'world': ue4ss.find_first_of('World') is not None, 'player_controller': _c is not None, "
                "'pawn': getattr(_c, 'Pawn', None) is not None}\ndel _c")


def _result(value: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=repr)}], "isError": False}


def _error(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def bridge_state(client: LiveClient) -> tuple[str, str]:
    """('alive' | 'not_running' | 'not_installed', what the user has to do). Never waits."""
    if client.is_alive():
        return "alive", ""
    main_py = client.root.parent / "python_scripts" / "main.py"
    try:
        installed = main_py.read_text(encoding="utf-8", errors="replace").startswith(BRIDGE_MARK)
    except OSError:
        installed = False
    if not installed:
        return "not_installed", ("The bridge is not installed: the user has to run `python tools/live.py install` in the ue4ss-python "
                                 "repository and then start the game (or reload the script in a running game). This server does not do that.")
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
            value = {"alive": state == "alive", "state": state, "directory": str(client.root)}
            if state == "alive":
                value.update(client.request("status", timeout=10.0).get("result") or {})
            else:
                value["what_to_do"] = advice
            response = _result(value)
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
            else:
                value = client.request("stop")
            response = _result(value)
    except (LiveClientError, ValueError, TypeError) as error:
        response = _error(str(error))
    return {"jsonrpc": "2.0", "id": request_id, "result": response}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--session-dir", help="the live session folder (default: PYUE_LIVE_DIR or the base mod's dist folder)")
    args = parser.parse_args(argv)
    client = client_from_environment(args.session_dir)
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
