"""Explicit ownership and lifecycle helpers for independently managed scripts.

This module is intentionally pure Python and does not alter the global ``ue4ss``
module. Components should use their :class:`ComponentContext` to register
resources they expect the runtime to dispose.
"""

from __future__ import annotations

import contextvars
import builtins
import copy
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import itertools
import os
import re
import sys
import threading
import time
import traceback
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import ue4ss

__all__ = [
    "CleanupError",
    "ComponentContext",
    "ComponentState",
    "ComponentStartError",
    "RuntimeManager",
    "ScriptRuntime",
    "WorkerHandle",
    "current_context",
]

_current_context: contextvars.ContextVar["ComponentContext | None"] = contextvars.ContextVar(
    "ue4ss_runtime_current_context", default=None
)
_instance_ids = itertools.count(1)
_SCRIPT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
MAX_SOURCE_BYTES = 128 * 1024
_script_runtimes: "weakref.WeakSet[ScriptRuntime]" = weakref.WeakSet()
_global_handover_runtimes: list["ScriptRuntime"] = []


def current_context() -> "ComponentContext | None":
    """Return the managed ownership scope active in this callback, if any.

    The value is callback-local and is restored after reentrant calls and
    exceptions. Legacy code outside managed callbacks receives ``None``.
    """
    return _current_context.get()


class CleanupError(RuntimeError):
    """One or more owned cleanup callbacks failed."""

    def __init__(self, errors: list[BaseException]):
        self.errors = tuple(errors)
        summary = "; ".join(f"{type(error).__name__}: {error}" for error in errors)
        super().__init__(f"{len(errors)} cleanup operation(s) failed: {summary}")


class ComponentStartError(RuntimeError):
    """A component failed during start; its partial resources were disposed."""

    def __init__(self, name: str, cause: BaseException, cleanup_error: CleanupError | None = None):
        self.name = name
        self.cause = cause
        self.cleanup_error = cleanup_error
        detail = f"component {name!r} failed to start: {type(cause).__name__}: {cause}"
        if cleanup_error:
            detail += f"; rollback also failed: {cleanup_error}"
        super().__init__(detail)


class ComponentState:
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass
class _CallbackSlot:
    """A delayed callback can be released even when the native queue cannot cancel it."""

    callback: Callable[..., Any] | None
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] | None = None


class WorkerHandle:
    """A signalled worker; stopping never joins it."""

    def __init__(self, thread: threading.Thread, stop_event: threading.Event):
        self.thread = thread
        self.stop_event = stop_event
        self.error: str | None = None

    @property
    def alive(self) -> bool:
        return self.thread.is_alive()

    def request_stop(self) -> None:
        self.stop_event.set()


