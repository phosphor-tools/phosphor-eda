"""Unified project domain model.

A Project ties together all data extracted from an EDA project: schematic,
boards, net classes, design rules, diff pairs, and library references.
This is the top-level container for the SQL query layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phosphor_eda.domain.pcb import Board
    from phosphor_eda.domain.schematic import Schematic
    from phosphor_eda.domain.variants import Variant


@dataclass
class ProjectMetadata:
    """Project-level metadata extracted from source files."""

    name: str = ""
    revision: str = ""
    author: str = ""
    date: str = ""
    organization: str = ""
    format: str = ""  # "kicad", "altium", "orcad", "eagle"
    format_version: str = ""
    source_paths: list[str] = field(default_factory=list)


class DocumentKind(StrEnum):
    SCHEMATIC = "schematic"
    PCB = "pcb"
    LIBRARY = "library"
    BOM = "bom"
    OUTPUT_JOB = "output_job"
    DRAWING = "drawing"
    SIMULATION = "simulation"
    REPORT = "report"
    OTHER = "other"


@dataclass
class ProjectDocument:
    """One source document or deliverable listed by the project manifest."""

    path: str
    kind: DocumentKind
    native_kind: str
    description: str = ""
    unique_id: str = ""
    order: int = 0
    exists: bool = False
    parsed: bool = False
    metadata: dict[str, str] = field(default_factory=dict)


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
    properties: dict[str, str] = field(default_factory=dict)


@dataclass
class Project:
    """A complete EDA project — the unified top-level container."""

    name: str
    metadata: ProjectMetadata = field(default_factory=ProjectMetadata)
    parameters: dict[str, str] = field(default_factory=dict)
    documents: list[ProjectDocument] = field(default_factory=list)
    schematic: Schematic | None = None
    boards: list[Board] = field(default_factory=list)
    net_classes: list[NetClass] = field(default_factory=list)
    design_rules: list[DesignRule] = field(default_factory=list)
    diff_pairs: list[DiffPair] = field(default_factory=list)
    variants: list[Variant] = field(default_factory=list)
    selected_variant_name: str = ""

    @property
    def board(self) -> Board | None:
        """The primary board — first in ``boards``, or None when layout-less."""
        return self.boards[0] if self.boards else None

    @property
    def active_variant(self) -> Variant | None:
        """The selected project variant, or None when the base design is active."""
        if not self.selected_variant_name:
            return None
        for variant in self.variants:
            if variant.name == self.selected_variant_name:
                return variant
        return None
