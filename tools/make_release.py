#!/usr/bin/env python3
"""Check an assembled mod folder (tools/package_mod.py output) and pack it into a release zip.

    python3 -I tools/make_release.py --mod-dir dist/PythonScripting --out-dir release --host-dll path/to/UE4SS.dll
    python tools\\make_release.py --build                 # Windows: build UE4SS, the proxy and the mod and package first
    python tools\\make_release.py --build --with-ue4ss    # also put the matching UE4SS.dll and dwmapi.dll into the zip

With --with-ue4ss the zip carries the UE4SS build the mod was made with (folder ue4ss/), so a user does not have to build
UE4SS: the two files replace the ones next to the game's executable. The zip is then named ...-win64-with-ue4ss.zip.

The check refuses to build the zip when something the mod needs at load time would be missing on a clean machine:
every DLL imported by main.dll, python3NN.dll and the extension modules must be shipped in dlls/, be a Windows system DLL,
or be imported by a host DLL that is certainly loaded (UE4SS.dll). Debug runtimes are refused. The CPython licence must be in
the folder. See docs/RELEASING.md for the checklist this automates.
"""
import argparse
import hashlib
import re
import sys
import zipfile
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pe_imports import PEError, imports_of  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MOD_NAME = "PythonScripting"
UE4SS_REF = "RE-UE4SS commit 97adba01 (after 3.0.1)"
UE4SS_FILES = ("UE4SS.dll", "dwmapi.dll")

# Present on every supported Windows (the Universal CRT forwarders and the base system libraries). Anything else must be
# found in System32 (when that is readable) or shipped.
SYSTEM_PATTERNS = (re.compile(r"^api-ms-win-.*\.dll$", re.I), re.compile(r"^ext-ms-.*\.dll$", re.I))
SYSTEM_NAMES = {n.lower() for n in (
    "kernel32.dll", "kernelbase.dll", "ntdll.dll", "user32.dll", "gdi32.dll", "advapi32.dll", "shell32.dll", "ole32.dll",
    "oleaut32.dll", "ws2_32.dll", "bcrypt.dll", "crypt32.dll", "version.dll", "shlwapi.dll", "ucrtbase.dll", "rpcrt4.dll",
    "iphlpapi.dll", "userenv.dll", "winmm.dll", "dbghelp.dll", "psapi.dll", "secur32.dll", "ncrypt.dll", "msvcrt.dll",
    "comdlg32.dll", "imm32.dll", "setupapi.dll", "wininet.dll", "mswsock.dll", "wldap32.dll", "cabinet.dll", "msi.dll",
    "propsys.dll", "wtsapi32.dll", "netapi32.dll", "d3d11.dll", "dxgi.dll", "dwmapi.dll", "opengl32.dll")}
# Installed by the Visual C++ Redistributable, not by Windows: System32 having them proves nothing about a clean machine.
REDIST = re.compile(r"^(msvcp|vcruntime|concrt|vcomp|vccorlib|mfc)\d+.*\.dll$", re.I)
DEBUG_RUNTIME = re.compile(r"(^|[^a-z])(msvcp\d+d|vcruntime\d+d|ucrtbased|python\d+_d|msvcr\d+d)", re.I)

STUB_MAIN = "# Put your script here. It runs on the game thread at the first engine tick.\nimport ue4ss\n\nprint(\"PythonScripting is running\")\n"

REQUIRED_FILES = ("enabled.txt", "dlls/main.dll", "dlls/LICENSE.txt", "python_scripts")


def is_system_dll(name: str, system_dir: Optional[Path]) -> bool:
    lowered = name.lower()
    if REDIST.match(name):
        return False
    if lowered in SYSTEM_NAMES or any(p.match(name) for p in SYSTEM_PATTERNS):
        return True
    return bool(system_dir and (system_dir / name).exists())


def find_system_dir() -> Optional[Path]:
    for candidate in (Path("C:/Windows/System32"), Path("/mnt/c/Windows/System32")):
        if candidate.is_dir():
            return candidate
    return None


