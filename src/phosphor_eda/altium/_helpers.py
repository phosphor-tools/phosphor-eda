"""Shared parsing utilities for Altium schematic and PCB parsers.

This module is the canonical home for property-dict helpers and binary
struct readers. Other modules should import from here rather than
duplicating the logic.
"""

from __future__ import annotations

import re
import struct

# DistanceFromTop fractional properties use 1/100000 resolution.
_FRAC_DENOM = 100_000

# Matches ``PREFIX[START..END]`` in Altium bus notation.
_BUS_RANGE_RE = re.compile(r"^(.*?)\[(\d+)\.\.(\d+)\]$")


# ---------------------------------------------------------------------------
# Property-dict helpers (text-based Altium records)
# ---------------------------------------------------------------------------


def prop_int(props: dict[str, str], key: str, default: int = 0) -> int:
    """Read an integer property, returning *default* on missing or invalid."""
    val = props.get(key, "")
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def prop_bool(props: dict[str, str], key: str) -> bool:
    """Read a boolean property (``T`` or ``TRUE`` → True, case-insensitive)."""
    return props.get(key, "").upper() in ("T", "TRUE")


def prop_str(
    props: dict[str, str],
    key: str,
    default: str = "",
    utf8: bool = False,
) -> str:
    """Read a string property with optional ``%UTF8%`` fallback.

    When *utf8* is True, checks ``%utf8%{key}`` first and falls back
    to *key* if the UTF-8 variant is absent.
    """
    if utf8:
        utf8_val = props.get(f"%utf8%{key}", "")
        if utf8_val:
            return utf8_val
    return props.get(key, default)


def prop_location(props: dict[str, str]) -> tuple[int, int]:
    """Read ``location.x`` and ``location.y`` as an (x, y) tuple."""
    return (prop_int(props, "location.x"), prop_int(props, "location.y"))


def prop_points(props: dict[str, str]) -> list[tuple[int, int]]:
    """Parse ``LocationCount`` / ``X1,Y1`` / ``X2,Y2`` / ... into points."""
    loc_count = prop_int(props, "locationcount", 2)
    points: list[tuple[int, int]] = []
    for i in range(1, loc_count + 1):
        x = prop_int(props, f"x{i}")
        y = prop_int(props, f"y{i}")
        points.append((x, y))
    return points


def distance_from_top(props: dict[str, str]) -> int:
    """Compute DistanceFromTop in standard Altium units.

    DistanceFromTop is stored in x10 encoding (mils).
    The ``_Frac1`` suffix adds sub-unit precision at 1/100000 resolution.
    """
    dist = prop_int(props, "distancefromtop")
    frac = prop_int(props, "distancefromtop_frac1")
    return round(dist * 10 + frac / _FRAC_DENOM)


def compute_pin_tip(
    location: tuple[int, int],
    pin_length: int,
    orientation: int,
) -> tuple[int, int]:
    """Compute pin wire-connection point from body origin + length + direction.

    Orientations: 0=right, 1=up, 2=left, 3=down.
    """
    ox, oy = location
    if orientation == 0:  # right
        return (ox + pin_length, oy)
    elif orientation == 1:  # up
        return (ox, oy + pin_length)
    elif orientation == 2:  # left
        return (ox - pin_length, oy)
    else:  # down
        return (ox, oy - pin_length)


def parse_bus_notation(text: str) -> list[str] | None:
    """Expand Altium bus notation into individual signal names.

    Handles range notation (``D[0..7]``), descending ranges (``D[7..0]``),
    and comma-separated mixed forms (``D[0..3],CLK,RESET``).

    Returns ``None`` if *text* does not contain any range notation.
    """
    if "[" not in text:
        return None

    members: list[str] = []
    has_range = False

    for token in text.split(","):
        token = token.strip()
        m = _BUS_RANGE_RE.match(token)
        if m:
            has_range = True
            prefix = m.group(1)
            start = int(m.group(2))
            end = int(m.group(3))
            step = 1 if start <= end else -1
            for i in range(start, end + step, step):
                members.append(f"{prefix}{i}")
        else:
            members.append(token)

    return members if has_range else None


# ---------------------------------------------------------------------------
# Binary struct readers
# ---------------------------------------------------------------------------


def u16(data: bytes | memoryview, offset: int) -> int:
    """Read uint16 (little-endian) from *data* at *offset*."""
    return int.from_bytes(data[offset : offset + 2], "little", signed=False)


def i32(data: bytes | memoryview, offset: int) -> int:
    """Read int32 (little-endian) from *data* at *offset*."""
    return int.from_bytes(data[offset : offset + 4], "little", signed=True)


def u32(data: bytes | memoryview, offset: int) -> int:
    """Read uint32 (little-endian) from *data* at *offset*."""
    return int.from_bytes(data[offset : offset + 4], "little", signed=False)


def f64(data: bytes | memoryview, offset: int) -> float:
    """Read float64 (little-endian) from *data* at *offset*."""
    result: tuple[float] = struct.unpack_from("<d", data, offset)
    return result[0]
