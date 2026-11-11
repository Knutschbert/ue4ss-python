"""Writes .pyi typing stubs for the running game from its reflection data (docs/TYPED_API.md). Nothing here reads a file of the game or an
SDK dump: classes, structs, enums, properties, functions and parameters come from the live engine through ue4ss, so the stubs describe
exactly the game (and mods, and loaded Blueprint classes) that is running.

    from ue4ss import engine
    engine.generate_stubs(r"C:\\typings")        # writes C:\\typings\\ue4ss\\...; add C:\\typings to the editor's stub path

Layout of the output folder:
    ue4ss/__init__.pyi            the base API (UObject, Struct, find_*, hooks, ...), made from the docstrings of the built-in module
    ue4ss/engine/__init__.pyi     re-exports every generated package and the helper functions of ue4ss.engine
    ue4ss/engine/<Package>.pyi    one file per engine package (Engine, CoreUObject, <the game's module>, ...): classes, structs, enums
    ue4ss/py.typed
"""
import keyword
import os
import re
import time

import ue4ss as _ue

FUNC_STATIC = 0x00002000
_PRIMITIVES = {
    "BoolProperty": "bool", "FloatProperty": "float", "DoubleProperty": "float", "StrProperty": "str", "TextProperty": "str",
    "NameProperty": "_ue.FName", "SoftObjectProperty": "str", "SoftClassProperty": "str",
    "Int8Property": "int", "Int16Property": "int", "IntProperty": "int", "Int64Property": "int", "ByteProperty": "int",
    "UInt16Property": "int", "UInt32Property": "int", "UInt64Property": "int",
}
_INT_PROPERTIES = {"Int8Property", "Int16Property", "IntProperty", "Int64Property", "ByteProperty", "UInt16Property", "UInt32Property", "UInt64Property"}
_NOT_TYPES = ("Default__", "SKEL_", "REINST_", "PLACEHOLDER")


_name_overrides = {}     # path -> stub name, for Blueprint classes whose short name occurs more than once in their package (set by _Collector)


def _package_and_name(path):
    """'/Script/Engine.Actor' -> ('Engine', 'Actor'). A Blueprint class lives in a module named like its folder: '/Game/Characters/BP_X.BP_X_C' ->
    ('Content.Game.Characters', 'BP_X_C') (file ue4ss/engine/Content/Game/Characters/__init__.pyi, the content browser path). Two Blueprint classes of
    the same name in one folder are called 'BP_X_C__Asset' (the asset that defines them), so no stub shadows another."""
    head, _, name = path.rpartition(".")
    if path in _name_overrides:
        name = _name_overrides[path]
    return _package_and_name_of(head), name


def _package_and_name_of(head):
    parts = [p for p in head.split("/") if p]
    if len(parts) > 1 and parts[0] == "Script":
        return parts[1]
    if len(parts) > 1:                                 # /Game/Folder/Asset: the asset's own name is the last part
        return "Content." + ".".join(parts[:-1])
    return "Content." + parts[0] if parts else "Unknown"


def _is_content(package):
    return package.startswith("Content.")


def _module_name(package):
    """'Content.Game.Weapons' -> 'Content.Game.Weapons' with every part made a valid identifier; native packages are one part."""
    return ".".join(_identifier(part) for part in package.split("."))


def _alias_of_module(package):
    return "_" + _module_name(package).replace(".", "_")


def _package_matches(package, wanted):
    """Does a requested package name select this module? "Game" selects everything under /Game, "Game.Weapons" one folder, "Engine" the native package."""
    return any(package == w or package == "Content." + w or package.startswith("Content." + w + ".") for w in wanted)


def _identifier(name):
    """A valid Python name: keywords and bad characters get a trailing underscore / are replaced."""
    clean = re.sub(r"\W", "_", name)
    if not clean or clean[0].isdigit():
        clean = "_" + clean
    return clean + "_" if keyword.iskeyword(clean) or clean in ("None", "True", "False") else clean


