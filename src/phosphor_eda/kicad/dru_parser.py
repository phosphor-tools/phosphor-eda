"""Parse a KiCad .kicad_dru custom design rules file.

The .kicad_dru format uses S-expression-like syntax:
  (rule "name"
    (layer inner|outer)
    (condition "expression")
    (constraint type (opt|min|max Xmm))
  )

Commented-out lines (starting with #) and commented-out constraints
within rules are excluded from the output.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from phosphor_eda.project import DesignRule

if TYPE_CHECKING:
    from pathlib import Path

# Match (rule "name with spaces" or (rule bare_name
_RULE_START_RE = re.compile(r'^\(rule\s+(?:"([^"]+)"|([^)\s]+))\s*$')
# Match (layer inner|outer)
_LAYER_RE = re.compile(r"^\s*\(layer\s+(\w+)\)")
# Match (condition "...")
_CONDITION_RE = re.compile(r'^\s*\(condition\s+"(.+)"\)')
# Match the constraint kind, then collect all qualifier/value pairs
_CONSTRAINT_KIND_RE = re.compile(r"^\s*\(constraint\s+(\w+)\b")
_CONSTRAINT_VALUE_RE = re.compile(r"\((opt|min|max)\s+([\d.]+)mm\)")


def parse_kicad_dru(path: Path) -> list[DesignRule]:
    """Parse custom design rules from a .kicad_dru file.

    Returns a list of DesignRule objects. Commented-out constraints
    are excluded. Each (constraint ...) within a rule becomes a separate
    DesignRule (since a single rule block can define multiple constraint types).
    """
    text = path.read_text(encoding="utf-8")
    rules: list[DesignRule] = []

    # Parse by tracking rule blocks
    current_name = ""
    current_layer = ""
    current_condition = ""
    constraints: list[tuple[str, str, float]] = []  # (kind, qualifier, value)

    def _flush() -> None:
        nonlocal current_name, current_layer, current_condition, constraints
        if current_name and constraints:
            # Group qualifiers by constraint kind so min/opt/max from one
            # constraint line produce a single rule with all values set.
            by_kind: dict[str, DesignRule] = {}
            for kind, qualifier, value in constraints:
                if kind not in by_kind:
                    by_kind[kind] = DesignRule(
                        name=current_name,
                        kind=kind,
                        layer_scope=current_layer,
                        scope1=current_condition,
                    )
                rule = by_kind[kind]
                if qualifier == "opt":
                    rule.preferred_value_mm = value
                elif qualifier == "min":
                    rule.min_value_mm = value
                elif qualifier == "max":
                    rule.max_value_mm = value
            rules.extend(by_kind.values())
        current_name = ""
        current_layer = ""
        current_condition = ""
        constraints = []

    for line in text.splitlines():
        stripped = line.strip()

        # Skip comments
        if stripped.startswith("#"):
            continue

        # Check for rule start
        rule_match = _RULE_START_RE.match(stripped)
        if rule_match:
            _flush()
            current_name = rule_match.group(1) or rule_match.group(2)
            continue

        # Skip if not inside a rule
        if not current_name:
            continue

        # Layer
        layer_match = _LAYER_RE.match(stripped)
        if layer_match:
            current_layer = layer_match.group(1)
            continue

        # Condition
        cond_match = _CONDITION_RE.match(stripped)
        if cond_match:
            current_condition = cond_match.group(1)
            continue

        # Constraint (skip commented ones) — collect all qualifiers on a line
        constraint_match = _CONSTRAINT_KIND_RE.match(stripped)
        if constraint_match:
            kind = constraint_match.group(1)
            for qualifier, value_str in _CONSTRAINT_VALUE_RE.findall(stripped):
                constraints.append((kind, qualifier, float(value_str)))
            continue

    # Flush last rule
    _flush()

    return rules
