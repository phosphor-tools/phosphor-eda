"""Bounded binary reads for Cadence Allegro / OrCAD PCB source files."""

from __future__ import annotations

import struct

from phosphor_eda.formats.allegro.errors import AllegroParseError


class BoundedBinaryReader:
    """Little-endian byte reader that fails before moving past available data."""

    def __init__(self, data: bytes, *, source_name: str = "<bytes>") -> None:
        self._data = data
        self._offset = 0
        self._source_name = source_name

    @property
    def offset(self) -> int:
        return self._offset

    @property
    def size(self) -> int:
        return len(self._data)

    @property
    def source_name(self) -> str:
        return self._source_name

    def seek(self, offset: int) -> None:
        if offset < 0 or offset > self.size:
            raise AllegroParseError(
                f"seek target 0x{offset:X} is outside file size {self.size}",
                code="invalid-seek",
                offset=offset,
                source_name=self._source_name,
            )
        self._offset = offset

    def skip(self, byte_count: int) -> None:
        if byte_count < 0:
            raise AllegroParseError(
                f"cannot read negative byte count {byte_count}",
                code="negative-read",
                offset=self._offset,
                source_name=self._source_name,
            )
        self._require(byte_count)
        self._offset += byte_count

    def read_uint8(self) -> int:
        return self.read_bytes(1)[0]

    def read_uint16(self) -> int:
        offset = self._offset
        self._require(2)
        value = struct.unpack_from("<H", self._data, offset)[0]
        self._offset += 2
        return value

    def read_uint32(self) -> int:
        offset = self._offset
        self._require(4)
        value = struct.unpack_from("<I", self._data, offset)[0]
        self._offset += 4
        return value

    def read_int32(self) -> int:
        offset = self._offset
        self._require(4)
        value = struct.unpack_from("<i", self._data, offset)[0]
        self._offset += 4
        return value

    def read_bytes(self, byte_count: int) -> bytes:
        if byte_count < 0:
            raise AllegroParseError(
                f"cannot read negative byte count {byte_count}",
                code="negative-read",
                offset=self._offset,
                source_name=self._source_name,
            )
        offset = self._offset
        self._require(byte_count)
        self._offset += byte_count
        return self._data[offset : offset + byte_count]

    def read_c_string(self) -> str:
        end = self._data.find(b"\x00", self._offset)
        if end == -1:
            raise AllegroParseError(
                "unterminated null-terminated string",
                code="unterminated-string",
                offset=self._offset,
                source_name=self._source_name,
            )
        raw = self._data[self._offset : end]
        self._offset = end + 1
        return raw.decode("latin1")

    def read_fixed_string(self, byte_count: int) -> str:
        raw = self.read_bytes(byte_count)
        return raw.split(b"\x00", 1)[0].decode("latin1")

    def _require(self, byte_count: int) -> None:
        if self._offset + byte_count > self.size:
            raise AllegroParseError(
                f"read of {byte_count} bytes exceeds file size {self.size}",
                code="truncated-read",
                offset=self._offset,
                source_name=self._source_name,
            )