class _Imports:
    """What one stub file imports: names from other packages' files, and packages imported as modules for qualified references."""

    def __init__(self):
        self.names = {}
        self.modules = set()


class _Collector:
    """Everything to write: the types of each package, and which short names are unique across all packages."""

    def __init__(self, packages, blueprints):
        self.classes, self.structs, self.enums = {}, {}, {}      # path -> UObject
        wanted = None if packages is None else set(packages)
        for kind, target in (("Class", self.classes), ("ScriptStruct", self.structs), ("Enum", self.enums)):
            for obj in _ue.find_objects(class_name=kind, exact_class=True):
                path = obj.get_full_name().split(" ", 1)[1]
                if "." not in path or path.startswith("/Engine/Transient") or any(mark in path for mark in _NOT_TYPES):
                    continue
                package, _ = _package_and_name(path)
                is_script = path.startswith("/Script/")
                if not is_script and not blueprints:          # classes, structs and enums of the content (made in Blueprint editors) come with blueprints=True
                    continue
                if wanted is not None and not _package_matches(package, wanted):
                    continue
                target[path] = obj
        if blueprints:
            # Blueprint classes are objects of BlueprintGeneratedClass (or WidgetBlueprintGeneratedClass, ...), not of Class, so the loop above
            # never sees them; the search for the base type includes its subtypes and skips default objects.
            skip = _ue.ObjectFlags.RF_ClassDefaultObject | _ue.ObjectFlags.RF_ArchetypeObject
            for obj in _ue.find_objects(class_name="BlueprintGeneratedClass", banned_flags=skip):
                path = obj.get_full_name().split(" ", 1)[1]
                if "." not in path or path.startswith(("/Script/", "/Engine/Transient")) or any(mark in path for mark in _NOT_TYPES):
                    continue
                if wanted is None or _package_matches(_package_and_name(path)[0], wanted):
                    self.classes[path] = obj
        _name_overrides.clear()
        groups = {}
        for path in self.classes:
            head, _, short = path.rpartition(".")
            groups.setdefault((_package_and_name_of(head), short), []).append(path)
        for (_, short), same in groups.items():
            if len(same) > 1:                  # two assets of one folder define a class of this name: say which asset
                for path in same:
                    _name_overrides[path] = f"{short}__{_identifier(path.rpartition('.')[0].rsplit('/', 1)[-1])}"
        self.by_name = {}
        for path in list(self.classes) + list(self.structs) + list(self.enums):
            package, name = _package_and_name(path)
            self.by_name.setdefault(name, set()).add(package)
        self.paths = set(self.classes) | set(self.structs) | set(self.enums)
        self.avoid = set()   # member names of the class being written
        self.unresolved = {}  # path of a referenced type that has no stub -> number of references (they are written as Any)

    def reference(self, path, current_package, imports):
        """How a stub in `current_package` names the type at `path`; records the needed import. Unknown paths become Any. The module
        qualified form (_Package.Name) is used when the short name is ambiguous or when the class being written has a member of that
        name (a property `FrameNumber: FrameNumber` would otherwise shadow its own type)."""
        if path not in self.paths:
            if path:
                self.unresolved[path] = self.unresolved.get(path, 0) + 1     # a type the typings do not contain (a class that is not loaded, a filtered package)
            return "Any"
        package, name = _package_and_name(path)
        qualified = len(self.by_name[name]) > 1 or _identifier(name) in self.avoid
        if not qualified:
            if package == current_package:
                return _identifier(name)
            imports.names.setdefault(package, set()).add(_identifier(name))
            return _identifier(name)
        imports.modules.add(package)
        return f"{_alias_of_module(package)}.{_identifier(name)}"


