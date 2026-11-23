"""Compatibility import for the canonical packaged live protocol."""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parents[1] / "python" / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

from ue4ss.live.protocol import (  # noqa: E402,F401
    DuplicateRequest,
    MAX_CODE_BYTES,
    MAX_OUTPUT_CHARS,
    MAX_TIMEOUT_SECONDS,
    OPERATIONS,
    PROTOCOL_VERSION,
    STATUSES,
    ProtocolError,
    make_request,
    make_response,
    read_record,
    validate_request,
    validate_request_id,
    validate_response,
    write_request,
    write_response,
)

__all__ = [
    "DuplicateRequest",
    "MAX_CODE_BYTES",
    "MAX_OUTPUT_CHARS",
    "MAX_TIMEOUT_SECONDS",
    "OPERATIONS",
    "PROTOCOL_VERSION",
    "STATUSES",
    "ProtocolError",
    "make_request",
    "make_response",
    "read_record",
    "validate_request",
    "validate_request_id",
    "validate_response",
    "write_request",
    "write_response",
]
