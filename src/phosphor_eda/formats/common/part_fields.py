"""Shared recognized-name resolution for typed component fields.

One table for all formats: Altium ``Manufacturer [N] Part Number`` /
``Supplier Part Number N``, KiCad ``MPN``/``Manufacturer``/``Datasheet``,
OrCAD CIS column conventions. Typed fields (part numbers, datasheet, the
DNP convention flag) are resolved from a component's ordered parameter
list; the raw parameters stay untouched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from phosphor_eda.domain.schematic import Parameter, PartNumber

if TYPE_CHECKING:
    from collections.abc import Sequence

# Whole-value DNP conventions (decision 25): exact match, case-insensitive.
_DNP_VALUES = frozenset(
    {
        "DNP",
        "DNI",
        "DNF",
        "NF",
        "NOPOP",
        "DO NOT POPULATE",
        "DO NOT PLACE",
        "NOT FITTED",
        "NO STUFF",
        "NO LOAD",
    }
)

# OrCAD CIS column names whose presence (with a non-falsy value) means DNP.
_DNP_PARAM_NAMES = frozenset({"no_mount", "_dnp"})
_FALSY_PARAM_VALUES = frozenset({"", "0", "no", "false"})

# Datasheet-bearing parameter names. KiCad's mandatory field uses "~" as an
# explicit empty placeholder.
_DATASHEET_NAMES = frozenset({"datasheet"})
_DATASHEET_PLACEHOLDERS = frozenset({"", "~"})

# Part-number parameter names: each regex captures the optional ordinal so
# the matching manufacturer/supplier name parameter can be paired up.
_MANUFACTURER_PN_RE = re.compile(r"^manufacturer(?: (\d+))? part number$")
_SUPPLIER_PN_RE = re.compile(r"^supplier part number(?: (\d+))?$")
_PLAIN_PN_NAMES = frozenset({"mpn", "part number"})


@dataclass(frozen=True)
class ResolvedPartFields:
    """Typed fields resolved from a component's parameters."""

    part_numbers: list[PartNumber] = field(default_factory=list)
    datasheet: str = ""
    dnp_convention: bool = False


def is_dnp_value(value: str) -> bool:
    """Whole-value DNP convention match, case-insensitive."""
    return value.strip().upper() in _DNP_VALUES


def _name_index(parameters: Sequence[Parameter]) -> dict[str, str]:
    """First-occurrence-wins lookup of parameter values by lowercased name."""
    index: dict[str, str] = {}
    for param in parameters:
        index.setdefault(param.name.lower(), param.value)
    return index


def _manufacturer_for(names: dict[str, str], prefix: str, ordinal: str | None) -> str:
    key = f"{prefix} {ordinal}" if ordinal else prefix
    return names.get(key, "")


def _part_number_for(param: Parameter, names: dict[str, str]) -> PartNumber | None:
    lowered = param.name.lower()
    match = _MANUFACTURER_PN_RE.match(lowered)
    if match:
        return PartNumber(
            manufacturer=_manufacturer_for(names, "manufacturer", match.group(1)),
            number=param.value,
        )
    match = _SUPPLIER_PN_RE.match(lowered)
    if match:
        return PartNumber(
            manufacturer=_manufacturer_for(names, "supplier", match.group(1)),
            number=param.value,
        )
    if lowered in _PLAIN_PN_NAMES:
        return PartNumber(manufacturer=names.get("manufacturer", ""), number=param.value)
    return None


def _is_dnp_by_convention(parameters: Sequence[Parameter], part: str) -> bool:
    if is_dnp_value(part):
        return True
    for param in parameters:
        if is_dnp_value(param.value):
            return True
        if (
            param.name.lower() in _DNP_PARAM_NAMES
            and param.value.strip().lower() not in _FALSY_PARAM_VALUES
        ):
            return True
    return False


def resolve_part_fields(parameters: Sequence[Parameter], *, part: str = "") -> ResolvedPartFields:
    """Resolve typed part fields from an ordered parameter list.

    *part* is the component's part/Comment/Value identity, included in the
    DNP convention check (some designs mark DNP in the Comment field only).
    """
    names = _name_index(parameters)

    part_numbers: list[PartNumber] = []
    datasheet = ""
    for param in parameters:
        if not datasheet and param.name.lower() in _DATASHEET_NAMES:
            candidate = param.value.strip()
            if candidate not in _DATASHEET_PLACEHOLDERS:
                datasheet = candidate
        if not param.value:
            continue
        number = _part_number_for(param, names)
        if number is not None and number not in part_numbers:
            part_numbers.append(number)

    return ResolvedPartFields(
        part_numbers=part_numbers,
        datasheet=datasheet,
        dnp_convention=_is_dnp_by_convention(parameters, part),
    )
