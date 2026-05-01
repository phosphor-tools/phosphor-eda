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

# Match (rule "name" or (rule name
_RULE_START_RE = re.compile(r'^\(rule\s+"?([^")\s]+)"?\s*$')
# Match (layer inner|outer)
_LAYER_RE = re.compile(r"^\s*\(layer\s+(\w+)\)")
# Match (condition "...")
_CONDITION_RE = re.compile(r'^\s*\(condition\s+"(.+)"\)')
# Match (constraint type (opt|min|max Xmm))
_CONSTRAINT_RE = re.compile(r"^\s*\(constraint\s+(\w+)\s+\((opt|min|max)\s+([\d.]+)mm\)\)")


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
            for kind, qualifier, value in constraints:
                rule = DesignRule(
                    name=current_name,
                    kind=kind,
                    layer_scope=current_layer,
                    scope1=current_condition,
                )
                if qualifier == "opt":
                    rule.preferred_value_mm = value
                elif qualifier == "min":
                    rule.min_value_mm = value
                elif qualifier == "max":
                    rule.max_value_mm = value
                rules.append(rule)
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
            current_name = rule_match.group(1)
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

        # Constraint (skip commented ones)
        constraint_match = _CONSTRAINT_RE.match(stripped)
        if constraint_match:
            kind = constraint_match.group(1)
            qualifier = constraint_match.group(2)
            value = float(constraint_match.group(3))
            constraints.append((kind, qualifier, value))
            continue

    # Flush last rule
    _flush()

    return rules
