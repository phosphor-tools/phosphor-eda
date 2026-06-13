"""Parse a KiCad .kicad_pro project file for net classes and text variables.

The .kicad_pro file is JSON with a `net_settings` object containing:
- `classes`: array of net class definitions
- `netclass_assignments`: dict mapping net names to net class names
  (KiCad 9+ writes a list of class names per net; older versions a string)
- `netclass_patterns`: array of {netclass, pattern} for wildcard matching

KiCad writes JSON null (not an absent key) for empty collections, so every
collection access validates the runtime type instead of assuming it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from phosphor_eda.domain.project import NetClass

if TYPE_CHECKING:
    from pathlib import Path


def parse_kicad_pro(path: Path) -> list[NetClass]:
    """Parse net classes from a .kicad_pro file.

    Returns a list of NetClass objects with members populated from both
    explicit assignments and pattern-based assignments.
    """
    text = path.read_text(encoding="utf-8")
    data: object = json.loads(text)

    net_settings = _json_dict(_json_dict(data).get("net_settings"))
    classes_raw = _json_list(net_settings.get("classes"))
    assignments = _json_dict(net_settings.get("netclass_assignments"))
    patterns = _json_list(net_settings.get("netclass_patterns"))

    # Build net classes from definitions
    net_classes: dict[str, NetClass] = {}
    for cls_raw in classes_raw:
        cls = _json_dict(cls_raw)
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

    # Populate members from explicit net-to-class assignments. Values are a
    # single class name up to KiCad 8; KiCad 9+ writes a list of class names.
    for net_name, assigned in assignments.items():
        if isinstance(assigned, str):
            class_names = [assigned]
        else:
            class_names = [name for name in _json_list(assigned) if isinstance(name, str)]
        for class_name in class_names:
            if class_name in net_classes:
                net_classes[class_name].members.append(net_name)

    # Populate members from pattern-based assignments
    for entry_raw in patterns:
        entry = _json_dict(entry_raw)
        class_name = _as_str(entry.get("netclass"))
        pattern = _as_str(entry.get("pattern"))
        if class_name in net_classes and pattern:
            net_classes[class_name].members.append(pattern)

    return list(net_classes.values())


def parse_kicad_text_variables(path: Path) -> dict[str, str]:
    """Parse KiCad project text variables from a .kicad_pro file."""
    text = path.read_text(encoding="utf-8")
    data: object = json.loads(text)
    variables = _json_dict(_json_dict(data).get("text_variables"))
    return {name: value for name, value in variables.items() if isinstance(value, str)}


# json.loads output is inherently untyped; isinstance() narrows `object` only
# to dict[Unknown, Unknown] / list[Unknown], so the casts restate what the
# runtime check guarantees for JSON data (keys are strings).


def _json_dict(value: object) -> dict[str, object]:
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _json_list(value: object) -> list[object]:
    return cast("list[object]", value) if isinstance(value, list) else []


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)
