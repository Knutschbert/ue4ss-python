"""Engine classes, structs and enums as Python types, created on first use from the game's reflection data (nothing is generated
or read from disk).

    from ue4ss.engine import Actor, Pawn, GameplayStatics, Vector

    statics = GameplayStatics                       # static functions are called on the class
    controller = GameplayStatics.GetPlayerController(world, 0)
    isinstance(controller.Pawn, Pawn)               # True for any wrapped object whose engine class is a Pawn
    actors = Actor.find_all()                       # every live instance (and subclasses)
    Vector(X=1, Y=2, Z=3)                           # a new struct value

Design and the rejected alternatives: docs/TYPED_API.md. Objects keep their native `ue4ss.UObject` wrapper (fast, dynamic attributes);
the classes here mirror the engine's inheritance, answer isinstance for those wrappers, and carry the class-level API (static functions,
find_all). Names are looked up in /Script/Engine, /Script/CoreUObject and then every other package; a name that exists in several packages
must be asked for with its package: `ue4ss.engine.package("Engine").Actor`.
"""
import enum as _enum
import importlib.abc as _importlib_abc
import importlib.machinery as _importlib_machinery
import keyword as _keyword
import sys as _sys
import types as _types

import ue4ss as _ue

_PREFERRED_PACKAGES = ("Engine", "CoreUObject")
_classes = {}      # full engine path -> generated Python class
_enums = {}        # full engine path -> IntEnum


class EngineError(_ue.UEError):
    """A name that is not an engine class, struct or enum (or is ambiguous)."""


class _EngineMeta(type):
    """Metaclass of every generated class: isinstance against wrapped engine objects and class-level function calls."""

    def __instancecheck__(cls, obj):
        if isinstance(obj, _ue.UObject):
            try:
                return obj.is_a(cls._engine_path)
            except _ue.UEError:
                return False
        return type.__instancecheck__(cls, obj)

    def __getattr__(cls, name):
        # Static functions: GameplayStatics.GetPlayerController(...) runs the function on the class default object.
        if name.startswith("_"):
            raise AttributeError(name)
        uclass = cls.__dict__.get("_uclass")
        if uclass is None or uclass.find_function(name) is None:
            raise AttributeError(f"{cls.__name__} has no class-level function '{name}'")
        return getattr(uclass.get_cdo(), name)

    def __dir__(cls):
        names = set(type.__dir__(cls))
        uclass = cls.__dict__.get("_uclass")
        if uclass is not None:
            names.update(uclass.get_function_names())
        return sorted(names)

    def __repr__(cls):
        return f"<engine class {cls._engine_path}>"


class EngineObject(metaclass=_EngineMeta):
    """Root of the generated hierarchy (mirrors /Script/CoreUObject.Object). Not instantiable: objects come from the engine."""

    _engine_path = "/Script/CoreUObject.Object"

    def __new__(cls, *args, **kwargs):
        raise TypeError(f"cannot create '{cls.__name__}' instances: engine objects come from the engine (find_all(), calls, properties)")

    @classmethod
    def find_all(cls):
        """Every live instance of this class or a subclass."""
        return _ue.find_all_of(cls.__name__)

    @classmethod
    def find_first(cls):
        """One live instance of this class or a subclass, or None."""
        return _ue.find_first_of(cls.__name__)

    @classmethod
    def static_class(cls):
        """The engine class object (ue4ss.UClass)."""
        return cls._uclass

    @classmethod
    def default_object(cls):
        """The class default object: the template functions and properties are read from."""
        return cls._uclass.get_cdo()


_classes["/Script/CoreUObject.Object"] = EngineObject


def _short(path):
    return path.rsplit(".", 1)[-1]


def class_for(uclass):
    """The Python class for a ue4ss.UClass, created (with its parents) on first use."""
    path = _path_of(uclass)
    made = _classes.get(path)
    if made is not None and (made is EngineObject or made._uclass.is_valid()):
        return made          # a Blueprint class that was unloaded and loaded again is a new object: its Python class is made again
    parent = uclass.get_super()
    base = class_for(parent) if parent is not None else EngineObject
    name = uclass.get_fname().to_string()
    made = _EngineMeta(name, (base,), {"_engine_path": path, "_uclass": uclass, "__module__": "ue4ss.engine",
                                       "__doc__": f"Engine class {path}."})
    _classes[path] = made
    return made


