"""PCB layout domain model.

Dataclasses representing a parsed PCB board — footprints, pads, traces,
vias, and board outline.  Coordinates are in millimetres (absolute board
space, Y increases downward).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PcbLine:
    """A line segment (silkscreen, courtyard, or board outline)."""

    start_x: float
    start_y: float
    end_x: float
    end_y: float
    layer: str
    width: float


@dataclass
class PcbArc:
    """An arc defined by start, midpoint, and end (board outline, etc.)."""

    start_x: float
    start_y: float
    mid_x: float
    mid_y: float
    end_x: float
    end_y: float
    layer: str
    width: float


@dataclass
class PcbPad:
    """A pad within a footprint (absolute board coordinates)."""

    number: str
    x: float
    y: float
    width: float
    height: float
    shape: str  # "circle", "rect", "roundrect", "oval", "custom"
    layers: list[str]
    net_number: int
    net_name: str
    footprint_ref: str


@dataclass
class PcbFootprint:
    """A placed footprint (component) on the board."""

    reference: str
    footprint_lib: str
    x: float
    y: float
    rotation: float
    layer: str  # "F.Cu" or "B.Cu"
    pads: list[PcbPad] = field(default_factory=list)
    silkscreen_lines: list[PcbLine] = field(default_factory=list)
    courtyard_lines: list[PcbLine] = field(default_factory=list)
    bbox: tuple[float, float, float, float] | None = None  # min_x, min_y, max_x, max_y


@dataclass
class PcbSegment:
    """A copper trace segment."""

    start_x: float
    start_y: float
    end_x: float
    end_y: float
    width: float
    layer: str
    net_number: int


@dataclass
class PcbVia:
    """A via connecting copper layers."""

    x: float
    y: float
    size: float
    drill: float
    layers: list[str]
    net_number: int


@dataclass
class PcbNet:
    """A named electrical net."""

    number: int
    name: str


@dataclass
class PcbBoard:
    """Complete parsed PCB board."""

    name: str
    nets: dict[int, PcbNet]
    footprints: list[PcbFootprint]
    segments: list[PcbSegment]
    vias: list[PcbVia]
    outline_lines: list[PcbLine]
    outline_arcs: list[PcbArc]

    def footprint_by_ref(self, ref: str) -> PcbFootprint | None:
        """Look up a footprint by reference designator (case-insensitive)."""
        ref_upper = ref.upper()
        for fp in self.footprints:
            if fp.reference.upper() == ref_upper:
                return fp
        return None

    def nets_for_component(self, ref: str) -> set[int]:
        """Return all net numbers connected to a component's pads."""
        fp = self.footprint_by_ref(ref)
        if fp is None:
            return set()
        return {p.net_number for p in fp.pads if p.net_number != 0}

    def net_numbers_by_name(self, name: str) -> set[int]:
        """Return net numbers matching *name* (case-insensitive substring)."""
        needle = name.upper()
        return {
            n.number
            for n in self.nets.values()
            if n.name and needle in n.name.upper()
        }

    def bbox(self) -> tuple[float, float, float, float]:
        """Board bounding box from outline geometry."""
        xs: list[float] = []
        ys: list[float] = []
        for ln in self.outline_lines:
            xs.extend([ln.start_x, ln.end_x])
            ys.extend([ln.start_y, ln.end_y])
        for arc in self.outline_arcs:
            xs.extend([arc.start_x, arc.mid_x, arc.end_x])
            ys.extend([arc.start_y, arc.mid_y, arc.end_y])
        if not xs:
            # Fallback: use pad extents
            for fp in self.footprints:
                for p in fp.pads:
                    xs.extend([p.x - p.width / 2, p.x + p.width / 2])
                    ys.extend([p.y - p.height / 2, p.y + p.height / 2])
        if not xs:
            return (0.0, 0.0, 100.0, 100.0)
        return (min(xs), min(ys), max(xs), max(ys))
