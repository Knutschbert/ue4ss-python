"""Pure-Python stand-in for the built-in ``ue4ss`` module.

Lets scripts and tools be imported and unit tested outside the game
(``PYTHONPATH=python/mock``). It mirrors the public surface of the real module, which grows
with the bindings; ``tests/test_mock_ue4ss.py`` keeps the parts that exist today in step.
"""

import concurrent.futures as _futures
import enum as _enum
import itertools as _itertools
import os as _os
import sys as _sys
import time as _time
import traceback as _traceback

__version__ = "0.1"  # must equal core/include/pyue/version.hpp (checked by the tests)


class UEError(Exception):
    """Base class for errors raised by the UE4SS bindings."""


class InvalidObjectError(UEError):
    """The Unreal object was destroyed, garbage collected or its slot was reused."""


def _selftest_raise(kind):
    """Internal: raise the Python exception the real module produces for a C++ exception kind."""
    if not isinstance(kind, str):
        raise TypeError("kind must be a str")
    if kind == "none":
        return None
    if kind == "ue":
        raise UEError("test UEError")
    if kind == "invalid":
        raise InvalidObjectError("test InvalidObjectError")
    if kind == "property":
        raise AttributeError("test PropertyNotFoundError")
    if kind == "std":
        raise UEError("internal error: test std::runtime_error")
    if kind == "memory":
        raise MemoryError()
    if kind == "unknown":
        raise UEError("internal error: unknown C++ exception")
    raise ValueError(f"unknown selftest kind: {kind}")


# --- game-thread scheduling -----------------------------------------------------------------
# The mock has no game thread: tasks queue up and run when the test calls _mock_run_pending().
# Names starting with _mock_ exist only here.

_pending = []  # (due, sequence, fn)
# the shared pure-Python submodules (ue4ss.engine) live in python/lib/ue4ss and are shipped in the mod's dlls folder
_lib_folder = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))), "lib", "ue4ss")
if _os.path.isdir(_lib_folder):
    __path__.append(_lib_folder)

_sequence = _itertools.count()
_in_game_thread = False
_mock_now = _time.monotonic  # tests replace this to control time


def _check_callable(fn):
    if not callable(fn):
        raise TypeError("callback must be callable")


def execute_in_game_thread(fn):
    """Run fn() on the game thread at a later tick. Any thread."""
    _check_callable(fn)
    _pending.append((0.0, next(_sequence), fn))


def execute_with_delay(milliseconds, fn):
    """Run fn() on the game thread after at least that delay (accurate to one frame)."""
    milliseconds = float(milliseconds)
    if not milliseconds >= 0:
        raise ValueError("delay must be a non-negative number of milliseconds")
    _check_callable(fn)
    _pending.append((_mock_now() + milliseconds / 1000.0, next(_sequence), fn))


_script_generation = 1
_reload_pending = False
_mock_main = None   # tests may set a callable that stands in for "run main.py"


def script_generation():
    """script_generation() -> int: 1 while the first run of main.py is current, plus one for every reload."""
    return _script_generation


def reload():
    """reload(): at the next game tick, unregister every hook, replacement, key binding and new-object notification, drop queued and delayed tasks and the modules imported from python_scripts, clear the names main.py defined and run main.py again. Python threads you started keep running (check script_generation() in them). Several calls before the tick make one reload."""
    global _reload_pending
    if _reload_pending:
        return None
    _reload_pending = True
    _pending.append((0.0, next(_sequence), _do_reload))
    return None


def _result_parameter(params):
    """Name of the parameter that carries a function's result: the return parameter or, for a function without one, its only pure output
    (Blueprint functions return through output pins, which are plain out parameters). None if there is no result."""
    for p in params:
        if p["is_return"]:
            return p["name"]
    outs = [p["name"] for p in params if p["is_out"] and not p["is_reference"]]
    return outs[0] if len(outs) == 1 else None


def _do_reload():
    """Runs from the task queue, like the real reload (the queue is emptied first, the main.py stand-in runs afterwards)."""
    global _reload_pending, _script_generation
    _reload_pending = False
    _script_generation += 1
    _pending.clear()                    # queued and delayed tasks of the old scripts (this one has already been taken)
    _hook_records.clear()
    _replacements.clear()
    _new_object_subscriptions.clear()
    _key_bindings.clear()
    if _mock_main is not None:
        _mock_main()


def is_game_thread():
    """True when called on the game thread."""
    return _in_game_thread


def call_in_game_thread(fn, *args, **kwargs):
    """Run fn(*args, **kwargs) on the game thread; returns a concurrent.futures.Future for the result."""
    if not callable(fn):
        raise TypeError("fn must be callable")
    future = _futures.Future()

    def run():
        if not future.set_running_or_notify_cancel():
            return
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as e:
            future.set_exception(e)

    execute_in_game_thread(run)
    return future


def _mock_run_pending():
    """Run every task that is due now, earliest first, as the engine tick would.

    Like the real module: exceptions are logged to stderr and never propagate, and tasks queued
    while running wait for the next call. Returns the number of tasks run.
    """
    global _in_game_thread
    now = _mock_now()
    due = sorted((item for item in _pending if item[0] <= now), key=lambda item: (item[0], item[1]))
    for item in due:
        _pending.remove(item)
    _in_game_thread = True
    try:
        for _, _, fn in due:
            try:
                fn()
            except SystemExit:
                print("sys.exit() inside a callback is ignored", file=_sys.stderr)
            except BaseException:
                _traceback.print_exc()
    finally:
        _in_game_thread = False
    return len(due)


def _mock_reset():
    """Forget queued tasks (test isolation)."""
    global _in_game_thread
    global _script_generation, _reload_pending, _mock_main
    _pending.clear()
    _in_game_thread = False
    _script_generation = 1
    _reload_pending = False
    _mock_main = None


# --- FName ----------------------------------------------------------------------------------
# Mirrors the engine's name table: names are case-insensitive, "Foo_3" is index("Foo") with number 4,
# and index 0 is "None". Indices differ from the engine's (they are process specific there too).

_name_spellings = ["None"]  # canonical spelling of each base name, by index
_name_lookup = {"none": 0}  # lower-cased base name -> index


def _split_name(text):
    """'Foo_3' -> ('Foo', 4). Only a trailing _<digits> without leading zeros counts (as in the engine)."""
    head, sep, tail = text.rpartition("_")
    if sep and head and tail.isascii() and tail.isdigit() and (tail == "0" or tail[0] != "0") and len(tail) <= 9:
        return head, int(tail) + 1
    return text, 0


def _name_id(text, add):
    base, number = _split_name(text)
    index = _name_lookup.get(base.lower())
    if index is None:
        if not add:
            return None
        index = len(_name_spellings)
        _name_spellings.append(base)
        _name_lookup[base.lower()] = index
    return index, number


class FName:
    """An Unreal name. FName('Foo_3') adds the name if it does not exist; FName.find('Foo_3') does not."""

    __slots__ = ("_index", "_number")

    def __init__(self, text):
        if isinstance(text, FName):
            self._index, self._number = text._index, text._number
        elif isinstance(text, str):
            self._index, self._number = _name_id(text, add=True)
        else:
            raise TypeError(f"FName() argument must be str or FName, not {type(text).__name__}")

    @classmethod
    def find(cls, text):
        """FName.find(text) -> FName | None: an existing name, never adds one."""
        if not isinstance(text, str):
            raise TypeError(f"expected str, not {type(text).__name__}")
        found = _name_id(text, add=False)
        if found is None:
            return None
        name = cls.__new__(cls)
        name._index, name._number = found
        return name

    def to_string(self):
        base = _name_spellings[self._index]
        return base if self._number == 0 else f"{base}_{self._number - 1}"

    __str__ = to_string

    def __repr__(self):
        return f"FName({self.to_string()!r})"

    @property
    def comparison_index(self):
        return self._index

    @property
    def number(self):
        return self._number

    def __eq__(self, other):
        if isinstance(other, FName):
            return (self._index, self._number) == (other._index, other._number)
        if isinstance(other, str):
            found = _name_id(other, add=False)
            return found == (self._index, self._number)
        return NotImplemented

    def __hash__(self):
        return hash((self._index, self._number))


def _selftest_fstring(text):
    """Internal: round-trip a str through an Unreal FString (identity here)."""
    if not isinstance(text, str):
        raise TypeError("expected str, not " + type(text).__name__)
    return text


def _selftest_thread_probe(start):
    """Internal: which OS threads reach which engine entry points (nothing is observed here)."""
    import threading
    names = ("init", "tick", "process_event", "static_construct", "object_created", "object_deleted")
    return {"sites": {n: {"calls": 0, "threads": []} for n in names}, "calling_thread": threading.get_ident(), "game_thread": 0}


def _selftest_objects():
    """Internal: check the object registry against a real engine object (all checks pass here)."""
    return {
        "object_name": "Class /Script/CoreUObject.Object",
        "resolved": True,
        "stale_rejected": True,
        "slot_mismatch_rejected": True,
        "out_of_range_rejected": True,
        "null_rejected": True,
        "destroyed_message_ok": True,
        "deleted_total": 0,
        "deleted_on_game_thread": 0,
        "deleted_off_game_thread": 0,
        "deleted_before_game_thread_known": 0,
    }


# --- UObject --------------------------------------------------------------------------------
# A small fake object model: classes with parents, objects with outers, and destruction that makes
# existing wrappers invalid, like the real handle check. Names starting with _mock_ exist only here.

_mock_enforce_game_thread = False  # tests set this to see the real module's game-thread requirement
_object_counter = _itertools.count(1)


class Key(_enum.IntEnum):
    """Keys for register_key_down (Windows virtual-key codes, names as in UE4SS). Aliases share a number."""

    LEFT_MOUSE_BUTTON = 0x1
    RIGHT_MOUSE_BUTTON = 0x2
    CANCEL = 0x3
    MIDDLE_MOUSE_BUTTON = 0x4
    XBUTTON_ONE = 0x5
    XBUTTON_TWO = 0x6
    BACKSPACE = 0x8
    TAB = 0x9
    CLEAR = 0x0C
    RETURN = 0x0D
    PAUSE = 0x13
    CAPS_LOCK = 0x14
    IME_KANA = 0x15
    IME_HANGUEL = 0x15
    IME_HANGUL = 0x15
    IME_ON = 0x16
    IME_JUNJA = 0x17
    IME_FINAL = 0x18
    IME_HANJA = 0x19
    IME_KANJI = 0x19
    IME_OFF = 0x1A
    ESCAPE = 0x1B
    IME_CONVERT = 0x1C
    IME_NONCONVERT = 0x1D
    IME_ACCEPT = 0x1E
    IME_MODECHANGE = 0x1F
    SPACE = 0x20
    PAGE_UP = 0x21
    PAGE_DOWN = 0x22
    END = 0x23
    HOME = 0x24
    LEFT_ARROW = 0x25
    UP_ARROW = 0x26
    RIGHT_ARROW = 0x27
    DOWN_ARROW = 0x28
    SELECT = 0x29
    PRINT = 0x2A
    EXECUTE = 0x2B
    PRINT_SCREEN = 0x2C
    INS = 0x2D
    DEL = 0x2E
    HELP = 0x2F
    ZERO = 0x30
    ONE = 0x31
    TWO = 0x32
    THREE = 0x33
    FOUR = 0x34
    FIVE = 0x35
    SIX = 0x36
    SEVEN = 0x37
    EIGHT = 0x38
    NINE = 0x39
    A = 0x41
    B = 0x42
    C = 0x43
    D = 0x44
    E = 0x45
    F = 0x46
    G = 0x47
    H = 0x48
    I = 0x49
    J = 0x4A
    K = 0x4B
    L = 0x4C
    M = 0x4D
    N = 0x4E
    O = 0x4F
    P = 0x50
    Q = 0x51
    R = 0x52
    S = 0x53
    T = 0x54
    U = 0x55
    V = 0x56
    W = 0x57
    X = 0x58
    Y = 0x59
    Z = 0x5A
    LEFT_WIN = 0x5B
    RIGHT_WIN = 0x5C
    APPS = 0x5D
    SLEEP = 0x5F
    NUM_ZERO = 0x60
    NUM_ONE = 0x61
    NUM_TWO = 0x62
    NUM_THREE = 0x63
    NUM_FOUR = 0x64
    NUM_FIVE = 0x65
    NUM_SIX = 0x66
    NUM_SEVEN = 0x67
    NUM_EIGHT = 0x68
    NUM_NINE = 0x69
    MULTIPLY = 0x6A
    ADD = 0x6B
    SEPARATOR = 0x6C
    SUBTRACT = 0x6D
    DECIMAL = 0x6E
    DIVIDE = 0x6F
    F1 = 0x70
    F2 = 0x71
    F3 = 0x72
    F4 = 0x73
    F5 = 0x74
    F6 = 0x75
    F7 = 0x76
    F8 = 0x77
    F9 = 0x78
    F10 = 0x79
    F11 = 0x7A
    F12 = 0x7B
    F13 = 0x7C
    F14 = 0x7D
    F15 = 0x7E
    F16 = 0x7F
    F17 = 0x80
    F18 = 0x81
    F19 = 0x82
    F20 = 0x83
    F21 = 0x84
    F22 = 0x85
    F23 = 0x86
    F24 = 0x87
    NUM_LOCK = 0x90
    SCROLL_LOCK = 0x91
    BROWSER_BACK = 0xA6
    BROWSER_FORWARD = 0xA7
    BROWSER_REFRESH = 0xA8
    BROWSER_STOP = 0xA9
    BROWSER_SEARCH = 0xAA
    BROWSER_FAVORITES = 0xAB
    BROWSER_HOME = 0xAC
    VOLUME_MUTE = 0xAD
    VOLUME_DOWN = 0xAE
    VOLUME_UP = 0xAF
    MEDIA_NEXT_TRACK = 0xB0
    MEDIA_PREV_TRACK = 0xB1
    MEDIA_STOP = 0xB2
    MEDIA_PLAY_PAUSE = 0xB3
    LAUNCH_MAIL = 0xB4
    LAUNCH_MEDIA_SELECT = 0xB5
    LAUNCH_APP1 = 0xB6
    LAUNCH_APP2 = 0xB7
    OEM_ONE = 0xBA
    OEM_PLUS = 0xBB
    OEM_COMMA = 0xBC
    OEM_MINUS = 0xBD
    OEM_PERIOD = 0xBE
    OEM_TWO = 0xBF
    OEM_THREE = 0xC0
    OEM_FOUR = 0xDB
    OEM_FIVE = 0xDC
    OEM_SIX = 0xDD
    OEM_SEVEN = 0xDE
    OEM_EIGHT = 0xDF
    OEM_102 = 0xE2
    IME_PROCESS = 0xE5
    PACKET = 0xE7
    ATTN = 0xF6
    CRSEL = 0xF7
    EXSEL = 0xF8
    EREOF = 0xF9
    PLAY = 0xFA
    ZOOM = 0xFB
    NONAME = 0xFC
    PA1 = 0xFD


