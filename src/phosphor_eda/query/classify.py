"""Component and net classification utilities.

Shared helpers for identifying passive components, power nets, and
extracting reference designator prefixes. Lives in its own module
to avoid import cycles between serialize and trace.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phosphor_eda.domain.schematic import Net

PASSIVE_PREFIXES = ("R", "C", "L", "D", "FB", "F", "Y")

# Named rails and grounds matched on the whole name, so decorated signals like
# GND_DETECT or VCC_SENSE stay signals.
_POWER_NET_NAMES = frozenset(
    {
        "GND",
        "VCC",
        "VDD",
        "VSS",
        "VBAT",
        "VBUS",
        "VIN",
        "VEE",
        "AVDD",
        "DVDD",
        "AGND",
        "PGND",
        "DGND",
    }
)

# Voltage-rail patterns, optional +/-/P prefix:
#   value-first: 3V3, 5V, 12V0, P3V3, +5V, -12V, 3.3V, 1.8V, +1V8
#   rail-first (Altium/OrCAD VxPy): V3P3, V1P8, V5P0
_POWER_NET_RE = re.compile(
    r"^[+\-P]?(?:\d+V\d*|\d*\.\d+V|\d+\.\d*V|V\d+P\d+)$",
)


def ref_prefix(reference: str) -> str:
    """Extract alpha prefix from a reference designator
    ("R10" -> "R", "FB1" -> "FB")."""
    prefix = ""
    for ch in reference:
        if ch.isalpha():
            prefix += ch
        else:
            break
    return prefix


def is_power_net(name: str, net: Net | None = None) -> bool:
    upper = name.upper()
    if upper in _POWER_NET_NAMES:
        return True
    if _POWER_NET_RE.match(upper):
        return True
    return bool(net is not None and net.metadata.get("ClassName") == "PWR")
