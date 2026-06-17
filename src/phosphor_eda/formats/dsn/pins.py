"""Shared DSN pin-name resolution.

Both the schematic converter and the netlist builder resolve a component
pin's name from the Cache symbol definitions: normalize the package name,
look up the symbol's 1-indexed pin list, and strip display overlines. This
is the single implementation they share.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.formats.common.text import strip_overline

if TYPE_CHECKING:
    from phosphor_eda.formats.common.diagnostics import ParseContext


def normalize_package_name(package_name: str) -> str:
    """Drop the ``.Normal`` suffix used for the default symbol variant."""
    return package_name.removesuffix(".Normal")


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
    if not 1 <= pn <= len(sym_pins):
        if pn >= 0xFF00:
            sentinel_index = 0x10000 - pn
            if 1 <= sentinel_index <= len(sym_pins):
                pin_name, _overline = strip_overline(sym_pins[sentinel_index - 1])
                return pin_name
        return ""
    pin_name, _overline = strip_overline(sym_pins[pn - 1])
    return pin_name
