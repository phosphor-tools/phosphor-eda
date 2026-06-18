"""Cadence packaged netlist sidecar parsing for OrCAD projects."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.formats.common.raw_models import ParsedDesign

_PART_RE = re.compile(r"^\s+(\S+)\s+'([^']+)':;")
_PRIMITIVE_RE = re.compile(r"^primitive '([^']+)';")
_PIN_RE = re.compile(r"^\s+'([^']+)':")
_PIN_NUMBER_RE = re.compile(r"^\s+PIN_NUMBER='\(([^)]*)\)';")


def apply_packaged_pin_names(raw: ParsedDesign, netlist_dir: Path) -> None:
    """Apply pstxprt/pstchip primitive pin-number maps to placed instances."""
    pstxprt = netlist_dir / "pstxprt.dat"
    pstchip = netlist_dir / "pstchip.dat"
    if not pstxprt.exists() or not pstchip.exists():
        return

    primitive_by_ref = _parse_pstxprt_primitives(pstxprt)
    pins_by_primitive = parse_pstchip_pin_maps(pstchip)
    if not primitive_by_ref or not pins_by_primitive:
        return

    for page in raw.pages:
        for instance in page.instances:
            primitive = primitive_by_ref.get(instance.reference)
            if primitive is None:
                continue
            pin_names = pins_by_primitive.get(primitive)
            if not pin_names:
                continue
            instance.pin_name_overrides = pin_names


def _parse_pstxprt_primitives(path: Path) -> dict[str, str]:
    primitives: dict[str, str] = {}
    for line in path.read_text(errors="replace").splitlines():
        match = _PART_RE.match(line)
        if match is None:
            continue
        primitives[match.group(1)] = match.group(2)
    return primitives


def parse_pstchip_pin_maps(path: Path) -> dict[str, dict[str, str]]:
    primitives: dict[str, dict[str, str]] = {}
    current_primitive = ""
    current_pin_name = ""
    current_pin_index = 0
    current_pins: list[tuple[int, str, str]] = []
    in_pin_block = False

    for line in path.read_text(errors="replace").splitlines():
        primitive_match = _PRIMITIVE_RE.match(line)
        if primitive_match is not None:
            current_primitive = primitive_match.group(1)
            current_pin_name = ""
            current_pin_index = 0
            current_pins = []
            in_pin_block = False
            if current_primitive not in primitives:
                primitives[current_primitive] = {}
            continue
        if not current_primitive:
            continue
        stripped = line.strip()
        if stripped == "pin":
            in_pin_block = True
            current_pin_index = 0
            current_pins = []
            continue
        if stripped == "end_pin;":
            if all(_is_scalar_pin_number(value) for _, _, value in current_pins):
                primitives[current_primitive] = {
                    str(index): name for index, name, _ in current_pins
                }
            else:
                primitives[current_primitive].update(
                    {
                        str(index): name
                        for index, name, value in current_pins
                        if _is_decimal_scalar_pin_number(value)
                    }
                )
            in_pin_block = False
            current_pin_name = ""
            current_pin_index = 0
            current_pins = []
            continue
        if not in_pin_block:
            continue
        pin_match = _PIN_RE.match(line)
        if pin_match is not None:
            current_pin_index += 1
            current_pin_name = pin_match.group(1)
            continue
        number_match = _PIN_NUMBER_RE.match(line)
        if number_match is not None:
            if current_pin_name:
                current_pins.append((current_pin_index, current_pin_name, number_match.group(1)))
            continue

    return primitives


def _is_scalar_pin_number(value: str) -> bool:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    return len(parts) == 1 and parts[0] != "0"


def _is_decimal_scalar_pin_number(value: str) -> bool:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    return len(parts) == 1 and parts[0].isdigit() and parts[0] != "0"
