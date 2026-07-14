"""PCB layout domain model.

The PCB domain model is the normalized boundary between source parsers,
renderer projection, annotations, and SQL loading.  It models PCB entities
directly: layers, footprints, pads, vias, drills, conductors, artwork,
pours, keepouts, and the physical board profile.
"""

from __future__ import annotations

import math
import weakref
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from phosphor_eda.domain.arc_geometry import arc_bounds

if TYPE_CHECKING:
    from collections.abc import Iterable

    from phosphor_eda.domain.project import Stackup


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
    """A circle or annular ring.

    ``radius`` is the canonical **centerline** radius (matching PcbLine/PcbArc
    stroke semantics): a filled circle is a disc of that radius, and an
    unfilled stroked circle paints an annulus spanning ``radius +/- width/2``.
    Parsers that carry an outer radius must subtract ``width/2`` before
    building a PcbCircle.
    """

    cx: float
    cy: float
    radius: float
    width: float
    fill: bool = False


@dataclass
class PcbPolygon:
    points: list[tuple[float, float]]
    holes: list[list[tuple[float, float]]] = field(default_factory=list)
    width: float = 0.0
    fill: bool = True


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


PcbShape = (
    PcbLine | PcbArc | PcbCircle | PcbPolygon | PcbClosedPath | PcbText | PcbDimension | PcbModel3D
)


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


# Layer-role → artwork-purpose, in priority order (first matching role wins).
# This is the single domain mapping every parser consults to classify an
# authored graphic from the semantic role of the layer it lives on. Formats
# keep their own pre/post handling (KiCad footprint-text kinds and 3D model
# bodies, Allegro's text fallback) and use this table only for the layer-role
# step.
#
# The order reconciles the per-format tables that preceded it. Text roles
# (DESIGNATOR/VALUE) lead — Allegro carries dedicated RefDes/Value layers,
# while KiCad resolves those from text_kind so its layers never hold them.
# MECHANICAL precedes USER as the more specific role (KiCad's order; Allegro
# previously had them reversed). COPPER/KEEPOUT trail the documentation roles
# so a graphic that also carries silkscreen/fab/etc. keeps that more specific
# purpose.
_ARTWORK_PURPOSE_BY_ROLE: tuple[tuple[LayerRole, PcbArtworkPurpose], ...] = (
    (LayerRole.DESIGNATOR, PcbArtworkPurpose.DESIGNATOR),
    (LayerRole.VALUE, PcbArtworkPurpose.VALUE),
    (LayerRole.SILKSCREEN, PcbArtworkPurpose.SILKSCREEN),
    (LayerRole.COURTYARD, PcbArtworkPurpose.COURTYARD),
    (LayerRole.FABRICATION, PcbArtworkPurpose.FABRICATION),
    (LayerRole.ASSEMBLY, PcbArtworkPurpose.ASSEMBLY),
    (LayerRole.SOLDER_MASK, PcbArtworkPurpose.SOLDER_MASK),
    (LayerRole.SOLDER_PASTE, PcbArtworkPurpose.SOLDER_PASTE),
    (LayerRole.DIMENSION, PcbArtworkPurpose.DIMENSION),
    (LayerRole.KEEPOUT, PcbArtworkPurpose.KEEPOUT),
    (LayerRole.COPPER, PcbArtworkPurpose.COPPER),
    (LayerRole.MECHANICAL, PcbArtworkPurpose.MECHANICAL),
    (LayerRole.USER, PcbArtworkPurpose.USER),
    (LayerRole.COMMENT, PcbArtworkPurpose.USER),
)


def artwork_purpose_for_layer(layer: PcbLayer | None) -> PcbArtworkPurpose | None:
    """Classify an authored graphic's purpose from its layer's roles.

    Returns the first purpose whose role the layer carries, in priority order,
    or ``None`` when the layer is absent or carries no mapped role. Callers
    apply their own format-specific fallback for the ``None`` case.
    """
    if layer is None:
        return None
    for role, purpose in _ARTWORK_PURPOSE_BY_ROLE:
        if layer.has_role(role):
            return purpose
    return None


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


class PadStackMode(StrEnum):
    """How a pad/via varies its copper geometry across layers."""

    SIMPLE = "simple"
    TOP_MID_BOTTOM = "top_mid_bottom"
    PER_LAYER = "per_layer"


@dataclass(frozen=True)
class PadStackLayer:
    """One copper geometry entry of a padstack.

    ``layer`` is "" for SIMPLE stacks, a tier name ("top"/"mid"/"bottom")
    for TOP_MID_BOTTOM, or a source copper layer name for PER_LAYER.
    ``corner_radius_ratio`` is the roundrect radius as a fraction of the
    smaller pad dimension (KiCad rratio semantics; Altium percent / 100) —
    stored natively so the source value round-trips exactly.
    """

    layer: str
    shape: str
    size_x: float
    size_y: float
    corner_radius_ratio: float = 0.0
    offset_x: float = 0.0
    offset_y: float = 0.0


