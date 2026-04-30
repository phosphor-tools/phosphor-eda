"""Component and net classification utilities.

Shared helpers for identifying passive components, power nets, and
extracting reference designator prefixes. Lives in its own module
to avoid import cycles between serialize and trace.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phosphor_eda.schematic import Net

PASSIVE_PREFIXES = ("R", "C", "L", "D", "FB", "F", "Y")

_POWER_NET_RE = re.compile(r"^P?\d+V\d*$")


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
    if upper in ("GND", "VCC", "VDD", "VSS", "VBAT"):
        return True
    if _POWER_NET_RE.match(upper):
        return True
    return bool(net is not None and net.metadata.get("ClassName") == "PWR")
