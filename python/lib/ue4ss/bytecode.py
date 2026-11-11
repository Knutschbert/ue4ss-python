"""Reads the compiled bytecode of Blueprint functions (UFunction.get_script()) far enough to list what a function calls.

    from ue4ss import bytecode
    for call in bytecode.scan_calls(function):
        print(call["offset"], call["kind"], call["target"])

The walker is read-only and never executes anything. It follows the expression stream of Unreal Engine 4.25 (a token byte followed by operands,
expressions nest) and stops with `BytecodeError` as soon as the stream stops making sense, so a wrong guess about a layout cannot run away:
`parse` only returns when the last instruction is EX_EndOfScript and ends exactly at the last byte. Pointers in the stream are 8-byte
addresses of engine objects; names are 8 bytes (index, number) or 12 bytes in builds that keep a display index (`name_size`).
"""
import struct

import ue4ss as _ue

# token -> (name, operand layout). Layout items: ptr name u8 u16 u32 i32 i64 f32 bytesN cstr ustr expr params(until EndFunctionParms)
# and the repeated groups handled in code (see _read). Numbers are EExprToken of Unreal Engine 4.25, complete up to its last
# token 0x6D. Unreal Engine 5 adds and changes tokens: a function that uses one stops with BytecodeError (docs/ENGINE_VERSIONS.md).
_TOKENS = {
    0x00: ("LocalVariable", ["ptr"]), 0x01: ("InstanceVariable", ["ptr"]), 0x02: ("DefaultVariable", ["ptr"]),
    0x04: ("Return", ["expr"]), 0x06: ("Jump", ["u32"]), 0x07: ("JumpIfNot", ["u32", "expr"]),
    0x09: ("Assert", ["u16", "u8", "expr"]), 0x0B: ("Nothing", []),
    0x0F: ("Let", ["ptr", "expr", "expr"]),
    0x12: ("ClassContext", ["expr", "u32", "ptr", "expr"]), 0x13: ("MetaCast", ["ptr", "expr"]), 0x14: ("LetBool", ["expr", "expr"]),
    0x15: ("EndParmValue", []), 0x16: ("EndFunctionParms", []), 0x17: ("Self", []), 0x18: ("Skip", ["u32", "expr"]),
    0x19: ("Context", ["expr", "u32", "ptr", "expr"]), 0x1A: ("Context_FailSilent", ["expr", "u32", "ptr", "expr"]),
    0x1B: ("VirtualFunction", ["name", "params"]), 0x1C: ("FinalFunction", ["ptr", "params"]),
    0x1D: ("IntConst", ["i32"]), 0x1E: ("FloatConst", ["f32"]), 0x1F: ("StringConst", ["cstr"]), 0x20: ("ObjectConst", ["ptr"]),
    0x21: ("NameConst", ["name"]), 0x22: ("RotationConst", ["bytes12"]), 0x23: ("VectorConst", ["bytes12"]), 0x24: ("ByteConst", ["u8"]),
    0x25: ("IntZero", []), 0x26: ("IntOne", []), 0x27: ("True", []), 0x28: ("False", []),
    0x29: ("TextConst", ["text"]), 0x2A: ("NoObject", []), 0x2B: ("TransformConst", ["bytes40"]), 0x2C: ("IntConstByte", ["u8"]),
    0x2D: ("NoInterface", []), 0x2E: ("DynamicCast", ["ptr", "expr"]),
    0x2F: ("StructConst", ["ptr", "i32", "until:0x30"]), 0x30: ("EndStructConst", []),
    0x31: ("SetArray", ["expr", "until:0x32"]), 0x32: ("EndArray", []),
    0x33: ("PropertyConst", ["ptr"]), 0x34: ("UnicodeStringConst", ["ustr"]), 0x35: ("Int64Const", ["i64"]), 0x36: ("UInt64Const", ["i64"]),
    0x38: ("PrimitiveCast", ["cast"]),
    0x39: ("SetSet", ["expr", "i32", "until:0x3A"]), 0x3A: ("EndSet", []),
    0x3B: ("SetMap", ["expr", "i32", "until:0x3C"]), 0x3C: ("EndMap", []),
    0x3D: ("SetConst", ["ptr", "i32", "until:0x3E"]), 0x3E: ("EndSetConst", []),
    0x3F: ("MapConst", ["ptr", "ptr", "i32", "until:0x40"]), 0x40: ("EndMapConst", []),
    0x42: ("StructMemberContext", ["ptr", "expr"]), 0x43: ("LetMulticastDelegate", ["expr", "expr"]), 0x44: ("LetDelegate", ["expr", "expr"]),
    0x45: ("LocalVirtualFunction", ["name", "params"]), 0x46: ("LocalFinalFunction", ["ptr", "params"]),
    0x48: ("LocalOutVariable", ["ptr"]), 0x4A: ("DeprecatedOp4A", []), 0x4B: ("InstanceDelegate", ["name"]),
    0x4C: ("PushExecutionFlow", ["u32"]), 0x4D: ("PopExecutionFlow", []), 0x4E: ("ComputedJump", ["expr"]), 0x4F: ("PopExecutionFlowIfNot", ["expr"]),
    0x50: ("Breakpoint", []), 0x51: ("InterfaceContext", ["expr"]), 0x52: ("ObjToInterfaceCast", ["ptr", "expr"]), 0x53: ("EndOfScript", []),
    0x54: ("CrossInterfaceCast", ["ptr", "expr"]), 0x55: ("InterfaceToObjCast", ["ptr", "expr"]),
    0x5A: ("WireTracepoint", []), 0x5B: ("SkipOffsetConst", ["u32"]), 0x5C: ("AddMulticastDelegate", ["expr", "expr"]),
    0x5D: ("ClearMulticastDelegate", ["expr"]), 0x5E: ("Tracepoint", []), 0x5F: ("LetObj", ["expr", "expr"]), 0x60: ("LetWeakObjPtr", ["expr", "expr"]),
    0x61: ("BindDelegate", ["name", "expr", "expr"]), 0x62: ("RemoveMulticastDelegate", ["expr", "expr"]),
    0x63: ("CallMulticastDelegate", ["ptr", "expr", "params"]), 0x64: ("LetValueOnPersistentFrame", ["ptr", "expr"]),
    0x65: ("ArrayConst", ["ptr", "i32", "until:0x66"]), 0x66: ("EndArrayConst", []), 0x67: ("SoftObjectConst", ["expr"]),
    0x68: ("CallMath", ["ptr", "params"]), 0x69: ("SwitchValue", ["switch"]), 0x6A: ("InstrumentationEvent", ["instrumentation"]),
    0x6B: ("ArrayGetByRef", ["expr", "expr"]),
    0x6C: ("ClassSparseDataVariable", ["ptr"]), 0x6D: ("FieldPathConst", ["expr"]),
}
END_OF_SCRIPT = 0x53
END_FUNCTION_PARMS = 0x16
# the tokens that call a function: kind -> (target is a pointer, is a local call)
CALL_TOKENS = {
    0x1C: ("final", True, False), 0x46: ("final", True, True), 0x68: ("math", True, False),
    0x1B: ("virtual", False, False), 0x45: ("virtual", False, True), 0x63: ("delegate", True, False),
}
_MAX_DEPTH = 200
_CAST_OBJECT_TO_INTERFACE = 0x46