def _annotation(desc, collector, package, imports, optional_objects=True, accept_plain=False):
    """The annotation for a type descriptor from get_properties() / get_params()."""
    kind = desc["type"]
    dim = desc.get("array_dim", 1)
    inner = desc.get("inner") or []
    if kind in _INT_PROPERTIES and desc.get("enum"):
        text = collector.reference(desc["enum"], package, imports)
        text = text if text != "Any" else "int"
    elif kind == "EnumProperty":
        text = collector.reference(desc["enum"], package, imports) if desc.get("enum") else "int"
        text = text if text != "Any" else "int"
    elif kind in _PRIMITIVES:
        text = _PRIMITIVES[kind]
    elif kind == "StructProperty":
        text = collector.reference(desc.get("struct") or "", package, imports)
        if accept_plain and text != "Any":
            text += " | dict[str, Any]"          # a struct argument may be a dict of field values
    elif kind in ("ObjectProperty", "WeakObjectProperty", "LazyObjectProperty", "InterfaceProperty", "SoftObjectProperty_"):
        base = collector.reference(desc.get("class") or "", package, imports)
        if accept_plain and base != "Any":
            base += " | _ue.UObject"          # an argument may also be a plain object from find_* / static_find_object
        text = f"{base} | None" if optional_objects else base
    elif kind in ("ClassProperty",):
        text = "_ue.UClass | None"
    elif kind == "ArrayProperty" and inner:
        element = _annotation(inner[0], collector, package, imports, False)
        text = f"_ue.Array[{element}]" + (f" | list[{element}] | tuple[{element}, ...]" if accept_plain else "")
    elif kind == "SetProperty" and inner:
        element = _annotation(inner[0], collector, package, imports, False)
        text = f"_ue.Set[{element}]" + (f" | set[{element}]" if accept_plain else "")
    elif kind == "MapProperty" and len(inner) == 2:
        key, value = _annotation(inner[0], collector, package, imports, False), _annotation(inner[1], collector, package, imports, False)
        text = f"_ue.Map[{key}, {value}]" + (f" | dict[{key}, {value}]" if accept_plain else "")
    elif kind in ("DelegateProperty", "MulticastInlineDelegateProperty", "MulticastSparseDelegateProperty"):
        text = "Any"
    else:
        text = "Any"
    if dim and dim > 1:
        text = f"_ue.FixedArray[{text}]"
    return text


def _function_stub(func, owner_path, collector, package, imports):
    """`def` lines for one engine function. Static functions are classmethods; outputs and the return value become the result (a tuple for several)."""
    try:
        params = func.get_params()
        flags = func.get_function_flags()
    except _ue.UEError:
        return []
    name = func.get_fname().to_string()
    inputs, outputs, result = [], [], None
    for param in params:
        annotation = _annotation(param, collector, package, imports, accept_plain=not (param["is_return"] or (param["is_out"] and not param["is_const"])))
        if param["is_return"]:
            result = annotation
        elif param["is_out"] and not param["is_const"] and not (param["is_reference"] and not param["is_out"]):
            outputs.append(annotation)
            if param["is_reference"]:
                inputs.append((_identifier(param["name"]), annotation))   # in/out: also an input
        else:
            inputs.append((_identifier(param["name"]), annotation))
    results = ([result] if result else []) + outputs
    returns = "None" if not results else results[0] if len(results) == 1 else "tuple[" + ", ".join(results) + "]"
    static = bool(flags & FUNC_STATIC)
    args = ", ".join([("cls" if static else "self")] + [f"{n}: {a}" for n, a in inputs])
    method = _identifier(name)
    lines = []
    if static:
        lines.append("    @classmethod")
    if method != name:
        lines.append(f"    # engine name: {name} (a Python keyword): call it with getattr")
    lines.append(f"    def {method}({args}) -> {returns}: ...")
    return lines


