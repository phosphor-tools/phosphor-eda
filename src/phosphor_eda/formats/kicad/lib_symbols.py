"""KiCad embedded symbol-library parsing helpers."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import phosphor_eda.formats.kicad.sexp as sexp

if TYPE_CHECKING:
    from phosphor_eda.formats.kicad.sexp import SExpNode
    from phosphor_eda.formats.kicad.source import KiCadPoint


@dataclass(frozen=True)
class LibPin:
    """A pin declared on a library symbol unit, in library coordinates."""

    number: str
    name: str
    pin_type: str
    x: float
    y: float


type LibPins = dict[str, dict[int, list[LibPin]]]

# KiCad overline: ~{TEXT} means TEXT with overline bar.
# Bare ~ means "no name" (unnamed pin).
_OVERLINE_RE = re.compile(r"~\{([^}]+)\}")
_SUB_SYMBOL_UNIT_RE = re.compile(r"_(\d+)_(\d+)$")
_LIB_ID_SUFFIX_RE = re.compile(r"_\d+$")


def strip_kicad_markup(name: str) -> str:
    """Strip KiCad text markup from a name."""
    if not name or name == "~":
        return ""
    return _OVERLINE_RE.sub(r"\1", name)


def parse_lib_symbols(lib_syms: SExpNode) -> tuple[LibPins, dict[str, str]]:
    """Parse embedded lib_symbols into per-unit pin definitions and descriptions."""
    pins_result: LibPins = {}
    desc_result: dict[str, str] = {}
    for sym in lib_syms[1:]:
        if sexp.tag(sym) != "symbol" or not isinstance(sym, list):
            continue
        lib_id = str(sym[1])
        desc = sexp.find_property(sym[2:], "ki_description")
        if desc:
            desc_result[lib_id] = desc
        units: dict[int, list[LibPin]] = {}
        for child in sym[2:]:
            if sexp.tag(child) != "symbol" or not isinstance(child, list):
                continue
            sub_name = str(child[1])
            match = _SUB_SYMBOL_UNIT_RE.search(sub_name)
            unit_num = int(match.group(1)) if match else 1
            for elem in child[1:]:
                if sexp.tag(elem) != "pin" or not isinstance(elem, list):
                    continue
                pin_type = str(elem[1])
                pnum = pname = ""
                px = py = 0.0
                for pe in elem[3:]:
                    if not isinstance(pe, list):
                        continue
                    tag_name = sexp.tag(pe)
                    if tag_name == "number":
                        pnum = sexp.val(pe)
                    elif tag_name == "name":
                        pname = strip_kicad_markup(sexp.val(pe))
                    elif tag_name == "at":
                        px = sexp.num(pe, 1)
                        py = sexp.num(pe, 2)
                units.setdefault(unit_num, []).append(
                    LibPin(number=pnum, name=pname, pin_type=pin_type, x=px, y=py)
                )
        pins_result[lib_id] = units
    return pins_result, desc_result


def resolve_lib_pins(lib_id: str, lib_pins: LibPins) -> dict[int, list[LibPin]]:
    """Resolve a placed instance's lib_id to its pin definitions."""
    if lib_id in lib_pins:
        return lib_pins[lib_id]
    base = lib_id.split(":")[-1] if ":" in lib_id else lib_id
    for key, units in lib_pins.items():
        key_base = _LIB_ID_SUFFIX_RE.sub("", key)
        if key_base == base:
            return units
    return {}


def lib_description(lib_id: str, lib_descs: dict[str, str]) -> str:
    """Resolve a placed instance's lib_id to its library description."""
    if lib_id in lib_descs:
        return lib_descs[lib_id]
    base = lib_id.split(":")[-1] if ":" in lib_id else lib_id
    for key, description in lib_descs.items():
        key_base = _LIB_ID_SUFFIX_RE.sub("", key)
        if key_base == base:
            return description
    return ""


def transform_pin(
    lib_x: float,
    lib_y: float,
    comp_x: float,
    comp_y: float,
    comp_rot: float,
    mirror: str | None = None,
) -> KiCadPoint:
    """Transform a pin from library coordinates to schematic coordinates.

    KiCad semantics (verified against kicad-cli netlists): flip the library
    y-axis into screen coordinates, rotate by the placement angle (positive
    is counterclockwise on screen, i.e. clockwise in math convention), then
    apply ``(mirror x|y)`` in screen coordinates.
    """
    sx, sy = lib_x, -lib_y
    rad = math.radians(comp_rot)
    rx = sx * math.cos(rad) + sy * math.sin(rad)
    ry = -sx * math.sin(rad) + sy * math.cos(rad)
    if mirror == "x":
        ry = -ry
    elif mirror == "y":
        rx = -rx
    return round(comp_x + rx, 4), round(comp_y + ry, 4)
