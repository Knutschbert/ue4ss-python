import hashlib
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import make_release  # noqa: E402
import pe_imports  # noqa: E402


def make_pe(imports, delay=()):
    """A minimal PE32+ file with one section holding the import (and delay-load) tables."""
    section_rva, section_raw = 0x1000, 0x400
    blob = bytearray()
    names = {}

    def put(text):
        names[text] = len(blob)
        blob.extend(text.encode() + b"\0")

    for n in list(imports) + list(delay):
        put(n)
    descriptors_at = len(blob)
    blob.extend(b"\0" * (20 * (len(imports) + 1)))
    delay_at = len(blob)
    blob.extend(b"\0" * (32 * (len(delay) + 1)))
    for i, n in enumerate(imports):
        struct.pack_into("<IIIII", blob, descriptors_at + 20 * i, 0, 0, 0, section_rva + names[n], 0)
    for i, n in enumerate(delay):
        struct.pack_into("<II", blob, delay_at + 32 * i, 1, section_rva + names[n])
    optional_size = 112 + 16 * 8
    dirs = bytearray(16 * 8)
    struct.pack_into("<II", dirs, 1 * 8, section_rva + descriptors_at if imports else 0, 20 * (len(imports) + 1))
    struct.pack_into("<II", dirs, 13 * 8, section_rva + delay_at if delay else 0, 32 * (len(delay) + 1))
    pe_offset = 0x40
    header = bytearray(section_raw)
    header[0:2] = b"MZ"
    struct.pack_into("<I", header, 0x3C, pe_offset)
    header[pe_offset:pe_offset + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", header, pe_offset + 4, 0x8664, 1, 0, 0, 0, optional_size, 0x2022)
    optional = pe_offset + 24
    struct.pack_into("<H", header, optional, 0x20B)
    header[optional + 112:optional + 112 + len(dirs)] = dirs
    section = optional + optional_size
    header[section:section + 8] = b".idata\0\0"
    struct.pack_into("<IIII", header, section + 8, len(blob), section_rva, len(blob), section_raw)
    return bytes(header) + bytes(blob)


def build_mod(root, main_imports=("KERNEL32.dll", "VCRUNTIME140.dll", "UE4SS.dll"), extra=None):
    mod = root / "PythonScripting"
    (mod / "dlls").mkdir(parents=True)
    (mod / "python_scripts").mkdir()
    (mod / "enabled.txt").write_text("")
    (mod / "dlls" / "main.dll").write_bytes(make_pe(main_imports))
    (mod / "dlls" / "python312.dll").write_bytes(make_pe(["KERNEL32.dll", "VCRUNTIME140.dll", "api-ms-win-crt-stdio-l1-1-0.dll"]))
    (mod / "dlls" / "python312.zip").write_bytes(b"zip")
    (mod / "dlls" / "vcruntime140.dll").write_bytes(make_pe(["KERNEL32.dll"]))
    (mod / "dlls" / "LICENSE.txt").write_text("PSF LICENSE AGREEMENT")
    for name, data in (extra or {}).items():
        (mod / "dlls" / name).write_bytes(data)
    return mod


class PeImportsTest(unittest.TestCase):
    def test_reads_normal_and_delay_load_imports(self):
        data = make_pe(["A.dll", "b.dll"], delay=["Late.dll"])
        self.assertEqual(pe_imports.imports(data), ["A.dll", "b.dll", "Late.dll"])

    def test_no_imports(self):
        self.assertEqual(pe_imports.imports(make_pe([])), [])

    def test_rejects_other_files(self):
        with self.assertRaises(pe_imports.PEError):
            pe_imports.imports(b"not a pe file")

    def test_a_real_system_dll_when_available(self):
        kernel32 = Path("/mnt/c/Windows/System32/kernel32.dll")
        if not kernel32.exists():
            self.skipTest("no Windows System32 here")
        self.assertTrue(any(n.lower().startswith("api-ms-win") for n in pe_imports.imports_of(kernel32)))


class CheckModTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_complete_mod_passes_and_unshipped_ue4ss_is_a_note(self):
        mod = build_mod(self.root)
        errors, notes = make_release.check_mod(mod)
        self.assertEqual(errors, [])
        self.assertTrue(any("UE4SS.dll" in n for n in notes))

    def test_redistributable_import_needs_a_host(self):
        mod = build_mod(self.root, main_imports=("KERNEL32.dll", "MSVCP140.dll"))
        errors, _ = make_release.check_mod(mod)
        self.assertTrue(any("MSVCP140.dll" in e for e in errors))
        host = self.root / "UE4SS.dll"
        host.write_bytes(make_pe(["MSVCP140.dll"]))
        errors, notes = make_release.check_mod(mod, [host])
        self.assertEqual(errors, [])
        self.assertTrue(any("MSVCP140.dll" in n for n in notes))

    def test_system_dir_does_not_vouch_for_redistributables(self):
        system = self.root / "sys"
        system.mkdir()
        (system / "MSVCP140.dll").write_bytes(b"x")
        (system / "odd.dll").write_bytes(b"x")
        mod = build_mod(self.root, main_imports=("MSVCP140.dll", "odd.dll"))
        errors, _ = make_release.check_mod(mod, system_dir=system)
        self.assertEqual(len(errors), 1)
        self.assertIn("MSVCP140.dll", errors[0])

    def test_debug_runtime_is_refused(self):
        mod = build_mod(self.root, main_imports=("KERNEL32.dll", "MSVCP140D.dll", "VCRUNTIME140D.dll"))
        errors, _ = make_release.check_mod(mod)
        self.assertEqual(sum("debug runtime" in e for e in errors), 2)

    def test_unresolved_import_of_an_extension_module(self):
        mod = build_mod(self.root, extra={"_odd.pyd": make_pe(["missing_lib.dll"])})
        errors, _ = make_release.check_mod(mod)
        self.assertTrue(any("_odd.pyd imports missing_lib.dll" in e for e in errors))

    def test_missing_pieces_are_reported(self):
        mod = build_mod(self.root)
        (mod / "dlls" / "LICENSE.txt").unlink()
        (mod / "dlls" / "python312.zip").unlink()
        (mod / "dlls" / "vcruntime140.dll").unlink()
        errors, _ = make_release.check_mod(mod)
        self.assertTrue(any("LICENSE.txt" in e for e in errors))
        self.assertTrue(any("standard library" in e for e in errors))
        self.assertTrue(any("vcruntime140" in e for e in errors))

    def test_other_python_version_is_refused(self):
        mod = build_mod(self.root)
        (mod / "dlls" / "python312.dll").rename(mod / "dlls" / "python313.dll")
        errors, _ = make_release.check_mod(mod)
        self.assertTrue(any("cp312" in e for e in errors))


class ReleaseZipTest(unittest.TestCase):
    def test_zip_is_reproducible_and_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod = build_mod(root)
            entries = make_release.collect(mod, "9.9", "RE-UE4SS test", None)
            one, two = root / "one.zip", root / "two.zip"
            make_release.write_zip(one, entries)
            make_release.write_zip(two, make_release.collect(mod, "9.9", "RE-UE4SS test", None))
            self.assertEqual(hashlib.sha256(one.read_bytes()).hexdigest(), hashlib.sha256(two.read_bytes()).hexdigest())
            self.assertEqual(make_release.verify_zip(one), [])
            with zipfile.ZipFile(one) as archive:
                names = archive.namelist()
                self.assertIn("PythonScripting/dlls/main.dll", names)
                self.assertIn("PSF LICENSE AGREEMENT", archive.read("THIRD_PARTY_NOTICES.txt").decode())
                self.assertIn("RE-UE4SS test", archive.read("README.txt").decode())

    def test_a_developers_main_py_is_replaced_by_a_stub(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod = build_mod(root)
            (mod / "python_scripts" / "main.py").write_text("print('my private script')")
            entries = dict(make_release.collect(mod, "9.9", "x", None))
            shipped = entries["PythonScripting/python_scripts/main.py"].decode()
            self.assertNotIn("private", shipped)
            self.assertIn("import ue4ss", shipped)
            empty = build_mod(root / "other")
            (empty / "python_scripts").rmdir()
            (empty / "python_scripts").mkdir()
            self.assertIn("PythonScripting/python_scripts/main.py", dict(make_release.collect(empty, "9.9", "x", None)))

    def test_verify_detects_a_changed_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entries = make_release.collect(build_mod(root), "9.9", "x", None)
            tampered = [(n, b"changed" if n.endswith("enabled.txt") else d) for n, d in entries]
            # keep the original checksum list so the mismatch is visible
            target = root / "t.zip"
            make_release.write_zip(target, tampered)
            self.assertTrue(any("checksum mismatch" in p for p in make_release.verify_zip(target)))

    def test_main_refuses_when_the_check_fails_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod = build_mod(root, main_imports=("MSVCP140.dll",))
            out = root / "out"
            code = make_release.main(["--mod-dir", str(mod), "--out-dir", str(out), "--host-dll", str(root / "none.dll")], system_dir=root)
            self.assertEqual(code, 1)
            self.assertFalse(out.exists())

    def test_version_comes_from_the_header(self):
        self.assertRegex(make_release.project_version(), r"^\d+\.\d+$")



class WithUe4ssTest(unittest.TestCase):
    def test_the_matching_ue4ss_files_and_licence_go_into_the_zip_and_the_readme_says_so(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            mod = build_mod(root)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            (bin_dir / "UE4SS.dll").write_bytes(b"ue4ss")
            (bin_dir / "dwmapi.dll").write_bytes(b"proxy")
            licence = root / "LICENSE"
            licence.write_text("MIT text")
            entries = dict(make_release.collect(mod, "9.9", "RE-UE4SS commit abc", None, ue4ss_dir=bin_dir, ue4ss_licence=licence))
            self.assertEqual(entries["ue4ss/UE4SS.dll"], b"ue4ss")
            self.assertEqual(entries["ue4ss/dwmapi.dll"], b"proxy")
            self.assertEqual(entries["ue4ss/LICENSE.txt"], b"MIT text")
            readme = entries["README.txt"].decode()
            self.assertIn("copy ue4ss/UE4SS.dll and ue4ss/dwmapi.dll", readme)
            self.assertIn("RE-UE4SS commit abc", readme)
            self.assertIn("MIT licence (ue4ss/LICENSE.txt)", entries["THIRD_PARTY_NOTICES.txt"].decode())
            self.assertIn("ue4ss/UE4SS.dll", entries["SHA256SUMS.txt"].decode())

    def test_without_the_option_nothing_of_ue4ss_is_in_the_zip(self):
        with tempfile.TemporaryDirectory() as folder:
            entries = dict(make_release.collect(build_mod(Path(folder)), "9.9", "RE-UE4SS commit abc", None))
            self.assertFalse([name for name in entries if name.startswith("ue4ss/")])
            self.assertIn("Build it from source", entries["README.txt"].decode())

    def test_a_missing_ue4ss_file_is_an_error_not_a_zip_without_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "bin").mkdir()
            (root / "bin" / "UE4SS.dll").write_bytes(b"ue4ss")
            with self.assertRaisesRegex(FileNotFoundError, "dwmapi.dll"):
                make_release.collect(build_mod(root), "9.9", "x", None, ue4ss_dir=root / "bin")

if __name__ == "__main__":
    unittest.main()