def _class_stub(path, uclass, collector, package, imports):
    name = _identifier(_package_and_name(path)[1])
    parent = uclass.get_super()
    parent_path = parent.get_full_name().split(" ", 1)[1] if parent is not None else None
    base = collector.reference(parent_path, package, imports) if parent_path else "_ue.UObjectBase"
    if base == "Any":
        base = "_ue.UObjectBase"
    own_fields = uclass.get_properties(inherited=False)
    declared = {fn.get_fname().to_string(): fn for fn in _own_functions(uclass, path)}
    collector.avoid = {_identifier(f["name"]) for f in own_fields} | {_identifier(n) for n in declared}
    lines = [f"class {name}({base}):", f'    """Engine class {path}."""']
    # Engine names that are not identifiers (Blueprint variables with spaces) are declared under their identifier form, which the runtime accepts.
    # Two members of the same form ('Sun height' and 'Sun_height') cannot both be declared: only a member whose name IS the identifier is, the others
    # are listed in a comment and reachable with getattr.
    ambiguous = []
    claims = {}
    for member in [f["name"] for f in own_fields] + sorted(declared):
        claims.setdefault(_identifier(member), []).append(member)
    blocked = {ident for ident, members in claims.items() if len(members) > 1}
    for field in own_fields:
        ident = _identifier(field["name"])
        if ident in blocked and field["name"] != ident:
            ambiguous.append(field["name"])
            continue
        lines.append(f"    {ident}: {_annotation(field, collector, package, imports)}")
    for fname in sorted(declared):
        if _identifier(fname) in blocked and fname != _identifier(fname):
            ambiguous.append(fname)
            continue
        lines += _function_stub(declared[fname], path, collector, package, imports)
    if ambiguous:
        lines.append("    # not declared, their identifier form is ambiguous (use getattr/setattr): " + ", ".join(repr(a) for a in ambiguous))
    if path == "/Script/CoreUObject.Object":      # the class-level API every engine class inherits
        lines += ["    @classmethod", "    def find_all(cls) -> list[Self]: ...", "    @classmethod", "    def find_first(cls) -> Self | None: ...",
                  "    @classmethod", "    def static_class(cls) -> _ue.UClass: ...", "    @classmethod", "    def default_object(cls) -> Self: ..."]
    collector.avoid = set()
    if len(lines) == 2:
        lines.append("    ...")
    return lines


def _own_functions(uclass, path):
    own = []
    for fname in uclass.get_function_names():
        func = uclass.find_function(fname)
        if func is None:
            continue
        outer = func.get_outer()
        if outer is not None and outer.get_full_name().split(" ", 1)[-1] == path:
            own.append(func)
    return own


def _struct_stub(path, ustruct, collector, package, imports):
    name = _identifier(_package_and_name(path)[1])
    lines = [f"class {name}(_ue.Struct):", f'    """Engine struct {path}."""']
    fields = ustruct.get_properties(inherited=True)
    collector.avoid = {_identifier(f["name"]) for f in fields}
    for field in fields:
        lines.append(f"    {_identifier(field['name'])}: {_annotation(field, collector, package, imports)}")
    args = ", ".join(f"{_identifier(f['name'])}: {_annotation(f, collector, package, imports)} = ..." for f in fields)
    lines.append(f"    def __init__(self, *, {args}) -> None: ..." if fields else "    def __init__(self) -> None: ...")
    collector.avoid = set()
    return lines


def _enum_stub(path, uenum):
    name = _identifier(_package_and_name(path)[1])
    lines = [f"class {name}(IntEnum):", f'    """Engine enum {path}."""']
    seen = set()
    for member, value in sorted(uenum.get_names().items(), key=lambda item: item[1]):
        member = _identifier(member.rsplit("::", 1)[-1])
        if member in seen:
            continue
        seen.add(member)
        lines.append(f"    {member} = {int(value)}")
    if len(lines) == 2:
        lines.append("    ...")
    return lines