def check_mod(mod_dir: Path, host_dlls: Iterable[Path] = (), system_dir: Optional[Path] = None) -> Tuple[List[str], List[str]]:
    """(errors, notes). Errors block the release; notes say how each non-obvious import is satisfied."""
    errors: List[str] = []
    notes: List[str] = []
    for rel in REQUIRED_FILES:
        if not (mod_dir / rel).exists():
            errors.append(f"missing {rel}")
    dlls = mod_dir / "dlls"
    if not dlls.is_dir():
        return errors + ["no dlls directory"], notes

    python_dlls = sorted(p.name for p in dlls.glob("python3[0-9]*.dll"))
    if len(python_dlls) != 1:
        errors.append(f"expected exactly one python3NN.dll, found {python_dlls}")
    elif python_dlls[0].lower() != "python312.dll":
        errors.append(f"{python_dlls[0]}: the mod is built against CPython 3.12 (cp312)")
    if not list(dlls.glob("python3[0-9]*.zip")):
        errors.append("no python3NN.zip (the standard library)")
    if not any(dlls.glob("vcruntime140*.dll")):
        errors.append("no vcruntime140*.dll next to python3NN.dll")

    host_imports = set()
    for host in host_dlls:
        try:
            host_imports |= {n.lower() for n in imports_of(host)}
        except (OSError, PEError) as e:
            errors.append(f"cannot read host dll {host}: {e}")

    shipped = {p.name.lower() for p in dlls.iterdir()}
    checked = sorted(p for p in dlls.iterdir() if p.suffix.lower() in (".dll", ".pyd"))
    for binary in checked:
        try:
            names = imports_of(binary)
        except (OSError, PEError) as e:
            errors.append(f"{binary.name}: cannot read imports: {e}")
            continue
        for name in names:
            lowered = name.lower()
            if DEBUG_RUNTIME.search(name):
                errors.append(f"{binary.name} imports the debug runtime {name}")
            elif lowered in shipped or is_system_dll(name, system_dir):
                continue
            elif lowered in host_imports:
                notes.append(f"{binary.name}: {name} is not shipped; the host's UE4SS.dll imports it too")
            elif lowered == "ue4ss.dll" and binary.name.lower() == "main.dll":
                notes.append("main.dll: UE4SS.dll is the host and must match the build's RE-UE4SS (see docs/RELEASING.md)")
            else:
                errors.append(f"{binary.name} imports {name}, which is neither shipped, a system DLL nor provided by the host")
    return errors, sorted(set(notes))


def project_version() -> str:
    text = (ROOT / "core" / "include" / "pyue" / "version.hpp").read_text(encoding="utf-8")
    major = re.search(r"version_major\s*=\s*(\d+)", text)
    minor = re.search(r"version_minor\s*=\s*(\d+)", text)
    if not major or not minor:
        raise ValueError("cannot read the version from core/include/pyue/version.hpp")
    return f"{major.group(1)}.{minor.group(1)}"


def readme_text(version: str, ue4ss_ref: str, with_ue4ss: bool = False) -> str:
    ue4ss_step = (f"""0. This zip includes the UE4SS build the mod was made with ({ue4ss_ref}): copy ue4ss/UE4SS.dll and ue4ss/dwmapi.dll
   next to the game's executable (<Game>/Binaries/Win64), replacing the UE4SS files there. Keep your UE4SS-settings.ini
   and Mods folder. Without a working UE4SS-settings.ini for your game, see https://docs.ue4ss.com/dev/installation-guide.html
""" if with_ue4ss else "")
    return f"""PythonScripting {version} for UE4SS (Windows x64)

Install:
{ue4ss_step}1. Copy the PythonScripting folder into <Game>/Binaries/Win64/Mods/ (next to the other UE4SS mods).
2. Add the line "PythonScripting : 1" to Mods/mods.txt (or keep enabled.txt in the folder).
3. Put your script in PythonScripting/python_scripts/main.py and start the game.
   Output and tracebacks go to UE4SS.log with the prefix [Python].

Requirements:
- UE4SS built from exactly {ue4ss_ref}: this mod is built against that source tree and its class layouts, and
  another UE4SS build can crash the game at start.{" The matching files are in the folder ue4ss/ of this zip." if with_ue4ss else " Build it from source (see the project README) or use a release zip that includes it."}
- Unreal Engine 4.25 games are the tested target (see docs/API.md for what scripts can do).
- Microsoft Visual C++ Redistributable 2015-2022 (x64): vcruntime140*.dll is shipped, msvcp140.dll comes with the game or
  the redistributable (UE4SS itself needs it).

The folder contains CPython 3.12 (embeddable distribution); its licence and the notices of the bundled components are in
THIRD_PARTY_NOTICES.txt and PythonScripting/dlls/LICENSE.txt. docs/API.md describes the ue4ss module.
"""


