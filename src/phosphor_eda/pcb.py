"""PCB layout domain model.

Dataclasses representing a parsed PCB board — footprints, pads, traces,
vias, and board outline.  Coordinates are in millimetres (absolute board
space, Y increases downward).

Each layer carries its native name (e.g. ``"F.Cu"`` for KiCad,
``"Top Layer"`` for Altium) plus a :class:`LayerFunction` label and a
*side* string (``"front"`` / ``"back"`` / ``""`` for inner or N/A).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class LayerFunction(Enum):
    """Semantic purpose of a PCB layer."""

    COPPER = "copper"
    SILKSCREEN = "silkscreen"
    SOLDER_MASK = "solder_mask"
    SOLDER_PASTE = "solder_paste"
    FAB = "fab"
    COURTYARD = "courtyard"
    EDGE = "edge"
    MECHANICAL = "mechanical"
    OTHER = "other"


@dataclass
class PcbLayer:
    """A layer definition with function metadata.

    ``name`` is the native layer name from the source format (e.g.
    ``"F.Cu"`` for KiCad, ``"Top Layer"`` for Altium).
    """

    name: str
    function: LayerFunction
    side: str = ""  # "front", "back", or "" for inner/both/N/A
    number: int | None = None


@dataclass
class PcbLine:
    """A line segment (silkscreen, courtyard, or board outline)."""

    start_x: float
    start_y: float
    end_x: float
    end_y: float
    layer: str
    width: float
    footprint_ref: str = ""


@dataclass
class PcbCircle:
    """A circle (component body outlines, etc.)."""

    cx: float
    cy: float
    radius: float
    layer: str
    width: float
    fill: bool = False
    footprint_ref: str = ""


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
    footprint_ref: str = ""


@dataclass
class PcbText:
    """A text label (reference designator, value, etc.) in absolute coords."""

    text: str
    x: float
    y: float
    rotation: float  # degrees
    layer: str
    font_size: float
    kind: str = ""  # "reference", "value", "user"
    hidden: bool = False
    footprint_ref: str = ""


@dataclass
class PcbPolygon:
    """A closed polygon — zone fill, graphic polygon, or footprint polygon."""

    points: list[tuple[float, float]]
    layer: str
    net_number: int = 0
    net_name: str = ""
    footprint_ref: str = ""
    holes: list[list[tuple[float, float]]] = field(default_factory=list)


@dataclass
class PcbTraceArc:
    """A curved copper trace arc (arc segment with net)."""

    start_x: float
    start_y: float
    mid_x: float
    mid_y: float
    end_x: float
    end_y: float
    width: float
    layer: str
    net_number: int


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
    rotation: float = 0.0  # total rotation in board space (degrees)
    drill: float = 0.0  # drill diameter (mm), 0 for SMD pads
    drill_shape: str = "circle"  # "circle" or "oval"
    drill_width: float = 0.0  # slot width or circular drill diameter (mm)
    drill_height: float = 0.0  # slot height or circular drill diameter (mm)
    roundrect_rratio: float = 0.0  # corner ratio for roundrect pads
    pin_function: str = ""  # schematic pin name ("K", "A", "VCC")
    pin_type: str = ""  # electrical type ("passive", "input", "power")
    # Altium multi-layer pad sizes
    mid_width: float | None = None
    mid_height: float | None = None
    bot_width: float | None = None
    bot_height: float | None = None
    mid_shape: str = ""
    bot_shape: str = ""
    # Mask overrides
    mask_expansion: float | None = None  # solder mask expansion override (mm)
    paste_expansion: float | None = None  # paste mask expansion override (mm)
    mask_aperture_width: float | None = None  # explicit/derived solder mask opening width (mm)
    mask_aperture_height: float | None = None  # explicit/derived solder mask opening height (mm)
    mask_aperture_source: str = ""  # provenance for explicit/derived mask aperture data


@dataclass
class PcbModel3D:
    """A 3D model reference attached to a footprint."""

    source: str  # raw model path (KiCad) or OLE model ID (Altium)
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    cache_key: str = ""  # sha256 of model content, set by cache functions


@dataclass
class PcbFootprint:
    """A placed footprint (component) on the board."""

    reference: str
    footprint_lib: str
    x: float
    y: float
    rotation: float
    layer: str  # "F.Cu" or "B.Cu"
    value: str = ""
    pads: list[PcbPad] = field(default_factory=list)
    silkscreen_lines: list[PcbLine] = field(default_factory=list)
    silkscreen_polygons: list[PcbPolygon] = field(default_factory=list)
    courtyard_lines: list[PcbLine] = field(default_factory=list)
    fab_lines: list[PcbLine] = field(default_factory=list)
    fab_circles: list[PcbCircle] = field(default_factory=list)
    fab_arcs: list[PcbArc] = field(default_factory=list)
    fab_polygons: list[PcbPolygon] = field(default_factory=list)
    texts: list[PcbText] = field(default_factory=list)
    models_3d: list[PcbModel3D] = field(default_factory=list)
    bbox: tuple[float, float, float, float] | None = None  # min_x, min_y, max_x, max_y
    properties: dict[str, str] = field(default_factory=dict)  # custom properties (MPN, DKPN, etc.)


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
    via_mode: str = ""  # "simple", "full_stack", "microvia"


@dataclass
class PcbZone:
    """A copper zone (fill area) with properties."""

    net_number: int
    net_name: str
    layer: str
    boundary: list[tuple[float, float]]
    priority: int = 0
    min_thickness_mm: float = 0.0
    thermal_gap_mm: float = 0.0
    thermal_bridge_width_mm: float = 0.0
    connect_pads_clearance_mm: float = 0.0
    fill_type: str = ""  # "solid", "hatch"


@dataclass
class PcbKeepoutRules:
    """Object classes constrained by a keepout/rule area."""

    tracks: str = ""
    vias: str = ""
    pads: str = ""
    copperpour: str = ""
    footprints: str = ""


@dataclass
class PcbKeepout:
    """A non-copper source keepout/rule area with source restrictions."""

    layers: list[str]
    boundary: list[tuple[float, float]]
    rules: PcbKeepoutRules = field(default_factory=PcbKeepoutRules)
    holes: list[list[tuple[float, float]]] = field(default_factory=list)
    source: str = ""
    footprint_ref: str = ""

    @property
    def layer(self) -> str:
        """Representative layer for APIs that expect a single layer."""
        return self.layers[0] if self.layers else ""


@dataclass
class PcbGraphicText:
    """A board-level graphic text (not inside a footprint)."""

    text: str
    x: float
    y: float
    rotation: float
    layer: str
    font_size: float
    justify: str = ""  # "left", "center", "right"


@dataclass
class PcbDimension:
    """A measurement dimension annotation on the board."""

    kind: str  # "aligned", "orthogonal", "leader", "center"
    value_mm: float
    layer: str
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    text: str = ""


@dataclass
class PcbNet:
    """A named electrical net."""

    number: int
    name: str


@dataclass
class Pcb:
    """Complete parsed PCB board."""

    name: str
    nets: dict[int, PcbNet]
    footprints: list[PcbFootprint]
    segments: list[PcbSegment]
    vias: list[PcbVia]
    outline_lines: list[PcbLine]
    outline_arcs: list[PcbArc]
    polygons: list[PcbPolygon] = field(default_factory=list)
    trace_arcs: list[PcbTraceArc] = field(default_factory=list)
    layers: list[PcbLayer] = field(default_factory=list)
    zones: list[PcbZone] = field(default_factory=list)
    keepouts: list[PcbKeepout] = field(default_factory=list)
    graphic_lines: list[PcbLine] = field(default_factory=list)
    graphic_arcs: list[PcbArc] = field(default_factory=list)
    graphic_texts: list[PcbGraphicText] = field(default_factory=list)
    dimensions: list[PcbDimension] = field(default_factory=list)

    # -- Layer helpers --------------------------------------------------------

    def layers_by_function(self, fn: LayerFunction) -> list[PcbLayer]:
        """Return all layers with a given function."""
        return [lyr for lyr in self.layers if lyr.function == fn]

    def layer_for(self, name: str) -> PcbLayer | None:
        """Look up a layer definition by native name."""
        for lyr in self.layers:
            if lyr.name == name:
                return lyr
        return None

    # -- Component helpers ----------------------------------------------------

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
        """Return net numbers matching *name* (case-insensitive exact match)."""
        needle = name.upper()
        return {n.number for n in self.nets.values() if n.name and n.name.upper() == needle}

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