class BytecodeError(ValueError):
    """The byte stream is not valid bytecode (truncated, unknown token, or it does not end where it should)."""


class Node:
    """One instruction: `offset`, `token`, `name`, `values` (operands that are plain values, in order) and `children` (nested expressions)."""

    __slots__ = ("offset", "token", "name", "values", "children", "end")

    def __init__(self, offset, token, name):
        self.offset, self.token, self.name = offset, token, name
        self.values, self.children, self.end = [], [], offset

    def __repr__(self):
        return f"<{self.name}@{self.offset} {self.values} +{len(self.children)}>"

    def walk(self):
        """This node and every nested one, in stream order."""
        yield self
        for child in self.children:
            yield from child.walk()


class _Reader:
    def __init__(self, data, name_size):
        self.data, self.name_size, self.pos = data, name_size, 0

    def take(self, count):
        if self.pos + count > len(self.data):
            raise BytecodeError(f"truncated at {self.pos}: need {count} more bytes")
        chunk = self.data[self.pos:self.pos + count]
        self.pos += count
        return chunk

    def number(self, fmt):
        return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))[0]

    def name(self):
        if self.name_size == 12:
            index, _display, number = struct.unpack("<iiI", self.take(12))
        else:
            index, number = struct.unpack("<iI", self.take(8))
        return (index, number)

    def cstring(self, width):
        start = self.pos
        step = width
        while True:
            if self.pos + step > len(self.data):
                raise BytecodeError(f"unterminated string at {start}")
            if self.data[self.pos:self.pos + step] == b"\0" * step:
                text = self.data[start:self.pos]
                self.pos += step
                return text.decode("utf-16-le" if width == 2 else "latin-1", errors="replace")
            self.pos += step


