"""Host tests for the bounded authenticated TCP wire primitives."""

from __future__ import annotations

import os
import socket
import stat
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python" / "lib"))
from ue4ss.live.framing import (  # noqa: E402
    HEADER,
    MAX_FRAME_BYTES,
    FrameDecoder,
    FrameError,
    authenticate_envelope,
    create_listener,
    encode_frame,
    send_frame,
    validate_bind_host,
    validate_envelope,
    verify_token,
)
from ue4ss.live.security import (  # noqa: E402
    TokenSecurityError,
    provision_token,
    read_token,
)


def envelope(kind="request", *, request_id="req-1", payload=None):
    value = {"version": 1, "kind": kind, "payload": payload or {}}
    if request_id is not None:
        value["id"] = request_id
    return value


def test_incremental_decoder_handles_fragmented_and_coalesced_frames():
    first = encode_frame(envelope())
    second = encode_frame(envelope("ping", request_id=None, payload={"n": 2}))
    decoder = FrameDecoder()
    assert decoder.feed(first[:2]) == []
    assert decoder.feed(first[2:7]) == []
    assert decoder.feed(first[7:] + second) == [
        envelope(), envelope("ping", request_id=None, payload={"n": 2})
    ]
    assert decoder.buffered_bytes == 0
    decoder.finish()


def test_decoder_rejects_oversize_before_waiting_for_body_or_allocating_it():
    decoder = FrameDecoder(max_bytes=32)
    with pytest.raises(FrameError, match="length"):
        decoder.feed(HEADER.pack(33))
    assert decoder.buffered_bytes == 0


@pytest.mark.parametrize("body", [
    b"\xff",
    b'{"version":1,"kind":"ping","payload":{"x":NaN}}',
    b'{"version":1,"kind":"ping","kind":"pong","payload":{}}',
    b'{"version":true,"kind":"ping","payload":{}}',
    b'{"version":1,"kind":"bogus","payload":{}}',
    b'{"version":1,"kind":"request","id":"bad id","payload":{}}',
])
def test_decoder_rejects_malformed_or_ambiguous_json_envelopes(body):
    decoder = FrameDecoder()
    with pytest.raises(FrameError):
        decoder.feed(HEADER.pack(len(body)) + body)


def test_rejects_invalid_frame_lengths_and_partial_eof():
    for size in (0, MAX_FRAME_BYTES + 1):
        decoder = FrameDecoder()
        with pytest.raises(FrameError):
            decoder.feed(HEADER.pack(size))
    decoder = FrameDecoder()
    decoder.feed(encode_frame(envelope())[:-1])
    with pytest.raises(FrameError, match="partial"):
        decoder.finish()


def test_authentication_is_constant_time_policy_and_pre_auth_is_restricted():
    token = "t" * 43
    auth = envelope("auth", request_id=None, payload={"token": token})
    assert authenticate_envelope(auth, token)
    assert not authenticate_envelope(envelope("request"), token)
    assert not authenticate_envelope(envelope("auth", request_id=None, payload={"token": "wrong"}), token)
    assert not verify_token(token, "short")
    with pytest.raises(FrameError, match="before authentication"):
        validate_envelope(envelope("request"), authenticated=False)
    assert validate_envelope(envelope("request"), authenticated=True)["kind"] == "request"


def test_frame_encoder_rejects_nonfinite_payload_and_oversized_frame():
    with pytest.raises(FrameError):
        encode_frame(envelope("ping", request_id=None, payload={"bad": float("inf")}))
    with pytest.raises(FrameError, match="frame body"):
        encode_frame(envelope("ping", request_id=None, payload={"data": "x" * 100}), max_bytes=20)


class ShortWriter:
    def __init__(self, limit=3):
        self.limit = limit
        self.data = bytearray()

    def send(self, data):
        count = min(len(data), self.limit)
        self.data.extend(data[:count])
        return count


def test_send_frame_retries_partial_writes_until_complete():
    writer = ShortWriter()
    expected = encode_frame(envelope())
    count = send_frame(writer, envelope(), deadline=time.monotonic() + 1)
    assert count == len(expected)
    assert bytes(writer.data) == expected


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.1", "localhost", "example.com", ""])
def test_bind_policy_rejects_non_loopback_or_hostname(host):
    with pytest.raises(ValueError):
        validate_bind_host(host)


def test_numeric_ipv4_and_ipv6_loopback_listener_binds_exclusively():
    listener = create_listener("127.0.0.1", 0)
    try:
        host, port = listener.getsockname()[:2]
        assert host == "127.0.0.1"
        assert port > 0
        with pytest.raises(OSError):
            duplicate = create_listener(host, port)
            duplicate.close()
    finally:
        listener.close()

    if socket.has_ipv6:
        try:
            listener6 = create_listener("::1", 0)
        except OSError:
            pytest.skip("IPv6 loopback unavailable in this environment")
        try:
            assert listener6.getsockname()[0] == "::1"
        finally:
            listener6.close()


def test_token_provisioning_is_private_and_never_overwrites(tmp_path):
    path = tmp_path / "private" / "live.token"
    token = provision_token(path)
    assert read_token(path) == token
    assert len(token) >= 32
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(TokenSecurityError, match="overwrite"):
        provision_token(path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode-bit mutation test")
def test_token_loader_rejects_group_or_world_readable_file(tmp_path):
    path = tmp_path / "token"
    path.write_text("x" * 43 + "\n", encoding="ascii")
    path.chmod(0o644)
    with pytest.raises(TokenSecurityError, match="mode 0600"):
        read_token(path)


def test_token_loader_rejects_symlink(tmp_path):
    real = tmp_path / "real"
    real.write_text("x" * 43 + "\n", encoding="ascii")
    real.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(TokenSecurityError):
        read_token(link)
