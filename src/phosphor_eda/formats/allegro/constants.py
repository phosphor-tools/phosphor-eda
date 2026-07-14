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


_VERSION_ORDER = {
    AllegroVersion.V_160: 160,
    AllegroVersion.V_162: 162,
    AllegroVersion.V_164: 164,
    AllegroVersion.V_165: 165,
    AllegroVersion.V_166: 166,
    AllegroVersion.V_172: 172,
    AllegroVersion.V_174: 174,
    AllegroVersion.V_175: 175,
    AllegroVersion.V_180: 180,
}


def version_at_least(version: AllegroVersion, minimum: AllegroVersion) -> bool:
    """Return whether ``version`` is at least ``minimum`` in release order."""
    return _VERSION_ORDER[version] >= _VERSION_ORDER[minimum]


class AllegroBoardUnits(StrEnum):
    MILS = "mils"
    INCHES = "inches"
    MILLIMETERS = "millimeters"
    CENTIMETERS = "centimeters"
    MICROMETERS = "micrometers"


def allegro_unit_to_mm(units: AllegroBoardUnits, unit_divisor: int) -> float:
    """Return the millimeter scale for one raw Allegro coordinate unit."""
    if unit_divisor <= 0:
        raise ValueError(f"Allegro unit divisor must be positive, got {unit_divisor}")
    if units is AllegroBoardUnits.MILS:
        return 0.0254 / unit_divisor
    if units is AllegroBoardUnits.INCHES:
        return 25.4 / unit_divisor
    if units is AllegroBoardUnits.MILLIMETERS:
        return 1.0 / unit_divisor
    if units is AllegroBoardUnits.CENTIMETERS:
        return 10.0 / unit_divisor
    if units is AllegroBoardUnits.MICROMETERS:
        return 0.001 / unit_divisor
    raise ValueError(f"unsupported Allegro board unit {units}")


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

# Pad-component shape codes → PCB pad shape name. A pad component whose type is
# absent from this table is a "custom" shape-symbol component: its non-zero
# string_key references a 0x28 flash shape record rather than a primitive shape.
PAD_COMPONENT_SHAPES = {
    0x02: "circle",
    0x03: "octagon",
    0x05: "rect",
    0x06: "rect",
    0x07: "diamond",
    0x0B: "oval",
    0x0C: "oval",
    0x1B: "roundrect",
    0x1C: "rect",
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
