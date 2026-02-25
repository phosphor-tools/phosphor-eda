import struct

from ecad_tools.dsn.binary_reader import BinaryReader, PREAMBLE


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