def _read(reader, depth=0):
    if depth > _MAX_DEPTH:
        raise BytecodeError("expressions nested too deeply")
    offset = reader.pos
    token = reader.take(1)[0]
    if token not in _TOKENS:
        raise BytecodeError(f"unknown token {token:#x} at {offset}")
    name, layout = _TOKENS[token]
    node = Node(offset, token, name)
    for item in layout:
        if item == "expr":
            node.children.append(_read(reader, depth + 1))
        elif item == "params":
            while True:
                child = _read(reader, depth + 1)
                node.children.append(child)
                if child.token == END_FUNCTION_PARMS:
                    break
        elif item.startswith("until:"):
            stop = int(item[6:], 16)
            while True:
                child = _read(reader, depth + 1)
                node.children.append(child)
                if child.token == stop:
                    break
        elif item == "ptr":
            node.values.append(("ptr", reader.number("Q")))
        elif item == "name":
            node.values.append(("name", reader.name()))
        elif item in ("u8", "u16", "u32", "i32", "i64", "f32"):
            fmt = {"u8": "B", "u16": "H", "u32": "I", "i32": "i", "i64": "q", "f32": "f"}[item]
            node.values.append((item, reader.number(fmt)))
        elif item.startswith("bytes"):
            node.values.append(("bytes", reader.take(int(item[5:]))))
        elif item == "cstr":
            node.values.append(("str", reader.cstring(1)))
        elif item == "ustr":
            node.values.append(("str", reader.cstring(2)))
        elif item == "cast":                       # PrimitiveCast: conversion type, an interface class for object-to-interface, the expression
            kind = reader.number("B")
            node.values.append(("u8", kind))
            if kind == _CAST_OBJECT_TO_INTERFACE:
                node.values.append(("ptr", reader.number("Q")))
            node.children.append(_read(reader, depth + 1))
        elif item == "text":                        # TextConst: literal type, then 0 none, 1 three strings, 2 and 3 one string, 4 object plus two strings
            kind = reader.number("B")
            node.values.append(("u8", kind))
            if kind == 1:
                count = 3
            elif kind in (2, 3):
                count = 1
            elif kind == 4:
                node.values.append(("ptr", reader.number("Q")))
                count = 2
            elif kind == 0:
                count = 0
            else:
                raise BytecodeError(f"unknown text literal type {kind} at {offset}")
            for _ in range(count):
                node.children.append(_read(reader, depth + 1))
        elif item == "switch":                      # number of cases, end offset, index expression, (case value, next offset, result)*, default
            cases = reader.number("H")
            node.values.append(("u16", cases))
            node.values.append(("u32", reader.number("I")))
            node.children.append(_read(reader, depth + 1))
            for _ in range(cases):
                node.children.append(_read(reader, depth + 1))
                node.values.append(("u32", reader.number("I")))
                node.children.append(_read(reader, depth + 1))
            node.children.append(_read(reader, depth + 1))
        elif item == "instrumentation":             # event type; an inline event also carries a name
            kind = reader.number("B")
            node.values.append(("u8", kind))
            if kind == 4:
                node.values.append(("name", reader.name()))
        else:
            raise AssertionError("bad layout item " + item)
    node.end = reader.pos
    return node