class ModifierKey(_enum.IntEnum):
    """Modifier keys for register_key_down(modifiers=...)."""

    SHIFT = 0x10
    CONTROL = 0x11
    ALT = 0x12


class ObjectFlags(_enum.IntFlag):
    """Engine object flags (EObjectFlags) for find_objects(required_flags=..., banned_flags=...)."""

    RF_Public = 0x00000001
    RF_Standalone = 0x00000002
    RF_Transactional = 0x00000008
    RF_ClassDefaultObject = 0x00000010
    RF_ArchetypeObject = 0x00000020
    RF_Transient = 0x00000040
    RF_MarkAsRootSet = 0x00000080
    RF_NeedLoad = 0x00000400
    RF_BeginDestroyed = 0x00008000
    RF_FinishDestroyed = 0x00010000
    RF_DefaultSubObject = 0x00040000
    RF_WasLoaded = 0x00080000


class _ObjectData:
    __slots__ = ("path", "class_path", "index", "generation", "alive", "properties", "extra", "flags")

    def __init__(self, path, class_path):
        # Default objects ("Default__X") carry RF_ClassDefaultObject, like in the engine.
        self.flags = int(ObjectFlags.RF_ClassDefaultObject) if _short(path).startswith("Default__") else 0
        self.extra = {}  # kind-specific data (function flags/params)
        self.properties = {}  # lower-cased name -> [type_name, value]
        self.path = path
        self.class_path = class_path
        self.index = next(_object_counter)
        self.generation = 0
        self.alive = True


_class_parents = {}  # class path -> parent class path or None
_objects = {}  # path -> _ObjectData, in creation order
_class_fields = {}  # class or struct path -> names of the properties it declares
_class_field_types = {}  # (class path, property name) -> mock property type, for get_properties
_class_functions = {}  # class path -> {function name: _ObjectData}
_struct_parents = {}  # struct type path -> parent struct path or None
_struct_defs = {}  # struct type path -> typed field definitions (or None)
_enum_names = {}  # enum path -> {name: value}
_enum_classes = {}  # enum path -> IntEnum class (cache, like the real module)


def _enum_member(path, value):
    """An enum property's value as a member of an IntEnum built from the UEnum; a number the enum does not name stays an int."""
    cls = _enum_classes.get(path)
    if cls is None and path not in _enum_classes:
        pairs = [(name.rpartition("::")[2], number) for name, number in _enum_names.get(path, {}).items()]
        pairs = [(name, number) for name, number in pairs if name and not name.startswith("_")]
        try:
            cls = _enum.IntEnum(path.rpartition(".")[2], pairs)
        except (ValueError, TypeError):
            cls = None
        _enum_classes[path] = cls
    if cls is None:
        return value
    try:
        return cls(value)
    except ValueError:
        return value


def _short(path):
    return path.rpartition(".")[2].rpartition(":")[2]


def _outer_path(path):
    for separator in (":", "."):
        head, found, _ = path.rpartition(separator)
        if found and head:
            return head
    return None


class UObject:
    """An Unreal object. Holds a checked handle, not a pointer: using it after the object was destroyed
    raises InvalidObjectError."""

    __slots__ = ("_data", "_generation")

    def __init__(self):
        raise TypeError("cannot create 'ue4ss.UObject' instances")

    @classmethod
    def _wrap(cls, data):
        wrapper = object.__new__(_wrapper_class_for(data))
        object.__setattr__(wrapper, "_data", data)
        object.__setattr__(wrapper, "_generation", data.generation)
        return wrapper

    def _live(self):
        if _mock_enforce_game_thread and not _in_game_thread:
            raise UEError("this must run on the game thread; use ue4ss.execute_in_game_thread or ue4ss.call_in_game_thread")
        if not self.is_valid():
            raise InvalidObjectError("the object was destroyed")
        return self._data

    def get_full_name(self):
        """get_full_name() -> str: 'ClassName /Path/To.Object'."""
        data = self._live()
        return f"{_short(data.class_path)} {data.path}"

    def get_fname(self):
        """get_fname() -> FName: the object's name."""
        return FName(_short(self._live().path))

    def get_class(self):
        """get_class() -> UObject: the object's class."""
        return _object_at(self._live().class_path)

    def get_outer(self):
        """get_outer() -> UObject | None: the object that contains this one."""
        outer = _outer_path(self._live().path)
        return _object_at(outer) if outer else None

    def get_address(self):
        """get_address() -> int: the object's address (changes between runs)."""
        return 0x10000 + self._live().index * 0x40

    def is_valid(self):
        """is_valid() -> bool: False once the object was destroyed. Never raises."""
        return self._data.alive and self._generation == self._data.generation

    def is_a(self, cls):
        """is_a(cls) -> bool: cls is a UObject class or a class path such as /Script/Engine.Actor."""
        data = self._live()
        if isinstance(cls, UObject):
            target = cls._live()
            if not _derives_from(target.class_path, "/Script/CoreUObject.Class"):
                raise UEError("the argument is not a class")
            target_path = target.path
        elif isinstance(cls, str):
            if cls not in _class_parents:
                raise UEError("no class with that path: " + cls)
            target_path = cls
        else:
            raise TypeError(f"is_a() argument must be a UObject class or a class path str, not {type(cls).__name__}")
        return _derives_from(data.class_path, target_path)

    def get_property_value(self, name):
        """get_property_value(name): read a property; same as obj.name, usable when the name clashes with a method."""
        return _read_property(self._live(), name)

    def set_property_value(self, name, value):
        """set_property_value(name, value): write a property; same as obj.name = value."""
        _write_property(self._live(), name, value)

    def get_property_names(self):
        """get_property_names() -> list[str]: every property of the object's class, supported or not."""
        return [entry[2] for entry in self._live().properties.values()]

    def call_function(self, function, *args, **kwargs):
        """call_function(function, *args, **kwargs): call a function of this object by name or as a UFunction. Same as obj.Name(...). Returns None, the value, or a tuple of the return value and outputs."""
        if isinstance(function, str):
            found = _find_method(self, function)
            if found is None:
                raise AttributeError(f"the object has no function named '{function}'")
            function = found
        elif not isinstance(function, UFunction):
            raise TypeError(f"call_function() needs a function name or a UFunction, not {type(function).__name__}")
        return _call_function(self, function, args, kwargs)

    def __getattr__(self, name):
        # Only called when normal lookup fails, like the real type's tp_getattro fallback: property, then function.
        if name.startswith("__"):
            raise AttributeError(f"'ue4ss.UObject' object has no attribute '{name}'")
        data = self._live()
        if name.lower() in data.properties or _alias_of(name, [e[2] for e in data.properties.values() if len(e) > 2]) is not None:
            return _read_property(data, name)
        method = _find_method(self, name)
        if method is not None:
            return BoundFunction._bind(self, method)
        raise AttributeError(f"the object has no property or function named '{name}'")

    def __setattr__(self, name, value):
        if hasattr(type(self), name):
            raise AttributeError(f"'{name}' is a read-only attribute of ue4ss.UObject")
        if name.startswith("__"):
            raise AttributeError(f"cannot set attribute '{name}' on ue4ss.UObject")
        _write_property(self._live(), name, value)

    def __delattr__(self, name):
        raise TypeError("properties cannot be deleted")

    def __repr__(self):
        if not self.is_valid():
            return f"<UObject (destroyed) index={self._data.index}>"
        if _mock_enforce_game_thread and not _in_game_thread:
            return f"<UObject index={self._data.index} (not readable here)>"
        return f"<UObject '{self.get_full_name()}'>"

    def __eq__(self, other):
        if not isinstance(other, UObject):
            return NotImplemented
        return self._data is other._data and self._generation == other._generation

    def __hash__(self):
        return hash((id(self._data), self._generation))


_MOCK_TO_ENGINE = {"bool": "BoolProperty", "int8": "Int8Property", "int16": "Int16Property", "int32": "IntProperty", "int64": "Int64Property",
                   "uint8": "ByteProperty", "uint16": "UInt16Property", "uint32": "UInt32Property", "uint64": "UInt64Property",
                   "float": "FloatProperty", "double": "DoubleProperty", "name": "NameProperty", "str": "StrProperty", "text": "TextProperty",
                   "soft": "SoftObjectProperty"}


def _descriptor(engine_type="Property", class_path=None, struct_path=None, enum_path=None, inner=(), array_dim=1):
    return {"type": engine_type, "class": class_path, "struct": struct_path, "enum": enum_path, "array_dim": array_dim, "inner": list(inner)}


def _descriptor_for_mock_type(type_name):
    """The type descriptor (as the real UStruct.get_properties reports it) for a mock property type such as 'float' or 'array:object:/Script/Engine.Actor'."""
    if type_name in _MOCK_TO_ENGINE:
        return _descriptor(_MOCK_TO_ENGINE[type_name])
    kind, _, rest = type_name.partition(":")
    if kind == "object":
        return _descriptor("ObjectProperty", class_path=rest)
    if kind in ("weak", "lazy"):
        return _descriptor("WeakObjectProperty" if kind == "weak" else "LazyObjectProperty", class_path=rest)
    if kind == "interface":
        return _descriptor("InterfaceProperty", class_path=rest)
    if kind == "struct":
        return _descriptor("StructProperty", struct_path=rest)
    if kind == "enum":
        return _descriptor("EnumProperty", enum_path=rest)
    if kind == "array":
        return _descriptor("ArrayProperty", inner=[_descriptor_for_mock_type(rest)])
    if kind == "set":
        return _descriptor("SetProperty", inner=[_descriptor_for_mock_type(rest)])
    if kind == "map":
        key, value = rest.split("=>", 1)
        return _descriptor("MapProperty", inner=[_descriptor_for_mock_type(key), _descriptor_for_mock_type(value)])
    if kind == "fixed":
        return dict(_descriptor_for_mock_type(rest), array_dim=2)
    if type_name in ("delegate", "multicast"):
        return _descriptor("DelegateProperty" if type_name == "delegate" else "MulticastInlineDelegateProperty")
    return _descriptor("Property")


class UStruct(UObject):
    """A class, struct or function type."""

    __slots__ = ()

    def _struct_path(self):
        return self._live().path

    def get_super(self):
        """get_super() -> UStruct | None: the parent class or struct."""
        path = self._struct_path()
        parent = _class_parents.get(path) if path in _class_parents else _struct_parents.get(path)
        return _object_at(parent) if parent else None

    def get_property_names(self):
        """get_property_names() -> list[str]: the properties instances of this class or struct have, parents included."""
        names = []
        path = self._struct_path()
        parents = _class_parents if path in _class_parents else _struct_parents
        while path is not None:
            names = _class_fields.get(path, []) + names
            path = parents.get(path)
        return names

    def get_properties(self, inherited=True):
        """get_properties(inherited=True) -> list[dict]: each property of the class or struct with its type: name, type (the engine property class), class, struct, enum (paths or None), array_dim, inner (descriptors of the element, or key and value), declared_in, flags. inherited=False lists only the properties this class declares."""
        path = self._struct_path()
        parents = _class_parents if path in _class_parents else _struct_parents
        chain = []
        while path is not None:
            chain.insert(0, path)
            path = parents.get(path)
        if not inherited:
            chain = chain[-1:]
        result = []
        for owner in chain:
            declared = _struct_defs.get(owner) or {}
            for name in _class_fields.get(owner, []):
                entry = declared.get(name)
                mock_type = (_class_field_types.get((owner, name)) or (entry[0] if entry else None))
                described = _descriptor_for_mock_type(mock_type) if mock_type else _descriptor()
                result.append(dict(described, name=name, declared_in=owner, flags=0))
        return result

    def get_function_names(self):
        """get_function_names() -> list[str]: the functions of this class, parents included."""
        names = []
        path = self._struct_path()
        while path is not None:
            names = list(_class_functions.get(path, {})) + names
            path = _class_parents.get(path)
        return names

    def find_function(self, name):
        """find_function(name) -> UFunction | None: a function of this class or a parent."""
        if not isinstance(name, str):
            raise TypeError("expected str, not " + type(name).__name__)
        path = self._struct_path()
        while path is not None:
            for function_name, data in _class_functions.get(path, {}).items():
                if function_name.lower() == name.lower():
                    return UObject._wrap(data)
            path = _class_parents.get(path)
        return None


