"""Shared DSN pin-name resolution.

Both the schematic converter and the netlist builder resolve a component
pin's name from the Cache symbol definitions: normalize the package name,
look up the symbol's 1-indexed pin list, and strip display overlines. This
is the single implementation they share.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from phosphor_eda.formats.common.electrical import PinElectrical
from phosphor_eda.formats.common.text import strip_overline

if TYPE_CHECKING:
    from phosphor_eda.formats.common.diagnostics import ParseContext
    from phosphor_eda.formats.dsn.raw_models import DsnSymbolPin


def normalize_package_name(package_name: str) -> str:
    """Drop the ``.Normal`` suffix used for the default symbol variant."""
    return package_name.removesuffix(".Normal")


@dataclass(frozen=True)
class OrCadPortType:
    name: str
    electrical: PinElectrical


ORCAD_PORT_TYPES = {
    0: OrCadPortType("input", PinElectrical.INPUT),
    1: OrCadPortType("bidirectional", PinElectrical.IO),
    2: OrCadPortType("output", PinElectrical.OUTPUT),
    3: OrCadPortType("open_collector", PinElectrical.OPEN_COLLECTOR),
    4: OrCadPortType("passive", PinElectrical.PASSIVE),
    5: OrCadPortType("three_state", PinElectrical.HI_Z),
    6: OrCadPortType("open_emitter", PinElectrical.OPEN_EMITTER),
    7: OrCadPortType("power", PinElectrical.POWER),
}


def _pin_index_from_int(pn: int, pin_count: int) -> int | None:
    # ``pin_number`` is the decoded 1-based display order; the old int16 sign
    # sentinel (``0xFF00`` marked pins) is stripped at parse time into
    # ``PinConnection.pin_order``, so a plain range check is all that remains.
    if 1 <= pn <= pin_count:
        return pn - 1
    return None


def _pin_index(pin_number: str, pin_count: int) -> int | None:
    try:
        pn = int(pin_number)
    except (ValueError, TypeError):
        return None
    return _pin_index_from_int(pn, pin_count)


def _normalized_pin_name(name: str) -> str:
    pin_name, _overline = strip_overline(name)
    return pin_name


def symbol_pins_align(
    legacy_pin_names: list[str],
    structured_pins: list[DsnSymbolPin],
) -> bool:
    if len(legacy_pin_names) != len(structured_pins):
        return False
    return all(
        _normalized_pin_name(legacy_name) == _normalized_pin_name(structured_pin.name)
        for legacy_name, structured_pin in zip(legacy_pin_names, structured_pins, strict=True)
    )


def resolve_pin_name(
    package_name: str,
    pin_number: str,
    symbol_pin_names: dict[str, list[str]],
    ctx: ParseContext | None = None,
    reference: str = "",
    pin_name_overrides: dict[str, str] | None = None,
) -> str:
    """Resolve a pin name from the Cache symbol definitions.

    ``pin_number`` is 1-indexed. Returns ``""`` when the number is
    non-numeric or out of range. Overlines are display markup and are
    stripped from the returned name.
    """
    override = (pin_name_overrides or {}).get(pin_number)
    if override is not None:
        pin_name, _overline = strip_overline(override)
        return pin_name

    sym_pins = symbol_pin_names.get(normalize_package_name(package_name), [])
    try:
        pn = int(pin_number)
    except (ValueError, TypeError):
        if ctx is not None:
            ctx.warn(
                "dsn_pin_number",
                f"{reference}: non-numeric pin number {pin_number!r}; pin name left blank",
            )
        return ""
    pin_index = _pin_index_from_int(pn, len(sym_pins))
    if pin_index is None:
        # Only a genuine overrun of a known symbol's pin list is a misalignment
        # worth surfacing; a symbol with no cache pins at all is a separate,
        # already-visible gap and would drown the log if warned per pin.
        if ctx is not None and sym_pins:
            ctx.warn(
                "dsn_pin_number",
                f"{reference}: pin number {pn} is out of range for symbol "
                f"{normalize_package_name(package_name)!r} ({len(sym_pins)} pins); "
                "pin name left blank",
            )
        return ""
    pin_name, _overline = strip_overline(sym_pins[pin_index])
    return pin_name


def resolve_symbol_pin(
    package_name: str,
    pin_number: str,
    symbol_pins: dict[str, list[DsnSymbolPin]],
    expected_pin_name: str = "",
    symbol_pin_names: dict[str, list[str]] | None = None,
) -> DsnSymbolPin | None:
    """Resolve a structured Cache symbol pin using OrCAD's display pin number."""
    symbol_name = normalize_package_name(package_name)
    pins = symbol_pins.get(symbol_name, [])
    # Alignment against legacy names only applies when legacy names exist for
    # the symbol; a structured-only symbol has nothing to disagree with.
    legacy_pin_names = None if symbol_pin_names is None else symbol_pin_names.get(symbol_name)
    if legacy_pin_names is not None and not symbol_pins_align(
        legacy_pin_names,
        pins,
    ):
        return None
    pin_index = _pin_index(pin_number, len(pins))
    if pin_index is None:
        return None
    pin = pins[pin_index]
    if expected_pin_name and _normalized_pin_name(pin.name) != _normalized_pin_name(
        expected_pin_name
    ):
        return None
    return pin
