"""Parse an Altium Designer .PcbDoc file into the PCB domain model.

A .PcbDoc is an OLE compound document containing separate streams for each
primitive type (tracks, pads, vias, etc.).  Text-based streams use
pipe-delimited ASCII properties; binary streams use fixed-size records with
a type(u8) + length(u32) header.

Coordinates in binary streams are stored as i32 in units of 0.1 µinch.
Text streams store coordinates as mil strings (e.g. "1153.8945mil").
All output coordinates are in millimetres with Y increasing downward.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING

import olefile

from phosphor_eda.domain.pcb import (
    LayerRole,
    Pcb,
    PcbArc,
    PcbArtwork,
    PcbArtworkKind,
    PcbArtworkPurpose,
    PcbBoardProfile,
    PcbBoardProfileElement,
    PcbCircle,
    PcbClosedPath,
    PcbConductor,
    PcbConductorKind,
    PcbDrill,
    PcbDrillPlating,
    PcbDrillShape,
    PcbFootprint,
    PcbFootprintMetadata,
    PcbKeepout,
    PcbKeepoutPermission,
    PcbKeepoutRules,
    PcbLayer,
    PcbLayerMetadata,
    PcbLine,
    PcbMaskAperture,
    PcbMetadata,
    PcbModel3D,
    PcbNet,
    PcbObjectMetadata,
    PcbPad,
    PcbPadType,
    PcbPolygon,
    PcbPour,
    PcbPourFillMode,
    PcbPourSettings,
    PcbText,
    PcbVia,
    PcbViaType,
)
from phosphor_eda.domain.pcb_builder import PcbBuilder
from phosphor_eda.domain.project import DesignRule, DiffPair, NetClass, Stackup, StackupLayer
from phosphor_eda.formats.altium._helpers import u32
from phosphor_eda.formats.altium.enums import (
    AltiumLayer,
    PadShape,
    PadShapeAlt,
    PcbRecordType,
    RegionKind,
)
from phosphor_eda.formats.altium.errors import AltiumPcbParseError
from phosphor_eda.formats.altium.pcb_records import (
    COMPONENT_NONE,
    NET_UNCONNECTED,
    ArcRecord,
    ExtendedVertex,
    FillRecord,
    PadRecord,
    RegionRecord,
    ShapeBasedRegionRecord,
    TextRecord,
    TrackRecord,
    ViaRecord,
)
from phosphor_eda.formats.altium.record_parser import parse_record_payload
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.common.text import strip_overline

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 1 internal unit = 0.1 µinch = 0.0000001 inch = 0.00000254 mm
_INT_TO_MM = 0.00000254

# 1 mil = 0.001 inch = 0.0254 mm
_MIL_TO_MM = 0.0254

_POLYGON_NONE = 0xFFFF

# Altium Board6/Data carries layer names and mechanical kinds. Numeric layer
# ranges are used only to decode file-format semantics after the source has
# provided a concrete layer identity.
_ALTIUM_LAYER_NUMBERS = tuple(range(AltiumLayer.TOP_LAYER, AltiumLayer.MULTI_LAYER + 1))

_MECHKIND_ROLES: dict[str, tuple[LayerRole, ...]] = {
    "assemblytop": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.ASSEMBLY,
        LayerRole.FRONT,
    ),
    "assemblybottom": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.ASSEMBLY,
        LayerRole.BACK,
    ),
    "assemblynotes": (LayerRole.MECHANICAL, LayerRole.ASSEMBLY_NOTES),
    "board": (LayerRole.MECHANICAL, LayerRole.BOARD),
    "coatingtop": (LayerRole.MECHANICAL, LayerRole.COATING, LayerRole.FRONT),
    "coatingbottom": (LayerRole.MECHANICAL, LayerRole.COATING, LayerRole.BACK),
    "componentcentertop": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.COMPONENT_CENTER,
        LayerRole.FRONT,
    ),
    "componentcenterbottom": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.COMPONENT_CENTER,
        LayerRole.BACK,
    ),
    "componentoutlinetop": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.COMPONENT_OUTLINE,
        LayerRole.FRONT,
    ),
    "componentoutlinebottom": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.COMPONENT_OUTLINE,
        LayerRole.BACK,
    ),
    "courtyardtop": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.COURTYARD,
        LayerRole.FRONT,
    ),
    "courtyardbottom": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.COURTYARD,
        LayerRole.BACK,
    ),
    "designatortop": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.DESIGNATOR,
        LayerRole.FRONT,
    ),
    "designatorbottom": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.DESIGNATOR,
        LayerRole.BACK,
    ),
    "dimensions": (LayerRole.MECHANICAL, LayerRole.DIMENSION),
    "dimensionstop": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.DIMENSION,
        LayerRole.FRONT,
    ),
    "dimensionsbottom": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.DIMENSION,
        LayerRole.BACK,
    ),
    "fabnotes": (LayerRole.MECHANICAL, LayerRole.FABRICATION, LayerRole.FAB_NOTES),
    "gluepointstop": (LayerRole.MECHANICAL, LayerRole.GLUE_POINTS, LayerRole.FRONT),
    "gluepointsbottom": (LayerRole.MECHANICAL, LayerRole.GLUE_POINTS, LayerRole.BACK),
    "goldplatingtop": (LayerRole.MECHANICAL, LayerRole.GOLD_PLATING, LayerRole.FRONT),
    "goldplatingbottom": (LayerRole.MECHANICAL, LayerRole.GOLD_PLATING, LayerRole.BACK),
    "valuetop": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.VALUE,
        LayerRole.FRONT,
    ),
    "valuebottom": (
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.VALUE,
        LayerRole.BACK,
    ),
    "vcut": (LayerRole.MECHANICAL, LayerRole.V_CUT),
    "3dbodytop": (LayerRole.MECHANICAL, LayerRole.THREE_D_BODY, LayerRole.FRONT),
    "3dbodybottom": (LayerRole.MECHANICAL, LayerRole.THREE_D_BODY, LayerRole.BACK),
    "routetoolpath": (LayerRole.MECHANICAL, LayerRole.ROUTE_TOOL_PATH),
    "sheet": (LayerRole.MECHANICAL, LayerRole.SHEET),
    "boardshape": (LayerRole.MECHANICAL, LayerRole.BOARD_SHAPE, LayerRole.EDGE),
}

# Copper layer numbers for filtering (top, mid 1-30, bottom).
_COPPER_LAYERS = frozenset(range(AltiumLayer.TOP_LAYER, AltiumLayer.BOTTOM_LAYER + 1))

# V7 layer name → Altium layer number.  Used to resolve the V7_LAYER property
# that overrides the byte-level layer number in region records.
_V7_NAME_TO_NUM: dict[str, int] = {
    "TOP": 1,
    **{f"MID{i - 1}": i for i in range(2, 32)},
    "BOTTOM": 32,
    "TOPOVERLAY": 33,
    "BOTTOMOVERLAY": 34,
    "TOPPASTE": 35,
    "BOTTOMPASTE": 36,
    "TOPSOLDER": 37,
    "BOTTOMSOLDER": 38,
    **{f"MECHANICAL{i}": 56 + i for i in range(1, 17)},
}

_V9_STACK_LAYER_ID_TO_NUM: dict[int, int] = {
    16777217: 1,
    **{16777218 + index: 2 + index for index in range(30)},
    16842751: 32,
    16973830: 33,
    16973831: 34,
    16973832: 35,
    16973833: 36,
    16973834: 37,
    16973835: 38,
}

# Pad shape byte → domain string (octagonal is treated as rect).
_PAD_SHAPES: dict[PadShape, str] = {
    PadShape.CIRCLE: "circle",
    PadShape.RECT: "rect",
    PadShape.OCTAGONAL: "rect",
}


def _pad_shape(value: int) -> PadShape:
    try:
        return PadShape(value)
    except ValueError:
        return PadShape.UNKNOWN


_PAD_TEMPLATE_MASK_RE = re.compile(
    r"^r(?P<pad_w>\d+)_(?P<pad_h>\d+)hn(?P<drill>\d+)r(?P<rounding>\d+)"
    r"m(?P<mask_w>\d+)_(?P<mask_h>\d+)$"
)


@dataclass(frozen=True)
class _PadMaskAperture:
    width: float
    height: float
    source: str


@dataclass(frozen=True)
class _DrillManagerRecord:
    properties: dict[str, str]
    primitive_indices: tuple[int, ...]


class _ParsedRole(StrEnum):
    ASSEMBLY = "assembly"
    BLIND_VIA = "blind_via"
    BOARD_OUTLINE = "board_outline"
    COMPONENT_BODY = "component_body"
    CONDUCTOR = "conductor"
    COURTYARD = "courtyard"
    DESIGNATOR = "designator"
    FABRICATION = "fabrication"
    FREE_VIA = "free_via"
    MECHANICAL = "mechanical"
    PLATED_HOLE = "plated_hole"
    POLYGON_CUTOUT = "polygon_cutout"
    SILKSCREEN = "silkscreen"
    SOLDER_MASK = "solder_mask"
    SOLDER_PASTE = "solder_paste"
    TEXT = "text"
    USER_TEXT = "user_text"
    VALUE = "value"


class _ParsedObjectKind(StrEnum):
    GRAPHIC = "graphic"
    MODEL_3D = "model_3d"
    PAD = "pad"
    REGION = "region"
    TEXT = "text"
    TRACK = "track"
    VIA = "via"


class _ParsedShapeKind(StrEnum):
    ARC = "arc"
    CIRCLE = "circle"
    LINE = "line"
    MODEL = "model"
    POLYGON = "polygon"
    RECTANGLE = "rectangle"
    TEXT = "text"


_PARSED_ROLE_ORDER: tuple[_ParsedRole, ...] = tuple(_ParsedRole)


def _normalize_parsed_roles(*roles: _ParsedRole | str) -> tuple[_ParsedRole, ...]:
    role_set = {role if isinstance(role, _ParsedRole) else _ParsedRole(role) for role in roles}
    return tuple(role for role in _PARSED_ROLE_ORDER if role in role_set)


@dataclass
class _ParsedPadPayload:
    number: str
    x: float
    y: float
    width: float
    height: float
    shape: str
    rotation: float = 0.0
    drill: float = 0.0
    mask_aperture_width: float | None = None
    mask_aperture_height: float | None = None
    mask_aperture_source: str = ""


@dataclass(frozen=True)
class _ParsedViaPayload:
    x: float
    y: float
    size: float
    drill: float


type _ParsedPayload = (
    PcbLine
    | PcbArc
    | PcbCircle
    | PcbPolygon
    | PcbText
    | PcbModel3D
    | _ParsedPadPayload
    | _ParsedViaPayload
)


@dataclass(frozen=True, kw_only=True)
class _ParsedPrimitive:
    id: str
    object_type: _ParsedObjectKind
    shape: _ParsedShapeKind
    roles: tuple[_ParsedRole, ...]
    data: _ParsedPayload
    layers: tuple[str, ...] = ()
    net_number: int = 0
    net_name: str = ""
    footprint_ref: str = ""
    pour_id: str = ""
    metadata: PcbObjectMetadata = field(default_factory=PcbObjectMetadata)

    def __post_init__(self) -> None:
        object.__setattr__(self, "layers", tuple(self.layers))
        object.__setattr__(self, "roles", _normalize_parsed_roles(*self.roles))

    @property
    def primary_layer(self) -> str:
        return self.layers[0] if self.layers else ""

    def has_role(self, role: _ParsedRole | str) -> bool:
        normalized = role if isinstance(role, _ParsedRole) else _ParsedRole(role)
        return normalized in self.roles


# ---------------------------------------------------------------------------
# Low-level stream readers
# ---------------------------------------------------------------------------


def read_text_records(
    data: bytes,
    ctx: ParseContext | None = None,
    *,
    source: str = "text records",
) -> list[dict[str, str]]:
    """Read pipe-delimited text records with a 4-byte LE length prefix."""
    records: list[dict[str, str]] = []
    pos = 0
    while pos + 4 <= len(data):
        length = u32(data, pos)
        pos += 4
        if length == 0 or pos + length > len(data):
            if ctx is not None and pos < len(data):
                ctx.warn(
                    "truncated_stream",
                    f"{source}: record length runs past end of stream at byte {pos}; "
                    f"{len(data) - pos} trailing bytes dropped",
                )
            break
        payload = data[pos : pos + length]
        pos += length
        props = parse_record_payload(payload)
        if props:
            records.append(props)
    return records


def _read_binary_records(
    data: bytes,
    ctx: ParseContext | None = None,
    *,
    source: str = "binary records",
) -> list[tuple[int, bytes]]:
    """Read binary records with type(u8) + length(u32) + body framing."""
    records: list[tuple[int, bytes]] = []
    pos = 0
    while pos + 5 <= len(data):
        rec_type = data[pos]
        rec_len = u32(data, pos + 1)
        pos += 5
        if pos + rec_len > len(data):
            if ctx is not None:
                ctx.warn(
                    "truncated_stream",
                    f"{source}: record body runs past end of stream at byte {pos}; "
                    f"{len(data) - pos} trailing bytes dropped",
                )
            break
        records.append((rec_type, data[pos : pos + rec_len]))
        pos += rec_len
    return records


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------


def _int_to_mm(val: int) -> float:
    """Convert Altium internal units (0.1 µinch) to millimetres."""
    return val * _INT_TO_MM


def _parse_mil(s: str) -> float:
    """Parse a mil-string like ``'1153.8945mil'`` and return mm."""
    return float(s.removesuffix("mil")) * _MIL_TO_MM


def _parse_rotation(s: str) -> float:
    """Parse a rotation string (may be scientific notation)."""
    return float(s)


def _build_layer_map(
    board_props: dict[str, str], ctx: ParseContext | None = None
) -> dict[int, PcbLayer]:
    """Build layer definitions from Board6 metadata and Altium layer IDs."""
    layers: dict[int, PcbLayer] = {}
    for num in _ALTIUM_LAYER_NUMBERS:
        native_kind = board_props.get(f"layer{num}mechkind", "")
        name = board_props.get(f"layer{num}name", "")
        if not name:
            continue
        layers[num] = PcbLayer(
            name=name,
            roles=(
                *_altium_number_roles(num),
                *_altium_mechkind_roles(native_kind),
                *_altium_name_roles(num, name, native_kind),
            ),
            number=num,
            metadata=PcbLayerMetadata(source_format="altium", native_kind=native_kind),
        )

    _apply_v9_stack_layer_names(layers, board_props, ctx)
    return layers


def _altium_number_roles(num: int) -> tuple[LayerRole, ...]:
    if num == AltiumLayer.TOP_LAYER:
        return (LayerRole.COPPER, LayerRole.FRONT, LayerRole.OUTER, LayerRole.SIGNAL)
    if AltiumLayer.MID_LAYER_1 <= num <= AltiumLayer.MID_LAYER_30:
        return (LayerRole.COPPER, LayerRole.INNER, LayerRole.SIGNAL)
    if num == AltiumLayer.BOTTOM_LAYER:
        return (LayerRole.COPPER, LayerRole.BACK, LayerRole.OUTER, LayerRole.SIGNAL)
    if num == AltiumLayer.TOP_OVERLAY:
        return (LayerRole.SILKSCREEN, LayerRole.FRONT)
    if num == AltiumLayer.BOTTOM_OVERLAY:
        return (LayerRole.SILKSCREEN, LayerRole.BACK)
    if num == AltiumLayer.TOP_PASTE:
        return (LayerRole.SOLDER_PASTE, LayerRole.FRONT)
    if num == AltiumLayer.BOTTOM_PASTE:
        return (LayerRole.SOLDER_PASTE, LayerRole.BACK)
    if num == AltiumLayer.TOP_SOLDER:
        return (LayerRole.SOLDER_MASK, LayerRole.FRONT)
    if num == AltiumLayer.BOTTOM_SOLDER:
        return (LayerRole.SOLDER_MASK, LayerRole.BACK)
    if AltiumLayer.INTERNAL_PLANE_1 <= num <= AltiumLayer.INTERNAL_PLANE_16:
        return (LayerRole.COPPER, LayerRole.INNER, LayerRole.PLANE, LayerRole.INTERNAL_PLANE)
    if num == AltiumLayer.DRILL_GUIDE:
        return (LayerRole.DRILL, LayerRole.DRILL_GUIDE)
    if num == AltiumLayer.KEEP_OUT_LAYER:
        return (LayerRole.KEEPOUT,)
    if AltiumLayer.MECHANICAL_1 <= num <= AltiumLayer.MECHANICAL_16:
        return (LayerRole.MECHANICAL,)
    if num == AltiumLayer.DRILL_DRAWING:
        return (LayerRole.DRILL, LayerRole.DRILL_DRAWING)
    if num == AltiumLayer.MULTI_LAYER:
        return (LayerRole.MULTI_LAYER,)
    return (LayerRole.UNKNOWN,)


def _altium_mechkind_roles(kind: str) -> tuple[LayerRole, ...]:
    return _MECHKIND_ROLES.get(kind.lower(), ())


def _altium_name_roles(num: int, name: str, native_kind: str) -> tuple[LayerRole, ...]:
    if native_kind or not (AltiumLayer.MECHANICAL_1 <= num <= AltiumLayer.MECHANICAL_16):
        return ()
    normalized = name.strip().lower().replace("-", " ").replace("_", " ")
    roles: list[LayerRole] = []
    if "outline" in normalized or "board shape" in normalized:
        roles.extend([LayerRole.BOARD_SHAPE, LayerRole.EDGE])
    if "courtyard" in normalized:
        roles.extend([LayerRole.FABRICATION, LayerRole.COURTYARD])
    if "assembly" in normalized:
        roles.extend([LayerRole.FABRICATION, LayerRole.ASSEMBLY])
    if "designator" in normalized or "reference" in normalized:
        roles.extend([LayerRole.FABRICATION, LayerRole.DESIGNATOR])
    if "value" in normalized or "comment" in normalized:
        roles.extend([LayerRole.FABRICATION, LayerRole.VALUE])
    if "3d body" in normalized or "3dbody" in normalized:
        roles.append(LayerRole.THREE_D_BODY)
    if normalized.startswith("top ") or normalized.endswith(" top"):
        roles.append(LayerRole.FRONT)
    elif normalized.startswith("bottom ") or normalized.endswith(" bottom"):
        roles.append(LayerRole.BACK)
    return tuple(roles)


def _apply_v9_stack_layer_names(
    layers: dict[int, PcbLayer],
    board_props: dict[str, str],
    ctx: ParseContext | None = None,
) -> None:
    """Use Altium v9 stackup layer IDs to preserve file-defined physical layer names."""
    for key, raw_layer_id in board_props.items():
        if not key.startswith("v9_stack_layer") or not key.endswith("_layerid"):
            continue

        prefix = key[: -len("layerid")]
        layer_name = board_props.get(f"{prefix}name", "")
        if not layer_name:
            continue

        layer_num = _v9_stack_layer_id_to_num(raw_layer_id, ctx, key=key)
        if layer_num is None:
            continue

        layers[layer_num] = PcbLayer(
            name=layer_name,
            roles=(
                *_altium_number_roles(layer_num),
                *_altium_name_roles(layer_num, layer_name, ""),
            ),
            number=layer_num,
            metadata=PcbLayerMetadata(source_format="altium"),
        )


def _v9_stack_layer_id_to_num(
    raw_layer_id: str, ctx: ParseContext | None = None, *, key: str = ""
) -> int | None:
    try:
        layer_id = int(raw_layer_id)
    except ValueError:
        if ctx is not None:
            ctx.warn(
                "malformed_layer_id",
                f"non-integer v9 stack layer id {raw_layer_id!r} for {key or 'layer'}; skipped",
            )
        return None
    return _V9_STACK_LAYER_ID_TO_NUM.get(layer_id)


def _layer_name(num: int, layer_map: dict[int, PcbLayer]) -> str:
    """Get native layer name for a layer number, or '' if unmapped."""
    layer = layer_map.get(num)
    return layer.name if layer else ""


def _layer_ref(num: int, layer_map: dict[int, PcbLayer], *, source: str) -> PcbLayer:
    layer = layer_map.get(num)
    if layer is None:
        msg = f"{source}: unknown Altium layer {num}; Board6/Data has no concrete layer name"
        raise AltiumPcbParseError(msg)
    return layer


def _net_number(raw: int) -> int:
    """Map Altium net index to domain net number (0 = unconnected)."""
    return 0 if raw == NET_UNCONNECTED else raw + 1


_LAYER_TO_GEOMETRY_ROLES: dict[LayerRole, _ParsedRole] = {
    LayerRole.SOLDER_MASK: _ParsedRole.SOLDER_MASK,
    LayerRole.SOLDER_PASTE: _ParsedRole.SOLDER_PASTE,
    LayerRole.SILKSCREEN: _ParsedRole.SILKSCREEN,
    LayerRole.FABRICATION: _ParsedRole.FABRICATION,
    LayerRole.ASSEMBLY: _ParsedRole.ASSEMBLY,
    LayerRole.COURTYARD: _ParsedRole.COURTYARD,
    LayerRole.DESIGNATOR: _ParsedRole.DESIGNATOR,
    LayerRole.VALUE: _ParsedRole.VALUE,
    LayerRole.MECHANICAL: _ParsedRole.MECHANICAL,
}


def _layer_geometry_roles(
    layer_num: int,
    layer_map: dict[int, PcbLayer],
) -> tuple[_ParsedRole, ...]:
    layer = layer_map.get(layer_num)
    if layer is None:
        return ()
    return tuple(
        geometry_role
        for role in layer.roles
        if (geometry_role := _LAYER_TO_GEOMETRY_ROLES.get(role)) is not None
    )


def _geometry_metadata(
    *,
    native_type: str,
    source_collection: str,
    native_kind: str = "",
    native_index: int | None = None,
    native_component_index: int | None = None,
    native_polygon_index: int | None = None,
    native_subpolygon_index: int | None = None,
    properties: dict[str, str] | None = None,
) -> PcbObjectMetadata:
    return PcbObjectMetadata(
        source_format="altium",
        native_type=native_type,
        native_kind=native_kind,
        native_index=native_index,
        source_collection=source_collection,
        native_component_index=native_component_index,
        native_polygon_index=native_polygon_index,
        native_subpolygon_index=native_subpolygon_index,
        properties=properties or {},
    )


def _pour_metadata(
    *,
    native_type: str,
    native_index: int | None = None,
    native_pour_index: int | None = None,
    properties: dict[str, str] | None = None,
) -> PcbObjectMetadata:
    return PcbObjectMetadata(
        source_format="altium",
        native_type=native_type,
        native_index=native_index,
        native_pour_index=native_pour_index,
        properties=properties or {},
    )


def _keepout_metadata(
    *,
    native_type: str,
    native_kind: str = "keepout",
    native_index: int | None = None,
    native_component_index: int | None = None,
    properties: dict[str, str] | None = None,
) -> PcbObjectMetadata:
    metadata_properties = dict(properties or {})
    if native_component_index is not None:
        metadata_properties["native_component_index"] = str(native_component_index)
    return PcbObjectMetadata(
        source_format="altium",
        native_type=native_type,
        native_kind=native_kind,
        native_index=native_index,
        properties=metadata_properties,
    )


def _resolve_pour_id(
    pour_id_map: dict[int, str],
    *indexes: int | None,
) -> str:
    for index in indexes:
        if index is not None and index >= 0 and index != _POLYGON_NONE and index in pour_id_map:
            return pour_id_map[index]
    return ""


def _resolve_pour_net(
    pour_net_map: dict[int, int] | None,
    *indexes: int | None,
) -> int:
    if pour_net_map is None:
        return 0
    for index in indexes:
        if index is not None and index >= 0 and index != _POLYGON_NONE and index in pour_net_map:
            return pour_net_map[index]
    return 0


def _layered_geometry_roles(
    layer_num: int,
    layer_map: dict[int, PcbLayer],
    *roles: _ParsedRole,
) -> tuple[_ParsedRole, ...]:
    return _normalize_parsed_roles(*_layer_geometry_roles(layer_num, layer_map), *roles)


def _with_footprint_ref(item: _ParsedPrimitive, footprint_ref: str) -> _ParsedPrimitive:
    return replace(item, footprint_ref=footprint_ref)


# ---------------------------------------------------------------------------
# Arc conversion: center/radius/angles → three-point
# ---------------------------------------------------------------------------


def _arc_to_three_point(
    cx_mm: float,
    cy_mm: float,
    radius_mm: float,
    start_deg: float,
    end_deg: float,
) -> tuple[float, float, float, float, float, float]:
    """Convert a center/radius/angle arc to (sx, sy, mx, my, ex, ey).

    The arc goes **counter-clockwise** from ``start_deg`` to ``end_deg``.
    When ``end_deg < start_deg`` the arc wraps past 360°.

    Callers that negate Y should use original (non-negated) angles here,
    then negate the Y coordinates of the returned points.
    """
    sa = math.radians(start_deg)
    ea = math.radians(end_deg)
    # Mid-angle: halfway around the CCW arc from start to end.
    if end_deg >= start_deg:
        ma = (sa + ea) / 2
    else:
        # Arc wraps past 360°.
        ma = (sa + ea + 2 * math.pi) / 2
        if ma >= 2 * math.pi:
            ma -= 2 * math.pi

    sx = cx_mm + radius_mm * math.cos(sa)
    sy = cy_mm + radius_mm * math.sin(sa)
    mx = cx_mm + radius_mm * math.cos(ma)
    my = cy_mm + radius_mm * math.sin(ma)
    ex = cx_mm + radius_mm * math.cos(ea)
    ey = cy_mm + radius_mm * math.sin(ea)
    return (sx, sy, mx, my, ex, ey)


def _arc_shape_payload(
    cx: float,
    cy_orig: float,
    radius: float,
    width: float,
    start_deg: float,
    end_deg: float,
) -> tuple[_ParsedShapeKind, PcbArc | PcbCircle]:
    if _is_full_circle_arc(start_deg, end_deg):
        # Altium stores the radius at the stroke centerline. Unfilled PcbCircle
        # payloads use the outer radius plus width to describe the annulus.
        return (
            _ParsedShapeKind.CIRCLE,
            PcbCircle(cx, -cy_orig, radius + width / 2.0, width, fill=False),
        )

    sx, sy, mx, my, ex, ey = _arc_to_three_point(cx, cy_orig, radius, start_deg, end_deg)
    return _ParsedShapeKind.ARC, PcbArc(sx, -sy, mx, -my, ex, -ey, width)


# ---------------------------------------------------------------------------
# Arc linearization for ShapeBasedRegion extended vertices
# ---------------------------------------------------------------------------

# Number of line segments per full circle when linearizing arcs
_ARC_SEGMENTS_PER_CIRCLE = 64


def linearize_arc_vertices(
    vertices: list[ExtendedVertex],
    segments_per_circle: int = _ARC_SEGMENTS_PER_CIRCLE,
) -> list[tuple[int, int]]:
    """Convert extended vertices to a polyline, interpolating arc edges.

    When a vertex has ``is_round=True``, the edge from that vertex to the
    next is an arc defined by center/radius/angles. This function replaces
    each arc edge with a sequence of line segments approximating the curve.

    Coordinates remain in Altium internal units (0.1 µinch). The caller
    handles mm conversion.
    """
    if not vertices:
        return []

    points: list[tuple[int, int]] = []

    for v in vertices:
        if not v.is_round:
            points.append((v.x, v.y))
            continue

        # Arc edge: interpolate from start_angle to end_angle
        cx, cy = v.center_x, v.center_y
        radius = v.radius
        start_deg = v.start_angle
        end_deg = v.end_angle

        # Compute sweep angle (always CCW in Altium)
        sweep = end_deg - start_deg
        if sweep <= 0:
            sweep += 360.0

        # Number of segments proportional to sweep angle
        n_segs = max(2, round(segments_per_circle * sweep / 360.0))

        for j in range(n_segs):
            angle_deg = start_deg + sweep * j / n_segs
            angle_rad = math.radians(angle_deg)
            px = round(cx + radius * math.cos(angle_rad))
            py = round(cy + radius * math.sin(angle_rad))
            points.append((px, py))

    return points


# ---------------------------------------------------------------------------
# Stream parsers
# ---------------------------------------------------------------------------


def _parse_nets(data: bytes) -> dict[int, PcbNet]:
    """Parse Nets6/Data → {net_number: PcbNet}.

    Nets are numbered starting at 1 (index+1 in the stream order).
    Net 0 is reserved for "unconnected".
    """
    records = read_text_records(data)
    nets: dict[int, PcbNet] = {}
    for i, rec in enumerate(records):
        num = i + 1
        raw_name = rec.get("name", "")
        # Strip Altium overline markup (e.g. "C\S\" → "CS") so net names
        # are clean for CSS selectors and downstream tooling.
        clean_name = strip_overline(raw_name)[0]
        nets[num] = PcbNet(number=num, name=clean_name)
    return nets


def _parse_components(data: bytes, layer_map: dict[int, PcbLayer]) -> list[PcbFootprint]:
    """Parse Components6/Data → list of footprint shells.

    Component records are text-based and contain position, pattern,
    layer, rotation, and designator.  Pads and geometry are added later.
    """
    records = read_text_records(data)
    footprints: list[PcbFootprint] = []
    for rec in records:
        x_str = rec.get("x", "0mil")
        y_str = rec.get("y", "0mil")
        x_mm = _parse_mil(x_str)
        y_mm = -_parse_mil(y_str)  # Negate Y

        layer_str = rec.get("layer", "TOP")
        layer = _layer_ref(1 if layer_str.upper() == "TOP" else 32, layer_map, source="component")

        rot = _parse_rotation(rec.get("rotation", "0"))

        ref = rec.get("sourcedesignator", rec.get("designator", "?"))
        pattern = rec.get("pattern", "")

        footprints.append(
            PcbFootprint(
                reference=ref,
                footprint_lib=pattern,
                x=x_mm,
                y=y_mm,
                rotation=rot,
                layer=layer,
                metadata=PcbFootprintMetadata(
                    source_format="altium",
                    native_type="component",
                    properties={
                        "nameon": rec.get("nameon", "TRUE"),
                        "commenton": rec.get("commenton", "FALSE"),
                    },
                    source_designator=ref,
                    source_unique_id=rec.get("uniqueid", ""),
                    source_footprint_library=pattern,
                    source_component_library=rec.get("sourcelibref", ""),
                ),
            )
        )
    return footprints


def _parse_tracks(
    data: bytes,
    layer_map: dict[int, PcbLayer],
    ctx: ParseContext,
    pour_id_map: dict[int, str] | None = None,
) -> tuple[list[_ParsedPrimitive], list[PcbKeepout]]:
    """Parse Tracks6/Data into normalized line geometry."""
    records = _read_binary_records(data, ctx, source="Tracks6/Data")
    geometry: list[_ParsedPrimitive] = []
    keepouts: list[PcbKeepout] = []
    resolved_pour_id_map = pour_id_map or {}

    for index, (rec_type, body) in enumerate(records):
        if rec_type != PcbRecordType.TRACK:
            continue
        track = TrackRecord.from_bytes(body, ctx)
        if track is None:
            continue

        layer_ref = _layer_ref(track.layer, layer_map, source=f"track {index}")
        layer = layer_ref.name

        x1 = _int_to_mm(track.start[0])
        y1 = -_int_to_mm(track.start[1])
        x2 = _int_to_mm(track.end[0])
        y2 = -_int_to_mm(track.end[1])
        width = _int_to_mm(track.width)

        component_index = None if track.component == COMPONENT_NONE else track.component
        if track.is_keepout:
            keepouts.append(
                _keepout_from_line(
                    layer=layer_ref,
                    track=track,
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                    width=width,
                    index=index,
                    component_index=component_index,
                )
            )
            continue

        pour_id = _resolve_pour_id(resolved_pour_id_map, track.polygon)
        if track.layer in _COPPER_LAYERS:
            object_type = _ParsedObjectKind.TRACK
            roles = _layered_geometry_roles(
                track.layer,
                layer_map,
                _ParsedRole.CONDUCTOR,
            )
            source_collection = "conductors"
            net_number = _net_number(track.net)
        elif layer_map[track.layer].has_role(LayerRole.EDGE):
            object_type = _ParsedObjectKind.GRAPHIC
            roles = _layered_geometry_roles(
                track.layer,
                layer_map,
                _ParsedRole.BOARD_OUTLINE,
            )
            source_collection = "board_profile"
            net_number = 0
        else:
            object_type = _ParsedObjectKind.GRAPHIC
            roles = _layered_geometry_roles(track.layer, layer_map)
            source_collection = "footprint_artwork" if component_index is not None else "artwork"
            net_number = 0

        geometry.append(
            _ParsedPrimitive(
                id=f"track:{track.layer}:{index}",
                object_type=object_type,
                shape=_ParsedShapeKind.LINE,
                roles=roles,
                data=PcbLine(x1, y1, x2, y2, width),
                layers=(layer,),
                net_number=net_number,
                pour_id=pour_id,
                metadata=_geometry_metadata(
                    native_type="TRACK",
                    source_collection=source_collection,
                    native_index=index,
                    native_component_index=component_index,
                    native_polygon_index=track.polygon,
                    native_subpolygon_index=track.subpoly_index,
                ),
            )
        )

    return geometry, keepouts


def _parse_vias(
    data: bytes, layer_map: dict[int, PcbLayer], ctx: ParseContext
) -> list[_ParsedPrimitive]:
    """Parse Vias6/Data into normalized via geometry."""
    records = _read_binary_records(data, ctx, source="Vias6/Data")
    vias: list[_ParsedPrimitive] = []

    for index, (rec_type, body) in enumerate(records):
        if rec_type != PcbRecordType.VIA:
            continue
        via = ViaRecord.from_bytes(body, ctx)
        if via is None:
            continue

        layer_refs = [
            _layer_ref(via.start_layer, layer_map, source=f"via {index} start"),
            _layer_ref(via.end_layer, layer_map, source=f"via {index} end"),
        ]
        layers: list[str] = []
        for layer_ref in layer_refs:
            if layer_ref.name not in layers:
                layers.append(layer_ref.name)

        roles = [_ParsedRole.CONDUCTOR]
        through_hole = (
            via.start_layer == AltiumLayer.TOP_LAYER and via.end_layer == AltiumLayer.BOTTOM_LAYER
        )
        if not through_hole:
            if via.start_layer == via.end_layer:
                roles.append(_ParsedRole.FREE_VIA)
            else:
                roles.append(_ParsedRole.BLIND_VIA)

        component_index = None if via.component == COMPONENT_NONE else via.component

        vias.append(
            _ParsedPrimitive(
                id=f"via:{index}",
                object_type=_ParsedObjectKind.VIA,
                shape=_ParsedShapeKind.CIRCLE,
                roles=tuple(roles),
                data=_ParsedViaPayload(
                    x=_int_to_mm(via.position[0]),
                    y=-_int_to_mm(via.position[1]),
                    size=_int_to_mm(via.diameter),
                    drill=_int_to_mm(via.hole_size),
                ),
                layers=tuple(layers),
                net_number=_net_number(via.net),
                metadata=_geometry_metadata(
                    native_type="VIA",
                    source_collection="vias",
                    native_index=index,
                    native_component_index=component_index,
                    properties={
                        "start_layer": str(via.start_layer),
                        "end_layer": str(via.end_layer),
                    },
                ),
            )
        )

    return vias


def _parse_arcs(
    data: bytes,
    layer_map: dict[int, PcbLayer],
    ctx: ParseContext,
    pour_id_map: dict[int, str] | None = None,
) -> tuple[list[_ParsedPrimitive], list[PcbKeepout]]:
    """Parse Arcs6/Data into normalized arc and keepout geometry."""
    records = _read_binary_records(data, ctx, source="Arcs6/Data")
    geometry: list[_ParsedPrimitive] = []
    keepouts: list[PcbKeepout] = []
    resolved_pour_id_map = pour_id_map or {}

    for index, (rec_type, body) in enumerate(records):
        if rec_type != PcbRecordType.ARC:
            continue
        arc = ArcRecord.from_bytes(body, ctx)
        if arc is None:
            continue

        layer_ref = _layer_ref(arc.layer, layer_map, source=f"arc {index}")
        layer = layer_ref.name

        cx = _int_to_mm(arc.center[0])
        cy_orig = _int_to_mm(arc.center[1])
        radius = _int_to_mm(arc.radius)
        width = _int_to_mm(arc.width)

        shape, payload = _arc_shape_payload(
            cx, cy_orig, radius, width, arc.start_angle, arc.end_angle
        )

        component_index = None if arc.component == COMPONENT_NONE else arc.component
        if arc.is_keepout:
            keepouts.append(
                _keepout_from_arc(
                    layer=layer_ref,
                    layer_num=arc.layer,
                    arc=arc,
                    cx=cx,
                    cy_orig=cy_orig,
                    radius=radius,
                    width=width,
                    index=index,
                    component_index=component_index,
                )
            )
            continue

        pour_id = _resolve_pour_id(resolved_pour_id_map, arc.polygon)
        if arc.layer in _COPPER_LAYERS:
            object_type = _ParsedObjectKind.TRACK
            roles = _layered_geometry_roles(
                arc.layer,
                layer_map,
                _ParsedRole.CONDUCTOR,
            )
            source_collection = "conductors"
            net_number = _net_number(arc.net)
        elif layer_map[arc.layer].has_role(LayerRole.EDGE):
            object_type = _ParsedObjectKind.GRAPHIC
            roles = _layered_geometry_roles(
                arc.layer,
                layer_map,
                _ParsedRole.BOARD_OUTLINE,
            )
            source_collection = "board_profile"
            net_number = 0
        else:
            object_type = _ParsedObjectKind.GRAPHIC
            roles = _layered_geometry_roles(arc.layer, layer_map)
            source_collection = "footprint_artwork" if component_index is not None else "artwork"
            net_number = 0

        geometry.append(
            _ParsedPrimitive(
                id=f"arc:{arc.layer}:{index}",
                object_type=object_type,
                shape=shape,
                roles=roles,
                data=payload,
                layers=(layer,),
                net_number=net_number,
                pour_id=pour_id,
                metadata=_geometry_metadata(
                    native_type="ARC",
                    source_collection=source_collection,
                    native_index=index,
                    native_component_index=component_index,
                    native_polygon_index=arc.polygon,
                    native_subpolygon_index=arc.subpoly_index,
                ),
            )
        )

    return geometry, keepouts


def _keepout_from_arc(
    *,
    layer: PcbLayer,
    layer_num: int,
    arc: ArcRecord,
    cx: float,
    cy_orig: float,
    radius: float,
    width: float,
    index: int,
    component_index: int | None,
) -> PcbKeepout:
    outer_radius = radius + width / 2.0
    inner_radius = max(radius - width / 2.0, 0.0)
    boundary = _arc_ring_points(
        cx=cx,
        cy_orig=cy_orig,
        radius=outer_radius,
        start_deg=arc.start_angle,
        end_deg=arc.end_angle,
    )
    holes: list[list[tuple[float, float]]] = []
    if _is_full_circle_arc(arc.start_angle, arc.end_angle) and inner_radius > 0:
        holes.append(
            list(
                reversed(
                    _arc_ring_points(
                        cx=cx,
                        cy_orig=cy_orig,
                        radius=inner_radius,
                        start_deg=arc.start_angle,
                        end_deg=arc.end_angle,
                    )
                )
            )
        )
    elif inner_radius > 0:
        inner = list(
            reversed(
                _arc_ring_points(
                    cx=cx,
                    cy_orig=cy_orig,
                    radius=inner_radius,
                    start_deg=arc.start_angle,
                    end_deg=arc.end_angle,
                )
            )
        )
        boundary = [*boundary, *inner]
    return PcbKeepout(
        id=f"keepout_arc:{layer_num}:{index}",
        boundary=PcbClosedPath.from_points(
            boundary,
            holes=tuple(PcbClosedPath.from_points(hole) for hole in holes),
        ),
        layers=(layer,),
        rules=_altium_keepout_rules(arc.keepout_restrictions),
        metadata=_keepout_metadata(
            native_type="ARC",
            native_kind="keepout",
            native_index=index,
            native_component_index=component_index,
            properties={"keepout_restrictions": str(arc.keepout_restrictions)},
        ),
    )


def _keepout_from_line(
    *,
    layer: PcbLayer,
    track: TrackRecord,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    width: float,
    index: int,
    component_index: int | None,
) -> PcbKeepout:
    return PcbKeepout(
        id=f"keepout_track:{track.layer}:{index}",
        boundary=PcbClosedPath.from_points(_line_rect_points(x1, y1, x2, y2, width)),
        layers=(layer,),
        rules=_altium_keepout_rules(track.keepout_restrictions),
        metadata=_keepout_metadata(
            native_type="TRACK",
            native_kind="keepout",
            native_index=index,
            native_component_index=component_index,
            properties={"keepout_restrictions": str(track.keepout_restrictions)},
        ),
    )


def _line_rect_points(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    width: float,
) -> list[tuple[float, float]]:
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    half_width = max(width, 0.01) / 2.0
    if length <= 0.0:
        return [
            (x1 - half_width, y1 - half_width),
            (x1 + half_width, y1 - half_width),
            (x1 + half_width, y1 + half_width),
            (x1 - half_width, y1 + half_width),
        ]
    nx = -dy / length * half_width
    ny = dx / length * half_width
    return [
        (x1 + nx, y1 + ny),
        (x2 + nx, y2 + ny),
        (x2 - nx, y2 - ny),
        (x1 - nx, y1 - ny),
    ]


def _arc_ring_points(
    *,
    cx: float,
    cy_orig: float,
    radius: float,
    start_deg: float,
    end_deg: float,
) -> list[tuple[float, float]]:
    sweep = _arc_sweep_degrees(start_deg, end_deg)
    segments = max(16, int(abs(sweep) / 360.0 * 96))
    points: list[tuple[float, float]] = []
    for index in range(segments):
        t = index / segments
        angle = math.radians(start_deg + sweep * t)
        points.append((cx + radius * math.cos(angle), -(cy_orig + radius * math.sin(angle))))
    return points


def _arc_sweep_degrees(start_deg: float, end_deg: float) -> float:
    sweep = end_deg - start_deg
    if _is_full_circle_arc(start_deg, end_deg):
        return 360.0 if sweep >= 0 else -360.0
    if sweep < 0:
        sweep += 360.0
    return sweep


def _is_full_circle_arc(start_deg: float, end_deg: float) -> bool:
    return abs(end_deg - start_deg) >= 359.999


def _altium_keepout_rules(mask: int) -> PcbKeepoutRules:
    if mask == 0:
        return PcbKeepoutRules(
            tracks=PcbKeepoutPermission.NOT_ALLOWED,
            vias=PcbKeepoutPermission.NOT_ALLOWED,
            pads=PcbKeepoutPermission.NOT_ALLOWED,
            copper_pours=PcbKeepoutPermission.NOT_ALLOWED,
            footprints=PcbKeepoutPermission.NOT_ALLOWED,
        )

    def restriction(bit: int) -> PcbKeepoutPermission:
        return PcbKeepoutPermission.NOT_ALLOWED if mask & bit else PcbKeepoutPermission.ALLOWED

    return PcbKeepoutRules(
        tracks=restriction(0x01),
        vias=restriction(0x02),
        pads=restriction(0x04),
        copper_pours=restriction(0x08),
        footprints=restriction(0x10),
    )


def _parse_pads(
    data: bytes, nets: dict[int, PcbNet], layer_map: dict[int, PcbLayer], ctx: ParseContext
) -> list[tuple[int, _ParsedPrimitive]]:
    """Parse Pads6/Data into component-indexed pad geometry.

    Each pad record has 6 subrecords: name, skip, skip, skip, geometry,
    per-layer-overrides. PadRecord.from_bytes handles the subrecord chain.
    """
    pads: list[tuple[int, _ParsedPrimitive]] = []
    pos = 0
    index = 0

    while pos < len(data):
        if data[pos] != 2:
            break
        # Find end of this pad record by parsing subrecord chain
        rec_data = data[pos:]
        pad = PadRecord.from_bytes(rec_data, ctx)

        # Advance past this record regardless of parse success.
        # Re-parse subrecord lengths to advance the position.
        pos += 1  # type byte
        for _ in range(4):  # sub1-sub4
            if pos + 4 > len(data):
                break
            sl = u32(data, pos)
            pos += 4 + sl
        for _ in range(2):  # sub5-sub6
            if pos + 4 > len(data):
                break
            sl = u32(data, pos)
            pos += 4 + sl

        if pad is None:
            continue

        # Determine shape string
        shape = _PAD_SHAPES.get(_pad_shape(pad.shape), "rect")
        if pad.shape_alt == PadShapeAlt.ROUNDRECT:
            shape = "roundrect"

        # Determine layers (multi-layer pad = layer 74 = through-hole)
        if pad.layer == AltiumLayer.MULTI_LAYER:
            layers = [
                layer.name for layer in layer_map.values() if layer.has_role(LayerRole.COPPER)
            ]
        else:
            layers = [_layer_ref(pad.layer, layer_map, source=f"pad {index}").name]

        net_num = _net_number(pad.net)
        net_obj = None if net_num == 0 else nets.get(net_num)
        if net_num != 0 and net_obj is None:
            msg = f"pad {index}: unknown Altium net index {pad.net}"
            raise AltiumPcbParseError(msg)
        net_name = net_obj.name if net_obj is not None else ""

        roles = [_ParsedRole.CONDUCTOR]
        if pad.layer not in _COPPER_LAYERS and pad.layer != AltiumLayer.MULTI_LAYER:
            roles.extend(_layer_geometry_roles(pad.layer, layer_map))
        if pad.hole_size > 0:
            roles.append(_ParsedRole.PLATED_HOLE)

        geometry_shape = (
            _ParsedShapeKind.CIRCLE if shape == "circle" else _ParsedShapeKind.RECTANGLE
        )
        if shape in {"oval", "roundrect"}:
            geometry_shape = _ParsedShapeKind.POLYGON

        pads.append(
            (
                pad.component,
                _ParsedPrimitive(
                    id=f"pad:{index}:{pad.name}",
                    object_type=_ParsedObjectKind.PAD,
                    shape=geometry_shape,
                    roles=tuple(roles),
                    data=_ParsedPadPayload(
                        number=pad.name,
                        x=_int_to_mm(pad.position[0]),
                        y=-_int_to_mm(pad.position[1]),
                        width=_int_to_mm(pad.top_size[0]),
                        height=_int_to_mm(pad.top_size[1]),
                        shape=shape,
                        rotation=pad.rotation,
                        drill=_int_to_mm(pad.hole_size),
                    ),
                    layers=tuple(layers),
                    net_number=net_num,
                    net_name=net_name,
                    metadata=_geometry_metadata(
                        native_type="PAD",
                        source_collection="pads",
                        native_index=index,
                        native_component_index=None
                        if pad.component == COMPONENT_NONE
                        else pad.component,
                        properties={
                            "pad_mode": str(pad.layer),
                            "shape_alt": "" if pad.shape_alt is None else str(pad.shape_alt),
                        },
                    ),
                ),
            )
        )
        index += 1

    return pads


def _apply_drill_manager_mask_apertures(
    raw_pads: list[tuple[int, _ParsedPrimitive]],
    drill_manager_data: bytes,
) -> None:
    """Attach validated Altium pad-template solder-mask apertures to pads.

    Altium pad/via templates can carry mask opening data. This parser only
    uses a narrow, validated template-name encoding when richer template data
    is not present in the file streams.
    """
    if not drill_manager_data:
        return
    for record in _parse_drill_manager_records(drill_manager_data):
        aperture = _pad_mask_aperture_from_drill_manager_record(record)
        if aperture is None:
            continue
        for primitive_index in record.primitive_indices:
            if primitive_index < 0 or primitive_index >= len(raw_pads):
                continue
            _component, pad_geometry = raw_pads[primitive_index]
            if not isinstance(pad_geometry.data, _ParsedPadPayload):
                continue
            pad = pad_geometry.data
            if not _pad_matches_template_aperture_source(pad, record.properties):
                continue
            pad.mask_aperture_width = aperture.width
            pad.mask_aperture_height = aperture.height
            pad.mask_aperture_source = aperture.source


def _parse_drill_manager_records(data: bytes) -> tuple[_DrillManagerRecord, ...]:
    records: list[_DrillManagerRecord] = []
    pos = 0
    while pos < len(data):
        header_size = _drill_manager_header_size(data, pos)
        if header_size == 0:
            break
        prop_len = u32(data, pos + header_size - 4)
        prop_start = pos + header_size
        prop_end = prop_start + prop_len
        if prop_end > len(data):
            break
        properties = parse_record_payload(data[prop_start:prop_end].rstrip(b"\0"))
        pos = prop_end
        if pos + 4 > len(data):
            break
        primitive_count = u32(data, pos)
        pos += 4
        refs_end = pos + primitive_count * 4
        if refs_end > len(data):
            break
        primitive_indices = tuple(u32(data, pos + index * 4) for index in range(primitive_count))
        pos = refs_end
        if properties:
            records.append(
                _DrillManagerRecord(
                    properties=properties,
                    primitive_indices=primitive_indices,
                )
            )
    return tuple(records)


def _drill_manager_header_size(data: bytes, pos: int) -> int:
    for header_size in (8, 12):
        if pos + header_size > len(data):
            continue
        prop_len = u32(data, pos + header_size - 4)
        prop_start = pos + header_size
        prop_end = prop_start + prop_len
        if prop_len <= 0 or prop_end > len(data):
            continue
        if data[prop_start : prop_start + 1] == b"|":
            return header_size
    return 0


def _pad_mask_aperture_from_drill_manager_record(
    record: _DrillManagerRecord,
) -> _PadMaskAperture | None:
    properties = record.properties
    if properties.get("objectid", "").lower() != "pad":
        return None
    template_name = properties.get("templatename", "")
    match = _PAD_TEMPLATE_MASK_RE.fullmatch(template_name)
    if match is None:
        return None
    mask_width = _template_hundredths_mm(match.group("mask_w"))
    mask_height = _template_hundredths_mm(match.group("mask_h"))
    if mask_width <= 0.0 or mask_height <= 0.0:
        return None
    return _PadMaskAperture(
        width=mask_width,
        height=mask_height,
        source=f"altium:drill-manager-template:{template_name}",
    )


def _pad_matches_template_aperture_source(
    pad: _ParsedPadPayload,
    properties: dict[str, str],
) -> bool:
    template_name = properties.get("templatename", "")
    match = _PAD_TEMPLATE_MASK_RE.fullmatch(template_name)
    if match is None:
        return False
    expected_width = _template_hundredths_mm(match.group("pad_w"))
    expected_height = _template_hundredths_mm(match.group("pad_h"))
    expected_drill = _template_hundredths_mm(match.group("drill"))
    expected_mask_width = _template_hundredths_mm(match.group("mask_w"))
    expected_mask_height = _template_hundredths_mm(match.group("mask_h"))
    return (
        _close_mm(pad.width, expected_width)
        and _close_mm(pad.height, expected_height)
        and _close_mm(pad.drill, expected_drill)
        and expected_mask_width >= max(pad.width, pad.drill)
        and expected_mask_height >= max(pad.height, pad.drill)
    )


def _template_hundredths_mm(raw: str) -> float:
    return int(raw) / 100.0


def _close_mm(value: float, expected: float) -> bool:
    return math.isclose(value, expected, abs_tol=0.03)


def _parse_texts(
    data: bytes, layer_map: dict[int, PcbLayer], ctx: ParseContext
) -> list[tuple[int, _ParsedPrimitive]]:
    """Parse Texts6/Data into component-indexed text geometry.

    Each text record has 2 subrecords: binary properties + Pascal string.
    TextRecord.from_bytes handles both subrecords.
    """
    texts: list[tuple[int, _ParsedPrimitive]] = []
    pos = 0
    index = 0

    while pos < len(data):
        if data[pos] != 5:
            break

        rec_data = data[pos:]
        text_rec = TextRecord.from_bytes(rec_data, ctx)

        # Advance past this record by re-parsing subrecord lengths
        pos += 1  # type byte
        for _ in range(2):  # sub1, sub2
            if pos + 4 > len(data):
                break
            sl = u32(data, pos)
            pos += 4 + sl

        if text_rec is None:
            continue

        layer = _layer_ref(text_rec.layer, layer_map, source=f"text {index}").name

        roles = list(_layer_geometry_roles(text_rec.layer, layer_map))
        roles.append(_ParsedRole.TEXT)
        if text_rec.is_designator:
            roles.append(_ParsedRole.DESIGNATOR)
        elif text_rec.is_comment:
            roles.append(_ParsedRole.VALUE)
        else:
            roles.append(_ParsedRole.USER_TEXT)

        component_index = None if text_rec.component == COMPONENT_NONE else text_rec.component

        texts.append(
            (
                text_rec.component,
                _ParsedPrimitive(
                    id=f"text:{index}",
                    object_type=_ParsedObjectKind.TEXT,
                    shape=_ParsedShapeKind.TEXT,
                    roles=tuple(roles),
                    data=PcbText(
                        text=text_rec.text,
                        x=_int_to_mm(text_rec.position[0]),
                        y=-_int_to_mm(text_rec.position[1]),
                        rotation=text_rec.rotation,
                        font_size=_int_to_mm(text_rec.height),
                    ),
                    layers=(layer,),
                    metadata=_geometry_metadata(
                        native_type="TEXT",
                        source_collection="artwork"
                        if component_index is None
                        else "footprint_artwork",
                        native_index=index,
                        native_component_index=component_index,
                    ),
                ),
            )
        )
        index += 1

    return texts


def _parse_fills(
    data: bytes, layer_map: dict[int, PcbLayer], ctx: ParseContext
) -> tuple[list[_ParsedPrimitive], list[PcbKeepout]]:
    """Parse Fills6/Data into rectangular source-layer geometry."""
    records = _read_binary_records(data, ctx, source="Fills6/Data")
    fills: list[_ParsedPrimitive] = []
    keepouts: list[PcbKeepout] = []

    for index, (rec_type, body) in enumerate(records):
        if rec_type != PcbRecordType.FILL:
            continue
        fill = FillRecord.from_bytes(body, ctx)
        if fill is None:
            continue
        layer_ref = _layer_ref(fill.layer, layer_map, source=f"fill {index}")
        layer = layer_ref.name

        x1 = _int_to_mm(fill.pos1[0])
        y1 = -_int_to_mm(fill.pos1[1])
        x2 = _int_to_mm(fill.pos2[0])
        y2 = -_int_to_mm(fill.pos2[1])

        # Build 4-corner rectangle, apply rotation around center
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        hw, hh = (x2 - x1) / 2, (y2 - y1) / 2
        corners: list[tuple[float, float]] = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]

        if fill.rotation != 0:
            rad = math.radians(fill.rotation)
            cos_r, sin_r = math.cos(rad), math.sin(rad)
            corners = [(dx * cos_r - dy * sin_r, dx * sin_r + dy * cos_r) for dx, dy in corners]

        points = [(cx + dx, cy + dy) for dx, dy in corners]
        if fill.is_keepout:
            keepouts.append(
                PcbKeepout(
                    id=f"keepout_fill:{fill.layer}:{index}",
                    boundary=PcbClosedPath.from_points(points),
                    layers=(layer_ref,),
                    rules=_altium_keepout_rules(fill.keepout_restrictions),
                    metadata=_keepout_metadata(
                        native_type="FILL",
                        native_kind="keepout",
                        native_index=index,
                        properties={"keepout_restrictions": str(fill.keepout_restrictions)},
                    ),
                )
            )
            continue

        roles = list(_layer_geometry_roles(fill.layer, layer_map))
        if fill.layer in _COPPER_LAYERS:
            roles.append(_ParsedRole.CONDUCTOR)
            object_type = _ParsedObjectKind.REGION
            source_collection = "conductors"
        else:
            object_type = _ParsedObjectKind.GRAPHIC
            source_collection = "artwork"

        component_index = None if fill.component == COMPONENT_NONE else fill.component

        fills.append(
            _ParsedPrimitive(
                id=f"fill:{fill.layer}:{index}",
                object_type=object_type,
                shape=_ParsedShapeKind.POLYGON,
                roles=_normalize_parsed_roles(*roles),
                data=PcbPolygon(points=points),
                layers=(layer,),
                net_number=_net_number(fill.net) if fill.layer in _COPPER_LAYERS else 0,
                metadata=_geometry_metadata(
                    native_type="FILL",
                    source_collection=source_collection,
                    native_index=index,
                    native_component_index=component_index,
                ),
            )
        )

    return fills, keepouts


def _parse_polygon_pours(
    data: bytes,
    nets: dict[int, PcbNet],
    layer_map: dict[int, PcbLayer],
) -> tuple[list[PcbPour], dict[int, str], dict[int, int]]:
    """Parse Polygons6/Data → copper-pour intent and lookup maps.

    Returns (pours, pour_id_map, pour_net_map). The maps let concrete fill
    geometry inherit net and parent-pour identity without rendering the source
    boundary as copper.
    """
    records = read_text_records(data)
    pours: list[PcbPour] = []
    pour_id_map: dict[int, str] = {}
    pour_net_map: dict[int, int] = {}

    for index, rec in enumerate(records):
        pourindex = int(rec.get("pourindex", "-1") or "-1")

        # Resolve net: text records store 0-based Nets6 index,
        # apply _net_number() to convert to 1-based pcb.nets key
        net_raw = int(rec.get("net", str(NET_UNCONNECTED)) or str(NET_UNCONNECTED))
        net_num = _net_number(net_raw)
        net = None if net_num == 0 else nets.get(net_num)
        if net_num != 0 and net is None:
            msg = f"polygon pour {index}: unknown Altium net index {net_raw}"
            raise AltiumPcbParseError(msg)

        # Resolve layer from V7 layer name
        layer_id = rec.get("layer", "").upper()
        layer_num = _V7_NAME_TO_NUM.get(layer_id)
        if layer_num is None:
            continue
        layer = _layer_ref(layer_num, layer_map, source=f"polygon pour {index}")

        # Extract boundary vertices (vx0..vxN, vy0..vyN in mils)
        boundary: list[tuple[float, float]] = []
        i = 0
        while True:
            vx_key = f"vx{i}"
            vy_key = f"vy{i}"
            if vx_key not in rec or vy_key not in rec:
                break
            x_mm = _parse_mil(rec[vx_key])
            y_mm = -_parse_mil(rec[vy_key])  # Altium Y is inverted
            boundary.append((x_mm, y_mm))
            i += 1

        if len(boundary) < 3:
            continue

        # Fill type from hatchstyle
        hatchstyle = rec.get("hatchstyle", "")
        fill_mode = _altium_pour_fill_mode(hatchstyle)

        # Track width (min thickness within pour)
        trackwidth_str = rec.get("trackwidth", "")
        track_width = _parse_mil(trackwidth_str) if trackwidth_str else 0.0
        grid_str = rec.get("gridsize", "")
        grid = _parse_mil(grid_str) if grid_str else 0.0

        pour_id = f"polygon_pour:{pourindex}:{index}"
        if pourindex >= 0:
            pour_id_map[pourindex] = pour_id
            pour_net_map[pourindex] = net_num

        pours.append(
            PcbPour(
                id=pour_id,
                boundary=PcbClosedPath.from_points(boundary),
                layers=(layer,),
                net=net,
                priority=pourindex,
                settings=PcbPourSettings(
                    fill_mode=fill_mode,
                    hatch_style=hatchstyle,
                    grid_mm=grid,
                    track_width_mm=track_width,
                    min_thickness_mm=track_width,
                ),
                metadata=_pour_metadata(
                    native_type="POLYGON",
                    native_index=index,
                    native_pour_index=pourindex,
                    properties=rec,
                ),
            )
        )

    return pours, pour_id_map, pour_net_map


def _altium_pour_fill_mode(hatchstyle: str) -> PcbPourFillMode:
    normalized = hatchstyle.strip().lower()
    if not normalized:
        return PcbPourFillMode.UNKNOWN
    if normalized == "solid":
        return PcbPourFillMode.SOLID
    if normalized in {"none", "no", "unfilled"}:
        return PcbPourFillMode.NONE
    return PcbPourFillMode.HATCH


def _parse_regions(
    data: bytes,
    nets: dict[int, PcbNet],
    layer_map: dict[int, PcbLayer],
    ctx: ParseContext,
    pour_id_map: dict[int, str] | None = None,
    pour_net_map: dict[int, int] | None = None,
) -> list[_ParsedPrimitive]:
    """Parse Regions6/Data into polygon geometry.

    Region records contain a property string followed by vertex data
    (pairs of float64 in Altium internal units).  All layers are included —
    copper regions carry net info, non-copper regions (silkscreen fills,
    paste openings, etc.) have net_number 0.

    When pour_net_map is provided, regions with net=0xFFFF (inherit) and
    a valid subpolyindex will inherit the net from their parent polygon pour.
    """
    records = _read_binary_records(data, ctx, source="Regions6/Data")
    polygons: list[_ParsedPrimitive] = []

    for index, (rec_type, body) in enumerate(records):
        if rec_type != PcbRecordType.REGION:
            continue
        region = RegionRecord.from_bytes(body, ctx)
        if region is None:
            continue

        # Determine layer from V7 property or fallback to byte
        v7_layer = region.properties.get("v7_layer", "").upper()
        resolved_num = (
            _V7_NAME_TO_NUM[v7_layer] if v7_layer and v7_layer in _V7_NAME_TO_NUM else region.layer
        )

        layer = _layer_ref(resolved_num, layer_map, source=f"region {index}").name
        region_kind = _region_kind(region.properties, ctx)

        points = [(_int_to_mm(int(vx)), -_int_to_mm(int(vy))) for vx, vy in region.vertices]
        if len(points) < 3:
            continue

        # Convert hole vertices
        holes: list[list[tuple[float, float]]] = []
        for hole_verts in region.holes:
            h_pts = [(_int_to_mm(int(vx)), -_int_to_mm(int(vy))) for vx, vy in hole_verts]
            if len(h_pts) >= 3:
                holes.append(h_pts)

        polygon_index = int(region.properties.get("polygonindex", "-1") or "-1")
        subpolygon_index = int(region.properties.get("subpolyindex", "-1") or "-1")
        pour_id = _resolve_pour_id(pour_id_map or {}, polygon_index, subpolygon_index)

        # Net resolution: use direct net if assigned, otherwise inherit from pour
        if resolved_num in _COPPER_LAYERS:
            if region.net == NET_UNCONNECTED and pour_net_map:
                # Inherit from parent polygon pour via subpolyindex
                net_num = _resolve_pour_net(pour_net_map, polygon_index, subpolygon_index)
            else:
                net_num = _net_number(region.net)
        else:
            net_num = 0

        net_obj = nets.get(net_num)
        net_name = net_obj.name if net_obj else ""

        roles = list(_layer_geometry_roles(resolved_num, layer_map))
        if region_kind == RegionKind.POLYGON_CUTOUT:
            roles.append(_ParsedRole.POLYGON_CUTOUT)
        elif resolved_num in _COPPER_LAYERS:
            roles.append(_ParsedRole.CONDUCTOR)

        component_index = None if region.component == COMPONENT_NONE else region.component

        polygons.append(
            _ParsedPrimitive(
                id=f"region:{resolved_num}:{index}",
                object_type=_ParsedObjectKind.REGION,
                shape=_ParsedShapeKind.POLYGON,
                roles=tuple(roles),
                data=PcbPolygon(points=points, holes=holes),
                layers=(layer,),
                net_number=net_num,
                net_name=net_name,
                pour_id=pour_id,
                metadata=_geometry_metadata(
                    native_type="REGION",
                    native_kind="" if region_kind is None else str(region_kind),
                    source_collection="conductors" if _ParsedRole.CONDUCTOR in roles else "artwork",
                    native_index=index,
                    native_component_index=component_index,
                    native_polygon_index=polygon_index,
                    native_subpolygon_index=subpolygon_index,
                    properties=region.properties,
                ),
            )
        )

    return polygons


def _parse_shape_based_regions(
    data: bytes,
    nets: dict[int, PcbNet],
    layer_map: dict[int, PcbLayer],
    ctx: ParseContext,
    pour_id_map: dict[int, str] | None = None,
    pour_net_map: dict[int, int] | None = None,
) -> list[_ParsedPrimitive]:
    """Parse ShapeBasedRegions6/Data into polygon geometry.

    Uses the extended vertex format (37 bytes per vertex with arc support).

    Net inheritance matches ``_parse_regions``: a copper region carrying the
    unconnected sentinel (net == 0xFFFF) inherits the net of its parent polygon
    pour via the (sub)polygon index.
    """
    records = _read_binary_records(data, ctx, source="ShapeBasedRegions6/Data")
    polygons: list[_ParsedPrimitive] = []

    for index, (rec_type, body) in enumerate(records):
        if rec_type != PcbRecordType.REGION:
            continue
        region = ShapeBasedRegionRecord.from_bytes(body, ctx)
        if region is None:
            continue

        # Determine layer from V7 property or fallback to byte
        v7_layer = region.properties.get("v7_layer", "").upper()
        resolved_num = (
            _V7_NAME_TO_NUM[v7_layer] if v7_layer and v7_layer in _V7_NAME_TO_NUM else region.layer
        )

        layer = _layer_ref(resolved_num, layer_map, source=f"shape region {index}").name
        region_kind = _region_kind(region.properties, ctx)

        # Linearize arc edges, then convert to mm with Y negated
        raw_pts = linearize_arc_vertices(region.vertices)
        points: list[tuple[float, float]] = [(_int_to_mm(x), -_int_to_mm(y)) for x, y in raw_pts]
        if len(points) < 3:
            continue

        # Convert hole vertices (stored as f64 in internal units)
        holes: list[list[tuple[float, float]]] = []
        for hole_verts in region.holes:
            h_pts = [(_int_to_mm(int(vx)), -_int_to_mm(int(vy))) for vx, vy in hole_verts]
            if len(h_pts) >= 3:
                holes.append(h_pts)

        polygon_index = int(region.properties.get("polygonindex", "-1") or "-1")
        subpolygon_index = int(region.properties.get("subpolyindex", "-1") or "-1")
        pour_id = _resolve_pour_id(pour_id_map or {}, polygon_index, subpolygon_index)

        # Net resolution: use direct net if assigned, otherwise inherit from pour
        if resolved_num in _COPPER_LAYERS:
            if region.net == NET_UNCONNECTED and pour_net_map:
                net_num = _resolve_pour_net(pour_net_map, polygon_index, subpolygon_index)
            else:
                net_num = _net_number(region.net)
        else:
            net_num = 0
        net_obj = nets.get(net_num)
        net_name = net_obj.name if net_obj else ""

        roles = list(_layer_geometry_roles(resolved_num, layer_map))
        if region_kind == RegionKind.POLYGON_CUTOUT:
            roles.append(_ParsedRole.POLYGON_CUTOUT)
        elif resolved_num in _COPPER_LAYERS:
            roles.append(_ParsedRole.CONDUCTOR)

        component_index = None if region.component == COMPONENT_NONE else region.component

        polygons.append(
            _ParsedPrimitive(
                id=f"shape_region:{resolved_num}:{index}",
                object_type=_ParsedObjectKind.REGION,
                shape=_ParsedShapeKind.POLYGON,
                roles=tuple(roles),
                data=PcbPolygon(points=points, holes=holes),
                layers=(layer,),
                net_number=net_num,
                net_name=net_name,
                pour_id=pour_id,
                metadata=_geometry_metadata(
                    native_type="SHAPE_BASED_REGION",
                    native_kind="" if region_kind is None else str(region_kind),
                    source_collection="conductors" if _ParsedRole.CONDUCTOR in roles else "artwork",
                    native_index=index,
                    native_component_index=component_index,
                    native_polygon_index=polygon_index,
                    native_subpolygon_index=subpolygon_index,
                    properties=region.properties,
                ),
            )
        )

    return polygons


def _dedupe_shape_based_board_polygons(
    regions: list[_ParsedPrimitive],
    shape_based_regions: list[_ParsedPrimitive],
) -> list[_ParsedPrimitive]:
    """Drop ShapeBasedRegions6 board polygons already represented by Regions6."""
    if not regions:
        return shape_based_regions
    region_keys = {
        key for polygon in regions for key in (_polygon_duplicate_key(polygon),) if key is not None
    }
    return [
        polygon
        for polygon in shape_based_regions
        if _polygon_duplicate_key(polygon) not in region_keys
    ]


type _PolygonDuplicateKey = tuple[str, int, tuple[tuple[float, float], ...]]


def _polygon_duplicate_key(poly: _ParsedPrimitive) -> _PolygonDuplicateKey | None:
    # Key on layer + vertex count + the rounded vertices themselves. A bbox-only
    # key dropped distinct polygons that merely share a bounding box (e.g. a
    # board frame and an inscribed shape). The Regions6 and ShapeBasedRegions6
    # representations of the same primitive differ only by an explicit closing
    # vertex, so normalize that away before keying.
    if not isinstance(poly.data, PcbPolygon) or len(poly.data.points) < 3:
        return None
    vertices = [(round(x, 3), round(y, 3)) for x, y in poly.data.points]
    if len(vertices) > 1 and vertices[0] == vertices[-1]:
        vertices.pop()
    return (poly.primary_layer, len(vertices), tuple(vertices))


def _region_kind(properties: dict[str, str], ctx: ParseContext | None = None) -> int | None:
    raw_kind = properties.get("kind")
    if raw_kind is None:
        return None
    try:
        return int(raw_kind)
    except ValueError:
        if ctx is not None:
            ctx.warn(
                "malformed_region_kind",
                f"non-integer region kind {raw_kind!r}; treated as default region",
            )
        return None


def _parse_board_outline(
    tracks_data: bytes,
    arcs_data: bytes,
    layer_map: dict[int, PcbLayer],
    ctx: ParseContext,
) -> list[_ParsedPrimitive]:
    """Extract board outline geometry from fallback mechanical/keepout layers.

    Falls back to Keep-Out layer (74) if no Mechanical 1 primitives found.
    Also checks for any mechanical layer whose MECHKIND is EDGE.
    """
    outline: list[_ParsedPrimitive] = []

    # Prefer a layer with EDGE function (from MECHKIND=BoardShape), then
    # fall back to Mechanical 1 (57), then Keep-Out (74).
    edge_layers = [
        num
        for num, lyr in layer_map.items()
        if lyr.has_role(LayerRole.EDGE) and num >= AltiumLayer.MECHANICAL_1
    ]
    candidates = edge_layers or [int(AltiumLayer.MECHANICAL_1)]
    candidates.append(int(AltiumLayer.MULTI_LAYER))
    # Deduplicate while preserving order
    seen: set[int] = set()
    target_layers: list[int] = []
    for n in candidates:
        if n not in seen:
            seen.add(n)
            target_layers.append(n)

    for target_layer in target_layers:
        if outline:
            break

        edge_name = _layer_name(target_layer, layer_map)
        if not edge_name:
            continue

        for index, (rec_type, body) in enumerate(
            _read_binary_records(tracks_data, ctx, source="Tracks6/Data (board outline)")
        ):
            if rec_type != PcbRecordType.TRACK:
                continue
            track = TrackRecord.from_bytes(body, ctx)
            if track is None or track.layer != target_layer:
                continue
            if track.component != COMPONENT_NONE:
                continue

            outline.append(
                _ParsedPrimitive(
                    id=f"outline_track:{target_layer}:{index}",
                    object_type=_ParsedObjectKind.GRAPHIC,
                    shape=_ParsedShapeKind.LINE,
                    roles=_layered_geometry_roles(
                        target_layer,
                        layer_map,
                        _ParsedRole.BOARD_OUTLINE,
                    ),
                    data=PcbLine(
                        start_x=_int_to_mm(track.start[0]),
                        start_y=-_int_to_mm(track.start[1]),
                        end_x=_int_to_mm(track.end[0]),
                        end_y=-_int_to_mm(track.end[1]),
                        width=_int_to_mm(track.width),
                    ),
                    layers=(edge_name,),
                    metadata=_geometry_metadata(
                        native_type="TRACK",
                        native_kind="board_outline",
                        source_collection="board_profile",
                        native_index=index,
                    ),
                )
            )

        for index, (rec_type, body) in enumerate(
            _read_binary_records(arcs_data, ctx, source="Arcs6/Data (board outline)")
        ):
            if rec_type != PcbRecordType.ARC:
                continue
            arc = ArcRecord.from_bytes(body, ctx)
            if arc is None or arc.layer != target_layer:
                continue
            if arc.component != COMPONENT_NONE:
                continue

            cx = _int_to_mm(arc.center[0])
            cy_orig = _int_to_mm(arc.center[1])
            radius = _int_to_mm(arc.radius)
            width = _int_to_mm(arc.width)

            shape, payload = _arc_shape_payload(
                cx, cy_orig, radius, width, arc.start_angle, arc.end_angle
            )
            outline.append(
                _ParsedPrimitive(
                    id=f"outline_arc:{target_layer}:{index}",
                    object_type=_ParsedObjectKind.GRAPHIC,
                    shape=shape,
                    roles=_layered_geometry_roles(
                        target_layer,
                        layer_map,
                        _ParsedRole.BOARD_OUTLINE,
                    ),
                    data=payload,
                    layers=(edge_name,),
                    metadata=_geometry_metadata(
                        native_type="ARC",
                        native_kind="board_outline",
                        source_collection="board_profile",
                        native_index=index,
                    ),
                )
            )

    return outline


def _parse_component_bodies(data: bytes) -> dict[int, list[_ParsedPrimitive]]:
    """Parse ComponentBodies6/Data into component-indexed model geometry.

    Text records with pipe-delimited properties. Key properties:
    - ``MODELID``: OLE stream ID for the embedded STEP data
    - ``COMPONENT``: component index (int, 65535 = board-level body)
    - ``MODEL.2D.X``, ``MODEL.2D.Y``: 2D position in mil
    - ``MODEL.3D.ROTX/Y/Z``: rotation in degrees
    - ``MODEL.3D.DZ``: Z offset in mil
    """
    records = read_text_records(data)
    result: dict[int, list[_ParsedPrimitive]] = {}

    for index, rec in enumerate(records):
        model_id = rec.get("modelid", "")
        if not model_id:
            continue

        comp_str = rec.get("component", "")
        if not comp_str:
            continue
        comp_idx = int(comp_str)
        if comp_idx == COMPONENT_NONE:
            continue

        # 2D position (mil → mm)
        x_str = rec.get("model.2d.x", "0mil")
        y_str = rec.get("model.2d.y", "0mil")
        offset_x = _parse_mil(x_str)
        offset_y = -_parse_mil(y_str)

        # Z offset (mil → mm)
        dz_str = rec.get("model.3d.dz", "0mil")
        offset_z = _parse_mil(dz_str)

        # Rotation (degrees, may be scientific notation)
        rot_x = float(rec.get("model.3d.rotx", "0"))
        rot_y = float(rec.get("model.3d.roty", "0"))
        rot_z = float(rec.get("model.3d.rotz", "0"))

        model = _ParsedPrimitive(
            id=f"component_body:{comp_idx}:{index}",
            object_type=_ParsedObjectKind.MODEL_3D,
            shape=_ParsedShapeKind.MODEL,
            roles=(_ParsedRole.COMPONENT_BODY,),
            data=PcbModel3D(
                source=model_id,
                offset=(offset_x, offset_y, offset_z),
                rotation=(rot_x, rot_y, rot_z),
            ),
            metadata=_geometry_metadata(
                native_type="COMPONENT_BODY",
                source_collection="footprint_artwork",
                native_index=index,
                native_component_index=comp_idx,
                properties=rec,
            ),
        )
        result.setdefault(comp_idx, []).append(model)

    return result


def _compute_bbox(
    pads: list[_ParsedPrimitive],
) -> tuple[float, float, float, float] | None:
    """Compute footprint bounding box from pads with 0.5mm margin."""
    pad_payloads = [pad.data for pad in pads if isinstance(pad.data, _ParsedPadPayload)]
    if not pad_payloads:
        return None
    xs = [p.x - p.width / 2 for p in pad_payloads] + [p.x + p.width / 2 for p in pad_payloads]
    ys = [p.y - p.height / 2 for p in pad_payloads] + [p.y + p.height / 2 for p in pad_payloads]
    margin = 0.5
    return (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)


def _build_pcb_from_parsed_primitives(
    *,
    name: str,
    layer_map: dict[int, PcbLayer],
    nets: dict[int, PcbNet],
    footprints: list[PcbFootprint],
    pours: list[PcbPour],
    keepouts: list[PcbKeepout],
    primitives: list[_ParsedPrimitive],
    ctx: ParseContext,
) -> Pcb:
    metadata = PcbMetadata(source_format="altium")
    if ctx.issues:
        metadata.properties["parse_issue_count"] = str(len(ctx.issues))
    builder = PcbBuilder(name, metadata=metadata)
    for layer in layer_map.values():
        builder.add_layer(layer, source="Board6/Data")
    for net in nets.values():
        builder.add_net(net, source="Nets6/Data")
    for footprint in footprints:
        builder.add_footprint(footprint, source=f"component {footprint.reference}")
    for pour in pours:
        builder.add_pour_object(pour, source=f"pour {pour.id}")

    board_profile_elements: list[PcbBoardProfileElement] = []
    pour_fills: dict[str, list[PcbConductor]] = {}
    for primitive in primitives:
        _add_parsed_primitive(
            builder,
            primitive,
            footprints=footprints,
            pours=pours,
            board_profile_elements=board_profile_elements,
            pour_fills=pour_fills,
        )
    for keepout in keepouts:
        builder.add_keepout_object(keepout, source=keepout.id)
    for pour in pours:
        pour.fills = tuple(pour_fills.get(pour.id, ()))
    builder.set_board_profile(
        PcbBoardProfile(elements=tuple(board_profile_elements)),
        source="board profile",
    )
    return builder.build(require_board_profile=True)


def _add_parsed_primitive(
    builder: PcbBuilder,
    primitive: _ParsedPrimitive,
    *,
    footprints: list[PcbFootprint],
    pours: list[PcbPour],
    board_profile_elements: list[PcbBoardProfileElement],
    pour_fills: dict[str, list[PcbConductor]],
) -> None:
    if primitive.has_role(_ParsedRole.BOARD_OUTLINE):
        element = _board_profile_element(builder, primitive)
        if element is not None:
            board_profile_elements.append(element)
        return
    if primitive.object_type == _ParsedObjectKind.PAD and isinstance(
        primitive.data, _ParsedPadPayload
    ):
        _add_parsed_pad(builder, primitive, primitive.data, footprints)
        return
    if primitive.object_type == _ParsedObjectKind.VIA and isinstance(
        primitive.data, _ParsedViaPayload
    ):
        _add_parsed_via(builder, primitive, primitive.data)
        return
    if _is_conductor_primitive(primitive):
        conductor = _parsed_conductor(builder, primitive, footprints, pours)
        if conductor is not None:
            builder.add_conductor_object(conductor, source=primitive.id)
            if conductor.pour is not None:
                pour_fills.setdefault(conductor.pour.id, []).append(conductor)
        return
    artwork = _parsed_artwork(builder, primitive, footprints)
    if artwork is not None:
        builder.add_artwork_object(artwork, source=primitive.id)


def _add_parsed_pad(
    builder: PcbBuilder,
    primitive: _ParsedPrimitive,
    pad: _ParsedPadPayload,
    footprints: list[PcbFootprint],
) -> None:
    layers = _parsed_layer_refs(builder, primitive.layers, source=primitive.id)
    drill = None
    if pad.drill > 0:
        drill = builder.add_drill_object(
            PcbDrill(
                id=f"drill:{primitive.id}",
                x=pad.x,
                y=pad.y,
                diameter=pad.drill,
                shape=PcbDrillShape.ROUND,
                plating=(
                    PcbDrillPlating.PLATED
                    if primitive.has_role(_ParsedRole.PLATED_HOLE)
                    else PcbDrillPlating.UNKNOWN
                ),
                rotation=pad.rotation,
                layers=layers,
                metadata=primitive.metadata,
            ),
            source=primitive.id,
        )
    mask_aperture = None
    if pad.mask_aperture_width is not None or pad.mask_aperture_height is not None:
        mask_aperture = PcbMaskAperture(
            aperture_width=pad.mask_aperture_width,
            aperture_height=pad.mask_aperture_height,
            source=pad.mask_aperture_source,
        )
    builder.add_pad_object(
        PcbPad(
            id=primitive.id,
            number=pad.number,
            x=pad.x,
            y=pad.y,
            width=pad.width,
            height=pad.height,
            shape=pad.shape,
            pad_type=PcbPadType.THROUGH_HOLE if drill is not None else PcbPadType.SMD,
            layers=layers,
            net=_net_from_parsed_number(builder, primitive.net_number, primitive.id),
            footprint=_footprint_for_primitive(primitive, footprints),
            drill=drill,
            rotation=pad.rotation,
            mask_aperture=mask_aperture,
            metadata=primitive.metadata,
        ),
        source=primitive.id,
    )


def _add_parsed_via(
    builder: PcbBuilder,
    primitive: _ParsedPrimitive,
    via: _ParsedViaPayload,
) -> None:
    layers = _parsed_layer_refs(builder, primitive.layers, source=primitive.id)
    drill = builder.add_drill_object(
        PcbDrill(
            id=f"drill:{primitive.id}",
            x=via.x,
            y=via.y,
            diameter=via.drill,
            shape=PcbDrillShape.ROUND,
            plating=PcbDrillPlating.PLATED,
            layers=layers,
            metadata=primitive.metadata,
        ),
        source=primitive.id,
    )
    builder.add_via_object(
        PcbVia(
            id=primitive.id,
            x=via.x,
            y=via.y,
            diameter=via.size,
            layers=layers,
            drill=drill,
            net=_net_from_parsed_number(builder, primitive.net_number, primitive.id),
            via_type=_parsed_via_type(primitive),
            metadata=primitive.metadata,
        ),
        source=primitive.id,
    )


def _is_conductor_primitive(primitive: _ParsedPrimitive) -> bool:
    return (
        primitive.object_type in {_ParsedObjectKind.TRACK, _ParsedObjectKind.REGION}
        and primitive.has_role(_ParsedRole.CONDUCTOR)
        and not primitive.has_role(_ParsedRole.POLYGON_CUTOUT)
    )


def _parsed_conductor(
    builder: PcbBuilder,
    primitive: _ParsedPrimitive,
    footprints: list[PcbFootprint],
    pours: list[PcbPour],
) -> PcbConductor | None:
    if not isinstance(primitive.data, PcbLine | PcbArc | PcbCircle | PcbPolygon):
        return None
    layer = _primary_layer_ref(builder, primitive, source=primitive.id)
    pour = _pour_for_primitive(primitive, pours)
    if pour is not None:
        kind = PcbConductorKind.POUR_FILL
    elif isinstance(primitive.data, PcbArc):
        kind = PcbConductorKind.TRACE_ARC
    elif isinstance(primitive.data, PcbLine):
        kind = PcbConductorKind.TRACE
    else:
        kind = PcbConductorKind.COPPER_REGION
    return PcbConductor(
        id=primitive.id,
        kind=kind,
        layer=layer,
        data=primitive.data,
        net=_net_from_parsed_number(builder, primitive.net_number, primitive.id),
        footprint=_footprint_for_primitive(primitive, footprints),
        pour=pour,
        metadata=primitive.metadata,
    )


def _parsed_artwork(
    builder: PcbBuilder,
    primitive: _ParsedPrimitive,
    footprints: list[PcbFootprint],
) -> PcbArtwork | None:
    if not isinstance(
        primitive.data,
        PcbLine | PcbArc | PcbCircle | PcbPolygon | PcbText | PcbModel3D,
    ):
        return None
    layer = (
        None
        if not primitive.layers
        else _primary_layer_ref(builder, primitive, source=primitive.id)
    )
    footprint = _footprint_for_primitive(primitive, footprints)
    metadata = _artwork_metadata_for_visibility(primitive, footprint)
    return PcbArtwork(
        id=primitive.id,
        kind=_artwork_kind(primitive),
        purpose=_artwork_purpose(primitive, layer),
        layer=layer,
        data=primitive.data,
        footprint=footprint,
        metadata=metadata,
    )


def _board_profile_element(
    builder: PcbBuilder,
    primitive: _ParsedPrimitive,
) -> PcbBoardProfileElement | None:
    if not isinstance(primitive.data, PcbLine | PcbArc | PcbCircle | PcbPolygon):
        return None
    return PcbBoardProfileElement(
        id=primitive.id,
        kind=_artwork_kind(primitive),
        layer=_primary_layer_ref(builder, primitive, source=primitive.id),
        data=primitive.data,
        metadata=primitive.metadata,
    )


def _parsed_layer_refs(
    builder: PcbBuilder,
    layer_names: tuple[str, ...],
    *,
    source: str,
) -> tuple[PcbLayer, ...]:
    layers: list[PcbLayer] = []
    for layer_name in layer_names:
        if layer_name == "*.Cu":
            selected = tuple(layer for layer in builder.layers if layer.has_role(LayerRole.COPPER))
        else:
            selected = (builder.resolve_layer(layer_name, source=source),)
        for layer in selected:
            if layer not in layers:
                layers.append(layer)
    return tuple(layers)


def _primary_layer_ref(
    builder: PcbBuilder, primitive: _ParsedPrimitive, *, source: str
) -> PcbLayer:
    layers = _parsed_layer_refs(builder, primitive.layers, source=source)
    if not layers:
        msg = f"{source}: primitive has no layer"
        raise AltiumPcbParseError(msg)
    return layers[0]


def _net_from_parsed_number(
    builder: PcbBuilder,
    net_number: int,
    source: str,
) -> PcbNet | None:
    return None if net_number == 0 else builder.resolve_net_number(net_number, source=source)


def _footprint_for_primitive(
    primitive: _ParsedPrimitive,
    footprints: list[PcbFootprint],
) -> PcbFootprint | None:
    component_index = primitive.metadata.native_component_index
    if component_index is not None and 0 <= component_index < len(footprints):
        return footprints[component_index]
    if not primitive.footprint_ref:
        return None
    for footprint in footprints:
        if footprint.reference == primitive.footprint_ref:
            return footprint
    return None


def _artwork_metadata_for_visibility(
    primitive: _ParsedPrimitive,
    footprint: PcbFootprint | None,
) -> PcbObjectMetadata:
    if primitive.metadata.hidden or footprint is None:
        return primitive.metadata
    if primitive.has_role(_ParsedRole.DESIGNATOR) and not _altium_component_text_visible(
        footprint, "nameon", default=True
    ):
        return replace(primitive.metadata, hidden=True)
    if primitive.has_role(_ParsedRole.VALUE) and not _altium_component_text_visible(
        footprint, "commenton", default=False
    ):
        return replace(primitive.metadata, hidden=True)
    return primitive.metadata


def _altium_component_text_visible(
    footprint: PcbFootprint,
    key: str,
    *,
    default: bool,
) -> bool:
    raw = footprint.metadata.properties.get(key, "")
    if not raw:
        return default
    return raw.upper() in {"T", "TRUE", "1", "YES"}


def _pour_for_primitive(primitive: _ParsedPrimitive, pours: list[PcbPour]) -> PcbPour | None:
    if not primitive.pour_id:
        return None
    for pour in pours:
        if pour.id == primitive.pour_id:
            return pour
    return None


def _parsed_via_type(primitive: _ParsedPrimitive) -> PcbViaType:
    if primitive.has_role(_ParsedRole.FREE_VIA):
        return PcbViaType.FREE
    if primitive.has_role(_ParsedRole.BLIND_VIA):
        return PcbViaType.BLIND
    return PcbViaType.THROUGH


def _artwork_kind(primitive: _ParsedPrimitive) -> PcbArtworkKind:
    if primitive.shape == _ParsedShapeKind.LINE:
        return PcbArtworkKind.LINE
    if primitive.shape == _ParsedShapeKind.ARC:
        return PcbArtworkKind.ARC
    if primitive.shape == _ParsedShapeKind.CIRCLE:
        return PcbArtworkKind.CIRCLE
    if primitive.shape == _ParsedShapeKind.TEXT:
        return PcbArtworkKind.TEXT
    if primitive.shape == _ParsedShapeKind.MODEL:
        return PcbArtworkKind.MODEL_3D
    return PcbArtworkKind.POLYGON


def _artwork_purpose(
    primitive: _ParsedPrimitive,
    layer: PcbLayer | None,
) -> PcbArtworkPurpose:
    if primitive.has_role(_ParsedRole.DESIGNATOR):
        return PcbArtworkPurpose.DESIGNATOR
    if primitive.has_role(_ParsedRole.VALUE):
        return PcbArtworkPurpose.VALUE
    if primitive.has_role(_ParsedRole.USER_TEXT) or primitive.has_role(_ParsedRole.TEXT):
        return PcbArtworkPurpose.USER_TEXT
    if primitive.has_role(_ParsedRole.COMPONENT_BODY):
        return PcbArtworkPurpose.COMPONENT_BODY
    if primitive.has_role(_ParsedRole.SILKSCREEN):
        return PcbArtworkPurpose.SILKSCREEN
    if primitive.has_role(_ParsedRole.FABRICATION):
        return PcbArtworkPurpose.FABRICATION
    if primitive.has_role(_ParsedRole.ASSEMBLY):
        return PcbArtworkPurpose.ASSEMBLY
    if primitive.has_role(_ParsedRole.COURTYARD):
        return PcbArtworkPurpose.COURTYARD
    if primitive.has_role(_ParsedRole.SOLDER_MASK):
        return PcbArtworkPurpose.SOLDER_MASK
    if primitive.has_role(_ParsedRole.SOLDER_PASTE):
        return PcbArtworkPurpose.SOLDER_PASTE
    if primitive.has_role(_ParsedRole.MECHANICAL):
        return PcbArtworkPurpose.MECHANICAL
    if layer is not None and layer.has_role(LayerRole.USER):
        return PcbArtworkPurpose.USER
    return PcbArtworkPurpose.UNKNOWN


# ---------------------------------------------------------------------------
# Project-level data: rules, classes, diff pairs, stackup
# ---------------------------------------------------------------------------


def _read_rules6_records(data: bytes) -> list[dict[str, str]]:
    """Read Rules6 stream records (2-byte header + 4-byte LE length framing)."""
    records: list[dict[str, str]] = []
    pos = 0
    while pos + 6 <= len(data):
        # 2-byte header (type + padding) + 4-byte LE length
        length = u32(data, pos + 2)
        pos += 6
        if length == 0 or pos + length > len(data):
            break
        payload = data[pos : pos + length]
        pos += length
        props = parse_record_payload(payload)
        if props:
            records.append(props)
    return records


def parse_altium_rules(data: bytes) -> list[DesignRule]:
    """Parse Altium Rules6 stream into DesignRule objects."""
    records = _read_rules6_records(data)
    rules: list[DesignRule] = []
    for props in records:
        name = props.get("name", "")
        kind = props.get("rulekind", "")
        enabled = props.get("enabled", "TRUE").upper() == "TRUE"
        priority = int(props.get("priority", "0") or "0")
        scope1 = props.get("scope1expression", "")
        scope2 = props.get("scope2expression", "")

        # Extract numeric values (may be in mils, convert to mm).
        # Different rule kinds use different property names for their values.
        min_val = _rule_value_mm(
            props,
            "minlimit",
            "gap",
            "genericclearance",
            "clearance",
            "minimumring",
            "minsoldermaskwidth",
            "minsilkscreentomaskgap",
            "minwidth",
            "minholewidth",
            "minheight",
            "minsize",
        )
        max_val = _rule_value_mm(
            props,
            "maxlimit",
            "maxwidth",
            "maxholewidth",
            "maxheight",
            "maxsize",
            "maxuncoupledlength",
            "tolerance",
            "limit",
        )
        pref_val = _rule_value_mm(
            props,
            "preferedwidth",
            "preferredwidth",
            "expansion",
            "prefheight",
            "preferedsize",
            "toplayer_prefwidth",
        )

        # Collect remaining properties
        skip_keys = {
            "name",
            "rulekind",
            "enabled",
            "priority",
            "scope1expression",
            "scope2expression",
            "selection",
            "layer",
            "locked",
            "polygonoutline",
            "userrouted",
            "keepout",
            "unionindex",
            "netscope",
            "layerkind",
            "superclass",
        }
        extra: dict[str, str] = {}
        for k, v in props.items():
            if k not in skip_keys and v:
                extra[k] = v

        rules.append(
            DesignRule(
                name=name,
                kind=kind,
                enabled=enabled,
                priority=priority,
                scope1=scope1,
                scope2=scope2,
                min_value_mm=min_val,
                max_value_mm=max_val,
                preferred_value_mm=pref_val,
                properties=extra,
            )
        )
    return rules


def _rule_value_mm(props: dict[str, str], *keys: str) -> float | None:
    """Extract a rule value in mm from property keys (values stored in mils).

    Values may have a "mil" suffix that must be stripped before conversion.
    """
    for key in keys:
        val_str = props.get(key, "")
        if val_str:
            try:
                return float(_strip_mil(val_str)) * _MIL_TO_MM
            except ValueError:
                continue
    return None


def parse_altium_classes(data: bytes) -> list[NetClass]:
    """Parse Altium Classes6 stream into NetClass objects."""
    records = read_text_records(data)
    classes: list[NetClass] = []
    for props in records:
        name = props.get("name", "")
        kind = int(props.get("kind", "0") or "0")
        # Extract members (M0, M1, M2, ...)
        members: list[str] = []
        i = 0
        while True:
            key = f"m{i}"
            if key in props:
                members.append(props[key])
                i += 1
            else:
                break
        classes.append(NetClass(name=name, kind=kind, members=members))
    return classes


def parse_altium_diff_pairs(data: bytes) -> list[DiffPair]:
    """Parse Altium DifferentialPairs6 stream into DiffPair objects."""
    records = read_text_records(data)
    pairs: list[DiffPair] = []
    for props in records:
        name = props.get("name", "")
        pos_net = props.get("positivenetname", "")
        neg_net = props.get("negativenetname", "")
        if name and pos_net and neg_net:
            pairs.append(DiffPair(name=name, positive_net=pos_net, negative_net=neg_net))
    return pairs


def parse_altium_stackup(board_props: dict[str, str]) -> Stackup | None:
    """Extract PCB stackup from Board6 properties.

    Prefers the v9 stackup format (v9_stack_layerN_*) which stores explicit
    layer names, correct physical ordering, and separate core/prepreg entries.
    Falls back to the legacy format (layerN + next-pointer chain) for older files.
    """
    stackup = _parse_v9_stackup(board_props)
    if stackup:
        return stackup
    return _parse_legacy_stackup(board_props)


def _parse_v9_stackup(board_props: dict[str, str]) -> Stackup | None:
    """Parse the v9 stackup format (Altium Designer 19+).

    v9 layers are stored as v9_stack_layer{N}_* in physical order from top
    to bottom. Includes solder mask, copper, prepreg, and core layers with
    explicit user-assigned names.
    """
    # Discover which v9 layer indices exist
    layer_indices: list[int] = []
    for key in board_props:
        if key.startswith("v9_stack_layer") and key.endswith("_name"):
            try:
                idx = int(key[len("v9_stack_layer") : -len("_name")])
                layer_indices.append(idx)
            except ValueError:
                continue

    if not layer_indices:
        return None

    layer_indices.sort()

    layers: list[StackupLayer] = []
    # Track whether we've seen the first and last copper to determine sides
    copper_indices: list[int] = []
    for idx in layer_indices:
        copthick = board_props.get(f"v9_stack_layer{idx}_copthick", "")
        if copthick:
            copper_indices.append(idx)

    first_copper = copper_indices[0] if copper_indices else -1
    last_copper = copper_indices[-1] if copper_indices else -1

    for idx in layer_indices:
        prefix = f"v9_stack_layer{idx}_"
        name = board_props.get(f"{prefix}name", "")
        if not name:
            continue

        copthick_str = _strip_mil(board_props.get(f"{prefix}copthick", ""))
        diel_type_raw = board_props.get(f"{prefix}dieltype", "")
        diel_height_str = _strip_mil(board_props.get(f"{prefix}dielheight", ""))
        diel_const_str = board_props.get(f"{prefix}dielconst", "")
        diel_material = board_props.get(f"{prefix}dielmaterial", "").strip()
        diel_loss_str = board_props.get(f"{prefix}diellosstangent", "")
        copper_orient = board_props.get(f"{prefix}copperorientation", "")

        if copthick_str:
            # Copper layer
            cop_thick_mm = float(copthick_str) * _MIL_TO_MM

            side = ""
            if idx == first_copper:
                side = "front"
            elif idx == last_copper:
                side = "back"

            orientation = ""
            if copper_orient == "1":
                orientation = "reversed"
            elif copper_orient == "0" or (copper_orient == "" and copthick_str):
                orientation = "normal"

            layers.append(
                StackupLayer(
                    name=name,
                    layer_type="copper",
                    thickness_mm=cop_thick_mm,
                    side=side,
                    copper_orientation=orientation,
                )
            )
        elif diel_height_str:
            # Dielectric layer (prepreg, core, or solder mask)
            thickness_mm = float(diel_height_str) * _MIL_TO_MM
            epsilon_r = float(diel_const_str) if diel_const_str else 0.0
            loss_tangent = float(diel_loss_str) if diel_loss_str else 0.0

            # dieltype: 0=unspecified, 1=core, 2=prepreg, 3=solder_mask
            diel_type_map = {"1": "core", "2": "prepreg", "3": "solder_mask"}
            layer_type = diel_type_map.get(diel_type_raw, "prepreg")

            layers.append(
                StackupLayer(
                    name=name,
                    layer_type=layer_type,
                    thickness_mm=thickness_mm,
                    material=diel_material,
                    epsilon_r=epsilon_r,
                    loss_tangent=loss_tangent,
                )
            )
        # Skip non-physical layers (paste, overlay) that have neither
        # copper thickness nor dielectric height

    if not layers:
        return None

    total = sum(ly.thickness_mm for ly in layers)
    return Stackup(layers=layers, total_thickness_mm=total)


def _parse_legacy_stackup(board_props: dict[str, str]) -> Stackup | None:
    """Parse the legacy layerN + next-pointer stackup format.

    Used by older Altium files that lack v9_stack_layer data. Follows the
    layer{N}next chain starting at layer 1. Dielectrics are numbered
    sequentially by traversal position.
    """
    layers: list[StackupLayer] = []

    # Follow the next-pointer chain starting at layer 1
    i = 1
    visited: set[int] = set()
    diel_counter = 0
    while i > 0 and i not in visited:
        visited.add(i)
        prefix = f"layer{i}"
        name = board_props.get(f"{prefix}name", "")
        if not name:
            break

        # Copper thickness (value may have "mil" suffix)
        cop_thick_str = _strip_mil(board_props.get(f"{prefix}copthick", ""))
        cop_thick_mm = float(cop_thick_str) * _MIL_TO_MM if cop_thick_str else 0.0

        # Dielectric properties
        diel_type_raw = board_props.get(f"{prefix}dieltype", "")
        diel_const_str = board_props.get(f"{prefix}dielconst", "")
        diel_height_str = _strip_mil(board_props.get(f"{prefix}dielheight", ""))
        diel_material = board_props.get(f"{prefix}dielmaterial", "").strip()
        diel_loss_str = board_props.get(f"{prefix}diellosstangent", "")

        epsilon_r = float(diel_const_str) if diel_const_str else 0.0
        diel_height_mm = float(diel_height_str) * _MIL_TO_MM if diel_height_str else 0.0
        loss_tangent = float(diel_loss_str) if diel_loss_str else 0.0

        # Dielectric type mapping
        diel_type_map = {"0": "prepreg", "1": "core", "2": "prepreg"}
        diel_type = diel_type_map.get(diel_type_raw, "prepreg")

        # Determine side
        side = ""
        name_lower = name.lower()
        if "top" in name_lower:
            side = "front"
        elif "bottom" in name_lower or "bot" in name_lower:
            side = "back"

        # Add copper layer
        layers.append(
            StackupLayer(
                name=name,
                layer_type="copper",
                thickness_mm=cop_thick_mm,
                side=side,
            )
        )

        # Follow next pointer
        next_str = board_props.get(f"{prefix}next", "0")
        next_layer = int(next_str) if next_str else 0

        # Add dielectric layer between this copper and the next (skip after last)
        if diel_height_mm > 0 and next_layer > 0:
            diel_counter += 1
            layers.append(
                StackupLayer(
                    name=f"Dielectric {diel_counter}",
                    layer_type=diel_type,
                    thickness_mm=diel_height_mm,
                    material=diel_material,
                    epsilon_r=epsilon_r,
                    loss_tangent=loss_tangent,
                )
            )

        i = next_layer

    if not layers:
        return None

    total = sum(ly.thickness_mm for ly in layers)
    return Stackup(layers=layers, total_thickness_mm=total)


def _strip_mil(s: str) -> str:
    """Strip 'mil' suffix from an Altium dimension string."""
    s = s.strip()
    if s.lower().endswith("mil"):
        return s[:-3]
    return s


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def _read_stream(ole: olefile.OleFileIO, name: str) -> bytes:
    """Read a stream from the OLE container, returning empty bytes if absent."""
    if ole.exists(name):
        return ole.openstream(name).read()
    return b""


def parse_altium_pcb(
    path: Path,
    ctx: ParseContext | None = None,
) -> Pcb:
    """Parse an Altium .PcbDoc file into the PCB domain model."""
    if ctx is None:
        ctx = ParseContext()
    ole = olefile.OleFileIO(str(path))
    try:
        # Read all streams
        nets_data = _read_stream(ole, "Nets6/Data")
        comp_data = _read_stream(ole, "Components6/Data")
        tracks_data = _read_stream(ole, "Tracks6/Data")
        vias_data = _read_stream(ole, "Vias6/Data")
        arcs_data = _read_stream(ole, "Arcs6/Data")
        pads_data = _read_stream(ole, "Pads6/Data")
        texts_data = _read_stream(ole, "Texts6/Data")
        fills_data = _read_stream(ole, "Fills6/Data")
        regions_data = _read_stream(ole, "Regions6/Data")
        polygons6_data = _read_stream(ole, "Polygons6/Data")
        sb_regions_data = _read_stream(ole, "ShapeBasedRegions6/Data")
        comp_bodies_data = _read_stream(ole, "ComponentBodies6/Data")
        board_data = _read_stream(ole, "Board6/Data")
        drill_manager_data = _read_stream(ole, "DrillManager/Data")
    finally:
        ole.close()

    # Build layer map from Board6 metadata + static defaults
    board_props: dict[str, str] = {}
    if board_data:
        board_records = read_text_records(board_data)
        if board_records:
            board_props = board_records[0]
    layer_map = _build_layer_map(board_props, ctx)

    # Parse text streams
    nets = _parse_nets(nets_data)
    footprints = _parse_components(comp_data, layer_map)

    # Parse binary streams
    vias = _parse_vias(vias_data, layer_map, ctx)
    raw_pads = _parse_pads(pads_data, nets, layer_map, ctx)
    _apply_drill_manager_mask_apertures(raw_pads, drill_manager_data)
    raw_texts = _parse_texts(texts_data, layer_map, ctx)
    pours, pour_id_map, pour_net_map = _parse_polygon_pours(polygons6_data, nets, layer_map)
    track_geometry, track_keepouts = _parse_tracks(tracks_data, layer_map, ctx, pour_id_map)
    arc_geometry, arc_keepouts = _parse_arcs(arcs_data, layer_map, ctx, pour_id_map)
    fills, fill_keepouts = _parse_fills(fills_data, layer_map, ctx)
    regions = _parse_regions(regions_data, nets, layer_map, ctx, pour_id_map, pour_net_map)
    shape_regions = _parse_shape_based_regions(
        sb_regions_data, nets, layer_map, ctx, pour_id_map, pour_net_map
    )
    comp_models = _parse_component_bodies(comp_bodies_data)

    geometry = [
        *[item for item in track_geometry if item.metadata.native_component_index is None],
        *vias,
        *[item for item in arc_geometry if item.metadata.native_component_index is None],
        *fills,
        *regions,
        *_dedupe_shape_based_board_polygons(
            regions,
            [item for item in shape_regions if item.metadata.native_component_index is None],
        ),
    ]
    if not any(item.has_role(_ParsedRole.BOARD_OUTLINE) for item in geometry):
        geometry.extend(_parse_board_outline(tracks_data, arcs_data, layer_map, ctx))

    for comp_idx, pad in raw_pads:
        if comp_idx == COMPONENT_NONE:
            geometry.append(pad)
        elif comp_idx < len(footprints):
            geometry.append(_with_footprint_ref(pad, footprints[comp_idx].reference))

    for comp_idx, text in raw_texts:
        if comp_idx != COMPONENT_NONE and comp_idx < len(footprints):
            geometry.append(_with_footprint_ref(text, footprints[comp_idx].reference))
        elif comp_idx == COMPONENT_NONE:
            geometry.append(text)

    for item in track_geometry + arc_geometry + shape_regions:
        comp_idx = item.metadata.native_component_index
        if comp_idx is None:
            continue
        if comp_idx < len(footprints):
            geometry.append(_with_footprint_ref(item, footprints[comp_idx].reference))

    for comp_idx, models in comp_models.items():
        if comp_idx < len(footprints):
            geometry.extend(
                _with_footprint_ref(model, footprints[comp_idx].reference) for model in models
            )

    # Extract value text and compute bounding boxes
    for fp in footprints:
        if not fp.value:
            fp.value = next(
                (
                    text.data.text
                    for text in geometry
                    if text.footprint_ref == fp.reference
                    and text.has_role(_ParsedRole.VALUE)
                    and isinstance(text.data, PcbText)
                ),
                "",
            )
        fp.bbox = _compute_bbox(
            [
                item
                for item in geometry
                if item.footprint_ref == fp.reference and item.object_type == _ParsedObjectKind.PAD
            ]
        )

    # Board name from Board6/Data (board_props already parsed above)
    board_name = board_props.get("filename", "")
    if "\\" in board_name:
        board_name = board_name.rsplit("\\", 1)[-1]
    if board_name.endswith(".$$$"):
        board_name = board_name[:-4]

    keepouts = [*track_keepouts, *arc_keepouts, *fill_keepouts]

    return _build_pcb_from_parsed_primitives(
        name=board_name,
        layer_map=layer_map,
        nets=nets,
        footprints=footprints,
        pours=pours,
        keepouts=keepouts,
        primitives=geometry,
        ctx=ctx,
    )