def _path_of(obj):
    """Engine path 'Package.Name' of a class/struct/enum object: its full name without the leading type word."""
    return obj.get_full_name().split(" ", 1)[1]


class _StructMeta(type):
    def __call__(cls, *args, **kwargs):
        if args:
            raise TypeError(f"{cls.__name__}() takes keyword arguments naming the fields")
        return _ue.make_struct(cls._engine_path, **kwargs)

    def __instancecheck__(cls, obj):
        return isinstance(obj, _ue.Struct) and obj.get_struct_name() == cls.__name__

    def __repr__(cls):
        return f"<engine struct {cls._engine_path}>"


def struct_for(ustruct):
    path = _path_of(ustruct)
    made = _classes.get(path)
    if made is None:
        made = _StructMeta(ustruct.get_fname().to_string(), (), {"_engine_path": path, "_ustruct": ustruct, "__module__": "ue4ss.engine",
                                                                 "__doc__": f"Engine struct {path}."})
        _classes[path] = made
    return made


def enum_for(uenum):
    path = _path_of(uenum)
    made = _enums.get(path)
    if made is None:
        members = {name.rsplit("::", 1)[-1]: int(value) for name, value in uenum.get_names().items()}
        for name, value in list(members.items()):   # a member named like a Python keyword (EDrawDebugTrace::None) also gets the name the stubs use: None_
            if _keyword.iskeyword(name) or name in ("None", "True", "False"):
                members.setdefault(name + "_", value)
        made = _enum.IntEnum(uenum.get_fname().to_string(), members, module="ue4ss.engine")
        _enums[path] = made
    return made


def from_object(obj):
    """The Python type for a ue4ss class, struct or enum object."""
    if isinstance(obj, _ue.UEnum):
        return enum_for(obj)
    if isinstance(obj, _ue.UScriptStruct):
        return struct_for(obj)
    if isinstance(obj, _ue.UClass):
        return class_for(obj)
    raise EngineError(f"{obj!r} is not a class, struct or enum")


def find(path, load=True):
    """The type at a full engine path such as /Script/Engine.Actor or /Game/Folder/BP_Thing.BP_Thing_C. A Blueprint class ("..._C") that the
    game has not loaded yet is loaded (a short hitch) unless load=False."""
    obj = _ue.static_find_object(path)
    if obj is None and load and path.startswith(("/Game/", "/Engine/")) and path.endswith("_C"):
        try:
            obj = _ue.static_find_object("/Script/Engine.KismetSystemLibrary").LoadClassAsset_Blocking(path)
        except _ue.UEError:
            obj = None
    if obj is None:
        raise EngineError(f"no engine class, struct or enum at '{path}'")
    return from_object(obj)


class _Package:
    """ue4ss.engine.package('Engine').Actor: names inside one package, no ambiguity."""

    def __init__(self, name):
        self._name = name

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return find(f"/Script/{self._name}.{name}")

    def __repr__(self):
        return f"<engine package /Script/{self._name}>"


def package(name):
    return _Package(name)


def lookup(name):
    """The type with this short name: Engine and CoreUObject first, then any package (ambiguous names raise)."""
    for pkg in _PREFERRED_PACKAGES:
        obj = _ue.static_find_object(f"/Script/{pkg}.{name}")
        if obj is not None:
            return from_object(obj)
    found = []
    for kind in ("Class", "ScriptStruct", "Enum"):
        found += [o for o in _ue.find_objects(class_name=kind, name=name, exact_class=True) if "/Script/" in o.get_full_name()]
    if len(found) == 1:
        return from_object(found[0])
    if not found:
        raise EngineError(f"no engine class, struct or enum named '{name}'")
    raise EngineError(f"'{name}' exists in several packages: " + ", ".join(_path_of(o) for o in found) + "; use package('Name').{name}")


def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    return lookup(name)


def generate_stubs(folder, packages=None, blueprints=False, dependencies=True, load=None, load_limit=1000):
    """Write .pyi typing stubs for the running game into `folder` (see ue4ss.stubgen) and return a summary dict."""
    from ue4ss import stubgen
    return stubgen.generate_stubs(folder, packages, blueprints, dependencies, load, load_limit)