def notices_text(mod_dir: Path) -> str:
    licence = (mod_dir / "dlls" / "LICENSE.txt").read_text(encoding="utf-8", errors="replace")
    bundled = sorted(p.name for p in (mod_dir / "dlls").iterdir() if p.suffix.lower() in (".dll", ".pyd") and p.name.lower() != "main.dll")
    return (
        "Third-party components shipped in PythonScripting/dlls\n"
        "======================================================\n\n"
        "CPython 3.12 (embeddable distribution, https://www.python.org): PSF licence, full text below.\n"
        "OpenSSL 3 (libcrypto-3.dll, libssl-3.dll): Apache License 2.0. libffi (libffi-8.dll): MIT. SQLite (sqlite3.dll): public domain.\n"
        "Microsoft Visual C++ runtime (vcruntime140*.dll): redistributable under the Microsoft Visual Studio licence terms.\n\n"
        "Files: " + ", ".join(bundled) + "\n\n"
        "---------------------------------------------------------------------\n"
        "Python licence text (dlls/LICENSE.txt)\n"
        "---------------------------------------------------------------------\n\n" + licence
    )


def collect(mod_dir: Path, version: str, ue4ss_ref: str, api_doc: Optional[Path], ue4ss_dir: Optional[Path] = None, ue4ss_licence: Optional[Path] = None) -> List[Tuple[str, bytes]]:
    """Archive entries (path, content), sorted so the zip is reproducible. With `ue4ss_dir` the UE4SS.dll and proxy of that
    folder are added under ue4ss/ (and the UE4SS licence text, when given)."""
    entries = []
    for path in sorted(mod_dir.rglob("*")):
        if path.is_file():
            relative = path.relative_to(mod_dir).as_posix()
            if path.suffix.lower() == ".pdb":   # gy7.4: the PDB is for in-game crash diagnosis, not the release
                continue
            # a developer's own main.py must not ship: the release gets a stub the user replaces with their script
            data = STUB_MAIN.encode("utf-8") if relative == "python_scripts/main.py" else path.read_bytes()
            entries.append((f"{MOD_NAME}/{relative}", data))
    if not any(name == f"{MOD_NAME}/python_scripts/main.py" for name, _ in entries):
        entries.append((f"{MOD_NAME}/python_scripts/main.py", STUB_MAIN.encode("utf-8")))
    if ue4ss_dir is not None:
        for name in UE4SS_FILES:
            if not (ue4ss_dir / name).is_file():
                raise FileNotFoundError(f"{ue4ss_dir / name} not found: build the targets UE4SS and proxy first")
            entries.append((f"ue4ss/{name}", (ue4ss_dir / name).read_bytes()))
        if ue4ss_licence is not None and ue4ss_licence.is_file():
            entries.append(("ue4ss/LICENSE.txt", ue4ss_licence.read_bytes()))
    entries.append(("README.txt", readme_text(version, ue4ss_ref, with_ue4ss=ue4ss_dir is not None).encode("utf-8")))
    notices = notices_text(mod_dir)
    if ue4ss_dir is not None:
        notices = ("UE4SS (ue4ss/UE4SS.dll, ue4ss/dwmapi.dll): built from https://github.com/UE4SS-RE/RE-UE4SS, " + ue4ss_ref + ", MIT licence"
                   + (" (ue4ss/LICENSE.txt)" if ue4ss_licence is not None and ue4ss_licence.is_file() else "") + ".\n\n" + notices)
    entries.append(("THIRD_PARTY_NOTICES.txt", notices.encode("utf-8")))
    if api_doc and api_doc.exists():
        entries.append(("docs/API.md", api_doc.read_bytes()))
    sums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(entries))
    entries.append(("SHA256SUMS.txt", sums.encode("utf-8")))
    return sorted(entries, key=lambda e: e[0])


