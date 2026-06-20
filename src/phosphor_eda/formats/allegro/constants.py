"""Constants from KiCad's reverse-engineered Allegro binary importer."""

from __future__ import annotations

from enum import Enum, StrEnum


class AllegroVersion(Enum):
    V_160 = "V_160"
    V_162 = "V_162"
    V_164 = "V_164"
    V_165 = "V_165"
    V_166 = "V_166"
    V_172 = "V_172"
    V_174 = "V_174"
    V_175 = "V_175"
    V_180 = "V_180"


class AllegroBoardUnits(StrEnum):
    MILS = "mils"
    INCHES = "inches"
    MILLIMETERS = "millimeters"
    CENTIMETERS = "centimeters"
    MICROMETERS = "micrometers"


MAGIC_TO_VERSION = {
    0x00130000: AllegroVersion.V_160,
    0x00130400: AllegroVersion.V_162,
    0x00130C00: AllegroVersion.V_164,
    0x00131000: AllegroVersion.V_165,
    0x00131500: AllegroVersion.V_166,
    0x00140400: AllegroVersion.V_172,
    0x00140500: AllegroVersion.V_172,
    0x00140600: AllegroVersion.V_172,
    0x00140700: AllegroVersion.V_172,
    0x00140900: AllegroVersion.V_174,
    0x00140E00: AllegroVersion.V_174,
    0x00141500: AllegroVersion.V_175,
    0x00150000: AllegroVersion.V_180,
}

BOARD_UNITS = {
    0x01: AllegroBoardUnits.MILS,
    0x02: AllegroBoardUnits.INCHES,
    0x03: AllegroBoardUnits.MILLIMETERS,
    0x04: AllegroBoardUnits.CENTIMETERS,
    0x05: AllegroBoardUnits.MICROMETERS,
}

VERSION_STRING_BYTES = 60
PRE_V18_VERSION_STRING_OFFSET = 0xF8
V18_VERSION_STRING_OFFSET = 0x124
UNIT_DIVISOR_OFFSET = 0x26C
LAYER_MAP_OFFSET = 0x428
STRING_TABLE_OFFSET = 0x1200
LAYER_MAP_ENTRY_COUNT = 25
MAX_STRING_TABLE_ENTRIES = 1_000_000
