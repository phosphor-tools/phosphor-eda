"""Parse a KiCad .kicad_pro project file for net classes.

The .kicad_pro file is JSON with a `net_settings` object containing:
- `classes`: array of net class definitions
- `netclass_assignments`: dict mapping net names to net class names
- `netclass_patterns`: array of {netclass, pattern} for wildcard matching
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from phosphor_eda.project import NetClass

if TYPE_CHECKING:
    from pathlib import Path


def parse_kicad_pro(path: Path) -> list[NetClass]:
    """Parse net classes from a .kicad_pro file.

    Returns a list of NetClass objects with members populated from both
    explicit assignments and pattern-based assignments.
    """
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)

    net_settings = data.get("net_settings", {})
    classes_raw = net_settings.get("classes", [])
    assignments = net_settings.get("netclass_assignments", {})
    patterns = net_settings.get("netclass_patterns", [])

    # Build net classes from definitions
    net_classes: dict[str, NetClass] = {}
    for cls in classes_raw:
        name = cls.get("name", "")
        nc = NetClass(
            name=name,
            clearance_mm=cls.get("clearance", 0.0),
            trace_width_mm=cls.get("track_width", 0.0),
            via_diameter_mm=cls.get("via_diameter", 0.0),
            via_drill_mm=cls.get("via_drill", 0.0),
            diff_pair_width_mm=cls.get("diff_pair_width", 0.0),
            diff_pair_gap_mm=cls.get("diff_pair_gap", 0.0),
            microvia_diameter_mm=cls.get("microvia_diameter", 0.0),
            microvia_drill_mm=cls.get("microvia_drill", 0.0),
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