class UClass(UStruct):
    """An Unreal class."""

    __slots__ = ()

    def get_cdo(self):
        """get_cdo() -> UObject | None: the class default object."""
        path = self._struct_path()
        outer = _outer_path(path)
        return _object_at(f"{outer}.Default__{_short(path)}" if outer else f"Default__{_short(path)}")

    def is_child_of(self, cls):
        """is_child_of(cls) -> bool: this class is cls or derives from it; cls is a UClass or a class path."""
        path = self._struct_path()
        if isinstance(cls, UClass):
            target = cls._struct_path()
        elif isinstance(cls, str):
            if cls not in _class_parents:
                raise UEError("no class with that path: " + cls)
            target = cls
        else:
            raise TypeError(f"is_child_of() argument must be a UClass or a class path str, not {type(cls).__name__}")
        return _derives_from(path, target)


class UScriptStruct(UStruct):
    """The type of a struct."""

    __slots__ = ()


class UFunction(UStruct):
    """An Unreal function."""

    __slots__ = ()

    def get_function_flags(self):
        """get_function_flags() -> int: the engine's EFunctionFlags."""
        return self._live().extra["flags"]

    def get_script(self):
        """get_script() -> bytes: the compiled Blueprint bytecode of the function (empty for a native function); see ue4ss.bytecode.scan_calls to list what it calls."""
        return bytes(self._live().extra.get("script", b""))

    def get_params(self):
        """get_params() -> list[dict]: for each parameter, in order: name, is_out, is_return, is_const, is_reference and the type descriptor (type, class, struct, enum, array_dim, inner) as in UStruct.get_properties()."""
        keys = ("name", "type", "is_out", "is_return", "is_const", "is_reference")
        result = []
        for param in self._live().extra["params"]:
            described = _descriptor_for_mock_type(param["mock_type"]) if param["mock_type"] else _descriptor(param["type"])
            described["type"] = param["type"]
            result.append(dict(described, **{key: param[key] for key in keys if key != "type"}))
        return result

    def make_param_block(self, *args, **kwargs):
        """make_param_block(*args, **kwargs) -> Struct: the parameter block a call to this function would use, filled from the arguments (positional or by name, case-insensitive). Outputs and the return value start default-initialised."""
        data = self._live()
        params = data.extra["params"]
        names = [p["name"] for p in params]
        kwargs = {(key if key.lower() in {n.lower() for n in names} else (_alias_of(key, names) or key)): value for key, value in kwargs.items()}
        error = _bind_arguments(params, len(args), list(kwargs))
        if isinstance(error, str):
            raise TypeError(f"{_short(data.path)}() {error}")
        fields = {}
        for param in params:
            type_name = param["mock_type"] or _ENGINE_TO_MOCK_TYPE.get(param["type"], "unsupported:" + param["type"])
            if type_name.startswith("struct:"):
                value = _mock_struct_fields(_struct_defs[type_name[len("struct:"):]] or {})
            elif type_name.startswith("unsupported:"):
                value = None
            else:
                value = _convert_for_property(type_name, _property_default(type_name))
            fields[param["name"].lower()] = [type_name, value, param["name"]]
        block = Struct._view(_OwnedToken(data), fields, data.path)
        values = {param["name"]: args[i] for i, param in enumerate(p for p in params if _role_of(p) == "input") if i < len(args)}
        for key, value in kwargs.items():
            values[next(p["name"] for p in params if p["name"].lower() == key.lower())] = value
        for name, value in values.items():
            _write_to(block._fields, block._owner, name, value)
        return block


class UEnum(UObject):
    """An Unreal enum."""

    __slots__ = ()

    def _pairs(self):
        return [(name.rpartition("::")[2], value) for name, value in _enum_names[self._live().path].items()]

    def get_names(self):
        """get_names() -> dict[str, int]: every name and its value, without the 'EnumName::' prefix."""
        return dict(self._pairs())

    def get_name_by_value(self, value):
        """get_name_by_value(value) -> str | None."""
        if not isinstance(value, int):
            raise TypeError("get_name_by_value() needs an int, not " + type(value).__name__)
        return next((name for name, found in self._pairs() if found == value), None)

    def get_value_by_name(self, name):
        """get_value_by_name(name) -> int | None; the name may carry the 'EnumName::' prefix."""
        if not isinstance(name, str):
            raise TypeError("expected str, not " + type(name).__name__)
        wanted = name.rpartition("::")[2]
        return next((value for found, value in self._pairs() if found == wanted), None)


def _role_of(param):
    """'return', 'output' or 'input' (a const/by-reference parameter is an input even though it carries the out flag)."""
    if param["is_return"]:
        return "return"
    if param["is_out"] and not param["is_reference"]:
        return "output"
    return "input"


_PY_KEYWORDS = frozenset(("False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue", "def", "del", "elif", "else", "except",
                          "finally", "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try",
                          "while", "with", "yield"))


def _identifier_form(name):
    """The name the generated typings declare for an engine name that is not a Python identifier ('Sun height' -> 'Sun_height'). Mirrors
    core/include/pyue/identifier.hpp and python/lib/ue4ss/stubgen.py (_identifier)."""
    clean = "".join(c if (c.isalnum() or c == "_") else "_" for c in name)
    if not clean or clean[0].isdigit():
        clean = "_" + clean
    return clean + "_" if clean in _PY_KEYWORDS else clean


def _alias_of(requested, engine_names):
    """The one engine name whose identifier form is `requested` (and differs from it), or None if there is none or several."""
    if "_" not in requested:
        return None
    found = {n for n in engine_names if n != requested and _identifier_form(n) == requested}
    return next(iter(found)) if len(found) == 1 else None


def _bind_arguments(params, positional_count, keywords):
    """Same rules and messages as bind_arguments() in core/include/pyue/call_binding.hpp. Returns None if the
    arguments bind, otherwise the error text."""
    inputs = [p for p in params if _role_of(p) == "input"]
    if positional_count > len(inputs):
        count = len(inputs)
        return (f"takes {count} positional argument{'s' if count != 1 else ''} but {positional_count} "
                f"{'was' if positional_count == 1 else 'were'} given")
    filled = {p["name"].lower() for p in inputs[:positional_count]}
    for keyword in keywords:
        match = next((p for p in params if p["name"].lower() == keyword.lower()), None)
        if match is None:
            return f"got an unexpected keyword argument '{keyword}'"
        if _role_of(match) != "input":
            return f"'{keyword}' is an output of the function and cannot be passed"
        if match["name"].lower() in filled:
            return f"got multiple values for argument '{keyword}'"
        filled.add(match["name"].lower())
    missing = [p["name"] for p in inputs if p["name"].lower() not in filled]
    if missing:
        return f"missing {len(missing)} required argument{'s' if len(missing) != 1 else ''}: " + ", ".join(f"'{n}'" for n in missing)
    return None


class BoundFunction:
    """A function of an object, ready to call. Calling it runs the function in the game and returns its result."""

    __slots__ = ("_object", "_function")

    def __init__(self):
        raise TypeError("cannot create 'ue4ss.BoundFunction' instances")

    @classmethod
    def _bind(cls, obj, function):
        bound = object.__new__(cls)
        object.__setattr__(bound, "_object", obj)
        object.__setattr__(bound, "_function", function)
        return bound

    def __call__(self, *args, **kwargs):
        return _call_function(self._object, self._function, args, kwargs)

    def is_valid(self):
        """is_valid() -> bool: False once the object or the function was destroyed. Never raises."""
        return self._object.is_valid() and self._function.is_valid()

    def __repr__(self):
        if not self.is_valid():
            return "<BoundFunction (destroyed)>"
        if _mock_enforce_game_thread and not _in_game_thread:
            return "<BoundFunction (not readable here)>"
        return f"<BoundFunction '{_short(self._function._data.path)}'>"


_FUNC_STATIC = 0x00002000


def _find_method(obj, name):
    """The function `name` that obj can call: of its class chain, or for a class object of that class itself."""
    data = obj._live()
    path = data.path if isinstance(obj, UClass) else data.class_path
    while path is not None:
        for function_name, function_data in _class_functions.get(path, {}).items():
            if function_name.lower() == name.lower():
                return UObject._wrap(function_data)
        path = _class_parents.get(path)
    # the identifier form of a function name with spaces, as the typings declare it
    chain, path = {}, data.path if isinstance(obj, UClass) else data.class_path
    while path is not None:
        chain.update({n: d for n, d in _class_functions.get(path, {}).items() if n not in chain})
        path = _class_parents.get(path)
    alias = _alias_of(name, list(chain))
    return UObject._wrap(chain[alias]) if alias is not None else None


def _plan_results(params):
    """Same plan as plan_results() in core/include/pyue/call_binding.hpp."""
    in_out = [p for p in params if p["is_out"] and p["is_reference"] and not p["is_const"] and not p["is_return"]]
    return ([p for p in params if _role_of(p) == "return"] + [p for p in params if _role_of(p) == "output"] + in_out)


_hook_records = {}  # pre id -> record
_hook_ids = _itertools.count(1)
_hook_nesting = 0
_MAX_HOOK_NESTING = 8

# Event dispatcher bindings (bind_event / unbind_event): binding id -> record. A "multicast" together with a broadcast
# trigger simulates what the engine does when a delegate property broadcasts: every bound callback for that object and
# property runs with (context, params).
_event_records = {}  # id -> record
_event_ids = _itertools.count(1)
_event_profile = {"process_events_seen": 0, "script_calls_seen": 0, "matched": 0, "total_ns": 0, "reset_count": 0}


class NullType:
    """The type of ue4ss.NULL."""

    __slots__ = ()

    def __init__(self):
        raise TypeError("cannot create 'ue4ss.NullType' instances")

    def __repr__(self):
        return "ue4ss.NULL"

    def __bool__(self):
        return False


NULL = object.__new__(NullType)  # a post-hook returns this to set a null object reference as the result


class CallOriginalType:
    """The type of ue4ss.CALL_ORIGINAL."""

    __slots__ = ()

    def __init__(self):
        raise TypeError("cannot create 'ue4ss.CallOriginalType' instances")

    def __repr__(self):
        return "ue4ss.CALL_ORIGINAL"


CALL_ORIGINAL = object.__new__(CallOriginalType)  # a replacement returns this to let the original function run

_replacements = {}  # function path -> (id, callback)
_replacement_ids = _itertools.count(1)
_running_replacements = set()  # function paths whose replacement is running now: a nested call runs the original


def register_replacement(path, callback):
    """register_replacement(path, callback) -> id: callback(context, params) runs INSTEAD of the function at path and returns its result (None for no result, ue4ss.NULL for a null object). Return ue4ss.CALL_ORIGINAL to let the function run normally; an exception in the callback also lets it run. params holds the call's parameters, so outputs can be set on it. Calling the replaced function from inside the callback runs the original. Only calls that go through ProcessEvent are replaced (C++ callers, delegates, events, timers, Python calls); a Blueprint script calling a native function is not. One replacement per function."""
    if not isinstance(path, str):
        raise TypeError("expected str, not " + type(path).__name__)
    if not callable(callback):
        raise TypeError(f"callback must be callable, not {type(callback).__name__}")
    _require_game_thread()
    function = _object_at(path)
    if not isinstance(function, UFunction):
        raise UEError("no function with that path (use the form /Script/Engine.Actor:ReceiveBeginPlay)")
    key = function._live().path
    if key in _replacements:
        raise UEError("this function already has a replacement; unregister it first")
    replacement_id = next(_replacement_ids)
    _replacements[key] = (replacement_id, callback)
    return replacement_id


def unregister_replacement(replacement_id):
    """unregister_replacement(id): remove a replacement; id is what register_replacement returned."""
    if not isinstance(replacement_id, int):
        raise TypeError("unregister_replacement() takes the id register_replacement returned")
    _require_game_thread()
    for key, (known, _) in list(_replacements.items()):
        if known == replacement_id:
            del _replacements[key]
            return
    raise ValueError("no replacement with that id (already unregistered?)")


def _apply_replacement(function_data, context, block):
    """True if a replacement ran instead of the function (its result is in the block)."""
    key = function_data.path
    entry = _replacements.get(key)
    if entry is None or key in _running_replacements:
        return False
    _running_replacements.add(key)
    try:
        try:
            result = entry[1](context, block)
        except Exception as e:  # reported, and the original runs, like the real module
            print(f"Traceback (most recent call last):\n{type(e).__name__}: {e}", file=_sys.stderr)
            return False
    finally:
        _running_replacements.discard(key)
    if result is CALL_ORIGINAL:
        return False
    if result is not None:
        name = _result_parameter(function_data.extra["params"])
        if name:
            try:
                block.set_property_value(name, None if result is NULL else result)
            except Exception as e:
                print(f"{type(e).__name__}: {e}", file=_sys.stderr)
                return False
    return True


_slow_callback_ms = 50


# --- Euler angles <-> quaternions (the same maths as core/include/pyue/rotation.hpp) -----------------------------------------
_math = __import__("math")


def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def _axis_quat(axis, radians):
    s, c = _math.sin(radians / 2), _math.cos(radians / 2)
    return (s if axis == 0 else 0.0, s if axis == 1 else 0.0, s if axis == 2 else 0.0, c)


def _parse_order(order):
    if not isinstance(order, str):
        raise TypeError(f"order must be a str, not {type(order).__name__}")
    if order.lower() == "unreal":
        return None
    letters = order.lower()
    if len(letters) != 3 or sorted(letters) != ["x", "y", "z"]:
        raise ValueError(f"unknown rotation order '{order}': use \"unreal\" (pitch, yaw, roll) or the three axes in some order, such as \"xyz\" or \"zyx\"")
    return ["xyz".index(c) for c in letters]


