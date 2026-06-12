"""PCB layout domain model.

The PCB domain model is the normalized boundary between source parsers,
renderer projection, annotations, and SQL loading.  It models PCB entities
directly: layers, footprints, pads, vias, drills, conductors, artwork,
pours, keepouts, and the physical board profile.
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


class PcbBuildError(ValueError):
    """Raised when parsed source data cannot form a strict PCB domain model."""


class LayerRole(StrEnum):
    """Normalized semantic role for a PCB source layer.

    Members are declared in canonical order — :func:`normalize_roles` returns
    roles in declaration order, so keep this list ordered intentionally.
    """

    COPPER = "copper"
    DIELECTRIC = "dielectric"
    SOLDER_MASK = "solder_mask"
    SOLDER_PASTE = "solder_paste"
    SILKSCREEN = "silkscreen"
    ADHESIVE = "adhesive"
    EDGE = "edge"
    MARGIN = "margin"
    MECHANICAL = "mechanical"
    KEEPOUT = "keepout"
    DRILL = "drill"
    DRILL_GUIDE = "drill_guide"
    DRILL_DRAWING = "drill_drawing"
    MULTI_LAYER = "multi_layer"

    FABRICATION = "fabrication"
    ASSEMBLY = "assembly"
    COURTYARD = "courtyard"
    DESIGNATOR = "designator"
    VALUE = "value"
    COMPONENT_OUTLINE = "component_outline"
    COMPONENT_CENTER = "component_center"
    DIMENSION = "dimension"
    BOARD = "board"
    BOARD_SHAPE = "board_shape"
    V_CUT = "v_cut"
    ROUTE_TOOL_PATH = "route_tool_path"
    SHEET = "sheet"
    DRAWING = "drawing"
    COMMENT = "comment"
    ASSEMBLY_NOTES = "assembly_notes"
    FAB_NOTES = "fab_notes"

    COATING = "coating"
    GLUE_POINTS = "glue_points"
    GOLD_PLATING = "gold_plating"
    THREE_D_BODY = "three_d_body"

    FRONT = "front"
    BACK = "back"
    INNER = "inner"
    OUTER = "outer"

    SIGNAL = "signal"
    POWER = "power"
    MIXED = "mixed"
    JUMPER = "jumper"
    PLANE = "plane"
    INTERNAL_PLANE = "internal_plane"

    USER = "user"
    UNKNOWN = "unknown"


_ROLE_ORDER: tuple[LayerRole, ...] = tuple(LayerRole)


def _coerce_role(role: LayerRole | str) -> LayerRole:
    if isinstance(role, LayerRole):
        return role
    return LayerRole(role)


def normalize_roles(*roles: LayerRole | str) -> tuple[LayerRole, ...]:
    """Return unique layer roles in canonical order."""
    role_set = {_coerce_role(role) for role in roles}
    if not role_set:
        role_set.add(LayerRole.UNKNOWN)
    return tuple(role for role in _ROLE_ORDER if role in role_set)


@dataclass
class PcbMetadata:
    """Native/source metadata common to PCB domain entities."""

    source_format: str = ""
    native_type: str = ""
    native_kind: str = ""
    native_id: str = ""
    native_index: int | None = None
    properties: dict[str, str] = field(default_factory=dict)


@dataclass
class PcbLayerMetadata(PcbMetadata):
    native_user_name: str = ""


@dataclass
class PcbFootprintMetadata(PcbMetadata):
    source_designator: str = ""
    source_unique_id: str = ""
    source_footprint_library: str = ""
    source_component_library: str = ""
    source_hierarchical_path: str = ""


@dataclass
class PcbObjectMetadata(PcbMetadata):
    source_collection: str = ""
    source_index: int | None = None
    native_layer_id: str = ""
    native_component_index: int | None = None
    native_polygon_index: int | None = None
    native_subpolygon_index: int | None = None
    native_pour_index: int | None = None
    locked: bool = False
    hidden: bool = False


@dataclass
class PcbLayer:
    """A source layer definition with normalized role metadata."""

    name: str
    roles: tuple[LayerRole, ...]
    number: int | None = None
    stack_index: int | None = None
    metadata: PcbLayerMetadata = field(default_factory=PcbLayerMetadata)

    def __post_init__(self) -> None:
        self.roles = normalize_roles(*self.roles)

    def has_role(self, role: LayerRole | str) -> bool:
        """Return whether this layer has a normalized role."""
        return _coerce_role(role) in self.roles

    @property
    def role_values(self) -> tuple[str, ...]:
        """String role values suitable for serialization."""
        return tuple(role.value for role in self.roles)

    @property
    def side(self) -> str:
        """Normalized physical side derived from placement roles."""
        if LayerRole.FRONT in self.roles:
            return "front"
        if LayerRole.BACK in self.roles:
            return "back"
        if LayerRole.INNER in self.roles:
            return "inner"
        return ""


class PcbPathSegmentKind(StrEnum):
    LINE = "line"
    ARC = "arc"


@dataclass(frozen=True)
class PcbPathSegment:
    kind: PcbPathSegmentKind
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    mid_x: float = 0.0
    mid_y: float = 0.0


@dataclass
class PcbClosedPath:
    segments: tuple[PcbPathSegment, ...]
    holes: tuple[PcbClosedPath, ...] = ()

    @classmethod
    def from_points(
        cls,
        points: Iterable[tuple[float, float]],
        *,
        holes: Iterable[PcbClosedPath] = (),
    ) -> PcbClosedPath:
        """Create a closed line-segment path from polygon points."""
        point_tuple = tuple(points)
        if len(point_tuple) < 3:
            msg = f"closed path needs at least 3 points, got {len(point_tuple)}"
            raise PcbBuildError(msg)
        segments: list[PcbPathSegment] = []
        for index, (start_x, start_y) in enumerate(point_tuple):
            end_x, end_y = point_tuple[(index + 1) % len(point_tuple)]
            segments.append(PcbPathSegment(PcbPathSegmentKind.LINE, start_x, start_y, end_x, end_y))
        return cls(segments=tuple(segments), holes=tuple(holes))

    @property
    def points(self) -> tuple[tuple[float, float], ...]:
        """Return segment start points for polygon-style consumers."""
        return tuple((segment.start_x, segment.start_y) for segment in self.segments)


@dataclass
class PcbLine:
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    width: float


@dataclass
class PcbArc:
    start_x: float
    start_y: float
    mid_x: float
    mid_y: float
    end_x: float
    end_y: float
    width: float


@dataclass
class PcbCircle:
    cx: float
    cy: float
    radius: float
    width: float
    fill: bool = False


@dataclass
class PcbPolygon:
    points: list[tuple[float, float]]
    holes: list[list[tuple[float, float]]] = field(default_factory=list)


@dataclass
class PcbText:
    text: str
    x: float
    y: float
    rotation: float
    font_size: float
    justify: str = ""
    # Explicit mirror flag from the source format (Altium Texts6). None means
    # the source has no per-text flag and mirroring is derived from the layer
    # side at render time.
    mirrored: bool | None = None


@dataclass
class PcbDimension:
    kind: str
    value_mm: float
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    text: str = ""


@dataclass
class PcbModel3D:
    source: str
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    cache_key: str = ""


PcbShape = PcbLine | PcbArc | PcbCircle | PcbPolygon | PcbText | PcbDimension | PcbModel3D


class PcbPadType(StrEnum):
    SMD = "smd"
    THROUGH_HOLE = "through_hole"


class PcbDrillShape(StrEnum):
    ROUND = "round"
    SLOT = "slot"


class PcbDrillPlating(StrEnum):
    PLATED = "plated"
    NON_PLATED = "non_plated"
    UNKNOWN = "unknown"


class PcbViaType(StrEnum):
    THROUGH = "through"
    BLIND = "blind"
    BURIED = "buried"
    MICROVIA = "microvia"
    FREE = "free"


class PcbConductorKind(StrEnum):
    TRACE = "trace"
    TRACE_ARC = "trace_arc"
    COPPER_REGION = "copper_region"
    POUR_FILL = "pour_fill"


class PcbArtworkKind(StrEnum):
    LINE = "line"
    ARC = "arc"
    CIRCLE = "circle"
    POLYGON = "polygon"
    TEXT = "text"
    DIMENSION = "dimension"
    IMAGE = "image"
    MODEL_3D = "model_3d"


class PcbArtworkPurpose(StrEnum):
    COPPER = "copper"
    BOARD_PROFILE = "board_profile"
    DRILL = "drill"
    SILKSCREEN = "silkscreen"
    FABRICATION = "fabrication"
    ASSEMBLY = "assembly"
    COURTYARD = "courtyard"
    DESIGNATOR = "designator"
    VALUE = "value"
    USER_TEXT = "user_text"
    SOLDER_MASK = "solder_mask"
    SOLDER_PASTE = "solder_paste"
    MECHANICAL = "mechanical"
    COMPONENT_BODY = "component_body"
    DIMENSION = "dimension"
    USER = "user"
    KEEPOUT = "keepout"
    UNKNOWN = "unknown"


@dataclass
class PcbFootprint:
    """A placed footprint on the board."""

    reference: str
    footprint_lib: str
    x: float
    y: float
    rotation: float
    layer: PcbLayer
    value: str = ""
    bbox: tuple[float, float, float, float] | None = None
    properties: dict[str, str] = field(default_factory=dict)
    metadata: PcbFootprintMetadata = field(default_factory=PcbFootprintMetadata)


@dataclass
class PcbNet:
    """A named electrical net."""

    number: int
    name: str
    metadata: PcbMetadata = field(default_factory=PcbMetadata)


@dataclass
class PcbDrill:
    """A manufactured hole or slot."""

    id: str
    x: float
    y: float
    diameter: float
    shape: PcbDrillShape = PcbDrillShape.ROUND
    plating: PcbDrillPlating = PcbDrillPlating.UNKNOWN
    width: float = 0.0
    height: float = 0.0
    rotation: float = 0.0
    layers: tuple[PcbLayer, ...] = ()
    metadata: PcbObjectMetadata = field(default_factory=PcbObjectMetadata)
    _owner_ref: weakref.ReferenceType[PcbPad | PcbVia] | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        self.layers = tuple(self.layers)
        if self.width <= 0.0:
            self.width = self.diameter
        if self.height <= 0.0:
            self.height = self.diameter

    @property
    def owner(self) -> PcbPad | PcbVia | None:
        """Return the pad or via that owns this drill, if any."""
        if self._owner_ref is None:
            return None
        return self._owner_ref()

    @owner.setter
    def owner(self, value: PcbPad | PcbVia | None) -> None:
        self._owner_ref = None if value is None else weakref.ref(value)


@dataclass(frozen=True)
class PcbPadStack:
    """Altium-only per-layer pad geometry (mid/bottom layers of a stack)."""

    mid_width: float | None = None
    mid_height: float | None = None
    bot_width: float | None = None
    bot_height: float | None = None
    mid_shape: str = ""
    bot_shape: str = ""


@dataclass(frozen=True)
class PcbMaskAperture:
    """Altium-only solder-mask/paste expansion and explicit mask opening.

    ``source`` records where the explicit mask opening came from; the only
    current producer is the Altium drill-manager template parser, which emits
    ``altium:drill-manager-template:<name>`` (the template name is dynamic).
    """

    mask_expansion: float | None = None
    paste_expansion: float | None = None
    aperture_width: float | None = None
    aperture_height: float | None = None
    source: str = ""


@dataclass
class PcbPad:
    """A footprint landing/contact on the board."""

    id: str
    number: str
    x: float
    y: float
    width: float
    height: float
    shape: str
    pad_type: PcbPadType
    layers: tuple[PcbLayer, ...]
    net: PcbNet | None = None
    footprint: PcbFootprint | None = None
    drill: PcbDrill | None = None
    rotation: float = 0.0
    roundrect_rratio: float = 0.0
    pin_function: str = ""
    pin_type: str = ""
    pad_stack: PcbPadStack | None = None
    mask_aperture: PcbMaskAperture | None = None
    custom_shapes: tuple[PcbLine | PcbArc | PcbCircle | PcbPolygon, ...] = ()
    metadata: PcbObjectMetadata = field(default_factory=PcbObjectMetadata)

    def __post_init__(self) -> None:
        self.layers = tuple(self.layers)
        self.custom_shapes = tuple(self.custom_shapes)
        if self.drill is not None:
            self.drill.owner = self


@dataclass
class PcbVia:
    """A conductive interlayer connection."""

    id: str
    x: float
    y: float
    diameter: float
    layers: tuple[PcbLayer, ...]
    drill: PcbDrill
    net: PcbNet | None = None
    via_type: PcbViaType = PcbViaType.THROUGH
    tented_front: bool = False
    tented_back: bool = False
    metadata: PcbObjectMetadata = field(default_factory=PcbObjectMetadata)

    def __post_init__(self) -> None:
        self.layers = tuple(self.layers)
        self.drill.owner = self


class PcbPourFillMode(StrEnum):
    SOLID = "solid"
    HATCH = "hatch"
    NONE = "none"
    UNKNOWN = "unknown"


@dataclass
class PcbPourSettings:
    fill_mode: PcbPourFillMode = PcbPourFillMode.UNKNOWN
    hatch_style: str = ""
    grid_mm: float = 0.0
    track_width_mm: float = 0.0
    min_thickness_mm: float = 0.0
    thermal_gap_mm: float = 0.0
    thermal_bridge_width_mm: float = 0.0
    connect_pads_clearance_mm: float = 0.0


@dataclass
class PcbPour:
    """A copper-pour source definition."""

    id: str
    boundary: PcbClosedPath
    layers: tuple[PcbLayer, ...]
    net: PcbNet | None = None
    name: str = ""
    priority: int = 0
    settings: PcbPourSettings = field(default_factory=PcbPourSettings)
    fills: tuple[PcbConductor, ...] = ()
    footprint: PcbFootprint | None = None
    metadata: PcbObjectMetadata = field(default_factory=PcbObjectMetadata)

    def __post_init__(self) -> None:
        self.layers = tuple(self.layers)
        self.fills = tuple(self.fills)


@dataclass
class PcbConductor:
    """Conductive board geometry, without route topology semantics."""

    id: str
    kind: PcbConductorKind
    layer: PcbLayer
    data: PcbLine | PcbArc | PcbCircle | PcbPolygon
    net: PcbNet | None = None
    footprint: PcbFootprint | None = None
    pour: PcbPour | None = None
    metadata: PcbObjectMetadata = field(default_factory=PcbObjectMetadata)


@dataclass
class PcbArtwork:
    """Authored non-pad, non-via, non-drill board or footprint artwork."""

    id: str
    kind: PcbArtworkKind
    purpose: PcbArtworkPurpose
    layer: PcbLayer | None
    data: PcbShape
    footprint: PcbFootprint | None = None
    metadata: PcbObjectMetadata = field(default_factory=PcbObjectMetadata)


@dataclass
class PcbBoardProfileElement:
    id: str
    kind: PcbArtworkKind
    layer: PcbLayer | None
    data: PcbLine | PcbArc | PcbCircle | PcbPolygon
    is_cutout: bool = False
    metadata: PcbObjectMetadata = field(default_factory=PcbObjectMetadata)


@dataclass
class PcbBoardProfile:
    """Physical board outline and cutouts."""

    elements: tuple[PcbBoardProfileElement, ...] = ()


class PcbKeepoutPermission(StrEnum):
    ALLOWED = "allowed"
    NOT_ALLOWED = "not_allowed"
    UNKNOWN = "unknown"


@dataclass
class PcbKeepoutRules:
    """Object classes constrained by a keepout/rule area."""

    tracks: PcbKeepoutPermission = PcbKeepoutPermission.UNKNOWN
    vias: PcbKeepoutPermission = PcbKeepoutPermission.UNKNOWN
    pads: PcbKeepoutPermission = PcbKeepoutPermission.UNKNOWN
    copper_pours: PcbKeepoutPermission = PcbKeepoutPermission.UNKNOWN
    footprints: PcbKeepoutPermission = PcbKeepoutPermission.UNKNOWN


@dataclass
class PcbKeepout:
    """A non-conductive rule area that constrains PCB object placement."""

    id: str
    boundary: PcbClosedPath
    layers: tuple[PcbLayer, ...]
    rules: PcbKeepoutRules = field(default_factory=PcbKeepoutRules)
    name: str = ""
    footprint: PcbFootprint | None = None
    metadata: PcbObjectMetadata = field(default_factory=PcbObjectMetadata)

    def __post_init__(self) -> None:
        self.layers = tuple(self.layers)


@dataclass
class Pcb:
    """Complete parsed PCB board."""

    name: str
    layers: list[PcbLayer]
    nets: dict[int, PcbNet]
    footprints: list[PcbFootprint]
    pads: list[PcbPad]
    vias: list[PcbVia]
    drills: list[PcbDrill]
    conductors: list[PcbConductor]
    artwork: list[PcbArtwork]
    pours: list[PcbPour]
    keepouts: list[PcbKeepout]
    board_profile: PcbBoardProfile | None = None
    metadata: PcbMetadata = field(default_factory=PcbMetadata)

    def layers_by_role(self, role: LayerRole | str) -> list[PcbLayer]:
        """Return all layers with a given normalized role."""
        return [lyr for lyr in self.layers if lyr.has_role(role)]

    def layers_with_all_roles(self, roles: Iterable[LayerRole | str]) -> list[PcbLayer]:
        """Return layers that contain every requested role."""
        normalized = tuple(_coerce_role(role) for role in roles)
        return [lyr for lyr in self.layers if all(lyr.has_role(role) for role in normalized)]

    def layers_with_any_role(self, roles: Iterable[LayerRole | str]) -> list[PcbLayer]:
        """Return layers that contain at least one requested role."""
        normalized = tuple(_coerce_role(role) for role in roles)
        return [lyr for lyr in self.layers if any(lyr.has_role(role) for role in normalized)]

    def layer_for(self, name: str) -> PcbLayer | None:
        """Look up a source layer by native name."""
        for layer in self.layers:
            if layer.name == name:
                return layer
        return None

    def footprint_by_ref(self, ref: str) -> PcbFootprint | None:
        """Look up a footprint by reference designator."""
        ref_upper = ref.upper()
        for footprint in self.footprints:
            if footprint.reference.upper() == ref_upper:
                return footprint
        return None

    def pads_for_footprint(self, ref: str | PcbFootprint) -> list[PcbPad]:
        """Return pads owned by a footprint."""
        footprint = self.footprint_by_ref(ref) if isinstance(ref, str) else ref
        if footprint is None:
            return []
        return [pad for pad in self.pads if pad.footprint is footprint]

    def pads_for_net(self, net: PcbNet | str | int) -> list[PcbPad]:
        """Return pads connected to a real net."""
        resolved = self._resolve_net_selector(net)
        if resolved is None:
            return []
        return [pad for pad in self.pads if pad.net is resolved]

    def vias_for_net(self, net: PcbNet | str | int) -> list[PcbVia]:
        """Return vias connected to a real net."""
        resolved = self._resolve_net_selector(net)
        if resolved is None:
            return []
        return [via for via in self.vias if via.net is resolved]

    def conductors_for_net(self, net: PcbNet | str | int) -> list[PcbConductor]:
        """Return conductors connected to a real net."""
        resolved = self._resolve_net_selector(net)
        if resolved is None:
            return []
        return [conductor for conductor in self.conductors if conductor.net is resolved]

    def net_numbers_by_name(self, name: str) -> set[int]:
        """Return net numbers matching *name* case-insensitively."""
        needle = name.upper()
        return {net.number for net in self.nets.values() if net.name.upper() == needle}

    def nets_for_component(self, ref: str) -> set[int]:
        """Return net numbers connected to a component's pads."""
        return {pad.net.number for pad in self.pads_for_footprint(ref) if pad.net is not None}

    def pour_for(self, pour_id: str) -> PcbPour | None:
        """Look up a copper pour by id."""
        for pour in self.pours:
            if pour.id == pour_id:
                return pour
        return None

    def pours_on_layer(self, layer: PcbLayer | str) -> list[PcbPour]:
        """Return copper pours that reference a source layer."""
        resolved = self.layer_for(layer) if isinstance(layer, str) else layer
        if resolved is None:
            return []
        return [pour for pour in self.pours if resolved in pour.layers]

    def pours_for_net(self, net: PcbNet | str | int) -> list[PcbPour]:
        """Return copper pours assigned to a real net."""
        resolved = self._resolve_net_selector(net)
        if resolved is None:
            return []
        return [pour for pour in self.pours if pour.net is resolved]

    def conductors_for_pour(self, pour: PcbPour | str) -> list[PcbConductor]:
        """Return concrete conductors generated by a pour."""
        resolved = self.pour_for(pour) if isinstance(pour, str) else pour
        if resolved is None:
            return []
        return [conductor for conductor in self.conductors if conductor.pour is resolved]

    def keepout_for(self, keepout_id: str) -> PcbKeepout | None:
        """Look up a keepout/rule area by id."""
        for keepout in self.keepouts:
            if keepout.id == keepout_id:
                return keepout
        return None

    def keepouts_on_layer(self, layer: PcbLayer | str) -> list[PcbKeepout]:
        """Return keepouts that apply to a source layer."""
        resolved = self.layer_for(layer) if isinstance(layer, str) else layer
        if resolved is None:
            return []
        return [keepout for keepout in self.keepouts if resolved in keepout.layers]

    def keepouts_for_footprint(self, ref: str | PcbFootprint) -> list[PcbKeepout]:
        """Return keepouts owned by a footprint."""
        footprint = self.footprint_by_ref(ref) if isinstance(ref, str) else ref
        if footprint is None:
            return []
        return [keepout for keepout in self.keepouts if keepout.footprint is footprint]

    def bbox(self) -> tuple[float, float, float, float] | None:
        """Board bounding box from profile, falling back to pad extents.

        Returns ``None`` for a board with no profile and no pads — callers
        must handle the empty case rather than rely on a fabricated default.
        """
        xs: list[float] = []
        ys: list[float] = []
        if self.board_profile is not None:
            for element in self.board_profile.elements:
                extend_shape_bounds(xs, ys, element.data)
        if not xs:
            for pad in self.pads:
                xs.extend([pad.x - pad.width / 2, pad.x + pad.width / 2])
                ys.extend([pad.y - pad.height / 2, pad.y + pad.height / 2])
        if not xs:
            return None
        return (min(xs), min(ys), max(xs), max(ys))

    def _resolve_net_selector(self, net: PcbNet | str | int) -> PcbNet | None:
        if isinstance(net, PcbNet):
            return net
        if isinstance(net, int):
            return self.nets.get(net)
        matches = [candidate for candidate in self.nets.values() if candidate.name == net]
        return matches[0] if matches else None


def extend_shape_bounds(xs: list[float], ys: list[float], shape: object) -> None:
    """Extend ``xs``/``ys`` with the axis-aligned extents of a PCB shape payload."""
    if isinstance(shape, PcbLine):
        xs.extend([shape.start_x, shape.end_x])
        ys.extend([shape.start_y, shape.end_y])
    elif isinstance(shape, PcbArc):
        xs.extend([shape.start_x, shape.mid_x, shape.end_x])
        ys.extend([shape.start_y, shape.mid_y, shape.end_y])
    elif isinstance(shape, PcbCircle):
        xs.extend([shape.cx - shape.radius, shape.cx + shape.radius])
        ys.extend([shape.cy - shape.radius, shape.cy + shape.radius])
    elif isinstance(shape, PcbPolygon):
        xs.extend(x for x, _y in shape.points)
        ys.extend(y for _x, y in shape.points)
