"""Unified project domain model.

A Project ties together all data extracted from an EDA project: schematic,
boards, net classes, design rules, diff pairs, and library references.
This is the top-level container for the SQL query layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phosphor_eda.domain.pcb import Board
    from phosphor_eda.domain.schematic import Schematic


@dataclass
class ProjectMetadata:
    """Project-level metadata extracted from source files."""

    name: str = ""
    revision: str = ""
    author: str = ""
    date: str = ""
    organization: str = ""
    format: str = ""  # "kicad", "altium", "eagle"
    format_version: str = ""
    source_paths: list[str] = field(default_factory=list)


@dataclass
class StackupLayer:
    """A single physical layer in the PCB stackup."""

    name: str
    layer_type: str  # "copper", "prepreg", "core", "solder_mask", "silk", "surface_finish"
    thickness_mm: float = 0.0
    material: str = ""
    epsilon_r: float = 0.0
    loss_tangent: float = 0.0
    copper_weight_oz: float = 0.0
    side: str = ""  # "front", "back", or "" for inner
    copper_orientation: str = ""  # "normal" or "reversed" (foil roughness side)


@dataclass
class Stackup:
    """PCB stackup — physical layer construction from top to bottom."""

    layers: list[StackupLayer] = field(default_factory=list)
    total_thickness_mm: float = 0.0
    copper_finish: str = ""


@dataclass
class NetClass:
    """A net class grouping nets with shared electrical constraints."""

    name: str
    kind: int = 0  # 0=net, 1=component, etc. (Altium kinds preserved)
    clearance_mm: float = 0.0
    trace_width_mm: float = 0.0
    via_diameter_mm: float = 0.0
    via_drill_mm: float = 0.0
    diff_pair_width_mm: float = 0.0
    diff_pair_gap_mm: float = 0.0
    microvia_diameter_mm: float = 0.0
    microvia_drill_mm: float = 0.0
    members: list[str] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)


@dataclass
class DesignRule:
    """A custom design rule (DRC constraint)."""

    name: str
    kind: str = ""  # "clearance", "track_width", "diff_pair_gap", etc.
    enabled: bool = True
    priority: int = 0
    scope1: str = ""  # condition/scope expression (opaque string)
    scope2: str = ""
    layer_scope: str = ""  # "inner", "outer", or "" for all
    min_value_mm: float | None = None
    max_value_mm: float | None = None
    preferred_value_mm: float | None = None
    properties: dict[str, str] = field(default_factory=dict)


@dataclass
class DiffPair:
    """A differential pair definition."""

    name: str
    positive_net: str
    negative_net: str


@dataclass
class Project:
    """A complete EDA project — the unified top-level container."""

    name: str
    metadata: ProjectMetadata = field(default_factory=ProjectMetadata)
    schematic: Schematic | None = None
    boards: list[Board] = field(default_factory=list)
    net_classes: list[NetClass] = field(default_factory=list)
    design_rules: list[DesignRule] = field(default_factory=list)
    diff_pairs: list[DiffPair] = field(default_factory=list)

    @property
    def board(self) -> Board | None:
        """The primary board — first in ``boards``, or None when layout-less."""
        return self.boards[0] if self.boards else None