def _angle(value, name):
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{name} must be a number, not {type(value).__name__}")
    return float(value)


def _check_degrees(degrees):
    if not isinstance(degrees, bool):
        raise TypeError(f"degrees must be True or False, not {type(degrees).__name__}")
    return degrees


def _rotator_quat(pitch, yaw, roll, degrees=True):
    k = _math.radians(1.0) if degrees else 1.0
    half = 0.5
    sp, cp = _math.sin(pitch * k * half), _math.cos(pitch * k * half)
    sy, cy = _math.sin(yaw * k * half), _math.cos(yaw * k * half)
    sr, cr = _math.sin(roll * k * half), _math.cos(roll * k * half)
    return (cr * sp * sy - sr * cp * cy, -cr * sp * cy - sr * cp * sy, cr * cp * sy - sr * sp * cy, cr * cp * cy + sr * sp * sy)


def euler_to_quat(a, b, c, order="unreal", degrees=True):
    """euler_to_quat(a, b, c, order="unreal", degrees=True) -> {"X", "Y", "Z", "W"}: the quaternion for Euler angles. The default order "unreal" takes (pitch, yaw, roll) like FRotator (pitch about Y, yaw about Z, roll about X). An axis order such as "xyz" or "zyx" means three intrinsic rotations in that order, the angles given in the same order. degrees=False takes radians."""
    axes = _parse_order(order)
    degrees = _check_degrees(degrees)
    a, b, c = _angle(a, "the first angle"), _angle(b, "the second angle"), _angle(c, "the third angle")
    if axes is None:
        q = _rotator_quat(a, b, c, degrees)
    else:
        k = _math.radians(1.0) if degrees else 1.0
        q = _qmul(_qmul(_axis_quat(axes[0], a * k), _axis_quat(axes[1], b * k)), _axis_quat(axes[2], c * k))
    return {"X": q[0], "Y": q[1], "Z": q[2], "W": q[3]}


def _read_quat(value):
    if isinstance(value, dict):
        lowered = {str(k).lower(): v for k, v in value.items()}
        if not all(name in lowered for name in "xyzw"):
            raise ValueError("a quaternion dict needs X, Y, Z and W")
        return tuple(_angle(lowered[name], name.upper()) for name in "xyzw")
    if isinstance(value, (list, tuple)):
        if len(value) != 4:
            raise ValueError("a quaternion sequence needs four numbers (x, y, z, w)")
        return tuple(_angle(v, "a quaternion component") for v in value)
    try:
        return tuple(_angle(getattr(value, name), name) for name in "XYZW")
    except AttributeError:
        raise TypeError(f"a quaternion is a dict, a sequence of four numbers or a struct with X, Y, Z and W, not {type(value).__name__}") from None


def _to_rotator(q, degrees):
    x, y, z, w = q
    singularity = z * x - w * y
    yaw_y = 2.0 * (w * z + x * y)
    yaw_x = 1.0 - 2.0 * (y * y + z * z)

    def normalize_axis(angle):
        angle = _math.fmod(angle, 360.0)
        if angle > 180.0:
            angle -= 360.0
        if angle < -180.0:
            angle += 360.0
        return angle
    yaw = _math.degrees(_math.atan2(yaw_y, yaw_x))
    if singularity < -0.4999995:
        pitch = -90.0
        roll = normalize_axis(-yaw - _math.degrees(2.0 * _math.atan2(x, w)))
    elif singularity > 0.4999995:
        pitch = 90.0
        roll = normalize_axis(yaw - _math.degrees(2.0 * _math.atan2(x, w)))
    else:
        pitch = _math.degrees(_math.asin(2.0 * singularity))
        roll = _math.degrees(_math.atan2(-2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y)))
    return tuple(_math.radians(v) if not degrees else v for v in (pitch, yaw, roll))


def _to_axes(q, axes, degrees):
    length = _math.sqrt(sum(v * v for v in q))
    x, y, z, w = (0.0, 0.0, 0.0, 1.0) if length < 1e-12 else tuple(v / length for v in q)
    m = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    i, j, k = axes
    s = 1.0 if (j - i + 3) % 3 == 1 else -1.0
    sin_b = max(-1.0, min(1.0, s * m[i][k]))
    b = _math.asin(sin_b)
    if abs(sin_b) < 0.9999999:
        a = _math.atan2(-s * m[j][k], m[k][k])
        c = _math.atan2(-s * m[i][j], m[i][i])
    else:
        c = 0.0
        a = _math.atan2(s * m[k][j], m[j][j])
    return tuple(_math.degrees(v) if degrees else v for v in (a, b, c))


def quat_to_euler(quat, order="unreal", degrees=True):
    """quat_to_euler(quat, order="unreal", degrees=True) -> (a, b, c): the Euler angles of a quaternion (a Quat struct, an X/Y/Z/W dict or a sequence of four numbers); the inverse of euler_to_quat."""
    axes = _parse_order(order)
    degrees = _check_degrees(degrees)
    q = _read_quat(quat)
    return _to_rotator(q, degrees) if axes is None else _to_axes(q, axes, degrees)


def make_transform(location=(0, 0, 0), rotation=(0, 0, 0), scale=(1, 1, 1), order="unreal", degrees=True):
    """make_transform(location=(0, 0, 0), rotation=(0, 0, 0), scale=(1, 1, 1), order="unreal", degrees=True) -> Struct: an owned FTransform. location and scale are (x, y, z) or {"X", "Y", "Z"}; rotation is Euler angles (pitch, yaw, roll for the default order, see euler_to_quat) or an X/Y/Z/W quaternion dict."""

    def vector(value):
        if isinstance(value, dict):
            return value
        x, y, z = value
        return {"X": x, "Y": y, "Z": z}

    quaternion = rotation if isinstance(rotation, dict) else euler_to_quat(*rotation, order=order, degrees=degrees)
    return make_struct("/Script/CoreUObject.Transform", Translation=vector(location), Rotation=quaternion, Scale3D=vector(scale))


def _rotator_like_quat(value):
    """The quaternion fields (as a dict) if `value` is a rotation given as Euler angles, else None (mirrors rotator_like_to_quat)."""
    if isinstance(value, Struct) and value._struct_path.rpartition(".")[2] == "Rotator":
        q = _rotator_quat(value.Pitch, value.Yaw, value.Roll)
    elif isinstance(value, (list, tuple)):
        if len(value) != 3:
            raise ValueError("a rotation given as a sequence needs three numbers (pitch, yaw, roll in degrees)")
        q = _rotator_quat(*(_angle(v, "a rotation angle") for v in value))
    elif isinstance(value, dict) and value and all(isinstance(k, str) for k in value):
        names = {k.lower() for k in value}
        if not names & {"pitch", "yaw", "roll"}:
            return None
        if names & {"x", "y", "z", "w"}:
            raise ValueError("give either Pitch/Yaw/Roll or X/Y/Z/W for a rotation, not both")
        unknown = names - {"pitch", "yaw", "roll"}
        if unknown:
            raise ValueError(f"unknown rotation key '{sorted(unknown)[0]}' (expected Pitch, Yaw and Roll)")
        lowered = {k.lower(): _angle(v, k) for k, v in value.items()}
        q = _rotator_quat(lowered.get("pitch", 0.0), lowered.get("yaw", 0.0), lowered.get("roll", 0.0))
    else:
        return None
    return {"X": q[0], "Y": q[1], "Z": q[2], "W": q[3]}


def set_slow_callback_warning(milliseconds):
    """set_slow_callback_warning(milliseconds): warn (in the log, at most 3 times per callback) when a Python callback on the game thread takes at least this long: game-thread tasks, hooks, replacements and new-object callbacks. Default 50; 0 switches the warnings off."""
    global _slow_callback_ms
    if not isinstance(milliseconds, int) or isinstance(milliseconds, bool):
        raise TypeError("set_slow_callback_warning() needs an int number of milliseconds, not " + type(milliseconds).__name__)
    if milliseconds < 0:
        raise ValueError("the threshold must not be negative (0 switches the warnings off)")
    _slow_callback_ms = milliseconds  # the mock does not time callbacks


def _load_blueprint_class(path):
    """Loads the class of a Blueprint function path (/Game/Folder/BP_X.BP_X_C:Function) when it is not loaded. True if the class exists afterwards."""
    class_path = path.partition(":")[0]
    if not (class_path.startswith("/Game/") and class_path.endswith("_C")):
        return False
    if _object_at(class_path) is not None:
        return True
    library = _object_at("/Script/Engine.KismetSystemLibrary")
    if library is None:
        return False
    try:
        return library.LoadClassAsset_Blocking(class_path) is not None
    except UEError:
        return False


def register_hook(path, pre=None, post=None, instance=None):
    """register_hook(path, pre=None, post=None, instance=None) -> (pre_id, post_id): call pre(context, params) before and post(context, params, result) after every call of the function at path, for example /Script/Engine.Actor:ReceiveBeginPlay. With instance=obj only calls on that object reach the callbacks (other objects cost nothing). A Blueprint function path (/Game/Folder/BP_X.BP_X_C:Function) loads the class first when the game has not loaded it. result is the function's return value (None if it returns nothing); do not read params.ReturnValue in a post-hook, it is stale. A post-hook that returns a value replaces the result (return ue4ss.NULL to set a null object); returning None keeps it. params is only valid inside the callback; params.copy() keeps a value."""
    if not isinstance(path, str):
        raise TypeError("expected str, not " + type(path).__name__)
    for what, callback in (("pre", pre), ("post", post)):
        if callback is not None and not callable(callback):
            raise TypeError(f"{what} must be callable or None, not {type(callback).__name__}")
    if pre is None and post is None:
        raise TypeError("register_hook() needs a pre callback, a post callback, or both")
    if instance is not None and not isinstance(instance, UObject):
        raise TypeError(f"instance must be a UObject or None, not {type(instance).__name__}")
    _require_game_thread()
    if path.startswith("/Game/") and _object_at(path) is None:
        _load_blueprint_class(path)
    function = _object_at(path)
    if not isinstance(function, UFunction):
        raise UEError("no function with that path (use the form /Script/Engine.Actor:ReceiveBeginPlay)")
    ids = (next(_hook_ids), next(_hook_ids))
    _hook_records[ids[0]] = {"function": function._data, "ids": ids, "pre": pre, "post": post, "instance": instance}
    return ids


def unregister_hook(*args):
    """unregister_hook(ids) or unregister_hook(pre_id, post_id): remove a hook; ids is what register_hook returned."""
    if len(args) == 1 and isinstance(args[0], tuple) and len(args[0]) == 2:
        args = args[0]
    if len(args) != 2 or not all(isinstance(a, int) for a in args):
        raise TypeError("unregister_hook() takes the (pre_id, post_id) tuple or the two ids")
    record = _hook_records.get(args[0])
    if record is None or record["ids"][1] != args[1]:
        raise ValueError("no hook with those ids (already unregistered?)")
    del _hook_records[args[0]]


def bind_event(object, name, callback, async_=False):
    """bind_event(object, name, callback, async_=False) -> id: call callback(context, params) on the game thread each time the
    multicast delegate property `name` (an Event Dispatcher) on the object broadcasts. context is the object and params
    is the delegate's parameter block (valid only inside the callback; params.copy() keeps a value). First supported for
    multicast (Event Dispatcher) properties only; single-cast and sparse delegates are not bindable yet. With async_=True,
    params is copied and the callback runs on a worker thread."""
    if not isinstance(object, UObject):
        raise TypeError(f"object must be a UObject, not {type(object).__name__}")
    if not callable(callback):
        raise TypeError(f"callback must be callable, not {type(callback).__name__}")
    _require_game_thread()
    data = object._live()
    entry = _find_property(data.properties, name)
    if entry is None:
        raise AttributeError(f"the object has no property named '{name}'")
    if entry[0] != "multicast":
        raise UEError("this property is not a multicast delegate (an Event Dispatcher)")
    event_id = next(_event_ids)
    if not isinstance(async_, bool):
        raise TypeError(f"async_ must be a bool, not {type(async_).__name__}")
    _event_records[event_id] = {"data": data, "name": name.lower(), "callback": callback, "async": async_}
    return event_id


def unbind_event(event_id):
    """unbind_event(id): stop delivering broadcasts to a bind_event binding; id is what bind_event returned."""
    if not isinstance(event_id, int):
        raise TypeError(f"event_id must be an int, not {type(event_id).__name__}")
    _require_game_thread()
    if event_id not in _event_records:
        raise ValueError("no event binding with that id (already unbound?)")
    del _event_records[event_id]


def _broadcast_event(object, name, fields=None, struct_path=""):
    """Mock-only: broadcast the multicast delegate property `name` on `object`, like the engine does when an Event
    Dispatcher fires. Every callback bound via bind_event for that (object, name) runs with (context, params); params
    is a Struct view over `fields` (or an empty block when fields is None)."""
    data = object._live()
    struct_path = struct_path or "DelegateSignatureParams"
    if struct_path not in _struct_defs:
        _mock_define_struct_type(
            struct_path,
            types={entry[2]: (entry[0], entry[1]) for entry in (fields or {}).values()},
        )
    token = _ScopedToken()
    params = Struct._view(token, fields or {}, struct_path)
    rerun = []
    for event_id, record in list(_event_records.items()):
        if record["data"] is data and record["name"] == name.lower():
            try:
                if record.get("async"):
                    import threading
                    worker_params = params.copy()
                    threading.Thread(target=record["callback"], args=(object, worker_params), daemon=True).start()
                else:
                    record["callback"](object, params)
            except SystemExit:
                print("sys.exit() inside a bound delegate callback is ignored", file=_sys.stderr)
            except BaseException:
                _traceback.print_exc()
            rerun.append(event_id)
    token.alive = False  # the parameters die when the broadcast returns
    return rerun


