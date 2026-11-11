"""The base stub (ue4ss/__init__.pyi) made from the live ue4ss module: signatures are read from the first line of each docstring
("name(args) -> result: text"), classes from their methods and bases, enums from their members. Used by ue4ss.engine.generate_stubs."""
import ast
import enum
import inspect
import re

_SIGNATURE = re.compile(r"^\s*(?P<name>\w+)\((?P<args>.*?)\)\s*(?:->\s*(?P<ret>[\w\[\], |.]+?))?\s*[:;]")
_FALLBACK = "(*args: Any, **kwargs: Any) -> Any"

# Container and view classes are generic in the stubs; they are written by hand because the runtime classes cannot say it.
_PRELUDE = '''from __future__ import annotations
from enum import IntEnum, IntFlag
from concurrent.futures import Future
from typing import Any, Callable, Generic, Iterator, TypeVar, overload

T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")

class Array(Generic[T]):
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[T]: ...
    @overload
    def __getitem__(self, index: int) -> T: ...
    @overload
    def __getitem__(self, index: slice) -> list[T]: ...
    def __setitem__(self, index: Any, value: Any) -> None: ...
    def __delitem__(self, index: Any) -> None: ...
    def __contains__(self, item: object) -> bool: ...
    def append(self, value: T) -> None: ...
    def extend(self, iterable: Any) -> None: ...
    def insert(self, index: int, value: T) -> None: ...
    def pop(self, index: int = -1) -> T: ...
    def clear(self) -> None: ...
    def to_list(self) -> list[T]: ...
    def is_valid(self) -> bool: ...

class FixedArray(Generic[T]):
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[T]: ...
    @overload
    def __getitem__(self, index: int) -> T: ...
    @overload
    def __getitem__(self, index: slice) -> list[T]: ...
    def __setitem__(self, index: int, value: T) -> None: ...
    def __contains__(self, item: object) -> bool: ...
    def to_list(self) -> list[T]: ...
    def is_valid(self) -> bool: ...

class Map(Generic[K, V]):
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[K]: ...
    def __getitem__(self, key: K) -> V: ...
    def __setitem__(self, key: K, value: V) -> None: ...
    def __delitem__(self, key: K) -> None: ...
    def __contains__(self, key: object) -> bool: ...
    def get(self, key: K, default: Any = None) -> Any: ...
    def keys(self) -> list[K]: ...
    def values(self) -> list[V]: ...
    def items(self) -> list[tuple[K, V]]: ...
    def update(self, other: Any) -> None: ...
    def pop(self, key: K, *default: Any) -> Any: ...
    def clear(self) -> None: ...
    def to_dict(self) -> dict[K, V]: ...
    def is_valid(self) -> bool: ...

class Set(Generic[T]):
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[T]: ...
    def __contains__(self, item: object) -> bool: ...
    def add(self, item: T) -> None: ...
    def discard(self, item: T) -> None: ...
    def remove(self, item: T) -> None: ...
    def update(self, items: Any) -> None: ...
    def clear(self) -> None: ...
    def to_list(self) -> list[T]: ...
    def is_valid(self) -> bool: ...
'''
_HAND_WRITTEN = {"Array", "FixedArray", "Map", "Set"}


_BUILTIN_TYPES = {"int", "str", "float", "bool", "list", "dict", "tuple", "set", "None", "Any", "object", "bytes", "type", "UEError"}


# Docstring signatures name parameters without types ("register_hook(path, pre=None, post=None)"). These parameter names mean the
# same thing wherever the module uses them, so they get their type here; any other parameter stays Any.
_PARAM_TYPES = {
    "path": "str", "class_path": "str", "class_name": "str", "name": "str", "address": "int", "id": "int", "limit": "int", "milliseconds": "float",
    "required_flags": "int", "banned_flags": "int", "exact_class": "bool", "inherited": "bool", "degrees": "bool", "order": "str", "async_": "bool",
    "callback": "Callable[..., Any]", "fn": "Callable[..., Any]", "pre": "Callable[..., Any]", "post": "Callable[..., Any]",
    "key": "Key | int", "modifiers": "tuple[ModifierKey | int, ...] | list[ModifierKey | int]", "object": "UObject", "instance": "UObject",
}
# Results of the module's functions whose docstring does not state one.
_RETURN_TYPES = {
    "is_game_thread": "bool", "reload": "None", "execute_in_game_thread": "None", "execute_with_delay": "None", "call_in_game_thread": "Future[Any]",
    "register_hook": "tuple[int, int]", "unregister_hook": "None", "register_replacement": "int", "unregister_replacement": "None",
    "register_key_down": "int", "unregister_key_down": "None", "notify_on_new_object": "int", "unregister_notify_on_new_object": "None",
    "bind_event": "int", "unbind_event": "None", "set_slow_callback_warning": "None",
}


def _typed(name, has_default):
    """`name: type` for a parameter without an annotation; a parameter with a default also accepts None."""
    kind = _PARAM_TYPES.get(name, "Any")
    if has_default and kind != "Any":
        kind += " | None"
    return f"{name}: {kind}"


def _clean_type(text, known):
    """The annotation text if every name in it is known, else Any (docstring return types are free text)."""
    names = set(re.findall(r"[A-Za-z_]\w*", text))
    return text if names <= (known | _BUILTIN_TYPES | {"T", "K", "V"}) else "Any"


