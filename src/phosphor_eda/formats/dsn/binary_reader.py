"""Binary reader and constants for OrCAD Capture DSN file parsing.

Based on the reverse-engineering work of the OpenOrCadParser C++ project:
https://github.com/Werni2A/OpenOrCadParser
"""

import struct

PREAMBLE = b"\xff\xe4\x5c\x39"

# Structure type IDs consumed by the parser (from OpenOrCadParser Enums/Structure.hpp).
STRUCT_WIRE_SCALAR = 20
STRUCT_WIRE_BUS = 21
STRUCT_PORT = 23
STRUCT_BUS_ENTRY = 29
STRUCT_GLOBAL = 37
STRUCT_OFF_PAGE_CONNECTOR = 38
STRUCT_NET_GROUP = 103

PAGE_SETTINGS_SIZE = 156  # Fixed-size block


class BinaryReader:
    def __init__(self, data: bytes, name: str = "") -> None:
        self.data = data
        self.pos = 0
        self.name = name

    def read_uint8(self) -> int:
        val = self.data[self.pos]
        self.pos += 1
        return val

    def read_int16(self) -> int:
        val = struct.unpack_from("<h", self.data, self.pos)[0]
        self.pos += 2
        return val

    def read_uint16(self) -> int:
        val = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return val

    def read_int32(self) -> int:
        val = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return val

    def read_uint32(self) -> int:
        val = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return val

    def read_bytes(self, n: int) -> bytes:
        result = self.data[self.pos : self.pos + n]
        self.pos += n
        return result

    def skip(self, n: int) -> None:
        self.pos += n

    def read_string_zero(self) -> str:
        end = self.data.index(b"\x00", self.pos)
        s = self.data[self.pos : end].decode("ascii", errors="replace")
        self.pos = end + 1
        return s

    def read_string_len_zero(self) -> str:
        """Read a length-prefixed null-terminated string.

        Format: [uint16 length] [length bytes of string] [0x00 null terminator]
        The length does NOT include the null terminator.
        """
        length = self.read_uint16()
        if length == 0:
            # Still consume the null terminator
            if self.pos < len(self.data) and self.data[self.pos] == 0:
                self.pos += 1
            return ""
        raw = self.data[self.pos : self.pos + length]
        self.pos += length
        # Skip the null terminator
        if self.pos < len(self.data) and self.data[self.pos] == 0:
            self.pos += 1
        return raw.decode("ascii", errors="replace")

    def at_preamble(self) -> bool:
        return self.data[self.pos : self.pos + 4] == PREAMBLE

    def try_read_preamble(self) -> bool:
        """Read preamble if present. Returns True if found."""
        if self.at_preamble():
            self.skip(4)  # magic
            data_len = self.read_uint32()
            self.skip(data_len)  # trailing preamble data
            return True
        return False

    def read_prefix_chain(self) -> tuple[int, int, list[tuple[int, int]]]:
        """Read a prefix chain.

        Returns (type_id, end_offset, name_value_pairs).

        Tries prefix counts from 10 down to 1. Each long-form prefix is 9 bytes:
        [1 byte type_id] [4 bytes byte_offset] [4 bytes padding]
        The last prefix is short form: [1 byte type_id] [2 bytes size]
        followed by optional name-value pairs (uint32 nameIdx, uint32 valueIdx).

        end_offset is the maximum stop offset across all long prefixes, or -1
        if there was only a short prefix.
        """
        save_pos = self.pos

        for num_prefixes in range(10, 0, -1):
            self.pos = save_pos
            try:
                return self._try_read_prefixes(num_prefixes)
            except (IndexError, struct.error, ValueError):
                continue

        self.pos = save_pos
        raise ValueError(f"Cannot parse prefix chain at offset {self.pos}")

    def _try_read_prefixes(self, count: int) -> tuple[int, int, list[tuple[int, int]]]:
        """Attempt to read exactly `count` prefixes."""
        type_id = None
        max_end_offset = -1
        stream_len = len(self.data)

        # Read (count-1) long-form prefixes (9 bytes each)
        for _ in range(count - 1):
            prefix_offset = self.pos
            tid = self.read_uint8()
            if type_id is not None and tid != type_id:
                raise ValueError(f"Prefix type mismatch: {tid} != {type_id}")
            type_id = tid
            byte_offset = self.read_uint32()
            self.read_uint32()  # padding

            end_offset = prefix_offset + 9 + byte_offset
            if end_offset > stream_len:
                raise ValueError(f"Prefix end {end_offset} > stream size {stream_len}")
            if end_offset > max_end_offset:
                max_end_offset = end_offset

        # Read last prefix (short form): type_id + int16 size
        tid = self.read_uint8()
        if type_id is not None and tid != type_id:
            raise ValueError(f"Short prefix type mismatch: {tid} != {type_id}")
        type_id = tid

        pairs: list[tuple[int, int]] = []
        size = self.read_int16()
        if size > 0:
            needed = size * 8
            if self.pos + needed > stream_len:
                raise ValueError(
                    f"Name-value pairs need {needed} bytes, only {stream_len - self.pos} left"
                )
            for _ in range(size):
                name_idx = self.read_uint32()
                value_idx = self.read_uint32()
                pairs.append((name_idx, value_idx))

        return type_id, max_end_offset, pairs

    def read_prefixes(self) -> int:
        """Read prefix chain and return just the type_id."""
        type_id, _, _ = self.read_prefix_chain()
        return type_id

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def eof(self) -> bool:
        return self.pos >= len(self.data)