def _run_hooks(function_data, context, fields, struct_path, post):
    """Run the pre or post callbacks of every hook on this function, like the engine would."""
    global _hook_nesting
    for record in list(_hook_records.values()):
        callback = record["post" if post else "pre"]
        if callback is None or record["function"] is not function_data:
            continue
        if record.get("instance") is not None and record["instance"] != context:
            continue            # register_hook(instance=obj): other objects never reach the callbacks
        if _hook_nesting >= _MAX_HOOK_NESTING:
            print("ue4ss: Python hooks are nested more than 8 deep (a hook calling the function it hooks?); this call was skipped", file=_sys.stderr)
            continue
        token = _ScopedToken()
        params = Struct._view(token, fields, struct_path)
        _hook_nesting += 1
        try:
            return_name = _result_parameter(function_data.extra["params"])
            if post:
                returned = callback(context, params, Struct._view(token, fields, struct_path).get_property_value(return_name) if return_name else None)
            else:
                returned = callback(context, params)
            if returned is not None:
                if not post:
                    print("ue4ss: a pre-hook returned a value; only post-hooks can change the result, so it was ignored", file=_sys.stderr)
                elif return_name is None:
                    print("ue4ss: a post-hook returned a value but this function has no result to override; it was ignored", file=_sys.stderr)
                else:
                    try:
                        _write_to(fields, token, return_name, None if returned is NULL else returned)
                    except BaseException:
                        _traceback.print_exc()  # a bad replacement is reported; the original result stays
        except SystemExit:
            print("sys.exit() inside a hook is ignored", file=_sys.stderr)
        except BaseException:
            _traceback.print_exc()
        finally:
            _hook_nesting -= 1
            token.alive = False  # the parameters die when the callback returns


def _call_function(obj, function, args, kwargs):
    """What obj.Func(*args, **kwargs) does, with the function's impl standing in for the game."""
    function_data = function._live()
    obj._live()
    block = function.make_param_block(*args, **kwargs)  # argument errors come first, before the game is touched
    name = _short(function_data.path)
    static = bool(function_data.extra["flags"] & _FUNC_STATIC)
    if isinstance(obj, UClass):
        if not static:
            raise UEError(f"'{name}' is an instance function; call it on an object of the class, not on the class itself")
        context = obj.get_cdo()
    else:
        context = obj
        if not static:
            owner = function_data.path.rpartition(":")[0]
            if not _derives_from(context._data.class_path, owner):
                raise UEError(f"'{name}' belongs to class {_short(owner)}, which this object is not an instance of")
    impl = function_data.extra.get("impl")
    if impl is None:
        raise UEError(f"(mock) '{name}' has no implementation; give _mock_define_function an impl")
    if not _apply_replacement(function_data, context, block):
        _run_hooks(function_data, context, block._fields, block._struct_path, post=False)
        impl(block)
        _run_hooks(function_data, context, block._fields, block._struct_path, post=True)
    results = [block.get_property_value(p["name"]) for p in _plan_results(function_data.extra["params"])]
    if not results:
        return None
    return results[0] if len(results) == 1 else tuple(results)


def _wrapper_class_for(data):
    """The most specific wrapper type for an object, like the real module chooses when it wraps one."""
    for path, wrapper in (("/Script/CoreUObject.Class", UClass), ("/Script/CoreUObject.ScriptStruct", UScriptStruct),
                          ("/Script/CoreUObject.Function", UFunction), ("/Script/CoreUObject.Struct", UStruct),
                          ("/Script/CoreUObject.Enum", UEnum)):
        if _derives_from(data.class_path, path):
            return wrapper
    return UObject


_new_object_subscriptions = {}  # id -> (class path, callback)
_new_object_ids = _itertools.count(1)


def notify_on_new_object(class_path, callback):
    """notify_on_new_object(class_path, callback) -> id: call callback(obj) on the game thread for every new instance of the class (or a subclass; default objects too), for example /Script/Engine.Actor. The call comes shortly after creation, not during it; an object destroyed before then is not delivered."""
    if not isinstance(class_path, str):
        raise TypeError("expected str, not " + type(class_path).__name__)
    if not callable(callback):
        raise TypeError("callback must be callable, not " + type(callback).__name__)
    _require_game_thread()
    cls = _object_at(class_path)
    if not isinstance(cls, UClass):
        raise UEError("no class with that path (use the form /Script/Engine.Actor)")
    subscription_id = next(_new_object_ids)
    _new_object_subscriptions[subscription_id] = (class_path, callback)
    return subscription_id


def unregister_notify_on_new_object(subscription_id):
    """unregister_notify_on_new_object(id): stop a notify_on_new_object subscription."""
    if not isinstance(subscription_id, int):
        raise TypeError("unregister_notify_on_new_object() takes the id notify_on_new_object returned")
    _require_game_thread()
    if _new_object_subscriptions.pop(subscription_id, None) is None:
        raise ValueError("no subscription with that id (already unregistered?)")


_key_bindings = {}  # id -> (key, frozenset of modifiers, callback)
_key_ids = _itertools.count(1)


def _parse_key(key, modifiers):
    if not isinstance(key, int) or isinstance(key, bool):
        raise TypeError("an integer (a ue4ss.Key) is required, not " + type(key).__name__)
    if not 1 <= key <= 0xFE or key in (0x10, 0x11, 0x12):
        raise ValueError("key must be a ue4ss.Key (or its number); Shift, Control and Alt are modifiers: pass them in modifiers=")
    mods = set()
    for modifier in (modifiers or ()):
        if modifier not in (0x10, 0x11, 0x12):
            raise ValueError("modifiers must be ue4ss.ModifierKey values (SHIFT, CONTROL, ALT)")
        mods.add(int(modifier))
    return int(key), frozenset(mods)


def register_key_down(key, callback, modifiers=()):
    """register_key_down(key, callback, modifiers=()) -> id: call callback() on the game thread each time the key (a ue4ss.Key) goes down while the game window has the focus. modifiers is an iterable of ue4ss.ModifierKey that must be held, exactly those. Several callbacks may share a key."""
    if not callable(callback):
        raise TypeError("callback must be callable, not " + type(callback).__name__)
    number, mods = _parse_key(key, modifiers)
    binding_id = next(_key_ids)
    _key_bindings[binding_id] = (number, mods, callback)
    return binding_id


def unregister_key_down(binding_id):
    """unregister_key_down(id): stop a register_key_down binding."""
    if not isinstance(binding_id, int):
        raise TypeError("unregister_key_down() takes the id register_key_down returned")
    if _key_bindings.pop(binding_id, None) is None:
        raise ValueError("no key binding with that id (already unregistered?)")


def _selftest_fire_key(key, modifiers=()):
    """Internal: act as if the key (with exactly these modifiers) went down; returns how many bindings will run (they run now in the mock)."""
    number, mods = _parse_key(key, modifiers)
    matching = [(i, cb) for i, (k, m, cb) in list(_key_bindings.items()) if k == number and m == mods]
    for binding_id, callback in matching:
        if binding_id not in _key_bindings:
            continue  # unregistered by an earlier callback of the same press
        try:
            callback()
        except SystemExit:
            pass
        except Exception:
            _traceback.print_exc()
    return len(matching)


def _announce_new_object(data):
    """Like the engine's creation listener: remember the object and deliver it at a later tick."""
    if not _new_object_subscriptions:
        return

    def deliver():
        if not data.alive:
            return
        for subscription_id, (class_path, callback) in list(_new_object_subscriptions.items()):
            if subscription_id in _new_object_subscriptions and _derives_from(data.class_path, class_path):
                callback(UObject._wrap(data))

    _pending.append((0.0, next(_sequence), deliver))


def _derives_from(class_path, ancestor_path):
    while class_path is not None:
        if class_path == ancestor_path:
            return True
        class_path = _class_parents.get(class_path)
    return False


def _object_at(path):
    data = _objects.get(path)
    return UObject._wrap(data) if data else None


def _require_game_thread():
    if _mock_enforce_game_thread and not _in_game_thread:
        raise UEError("this must run on the game thread; use ue4ss.execute_in_game_thread or ue4ss.call_in_game_thread")


def make_struct(struct_type, **fields):
    """make_struct(type, **fields) -> Struct: a new struct value that owns its memory; type is a UScriptStruct or a path such as /Script/CoreUObject.Vector."""
    _require_game_thread()
    if isinstance(struct_type, UObject):
        path = struct_type._live().path
        if not isinstance(struct_type, UScriptStruct):
            raise UEError("this object is not a struct type")
    elif isinstance(struct_type, str):
        found = _objects.get(struct_type)
        if found is None:
            raise UEError("no struct type with that path")
        path = struct_type
    else:
        raise TypeError(f"make_struct() needs a UScriptStruct or a struct path str, not {type(struct_type).__name__}")
    if _struct_defs.get(path) is None:
        raise UEError("this object is not a struct type")
    value = Struct._view(_OwnedToken(_objects[path]), _mock_struct_fields(_struct_defs[path]), path)
    for name, field_value in fields.items():
        _write_to(value._fields, value._owner, name, field_value)
    return value


def static_find_object(path):
    """static_find_object(path) -> UObject | None: the object with that full path, for example /Script/Engine.Actor."""
    if not isinstance(path, str):
        raise TypeError("expected str, not " + type(path).__name__)
    _require_game_thread()
    return _object_at(path)


def object_at_address(address):
    """object_at_address(address) -> UObject | None: the live object at this address (for example a pointer read from Blueprint bytecode), or None if no object lives there. Never faults, whatever the number."""
    if not isinstance(address, int) or isinstance(address, bool):
        raise TypeError("address must be an int, not " + type(address).__name__)
    if address < 0 or address >= 2**64:
        raise OverflowError("address out of range")
    _require_game_thread()
    for data in _objects.values():
        if data.alive and 0x10000 + data.index * 0x40 == address:
            return UObject._wrap(data)
    return None


def find_first_of(class_name):
    """find_first_of(class_name) -> UObject | None: first instance of the class or a subclass (not the default object)."""
    if not isinstance(class_name, str):
        raise TypeError("expected str, not " + type(class_name).__name__)
    _require_game_thread()
    for data in _objects.values():
        if _short(data.path).startswith("Default__"):
            continue
        class_path = data.class_path
        while class_path is not None:
            if _short(class_path) == class_name:
                return UObject._wrap(data)
            class_path = _class_parents.get(class_path)
    return None


def _is_instance_of_named(data, class_name, exact):
    """Does the object's class (or, unless exact, a parent class) have this short name?"""
    class_path = data.class_path
    while class_path is not None:
        if _short(class_path).lower() == class_name.lower():
            return True
        if exact:
            return False
        class_path = _class_parents.get(class_path)
    return False


def find_all_of(class_name):
    """find_all_of(class_name) -> list[UObject]: every live instance of the class or a subclass; default objects and archetypes are not instances and are left out (same as find_objects(class_name, banned_flags=RF_ClassDefaultObject | RF_ArchetypeObject))."""
    if not isinstance(class_name, str):
        raise TypeError("expected str, not " + type(class_name).__name__)
    _require_game_thread()
    not_instances = ObjectFlags.RF_ClassDefaultObject | ObjectFlags.RF_ArchetypeObject
    return [UObject._wrap(data) for data in _objects.values()
            if not data.flags & not_instances and _is_instance_of_named(data, class_name, exact=False)]


def find_objects(class_name=None, name=None, required_flags=0, banned_flags=0, exact_class=False, limit=0):
    """find_objects(class_name=None, name=None, required_flags=0, banned_flags=0, exact_class=False, limit=0) -> list[UObject]: objects matching the class (and subclasses unless exact_class), the object name, or both. Includes default objects unless banned_flags has ObjectFlags.RF_ClassDefaultObject. limit 0 means no limit."""
    for what, value in (("class_name", class_name), ("name", name)):
        if value is not None and not isinstance(value, str):
            raise TypeError(f"{what} must be a str or None, not {type(value).__name__}")
    for what, value in (("required_flags", required_flags), ("banned_flags", banned_flags)):
        if not isinstance(value, int):
            raise TypeError(f"{what} must be an int, not {type(value).__name__}")
        if not 0 <= value <= 2**31 - 1:
            raise ValueError("object flags must be between 0 and 2**31 - 1")
    if not isinstance(limit, int):
        raise TypeError("limit must be an int")
    if limit < 0:
        raise ValueError("limit must not be negative (0 means no limit)")
    if class_name is None and name is None:
        raise ValueError("find_objects() needs class_name, name, or both")
    _require_game_thread()
    found = []
    for data in _objects.values():
        if limit and len(found) >= limit:
            break
        if class_name is not None and not _is_instance_of_named(data, class_name, exact_class):
            continue
        if name is not None and _short(data.path).lower() != name.lower():
            continue
        if required_flags and (data.flags & required_flags) != required_flags:
            continue
        if banned_flags and data.flags & banned_flags:
            continue
        found.append(UObject._wrap(data))
    return found


def _mock_set_flags(obj, flags):
    """Set the engine object flags of a mock object."""
    obj._data.flags = int(flags)


def _mock_create_object(path, class_path):
    """Create an object (and any missing outers as packages) and return its wrapper."""
    outer = _outer_path(path)
    if outer and outer not in _objects:
        _mock_create_object(outer, "/Script/CoreUObject.Package")
    if path in _objects:
        raise ValueError("object already exists: " + path)
    if class_path not in _class_parents:
        raise ValueError("unknown class: " + class_path)
    data = _ObjectData(path, class_path)
    _objects[path] = data
    _announce_new_object(data)
    return UObject._wrap(data)