@dataclass(frozen=True)
class PadStack:
    """Full per-layer copper definition of a pad or via (decision 23).

    ``layers`` is ordered outer-first: the first entry is the top/outer
    geometry that 2D views and scalar accessors use. ``remove_unused_layers``
    / ``keep_end_layers`` are KiCad copper-pruning flags;
    ``zone_connected_layers`` records layers the source tool marked as
    zone-connected (KiCad ``zone_layer_connections``), which count as used.
    """

    mode: PadStackMode
    layers: tuple[PadStackLayer, ...]
    remove_unused_layers: bool = False
    keep_end_layers: bool = False
    zone_connected_layers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.layers:
            msg = "PadStack requires at least one layer entry"
            raise PcbBuildError(msg)

    @property
    def outer(self) -> PadStackLayer:
        """The top/outer geometry entry."""
        return self.layers[0]

    @classmethod
    def simple(
        cls,
        shape: str,
        size_x: float,
        size_y: float,
        corner_radius_ratio: float = 0.0,
        offset_x: float = 0.0,
        offset_y: float = 0.0,
    ) -> PadStack:
        """Wrap a single uniform geometry as a SIMPLE stack."""
        return cls(
            mode=PadStackMode.SIMPLE,
            layers=(
                PadStackLayer(
                    layer="",
                    shape=shape,
                    size_x=size_x,
                    size_y=size_y,
                    corner_radius_ratio=corner_radius_ratio,
                    offset_x=offset_x,
                    offset_y=offset_y,
                ),
            ),
        )


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
    """A footprint landing/contact on the board.

    Copper geometry lives in ``stack``; the scalar ``width``/``height``/
    ``shape``/``roundrect_rratio`` accessors read the stack's outer layer,
    which is what 2D consumers (renderer, SQL outer-layer columns) use.
    """

    id: str
    number: str
    x: float
    y: float
    stack: PadStack
    pad_type: PcbPadType
    layers: tuple[PcbLayer, ...]
    net: PcbNet | None = None
    footprint: PcbFootprint | None = None
    drill: PcbDrill | None = None
    rotation: float = 0.0
    pin_function: str = ""
    pin_type: str = ""
    mask_aperture: PcbMaskAperture | None = None
    custom_shapes: tuple[PcbLine | PcbArc | PcbCircle | PcbPolygon, ...] = ()
    metadata: PcbObjectMetadata = field(default_factory=PcbObjectMetadata)

    def __post_init__(self) -> None:
        self.layers = tuple(self.layers)
        self.custom_shapes = tuple(self.custom_shapes)
        if self.drill is not None:
            self.drill.owner = self

    @property
    def width(self) -> float:
        return self.stack.outer.size_x

    @property
    def height(self) -> float:
        return self.stack.outer.size_y

    @property
    def shape(self) -> str:
        return self.stack.outer.shape

    @property
    def roundrect_rratio(self) -> float:
        return self.stack.outer.corner_radius_ratio


@dataclass
class PcbVia:
    """A conductive interlayer connection.

    Copper geometry lives in ``stack``; ``diameter`` reads the stack's
    outer layer.
    """

    id: str
    x: float
    y: float
    stack: PadStack
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

    @property
    def diameter(self) -> float:
        return self.stack.outer.size_x


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
    data: PcbLine | PcbArc | PcbCircle | PcbPolygon | PcbClosedPath
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
    data: PcbLine | PcbArc | PcbCircle | PcbPolygon | PcbClosedPath
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
class Board:
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
    stackup: Stackup | None = None
    source_path: str = ""
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
                _extend_rotated_rect_bounds(
                    xs, ys, pad.x, pad.y, pad.width, pad.height, pad.rotation
                )
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


def _extend_rotated_rect_bounds(
    xs: list[float],
    ys: list[float],
    cx: float,
    cy: float,
    width: float,
    height: float,
    rotation: float,
) -> None:
    """Extend ``xs``/``ys`` with a ``width``x``height`` rect about (cx, cy).

    ``rotation`` follows the pad convention (clockwise degrees in the y-down
    board frame); the four rotated corners bound the painted extent.
    """
    half_w = width / 2.0
    half_h = height / 2.0
    angle = math.radians(-rotation)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    for dx, dy in ((-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)):
        xs.append(cx + dx * cos_a - dy * sin_a)
        ys.append(cy + dx * sin_a + dy * cos_a)


