import struct

import pytest

from phosphor_eda.formats.dsn.binary_reader import PREAMBLE, BinaryReader
from phosphor_eda.formats.dsn.errors import DsnFormatError


def test_read_uint8():
    r = BinaryReader(b"\x42")
    assert r.read_uint8() == 0x42
    assert r.eof()


def test_read_int16():
    r = BinaryReader(struct.pack("<h", -1234))
    assert r.read_int16() == -1234


def test_read_uint32():
    r = BinaryReader(struct.pack("<I", 0xDEADBEEF))
    assert r.read_uint32() == 0xDEADBEEF


def test_read_string_zero():
    r = BinaryReader(b"hello\x00rest")
    assert r.read_string_zero() == "hello"
    assert r.pos == 6


def test_read_string_len_zero():
    # Format: [uint16 length][string bytes][null]
    data = struct.pack("<H", 3) + b"abc\x00"
    r = BinaryReader(data)
    assert r.read_string_len_zero() == "abc"


def test_read_string_len_zero_empty():
    data = struct.pack("<H", 0) + b"\x00"
    r = BinaryReader(data)
    assert r.read_string_len_zero() == ""


def test_at_preamble():
    r = BinaryReader(PREAMBLE + b"\x00\x00\x00\x00")
    assert r.at_preamble()


def test_skip():
    r = BinaryReader(b"\x00" * 10)
    r.skip(5)
    assert r.pos == 5
    assert r.remaining() == 5


def test_read_bytes_rejects_truncated_payload_without_advancing() -> None:
    r = BinaryReader(b"\x01", "tiny-stream")

    with pytest.raises(DsnFormatError) as exc_info:
        r.read_bytes(2)

    assert exc_info.value.offset == 0
    assert "tiny-stream" in str(exc_info.value)
    assert r.pos == 0


def test_read_string_zero_rejects_unterminated_string_without_advancing() -> None:
    r = BinaryReader(b"unterminated", "string-stream")

    with pytest.raises(DsnFormatError) as exc_info:
        r.read_string_zero()

    assert exc_info.value.offset == 0
    assert "unterminated null-terminated string" in str(exc_info.value)
    assert r.pos == 0
