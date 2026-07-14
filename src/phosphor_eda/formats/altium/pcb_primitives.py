"""Shared primitive model and low-level helpers for the Altium PCB parser.

This is the foundation layer imported by every other Altium PCB module
(``pcb_layers``, ``pcb_streams``, ``pcb_keepouts``, ``pcb_build``,
``pcb_parser``). It holds the intermediate ``_Parsed*`` model the parser
assembles before building the domain ``Board``, the binary/text stream framing
readers, coordinate conversions, and the metadata/role helpers shared across
stream decoding and domain assembly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from phosphor_eda.domain.pcb import (
    LayerRole,
    PadStack,
    PcbArc,
    PcbCircle,
    PcbLayer,
    PcbLine,
    PcbModel3D,
    PcbNet,
    PcbObjectMetadata,
    PcbPolygon,
    PcbText,
)
from phosphor_eda.formats.altium._helpers import guarded_float, u32
from phosphor_eda.formats.altium.enums import AltiumLayer
from phosphor_eda.formats.altium.pcb_records import NET_UNCONNECTED
from phosphor_eda.formats.altium.record_parser import parse_record_payload
from phosphor_eda.formats.common.diagnostics import warn_optional

if TYPE_CHECKING:
    from phosphor_eda.formats.common.diagnostics import ParseContext

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 1 internal unit = 0.1 µinch = 0.0000001 inch = 0.00000254 mm
_INT_TO_MM = 0.00000254

# 1 mil = 0.001 inch = 0.0254 mm
MIL_TO_MM = 0.0254

_POLYGON_NONE = 0xFFFF

# Copper layer numbers for filtering (top, mid 1-30, bottom).
COPPER_LAYERS = frozenset(range(AltiumLayer.TOP_LAYER, AltiumLayer.BOTTOM_LAYER + 1))


# ---------------------------------------------------------------------------
# Parsed primitive model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PadMaskAperture:
    width: float
    height: float
    source: str


@dataclass(frozen=True)
class DrillManagerRecord:
    properties: dict[str, str]
    primitive_indices: tuple[int, ...]


class ParsedRole(StrEnum):
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


class ParsedObjectKind(StrEnum):
    GRAPHIC = "graphic"
    MODEL_3D = "model_3d"
    PAD = "pad"
    REGION = "region"
    TEXT = "text"
    TRACK = "track"
    VIA = "via"


class ParsedShapeKind(StrEnum):
    ARC = "arc"
    CIRCLE = "circle"
    LINE = "line"
    MODEL = "model"
    POLYGON = "polygon"
    RECTANGLE = "rectangle"
    TEXT = "text"


_PARSED_ROLE_ORDER: tuple[ParsedRole, ...] = tuple(ParsedRole)


def normalize_parsed_roles(*roles: ParsedRole | str) -> tuple[ParsedRole, ...]:
    role_set = {role if isinstance(role, ParsedRole) else ParsedRole(role) for role in roles}
    return tuple(role for role in _PARSED_ROLE_ORDER if role in role_set)


@dataclass(frozen=True)
class ParsedPadPayload:
    number: str
    x: float
    y: float
    width: float
    height: float
    shape: str
    rotation: float = 0.0
    drill: float = 0.0
    roundrect_rratio: float = 0.0
    hole_plated: bool | None = None
    hole_is_slot: bool = False
    slot_length: float = 0.0
    slot_rotation: float = 0.0
    mask_aperture_width: float | None = None
    mask_aperture_height: float | None = None
    mask_aperture_source: str = ""
    # Non-simple padstack (top-mid-bottom / per-layer); None = SIMPLE.
    stack: PadStack | None = None


@dataclass(frozen=True)
class ParsedViaPayload:
    x: float
    y: float
    size: float
    drill: float
    # Non-simple padstack (top-mid-bottom / per-layer); None = SIMPLE.
    stack: PadStack | None = None


type _ParsedPayload = (
    PcbLine
    | PcbArc
    | PcbCircle
    | PcbPolygon
    | PcbText
    | PcbModel3D
    | ParsedPadPayload
    | ParsedViaPayload
)


@dataclass(frozen=True, kw_only=True)
class ParsedPrimitive:
    id: str
    object_type: ParsedObjectKind
    shape: ParsedShapeKind
    roles: tuple[ParsedRole, ...]
    data: _ParsedPayload
    layers: tuple[str, ...] = ()
    net_number: int = 0
    net_name: str = ""
    pour_id: str = ""
    metadata: PcbObjectMetadata = field(default_factory=PcbObjectMetadata)

    def __post_init__(self) -> None:
        object.__setattr__(self, "layers", tuple(self.layers))
        object.__setattr__(self, "roles", normalize_parsed_roles(*self.roles))

    @property
    def primary_layer(self) -> str:
        return self.layers[0] if self.layers else ""

    def has_role(self, role: ParsedRole | str) -> bool:
        normalized = role if isinstance(role, ParsedRole) else ParsedRole(role)
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


def read_binary_records(
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


def int_to_mm(val: int) -> float:
    """Convert Altium internal units (0.1 µinch) to millimetres."""
    return val * _INT_TO_MM


def parse_mil(s: str, *, ctx: ParseContext | None = None, field: str = "mil value") -> float:
    """Parse a mil-string like ``'1153.8945mil'`` and return mm.

    File-supplied; a garbage value degrades to ``0.0`` with a diagnostic on
    *ctx* rather than raising.
    """
    return guarded_float(s.removesuffix("mil"), ctx=ctx, field=field) * MIL_TO_MM


def parse_rotation(s: str, *, ctx: ParseContext | None = None, field: str = "rotation") -> float:
    """Parse a rotation string (may be scientific notation)."""
    return guarded_float(s, ctx=ctx, field=field)


def altium_net_number(raw: int) -> int:
    """Map Altium net index to domain net number (0 = unconnected)."""
    return 0 if raw == NET_UNCONNECTED else raw + 1


def resolve_stream_net(raw: int, nets: dict[int, PcbNet], unknown: list[int]) -> int:
    """Resolve a raw Altium net index to a domain net number.

    Returns the 1-based domain number, or ``0`` (unconnected) when the index is
    absent from *nets*. Each unresolved raw index is appended to *unknown* so a
    single per-stream diagnostic can summarize the degradations without one
    warning per primitive.
    """
    num = altium_net_number(raw)
    if num != 0 and num not in nets:
        unknown.append(raw)
        return 0
    return num


def warn_unknown_stream_nets(ctx: ParseContext | None, source: str, unknown: list[int]) -> None:
    """Emit one diagnostic summarizing unknown net indices seen in a stream."""
    if not unknown:
        return
    distinct = sorted(set(unknown))
    warn_optional(
        ctx,
        "unknown_net",
        f"{source}: {len(unknown)} primitive(s) reference unknown net "
        f"index(es) {distinct}; treated as unconnected",
    )


# ---------------------------------------------------------------------------
# Metadata + role helpers
# ---------------------------------------------------------------------------


_LAYER_TO_GEOMETRY_ROLES: dict[LayerRole, ParsedRole] = {
    LayerRole.SOLDER_MASK: ParsedRole.SOLDER_MASK,
    LayerRole.SOLDER_PASTE: ParsedRole.SOLDER_PASTE,
    LayerRole.SILKSCREEN: ParsedRole.SILKSCREEN,
    LayerRole.FABRICATION: ParsedRole.FABRICATION,
    LayerRole.ASSEMBLY: ParsedRole.ASSEMBLY,
    LayerRole.COURTYARD: ParsedRole.COURTYARD,
    LayerRole.DESIGNATOR: ParsedRole.DESIGNATOR,
    LayerRole.VALUE: ParsedRole.VALUE,
    LayerRole.MECHANICAL: ParsedRole.MECHANICAL,
}


def layer_geometry_roles(
    layer_num: int,
    layer_map: dict[int, PcbLayer],
) -> tuple[ParsedRole, ...]:
    layer = layer_map.get(layer_num)
    if layer is None:
        return ()
    return tuple(
        geometry_role
        for role in layer.roles
        if (geometry_role := _LAYER_TO_GEOMETRY_ROLES.get(role)) is not None
    )


def layered_geometry_roles(
    layer_num: int,
    layer_map: dict[int, PcbLayer],
    *roles: ParsedRole,
) -> tuple[ParsedRole, ...]:
    return normalize_parsed_roles(*layer_geometry_roles(layer_num, layer_map), *roles)


def geometry_metadata(
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


def pour_metadata(
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


def keepout_metadata(
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


def resolve_pour_id(
    pour_id_map: dict[int, str],
    index: int | None,
) -> str:
    if index is not None and index >= 0 and index != _POLYGON_NONE and index in pour_id_map:
        return pour_id_map[index]
    return ""


def resolve_pour_net(
    pour_net_map: dict[int, int] | None,
    index: int | None,
) -> int:
    if pour_net_map is None:
        return 0
    if index is not None and index >= 0 and index != _POLYGON_NONE and index in pour_net_map:
        return pour_net_map[index]
    return 0