def write_zip(target: Path, entries: List[Tuple[str, bytes]]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))   # fixed time: the same input gives the same zip
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    tmp.replace(target)


def verify_zip(target: Path) -> List[str]:
    """Problems found when reading the finished zip back (empty list when it is fine)."""
    problems = []
    with zipfile.ZipFile(target) as archive:
        bad = archive.testzip()
        if bad:
            problems.append(f"corrupt member {bad}")
        names = set(archive.namelist())
        for need in ("README.txt", "THIRD_PARTY_NOTICES.txt", "SHA256SUMS.txt", f"{MOD_NAME}/dlls/main.dll", f"{MOD_NAME}/dlls/LICENSE.txt", f"{MOD_NAME}/enabled.txt"):
            if need not in names:
                problems.append(f"zip lacks {need}")
        sums = archive.read("SHA256SUMS.txt").decode("utf-8") if "SHA256SUMS.txt" in names else ""
        for line in sums.splitlines():
            digest, name = line.split("  ", 1)
            if name in names and hashlib.sha256(archive.read(name)).hexdigest() != digest:
                problems.append(f"checksum mismatch for {name}")
    return problems


def main(argv=None, system_dir: Optional[Path] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mod-dir", type=Path, default=ROOT / "dist" / MOD_NAME)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "release")
    parser.add_argument("--host-dll", type=Path, action="append", default=None,
                        help="UE4SS.dll of the target game (imports it provides count as present); default: the UE4SS.dll of this build")
    parser.add_argument("--ue4ss-ref", default=UE4SS_REF, help="text for the README's requirements line")
    parser.add_argument("--check-only", action="store_true", help="run the checks and stop")
    parser.add_argument("--build", action="store_true", help="build UE4SS, the proxy and the mod and package the mod first (Windows)")
    parser.add_argument("--with-ue4ss", action="store_true", help="put the matching UE4SS.dll and dwmapi.dll into the zip (folder ue4ss/)")
    parser.add_argument("--ue4ss-bin", type=Path, default=ROOT / "build" / "Game__Shipping__Win64" / "bin", help="where the built UE4SS.dll and dwmapi.dll are")
    parser.add_argument("--ue4ss-root", type=Path, default=ROOT.parent / "RE-UE4SS", help="the RE-UE4SS checkout (for its licence text)")
    args = parser.parse_args(argv)

    if args.build:
        import install      # here: only this option needs the build and packaging steps
        try:
            steps = install.Installer()
            steps.build_all()
            steps.package_mod()
        except install.InstallError as error:
            print("ERROR:", error, file=sys.stderr)
            return 1

    if not args.mod_dir.is_dir():
        print(f"mod folder not found: {args.mod_dir} (run tools/package_mod.py first)", file=sys.stderr)
        return 2
    host_dlls = args.host_dll if args.host_dll is not None else [p for p in (ROOT / "build" / "Game__Shipping__Win64" / "bin" / "UE4SS.dll",) if p.exists()]
    errors, notes = check_mod(args.mod_dir, host_dlls, system_dir if system_dir is not None else find_system_dir())
    for note in notes:
        print("note:", note)
    for error in errors:
        print("ERROR:", error)
    if errors:
        print(f"release check failed: {len(errors)} problem(s)")
        return 1
    print("release check ok")
    if args.check_only:
        return 0

    version = project_version()
    target = args.out_dir / f"{MOD_NAME}-{version}-win64{'-with-ue4ss' if args.with_ue4ss else ''}.zip"
    try:
        entries = collect(args.mod_dir, version, args.ue4ss_ref, ROOT / "docs" / "API.md", ue4ss_dir=args.ue4ss_bin if args.with_ue4ss else None,
                          ue4ss_licence=args.ue4ss_root / "LICENSE")
    except FileNotFoundError as error:
        print("ERROR:", error, file=sys.stderr)
        return 1
    write_zip(target, entries)
    problems = verify_zip(target) + ([f"zip lacks ue4ss/{name}" for name in UE4SS_FILES if f"ue4ss/{name}" not in zipfile.ZipFile(target).namelist()] if args.with_ue4ss else [])
    if problems:
        for problem in problems:
            print("ERROR:", problem)
        return 1
    print(f"wrote {target} ({target.stat().st_size // 1024} KiB), contents verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