def _signature_of(obj, method):
    """The parameter list of a Python function (names and which have defaults, all typed Any), or None for built-ins that do not tell."""
    target = getattr(obj, "__func__", obj)
    try:
        parameters = list(inspect.signature(target).parameters.values())
    except (TypeError, ValueError):
        return None
    if method and parameters and parameters[0].name in ("self", "cls"):
        parameters = parameters[1:]
    pieces, star = [], False
    for parameter in parameters:
        default = " = ..." if parameter.default is not inspect.Parameter.empty else ""
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            pieces.append(f"*{parameter.name}: Any")
            star = True
        elif parameter.kind is inspect.Parameter.VAR_KEYWORD:
            pieces.append(f"**{parameter.name}: Any")
        else:
            if parameter.kind is inspect.Parameter.KEYWORD_ONLY and not star:
                pieces.append("*")
                star = True
            pieces.append(_typed(parameter.name, bool(default)) + default)
    return ", ".join((["self"] if method else []) + pieces)


def _def_line(indent, name, doc, method=False, known=frozenset(), obj=None):
    """One `def` from a docstring signature; `self` is added for methods whose signature does not name it. Without a usable docstring the
    signature of a Python function is read from the function itself (the off-game module); built-ins get the generic one."""
    match = _SIGNATURE.match(doc or "")
    if match and match.group("name") == name:
        args = match.group("args").strip()
        ret = _clean_type((match.group("ret") or "Any").strip(), known)
        if ret == "Any" and not method:             # the docstring says nothing usable ("-> id"): the table knows the module's functions
            ret = _RETURN_TYPES.get(name, "Any")
        pieces = [a.strip() for a in args.split(",") if a.strip()] if args else []
        cleaned = []
        for piece in pieces:
            if piece.startswith(("*", "/")) or "=" in piece or ":" in piece:
                cleaned.append(piece if "=" not in piece or ":" in piece.split("=")[0] else piece.replace("=", ": Any = ", 1) if False else piece)
            else:
                cleaned.append(_typed(piece, False))
        # defaults written in the docstring (name=value) become `name: <type> | None = ...`
        cleaned = [re.sub(r"^(\w+)=.*$", lambda m: _typed(m.group(1), True) + " = ...", c) for c in cleaned]
        signature = ", ".join((["self"] if method else []) + cleaned)
        line = f"{indent}def {name}({signature}) -> {ret}: ..."
        try:
            ast.parse(line.strip() + "\n")
            return line
        except SyntaxError:
            pass   # an odd docstring: fall through to the generic signature
    signature = _signature_of(obj, method) if obj is not None else None
    if signature is not None:
        return f"{indent}def {name}({signature}) -> {'Any' if method else _RETURN_TYPES.get(name, 'Any')}: ..."
    return f"{indent}def {name}({'self, ' if method else ''}*args: Any, **kwargs: Any) -> Any: ..."


def make_base_stub(module):
    lines = ["# Generated by ue4ss.engine.generate_stubs from the live ue4ss module. Do not edit.", ""]
    classes = [(n, o) for n, o in vars(module).items() if inspect.isclass(o) and not n.startswith("_") and n not in _HAND_WRITTEN]
    functions = [(n, o) for n, o in vars(module).items() if callable(o) and not inspect.isclass(o) and not n.startswith("_") and n != "engine"]
    constants = [n for n, o in vars(module).items() if not n.startswith("_") and not callable(o) and not inspect.ismodule(o)]
    lines += _PRELUDE.splitlines() + [""]
    emitted = set(_HAND_WRITTEN)
    known = {n for n, _ in classes} | _HAND_WRITTEN

    def emit(name, cls):
        if name in emitted:
            return
        for base in cls.__bases__:
            if base.__name__ in dict(classes):
                emit(base.__name__, base)
        emitted.add(name)
        bases = [b.__name__ for b in cls.__bases__ if b is not object and (b.__name__ in dict(classes) or b.__name__ in ("IntEnum", "IntFlag", "Exception"))]
        if issubclass(cls, enum.Enum):
            base = "IntFlag" if issubclass(cls, enum.Flag) else "IntEnum"
            lines.append(f"class {name}({base}):")
            for member in cls:
                lines.append(f"    {member.name} = {int(member.value)}")
            lines.append("")
            return
        if name == "UObject":
            # The wrapper methods live in UObjectBase, which the generated engine classes derive from (unknown attributes are errors there);
            # a plain UObject, what find_* and static_find_object return, also allows any attribute.
            lines.append("class UObjectBase(object):")
        else:
            lines.append(f"class {name}({', '.join(bases) if bases else 'object'}):")
        body = 0
        for member, value in vars(cls).items():
            if member.startswith("_") or not callable(value) and not isinstance(value, (classmethod, staticmethod)):
                continue
            if inspect.isroutine(value) or inspect.ismethoddescriptor(value) or isinstance(value, (classmethod, staticmethod)):
                lines.append(_def_line("    ", member, getattr(value, "__doc__", None), method=True, known=known, obj=value))
                body += 1
        if name == "UObject":
            lines.append("")
            lines.append("class UObject(UObjectBase):")
            lines.append("    def __getattr__(self, name: str) -> Any: ...")
            lines.append("    def __setattr__(self, name: str, value: Any) -> None: ...")
            body += 1
        elif name == "Struct":
            lines.append("    def __getattr__(self, name: str) -> Any: ...")
            lines.append("    def __setattr__(self, name: str, value: Any) -> None: ...")
            body += 2
        if name == "FName":
            lines.append("    comparison_index: int")
            lines.append("    number: int")
            body += 2
        if not body:
            lines.append("    ...")
        lines.append("")

    for name, cls in classes:
        emit(name, cls)
    for name, func in sorted(functions):
        lines.append(_def_line("", name, getattr(func, "__doc__", None), known=known, obj=func))
    for name in sorted(constants):
        lines.append(f"{name}: Any")
    lines.append("def __getattr__(name: str) -> Any: ...")
    lines.append("")
    return "\n".join(lines)