def parse(script, name_size=8):
    """The top-level instructions of `script` (bytes) as a list of `Node`. Raises BytecodeError unless the stream is well formed and ends with
    EX_EndOfScript on its last byte."""
    data = bytes(script)
    reader = _Reader(data, name_size)
    nodes = []
    while reader.pos < len(data):
        node = _read(reader)
        nodes.append(node)
        if node.token == END_OF_SCRIPT:
            if reader.pos != len(data):
                raise BytecodeError(f"EndOfScript at {node.offset} but {len(data) - reader.pos} bytes follow")
            return nodes
    raise BytecodeError("the script does not end with EndOfScript")


_build_name_size = None


def _has_names(nodes):
    return any(kind == "name" for top in nodes for node in top.walk() for kind, _ in node.values)


def parse_auto(script):
    """parse() with the name size of the build: the size learnt from an earlier function first, else 8 bytes, then 12. Returns (nodes, name_size).
    A function without names parses with either size, so only a function that has names teaches the size (this game's builds use 12)."""
    global _build_name_size
    order = [_build_name_size] if _build_name_size else []
    order += [size for size in (8, 12) if size not in order]
    first = None
    for size in order:
        try:
            nodes = parse(script, size)
        except BytecodeError as error:
            first = first or error
            continue
        if _build_name_size is None and _has_names(nodes):
            _build_name_size = size
        return nodes, size
    raise first


def calls_in(nodes):
    """Every call instruction in the parsed `nodes`, as dicts: offset, kind (final, virtual, math, delegate), local (a call that stays inside
    Blueprint code), pointer (address of the UFunction, 0 for virtual calls) and name (index, number) for virtual calls."""
    found = []
    for top in nodes:
        for node in top.walk():
            if node.token in CALL_TOKENS:
                kind, by_pointer, local = CALL_TOKENS[node.token]
                value = node.values[0][1]
                found.append({"offset": node.offset, "kind": kind, "local": local, "token": node.name,
                              "pointer": value if by_pointer else 0, "name": None if by_pointer else value})
    return found


def scan_calls(function):
    """The functions a Blueprint `function` (a UFunction) calls, in stream order: dicts with offset, kind, local, token and `target`, the called
    function's full path (resolved from the pointer, or from the name among the functions of the owning class and its parents) or None.
    A native function has no bytecode and gives []. Raises BytecodeError for a stream that does not parse."""
    script = function.get_script()
    if not script:
        return []
    nodes, name_size = parse_auto(script)
    owner = function.get_outer()
    names = _name_table(owner)
    result = []
    for call in calls_in(nodes):
        target = None
        if call["pointer"]:
            obj = _ue.object_at_address(call["pointer"])
            target = obj.get_full_name().split(" ", 1)[1] if obj is not None else None
        elif call["name"] is not None:
            target = names.get(call["name"][0]) or _all_function_names().get(call["name"][0])
        call["target"] = target
        result.append(call)
    return result


def _name_table(owner):
    """comparison index -> function name for the functions of `owner` (a class) and its parents, to resolve virtual calls by name."""
    table = {}
    cls = owner
    while cls is not None and isinstance(cls, _ue.UStruct):
        for fname in cls.get_function_names():
            try:
                table[_ue.FName(fname).comparison_index] = fname
            except Exception:  # noqa: BLE001 - a name that cannot be looked up is just not resolved
                pass
        cls = cls.get_super()
    return table


_function_names = None


def _all_function_names():
    """comparison index -> name of every function object the game has loaded, built once (a virtual call names a function of the object it is called
    on, which can be any class). `forget_names()` drops it, for example after more Blueprint classes were loaded."""
    global _function_names
    if _function_names is None:
        table = {}
        for func in _ue.find_all_of("Function"):
            fname = func.get_fname()
            table.setdefault(fname.comparison_index, fname.to_string())
        _function_names = table
    return _function_names


def forget_names():
    """Drop the cached table of function names (see scan_calls)."""
    global _function_names
    _function_names = None