def _mock_define_class(path, parent_path=None, fields=(), field_types=None):
    """Define a class (also creates the class object and its default object) and return the class wrapper.
    `fields` are the names of the properties instances have (besides those of the parents); `field_types` maps some of them to a mock
    property type ('float', 'object:/Script/Engine.Actor', ...) for get_properties."""
    if parent_path is not None and parent_path not in _class_parents:
        raise ValueError("unknown parent class: " + parent_path)
    _class_parents[path] = parent_path
    _class_fields[path] = list(fields)
    for field_name, mock_type in (field_types or {}).items():
        _class_field_types[(path, field_name)] = mock_type
    _class_functions[path] = {}
    cls = _mock_create_object(path, "/Script/CoreUObject.Class")
    outer = _outer_path(path)
    _mock_create_object(f"{outer}.Default__{_short(path)}" if outer else f"Default__{_short(path)}", path)
    return cls


def _mock_define_struct_type(path, parent_path=None, fields=(), types=None):
    """Define a struct type (a UScriptStruct object) and return its wrapper. `types` maps field names to
    (type_name, default) like _mock_struct_fields and is what make_struct builds values from."""
    _struct_parents[path] = parent_path
    _class_fields[path] = list(types) if types else list(fields)
    _struct_defs[path] = types
    return _mock_create_object(path, "/Script/CoreUObject.ScriptStruct")


_ENGINE_TO_MOCK_TYPE = {"BoolProperty": "bool", "IntProperty": "int32", "Int64Property": "int64", "FloatProperty": "float",
                        "DoubleProperty": "double", "NameProperty": "name", "StrProperty": "str", "ByteProperty": "uint8"}


def _normalize_param(param):
    """(name, engine_type, is_out, is_return) tuples or dicts -> the dict the mock stores."""
    if isinstance(param, dict):
        spec = dict(param)
    else:
        name, type_name, is_out, is_return = param
        spec = {"name": name, "type": type_name, "is_out": is_out, "is_return": is_return}
    spec.setdefault("is_const", False)
    spec.setdefault("is_reference", False)
    spec.setdefault("mock_type", None)  # the mock property type used to hold the value (see _mock_define_property)
    return spec


def _mock_define_function(class_path, name, flags=0, params=(), impl=None, script=b""):
    """Define a function of a class. params: (name, engine_type, is_out, is_return) tuples or dicts with the keys
    name, type, is_out, is_return, is_const, is_reference and mock_type, in order. `impl(block)` stands in for the
    game running the function: it reads the inputs from the parameter block and fills outputs and the return value."""
    function = _mock_create_object(f"{class_path}:{name}", "/Script/CoreUObject.Function")
    function._data.extra = {"flags": flags, "params": [_normalize_param(p) for p in params], "impl": impl, "script": bytes(script)}
    _class_functions[class_path][name] = function._data
    return function


def _mock_define_enum(path, names):
    """Define an enum. names maps 'EName::Value' (or 'Value') to its integer value."""
    _enum_names[path] = dict(names)
    return _mock_create_object(path, "/Script/CoreUObject.Enum")


def _mock_destroy_object(obj):
    """Destroy the object: every wrapper of it becomes invalid."""
    data = obj._data
    data.alive = False
    data.generation += 1
    _objects.pop(data.path, None)


def _mock_reset_objects():
    """Forget all objects and classes, then define the built-in ones."""
    global _mock_enforce_game_thread, _event_ids
    _mock_enforce_game_thread = False
    _class_parents.clear()
    _objects.clear()
    _hook_records.clear()
    _event_records.clear()
    _event_ids = _itertools.count(1)
    _event_profile.clear()
    _replacements.clear()
    _running_replacements.clear()
    _new_object_subscriptions.clear()
    _key_bindings.clear()
    _class_fields.clear()
    _class_field_types.clear()
    _class_functions.clear()
    _struct_parents.clear()
    _struct_defs.clear()
    _enum_names.clear()
    _enum_classes.clear()
    _class_parents["/Script/CoreUObject.Object"] = None
    _class_parents["/Script/CoreUObject.Struct"] = "/Script/CoreUObject.Object"
    _class_parents["/Script/CoreUObject.Class"] = "/Script/CoreUObject.Struct"
    _class_parents["/Script/CoreUObject.ScriptStruct"] = "/Script/CoreUObject.Struct"
    _class_parents["/Script/CoreUObject.Function"] = "/Script/CoreUObject.Struct"
    _class_parents["/Script/CoreUObject.Enum"] = "/Script/CoreUObject.Object"
    _class_parents["/Script/CoreUObject.Package"] = "/Script/CoreUObject.Object"
    for path, class_path in (
        ("/Script/CoreUObject", "/Script/CoreUObject.Package"),
        ("/Script/CoreUObject.Object", "/Script/CoreUObject.Class"),
        ("/Script/CoreUObject.Struct", "/Script/CoreUObject.Class"),
        ("/Script/CoreUObject.Class", "/Script/CoreUObject.Class"),
        ("/Script/CoreUObject.ScriptStruct", "/Script/CoreUObject.Class"),
        ("/Script/CoreUObject.Function", "/Script/CoreUObject.Class"),
        ("/Script/CoreUObject.Enum", "/Script/CoreUObject.Class"),
        ("/Script/CoreUObject.Package", "/Script/CoreUObject.Class"),
    ):
        data = _ObjectData(path, class_path)
        _objects[path] = data


_mock_reset_objects()


# --- properties -----------------------------------------------------------------------------
# Same conversion rules as the real module (src/PyUObject.cpp, core/include/pyue/scalar.hpp).

_INT_RANGES = {
    "int8": (-(2**7), 2**7 - 1),
    "int16": (-(2**15), 2**15 - 1),
    "int32": (-(2**31), 2**31 - 1),
    "int64": (-(2**63), 2**63 - 1),
    "uint8": (0, 2**8 - 1),
    "uint16": (0, 2**16 - 1),
    "uint32": (0, 2**32 - 1),
    "uint64": (0, 2**64 - 1),
}
_FLOAT_MAX = 3.4028234663852886e38  # FLT_MAX


def _as_float32(value):
    import struct
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _convert_for_property(type_name, value):
    if type_name == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            if value in (0, 1):
                return bool(value)
            raise ValueError("a bool property accepts True, False, 0 or 1 (got another integer)")
        raise TypeError(f"a bool property needs a bool, not {type(value).__name__}")
    if type_name.startswith("enum:"):
        return _convert_for_property("uint8", value)  # an IntEnum member is an int; anything else is refused like an int
    if type_name in _INT_RANGES:
        if not isinstance(value, int):
            raise TypeError(f"a {type_name} property needs an int, not {type(value).__name__}")
        low, high = _INT_RANGES[type_name]
        if not low <= value <= high:
            raise OverflowError(f"the integer does not fit a {type_name} property")
        return int(value)
    if type_name in ("float", "double"):
        if isinstance(value, (str, bytes)):
            raise TypeError(f"a {type_name} property needs a number, not {type(value).__name__}")
        number = float(value)  # ints too; raises OverflowError for huge ints and TypeError for non-numbers
        if type_name == "float":
            import math
            if math.isfinite(number) and abs(number) > _FLOAT_MAX:
                raise OverflowError("the number does not fit a float property")
            return _as_float32(number)
        return number
    if type_name == "name":
        if isinstance(value, (FName, str)):
            return FName(value)
        raise TypeError(f"a name property needs an FName or str, not {type(value).__name__}")
    if type_name in ("str", "text"):
        if isinstance(value, str):
            return value
        raise TypeError(f"expected str, not {type(value).__name__}")
    if type_name == "soft":
        if value is None:
            return ""
        if not isinstance(value, str):
            raise TypeError(f"a soft reference needs a path str or None, not {type(value).__name__}")
        return value
    if type_name.startswith(("object:", "weak:", "lazy:")):
        prefix = type_name[:type_name.index(":") + 1]
        type_name = "object:" + type_name[len(prefix):]
    if type_name.startswith("object:"):
        if value is None:
            return None
        if isinstance(value, type) and hasattr(value, "static_class"):
            try:
                value = value.static_class()        # an engine class type of ue4ss.engine stands for its engine class object
            except Exception:  # noqa: BLE001
                pass
        if not isinstance(value, UObject):
            raise TypeError(f"an object property needs a UObject or None, not {type(value).__name__}")
        data = value._live()
        if not _derives_from(data.class_path, type_name[len("object:"):]):
            raise UEError("the object is not an instance of the property's class " + _short(type_name[len("object:"):]))
        return value
    raise AssertionError(type_name)


def _find_property(properties, name):
    entry = properties.get(name.lower())
    if entry is None:
        # the identifier form of a property name with spaces, as the typings declare it
        alias = _alias_of(name, [e[2] for e in properties.values() if len(e) > 2])
        entry = properties.get(alias.lower()) if alias is not None else None
    if entry is None:
        raise AttributeError(f"the object has no property named '{name}'")
    if entry[0].startswith("unsupported:"):
        raise UEError(f"property '{name}' has type {entry[0][len('unsupported:'):]}, which is not supported yet")
    return entry


def _read_property(data, name):
    """Read from an object's data or from a struct value's field store (both are _PropertyStore users)."""
    return _read_from(data.properties, data, name)


def _read_from(properties, owner_data, name):
    entry = _find_property(properties, name)
    if entry[0].startswith("enum:"):
        return _enum_member(entry[0][len("enum:"):], entry[1])
    if entry[0] == "delegate":
        return entry[1][0] if entry[1] else None
    if entry[0] == "multicast":
        return list(entry[1])
    if entry[0].startswith(("weak:", "lazy:")):
        value = entry[1]
        return value if value is not None and value._data.alive else None  # a weak reference goes null with its object
    if entry[0].startswith("interface:"):
        return entry[1]
    if entry[0].startswith("map:"):
        return Map._view(owner_data, entry)
    if entry[0].startswith("set:"):
        return Set._view(owner_data, entry)
    if entry[0].startswith("array:"):
        return Array._view(owner_data, entry)
    if entry[0].startswith("fixed:"):
        return FixedArray._view(owner_data, entry)
    if entry[0].startswith("struct:"):
        return Struct._view(owner_data, entry[1], entry[0][len("struct:"):])
    return entry[1]


def _write_property(data, name, value):
    _write_to(data.properties, data, name, value)


def _write_to(properties, owner_data, name, value):
    entry = _find_property(properties, name)
    if entry[0] in ("delegate", "multicast"):
        raise UEError("delegate properties are read-only")
    if entry[0].startswith("interface:"):
        raise UEError("interface properties are read-only")
    if entry[0].startswith("array:"):
        Array._view(owner_data, entry)._replace(0, len(entry[1]), value)
        return
    if entry[0].startswith("fixed:"):
        FixedArray._view(owner_data, entry)._assign(value)
        return
    if entry[0].startswith("map:"):
        Map._view(owner_data, entry)._update(value, replace=True)
        return
    if entry[0].startswith("set:"):
        Set._view(owner_data, entry)._update(value, replace=True)
        return
    if entry[0].startswith("struct:"):
        struct_path = entry[0][len("struct:"):]
        if struct_path.rpartition(".")[2] == "Quat":
            converted = _rotator_like_quat(value)
            if converted is not None:
                value = converted
        if isinstance(value, Struct):
            if value._struct_path != struct_path:
                raise UEError("the source is not a struct of the same type as the property")
            value._check_alive()
            entry[1] = _copy_fields(value._fields)
        elif isinstance(value, dict):
            view = Struct._view(owner_data, entry[1], struct_path)
            for key, field_value in value.items():
                _write_to(view._fields, owner_data, key, field_value)
        else:
            raise TypeError(f"a struct property needs a Struct or a dict of field values, not {type(value).__name__}")
        return
    entry[1] = _convert_for_property(entry[0], value)


def _copy_fields(fields):
    copied = {}
    for key, (type_name, value, display) in fields.items():
        if type_name.startswith("struct:"):
            value = _copy_fields(value)
        elif type_name.startswith(("array:", "fixed:")):
            value = [_copy_fields(item) if isinstance(item, dict) else item for item in value]
        elif type_name.startswith("map:"):
            value = [[k, _copy_fields(v) if isinstance(v, dict) else v] for k, v in value]
        elif type_name.startswith("set:"):
            value = list(value)
        copied[key] = [type_name, value, display]
    return copied


def _split_map_type(type_name):
    key, value = type_name[len("map:"):].split("=>", 1)
    return key, value


def _initial_container(type_name, value):
    """The stored form of a container property's initial value (a list of elements, or of [key, value] pairs)."""
    if type_name.startswith(("array:", "fixed:")):
        inner = type_name[len("array:"):]  # both prefixes are 6 characters long
        return [_mock_struct_fields(item) if inner.startswith("struct:") else _convert_for_property(inner, item) for item in (value or [])]
    if type_name.startswith("set:"):
        inner = type_name[len("set:"):]
        return [_convert_for_property(inner, item) for item in (value or [])]
    key_type, value_type = _split_map_type(type_name)
    return [[_convert_for_property(key_type, k),
             _mock_struct_fields(v) if value_type.startswith("struct:") else _convert_for_property(value_type, v)] for k, v in (value or {}).items()]


