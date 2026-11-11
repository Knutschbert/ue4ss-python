#!/usr/bin/env python3
"""List the DLLs a Windows PE file imports (normal and delay-load), without dumpbin: used by the release check.

    python3 tools/pe_imports.py path/to/main.dll
"""
import struct
import sys
from pathlib import Path
from typing import List


class PEError(ValueError):
    pass


def _sections(data: bytes, first_section: int, count: int) -> list:
    sections = []
    for i in range(count):
        virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from("<IIII", data, first_section + i * 40 + 8)
        sections.append((virtual_address, max(virtual_size, raw_size), raw_pointer))
    return sections


def _to_offset(sections: list, rva: int) -> int:
    for virtual_address, size, raw_pointer in sections:
        if virtual_address <= rva < virtual_address + size:
            return rva - virtual_address + raw_pointer
    raise PEError(f"RVA {rva:#x} is outside every section")


def _cstring(data: bytes, offset: int) -> str:
    end = data.index(b"\0", offset)
    return data[offset:end].decode("ascii", "replace")


def imports(data: bytes) -> List[str]:
    """Names of the imported DLLs in file order, import table first, then delay-load imports."""
    if data[:2] != b"MZ":
        raise PEError("not a PE file (no MZ header)")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise PEError("not a PE file (no PE signature)")
    section_count, = struct.unpack_from("<H", data, pe + 6)
    optional_size, = struct.unpack_from("<H", data, pe + 20)
    optional = pe + 24
    magic, = struct.unpack_from("<H", data, optional)
    if magic not in (0x10B, 0x20B):
        raise PEError(f"unknown optional header magic {magic:#x}")
    directories = optional + (96 if magic == 0x10B else 112)
    sections = _sections(data, optional + optional_size, section_count)

    names: List[str] = []
    import_rva, import_size = struct.unpack_from("<II", data, directories + 1 * 8)
    if import_rva:
        offset = _to_offset(sections, import_rva)
        while True:
            fields = struct.unpack_from("<IIIII", data, offset)
            if fields == (0, 0, 0, 0, 0):
                break
            names.append(_cstring(data, _to_offset(sections, fields[3])))
            offset += 20
    delay_rva, _ = struct.unpack_from("<II", data, directories + 13 * 8)
    if delay_rva:
        offset = _to_offset(sections, delay_rva)
        while True:
            attributes, name_rva = struct.unpack_from("<II", data, offset)
            if name_rva == 0:
                break
            names.append(_cstring(data, _to_offset(sections, name_rva)))
            offset += 32
    return names


def imports_of(path: Path) -> List[str]:
    return imports(Path(path).read_bytes())


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    for name in imports_of(Path(sys.argv[1])):
        print(name)