class ComponentContext:
    """The resources and callbacks owned by one component instance or child scope.

    Lifecycle and Unreal registration methods are game-thread-only. Worker
    functions should use their stop event and hand Unreal work back through
    :meth:`execute_in_game_thread`.
    """

    def __init__(
        self,
        name: str,
        generation: int,
        *,
        parent: "ComponentContext | None" = None,
        root_state: dict[str, str] | None = None,
        config: dict[str, Any] | None = None,
    ):
        self.name = name
        self.instance_id = next(_instance_ids)
        self.generation = generation
        self.config = copy.deepcopy(config or {})
        self.parent = parent
        self._root_state = root_state or {"value": ComponentState.STARTING}
        self._valid = True
        self._lock = threading.RLock()
        self._cleanups: list[tuple[str | None, Callable[[], Any]]] = []
        self._slots: list[_CallbackSlot] = []
        self._workers: list[WorkerHandle] = []
        self._children: list[ComponentContext] = []
        self._cleanup_errors: list[BaseException] = []

    @property
    def state(self) -> str:
        self._refresh_root_state()
        return self._root_state["value"]

    @property
    def is_valid(self) -> bool:
        return self._valid and (self.parent is None or self.parent.is_valid)

    @property
    def cleanup_errors(self) -> tuple[BaseException, ...]:
        return tuple(self._cleanup_errors)

    @property
    def workers(self) -> tuple[WorkerHandle, ...]:
        self._refresh_root_state()
        return tuple(self._workers)

    @property
    def diagnostics(self) -> dict[str, Any]:
        self._refresh_root_state()
        return {
            "name": self.name,
            "instance_id": self.instance_id,
            "generation": self.generation,
            "state": self.state,
            "valid": self.is_valid,
            "worker_count": sum(1 for worker in self._all_workers()),
            "live_worker_count": sum(1 for worker in self._all_workers() if worker.alive),
            "workers": [
                {
                    "name": worker.thread.name,
                    "alive": worker.alive,
                    "stop_requested": worker.stop_event.is_set(),
                    "error": worker.error,
                }
                for worker in self._all_workers()
            ],
            "cleanup_errors": [f"{type(error).__name__}: {error}" for error in self._cleanup_errors],
        }

    def _require_game_thread(self) -> None:
        if not ue4ss.is_game_thread():
            raise ue4ss.UEError("managed component lifecycle and Unreal resource registration require the game thread")

    def _require_valid(self) -> None:
        with self._lock:
            if not self.is_valid:
                raise RuntimeError(f"component scope {self.name!r} is no longer valid")

    def _refresh_root_state(self) -> None:
        if self._root_state["value"] == ComponentState.STOPPING and not self._has_live_workers():
            self._root_state["value"] = ComponentState.STOPPED

    def _all_workers(self):
        yield from self._workers
        for child in self._children:
            yield from child._all_workers()

    def _has_live_workers(self) -> bool:
        return any(worker.alive for worker in self._all_workers())

    def _run_in_context(self, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if not self.is_valid:
            return None
        marker = _current_context.set(self)
        try:
            return callback(*args, **kwargs)
        finally:
            _current_context.reset(marker)

    def register_cleanup(self, callable: Callable[[], Any], *, name: str | None = None) -> Callable[[], Any]:
        """Adopt a cleanup action, run once during stop in reverse registration order."""
        self._require_game_thread()
        self._require_valid()
        if not callable_builtin(callable):
            raise TypeError("cleanup must be callable")
        self._cleanups.append((name, callable))
        return callable

    def adopt(self, cleanup: Callable[[], Any], *, name: str | None = None) -> Callable[[], Any]:
        """Explicitly adopt an externally-created resource's cleanup action."""
        return self.register_cleanup(cleanup, name=name)

    def child_scope(self, name: str) -> "ComponentContext":
        """Create a child ownership scope that is disposed with this scope."""
        self._require_game_thread()
        self._require_valid()
        if not isinstance(name, str) or not name:
            raise ValueError("child scope name must be a non-empty string")
        child = ComponentContext(
            f"{self.name}/{name}", self.generation, parent=self, root_state=self._root_state
        )
        child._root_state = {"value": ComponentState.RUNNING}
        self._children.append(child)
        self.register_cleanup(lambda child=child: child.stop(), name=f"child scope {name}")
        return child

    def execute_in_game_thread(self, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Queue a callback that is validity-checked and run in this owner context."""
        self._require_valid()
        if not callable_builtin(callback):
            raise TypeError("callback must be callable")
        slot = _CallbackSlot(callback, args, kwargs)
        with self._lock:
            self._require_valid()
            self._slots.append(slot)
        slot_ref = weakref.ref(slot)
        owner_ref = weakref.ref(self)

        def deliver() -> None:
            current_slot, owner = slot_ref(), owner_ref()
            if current_slot is None:
                return
            function, current_slot.callback = current_slot.callback, None
            call_args, current_slot.args = current_slot.args, ()
            call_kwargs, current_slot.kwargs = current_slot.kwargs, None
            if owner is not None:
                with owner._lock:
                    if current_slot in owner._slots:
                        owner._slots.remove(current_slot)
            if function is not None and owner is not None and owner.is_valid:
                owner._run_in_context(function, *call_args, **(call_kwargs or {}))

        ue4ss.execute_in_game_thread(deliver)

    def execute_with_delay(
        self, milliseconds: int, callback: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> None:
        """Schedule an owner-guarded callback; invalidation releases its closure."""
        self._require_valid()
        if not callable_builtin(callback):
            raise TypeError("callback must be callable")
        slot = _CallbackSlot(callback, args, kwargs)
        with self._lock:
            self._require_valid()
            self._slots.append(slot)
        slot_ref = weakref.ref(slot)
        owner_ref = weakref.ref(self)

        def deliver() -> None:
            current_slot, owner = slot_ref(), owner_ref()
            if current_slot is None:
                return
            function, current_slot.callback = current_slot.callback, None
            call_args, current_slot.args = current_slot.args, ()
            call_kwargs, current_slot.kwargs = current_slot.kwargs, None
            if owner is not None:
                with owner._lock:
                    if current_slot in owner._slots:
                        owner._slots.remove(current_slot)
            if function is not None and owner is not None and owner.is_valid:
                owner._run_in_context(function, *call_args, **(call_kwargs or {}))

        ue4ss.execute_with_delay(milliseconds, deliver)

    def _guard(self, callback: Callable[..., Any]) -> Callable[..., Any]:
        slot = _CallbackSlot(callback)
        with self._lock:
            self._require_valid()
            self._slots.append(slot)
        slot_ref = weakref.ref(slot)
        owner_ref = weakref.ref(self)

        def guarded(*args: Any, **kwargs: Any) -> Any:
            owner, current_slot = owner_ref(), slot_ref()
            function = current_slot.callback if current_slot is not None else None
            if owner is not None and function is not None and owner.is_valid:
                return owner._run_in_context(function, *args, **kwargs)
            return None

        return guarded

    def _register_owned(self, register: Callable[..., Any], unregister: Callable[..., Any],
                        callback: Callable[..., Any], *args: Any, cleanup_name: str, **kwargs: Any) -> Any:
        self._require_game_thread()
        self._require_valid()
        owned_callback = self._guard(callback)
        identifier = register(*args, owned_callback, **kwargs)
        self.register_cleanup(lambda: unregister(identifier), name=cleanup_name)
        return identifier

    def register_key_down(self, key: Any, callback: Callable[..., Any], modifiers: Any = ()) -> int:
        return self._register_owned(
            ue4ss.register_key_down, ue4ss.unregister_key_down, callback, key,
            cleanup_name=f"key binding {key}", modifiers=modifiers
        )

    def register_hook(self, path: str, pre: Callable[..., Any] | None = None,
                      post: Callable[..., Any] | None = None, instance: Any = None) -> tuple[int, int]:
        self._require_game_thread()
        self._require_valid()
        guarded_pre = self._guard(pre) if pre is not None else None
        guarded_post = self._guard(post) if post is not None else None
        identifiers = ue4ss.register_hook(path, pre=guarded_pre, post=guarded_post, instance=instance)
        self.register_cleanup(lambda: ue4ss.unregister_hook(identifiers), name=f"hook {path}")
        return identifiers

    def bind_event(self, object: Any, name: str, callback: Callable[..., Any], *, async_: bool = False) -> int:
        identifier = self._register_owned(
            ue4ss.bind_event, ue4ss.unbind_event, callback, object, name,
            cleanup_name=f"event binding {name}", async_=async_
        )
        return identifier

    def notify_on_new_object(self, class_path: str, callback: Callable[..., Any]) -> int:
        return self._register_owned(
            ue4ss.notify_on_new_object, ue4ss.unregister_notify_on_new_object,
            callback, class_path, cleanup_name=f"new-object notification {class_path}"
        )

    def register_replacement(self, path: str, callback: Callable[..., Any]) -> int:
        return self._register_owned(
            ue4ss.register_replacement, ue4ss.unregister_replacement,
            callback, path, cleanup_name=f"replacement {path}"
        )

    def spawn_worker(self, target: Callable[..., Any], *args: Any, name: str | None = None,
                     daemon: bool = True, **kwargs: Any) -> WorkerHandle:
        """Start a worker that receives its owner stop event; never joined on stop."""
        self._require_game_thread()
        self._require_valid()
        if not callable_builtin(target):
            raise TypeError("worker target must be callable")
        stopped = threading.Event()
        thread_name = name or f"ue4ss:{self.name}:{self.instance_id}"
        handle: WorkerHandle

        def run_worker() -> None:
            try:
                target(stopped, *args, **kwargs)
            except BaseException as error:
                handle.error = f"{type(error).__name__}: {error}"

        thread = threading.Thread(target=run_worker, name=thread_name, daemon=daemon)
        handle = WorkerHandle(thread, stopped)
        self._workers.append(handle)
        self.register_cleanup(handle.request_stop, name=f"stop worker {thread_name}")
        try:
            thread.start()
        except BaseException:
            self._workers.remove(handle)
            self._cleanups = [entry for entry in self._cleanups if entry[1] != handle.request_stop]
            raise
        return handle

    def _invalidate(self) -> None:
        with self._lock:
            self._valid = False
            # A native delayed-task queue may retain deliver(), but it retains only a
            # weak reference to this slot; clear user closures now rather than at due time.
            for slot in self._slots:
                slot.callback = None
                slot.args = ()
                slot.kwargs = None
            self._slots.clear()

    def stop(self) -> None:
        """Invalidate, dispose in reverse order, signal workers, and return without joining."""
        self._require_game_thread()
        if not self._valid:
            self._refresh_root_state()
            if self.parent is None and self._root_state["value"] == ComponentState.STOPPING:
                return
            return
        self._root_state["value"] = ComponentState.STOPPING
        self._invalidate()
        errors: list[BaseException] = []
        cleanups, self._cleanups = self._cleanups, []
        for _name, cleanup in reversed(cleanups):
            try:
                cleanup()
            except BaseException as error:
                errors.append(error)
        for worker in self._workers:
            worker.request_stop()
        self._cleanup_errors.extend(errors)
        self._root_state["value"] = (
            ComponentState.STOPPING if self._has_live_workers() else ComponentState.STOPPED
        )
        if errors:
            raise CleanupError(errors)


def callable_builtin(value: Any) -> bool:
    # Avoid shadowing the built-in callable with the cleanup parameter name.
    return builtins.callable(value)


class _DirectorySourceTree:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def read(self, relative: str) -> tuple[str, str] | None:
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise ImportError(f"managed import escapes its package root: {relative}")
        if not candidate.is_file() or candidate.suffix != ".py":
            return None
        return candidate.read_text(encoding="utf-8"), str(candidate)

    def validate(self, name: str) -> None:
        init = self.read("__init__.py")
        if init is None:
            raise ValueError(f"managed package {name!r} must contain __init__.py")
        for path in self.root.rglob("*.py"):
            resolved = path.resolve()
            try:
                resolved.relative_to(self.root)
            except ValueError:
                raise ValueError(f"managed source escapes package root: {path}")
            source = resolved.read_text(encoding="utf-8")
            compile(source, str(resolved), "exec", dont_inherit=True)


class _MemorySourceTree:
    def __init__(self, name: str, source: str):
        self.name = name
        self.source = source

    def read(self, relative: str) -> tuple[str, str] | None:
        if relative != "__init__.py":
            return None
        return self.source, f"<managed:{self.name}>"

    def validate(self, name: str) -> None:
        compile(self.source, f"<managed:{name}>", "exec", dont_inherit=True)


class _ManagedSourceLoader(importlib.abc.Loader):
    def __init__(self, tree: Any, relative: str, is_package: bool):
        self.tree = tree
        self.relative = relative
        self.is_package = is_package

    def create_module(self, spec):
        return None

    def exec_module(self, module) -> None:
        item = self.tree.read(self.relative)
        if item is None:
            raise ImportError(f"managed source disappeared while importing {self.relative}")
        source, filename = item
        module.__file__ = filename
        code = compile(source, filename, "exec", dont_inherit=True)
        exec(code, module.__dict__, module.__dict__)


class _ManagedSourceFinder(importlib.abc.MetaPathFinder):
    """One stable finder for fresh, private managed package prefixes."""

    def __init__(self):
        self.trees: dict[str, Any] = {}

    def find_spec(self, fullname: str, path=None, target=None):
        matches = [prefix for prefix in self.trees if fullname == prefix or fullname.startswith(prefix + ".")]
        if not matches:
            return None
        prefix = max(matches, key=len)
        tree = self.trees[prefix]
        suffix = fullname[len(prefix):].lstrip(".")
        if not suffix:
            relative, is_package = "__init__.py", True
        else:
            components = suffix.split(".")
            if any(not part.isidentifier() for part in components):
                return None
            stem = "/".join(components)
            package = tree.read(stem + "/__init__.py")
            if package is not None:
                relative, is_package = stem + "/__init__.py", True
            else:
                module = tree.read(stem + ".py")
                if module is None:
                    return None
                relative, is_package = stem + ".py", False
        item = tree.read(relative)
        if item is None:
            return None
        filename = item[1]
        loader = _ManagedSourceLoader(tree, relative, is_package)
        spec = importlib.machinery.ModuleSpec(fullname, loader, origin=filename, is_package=is_package)
        if is_package:
            spec.submodule_search_locations = (
                [str(Path(filename).parent)] if not filename.startswith("<") else []
            )
        return spec


_managed_finder = _ManagedSourceFinder()
if _managed_finder not in sys.meta_path:
    sys.meta_path.insert(0, _managed_finder)


@dataclass
class _ManagedScript:
    name: str
    tree: Any
    source_kind: str
    context: ComponentContext | None = None
    prefix: str | None = None
    module: Any = None
    state: str = ComponentState.STOPPED
    error: str | None = None
    config: dict[str, Any] = field(default_factory=dict)


class ScriptRuntime:
    """Loads isolated managed component packages and reloads them independently.

    ``load_source`` accepts one UTF-8 file in memory, starts it for this session,
    and never edits a startup manifest. ``load_local`` accepts a package directory
    contained by one of the configured roots. Each start/reload uses a fresh
    private package prefix and module objects; shared imports are left untouched.
    """

    def __init__(self, *, roots: tuple[str | os.PathLike[str], ...] = (), manager: RuntimeManager | None = None):
        self.manager = manager or RuntimeManager()
        self.roots = tuple(Path(root).resolve() for root in roots)
        self._scripts: dict[str, _ManagedScript] = {}
        _script_runtimes.add(self)

    def _validate_name(self, name: str) -> None:
        if not isinstance(name, str) or not _SCRIPT_NAME.fullmatch(name):
            raise ValueError("script name must match [A-Za-z][A-Za-z0-9_-]{0,63}")

    def _fresh_prefix(self, name: str) -> str:
        digest = hashlib.sha256(name.encode("ascii")).hexdigest()[:10]
        return f"_ue4ss_managed_{name.replace('-', '_')}_{digest}_{next(_instance_ids)}"

    def _refresh(self) -> None:
        for script in self._scripts.values():
            context = self.manager.get(script.name)
            if context is None and script.context is not None:
                script.state = script.context.state
                script.context = None
                script.error = script.error
                self._unload_modules(script)

    def _unload_modules(self, script: _ManagedScript) -> None:
        prefix = script.prefix
        if prefix is not None:
            for fullname in tuple(sys.modules):
                if fullname == prefix or fullname.startswith(prefix + "."):
                    del sys.modules[fullname]
            _managed_finder.trees.pop(prefix, None)
        script.prefix = None
        script.module = None

    def _start_record(self, script: _ManagedScript) -> ComponentContext:
        self.manager._require_game_thread()
        script.tree.validate(script.name)
        prefix = self._fresh_prefix(script.name)
        _managed_finder.trees[prefix] = script.tree
        spec = _managed_finder.find_spec(prefix)
        module = importlib.util.module_from_spec(spec)
        sys.modules[prefix] = module
        script.prefix, script.module = prefix, module
        try:
            spec.loader.exec_module(module)
            starter = getattr(module, "start", None)
            if not callable_builtin(starter):
                raise TypeError(f"managed script {script.name!r} must export start(ctx)")
            context = self.manager.start(
                script.name,
                starter,
                config=script.config,
                script_runtime=self,
            )
            script.context = context
            script.state = context.state
            script.error = None
            return context
        except BaseException:
            script.error = traceback.format_exc()
            script.context = self.manager.get(script.name)
            script.state = script.context.state if script.context is not None else ComponentState.FAILED
            if script.context is None:
                self._unload_modules(script)
            raise

    def load_source(self, name: str, source: str | bytes) -> ComponentContext:
        """Start ephemeral single-file source. This never writes files or persists."""
        self.manager._require_game_thread()
        self._validate_name(name)
        if isinstance(source, bytes):
            raw = source
            try:
                text = source.decode("utf-8", errors="strict")
            except UnicodeDecodeError as error:
                raise ValueError("managed source must be valid UTF-8") from error
        elif isinstance(source, str):
            text = source
            raw = source.encode("utf-8", errors="strict")
        else:
            raise TypeError("source must be str or UTF-8 bytes")
        if len(raw) > MAX_SOURCE_BYTES:
            raise ValueError(f"managed source exceeds the {MAX_SOURCE_BYTES}-byte limit")
        tree = _MemorySourceTree(name, text)
        tree.validate(name)  # syntax errors do not stop an existing component
        self._refresh()
        existing = self._scripts.get(name)
        if existing is not None and existing.state in (ComponentState.RUNNING, ComponentState.STOPPING):
            raise RuntimeError(f"script {name!r} is already {existing.state}; use reload()")
        script = _ManagedScript(name, tree, "session")
        self._scripts[name] = script
        return self._start_record(script)

    def load(self, name: str, source: str | bytes) -> ComponentContext:
        """Load a bounded, session-only source payload (alias for ``load_source``)."""
        return self.load_source(name, source)

    def load_local(
        self,
        name: str,
        package: str | os.PathLike[str],
        *,
        config: dict[str, Any] | None = None,
    ) -> ComponentContext:
        """Start a local package directory, constrained to a configured root."""
        self.manager._require_game_thread()
        self._validate_name(name)
        if config is not None and not isinstance(config, dict):
            raise TypeError("component config must be a dict or None")
        package_path = Path(package).resolve()
        if not any(_is_relative_to(package_path, root) for root in self.roots):
            raise ValueError(f"managed package must be under a configured root: {package_path}")
        tree = _DirectorySourceTree(package_path)
        tree.validate(name)
        self._refresh()
        existing = self._scripts.get(name)
        if existing is not None and existing.state in (ComponentState.RUNNING, ComponentState.STOPPING):
            raise RuntimeError(f"script {name!r} is already {existing.state}; use reload()")
        script = _ManagedScript(name, tree, "local", config=copy.deepcopy(config or {}))
        self._scripts[name] = script
        return self._start_record(script)

    def start(self, name: str) -> ComponentContext:
        """Start the last loaded source/package as a fresh component instance."""
        self.manager._require_game_thread()
        self._refresh()
        script = self._scripts.get(name)
        if script is None:
            raise KeyError(name)
        if script.state == ComponentState.STOPPING:
            raise RuntimeError(f"script {name!r} is still stopping")
        return self._start_record(script)

    def stop(self, name: str) -> None:
        self.manager._require_game_thread()
        script = self._scripts.get(name)
        if script is None:
            return
        try:
            self.manager.stop(name)
        finally:
            context = self.manager.get(name)
            if context is not None:
                script.context = context
                script.state = context.state
            else:
                if script.context is not None:
                    script.state = script.context.state
                script.context = None
                self._unload_modules(script)

    def reload(self, name: str, source: str | bytes | None = None) -> ComponentContext:
        """Reload with fresh source/module identity; syntax errors preserve old instance."""
        self.manager._require_game_thread()
        self._validate_name(name)
        self._refresh()
        old = self._scripts.get(name)
        if old is None:
            if source is None:
                raise KeyError(name)
            return self.load_source(name, source)
        candidate = old.tree
        if source is not None:
            if old.source_kind != "session":
                raise ValueError("source replacement is supported only for session-loaded single-file scripts")
            if isinstance(source, bytes):
                if len(source) > MAX_SOURCE_BYTES:
                    raise ValueError(f"managed source exceeds the {MAX_SOURCE_BYTES}-byte limit")
                try:
                    text = source.decode("utf-8", errors="strict")
                except UnicodeDecodeError as error:
                    raise ValueError("managed source must be valid UTF-8") from error
            elif isinstance(source, str):
                text = source
                if len(source.encode("utf-8", errors="strict")) > MAX_SOURCE_BYTES:
                    raise ValueError(f"managed source exceeds the {MAX_SOURCE_BYTES}-byte limit")
            else:
                raise TypeError("source must be str or UTF-8 bytes")
            candidate = _MemorySourceTree(name, text)
        try:
            candidate.validate(name)  # must happen before invalidating the old instance
        except BaseException:
            old.error = traceback.format_exc()
            raise
        context = self.manager.get(name)
        if context is not None:
            self.stop(name)
            context = self.manager.get(name)
            if context is not None:
                raise RuntimeError(f"script {name!r} is still stopping; reload will not overlap instances")
        self._unload_modules(old)
        replacement = _ManagedScript(
            name, candidate, old.source_kind, config=copy.deepcopy(old.config)
        )
        self._scripts[name] = replacement
        return self._start_record(replacement)

    def list(self) -> list[dict[str, Any]]:
        """Return per-script lifecycle and traceback diagnostics."""
        self._refresh()
        return [
            {
                "name": script.name,
                "source_kind": script.source_kind,
                "state": script.state,
                "instance_id": script.context.instance_id if script.context else None,
                "generation": script.context.generation if script.context else None,
                "diagnostics": script.context.diagnostics if script.context else None,
                "error": script.error,
            }
            for script in self._scripts.values()
        ]

    def _prepare_global_reload(self) -> list[str]:
        errors = []
        for name in tuple(self._scripts):
            try:
                self.stop(name)
            except BaseException:
                errors.append(f"{name}: {traceback.format_exc()}")
        self._refresh()
        return errors

    def _handover_workers(self) -> list[WorkerHandle]:
        self._refresh()
        return [
            worker
            for script in self._scripts.values()
            if script.context is not None
            for worker in script.context._all_workers()
            if worker.alive
        ]


def _before_global_reload() -> dict[str, Any]:
    """Private native hook: stop already-loaded managers without importing runtime."""
    errors: list[str] = []
    cancel_futures = getattr(ue4ss, "_cancel_pending_futures", None)
    if callable_builtin(cancel_futures):
        cancel_futures()
    runtimes = tuple(_script_runtimes)
    for runtime in runtimes:
        errors.extend(runtime._prepare_global_reload())
        if runtime._handover_workers() and runtime not in _global_handover_runtimes:
            _global_handover_runtimes.append(runtime)
    return {
        "runtime_count": len(runtimes),
        "pending_workers": sum(len(runtime._handover_workers()) for runtime in _global_handover_runtimes),
        "errors": errors,
    }


def wait_for_global_handover(
    on_ready: Callable[[dict[str, Any]], Any],
    *,
    timeout_ms: int = 5000,
    poll_ms: int = 50,
    on_timeout: Callable[[dict[str, Any]], Any] | None = None,
) -> None:
    """Poll after a global reload without blocking or rebinding over old workers.

    Call on the game thread before starting exclusive resources. The success
    callback runs only after prior managed workers exit. A timeout reports the
    surviving workers and intentionally leaves their old runtime objects intact.
    """
    if not ue4ss.is_game_thread():
        raise ue4ss.UEError("global reload handover must be started on the game thread")
    if not callable_builtin(on_ready):
        raise TypeError("on_ready must be callable")
    if on_timeout is not None and not callable_builtin(on_timeout):
        raise TypeError("on_timeout must be callable or None")
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or not 0 <= timeout_ms <= 60_000:
        raise ValueError("timeout_ms must be an integer from 0 through 60000")
    if not isinstance(poll_ms, int) or isinstance(poll_ms, bool) or poll_ms < 1:
        raise ValueError("poll_ms must be a positive integer")
    deadline = time.monotonic() + timeout_ms / 1000.0

    def poll() -> None:
        pending_workers = []
        for runtime in tuple(_global_handover_runtimes):
            pending_workers.extend(runtime._handover_workers())
        status = {
            "pending_workers": len(pending_workers),
            "runtime_count": len(_global_handover_runtimes),
            "timed_out": False,
        }
        if not pending_workers:
            _global_handover_runtimes.clear()
            on_ready(status)
        elif time.monotonic() >= deadline:
            status["timed_out"] = True
            status["workers"] = [
                {
                    "name": worker.thread.name,
                    "error": worker.error,
                    "stop_requested": worker.stop_event.is_set(),
                }
                for worker in pending_workers
            ]
            if on_timeout is not None:
                on_timeout(status)
            else:
                print(f"ue4ss.runtime: managed reload handover timed out; refusing unsafe restart: {status!r}")
        else:
            ue4ss.execute_with_delay(poll_ms, poll)

    poll()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class RuntimeManager:
    """Owns named component instances; callers invoke lifecycle methods on game thread."""

    def __init__(self):
        self._components: dict[str, ComponentContext] = {}
        self._generation = 0

    @property
    def components(self) -> dict[str, ComponentContext]:
        self._refresh()
        return dict(self._components)

    def _require_game_thread(self) -> None:
        if not ue4ss.is_game_thread():
            raise ue4ss.UEError("managed component lifecycle operations require the game thread")

    def _refresh(self) -> None:
        for name, context in tuple(self._components.items()):
            context._refresh_root_state()
            if context.state in (ComponentState.STOPPED, ComponentState.FAILED):
                del self._components[name]

    def start(
        self,
        name: str,
        starter: Callable[[ComponentContext], Any],
        *,
        config: dict[str, Any] | None = None,
        script_runtime: ScriptRuntime | None = None,
    ) -> ComponentContext:
        """Start a named component, rolling back partial ownership if startup raises."""
        self._require_game_thread()
        if not isinstance(name, str) or not name:
            raise ValueError("component name must be a non-empty string")
        if not callable_builtin(starter):
            raise TypeError("starter must be callable")
        if config is not None and not isinstance(config, dict):
            raise TypeError("component config must be a dict or None")
        self._refresh()
        previous = self._components.get(name)
        if previous is not None:
            state = previous.state
            if state == ComponentState.STOPPING:
                raise RuntimeError(f"component {name!r} is still stopping; restart is not yet safe")
            raise RuntimeError(f"component {name!r} is already {state}")
        self._generation += 1
        context = ComponentContext(name, self._generation, config=config)
        context.script_runtime = script_runtime
        self._components[name] = context
        context._root_state["value"] = ComponentState.STARTING
        try:
            marker = _current_context.set(context)
            try:
                starter(context)
            finally:
                _current_context.reset(marker)
            if not context.is_valid:
                raise RuntimeError("component stopped itself during startup")
            context._root_state["value"] = ComponentState.RUNNING
            return context
        except BaseException as cause:
            context._root_state["value"] = ComponentState.FAILED
            try:
                context.stop()
                cleanup_error = None
            except CleanupError as error:
                cleanup_error = error
            context._root_state["value"] = (
                ComponentState.STOPPING if context._has_live_workers() else ComponentState.FAILED
            )
            raise ComponentStartError(name, cause, cleanup_error) from cause

    def stop(self, name: str) -> None:
        self._require_game_thread()
        self._refresh()
        context = self._components.get(name)
        if context is None:
            return
        context.stop()
        self._refresh()

    def stop_all(self) -> None:
        self._require_game_thread()
        errors: list[BaseException] = []
        for name in reversed(tuple(self._components)):
            try:
                self.stop(name)
            except BaseException as error:
                errors.append(error)
        if errors:
            raise CleanupError(errors)

    def get(self, name: str) -> ComponentContext | None:
        """Return the current named component, or None after it fully stopped."""
        self._refresh()
        return self._components.get(name)

    def current_context(self) -> ComponentContext | None:
        """Return the owner context only while one of its guarded callbacks runs."""
        return current_context()