def _order_classes(paths, collector):
    """Parents before children inside one file (stubs do not need it, but readers do)."""
    depth = {}

    def of(path):
        if path in depth:
            return depth[path]
        depth[path] = 0
        parent = collector.classes[path].get_super()
        if parent is not None:
            parent_path = parent.get_full_name().split(" ", 1)[1]
            if parent_path in collector.classes:
                depth[path] = of(parent_path) + 1
        return depth[path]

    return sorted(paths, key=lambda p: (of(p), p))


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _blueprint_assets(folder):
    """Object paths of the Blueprint assets under a content folder such as "/Game/Weapons", loaded or not, from the engine's asset registry."""
    helpers = _ue.static_find_object("/Script/AssetRegistry.AssetRegistryHelpers")
    if helpers is None:
        raise RuntimeError("the asset registry is not available in this game")
    ok, assets = helpers.GetAssetRegistry().GetAssetsByPath(folder, True, False)
    return [str(asset.ObjectPath) for asset in assets if str(asset.AssetClass).endswith("Blueprint")]


def load_blueprints(folders, limit=1000):
    """Load the Blueprint classes of the assets under the given content folders (["/Game/Weapons"]) so that generate_stubs sees them. Loading a class can
    take a moment and uses memory; `limit` caps how many are loaded in this call. Returns {"found", "already", "loaded", "failed", "skipped"}."""
    result = {"found": 0, "already": 0, "loaded": 0, "failed": 0, "skipped": 0}
    library = _ue.static_find_object("/Script/Engine.KismetSystemLibrary")
    for folder in folders:
        for object_path in _blueprint_assets(folder):
            result["found"] += 1
            class_path = object_path + "_C"
            if _ue.static_find_object(class_path) is not None:
                result["already"] += 1
            elif result["loaded"] + result["failed"] >= limit:
                result["skipped"] += 1
            else:
                try:
                    loaded = library.LoadClassAsset_Blocking(class_path)
                except _ue.UEError:
                    loaded = None
                result["loaded" if loaded is not None else "failed"] += 1
    return result


