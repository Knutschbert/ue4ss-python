"""Restricted bearer-token file provisioning for the loopback live listener."""

from __future__ import annotations

import csv
import os
import re
import secrets
import stat
import subprocess
import tempfile
from pathlib import Path

MIN_TOKEN_BYTES = 32
MAX_TOKEN_BYTES = 256


class TokenSecurityError(PermissionError):
    """A token file cannot be established as private to the current user."""


def _check_posix_mode(path: Path, info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise TokenSecurityError("token path must be a regular file")
    if info.st_mode & 0o077:
        raise TokenSecurityError("token file is accessible by group or other users; require mode 0600")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise TokenSecurityError("token file must be owned by the current user")


def _windows_user_sid() -> str:
    try:
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True, text=True, errors="replace", timeout=5, check=True,
        )
        row = next(csv.reader([result.stdout.strip()]))
        sid = row[-1].strip()
    except (OSError, subprocess.SubprocessError, StopIteration, csv.Error, IndexError) as error:
        raise TokenSecurityError("cannot determine current Windows user SID") from error
    if not sid.startswith("S-1-"):
        raise TokenSecurityError("whoami did not return a valid Windows user SID")
    return sid


def _set_and_check_windows_acl(path: Path) -> None:
    _set_windows_acl(path, "(R)")


def _set_windows_directory_acl(path: Path) -> None:
    _set_windows_acl(path, "(OI)(CI)(F)")


def _set_windows_acl(path: Path, grant: str) -> None:
    sid = _windows_user_sid()
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:{grant}"],
            capture_output=True, text=True, errors="replace", timeout=10, check=True,
        )
        # Windows user profile/temp directories commonly carry explicit SYSTEM,
        # Administrators and OWNER RIGHTS ACEs. /inheritance:r removes inherited
        # entries but, per icacls semantics, intentionally leaves explicit ACEs.
        # Remove these known local defaults; verification below still fails closed
        # for any other principal.
        subprocess.run(
            [
                "icacls", str(path), "/remove:g",
                "*S-1-5-18",       # LOCAL SYSTEM
                "*S-1-5-32-544",   # BUILTIN\\Administrators
                "*S-1-3-4",        # OWNER RIGHTS
            ],
            capture_output=True, text=True, errors="replace", timeout=10, check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise TokenSecurityError("cannot set and verify a restricted token-file ACL") from error
    _check_windows_acl(path)


def _windows_acl_sids(path: Path) -> list[str]:
    """Read trustee SIDs from icacls' saved DACL format, independent of UI locale."""
    try:
        with tempfile.TemporaryDirectory(prefix="ue4ss-live-acl-") as temporary:
            acl_file = Path(temporary) / "dacl.txt"
            subprocess.run(
                ["icacls", str(path), "/save", str(acl_file)],
                capture_output=True, text=True, errors="replace", timeout=10, check=True,
            )
            raw = acl_file.read_bytes()
    except (OSError, subprocess.SubprocessError) as error:
        raise TokenSecurityError("cannot verify token-file ACL") from error
    try:
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = raw.decode("utf-16")
        elif b"\x00" in raw:
            text = raw.decode("utf-16le")
        else:
            text = raw.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise TokenSecurityError("Windows returned an unrecognized ACL encoding") from error

    records = re.findall(r"\(([^()]*)\)", text)
    if not records:
        return []
    sids = []
    for record in records:
        fields = record.split(";")
        if len(fields) != 6 or not fields[-1]:
            raise TokenSecurityError("Windows returned an unrecognized ACL entry")
        sids.append(fields[-1].upper())
    return sids


def _check_windows_acl(path: Path) -> None:
    sid = _windows_user_sid()
    ace_sids = _windows_acl_sids(path)
    if not ace_sids or any(ace_sid != sid.upper() for ace_sid in ace_sids):
        raise TokenSecurityError("token-file ACL is not restricted to the current user SID")


def read_token(path: str | os.PathLike[str]) -> str:
    """Read a bounded token without following symlinks and verify restrictive access."""
    target = Path(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(target, flags)
    except OSError as error:
        raise TokenSecurityError(f"cannot safely open token file: {target}") from error
    try:
        info = os.fstat(descriptor)
        if os.name == "nt":
            _check_windows_acl(target)
        else:
            _check_posix_mode(target, info)
        if info.st_size < MIN_TOKEN_BYTES or info.st_size > MAX_TOKEN_BYTES + 1:
            raise TokenSecurityError("token file has an invalid size")
        chunks = []
        remaining = MAX_TOKEN_BYTES + 1
        while remaining:
            block = os.read(descriptor, remaining)
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    if not MIN_TOKEN_BYTES <= len(raw) <= MAX_TOKEN_BYTES:
        raise TokenSecurityError("token file has an invalid size")
    try:
        token = raw.decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise TokenSecurityError("token must be ASCII") from error
    if any(character.isspace() for character in token):
        raise TokenSecurityError("token must not contain whitespace")
    return token


def provision_token(path: str | os.PathLike[str]) -> str:
    """Create a fresh token in an exclusive, owner-only file; never overwrite."""
    target = Path(path)
    parent_existed = target.parent.exists()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt":
        if parent_existed:
            _check_windows_acl(target.parent)
        else:
            _set_windows_directory_acl(target.parent)
    else:
        parent_info = target.parent.stat()
        if not stat.S_ISDIR(parent_info.st_mode):
            raise TokenSecurityError("token parent must be a directory")
        if parent_info.st_mode & 0o077:
            raise TokenSecurityError("token parent directory must be private (mode 0700)")
        if hasattr(os, "getuid") and parent_info.st_uid != os.getuid():
            raise TokenSecurityError("token parent directory must be owned by the current user")
    token = secrets.token_urlsafe(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(target, flags, 0o600)
    except FileExistsError as error:
        raise TokenSecurityError("refusing to overwrite an existing token file") from error
    except OSError as error:
        raise TokenSecurityError(f"cannot create token file: {target}") from error
    try:
        payload = memoryview(token.encode("ascii") + b"\n")
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if os.name == "nt":
            _set_and_check_windows_acl(target)
        else:
            os.chmod(target, 0o600)
            _check_posix_mode(target, os.stat(target, follow_symlinks=False))
        # Return only after the exact stored credential passes normal validation.
        stored = read_token(target)
        if not secrets.compare_digest(stored, token):
            raise TokenSecurityError("token file verification failed")
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            target.unlink()
        except OSError:
            pass
        raise
    return token


def validate_token_file(path: str | os.PathLike[str]) -> str:
    """Alias emphasizing that token-file validation is part of startup policy."""
    return read_token(path)


__all__ = ["TokenSecurityError", "provision_token", "read_token", "validate_token_file"]
