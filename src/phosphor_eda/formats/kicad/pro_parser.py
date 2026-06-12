"""Parse a KiCad .kicad_pro project file for net classes.

The .kicad_pro file is JSON with a `net_settings` object containing:
- `classes`: array of net class definitions
- `netclass_assignments`: dict mapping net names to net class names
- `netclass_patterns`: array of {netclass, pattern} for wildcard matching
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from phosphor_eda.domain.project import NetClass

if TYPE_CHECKING:
    from pathlib import Path


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


def parse_kicad_pro(path: Path) -> list[NetClass]:
    """Parse net classes from a .kicad_pro file.

    Returns a list of NetClass objects with members populated from both
    explicit assignments and pattern-based assignments.
    """
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)

    net_settings = data.get("net_settings", {})
    # Migrated projects carry explicit nulls for unused fields, so guard
    # against None as well as absence.
    classes_raw: list[dict[str, object]] = net_settings.get("classes") or []
    assignments: dict[str, str] = net_settings.get("netclass_assignments") or {}
    patterns: list[dict[str, str]] = net_settings.get("netclass_patterns") or []

    # Build net classes from definitions
    net_classes: dict[str, NetClass] = {}
    for cls in classes_raw:
        name = _as_str(cls.get("name"))
        nc = NetClass(
            name=name,
            clearance_mm=_as_float(cls.get("clearance")),
            trace_width_mm=_as_float(cls.get("track_width")),
            via_diameter_mm=_as_float(cls.get("via_diameter")),
            via_drill_mm=_as_float(cls.get("via_drill")),
            diff_pair_width_mm=_as_float(cls.get("diff_pair_width")),
            diff_pair_gap_mm=_as_float(cls.get("diff_pair_gap")),
            microvia_diameter_mm=_as_float(cls.get("microvia_diameter")),
            microvia_drill_mm=_as_float(cls.get("microvia_drill")),
        )
        net_classes[name] = nc

    # Populate members from explicit net-to-class assignments
    for net_name, class_name in assignments.items():
        if class_name in net_classes:
            net_classes[class_name].members.append(net_name)

    # Populate members from pattern-based assignments
    for entry in patterns:
        class_name = entry.get("netclass", "")
        pattern = entry.get("pattern", "")
        if class_name in net_classes and pattern:
            net_classes[class_name].members.append(pattern)

    return list(net_classes.values())