def _mock_define_property(obj, name, type_name, value=None):
    """Give the mock object a property. type_name is bool, int8..uint64, float, double, name, str,
    'object:<class path>', 'struct:<struct name>' (value = dict of field definitions, see
    _mock_struct_fields), 'array:<one of those>' (value = list of element values; for struct elements a list of field
    definition dicts), 'fixed:<one of those>' (a fixed-size array: the value lists exactly its elements), or 'weak:<class path>' / 'lazy:<class path>' (a reference that reads as None once the object is gone), or 'unsupported:<EngineTypeName>' (reads and writes raise UEError)."""
    if type_name.startswith(("array:", "set:", "map:", "fixed:")):
        value = _initial_container(type_name, value)
    elif type_name.startswith("struct:"):
        value = _mock_struct_fields(value or {})
    elif type_name in ("delegate", "multicast"):
        value = [(target, FName(function)) for target, function in (value or [])]
    elif type_name.startswith("interface:"):
        value = value
    elif not type_name.startswith("unsupported:"):
        value = _convert_for_property(type_name, value if value is not None else _property_default(type_name))
    obj._data.properties[name.lower()] = [type_name, value, name]


def _mock_struct_fields(definition):
    """{'X': ('float', 1.0), 'Inner': ('struct:Vector', {'Z': ('float', 2.0)})} -> field store."""
    fields = {}
    for name, (type_name, value) in definition.items():
        if type_name.startswith(("array:", "set:", "map:", "fixed:")):
            value = _initial_container(type_name, value)
        elif type_name.startswith("struct:"):
            value = _mock_struct_fields(value or {})
        elif not type_name.startswith("unsupported:"):
            value = _convert_for_property(type_name, value if value is not None else _property_default(type_name))
        fields[name.lower()] = [type_name, value, name]
    return fields


class _OwnedToken:
    """Stands in for the owner of a struct value that owns its memory. It is alive exactly as long as the
    struct TYPE is (the real module needs the type to destroy the value), not any object."""

    __slots__ = ("_type_data",)

    def __init__(self, type_data):
        self._type_data = type_data

    @property
    def alive(self):
        return self._type_data.alive

    @property
    def generation(self):
        return self._type_data.generation


class _ScopedToken:
    """The owner of hook parameters: alive only while the hook callback runs."""

    __slots__ = ("alive",)
    generation = 0

    def __init__(self):
        self.alive = True


class Struct:
    """A struct value. A view into an object reads and writes the game's memory directly and stays valid only
    as long as its owner does; an owned struct (make_struct, copy) has its own memory.

    (mock) A view of a struct inside an object. Reads and writes go straight to the game's memory; the view
    stays valid only as long as its owner object does."""

    __slots__ = ("_owner", "_fields", "_struct_path", "_generation")

    def __init__(self):
        raise TypeError("cannot create 'ue4ss.Struct' instances")

    @classmethod
    def _view(cls, owner_data, fields, struct_path):
        view = object.__new__(cls)
        object.__setattr__(view, "_owner", owner_data)
        object.__setattr__(view, "_fields", fields)
        object.__setattr__(view, "_struct_path", struct_path)
        object.__setattr__(view, "_generation", owner_data.generation)
        return view

    def _check_alive(self):
        if _mock_enforce_game_thread and not _in_game_thread:
            raise UEError("this must run on the game thread; use ue4ss.execute_in_game_thread or ue4ss.call_in_game_thread")
        if not self.is_valid():
            if isinstance(self._owner, _ScopedToken):
                raise InvalidObjectError("the hook parameters are only valid inside the hook callback; use copy() to keep a value")
            raise InvalidObjectError("the object was destroyed")

    def is_valid(self):
        """is_valid() -> bool: False once the owner object was destroyed. Never raises."""
        return self._owner.alive and self._generation == self._owner.generation

    def copy(self):
        """copy() -> Struct: an independent copy that owns its memory and outlives the object this view came from."""
        self._check_alive()
        type_data = _objects[self._struct_path]
        return Struct._view(_OwnedToken(type_data), _copy_fields(self._fields), self._struct_path)

    def is_owned(self):
        """is_owned() -> bool: True for a struct you own (make_struct, copy), False for a view into an object."""
        return isinstance(self._owner, _OwnedToken)

    def get_struct_name(self):
        """get_struct_name() -> str: the struct type's name."""
        self._check_alive()
        return _short(self._struct_path)

    def get_property_names(self):
        """get_property_names() -> list[str]: every field, supported or not."""
        self._check_alive()
        return [entry[2] for entry in self._fields.values()]

    def get_property_value(self, name):
        """get_property_value(name): read a field; same as view.name."""
        self._check_alive()
        return _read_from(self._fields, self._owner, name)

    def set_property_value(self, name, value):
        """set_property_value(name, value): write a field; same as view.name = value."""
        self._check_alive()
        _write_to(self._fields, self._owner, name, value)

    def to_dict(self):
        """to_dict() -> dict: a copy of every supported field; nested structs become dicts and arrays become lists."""
        self._check_alive()
        result = {}
        for type_name, value, display in self._fields.values():
            if type_name.startswith("unsupported:"):
                continue
            if type_name.startswith("struct:"):
                result[display] = Struct._view(self._owner, value, type_name[len("struct:"):]).to_dict()
            elif type_name.startswith("map:"):
                result[display] = Map._view(self._owner, [type_name, value, display]).to_dict(plain=True)
            elif type_name.startswith("set:"):
                result[display] = Set._view(self._owner, [type_name, value, display]).to_list()
            elif type_name.startswith("array:"):
                items = Array._view(self._owner, [type_name, value, display]).to_list()
                result[display] = [item.to_dict() if isinstance(item, Struct) else item for item in items]
            elif type_name.startswith("fixed:"):
                items = FixedArray._view(self._owner, [type_name, value, display]).to_list()
                result[display] = [item.to_dict() if isinstance(item, Struct) else item for item in items]
            elif type_name.startswith(("weak:", "lazy:", "interface:")) or type_name in ("delegate", "multicast"):
                result[display] = _read_from(self._fields, self._owner, display)
            else:
                result[display] = value
        return result

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(f"'ue4ss.Struct' object has no attribute '{name}'")
        self._check_alive()
        return _read_from(self._fields, self._owner, name)

    def __setattr__(self, name, value):
        if hasattr(type(self), name):
            raise AttributeError(f"'{name}' is a read-only attribute of ue4ss.Struct")
        if name.startswith("__"):
            raise AttributeError(f"cannot set attribute '{name}' on ue4ss.Struct")
        self._check_alive()
        _write_to(self._fields, self._owner, name, value)

    def __delattr__(self, name):
        raise TypeError("properties cannot be deleted")

    def __repr__(self):
        scoped = isinstance(self._owner, _ScopedToken)
        if not self.is_valid():
            if scoped:
                return "<Struct (hook parameters out of scope)>"
            return "<Struct (type destroyed)>" if self.is_owned() else "<Struct (owner destroyed)>"
        if _mock_enforce_game_thread and not _in_game_thread:
            return "<Struct (not readable here)>"
        kind = "owned" if self.is_owned() else "hook parameters" if scoped else "view"
        return f"<Struct '{_short(self._struct_path)}' {kind}>"

    def __eq__(self, other):
        if not isinstance(other, Struct):
            return NotImplemented
        return self._fields is other._fields and self._owner is other._owner and self._generation == other._generation

    def __hash__(self):
        return hash((id(self._fields), id(self._owner), self._generation))


class Array:
    """A TArray property of an object or struct. len(), indexing, slicing, iteration and assignment of single elements
    work, and it can be resized like a list. Every access looks the array up again, so it is always current.

    (mock) The elements live in the owner's property store."""

    __slots__ = ("_owner", "_entry", "_generation")

    def __init__(self):
        raise TypeError("cannot create 'ue4ss.Array' instances")

    @classmethod
    def _view(cls, owner_data, entry):
        view = object.__new__(cls)
        object.__setattr__(view, "_owner", owner_data)
        object.__setattr__(view, "_entry", entry)
        object.__setattr__(view, "_generation", owner_data.generation)
        return view

    def _inner(self):
        return self._entry[0][len("array:"):]

    def _check_alive(self):
        if _mock_enforce_game_thread and not _in_game_thread:
            raise UEError("this must run on the game thread; use ue4ss.execute_in_game_thread or ue4ss.call_in_game_thread")
        if not self.is_valid():
            raise InvalidObjectError("the object was destroyed")

    def is_valid(self):
        """is_valid() -> bool: False once the owner object was destroyed. Never raises."""
        return self._owner.alive and self._generation == self._owner.generation

    def __len__(self):
        self._check_alive()
        return len(self._entry[1])

    def _element(self, index):
        inner = self._inner()
        value = self._entry[1][index]
        if inner.startswith("struct:"):
            path = inner[len("struct:"):]
            return Struct._view(_OwnedToken(_objects[path]), _copy_fields(value), path)  # an owned copy
        return value

    def __getitem__(self, index):
        self._check_alive()
        if isinstance(index, slice):
            return [self._element(i) for i in range(*index.indices(len(self._entry[1])))]
        if not isinstance(index, int):
            raise TypeError(f"array indices must be integers or slices, not {type(index).__name__}")
        if not -len(self._entry[1]) <= index < len(self._entry[1]):
            raise IndexError("array index out of range")
        return self._element(index)

    def _stored(self, value):
        """One Python value as the stored element (strict conversion; raises before anything changes)."""
        inner = self._inner()
        if inner.startswith("struct:"):
            path = inner[len("struct:"):]
            if not isinstance(value, Struct):
                raise TypeError(f"a struct element needs a Struct, not {type(value).__name__}")
            if value._struct_path != path:
                raise UEError("the source is not a struct of the same type as the array's elements")
            value._check_alive()
            return _copy_fields(value._fields)
        return _convert_for_property(inner, value)

    def _replace(self, start, stop, items):
        """Replace elements [start, stop) with items. Every item is converted first, so a bad one changes nothing."""
        try:
            items = list(items)
        except TypeError:
            raise TypeError("an array can only be assigned from an iterable") from None
        stored = [self._stored(item) for item in items]
        self._entry[1][start:stop] = stored

    def __setitem__(self, index, value):
        self._check_alive()
        length = len(self._entry[1])
        if isinstance(index, slice):
            start, stop, step = index.indices(length)
            if step != 1:
                raise ValueError("only slices with step 1 can be assigned or deleted")
            self._replace(start, max(start, stop), value)
            return
        if not isinstance(index, int):
            raise TypeError(f"array indices must be integers or slices, not {type(index).__name__}")
        if not -length <= index < length:
            raise IndexError("array assignment index out of range")
        self._entry[1][index] = self._stored(value)

    def __delitem__(self, index):
        self._check_alive()
        length = len(self._entry[1])
        if isinstance(index, slice):
            start, stop, step = index.indices(length)
            if step != 1:
                raise ValueError("only slices with step 1 can be assigned or deleted")
            del self._entry[1][start:max(start, stop)]
            return
        if not isinstance(index, int):
            raise TypeError(f"array indices must be integers or slices, not {type(index).__name__}")
        if not -length <= index < length:
            raise IndexError("array deletion index out of range")
        del self._entry[1][index]

    def append(self, value):
        """append(value): add one element at the end."""
        self._check_alive()
        self._replace(len(self._entry[1]), len(self._entry[1]), [value])

    def extend(self, iterable):
        """extend(iterable): add every item at the end. All items are converted before the array changes."""
        self._check_alive()
        self._replace(len(self._entry[1]), len(self._entry[1]), list(iterable))

    def insert(self, index, value):
        """insert(index, value): insert before index (like list.insert)."""
        self._check_alive()
        length = len(self._entry[1])
        if index < 0:
            index = max(0, index + length)
        index = min(index, length)
        self._replace(index, index, [value])

    def pop(self, index=-1):
        """pop(index=-1): remove and return an element (a struct comes back as an owned copy)."""
        self._check_alive()
        length = len(self._entry[1])
        if length == 0:
            raise IndexError("pop from empty array")
        if not -length <= index < length:
            raise IndexError("pop index out of range")
        result = self._element(index)
        del self._entry[1][index]
        return result

    def clear(self):
        """clear(): remove every element."""
        self._check_alive()
        del self._entry[1][:]

    def to_list(self):
        """to_list() -> list: a copy of every element (struct elements are owned copies)."""
        self._check_alive()
        return [self._element(i) for i in range(len(self._entry[1]))]

    def __repr__(self):
        if not self.is_valid():
            return "<Array (owner destroyed)>"
        if _mock_enforce_game_thread and not _in_game_thread:
            return "<Array (not readable here)>"
        return f"<Array '{self._entry[2]}' of {len(self._entry[1])} {self._inner()}>"