def extend_shape_bounds(xs: list[float], ys: list[float], shape: object) -> None:
    """Extend ``xs``/``ys`` with the painted axis-aligned extents of a PCB shape.

    Stroked lines and arcs paint half their width beyond the centerline, and an
    arc reaches axis extremes beyond its three defining points; both are
    included so a bounding box covers the real painted area.
    """
    if isinstance(shape, PcbLine):
        half = shape.width / 2.0
        xs.extend([shape.start_x - half, shape.start_x + half])
        xs.extend([shape.end_x - half, shape.end_x + half])
        ys.extend([shape.start_y - half, shape.start_y + half])
        ys.extend([shape.end_y - half, shape.end_y + half])
    elif isinstance(shape, PcbArc):
        min_x, min_y, max_x, max_y = arc_bounds(
            shape.start_x, shape.start_y, shape.mid_x, shape.mid_y, shape.end_x, shape.end_y
        )
        half = shape.width / 2.0
        xs.extend([min_x - half, max_x + half])
        ys.extend([min_y - half, max_y + half])
    elif isinstance(shape, PcbCircle):
        # radius is the stroke centerline; an unfilled ring paints out to
        # radius + width/2.
        extent = shape.radius + (0.0 if shape.fill else shape.width / 2.0)
        xs.extend([shape.cx - extent, shape.cx + extent])
        ys.extend([shape.cy - extent, shape.cy + extent])
    elif isinstance(shape, PcbPolygon):
        xs.extend(x for x, _y in shape.points)
        ys.extend(y for _x, y in shape.points)
    elif isinstance(shape, PcbClosedPath):
        for segment in shape.segments:
            xs.extend([segment.start_x, segment.end_x])
            ys.extend([segment.start_y, segment.end_y])
            if segment.kind is PcbPathSegmentKind.ARC:
                xs.append(segment.mid_x)
                ys.append(segment.mid_y)


# Endpoint-match tolerance for "a trace connects here" checks (mm).
_COPPER_TOUCH_TOLERANCE = 1e-3


def copper_layers(item: PcbPad | PcbVia, board: Board) -> list[str]:
    """Copper layer names where *item* actually carries copper.

    The base span follows the item: an SMD pad's own copper layers, a
    through pad across every board copper layer, a via between its start
    and end layers. With ``remove_unused_layers`` set, spanned layers keep
    copper only when the source marked them zone-connected, a same-net
    conductor endpoint lands on the item position on that layer, or they
    are span ends protected by ``keep_end_layers``.
    """
    span = _copper_span(item, board)
    stack = item.stack
    if not stack.remove_unused_layers or len(span) < 2:
        return span

    used = set(stack.zone_connected_layers)
    used.update(_endpoint_connected_layers(item, board, span))
    kept: list[str] = []
    for index, name in enumerate(span):
        is_end = index in (0, len(span) - 1)
        if name in used or (is_end and stack.keep_end_layers):
            kept.append(name)
    return kept


def _copper_span(item: PcbPad | PcbVia, board: Board) -> list[str]:
    board_copper = [layer.name for layer in board.layers_by_role(LayerRole.COPPER)]
    if isinstance(item, PcbPad):
        if item.pad_type is PcbPadType.THROUGH_HOLE:
            return board_copper
        return [layer.name for layer in item.layers if layer.has_role(LayerRole.COPPER)]

    item_copper = [layer.name for layer in item.layers if layer.has_role(LayerRole.COPPER)]
    if not item_copper:
        return []
    try:
        start = board_copper.index(item_copper[0])
        end = board_copper.index(item_copper[-1])
    except ValueError:
        return item_copper
    if start > end:
        start, end = end, start
    return board_copper[start : end + 1]


def _endpoint_connected_layers(item: PcbPad | PcbVia, board: Board, span: list[str]) -> set[str]:
    if item.net is None:
        return set()
    span_names = set(span)
    touched: set[str] = set()
    for conductor in board.conductors:
        if conductor.net is not item.net or conductor.layer.name not in span_names:
            continue
        if conductor.layer.name in touched:
            continue
        if _endpoint_touches(conductor.data, item.x, item.y):
            touched.add(conductor.layer.name)
    return touched


def _endpoint_touches(
    shape: PcbLine | PcbArc | PcbCircle | PcbPolygon | PcbClosedPath, x: float, y: float
) -> bool:
    if isinstance(shape, PcbLine | PcbArc):
        return (
            abs(shape.start_x - x) <= _COPPER_TOUCH_TOLERANCE
            and abs(shape.start_y - y) <= _COPPER_TOUCH_TOLERANCE
        ) or (
            abs(shape.end_x - x) <= _COPPER_TOUCH_TOLERANCE
            and abs(shape.end_y - y) <= _COPPER_TOUCH_TOLERANCE
        )
    return False
