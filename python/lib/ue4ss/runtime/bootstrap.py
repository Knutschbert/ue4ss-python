"""Stable main.py bootstrap and local, explicitly ordered component manifest."""

from __future__ import annotations

import json
import re
import traceback
from pathlib import Path
from typing import Any

import ue4ss

from . import ScriptRuntime, wait_for_global_handover

MANIFEST_NAME = "manifest.json"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_COMPONENTS = 64
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_instances: dict[str, "ManagedBootstrap"] = {}


def empty_manifest() -> dict[str, Any]:
    return {"schema_version": 1, "startup_order": [], "components": {}}


def _reject_constant(value: str):
    raise ValueError(f"non-finite number {value} is not valid manifest JSON")


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate manifest key: {key}")
        value[key] = item
    return value


def load_manifest(path: str | Path) -> dict[str, Any]:
    """Read and validate a bounded manifest; missing manifests mean no components."""
    target = Path(path)
    if target.is_symlink():
        raise ValueError("managed manifest may not be a symlink")
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        return empty_manifest()
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError(f"manifest exceeds the {MAX_MANIFEST_BYTES}-byte limit")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"manifest is not strict UTF-8 JSON: {error}") from error
    if (
        not isinstance(value, dict)
        or isinstance(value.get("schema_version"), bool)
        or value.get("schema_version") != 1
    ):
        raise ValueError("manifest schema_version must be 1")
    if set(value) != {"schema_version", "startup_order", "components"}:
        raise ValueError("manifest must contain only schema_version, startup_order and components")
    order = value["startup_order"]
    components = value["components"]
    if not isinstance(order, list) or not isinstance(components, dict):
        raise ValueError("manifest startup_order and components have invalid types")
    if len(components) > MAX_COMPONENTS:
        raise ValueError(f"manifest may contain at most {MAX_COMPONENTS} components")
    names = set(components)
    if any(not isinstance(name, str) for name in order):
        raise ValueError("startup_order entries must be strings")
    if len(order) != len(set(order)) or set(order) != names:
        raise ValueError("startup_order must list every component exactly once")
    if any(not isinstance(name, str) or not _NAME.fullmatch(name) for name in names):
        raise ValueError("component names must match [A-Za-z][A-Za-z0-9_-]{0,63}")
    for name, config in components.items():
        if not isinstance(config, dict) or not {"enabled", "path"} <= set(config) <= {
            "enabled", "path", "config"
        }:
            raise ValueError(f"component {name!r} must contain enabled, path and optional config")
        if not isinstance(config["enabled"], bool):
            raise ValueError(f"component {name!r} enabled must be boolean")
        if "config" in config and not isinstance(config["config"], dict):
            raise ValueError(f"component {name!r} config must be an object")
        package = config["path"]
        if not isinstance(package, str) or not package or "\\" in package:
            raise ValueError(f"component {name!r} path must be a relative POSIX path")
        parts = package.split("/")
        if (
            re.match(r"^[A-Za-z]:", package)
            or package.startswith("/")
            or any(part in ("", ".", "..") for part in parts)
        ):
            raise ValueError(f"component {name!r} path must stay within managed_runtime")
    return value


class ManagedBootstrap:
    """Strongly retain the runtime object and isolate failures between components."""

    def __init__(self, root: Path, manifest: dict[str, Any]):
        self.root = root
        self.manifest = manifest
        self.runtime = ScriptRuntime(roots=(root,))
        self.errors: dict[str, str] = {}
        self.started: list[str] = []

    def start_components(self) -> None:
        if not ue4ss.is_game_thread():
            raise ue4ss.UEError("managed bootstrap must start on the game thread")
        for name in self.manifest["startup_order"]:
            config = self.manifest["components"][name]
            if not config["enabled"]:
                continue
            try:
                self.runtime.load_local(
                    name,
                    self.root / config["path"],
                    config=config.get("config", {}),
                )
                self.started.append(name)
            except BaseException:
                self.errors[name] = traceback.format_exc()
                print(f"ue4ss.runtime: component {name!r} failed to start: {self.errors[name]}")


def start(main_file: str | Path, *, timeout_ms: int = 5000, poll_ms: int = 50) -> ManagedBootstrap:
    """Start the stable bootstrap, waiting asynchronously for prior global reload workers."""
    if not ue4ss.is_game_thread():
        raise ue4ss.UEError("managed bootstrap must start on the game thread")
    scripts = Path(main_file).resolve().parent
    root = scripts.parent / "managed_runtime"
    if root.is_symlink():
        raise ValueError("managed_runtime state directory may not be a symlink")
    manifest = load_manifest(root / MANIFEST_NAME)
    key = str(root)
    instance = ManagedBootstrap(root, manifest)

    def ready(_status: dict[str, Any]) -> None:
        _instances[key] = instance
        instance.start_components()

    def timed_out(status: dict[str, Any]) -> None:
        print(f"ue4ss.runtime: refusing managed startup after reload handover timeout: {status!r}")

    wait_for_global_handover(
        ready,
        timeout_ms=timeout_ms,
        poll_ms=poll_ms,
        on_timeout=timed_out,
    )
    return instance


__all__ = ["MANIFEST_NAME", "ManagedBootstrap", "empty_manifest", "load_manifest", "start"]