class FixedArray:
    """A fixed-size array property (int Foo[4]) of an object or struct. It behaves like a list that cannot change length:
    len(), indexing, slicing (read), iteration and item assignment. Assigning the property takes an iterable of exactly
    the same length.

    (mock) The elements live in the owner's property store."""

    __slots__ = ("_owner", "_entry", "_generation")

    def __init__(self):
        raise TypeError("cannot create 'ue4ss.FixedArray' instances")

    @classmethod
    def _view(cls, owner_data, entry):
        view = object.__new__(cls)
        object.__setattr__(view, "_owner", owner_data)
        object.__setattr__(view, "_entry", entry)
        object.__setattr__(view, "_generation", owner_data.generation)
        return view

    def _inner(self):
        return self._entry[0][len("fixed:"):]

    _check_alive = Array._check_alive
    _stored = Array._stored

    def is_valid(self):
        """is_valid() -> bool: False once the owner object was destroyed. Never raises."""
        return self._owner.alive and self._generation == self._owner.generation

    def __len__(self):
        self._check_alive()
        return len(self._entry[1])

    def _element(self, index):
        value = self._entry[1][index]
        inner = self._inner()
        if inner.startswith("struct:"):
            return Struct._view(self._owner, value, inner[len("struct:"):])  # a view: the memory is inline and never moves
        return value

    def __getitem__(self, index):
        self._check_alive()
        if isinstance(index, slice):
            return [self._element(i) for i in range(*index.indices(len(self._entry[1])))]
        if not isinstance(index, int):
            raise TypeError(f"array indices must be integers or slices, not {type(index).__name__}")
        if not -len(self._entry[1]) <= index < len(self._entry[1]):
            raise IndexError("array index out of range")
        return self._element(index)

    def _write_element(self, index, value):
        self._entry[1][index] = self._stored(value)

    def __setitem__(self, index, value):
        self._check_alive()
        if isinstance(index, slice):
            raise TypeError("slices of a fixed-size array cannot be assigned: assign the elements one by one or the whole array")
        if not isinstance(index, int):
            raise TypeError(f"array indices must be integers, not {type(index).__name__}")
        if not -len(self._entry[1]) <= index < len(self._entry[1]):
            raise IndexError("array assignment index out of range")
        self._write_element(index, value)

    def __delitem__(self, index):
        self._check_alive()
        raise TypeError("a fixed-size array cannot shrink: its elements cannot be deleted")

    def _assign(self, iterable):
        try:
            items = list(iterable)
        except TypeError:
            raise TypeError("a fixed-size array can only be assigned from an iterable") from None
        if len(items) != len(self._entry[1]):
            raise ValueError(f"'{self._entry[2]}' has exactly {len(self._entry[1])} elements, got {len(items)}")
        stored = [self._stored(item) for item in items]
        self._entry[1][:] = stored

    def to_list(self):
        """to_list() -> list: a copy of every element."""
        self._check_alive()
        return [self._element(i) for i in range(len(self._entry[1]))]

    def __repr__(self):
        if not self.is_valid():
            return "<FixedArray (owner destroyed)>"
        if _mock_enforce_game_thread and not _in_game_thread:
            return "<FixedArray (not readable here)>"
        return f"<FixedArray '{self._entry[2]}' of {len(self._entry[1])} {self._inner()}>"


def _same_key(a, b):
    """Engine-style key identity: names compare by index and number, everything else by value."""
    if isinstance(a, FName) and isinstance(b, FName):
        return a.comparison_index == b.comparison_index and a.number == b.number
    return type(a) is type(b) and a == b


class _ContainerView:
    __slots__ = ("_owner", "_entry", "_generation")

    def __init__(self):
        raise TypeError(f"cannot create 'ue4ss.{type(self).__name__}' instances")

    @classmethod
    def _view(cls, owner_data, entry):
        view = object.__new__(cls)
        object.__setattr__(view, "_owner", owner_data)
        object.__setattr__(view, "_entry", entry)
        object.__setattr__(view, "_generation", owner_data.generation)
        return view

    def _check_alive(self):
        if _mock_enforce_game_thread and not _in_game_thread:
            raise UEError("this must run on the game thread; use ue4ss.execute_in_game_thread or ue4ss.call_in_game_thread")
        if not self.is_valid():
            raise InvalidObjectError("the object was destroyed")

    def is_valid(self):
        """is_valid() -> bool: False once the owner object was destroyed. Never raises."""
        return self._owner.alive and self._generation == self._owner.generation

    @staticmethod
    def _read(type_name, value):
        if type_name.startswith("struct:"):
            path = type_name[len("struct:"):]
            return Struct._view(_OwnedToken(_objects[path]), _copy_fields(value), path)  # an owned copy
        return value

    @staticmethod
    def _convert(type_name, value, role):
        if type_name.startswith("struct:"):
            path = type_name[len("struct:"):]
            if not isinstance(value, Struct):
                raise TypeError(f"a struct needs a Struct, not {type(value).__name__}")
            if value._struct_path != path:
                raise UEError("the source is not a struct of the same type")
            value._check_alive()
            return _copy_fields(value._fields)
        return _convert_for_property(type_name, value)

    @staticmethod
    def _require_lookup(type_name):
        if type_name.startswith("struct:"):
            raise UEError("struct keys cannot be looked up or added yet")


class Map(_ContainerView):
    """A TMap property. It behaves like a dict: len, [], in, iteration over keys, keys/values/items/get/pop/update/clear, del. Keys may be numbers, bool, FName, str or objects (not structs). Every access looks the map up again.

    (mock) The pairs live in the owner's property store."""

    __slots__ = ()
    is_valid = _ContainerView.is_valid

    def _types(self):
        return _split_map_type(self._entry[0])

    def _find(self, key):
        key_type, _ = self._types()
        self._require_lookup(key_type)
        key = self._convert(key_type, key, "key")
        for pair in self._entry[1]:
            if _same_key(pair[0], key):
                return pair
        return None

    def __len__(self):
        self._check_alive()
        return len(self._entry[1])

    def __getitem__(self, key):
        self._check_alive()
        pair = self._find(key)
        if pair is None:
            raise KeyError(key)
        return self._read(self._types()[1], pair[1])

    def __setitem__(self, key, value):
        self._check_alive()
        self._update([(key, value)], replace=False)

    def __delitem__(self, key):
        self._check_alive()
        pair = self._find(key)
        if pair is None:
            raise KeyError(key)
        self._entry[1].remove(pair)

    def __contains__(self, key):
        self._check_alive()
        return self._find(key) is not None

    def __iter__(self):
        return iter(self.keys())

    def keys(self):
        """keys() -> list: the keys, in the engine's storage order."""
        self._check_alive()
        return [self._read(self._types()[0], pair[0]) for pair in self._entry[1]]

    def values(self):
        """values() -> list: the values (struct values are owned copies)."""
        self._check_alive()
        return [self._read(self._types()[1], pair[1]) for pair in self._entry[1]]

    def items(self):
        """items() -> list of (key, value) tuples."""
        self._check_alive()
        key_type, value_type = self._types()
        return [(self._read(key_type, pair[0]), self._read(value_type, pair[1])) for pair in self._entry[1]]

    def get(self, key, default=None):
        """get(key, default=None)."""
        try:
            return self[key]
        except KeyError:
            return default

    _MISSING = object()

    def pop(self, key, default=_MISSING):
        """pop(key[, default]): remove the key and return its value."""
        self._check_alive()
        try:
            value = self[key]
        except KeyError:
            if default is Map._MISSING:
                raise
            return default
        del self[key]
        return value

    def clear(self):
        """clear(): remove every entry."""
        self._check_alive()
        del self._entry[1][:]

    def _update(self, source, replace):
        pairs = list(source.items()) if hasattr(source, "items") else list(source)
        key_type, value_type = self._types()
        self._require_lookup(key_type)
        converted = []
        for item in pairs:
            if len(item) != 2:
                raise ValueError("each entry must be a (key, value) pair")
            converted.append((self._convert(key_type, item[0], "key"), self._convert(value_type, item[1], "value")))
        if replace:
            del self._entry[1][:]
        for key, value in converted:
            for pair in self._entry[1]:
                if _same_key(pair[0], key):
                    pair[1] = value
                    break
            else:
                self._entry[1].append([key, value])

    def update(self, mapping_or_pairs):
        """update(mapping_or_pairs): add or overwrite entries. Everything is converted before the map changes."""
        self._check_alive()
        self._update(mapping_or_pairs, replace=False)

    def to_dict(self, plain=False):
        """to_dict() -> dict: a copy (struct values stay owned Struct copies)."""
        self._check_alive()
        result = {}
        for key, value in self.items():
            result[key] = value.to_dict() if plain and isinstance(value, Struct) else value
        return result

    def __repr__(self):
        if not self.is_valid():
            return "<Map (owner destroyed)>"
        if _mock_enforce_game_thread and not _in_game_thread:
            return "<Map (not readable here)>"
        key_type, value_type = self._types()
        return f"<Map '{self._entry[2]}' of {len(self._entry[1])} {key_type} -> {value_type}>"


class Set(_ContainerView):
    """A TSet property. It behaves like a set: len, in, iteration, add/discard/remove/update/clear. Elements may be numbers, bool, FName, str or objects (not structs). Every access looks the set up again.

    (mock) The elements live in the owner's property store."""

    __slots__ = ()
    is_valid = _ContainerView.is_valid

    def _type(self):
        return self._entry[0][len("set:"):]

    def _index(self, value):
        self._require_lookup(self._type())
        value = self._convert(self._type(), value, "element")
        for i, existing in enumerate(self._entry[1]):
            if _same_key(existing, value):
                return i, value
        return None, value

    def __len__(self):
        self._check_alive()
        return len(self._entry[1])

    def __contains__(self, value):
        self._check_alive()
        return self._index(value)[0] is not None

    def __iter__(self):
        return iter(self.to_list())

    def add(self, value):
        """add(value)."""
        self._check_alive()
        self._update([value], replace=False)

    def discard(self, value):
        """discard(value): remove it if present."""
        self._check_alive()
        i, _ = self._index(value)
        if i is not None:
            del self._entry[1][i]

    def remove(self, value):
        """remove(value): remove it; KeyError if absent."""
        self._check_alive()
        i, _ = self._index(value)
        if i is None:
            raise KeyError(value)
        del self._entry[1][i]

    def _update(self, iterable, replace):
        self._require_lookup(self._type())
        try:
            items = list(iterable)
        except TypeError:
            raise TypeError("a set can only be assigned from an iterable") from None
        converted = [self._convert(self._type(), item, "element") for item in items]
        if replace:
            del self._entry[1][:]
        for value in converted:
            if not any(_same_key(existing, value) for existing in self._entry[1]):
                self._entry[1].append(value)

    def update(self, iterable):
        """update(iterable): add every item. Everything is converted before the set changes."""
        self._check_alive()
        self._update(iterable, replace=False)

    def clear(self):
        """clear(): remove every element."""
        self._check_alive()
        del self._entry[1][:]

    def to_list(self):
        """to_list() -> list: the elements in the engine's storage order."""
        self._check_alive()
        return list(self._entry[1])

    def to_set(self):
        """to_set() -> set: a Python set copy."""
        return set(self.to_list())

    def __repr__(self):
        if not self.is_valid():
            return "<Set (owner destroyed)>"
        if _mock_enforce_game_thread and not _in_game_thread:
            return "<Set (not readable here)>"
        return f"<Set '{self._entry[2]}' of {len(self._entry[1])} {self._type()}>"


def _property_default(type_name):
    if type_name.startswith(("object:", "weak:", "lazy:")):
        return None
    if type_name.startswith("enum:"):
        return 0
    return {"bool": False, "float": 0.0, "double": 0.0, "name": "None", "str": "", "text": "", "soft": ""}.get(type_name, 0)


_poisoned_entries = set()
_faults_survived = 0


def _selftest_call_with_block(obj, function, block):
    """Internal: call function on obj with a prebuilt parameter block (used to prove that a fault inside the game becomes a UEError)."""
    global _faults_survived
    if not (isinstance(obj, UObject) and isinstance(function, UFunction) and isinstance(block, Struct)):
        raise TypeError("_selftest_call_with_block(object, function, block) needs a UObject, a UFunction and a Struct")
    _require_game_thread()
    if any(id(entry) in _poisoned_entries for entry in block._fields.values()):
        _faults_survived += 1
        raise UEError("the game faulted (mock access violation) while running '" + _short(function._live().path) + "'; the call was abandoned, but the game may be in an inconsistent state")


def _selftest_faults_survived():
    """Internal: how many hardware faults inside engine calls were turned into UEError."""
    return _faults_survived


def _selftest_event_profile(reset=0):
    """Internal: what the bind_event broadcast callbacks cost. Returns a dict; pass 1 to reset the counters first."""
    if reset:
        _event_profile.clear()
    return dict(_event_profile)


def _selftest_manual_pe_detour():
    """Internal (C++ only): install a manual PLH detour on UObject::ProcessEvent. The mock has no engine, so it reports not armed."""
    return False


def _selftest_manual_pe_detour_calls():
    """Internal (C++ only): how many times the manual ProcessEvent detour fired. The mock has no engine, so 0."""
    return 0


def _selftest_delegate_broadcast_diag(on):
    """Internal (C++ only): wildcard capture of __DelegateSignature names. The mock has no engine."""
    return None


def _selftest_delegate_broadcasts():
    """Internal (C++ only): the recorded __DelegateSignature names. The mock has no engine, so an empty list."""
    return []


def _selftest_process_event_dump(on):
    """Internal (C++ only): enable the ProcessEvent name dump. The mock has no engine."""
    if not isinstance(on, int):
        raise TypeError(f"on must be an int, not {type(on).__name__}")


def _selftest_process_event_dump_read():
    """Internal (C++ only): return (name, ProcessEvent count, local-script count); the mock has no engine."""
    return []

def _selftest_poison_pointer(struct, name, mode):
    """Internal: overwrite an object pointer field of a Struct with a bad value; reading it must give None."""
    if not isinstance(struct, Struct):
        raise TypeError("_selftest_poison_pointer needs a Struct")
    if mode not in (0, 1, 2, 3, 4):
        raise UEError("unknown poison mode")
    entry = _find_property(struct._fields, name)
    if not entry[0].startswith("object:"):
        raise UEError("not a plain object pointer property")
    entry[1] = None  # the real module stores garbage that reads as None
    _poisoned_entries.add(id(entry))


def _selftest_struct_stats():
    """Internal: counts of owned structs created, destroyed, queued for destruction and leaked (all zero here)."""
    return {"created": 0, "destroyed": 0, "queued": 0, "leaked_inner": 0, "leaked_block": 0, "live": 0}
