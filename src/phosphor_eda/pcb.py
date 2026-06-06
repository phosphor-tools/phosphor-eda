"""PCB layout domain model.

Dataclasses representing a parsed PCB board — footprints, pads, traces,
vias, and board outline.  Coordinates are in millimetres (absolute board
space, Y increases downward).

Each layer carries its native name (e.g. ``"F.Cu"`` for KiCad,
``"Top Layer"`` for Altium) plus normalized semantic roles.  Roles are
multi-valued so a layer can be both ``copper`` and ``inner``, or both
``fabrication`` and ``courtyard``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


class LayerRole(StrEnum):
    """Normalized semantic role for a PCB layer."""

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

    USER = "user"
    UNKNOWN = "unknown"


_ROLE_ORDER: tuple[LayerRole, ...] = (
    LayerRole.COPPER,
    LayerRole.DIELECTRIC,
    LayerRole.SOLDER_MASK,
    LayerRole.SOLDER_PASTE,
    LayerRole.SILKSCREEN,
    LayerRole.ADHESIVE,
    LayerRole.EDGE,
    LayerRole.MARGIN,
    LayerRole.MECHANICAL,
    LayerRole.KEEPOUT,
    LayerRole.DRILL,
    LayerRole.DRILL_GUIDE,
    LayerRole.DRILL_DRAWING,
    LayerRole.MULTI_LAYER,
    LayerRole.FABRICATION,
    LayerRole.ASSEMBLY,
    LayerRole.COURTYARD,
    LayerRole.DESIGNATOR,
    LayerRole.VALUE,
    LayerRole.COMPONENT_OUTLINE,
    LayerRole.COMPONENT_CENTER,
    LayerRole.DIMENSION,
    LayerRole.BOARD,
    LayerRole.BOARD_SHAPE,
    LayerRole.V_CUT,
    LayerRole.ROUTE_TOOL_PATH,
    LayerRole.SHEET,
    LayerRole.DRAWING,
    LayerRole.COMMENT,
    LayerRole.ASSEMBLY_NOTES,
    LayerRole.FAB_NOTES,
    LayerRole.COATING,
    LayerRole.GLUE_POINTS,
    LayerRole.GOLD_PLATING,
    LayerRole.THREE_D_BODY,
    LayerRole.FRONT,
    LayerRole.BACK,
    LayerRole.INNER,
    LayerRole.OUTER,
    LayerRole.SIGNAL,
    LayerRole.POWER,
    LayerRole.MIXED,
    LayerRole.JUMPER,
    LayerRole.PLANE,
    LayerRole.INTERNAL_PLANE,
    LayerRole.USER,
    LayerRole.UNKNOWN,
)

_PRIMARY_ROLE_ORDER: tuple[LayerRole, ...] = (
    LayerRole.EDGE,
    LayerRole.DRILL,
    LayerRole.DRILL_GUIDE,
    LayerRole.DRILL_DRAWING,
    LayerRole.KEEPOUT,
    LayerRole.COPPER,
    LayerRole.SOLDER_MASK,
    LayerRole.SOLDER_PASTE,
    LayerRole.SILKSCREEN,
    LayerRole.COURTYARD,
    LayerRole.DESIGNATOR,
    LayerRole.VALUE,
    LayerRole.ASSEMBLY,
    LayerRole.COMPONENT_OUTLINE,
    LayerRole.COMPONENT_CENTER,
    LayerRole.DIMENSION,
    LayerRole.BOARD_SHAPE,
    LayerRole.V_CUT,
    LayerRole.ROUTE_TOOL_PATH,
    LayerRole.SHEET,
    LayerRole.COATING,
    LayerRole.GLUE_POINTS,
    LayerRole.GOLD_PLATING,
    LayerRole.THREE_D_BODY,
    LayerRole.FABRICATION,
    LayerRole.MECHANICAL,
    LayerRole.USER,
    LayerRole.DIELECTRIC,
    LayerRole.UNKNOWN,
)


def _coerce_role(role: LayerRole | str) -> LayerRole:
    if isinstance(role, LayerRole):
        return role
    return LayerRole(role)


def normalize_roles(*roles: LayerRole | str) -> tuple[LayerRole, ...]:
    """Return unique roles in canonical order."""
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
class PcbNetMetadata(PcbMetadata):
    pass


@dataclass
class PcbFootprintMetadata(PcbMetadata):
    source_designator: str = ""
    source_unique_id: str = ""
    source_footprint_library: str = ""
    source_component_library: str = ""
    source_hierarchical_path: str = ""


@dataclass
class PcbGeometryMetadata(PcbMetadata):
    source_collection: str = ""
    source_index: int | None = None
    native_layer_id: str = ""
    native_component_index: int | None = None
    native_polygon_index: int | None = None
    native_subpolygon_index: int | None = None
    locked: bool = False
    hidden: bool = False


@dataclass
class PcbLayer:
    """A layer definition with normalized role metadata."""

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
    def primary_role(self) -> LayerRole:
        """Display/grouping role for single-role consumers such as rendering."""
        for role in _PRIMARY_ROLE_ORDER:
            if role in self.roles:
                return role
        return LayerRole.UNKNOWN

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


class PcbGeometryObject(StrEnum):
    TRACK = "track"
    VIA = "via"
    PAD = "pad"
    ZONE = "zone"
    GRAPHIC = "graphic"
    TEXT = "text"
    DIMENSION = "dimension"
    KEEP_OUT = "keepout"
    MODEL_3D = "model_3d"
    COMPONENT_BODY = "component_body"
    IMAGE = "image"
    TABLE = "table"
    TABLE_CELL = "table_cell"
    TARGET = "target"
    POINT = "point"
    GROUP = "group"
    UNKNOWN = "unknown"


class PcbGeometryShape(StrEnum):
    NONE = "none"
    POINT = "point"
    LINE = "line"
    ARC = "arc"
    CIRCLE = "circle"
    RECTANGLE = "rectangle"
    POLYGON = "polygon"
    TEXT = "text"
    MODEL = "model"
    GROUP = "group"
    UNKNOWN = "unknown"


class PcbGeometryRole(StrEnum):
    COPPER = "copper"
    SOLDER_MASK = "solder_mask"
    SOLDER_PASTE = "solder_paste"
    SILKSCREEN = "silkscreen"
    FABRICATION = "fabrication"
    ASSEMBLY = "assembly"
    COURTYARD = "courtyard"
    DESIGNATOR = "designator"
    VALUE = "value"
    COMMENT = "comment"
    EDGE = "edge"
    MECHANICAL = "mechanical"

    BOARD_OUTLINE = "board_outline"
    BOARD_CUTOUT = "board_cutout"
    POLYGON_CUTOUT = "polygon_cutout"
    CAVITY_DEFINITION = "cavity_definition"
    ROUTE_TOOL_PATH = "route_tool_path"
    V_CUT = "v_cut"

    CONDUCTOR = "conductor"
    ROUTE = "route"
    TRACE = "trace"
    POUR = "pour"
    ZONE_OUTLINE = "zone_outline"
    ZONE_FILL = "zone_fill"
    POLYGON_OUTLINE = "polygon_outline"
    DIFF_PAIR = "diff_pair"
    FROM_TO = "from_to"
    TESTPOINT = "testpoint"

    DRILL = "drill"
    PLATED_HOLE = "plated_hole"
    NON_PLATED_HOLE = "non_plated_hole"
    THROUGH_HOLE = "through_hole"
    SMD = "smd"
    CUSTOM_PAD = "custom_pad"
    BLIND_VIA = "blind_via"
    BURIED_VIA = "buried_via"
    MICROVIA = "microvia"
    FREE_VIA = "free_via"
    TENTED = "tented"
    TENTED_FRONT = "tented_front"
    TENTED_BACK = "tented_back"

    KEEPOUT = "keepout"
    RULE_AREA = "rule_area"
    TRACK_KEEPOUT = "track_keepout"
    VIA_KEEPOUT = "via_keepout"
    PAD_KEEPOUT = "pad_keepout"
    COPPER_POUR_KEEPOUT = "copper_pour_keepout"
    FOOTPRINT_KEEPOUT = "footprint_keepout"

    TEXT = "text"
    USER_TEXT = "user_text"
    BARCODE = "barcode"
    DIMENSION = "dimension"
    LEADER = "leader"
    DATUM = "datum"
    RADIAL = "radial"
    ORTHOGONAL = "orthogonal"
    CENTER_MARK = "center_mark"

    BOARD_LEVEL = "board_level"
    FOOTPRINT_MEMBER = "footprint_member"
    PAD_PRIMITIVE = "pad_primitive"
    CUSTOM_PAD_PRIMITIVE = "custom_pad_primitive"
    COMPONENT_BODY = "component_body"
    USER = "user"
    GENERATED = "generated"

    UNKNOWN = "unknown"


_GEOMETRY_ROLE_ORDER: tuple[PcbGeometryRole, ...] = (
    PcbGeometryRole.COPPER,
    PcbGeometryRole.SOLDER_MASK,
    PcbGeometryRole.SOLDER_PASTE,
    PcbGeometryRole.SILKSCREEN,
    PcbGeometryRole.FABRICATION,
    PcbGeometryRole.ASSEMBLY,
    PcbGeometryRole.COURTYARD,
    PcbGeometryRole.DESIGNATOR,
    PcbGeometryRole.VALUE,
    PcbGeometryRole.COMMENT,
    PcbGeometryRole.EDGE,
    PcbGeometryRole.MECHANICAL,
    PcbGeometryRole.BOARD_OUTLINE,
    PcbGeometryRole.BOARD_CUTOUT,
    PcbGeometryRole.POLYGON_CUTOUT,
    PcbGeometryRole.CAVITY_DEFINITION,
    PcbGeometryRole.ROUTE_TOOL_PATH,
    PcbGeometryRole.V_CUT,
    PcbGeometryRole.CONDUCTOR,
    PcbGeometryRole.ROUTE,
    PcbGeometryRole.TRACE,
    PcbGeometryRole.POUR,
    PcbGeometryRole.ZONE_OUTLINE,
    PcbGeometryRole.ZONE_FILL,
    PcbGeometryRole.POLYGON_OUTLINE,
    PcbGeometryRole.DIFF_PAIR,
    PcbGeometryRole.FROM_TO,
    PcbGeometryRole.TESTPOINT,
    PcbGeometryRole.DRILL,
    PcbGeometryRole.PLATED_HOLE,
    PcbGeometryRole.NON_PLATED_HOLE,
    PcbGeometryRole.THROUGH_HOLE,
    PcbGeometryRole.SMD,
    PcbGeometryRole.CUSTOM_PAD,
    PcbGeometryRole.BLIND_VIA,
    PcbGeometryRole.BURIED_VIA,
    PcbGeometryRole.MICROVIA,
    PcbGeometryRole.FREE_VIA,
    PcbGeometryRole.TENTED,
    PcbGeometryRole.TENTED_FRONT,
    PcbGeometryRole.TENTED_BACK,
    PcbGeometryRole.KEEPOUT,
    PcbGeometryRole.RULE_AREA,
    PcbGeometryRole.TRACK_KEEPOUT,
    PcbGeometryRole.VIA_KEEPOUT,
    PcbGeometryRole.PAD_KEEPOUT,
    PcbGeometryRole.COPPER_POUR_KEEPOUT,
    PcbGeometryRole.FOOTPRINT_KEEPOUT,
    PcbGeometryRole.TEXT,
    PcbGeometryRole.USER_TEXT,
    PcbGeometryRole.BARCODE,
    PcbGeometryRole.DIMENSION,
    PcbGeometryRole.LEADER,
    PcbGeometryRole.DATUM,
    PcbGeometryRole.RADIAL,
    PcbGeometryRole.ORTHOGONAL,
    PcbGeometryRole.CENTER_MARK,
    PcbGeometryRole.BOARD_LEVEL,
    PcbGeometryRole.FOOTPRINT_MEMBER,
    PcbGeometryRole.PAD_PRIMITIVE,
    PcbGeometryRole.CUSTOM_PAD_PRIMITIVE,
    PcbGeometryRole.COMPONENT_BODY,
    PcbGeometryRole.USER,
    PcbGeometryRole.GENERATED,
    PcbGeometryRole.UNKNOWN,
)

_GEOMETRY_PRIMARY_ROLE_ORDER: tuple[PcbGeometryRole, ...] = (
    PcbGeometryRole.BOARD_CUTOUT,
    PcbGeometryRole.BOARD_OUTLINE,
    PcbGeometryRole.EDGE,
    PcbGeometryRole.DRILL,
    PcbGeometryRole.KEEPOUT,
    PcbGeometryRole.TRACE,
    PcbGeometryRole.ROUTE,
    PcbGeometryRole.POUR,
    PcbGeometryRole.ZONE_FILL,
    PcbGeometryRole.COPPER,
    PcbGeometryRole.SOLDER_MASK,
    PcbGeometryRole.SOLDER_PASTE,
    PcbGeometryRole.SILKSCREEN,
    PcbGeometryRole.COURTYARD,
    PcbGeometryRole.DESIGNATOR,
    PcbGeometryRole.VALUE,
    PcbGeometryRole.ASSEMBLY,
    PcbGeometryRole.FABRICATION,
    PcbGeometryRole.DIMENSION,
    PcbGeometryRole.BARCODE,
    PcbGeometryRole.TEXT,
    PcbGeometryRole.COMPONENT_BODY,
    PcbGeometryRole.MECHANICAL,
    PcbGeometryRole.USER,
    PcbGeometryRole.GENERATED,
    PcbGeometryRole.UNKNOWN,
)


def _coerce_geometry_role(role: PcbGeometryRole | str) -> PcbGeometryRole:
    if isinstance(role, PcbGeometryRole):
        return role
    return PcbGeometryRole(role)


def normalize_geometry_roles(*roles: PcbGeometryRole | str) -> tuple[PcbGeometryRole, ...]:
    """Return unique geometry roles in canonical order."""
    role_set = {_coerce_geometry_role(role) for role in roles}
    if not role_set:
        role_set.add(PcbGeometryRole.UNKNOWN)
    return tuple(role for role in _GEOMETRY_ROLE_ORDER if role in role_set)


@dataclass
class PcbLineGeometry:
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    width: float


@dataclass
class PcbArcGeometry:
    start_x: float
    start_y: float
    mid_x: float
    mid_y: float
    end_x: float
    end_y: float
    width: float


@dataclass
class PcbCircleGeometry:
    cx: float
    cy: float
    radius: float
    width: float
    fill: bool = False


@dataclass
class PcbPolygonGeometry:
    points: list[tuple[float, float]]
    holes: list[list[tuple[float, float]]] = field(default_factory=list)


@dataclass
class PcbTextGeometry:
    text: str
    x: float
    y: float
    rotation: float
    font_size: float
    justify: str = ""


@dataclass
class PcbPadGeometry:
    number: str
    x: float
    y: float
    width: float
    height: float
    shape: str
    rotation: float = 0.0
    drill: float = 0.0
    drill_shape: str = "circle"
    drill_width: float = 0.0
    drill_height: float = 0.0
    roundrect_rratio: float = 0.0
    pin_function: str = ""
    pin_type: str = ""
    mid_width: float | None = None
    mid_height: float | None = None
    bot_width: float | None = None
    bot_height: float | None = None
    mid_shape: str = ""
    bot_shape: str = ""
    mask_expansion: float | None = None
    paste_expansion: float | None = None
    mask_aperture_width: float | None = None
    mask_aperture_height: float | None = None
    mask_aperture_source: str = ""


@dataclass
class PcbViaGeometry:
    x: float
    y: float
    size: float
    drill: float
    via_mode: str = ""


@dataclass
class PcbZoneGeometry:
    boundary: list[tuple[float, float]]
    priority: int = 0
    min_thickness_mm: float = 0.0
    thermal_gap_mm: float = 0.0
    thermal_bridge_width_mm: float = 0.0
    connect_pads_clearance_mm: float = 0.0
    fill_type: str = ""


@dataclass
class PcbKeepoutRules:
    """Object classes constrained by a keepout/rule area."""

    tracks: str = ""
    vias: str = ""
    pads: str = ""
    copperpour: str = ""
    footprints: str = ""


@dataclass
class PcbKeepoutGeometry:
    boundary: list[tuple[float, float]]
    rules: PcbKeepoutRules = field(default_factory=PcbKeepoutRules)
    holes: list[list[tuple[float, float]]] = field(default_factory=list)


@dataclass
class PcbDimensionGeometry:
    kind: str
    value_mm: float
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    text: str = ""


@dataclass
class PcbModel3DGeometry:
    source: str
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    cache_key: str = ""


PcbGeometryData = (
    PcbLineGeometry
    | PcbArcGeometry
    | PcbCircleGeometry
    | PcbPolygonGeometry
    | PcbTextGeometry
    | PcbPadGeometry
    | PcbViaGeometry
    | PcbZoneGeometry
    | PcbKeepoutGeometry
    | PcbDimensionGeometry
    | PcbModel3DGeometry
)


@dataclass
class PcbGeometry:
    id: str
    object_type: PcbGeometryObject
    shape: PcbGeometryShape
    roles: tuple[PcbGeometryRole, ...]
    data: PcbGeometryData
    layers: tuple[str, ...] = ()
    net_number: int = 0
    net_name: str = ""
    footprint_ref: str = ""
    metadata: PcbGeometryMetadata = field(default_factory=PcbGeometryMetadata)

    def __post_init__(self) -> None:
        self.roles = normalize_geometry_roles(*self.roles)
        self.layers = tuple(self.layers)

    def has_role(self, role: PcbGeometryRole | str) -> bool:
        """Return whether this geometry has a normalized role."""
        return _coerce_geometry_role(role) in self.roles

    @property
    def role_values(self) -> tuple[str, ...]:
        """String role values suitable for serialization."""
        return tuple(role.value for role in self.roles)

    @property
    def primary_role(self) -> PcbGeometryRole:
        """Display/grouping role for single-role consumers such as rendering."""
        for role in _GEOMETRY_PRIMARY_ROLE_ORDER:
            if role in self.roles:
                return role
        return PcbGeometryRole.UNKNOWN

    @property
    def primary_layer(self) -> str:
        return self.layers[0] if self.layers else ""

    @property
    def display_role(self) -> str:
        if self.object_type in {PcbGeometryObject.PAD, PcbGeometryObject.VIA}:
            return self.object_type.value
        return self.primary_role.value


@dataclass
class PcbFootprint:
    """A placed footprint (component) on the board."""

    reference: str
    footprint_lib: str
    x: float
    y: float
    rotation: float
    layer: str
    value: str = ""
    bbox: tuple[float, float, float, float] | None = None
    properties: dict[str, str] = field(default_factory=dict)
    metadata: PcbFootprintMetadata = field(default_factory=PcbFootprintMetadata)


@dataclass
class PcbNet:
    """A named electrical net."""

    number: int
    name: str
    metadata: PcbNetMetadata = field(default_factory=PcbNetMetadata)


@dataclass
class Pcb:
    """Complete parsed PCB board."""

    name: str
    nets: dict[int, PcbNet]
    footprints: list[PcbFootprint]
    geometry: list[PcbGeometry]
    layers: list[PcbLayer] = field(default_factory=list)
    metadata: PcbMetadata = field(default_factory=PcbMetadata)

    # -- Layer helpers --------------------------------------------------------

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
        """Look up a layer definition by native name."""
        for lyr in self.layers:
            if lyr.name == name:
                return lyr
        return None

    # -- Geometry helpers -----------------------------------------------------

    def geometry_by_role(self, role: PcbGeometryRole | str) -> list[PcbGeometry]:
        """Return all geometry with a given normalized role."""
        return [item for item in self.geometry if item.has_role(role)]

    def geometry_with_all_roles(self, roles: Iterable[PcbGeometryRole | str]) -> list[PcbGeometry]:
        """Return geometry that contains every requested role."""
        normalized = tuple(_coerce_geometry_role(role) for role in roles)
        return [item for item in self.geometry if all(item.has_role(role) for role in normalized)]

    def geometry_with_any_role(self, roles: Iterable[PcbGeometryRole | str]) -> list[PcbGeometry]:
        """Return geometry that contains at least one requested role."""
        normalized = tuple(_coerce_geometry_role(role) for role in roles)
        return [item for item in self.geometry if any(item.has_role(role) for role in normalized)]

    def geometry_by_object_type(self, object_type: PcbGeometryObject | str) -> list[PcbGeometry]:
        """Return geometry with a given object family."""
        normalized = (
            object_type
            if isinstance(object_type, PcbGeometryObject)
            else PcbGeometryObject(object_type)
        )
        return [item for item in self.geometry if item.object_type is normalized]

    def geometry_by_shape(self, shape: PcbGeometryShape | str) -> list[PcbGeometry]:
        """Return geometry with a given shape."""
        normalized = shape if isinstance(shape, PcbGeometryShape) else PcbGeometryShape(shape)
        return [item for item in self.geometry if item.shape is normalized]

    def geometry_on_layer(self, layer_name: str) -> list[PcbGeometry]:
        """Return geometry that references a native layer name."""
        return [item for item in self.geometry if layer_name in item.layers]

    def geometry_for_footprint(self, ref: str) -> list[PcbGeometry]:
        """Return geometry owned by a footprint reference designator."""
        ref_upper = ref.upper()
        return [item for item in self.geometry if item.footprint_ref.upper() == ref_upper]

    def geometry_for_net(self, net_number: int) -> list[PcbGeometry]:
        """Return geometry connected to a net number."""
        return [item for item in self.geometry if item.net_number == net_number]

    def board_profile_geometry(self) -> list[PcbGeometry]:
        """Return physical board profile geometry used for bounds and clipping."""
        profile_roles = {
            PcbGeometryRole.BOARD_OUTLINE,
            PcbGeometryRole.BOARD_CUTOUT,
        }
        profile_shapes = {
            PcbGeometryShape.LINE,
            PcbGeometryShape.ARC,
            PcbGeometryShape.POLYGON,
        }
        return [
            item
            for item in self.geometry
            if item.shape in profile_shapes and profile_roles.intersection(item.roles)
        ]

    # -- Component helpers ----------------------------------------------------

    def footprint_by_ref(self, ref: str) -> PcbFootprint | None:
        """Look up a footprint by reference designator (case-insensitive)."""
        ref_upper = ref.upper()
        for fp in self.footprints:
            if fp.reference.upper() == ref_upper:
                return fp
        return None

    def nets_for_component(self, ref: str) -> set[int]:
        """Return all net numbers connected to a component's geometry."""
        return {
            item.net_number for item in self.geometry_for_footprint(ref) if item.net_number != 0
        }

    def net_numbers_by_name(self, name: str) -> set[int]:
        """Return net numbers matching *name* (case-insensitive exact match)."""
        needle = name.upper()
        return {n.number for n in self.nets.values() if n.name and n.name.upper() == needle}

    def bbox(self) -> tuple[float, float, float, float]:
        """Board bounding box from normalized profile geometry."""
        xs: list[float] = []
        ys: list[float] = []
        for item in self.board_profile_geometry():
            _extend_geometry_bounds(xs, ys, item)
        if not xs:
            for item in self.geometry_by_object_type(PcbGeometryObject.PAD):
                if isinstance(item.data, PcbPadGeometry):
                    xs.extend(
                        [item.data.x - item.data.width / 2, item.data.x + item.data.width / 2]
                    )
                    ys.extend(
                        [item.data.y - item.data.height / 2, item.data.y + item.data.height / 2]
                    )
        if not xs:
            return (0.0, 0.0, 100.0, 100.0)
        return (min(xs), min(ys), max(xs), max(ys))


def _extend_geometry_bounds(xs: list[float], ys: list[float], item: PcbGeometry) -> None:
    data = item.data
    if isinstance(data, PcbLineGeometry):
        xs.extend([data.start_x, data.end_x])
        ys.extend([data.start_y, data.end_y])
    elif isinstance(data, PcbArcGeometry):
        xs.extend([data.start_x, data.mid_x, data.end_x])
        ys.extend([data.start_y, data.mid_y, data.end_y])
    elif isinstance(data, PcbCircleGeometry):
        xs.extend([data.cx - data.radius, data.cx + data.radius])
        ys.extend([data.cy - data.radius, data.cy + data.radius])
    elif isinstance(data, PcbPolygonGeometry):
        xs.extend(x for x, _y in data.points)
        ys.extend(y for _x, y in data.points)
    elif isinstance(data, PcbPadGeometry):
        xs.extend([data.x - data.width / 2, data.x + data.width / 2])
        ys.extend([data.y - data.height / 2, data.y + data.height / 2])