def generate_stubs(folder, packages=None, blueprints=False, dependencies=True, load=None, load_limit=1000):
    """Write the stubs for the running game into `folder` and return a summary dict (counts, files, seconds, and how many referenced types had no stub).
    packages: names such as ["Engine", "CoreUObject", "TBL"] (default: every /Script package that has classes, structs or enums).
    blueprints: also include the Blueprint classes that are loaded right now (package "Game").
    load: content folders (["/Game/Weapons"]) whose Blueprint classes are loaded first, so classes the current map has not loaded are typed too (needs
    blueprints=True; see load_blueprints; at most `load_limit` classes are loaded). The result is in summary["load"].
    dependencies: with `packages`, also write every package that the requested ones import (base classes, structs, enums they use), so the stubs of
    ["TBL"] resolve their Engine types. False writes exactly the requested packages and leaves references to the others as Any."""
    started = time.perf_counter()
    loaded = load_blueprints(load, load_limit) if (blueprints and load) else None
    collector = _Collector(None if dependencies else packages, blueprints)
    by_package = {}
    for kind, items in (("classes", collector.classes), ("structs", collector.structs), ("enums", collector.enums)):
        for path in items:
            by_package.setdefault(_package_and_name(path)[0], {"classes": [], "structs": [], "enums": []})[kind].append(path)
    ue_root = os.path.join(folder, "ue4ss")
    engine_dir = os.path.join(ue_root, "engine")
    files, errors = [], []
    content_markers = set()
    queue = sorted(by_package) if packages is None else sorted(p for p in by_package if _package_matches(p, packages))
    written = []
    while queue:
        package = queue.pop(0)
        if package in written:
            continue
        written.append(package)
        entry = by_package[package]
        imports, body = _Imports(), []
        for path in sorted(entry["enums"]):
            body += _enum_stub(path, collector.enums[path]) + ["", ""]
        for path in sorted(entry["structs"]):
            try:
                body += _struct_stub(path, collector.structs[path], collector, package, imports) + ["", ""]
            except Exception as e:  # noqa: BLE001
                errors.append((path, repr(e)))
        for path in _order_classes(entry["classes"], collector):
            try:
                body += _class_stub(path, collector.classes[path], collector, package, imports) + ["", ""]
            except Exception as e:  # noqa: BLE001
                errors.append((path, repr(e)))
        if dependencies and packages is not None:
            queue += [other for other in sorted(set(imports.names) | imports.modules) if other in by_package and other not in written]
        header = ["# Generated by ue4ss.engine.generate_stubs from the running game's reflection data. Do not edit.",
                  "from __future__ import annotations", "from enum import IntEnum", "from typing import Any, Self", "", "import ue4ss as _ue"]
        for other in sorted(imports.names):
            header.append(f"from ue4ss.engine.{_module_name(other)} import {', '.join(sorted(imports.names[other]))}")
        for other in sorted(imports.modules):
            header.append(f"import ue4ss.engine.{_module_name(other)} as {_alias_of_module(other)}")
        header += ["", ""]
        # UObject is the root: the generated Object class replaces it inside CoreUObject
        text = "\n".join(header + body)
        if _is_content(package):
            target = os.path.join(engine_dir, *_module_name(package).split("."), "__init__.pyi")
            for depth in range(1, len(package.split("."))):                 # every folder on the way is a package
                marker = os.path.join(engine_dir, *_module_name(package).split(".")[:depth], "__init__.pyi")
                if not os.path.exists(marker) and marker not in content_markers:
                    content_markers.add(marker)
        else:
            target = os.path.join(engine_dir, _identifier(package) + ".pyi")
        _write(target, text)
        files.append(target)
    init_lines = ["# Generated by ue4ss.engine.generate_stubs. Do not edit.", "from typing import Any", ""]
    for package in sorted(written):
        if not _is_content(package):             # Blueprint modules are imported by their path, not star-imported (thousands of names)
            init_lines.append(f"from .{_identifier(package)} import *")
    init_lines += ["", "class EngineError(Exception): ...", "def find(path: str) -> Any: ...", "def package(name: str) -> Any: ...",
                   "def lookup(name: str) -> Any: ...", "def from_object(obj: Any) -> Any: ...", "def class_for(uclass: Any) -> Any: ...",
                   "def generate_stubs(folder: str, packages: list[str] | None = ..., blueprints: bool = ..., dependencies: bool = ..., load: list[str] | None = ..., load_limit: int = ...) -> dict[str, Any]: ...",
                   "def __getattr__(name: str) -> Any: ...", ""]
    _write(os.path.join(engine_dir, "__init__.pyi"), "\n".join(init_lines))
    from ue4ss import basestubs
    _write(os.path.join(ue_root, "__init__.pyi"), basestubs.make_base_stub(_ue))
    _write(os.path.join(ue_root, "py.typed"), "")
    for marker in sorted(content_markers):
        if marker not in files:
            _write(marker, "")                    # the folder packages between engine/ and the modules that hold classes
            files.append(marker)
    included = [path for items in (collector.classes, collector.structs, collector.enums) for path in items if _package_and_name(path)[0] in written]
    return {"folder": folder, "packages": len(written), "classes": len([p for p in collector.classes if _package_and_name(p)[0] in written]),
            "structs": len([p for p in collector.structs if _package_and_name(p)[0] in written]),
            "enums": len([p for p in collector.enums if _package_and_name(p)[0] in written]), "files": len(files) + 3, "errors": errors, "seconds": round(time.perf_counter() - started, 2),
            "unresolved_types": len(collector.unresolved), "unresolved_references": sum(collector.unresolved.values()),
            "unresolved_top": sorted(collector.unresolved.items(), key=lambda item: -item[1])[:10], "load": loaded}
