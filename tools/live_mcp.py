"""Minimal stdio MCP adapter for a running live-game bridge.

Exposes the operations of a running bridge (tools/live.py install, then start the game) as MCP tools over stdio, so an
AI assistant can run Python in the game. It does not start a game: without a live session every tool reports that.
The session folder is PYUE_LIVE_DIR, default dist/PythonScripting/live.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from live_client import LiveClient, LiveClientError, client_from_environment


TOOLS = [
    {"name": "live_status", "description": "Check whether the resident game bridge is alive.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "live_execute", "description": "Execute a bounded Python block in the resident game session.", "inputSchema": {"type": "object", "required": ["code"], "properties": {"code": {"type": "string"}, "timeout": {"type": "number"}}}},
    {"name": "live_reset", "description": "Reset the persistent Python session namespace and generation.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "live_job_start", "description": "Queue a Python block and return a job ID.", "inputSchema": {"type": "object", "required": ["code"], "properties": {"code": {"type": "string"}}}},
    {"name": "live_job_status", "description": "Read a queued or completed live job.", "inputSchema": {"type": "object", "required": ["job_id"], "properties": {"job_id": {"type": "string"}}}},
    {"name": "live_cleanup", "description": "Run cleanup callbacks registered in a named group.", "inputSchema": {"type": "object", "properties": {"group": {"type": "string"}}}},
    {"name": "live_wait_until", "description": "Block until a readiness check is truthy or the timeout hits. Pass `check` as a Python expression the bridge re-evaluates across engine frames (e.g. a variable a move or refill job sets); pass no `check` to block for the full timeout with no polling.", "inputSchema": {"type": "object", "required": ["timeout"], "properties": {"check": {"type": "string"}, "timeout": {"type": "number"}}}},
    {"name": "live_sleep", "description": "Block for a fixed number of seconds with no polling (the no-condition wait).", "inputSchema": {"type": "object", "required": ["seconds"], "properties": {"seconds": {"type": "number"}}}},
    {"name": "live_travel", "description": "Request a map travel in the resident game session.", "inputSchema": {"type": "object", "required": ["map"], "properties": {"map": {"type": "string"}, "timeout": {"type": "number"}}}},
    {"name": "live_reload", "description": "Reload the resident Python script.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "live_stop", "description": "Stop the resident bridge session.", "inputSchema": {"type": "object", "properties": {}}},
]


def _result(value: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=repr)}], "isError": False}


def handle(message: dict[str, Any], client: LiveClient) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "pyue-live", "version": "0.1"}}}
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
        if name == "live_status":
            value = {"alive": client.is_alive(), "directory": str(client.root)}
        elif name == "live_execute":
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
            timeout = float(arguments.get("timeout", 30))
            value = client.request("wait_until", code=arguments.get("check", ""), timeout=timeout)
        elif name == "live_sleep":
            seconds = float(arguments.get("seconds", 1))
            value = client.request("wait_until", code="", timeout=seconds)
        elif name == "live_travel":
            value = client.request("travel", args={"map": arguments.get("map", "")}, timeout=float(arguments.get("timeout", 60)))
        elif name == "live_reload":
            value = client.request("reload")
        elif name == "live_stop":
            value = client.request("stop")
        else:
            raise LiveClientError(f"unknown tool: {name}")
        response = _result(value)
    except (LiveClientError, ValueError, TypeError) as error:
        response = {"content": [{"type": "text", "text": str(error)}], "isError": True}
    return {"jsonrpc": "2.0", "id": request_id, "result": response}


def main() -> int:
    client = client_from_environment()
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
