"""Managed-runtime adapter for the shared live bridge transports."""

from __future__ import annotations

from pathlib import Path

import ue4ss

from .core import LiveCore
from .file_transport import FileTransport
from .framing import validate_bind_host
from .security import validate_token_file
from .socket_transport import LiveSocketService


class _OwnerScheduler:
    def __init__(self, ctx):
        self.ctx = ctx

    def __getattr__(self, name):
        return getattr(ue4ss, name)

    def execute_with_delay(self, milliseconds, callback):
        self.ctx.execute_with_delay(milliseconds, callback)


def _noop(_event):
    pass


def start(ctx) -> None:
    """Start explicitly selected transports around one persistent LiveCore.

    ``ctx.config`` accepts ``transport`` as ``file``, ``socket`` or ``both``.
    File IPC is the default. File paths and token-file paths are local settings;
    never place the token itself in the manifest.
    """
    config = ctx.config
    transport = config.get("transport", "file")
    if transport not in ("file", "socket", "both"):
        raise ValueError("live transport must be file, socket or both")

    file_adapter = None
    if transport in ("file", "both"):
        file_config = config.get("file", {})
        if not isinstance(file_config, dict):
            raise ValueError("live file configuration must be an object")
        inbox = file_config.get("inbox")
        outbox = file_config.get("outbox")
        heartbeat = file_config.get("heartbeat")
        if not all(isinstance(path, str) and path for path in (inbox, outbox, heartbeat)):
            raise ValueError("live file transport requires inbox, outbox and heartbeat paths")
        poll_ms = file_config.get("poll_ms", 250)
        if isinstance(poll_ms, bool) or not isinstance(poll_ms, int) or not 50 <= poll_ms <= 2000:
            raise ValueError("live file poll_ms must be between 50 and 2000")
        file_adapter = FileTransport(
            None, inbox, outbox, heartbeat, poll_ms, ue_module=_OwnerScheduler(ctx)
        )
        core = file_adapter.core
    else:
        core = LiveCore(_noop, ue_module=ue4ss)

    # The bootstrap assigns the owning ScriptRuntime before invoking start().
    core.script_runtime = getattr(ctx, "script_runtime", None)
    ctx.live_core = core
    ctx.register_cleanup(core.stop, name="stop live execution scope")

    service = None
    if transport in ("socket", "both"):
        socket_config = config.get("socket", {})
        if not isinstance(socket_config, dict):
            raise ValueError("live socket configuration must be an object")
        token_file = socket_config.get("token_file")
        if not isinstance(token_file, str) or not token_file:
            raise ValueError("live socket transport requires a restricted token_file")
        token = validate_token_file(token_file)
        host = socket_config.get("host", "127.0.0.1")
        port = socket_config.get("port", 9000)
        host = validate_bind_host(host)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("live socket port must be between 1 and 65535")
        service = LiveSocketService(core, token, host=host, port=port, ue_module=_OwnerScheduler(ctx))
        ctx.live_socket_service = service
        ctx.register_cleanup(service.request_stop, name="stop live socket listener")

    if file_adapter is not None:
        file_adapter.start()
    if service is not None:
        def run_socket(stop_event):
            service.run(stop_event)
            if service.error:
                print(f"ue4ss live socket stopped with error: {service.error}")

        ctx.spawn_worker(run_socket, name="ue4ss:live-socket")


__all__ = ["start"]