# --- importable submodules ------------------------------------------------------------------------------------------------------------
# The typings (generate_stubs) declare modules: ue4ss.engine.Engine, ue4ss.engine.TBL for the native packages and ue4ss.engine.Content.Game.Folder.Sub
# for the Blueprint classes of /Game/Folder/Sub. The same imports work in the game, so what an editor accepts also runs:
#     from ue4ss.engine.TBL import TBLGameMode
#     from ue4ss.engine.Content.Game.Interactables.World.Seatable import LevelActor_Chair_C
# A native module resolves a name in /Script/<Package>; a content module resolves a name in the asset folder (the class `Name_C` of the asset `Name`,
# a struct or an enum of that name, or `Name_C__Asset` for a class that an asset of another name defines). Blueprint classes are loaded on demand.
__path__ = []       # ue4ss.engine is a package for the import system; the finder below creates its submodules


class _NativeModule(_types.ModuleType):
    def __init__(self, fullname, package):
        super().__init__(fullname)
        self._package = package

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        try:
            return find(f"/Script/{self._package}.{name}")
        except EngineError:
            pass
        try:                         # a type that lives in another package is still found when its short name is unique (Quat is in CoreUObject, not Engine)
            return lookup(name)
        except EngineError:
            raise AttributeError(f"module '{self.__name__}' has no type named '{name}'") from None

    def __dir__(self):
        return ["__name__"]


class _ContentModule(_types.ModuleType):
    def __init__(self, fullname, folder):
        super().__init__(fullname)
        self.__path__ = []      # sub folders are submodules
        self._folder = folder   # "/Game/Interactables/World/Seatable", "" above the mounts

    def __getattr__(self, name):
        if name.startswith("_") or not self._folder.count("/"):
            raise AttributeError(f"module '{self.__name__}' has no type named '{name}'")
        short, _, asset = name.partition("__")
        candidates = [asset] if asset else ([short[:-2], short] if short.endswith("_C") else [short])
        for candidate in candidates:
            try:
                return find(f"{self._folder}/{candidate}.{short}")
            except EngineError:
                continue
        raise AttributeError(f"module '{self.__name__}' has no type named '{name}' (asset folder {self._folder})")

    def __dir__(self):
        return ["__name__"]


_folder_cache = {}


def _folder_exists(folder):
    """Is there any content under this folder ("/Game/Interactables")? The asset registry knows (assets of the build, loaded or not); without a registry the
    loaded objects decide. Needed because `from ue4ss.engine.Content.Game import Typo` also asks for a submodule Typo, which must not exist."""
    if folder not in _folder_cache:
        found = False
        helpers = _ue.static_find_object("/Script/AssetRegistry.AssetRegistryHelpers")
        if helpers is not None:
            try:
                _, assets = helpers.GetAssetRegistry().GetAssetsByPath(folder, True, False)
                found = len(assets) > 0
            except _ue.UEError:
                helpers = None
        if helpers is None:
            prefix = folder + "/"
            found = any(o.get_full_name().split(" ", 1)[-1].startswith(prefix) for o in _ue.find_all_of("Object"))
        _folder_cache[folder] = found
    return _folder_cache[folder]


class _EngineModuleFinder(_importlib_abc.MetaPathFinder, _importlib_abc.Loader):
    """Creates ue4ss.engine.<NativePackage> and ue4ss.engine.Content.<mount>.<folder>... on demand."""

    def find_spec(self, fullname, path=None, target=None):
        prefix = __name__ + "."
        if not fullname.startswith(prefix):
            return None
        parts = fullname[len(prefix):].split(".")
        if parts[0] == "Content":
            if len(parts) <= 2 or _folder_exists("".join("/" + p for p in parts[1:])):       # Content and the mounts (Game, Engine, plugins) always; folders must exist
                return _importlib_machinery.ModuleSpec(fullname, self, is_package=True)
            return None
        if len(parts) == 1 and _ue.static_find_object(f"/Script/{parts[0]}") is not None:
            return _importlib_machinery.ModuleSpec(fullname, self)
        return None

    def create_module(self, spec):
        parts = spec.name[len(__name__) + 1:].split(".")
        if parts[0] == "Content":
            return _ContentModule(spec.name, "".join("/" + p for p in parts[1:]))
        return _NativeModule(spec.name, parts[0])

    def exec_module(self, module):
        pass


if not any(isinstance(finder, _EngineModuleFinder) for finder in _sys.meta_path):
    _sys.meta_path.append(_EngineModuleFinder())
