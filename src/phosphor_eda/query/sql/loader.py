"""Load a Project into an in-memory DuckDB database.

Every table is described by a declarative :class:`TableSpec` (DDL + named-column
inserts generated from the same column list). The ``_load_*`` functions build
per-row source objects and hand them to the spec, so column order and the
on-disk schema can never drift apart.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, is_dataclass
from typing import TYPE_CHECKING, Protocol

import duckdb
import numpy as np
import shapely
from shapely import LineString, Point
from shapely.affinity import rotate

from phosphor_eda.domain.pcb import (
    LayerRole,
    PcbArc,
    PcbCircle,
    PcbDimension,
    PcbDrill,
    PcbDrillShape,
    PcbLayer,
    PcbLine,
    PcbMetadata,
    PcbModel3D,
    PcbPad,
    PcbPolygon,
    PcbShape,
    PcbText,
    PcbVia,
    copper_layers,
    normalize_roles,
)
from phosphor_eda.formats.common.electrical import ELECTRICAL_KEY
from phosphor_eda.geometry.pcb_geometry import (
    arc_center_from_three_points,
    arc_sweep_angle,
    arc_to_polyline,
    board_outline_polygon,
    closed_path_geometry,
    footprint_bbox_polygon,
    footprint_side,
    pad_polygon,
    polygon_shape_geometry,
    segment_geometry,
    trace_arc_geometry,
    via_geometry,
)
from phosphor_eda.geometry.text_outlines import text_outline_geometry
from phosphor_eda.query.classify import is_power_net
from phosphor_eda.query.sql.schema import (
    GEOMETRY,
    INDEX_DDL,
    VIEW_DDL,
    TableSpec,
    col,
    create_views,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from numpy.typing import NDArray
    from shapely.geometry.base import BaseGeometry

    from phosphor_eda.domain.pcb import (
        Board,
        PcbArtwork,
        PcbBoardProfileElement,
        PcbConductor,
        PcbFootprint,
        PcbKeepout,
        PcbNet,
        PcbPour,
    )
    from phosphor_eda.domain.project import (
        DesignRule,
        DiffPair,
        NetClass,
        Project,
        ProjectDocument,
    )
    from phosphor_eda.domain.schematic import (
        Bus,
        Component,
        ComponentOccurrence,
        FootprintModel,
        Net,
        NetOccurrence,
        Page,
        Parameter,
        PartNumber,
        Pin,
        PinOccurrence,
        Schematic,
        SchematicDirective,
        TitleBlock,
    )
    from phosphor_eda.domain.variants import Variant, VariantOverride, VariantValue


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

_QUAD_SEGS_CIRCLE = 16
_QUAD_SEGS_DRILL = 8
_PROFILE_ARC_POINTS = 32

_LineCoords = tuple[tuple[float, float], tuple[float, float]]


class _ShapelyBuffer(Protocol):
    def __call__(
        self,
        geometry: NDArray[np.object_],
        distance: NDArray[np.float64],
        *,
        cap_style: str,
    ) -> NDArray[np.object_]: ...


_shapely_linestrings: Callable[[Sequence[_LineCoords]], NDArray[np.object_]] = vars(shapely)[
    "linestrings"
]
_shapely_buffer: _ShapelyBuffer = vars(shapely)["buffer"]
_shapely_is_empty: Callable[[NDArray[np.object_]], NDArray[np.bool_]] = vars(shapely)["is_empty"]
_shapely_to_wkb: Callable[[NDArray[np.object_]], NDArray[np.bytes_]] = vars(shapely)["to_wkb"]


def _wkb(geom: BaseGeometry | None) -> bytes | None:
    return None if geom is None or geom.is_empty else geom.wkb


def _metadata_json(metadata: PcbMetadata) -> str:
    return json.dumps(asdict(metadata), separators=(",", ":"), sort_keys=True)


def _json_or_null(mapping: dict[str, str]) -> str | None:
    if not mapping:
        return None
    return json.dumps(mapping, separators=(",", ":"), sort_keys=True)


def _variant_value_json(value: VariantValue) -> str | None:
    if value is None:
        return None
    if isinstance(value, (bool, str)):
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    if is_dataclass(value):
        return json.dumps(asdict(value), separators=(",", ":"), sort_keys=True)
    return json.dumps(
        [asdict(item) if is_dataclass(item) else item for item in value],
        separators=(",", ":"),
        sort_keys=True,
    )


def _net_fields(net: PcbNet | None) -> tuple[str | None, int | None]:
    if net is None:
        return None, None
    return net.name, net.number


def _layer_names(layers: tuple[PcbLayer, ...]) -> list[str]:
    return [layer.name for layer in layers]


def _primary_layer(layers: tuple[PcbLayer, ...]) -> str:
    return layers[0].name if layers else ""


def _pad_side(pad: PcbPad) -> str:
    sides = {layer.side for layer in pad.layers if layer.side}
    if len(sides) > 1:
        return "through"
    return next(iter(sides), "")


def _shape_geometry(payload: PcbShape) -> BaseGeometry | None:
    # Exhaustive over PcbShape: every member is handled and the final narrowing
    # assignment makes a new member a type error rather than a silently-NULL
    # geom column.
    if isinstance(payload, PcbLine):
        if payload.width > 0.0:
            return segment_geometry(payload)[1]
        return LineString(((payload.start_x, payload.start_y), (payload.end_x, payload.end_y)))
    if isinstance(payload, PcbArc):
        if payload.width > 0.0:
            return trace_arc_geometry(payload)[1]
        return LineString(
            arc_to_polyline(
                payload.start_x,
                payload.start_y,
                payload.mid_x,
                payload.mid_y,
                payload.end_x,
                payload.end_y,
            )
        )
    if isinstance(payload, PcbPolygon):
        return polygon_shape_geometry(payload)
    if isinstance(payload, PcbCircle):
        outer = Point(payload.cx, payload.cy).buffer(payload.radius, quad_segs=_QUAD_SEGS_CIRCLE)
        if payload.fill:
            return outer
        return outer.boundary.buffer(max(payload.width, 0.01) / 2.0)
    if isinstance(payload, PcbText):
        return text_outline_geometry(payload)
    if isinstance(payload, PcbDimension):
        return LineString(((payload.start_x, payload.start_y), (payload.end_x, payload.end_y)))
    # Only PcbModel3D remains, which carries no 2D board geometry. The explicit
    # type annotation makes a new PcbShape member a type error here rather than a
    # silently-NULL geom column (assert_never can't be used: PcbModel3D is a
    # valid no-geometry case, not unreachable).
    _: PcbModel3D = payload
    return None


def _line_artwork_wkbs(lines: list[PcbLine]) -> list[bytes | None]:
    if not lines:
        return []

    centerlines = _shapely_linestrings(
        [((line.start_x, line.start_y), (line.end_x, line.end_y)) for line in lines]
    )
    widths = np.array([line.width for line in lines], dtype=np.float64)
    geometries = _shapely_buffer(
        centerlines,
        widths / 2.0,
        cap_style="flat",
    )
    geometries = np.where(widths > 0.0, geometries, centerlines)
    empty_flags = _shapely_is_empty(geometries)
    wkb_values = _shapely_to_wkb(geometries)
    return [
        None if bool(is_empty) else bytes(wkb)
        for is_empty, wkb in zip(empty_flags, wkb_values, strict=True)
    ]


def _profile_shape_geometry(
    payload: PcbLine | PcbArc | PcbCircle | PcbPolygon,
) -> BaseGeometry | None:
    if isinstance(payload, PcbLine):
        return LineString(((payload.start_x, payload.start_y), (payload.end_x, payload.end_y)))
    if isinstance(payload, PcbArc):
        return LineString(
            arc_to_polyline(
                payload.start_x,
                payload.start_y,
                payload.mid_x,
                payload.mid_y,
                payload.end_x,
                payload.end_y,
                num_points=_PROFILE_ARC_POINTS,
            )
        )
    return _shape_geometry(payload)


def _drill_geometry(drill: PcbDrill) -> BaseGeometry | None:
    width = drill.width if drill.width > 0.0 else drill.diameter
    height = drill.height if drill.height > 0.0 else drill.diameter
    if width <= 0.0 or height <= 0.0:
        return None
    if drill.shape != PcbDrillShape.SLOT or math.isclose(width, height):
        return Point(drill.x, drill.y).buffer(width / 2.0, quad_segs=_QUAD_SEGS_DRILL)
    radius = min(width, height) / 2.0
    if width > height:
        half_span = (width - height) / 2.0
        line = LineString(((drill.x - half_span, drill.y), (drill.x + half_span, drill.y)))
    else:
        half_span = (height - width) / 2.0
        line = LineString(((drill.x, drill.y - half_span), (drill.x, drill.y + half_span)))
    geometry = line.buffer(radius, quad_segs=_QUAD_SEGS_DRILL)
    if not math.isclose(drill.rotation % 360.0, 0.0):
        geometry = rotate(geometry, -drill.rotation, origin=(drill.x, drill.y))
    return geometry


def _drill_owner(drill: PcbDrill) -> tuple[str, str]:
    owner = drill.owner
    if isinstance(owner, PcbPad):
        return "pad", owner.id
    if isinstance(owner, PcbVia):
        return "via", owner.id
    return "mechanical", ""


def _stackup_layer_as_pcb_layer(layer_type: str, side: str) -> PcbLayer:
    roles: list[LayerRole] = []
    if layer_type == "copper":
        roles.append(LayerRole.COPPER)
    elif layer_type in {"core", "prepreg", "dielectric"}:
        roles.append(LayerRole.DIELECTRIC)
    elif layer_type == "solder_mask":
        roles.append(LayerRole.SOLDER_MASK)
    else:
        roles.append(LayerRole.UNKNOWN)
    if side == "front":
        roles.append(LayerRole.FRONT)
    elif side == "back":
        roles.append(LayerRole.BACK)
    elif side == "inner":
        roles.append(LayerRole.INNER)
    return PcbLayer(name="", roles=normalize_roles(*roles))


def _scope_path(page: Page) -> str:
    return str(page.scope_id)


def _page_names(pages: list[Page]) -> str | None:
    names = sorted({page.name for page in pages})
    return ",".join(names) if names else None


def _page_ids(pages: list[Page]) -> str | None:
    ids = sorted({page.id for page in pages})
    return ",".join(ids) if ids else None


def _csv(values: set[str]) -> str | None:
    return ",".join(sorted(values)) if values else None


def _ordered_csv(values: Iterable[str]) -> str | None:
    items = list(values)
    return ",".join(items) if items else None


def _null_if_unset[FalsyT: (float, str)](value: FalsyT) -> FalsyT | None:
    """Map a falsy sentinel (0.0 / "") to SQL NULL.

    Several domain fields use 0.0 or "" to mean "unset" because their parsers
    cannot distinguish a genuine zero/empty from a missing value. Centralizing
    that policy keeps the falsy→NULL decision in one documented place.
    """
    return value if value else None


def _copper_layer_count(board: Board) -> int:
    """Copper layer count, preferring the stackup over source layer roles."""
    if board.stackup:
        return sum(1 for layer in board.stackup.layers if layer.layer_type == "copper")
    return len(board.layers_by_role(LayerRole.COPPER))


def _unique_pages(pages: list[Page]) -> list[Page]:
    result: list[Page] = []
    seen: set[str] = set()
    for page in sorted(pages, key=lambda page: page.id):
        if page.id in seen:
            continue
        seen.add(page.id)
        result.append(page)
    return result


# ---------------------------------------------------------------------------
# Row-context types for tables that need cross-row context or computed geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _LayerRow:
    position: int | None
    name: str
    roles: list[str]
    side: str
    number: int | None
    thickness_mm: float | None
    material: str | None
    epsilon_r: float | None
    loss_tangent: float | None
    layer_type: str | None
    copper_orientation: str | None


@dataclass(frozen=True, slots=True)
class _BoardRow:
    board: Board
    geom: BaseGeometry | None


@dataclass(frozen=True, slots=True)
class _BoardsRow:
    board_id: str
    board: Board


@dataclass(frozen=True, slots=True)
class _ConductorRow:
    conductor: PcbConductor
    net_name: str | None
    net_number: int | None
    width: float | None
    start_x: float | None
    start_y: float | None
    end_x: float | None
    end_y: float | None
    is_arc: bool
    arc_center_x: float | None
    arc_center_y: float | None
    arc_angle: float | None
    length: float | None
    centerline: BaseGeometry | None
    geom: BaseGeometry | None


@dataclass(frozen=True, slots=True)
class _ViaRow:
    via: PcbVia
    net_name: str | None
    net_number: int | None
    start_layer: str
    end_layer: str
    copper_layers: list[str]
    geom: BaseGeometry | None


@dataclass(frozen=True, slots=True)
class _PadRow:
    pad: PcbPad
    copper_layers: list[str]


@dataclass(frozen=True, slots=True)
class _ArtworkRow:
    artwork: PcbArtwork
    text: str | None
    x: float | None
    y: float | None
    rotation: float | None
    font_size: float | None
    geom: BaseGeometry | None


@dataclass(frozen=True, slots=True)
class _NetRow:
    net: Net
    is_power: bool
    net_class: str | None
    diff_pair: str | None
    diff_pair_polarity: str | None


@dataclass(frozen=True, slots=True)
class _CompPageRow:
    component: Component
    page: Page


@dataclass(frozen=True, slots=True)
class _NetPageRow:
    net: Net
    page: Page


@dataclass(frozen=True, slots=True)
class _KeyValueRow:
    owner_id: str
    ref: str
    key: str
    value: str


@dataclass(frozen=True, slots=True)
class _OccKeyValueRow:
    occurrence_id: str
    owner_id: str
    key: str
    value: str


@dataclass(frozen=True, slots=True)
class _AliasRow:
    net_id: str
    net_name: str
    alias: str


@dataclass(frozen=True, slots=True)
class _SourceNameRow:
    occurrence_id: str
    net_id: str
    source_name: str


@dataclass(frozen=True, slots=True)
class _SchematicDirectiveRow:
    occurrence: NetOccurrence
    ord: int
    directive: SchematicDirective


@dataclass(frozen=True, slots=True)
class _BusMemberRow:
    bus: Bus
    net: Net
    ord: int


@dataclass(frozen=True, slots=True)
class _ComponentParameterRow:
    component_id: str
    ord: int
    parameter: Parameter


@dataclass(frozen=True, slots=True)
class _ComponentFootprintRow:
    component_id: str
    ord: int
    model: FootprintModel


@dataclass(frozen=True, slots=True)
class _ComponentPartNumberRow:
    component_id: str
    ord: int
    part_number: PartNumber


@dataclass(frozen=True, slots=True)
class _TitleBlockRow:
    page: Page
    block: TitleBlock


@dataclass(frozen=True, slots=True)
class _VariantOverrideRow:
    variant_name: str
    ord: int
    override: VariantOverride


@dataclass(frozen=True, slots=True)
class _PageAnnotationRow:
    page: Page
    ord: int
    text: str


# ---------------------------------------------------------------------------
# Table specs — DDL and inserts generated from one column list per table
# ---------------------------------------------------------------------------

_FOOTPRINTS: TableSpec[PcbFootprint] = TableSpec(
    "footprints",
    (
        col("reference", "VARCHAR", lambda fp: fp.reference),
        col("footprint_lib", "VARCHAR", lambda fp: fp.footprint_lib),
        col("x", "DOUBLE", lambda fp: fp.x),
        col("y", "DOUBLE", lambda fp: fp.y),
        col("rotation", "DOUBLE", lambda fp: fp.rotation),
        col("side", "VARCHAR", footprint_side),
        col("value", "VARCHAR", lambda fp: fp.value),
        col("geom", GEOMETRY, lambda fp: _wkb(footprint_bbox_polygon(fp))),
    ),
)

_PADS: TableSpec[_PadRow] = TableSpec(
    "pads",
    (
        col("id", "VARCHAR", lambda r: r.pad.id),
        col(
            "reference",
            "VARCHAR",
            lambda r: None if r.pad.footprint is None else r.pad.footprint.reference,
        ),
        col("pad_number", "VARCHAR", lambda r: r.pad.number),
        col("net_name", "VARCHAR", lambda r: _net_fields(r.pad.net)[0]),
        col("net_number", "INTEGER", lambda r: _net_fields(r.pad.net)[1]),
        col("x", "DOUBLE", lambda r: r.pad.x),
        col("y", "DOUBLE", lambda r: r.pad.y),
        col("width", "DOUBLE", lambda r: r.pad.width),
        col("height", "DOUBLE", lambda r: r.pad.height),
        col("shape", "VARCHAR", lambda r: r.pad.shape),
        col("pad_type", "VARCHAR", lambda r: r.pad.pad_type.value),
        col("drill_id", "VARCHAR", lambda r: None if r.pad.drill is None else r.pad.drill.id),
        col("drill", "DOUBLE", lambda r: None if r.pad.drill is None else r.pad.drill.diameter),
        col("side", "VARCHAR", lambda r: _pad_side(r.pad)),
        col("primary_layer", "VARCHAR", lambda r: _primary_layer(r.pad.layers)),
        col("layers", "VARCHAR[]", lambda r: _layer_names(r.pad.layers)),
        col("pin_function", "VARCHAR", lambda r: r.pad.pin_function),
        col("pin_type", "VARCHAR", lambda r: r.pad.pin_type),
        col(
            "mask_aperture_width",
            "DOUBLE",
            lambda r: None if r.pad.mask_aperture is None else r.pad.mask_aperture.aperture_width,
        ),
        col(
            "mask_aperture_height",
            "DOUBLE",
            lambda r: None if r.pad.mask_aperture is None else r.pad.mask_aperture.aperture_height,
        ),
        col(
            "mask_aperture_source",
            "VARCHAR",
            lambda r: (
                None if r.pad.mask_aperture is None else _null_if_unset(r.pad.mask_aperture.source)
            ),
        ),
        col("stack_mode", "VARCHAR", lambda r: r.pad.stack.mode.value),
        col("copper_layers", "VARCHAR[]", lambda r: r.copper_layers),
        col("geom", GEOMETRY, lambda r: _wkb(pad_polygon(r.pad))),
    ),
)

_VIAS: TableSpec[_ViaRow] = TableSpec(
    "vias",
    (
        col("id", "VARCHAR", lambda r: r.via.id),
        col("net_name", "VARCHAR", lambda r: r.net_name),
        col("net_number", "INTEGER", lambda r: r.net_number),
        col("x", "DOUBLE", lambda r: r.via.x),
        col("y", "DOUBLE", lambda r: r.via.y),
        col("diameter_mm", "DOUBLE", lambda r: r.via.diameter),
        col("drill_id", "VARCHAR", lambda r: r.via.drill.id),
        col("via_type", "VARCHAR", lambda r: r.via.via_type.value),
        col("start_layer", "VARCHAR", lambda r: r.start_layer),
        col("end_layer", "VARCHAR", lambda r: r.end_layer),
        col("layers", "VARCHAR[]", lambda r: _layer_names(r.via.layers)),
        col("stack_mode", "VARCHAR", lambda r: r.via.stack.mode.value),
        col("copper_layers", "VARCHAR[]", lambda r: r.copper_layers),
        col("geom", GEOMETRY, lambda r: _wkb(r.geom)),
    ),
)

_DRILLS: TableSpec[PcbDrill] = TableSpec(
    "drills",
    (
        col("id", "VARCHAR", lambda drill: drill.id),
        col("owner_kind", "VARCHAR", lambda drill: _drill_owner(drill)[0]),
        col("owner_id", "VARCHAR", lambda drill: _drill_owner(drill)[1]),
        col("plating", "VARCHAR", lambda drill: drill.plating.value),
        col("shape", "VARCHAR", lambda drill: drill.shape.value),
        col("x", "DOUBLE", lambda drill: drill.x),
        col("y", "DOUBLE", lambda drill: drill.y),
        col("diameter_mm", "DOUBLE", lambda drill: drill.diameter),
        col("width_mm", "DOUBLE", lambda drill: drill.width),
        col("height_mm", "DOUBLE", lambda drill: drill.height),
        col("rotation", "DOUBLE", lambda drill: drill.rotation),
        col("layers", "VARCHAR[]", lambda drill: _layer_names(drill.layers)),
        col("geom", GEOMETRY, lambda drill: _wkb(_drill_geometry(drill))),
    ),
)

_CONDUCTORS: TableSpec[_ConductorRow] = TableSpec(
    "conductors",
    (
        col("id", "VARCHAR", lambda r: r.conductor.id),
        col("kind", "VARCHAR", lambda r: r.conductor.kind.value),
        col("net_name", "VARCHAR", lambda r: r.net_name),
        col("net_number", "INTEGER", lambda r: r.net_number),
        col("layer", "VARCHAR", lambda r: r.conductor.layer.name),
        col("width_mm", "DOUBLE", lambda r: r.width),
        col("start_x", "DOUBLE", lambda r: r.start_x),
        col("start_y", "DOUBLE", lambda r: r.start_y),
        col("end_x", "DOUBLE", lambda r: r.end_x),
        col("end_y", "DOUBLE", lambda r: r.end_y),
        col("is_arc", "BOOLEAN", lambda r: r.is_arc),
        col("arc_center_x", "DOUBLE", lambda r: r.arc_center_x),
        col("arc_center_y", "DOUBLE", lambda r: r.arc_center_y),
        col("arc_angle", "DOUBLE", lambda r: r.arc_angle),
        col("length_mm", "DOUBLE", lambda r: r.length),
        col(
            "footprint_ref",
            "VARCHAR",
            lambda r: None if r.conductor.footprint is None else r.conductor.footprint.reference,
        ),
        col(
            "pour_id",
            "VARCHAR",
            lambda r: None if r.conductor.pour is None else r.conductor.pour.id,
        ),
        col("centerline", GEOMETRY, lambda r: _wkb(r.centerline)),
        col("geom", GEOMETRY, lambda r: _wkb(r.geom)),
    ),
)

_ARTWORK: TableSpec[_ArtworkRow] = TableSpec(
    "artwork",
    (
        col("id", "VARCHAR", lambda r: r.artwork.id),
        col("purpose", "VARCHAR", lambda r: r.artwork.purpose.value),
        col("content_kind", "VARCHAR", lambda r: r.artwork.kind.value),
        col(
            "footprint_ref",
            "VARCHAR",
            lambda r: None if r.artwork.footprint is None else r.artwork.footprint.reference,
        ),
        col(
            "layer", "VARCHAR", lambda r: None if r.artwork.layer is None else r.artwork.layer.name
        ),
        col("text", "VARCHAR", lambda r: r.text),
        col("x", "DOUBLE", lambda r: r.x),
        col("y", "DOUBLE", lambda r: r.y),
        col("rotation", "DOUBLE", lambda r: r.rotation),
        col("font_size", "DOUBLE", lambda r: r.font_size),
        col("geom", GEOMETRY, lambda r: _wkb(r.geom)),
    ),
)

_BOARD_PROFILE: TableSpec[PcbBoardProfileElement] = TableSpec(
    "board_profile",
    (
        col("id", "VARCHAR", lambda el: el.id),
        col("kind", "VARCHAR", lambda el: el.kind.value),
        col("layer", "VARCHAR", lambda el: None if el.layer is None else el.layer.name),
        col("is_cutout", "BOOLEAN", lambda el: el.is_cutout),
        col("geom", GEOMETRY, lambda el: _wkb(_profile_shape_geometry(el.data))),
    ),
)

_POURS: TableSpec[PcbPour] = TableSpec(
    "pours",
    (
        col("id", "VARCHAR", lambda pour: pour.id),
        col("name", "VARCHAR", lambda pour: pour.name),
        col("net_name", "VARCHAR", lambda pour: _net_fields(pour.net)[0]),
        col("net_number", "INTEGER", lambda pour: _net_fields(pour.net)[1]),
        col("primary_layer", "VARCHAR", lambda pour: _primary_layer(pour.layers)),
        col("layers", "VARCHAR[]", lambda pour: _layer_names(pour.layers)),
        col("priority", "INTEGER", lambda pour: pour.priority),
        col("fill_mode", "VARCHAR", lambda pour: pour.settings.fill_mode.value),
        col("hatch_style", "VARCHAR", lambda pour: pour.settings.hatch_style),
        col("grid_mm", "DOUBLE", lambda pour: pour.settings.grid_mm),
        col("track_width_mm", "DOUBLE", lambda pour: pour.settings.track_width_mm),
        col("min_thickness_mm", "DOUBLE", lambda pour: pour.settings.min_thickness_mm),
        col("thermal_gap_mm", "DOUBLE", lambda pour: pour.settings.thermal_gap_mm),
        col(
            "thermal_bridge_width_mm", "DOUBLE", lambda pour: pour.settings.thermal_bridge_width_mm
        ),
        col(
            "connect_pads_clearance_mm",
            "DOUBLE",
            lambda pour: pour.settings.connect_pads_clearance_mm,
        ),
        col("fill_conductor_ids", "VARCHAR[]", lambda pour: [fill.id for fill in pour.fills]),
        col(
            "footprint_ref",
            "VARCHAR",
            lambda pour: None if pour.footprint is None else pour.footprint.reference,
        ),
        col("source_format", "VARCHAR", lambda pour: pour.metadata.source_format),
        col("native_type", "VARCHAR", lambda pour: pour.metadata.native_type),
        col("native_kind", "VARCHAR", lambda pour: pour.metadata.native_kind),
        col("native_id", "VARCHAR", lambda pour: pour.metadata.native_id),
        col("native_index", "INTEGER", lambda pour: pour.metadata.native_index),
        col("metadata", "JSON", lambda pour: _metadata_json(pour.metadata)),
        col("boundary", GEOMETRY, lambda pour: _wkb(closed_path_geometry(pour.boundary))),
    ),
)

_KEEPOUTS: TableSpec[PcbKeepout] = TableSpec(
    "keepouts",
    (
        col("id", "VARCHAR", lambda ko: ko.id),
        col("name", "VARCHAR", lambda ko: ko.name),
        col(
            "footprint_ref",
            "VARCHAR",
            lambda ko: None if ko.footprint is None else ko.footprint.reference,
        ),
        col("primary_layer", "VARCHAR", lambda ko: _primary_layer(ko.layers)),
        col("layers", "VARCHAR[]", lambda ko: _layer_names(ko.layers)),
        col("tracks", "VARCHAR", lambda ko: ko.rules.tracks.value),
        col("vias", "VARCHAR", lambda ko: ko.rules.vias.value),
        col("pads", "VARCHAR", lambda ko: ko.rules.pads.value),
        col("copper_pours", "VARCHAR", lambda ko: ko.rules.copper_pours.value),
        col("footprints", "VARCHAR", lambda ko: ko.rules.footprints.value),
        col("source_format", "VARCHAR", lambda ko: ko.metadata.source_format),
        col("native_type", "VARCHAR", lambda ko: ko.metadata.native_type),
        col("native_kind", "VARCHAR", lambda ko: ko.metadata.native_kind),
        col("native_id", "VARCHAR", lambda ko: ko.metadata.native_id),
        col("native_index", "INTEGER", lambda ko: ko.metadata.native_index),
        col("metadata", "JSON", lambda ko: _metadata_json(ko.metadata)),
        col("boundary", GEOMETRY, lambda ko: _wkb(closed_path_geometry(ko.boundary))),
    ),
)

_LAYERS: TableSpec[_LayerRow] = TableSpec(
    "layers",
    (
        col("position", "INTEGER", lambda r: r.position),
        col("name", "VARCHAR", lambda r: r.name),
        col("roles", "VARCHAR[]", lambda r: r.roles),
        col("side", "VARCHAR", lambda r: r.side),
        col("number", "INTEGER", lambda r: r.number),
        col("thickness_mm", "DOUBLE", lambda r: r.thickness_mm),
        col("material", "VARCHAR", lambda r: r.material),
        col("epsilon_r", "DOUBLE", lambda r: r.epsilon_r),
        col("loss_tangent", "DOUBLE", lambda r: r.loss_tangent),
        col("layer_type", "VARCHAR", lambda r: r.layer_type),
        col("copper_orientation", "VARCHAR", lambda r: r.copper_orientation),
    ),
)

_BOARD: TableSpec[_BoardRow] = TableSpec(
    "board",
    (
        col("name", "VARCHAR", lambda r: r.board.name),
        col(
            "total_thickness_mm",
            "DOUBLE",
            lambda r: r.board.stackup.total_thickness_mm if r.board.stackup else None,
        ),
        col(
            "copper_finish",
            "VARCHAR",
            lambda r: r.board.stackup.copper_finish if r.board.stackup else None,
        ),
        col("layer_count", "INTEGER", lambda r: _copper_layer_count(r.board)),
        col("geom", GEOMETRY, lambda r: _wkb(r.geom)),
    ),
)

_BOARDS: TableSpec[_BoardsRow] = TableSpec(
    "boards",
    (
        col("board_id", "VARCHAR", lambda r: r.board_id, constraint="PRIMARY KEY"),
        col("name", "VARCHAR", lambda r: r.board.name),
        col("source_path", "VARCHAR", lambda r: _null_if_unset(r.board.source_path)),
        col("layer_count", "INTEGER", lambda r: _copper_layer_count(r.board)),
        col(
            "total_thickness_mm",
            "DOUBLE",
            lambda r: r.board.stackup.total_thickness_mm if r.board.stackup else None,
        ),
    ),
)

_NET_CLASSES: TableSpec[NetClass] = TableSpec(
    "net_classes",
    (
        col("name", "VARCHAR", lambda nc: nc.name),
        col("kind", "INTEGER", lambda nc: nc.kind),
        col("trace_width_mm", "DOUBLE", lambda nc: _null_if_unset(nc.trace_width_mm)),
        col("clearance_mm", "DOUBLE", lambda nc: _null_if_unset(nc.clearance_mm)),
        col("via_diameter_mm", "DOUBLE", lambda nc: _null_if_unset(nc.via_diameter_mm)),
        col("via_drill_mm", "DOUBLE", lambda nc: _null_if_unset(nc.via_drill_mm)),
        col("diff_pair_width_mm", "DOUBLE", lambda nc: _null_if_unset(nc.diff_pair_width_mm)),
        col("diff_pair_gap_mm", "DOUBLE", lambda nc: _null_if_unset(nc.diff_pair_gap_mm)),
        col("properties", "JSON", lambda nc: _json_or_null(nc.properties)),
    ),
)

_NET_CLASS_MEMBERS: TableSpec[tuple[str, str]] = TableSpec(
    "net_class_members",
    (
        col("net_name", "VARCHAR", lambda r: r[0]),
        col("net_class", "VARCHAR", lambda r: r[1]),
    ),
)

_DESIGN_RULES: TableSpec[DesignRule] = TableSpec(
    "design_rules",
    (
        col("name", "VARCHAR", lambda rule: rule.name),
        col("kind", "VARCHAR", lambda rule: rule.kind),
        col("enabled", "BOOLEAN", lambda rule: rule.enabled),
        col("priority", "INTEGER", lambda rule: rule.priority),
        col("scope1", "VARCHAR", lambda rule: _null_if_unset(rule.scope1)),
        col("scope2", "VARCHAR", lambda rule: _null_if_unset(rule.scope2)),
        col("layer_scope", "VARCHAR", lambda rule: _null_if_unset(rule.layer_scope)),
        col("min_value_mm", "DOUBLE", lambda rule: rule.min_value_mm),
        col("max_value_mm", "DOUBLE", lambda rule: rule.max_value_mm),
        col("preferred_value_mm", "DOUBLE", lambda rule: rule.preferred_value_mm),
        col("properties", "JSON", lambda rule: _json_or_null(rule.properties)),
    ),
)

_DIFF_PAIRS: TableSpec[DiffPair] = TableSpec(
    "diff_pairs",
    (
        col("name", "VARCHAR", lambda pair: pair.name),
        col("positive_net", "VARCHAR", lambda pair: pair.positive_net),
        col("negative_net", "VARCHAR", lambda pair: pair.negative_net),
        col("properties", "JSON", lambda pair: _json_or_null(pair.properties)),
    ),
)

_COMPONENTS: TableSpec[Component] = TableSpec(
    "components",
    (
        col("component_id", "VARCHAR", lambda c: c.id, constraint="PRIMARY KEY"),
        col("reference", "VARCHAR", lambda c: c.reference, constraint="NOT NULL"),
        col("part", "VARCHAR", lambda c: c.part, constraint="NOT NULL"),
        col("description", "VARCHAR", lambda c: c.description, constraint="NOT NULL"),
        col("kind", "VARCHAR", lambda c: c.kind.value, constraint="NOT NULL"),
        col("dnp", "BOOLEAN", lambda c: c.dnp, constraint="NOT NULL"),
        col("dnp_source", "VARCHAR", lambda c: c.dnp_source.value if c.dnp_source else None),
        col("exclude_from_bom", "BOOLEAN", lambda c: c.exclude_from_bom, constraint="NOT NULL"),
        col(
            "exclude_from_simulation",
            "BOOLEAN",
            lambda c: c.exclude_from_simulation,
            constraint="NOT NULL",
        ),
        col("datasheet", "VARCHAR", lambda c: _null_if_unset(c.datasheet)),
        col("lib_symbol", "VARCHAR", lambda c: _null_if_unset(c.lib.symbol) if c.lib else None),
        col("lib_library", "VARCHAR", lambda c: _null_if_unset(c.lib.library) if c.lib else None),
        col(
            "lib_design_item_id",
            "VARCHAR",
            lambda c: _null_if_unset(c.lib.design_item_id) if c.lib else None,
        ),
        col("page_ids", "VARCHAR", lambda c: _page_ids(c.pages)),
        col("page_names", "VARCHAR", lambda c: _page_names(c.pages)),
    ),
)

_COMPONENT_PARAMETERS: TableSpec[_ComponentParameterRow] = TableSpec(
    "component_parameters",
    (
        col("component_id", "VARCHAR", lambda r: r.component_id, constraint="NOT NULL"),
        col("ord", "INTEGER", lambda r: r.ord, constraint="NOT NULL"),
        col("name", "VARCHAR", lambda r: r.parameter.name, constraint="NOT NULL"),
        col("value", "VARCHAR", lambda r: r.parameter.value, constraint="NOT NULL"),
        col("visible", "BOOLEAN", lambda r: r.parameter.visible, constraint="NOT NULL"),
    ),
)

_COMPONENT_FOOTPRINTS: TableSpec[_ComponentFootprintRow] = TableSpec(
    "component_footprints",
    (
        col("component_id", "VARCHAR", lambda r: r.component_id, constraint="NOT NULL"),
        col("ord", "INTEGER", lambda r: r.ord, constraint="NOT NULL"),
        col("name", "VARCHAR", lambda r: r.model.name, constraint="NOT NULL"),
        col("library", "VARCHAR", lambda r: _null_if_unset(r.model.library)),
        col("is_current", "BOOLEAN", lambda r: r.model.is_current, constraint="NOT NULL"),
        col("description", "VARCHAR", lambda r: _null_if_unset(r.model.description)),
    ),
)

_COMPONENT_PART_NUMBERS: TableSpec[_ComponentPartNumberRow] = TableSpec(
    "component_part_numbers",
    (
        col("component_id", "VARCHAR", lambda r: r.component_id, constraint="NOT NULL"),
        col("ord", "INTEGER", lambda r: r.ord, constraint="NOT NULL"),
        col("manufacturer", "VARCHAR", lambda r: _null_if_unset(r.part_number.manufacturer)),
        col("number", "VARCHAR", lambda r: r.part_number.number, constraint="NOT NULL"),
    ),
)

_COMPONENT_OCCURRENCES: TableSpec[ComponentOccurrence] = TableSpec(
    "component_occurrences",
    (
        col("occurrence_id", "VARCHAR", lambda o: o.id, constraint="PRIMARY KEY"),
        col("component_id", "VARCHAR", lambda o: o.component.id, constraint="NOT NULL"),
        col("reference", "VARCHAR", lambda o: o.component.reference, constraint="NOT NULL"),
        col("page_id", "VARCHAR", lambda o: o.page.id, constraint="NOT NULL"),
        col("page_name", "VARCHAR", lambda o: o.page.name, constraint="NOT NULL"),
        col("scope_path", "VARCHAR", lambda o: str(o.scope_id), constraint="NOT NULL"),
        col("source_id", "VARCHAR", lambda o: o.source_id, constraint="NOT NULL"),
        col("part_id", "VARCHAR", lambda o: _null_if_unset(o.part_id)),
        col("x", "DOUBLE", lambda o: o.x),
        col("y", "DOUBLE", lambda o: o.y),
        col("rotation", "DOUBLE", lambda o: o.rotation),
        col("mirror", "BOOLEAN", lambda o: o.mirror),
        col("physical_designator", "VARCHAR", lambda o: _null_if_unset(o.physical_designator)),
    ),
)

_COMPONENT_PAGES: TableSpec[_CompPageRow] = TableSpec(
    "component_pages",
    (
        col("component_id", "VARCHAR", lambda r: r.component.id, constraint="NOT NULL"),
        col("reference", "VARCHAR", lambda r: r.component.reference, constraint="NOT NULL"),
        col("page_id", "VARCHAR", lambda r: r.page.id, constraint="NOT NULL"),
        col("page_name", "VARCHAR", lambda r: r.page.name, constraint="NOT NULL"),
    ),
)

_COMPONENT_METADATA: TableSpec[_KeyValueRow] = TableSpec(
    "component_metadata",
    (
        col("component_id", "VARCHAR", lambda r: r.owner_id, constraint="NOT NULL"),
        col("reference", "VARCHAR", lambda r: r.ref, constraint="NOT NULL"),
        col("key", "VARCHAR", lambda r: r.key, constraint="NOT NULL"),
        col("value", "VARCHAR", lambda r: r.value, constraint="NOT NULL"),
    ),
)

_COMPONENT_OCCURRENCE_METADATA: TableSpec[_OccKeyValueRow] = TableSpec(
    "component_occurrence_metadata",
    (
        col("occurrence_id", "VARCHAR", lambda r: r.occurrence_id, constraint="NOT NULL"),
        col("component_id", "VARCHAR", lambda r: r.owner_id, constraint="NOT NULL"),
        col("key", "VARCHAR", lambda r: r.key, constraint="NOT NULL"),
        col("value", "VARCHAR", lambda r: r.value, constraint="NOT NULL"),
    ),
)

_PINS: TableSpec[Pin] = TableSpec(
    "pins",
    (
        col("pin_id", "VARCHAR", lambda pin: pin.id, constraint="PRIMARY KEY"),
        col("component_id", "VARCHAR", lambda pin: pin.component.id, constraint="NOT NULL"),
        col("reference", "VARCHAR", lambda pin: pin.component.reference, constraint="NOT NULL"),
        col("designator", "VARCHAR", lambda pin: pin.designator, constraint="NOT NULL"),
        col("name", "VARCHAR", lambda pin: pin.name, constraint="NOT NULL"),
        col("net_id", "VARCHAR", lambda pin: pin.net.id if pin.net else None),
        col("net_name", "VARCHAR", lambda pin: pin.net.name if pin.net else None),
        col("electrical", "VARCHAR", lambda pin: pin.metadata.get(ELECTRICAL_KEY)),
        col("no_connect", "BOOLEAN", lambda pin: pin.no_connect, constraint="NOT NULL"),
    ),
)

_PIN_OCCURRENCES: TableSpec[PinOccurrence] = TableSpec(
    "pin_occurrences",
    (
        col("occurrence_id", "VARCHAR", lambda o: o.id, constraint="PRIMARY KEY"),
        col("pin_id", "VARCHAR", lambda o: o.pin.id, constraint="NOT NULL"),
        col("component_id", "VARCHAR", lambda o: o.pin.component.id, constraint="NOT NULL"),
        col("reference", "VARCHAR", lambda o: o.pin.component.reference, constraint="NOT NULL"),
        col("designator", "VARCHAR", lambda o: o.pin.designator, constraint="NOT NULL"),
        col("page_id", "VARCHAR", lambda o: o.page.id, constraint="NOT NULL"),
        col("page_name", "VARCHAR", lambda o: o.page.name, constraint="NOT NULL"),
        col("scope_path", "VARCHAR", lambda o: str(o.scope_id), constraint="NOT NULL"),
        col("source_id", "VARCHAR", lambda o: o.source_id, constraint="NOT NULL"),
    ),
)

_PIN_OCCURRENCE_METADATA: TableSpec[_OccKeyValueRow] = TableSpec(
    "pin_occurrence_metadata",
    (
        col("occurrence_id", "VARCHAR", lambda r: r.occurrence_id, constraint="NOT NULL"),
        col("pin_id", "VARCHAR", lambda r: r.owner_id, constraint="NOT NULL"),
        col("key", "VARCHAR", lambda r: r.key, constraint="NOT NULL"),
        col("value", "VARCHAR", lambda r: r.value, constraint="NOT NULL"),
    ),
)

_NETS: TableSpec[_NetRow] = TableSpec(
    "nets",
    (
        col("net_id", "VARCHAR", lambda r: r.net.id, constraint="PRIMARY KEY"),
        col("name", "VARCHAR", lambda r: r.net.name, constraint="NOT NULL"),
        col("pin_count", "INTEGER", lambda r: len(r.net.pins), constraint="NOT NULL"),
        col("page_ids", "VARCHAR", lambda r: _page_ids(r.net.pages)),
        col("page_names", "VARCHAR", lambda r: _page_names(r.net.pages)),
        col("is_power", "BOOLEAN", lambda r: r.is_power, constraint="NOT NULL"),
        col("net_class", "VARCHAR", lambda r: r.net_class),
        col("diff_pair", "VARCHAR", lambda r: r.diff_pair),
        col("diff_pair_polarity", "VARCHAR", lambda r: r.diff_pair_polarity),
        col("aliases", "VARCHAR", lambda r: _csv(r.net.aliases)),
    ),
)

_NET_PAGES: TableSpec[_NetPageRow] = TableSpec(
    "net_pages",
    (
        col("net_id", "VARCHAR", lambda r: r.net.id, constraint="NOT NULL"),
        col("name", "VARCHAR", lambda r: r.net.name, constraint="NOT NULL"),
        col("page_id", "VARCHAR", lambda r: r.page.id, constraint="NOT NULL"),
        col("page_name", "VARCHAR", lambda r: r.page.name, constraint="NOT NULL"),
    ),
)

_NET_ALIASES: TableSpec[_AliasRow] = TableSpec(
    "net_aliases",
    (
        col("net_id", "VARCHAR", lambda r: r.net_id, constraint="NOT NULL"),
        col("name", "VARCHAR", lambda r: r.net_name, constraint="NOT NULL"),
        col("alias", "VARCHAR", lambda r: r.alias, constraint="NOT NULL"),
    ),
)

_NET_OCCURRENCES: TableSpec[NetOccurrence] = TableSpec(
    "net_occurrences",
    (
        col("occurrence_id", "VARCHAR", lambda o: o.id, constraint="PRIMARY KEY"),
        col("net_id", "VARCHAR", lambda o: o.net.id, constraint="NOT NULL"),
        col("name", "VARCHAR", lambda o: o.net.name, constraint="NOT NULL"),
        col("page_id", "VARCHAR", lambda o: o.page.id, constraint="NOT NULL"),
        col("page_name", "VARCHAR", lambda o: o.page.name, constraint="NOT NULL"),
        col("scope_path", "VARCHAR", lambda o: str(o.scope_id), constraint="NOT NULL"),
        col(
            "source_local_net_id", "VARCHAR", lambda o: o.source_local_net_id, constraint="NOT NULL"
        ),
        col("source_names", "VARCHAR", lambda o: _csv(o.source_names)),
    ),
)

_NET_OCCURRENCE_SOURCE_NAMES: TableSpec[_SourceNameRow] = TableSpec(
    "net_occurrence_source_names",
    (
        col("occurrence_id", "VARCHAR", lambda r: r.occurrence_id, constraint="NOT NULL"),
        col("net_id", "VARCHAR", lambda r: r.net_id, constraint="NOT NULL"),
        col("source_name", "VARCHAR", lambda r: r.source_name, constraint="NOT NULL"),
    ),
)

_SCHEMATIC_DIRECTIVES: TableSpec[_SchematicDirectiveRow] = TableSpec(
    "schematic_directives",
    (
        col(
            "directive_id",
            "VARCHAR",
            lambda r: f"{r.occurrence.id}:directive:{r.ord:04d}",
            constraint="PRIMARY KEY",
        ),
        col("net_id", "VARCHAR", lambda r: r.occurrence.net.id, constraint="NOT NULL"),
        col("net_name", "VARCHAR", lambda r: r.occurrence.net.name, constraint="NOT NULL"),
        col("occurrence_id", "VARCHAR", lambda r: r.occurrence.id, constraint="NOT NULL"),
        col("page_id", "VARCHAR", lambda r: r.occurrence.page.id, constraint="NOT NULL"),
        col("scope_path", "VARCHAR", lambda r: str(r.occurrence.scope_id), constraint="NOT NULL"),
        col("kind", "VARCHAR", lambda r: r.directive.kind.value, constraint="NOT NULL"),
        col("value", "VARCHAR", lambda r: r.directive.value, constraint="NOT NULL"),
        col("source", "VARCHAR", lambda r: r.directive.source, constraint="NOT NULL"),
        col("source_id", "VARCHAR", lambda r: _null_if_unset(r.directive.source_id)),
        col("native_name", "VARCHAR", lambda r: _null_if_unset(r.directive.native_name)),
        col("x", "DOUBLE", lambda r: r.directive.x),
        col("y", "DOUBLE", lambda r: r.directive.y),
        col("metadata", "JSON", lambda r: _json_or_null(r.directive.metadata)),
    ),
)

_NET_METADATA: TableSpec[_KeyValueRow] = TableSpec(
    "net_metadata",
    (
        col("net_id", "VARCHAR", lambda r: r.owner_id, constraint="NOT NULL"),
        col("name", "VARCHAR", lambda r: r.ref, constraint="NOT NULL"),
        col("key", "VARCHAR", lambda r: r.key, constraint="NOT NULL"),
        col("value", "VARCHAR", lambda r: r.value, constraint="NOT NULL"),
    ),
)

_NET_OCCURRENCE_METADATA: TableSpec[_OccKeyValueRow] = TableSpec(
    "net_occurrence_metadata",
    (
        col("occurrence_id", "VARCHAR", lambda r: r.occurrence_id, constraint="NOT NULL"),
        col("net_id", "VARCHAR", lambda r: r.owner_id, constraint="NOT NULL"),
        col("key", "VARCHAR", lambda r: r.key, constraint="NOT NULL"),
        col("value", "VARCHAR", lambda r: r.value, constraint="NOT NULL"),
    ),
)

_BUSES: TableSpec[Bus] = TableSpec(
    "buses",
    (
        col("bus_id", "VARCHAR", lambda bus: bus.id, constraint="PRIMARY KEY"),
        col("name", "VARCHAR", lambda bus: bus.name, constraint="NOT NULL"),
        col("kind", "VARCHAR", lambda bus: bus.kind.value, constraint="NOT NULL"),
        col("member_count", "INTEGER", lambda bus: len(bus.members), constraint="NOT NULL"),
        col("members", "VARCHAR", lambda bus: _ordered_csv(member.name for member in bus.members)),
        col("metadata", "JSON", lambda bus: _json_or_null(bus.metadata)),
    ),
)

_BUS_MEMBERS: TableSpec[_BusMemberRow] = TableSpec(
    "bus_members",
    (
        col("bus_id", "VARCHAR", lambda r: r.bus.id, constraint="NOT NULL"),
        col("name", "VARCHAR", lambda r: r.bus.name, constraint="NOT NULL"),
        col("kind", "VARCHAR", lambda r: r.bus.kind.value, constraint="NOT NULL"),
        col("net_id", "VARCHAR", lambda r: r.net.id, constraint="NOT NULL"),
        col("net_name", "VARCHAR", lambda r: r.net.name, constraint="NOT NULL"),
        col("ord", "INTEGER", lambda r: r.ord, constraint="NOT NULL"),
    ),
)

_PAGES: TableSpec[Page] = TableSpec(
    "pages",
    (
        col("page_id", "VARCHAR", lambda page: page.id, constraint="PRIMARY KEY"),
        col("name", "VARCHAR", lambda page: page.name, constraint="NOT NULL"),
        col("source_file", "VARCHAR", lambda page: _null_if_unset(page.source_file)),
        col("scope_path", "VARCHAR", _scope_path, constraint="NOT NULL"),
        col("component_count", "INTEGER", lambda page: len(page.components), constraint="NOT NULL"),
        col("net_count", "INTEGER", lambda page: len(page.nets), constraint="NOT NULL"),
    ),
)

_PAGE_ANNOTATIONS: TableSpec[_PageAnnotationRow] = TableSpec(
    "page_annotations",
    (
        col(
            "annotation_id",
            "VARCHAR",
            lambda r: f"{r.page.id}:annotation:{r.ord:04d}",
            constraint="PRIMARY KEY",
        ),
        col("page_id", "VARCHAR", lambda r: r.page.id, constraint="NOT NULL"),
        col("page_name", "VARCHAR", lambda r: r.page.name, constraint="NOT NULL"),
        col("ord", "INTEGER", lambda r: r.ord, constraint="NOT NULL"),
        col("text", "VARCHAR", lambda r: r.text, constraint="NOT NULL"),
    ),
)

_TITLE_BLOCKS: TableSpec[_TitleBlockRow] = TableSpec(
    "title_blocks",
    (
        col("page_id", "VARCHAR", lambda r: r.page.id, constraint="PRIMARY KEY"),
        col("title", "VARCHAR", lambda r: _null_if_unset(r.block.title)),
        col("revision", "VARCHAR", lambda r: _null_if_unset(r.block.revision)),
        col("date", "VARCHAR", lambda r: _null_if_unset(r.block.date)),
        col("organization", "VARCHAR", lambda r: _null_if_unset(r.block.organization)),
        col("org_address", "VARCHAR", lambda r: _null_if_unset(r.block.org_address)),
        col("document_number", "VARCHAR", lambda r: _null_if_unset(r.block.document_number)),
        col("sheet_number", "VARCHAR", lambda r: _null_if_unset(r.block.sheet_number)),
        col("sheet_total", "VARCHAR", lambda r: _null_if_unset(r.block.sheet_total)),
        col("author", "VARCHAR", lambda r: _null_if_unset(r.block.author)),
        col("drawn_by", "VARCHAR", lambda r: _null_if_unset(r.block.drawn_by)),
        col("checked_by", "VARCHAR", lambda r: _null_if_unset(r.block.checked_by)),
        col("approved_by", "VARCHAR", lambda r: _null_if_unset(r.block.approved_by)),
        col("created_date", "VARCHAR", lambda r: _null_if_unset(r.block.created_date)),
        col("modified_date", "VARCHAR", lambda r: _null_if_unset(r.block.modified_date)),
        col("cage_code", "VARCHAR", lambda r: _null_if_unset(r.block.cage_code)),
        col("comments", "JSON", lambda r: _json_or_null(r.block.comments)),
        col("metadata", "JSON", lambda r: _json_or_null(r.block.metadata)),
    ),
)

_PROJECT: TableSpec[tuple[str, str]] = TableSpec(
    "project",
    (
        col("key", "VARCHAR", lambda r: r[0]),
        col("value", "VARCHAR", lambda r: r[1]),
    ),
)

_PROJECT_DOCUMENTS: TableSpec[ProjectDocument] = TableSpec(
    "project_documents",
    (
        col(
            "document_id",
            "VARCHAR",
            lambda doc: f"document:{doc.order:04d}",
            constraint="PRIMARY KEY",
        ),
        col("path", "VARCHAR", lambda doc: doc.path, constraint="NOT NULL"),
        col("kind", "VARCHAR", lambda doc: doc.kind.value, constraint="NOT NULL"),
        col("native_kind", "VARCHAR", lambda doc: doc.native_kind),
        col("description", "VARCHAR", lambda doc: doc.description),
        col("unique_id", "VARCHAR", lambda doc: doc.unique_id),
        col("ord", "INTEGER", lambda doc: doc.order, constraint="NOT NULL"),
        col("exists", "BOOLEAN", lambda doc: doc.exists, constraint="NOT NULL"),
        col("parsed", "BOOLEAN", lambda doc: doc.parsed, constraint="NOT NULL"),
        col("metadata", "JSON", lambda doc: _json_or_null(doc.metadata)),
    ),
)

_PROJECT_PARAMETERS: TableSpec[tuple[str, str]] = TableSpec(
    "project_parameters",
    (
        col("key", "VARCHAR", lambda r: r[0], constraint="NOT NULL"),
        col("value", "VARCHAR", lambda r: r[1], constraint="NOT NULL"),
    ),
)

_PROJECT_VARIANTS: TableSpec[Variant] = TableSpec(
    "project_variants",
    (
        col("variant_name", "VARCHAR", lambda v: v.name, constraint="PRIMARY KEY"),
        col("description", "VARCHAR", lambda v: v.description),
        col("ord", "INTEGER", lambda v: v.order, constraint="NOT NULL"),
        col(
            "active",
            "BOOLEAN",
            lambda v: any(override.applied for override in v.overrides),
            constraint="NOT NULL",
        ),
        col("override_count", "INTEGER", lambda v: len(v.overrides), constraint="NOT NULL"),
        col(
            "not_fitted_count",
            "INTEGER",
            lambda v: sum(1 for o in v.overrides if o.field.value == "fitted" and o.value is False),
            constraint="NOT NULL",
        ),
        col(
            "alternate_part_count",
            "INTEGER",
            lambda v: sum(1 for o in v.overrides if o.field.value == "alternate_part"),
            constraint="NOT NULL",
        ),
        col(
            "parameter_count",
            "INTEGER",
            lambda v: sum(1 for o in v.overrides if o.field.value == "parameter"),
            constraint="NOT NULL",
        ),
        col("source_id", "VARCHAR", lambda v: _null_if_unset(v.source_id)),
        col("metadata", "JSON", lambda v: _json_or_null(v.metadata)),
    ),
)

_VARIANT_OVERRIDES: TableSpec[_VariantOverrideRow] = TableSpec(
    "variant_overrides",
    (
        col("variant_name", "VARCHAR", lambda r: r.variant_name, constraint="NOT NULL"),
        col("ord", "INTEGER", lambda r: r.ord, constraint="NOT NULL"),
        col(
            "target_kind", "VARCHAR", lambda r: r.override.target.kind.value, constraint="NOT NULL"
        ),
        col("target_object_id", "VARCHAR", lambda r: _null_if_unset(r.override.target.object_id)),
        col("target_reference", "VARCHAR", lambda r: _null_if_unset(r.override.target.reference)),
        col(
            "target_occurrence_id",
            "VARCHAR",
            lambda r: _null_if_unset(r.override.target.occurrence_id),
        ),
        col("target_source_id", "VARCHAR", lambda r: _null_if_unset(r.override.target.source_id)),
        col("scope_path", "VARCHAR", lambda r: _null_if_unset(r.override.target.scope_path)),
        col("field", "VARCHAR", lambda r: r.override.field.value, constraint="NOT NULL"),
        col(
            "parameter_name", "VARCHAR", lambda r: _null_if_unset(r.override.target.parameter_name)
        ),
        col("value", "JSON", lambda r: _variant_value_json(r.override.value)),
        col("base_value", "JSON", lambda r: _variant_value_json(r.override.base_value)),
        col("native_kind", "VARCHAR", lambda r: _null_if_unset(r.override.native_kind)),
        col("source_id", "VARCHAR", lambda r: _null_if_unset(r.override.source_id)),
        col("applied", "BOOLEAN", lambda r: r.override.applied, constraint="NOT NULL"),
        col("metadata", "JSON", lambda r: _json_or_null(r.override.metadata)),
    ),
)


# Table creation order. Only ``name``/``create_ddl()`` are read here — neither
# depends on a spec's row type — so the specs can stay heterogeneously typed.
_ORDERED_SPECS = (
    _FOOTPRINTS,
    _PADS,
    _VIAS,
    _DRILLS,
    _CONDUCTORS,
    _ARTWORK,
    _BOARD_PROFILE,
    _POURS,
    _KEEPOUTS,
    _LAYERS,
    _BOARD,
    _BOARDS,
    _NET_CLASSES,
    _NET_CLASS_MEMBERS,
    _DESIGN_RULES,
    _DIFF_PAIRS,
    _COMPONENTS,
    _COMPONENT_PARAMETERS,
    _COMPONENT_FOOTPRINTS,
    _COMPONENT_PART_NUMBERS,
    _COMPONENT_OCCURRENCES,
    _COMPONENT_PAGES,
    _COMPONENT_METADATA,
    _COMPONENT_OCCURRENCE_METADATA,
    _PINS,
    _PIN_OCCURRENCES,
    _PIN_OCCURRENCE_METADATA,
    _NETS,
    _NET_PAGES,
    _NET_ALIASES,
    _NET_OCCURRENCES,
    _NET_OCCURRENCE_SOURCE_NAMES,
    _SCHEMATIC_DIRECTIVES,
    _NET_METADATA,
    _NET_OCCURRENCE_METADATA,
    _BUSES,
    _BUS_MEMBERS,
    _PAGES,
    _PAGE_ANNOTATIONS,
    _TITLE_BLOCKS,
    _PROJECT_DOCUMENTS,
    _PROJECT_PARAMETERS,
    _PROJECT_VARIANTS,
    _VARIANT_OVERRIDES,
    _PROJECT,
)

TABLE_DDL: dict[str, str] = {spec.name: spec.create_ddl() for spec in _ORDERED_SPECS}


def create_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Install spatial extension and create all tables."""
    _ = con.execute("INSTALL spatial")
    _ = con.execute("LOAD spatial")
    for ddl in TABLE_DDL.values():
        _ = con.execute(ddl)
    for ddl in INDEX_DDL.values():
        _ = con.execute(ddl)


def schema_text() -> str:
    """Return formatted DDL for all tables, indexes, and views."""
    lines: list[str] = ["-- Tables\n"]
    _append_ddl_block(lines, TABLE_DDL)
    lines.append("-- Indexes\n")
    _append_ddl_block(lines, INDEX_DDL)
    lines.append("-- Views\n")
    _append_ddl_block(lines, VIEW_DDL)
    return "\n".join(lines)


def _append_ddl_block(lines: list[str], ddls: dict[str, str]) -> None:
    for name, ddl in ddls.items():
        cleaned = "\n".join(line.strip() for line in ddl.strip().splitlines())
        lines.append(f"-- {name}")
        lines.append(cleaned)
        lines.append("")


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_database(project: Project) -> duckdb.DuckDBPyConnection:
    """Create an in-memory DuckDB with spatial extension and load project data."""
    con = duckdb.connect(":memory:")
    create_tables(con)

    # Geometry tables describe the primary board; the boards table lists all.
    board = project.board
    if board:
        _load_footprints(con, board)
        _load_pads(con, board)
        _load_vias(con, board)
        _load_drills(con, board)
        _load_conductors(con, board)
        _load_artwork(con, board)
        _load_board_profile(con, board)
        _load_pours(con, board)
        _load_keepouts(con, board)
        _load_layers(con, board)
        _load_board(con, board)
    _load_boards(con, project)

    _load_net_classes(con, project)
    _load_design_rules(con, project)
    _load_diff_pairs(con, project)

    if project.schematic:
        _load_pages(con, project.schematic)
        _load_page_annotations(con, project.schematic)
        _load_title_blocks(con, project.schematic)
        _load_components(con, project.schematic)
        _load_component_enrichment(con, project.schematic)
        _load_component_pages(con, project.schematic)
        _load_component_occurrences(con, project.schematic)
        _load_component_occurrence_metadata(con, project.schematic)
        _load_component_metadata(con, project.schematic)
        _load_nets(con, project.schematic, project)
        _load_net_pages(con, project.schematic)
        _load_net_aliases(con, project.schematic)
        _load_net_occurrences(con, project.schematic)
        _load_net_occurrence_source_names(con, project.schematic)
        _load_schematic_directives(con, project.schematic)
        _load_net_occurrence_metadata(con, project.schematic)
        _load_net_metadata(con, project.schematic)
        _load_buses(con, project.schematic)
        _load_pins(con, project.schematic)
        _load_pin_occurrences(con, project.schematic)

    _load_project_documents(con, project)
    _load_project_parameters(con, project)
    _load_project_variants(con, project)
    _load_project_metadata(con, project)

    create_views(con)
    return con


def _load_footprints(con: duckdb.DuckDBPyConnection, pcb: Board) -> None:
    _FOOTPRINTS.bulk_insert(con, pcb.footprints)


def _load_pads(con: duckdb.DuckDBPyConnection, pcb: Board) -> None:
    _PADS.bulk_insert(
        con,
        (_PadRow(pad=pad, copper_layers=copper_layers(pad, pcb)) for pad in pcb.pads),
    )


def _load_vias(con: duckdb.DuckDBPyConnection, pcb: Board) -> None:
    rows: list[_ViaRow] = []
    for via in pcb.vias:
        net_name, net_number = _net_fields(via.net)
        start_layer = via.layers[0].name if via.layers else ""
        end_layer = via.layers[-1].name if len(via.layers) > 1 else start_layer
        rows.append(
            _ViaRow(
                via=via,
                net_name=net_name,
                net_number=net_number,
                start_layer=start_layer,
                end_layer=end_layer,
                copper_layers=copper_layers(via, pcb),
                geom=via_geometry(via)[0],
            ),
        )
    _VIAS.bulk_insert(con, rows)


def _load_drills(con: duckdb.DuckDBPyConnection, pcb: Board) -> None:
    _DRILLS.bulk_insert(con, pcb.drills)


def _conductor_row(conductor: PcbConductor) -> _ConductorRow:
    net_name, net_number = _net_fields(conductor.net)
    data = conductor.data
    if isinstance(data, PcbLine):
        centerline, geom = segment_geometry(data)
        return _ConductorRow(
            conductor=conductor,
            net_name=net_name,
            net_number=net_number,
            width=data.width,
            start_x=data.start_x,
            start_y=data.start_y,
            end_x=data.end_x,
            end_y=data.end_y,
            is_arc=False,
            arc_center_x=None,
            arc_center_y=None,
            arc_angle=None,
            length=centerline.length,
            centerline=centerline,
            geom=geom,
        )
    if isinstance(data, PcbArc):
        centerline, geom = trace_arc_geometry(data)
        arc_center_x, arc_center_y, _radius = arc_center_from_three_points(
            data.start_x, data.start_y, data.mid_x, data.mid_y, data.end_x, data.end_y
        )
        arc_angle = arc_sweep_angle(
            data.start_x,
            data.start_y,
            data.mid_x,
            data.mid_y,
            data.end_x,
            data.end_y,
            arc_center_x,
            arc_center_y,
        )
        return _ConductorRow(
            conductor=conductor,
            net_name=net_name,
            net_number=net_number,
            width=data.width,
            start_x=data.start_x,
            start_y=data.start_y,
            end_x=data.end_x,
            end_y=data.end_y,
            is_arc=True,
            arc_center_x=arc_center_x,
            arc_center_y=arc_center_y,
            arc_angle=arc_angle,
            length=centerline.length,
            centerline=centerline,
            geom=geom,
        )
    geom = _shape_geometry(data)
    if geom is None:
        msg = f"unsupported conductor payload type {type(data).__name__}"
        raise TypeError(msg)
    return _ConductorRow(
        conductor=conductor,
        net_name=net_name,
        net_number=net_number,
        width=None,
        start_x=None,
        start_y=None,
        end_x=None,
        end_y=None,
        is_arc=False,
        arc_center_x=None,
        arc_center_y=None,
        arc_angle=None,
        length=0.0,
        centerline=None,
        geom=geom,
    )


def _load_conductors(con: duckdb.DuckDBPyConnection, pcb: Board) -> None:
    _CONDUCTORS.bulk_insert(con, (_conductor_row(conductor) for conductor in pcb.conductors))


def _load_artwork(con: duckdb.DuckDBPyConnection, pcb: Board) -> None:
    rows: list[list[object]] = []
    line_row_indexes: list[int] = []
    line_payloads: list[PcbLine] = []
    for artwork in pcb.artwork:
        text = x = y = rotation = font_size = None
        geom_wkb = None
        if isinstance(artwork.data, PcbText):
            text = artwork.data.text
            x = artwork.data.x
            y = artwork.data.y
            rotation = artwork.data.rotation
            font_size = artwork.data.font_size
            geom_wkb = _wkb(_shape_geometry(artwork.data))
        elif isinstance(artwork.data, PcbLine):
            line_row_indexes.append(len(rows))
            line_payloads.append(artwork.data)
        else:
            geom_wkb = _wkb(_shape_geometry(artwork.data))
        rows.append(
            [
                artwork.id,
                artwork.purpose.value,
                artwork.kind.value,
                None if artwork.footprint is None else artwork.footprint.reference,
                None if artwork.layer is None else artwork.layer.name,
                text,
                x,
                y,
                rotation,
                font_size,
                geom_wkb,
            ]
        )
    if not rows:
        return

    for row_index, geom_wkb in zip(
        line_row_indexes,
        _line_artwork_wkbs(line_payloads),
        strict=True,
    ):
        rows[row_index][-1] = geom_wkb

    _ARTWORK.bulk_insert_values(con, rows)


def _load_board_profile(con: duckdb.DuckDBPyConnection, pcb: Board) -> None:
    if pcb.board_profile is None:
        return
    _BOARD_PROFILE.bulk_insert(con, pcb.board_profile.elements)


def _load_pours(con: duckdb.DuckDBPyConnection, pcb: Board) -> None:
    _POURS.bulk_insert(con, pcb.pours)


def _load_keepouts(con: duckdb.DuckDBPyConnection, pcb: Board) -> None:
    _KEEPOUTS.bulk_insert(con, pcb.keepouts)


def _load_layers(con: duckdb.DuckDBPyConnection, pcb: Board) -> None:
    stackup = pcb.stackup
    stackup_map: dict[str, int] = {}
    rows: list[_LayerRow] = []
    if stackup:
        for index, stack_layer in enumerate(stackup.layers, start=1):
            stackup_map[stack_layer.name] = index
            pcb_layer = pcb.layer_for(stack_layer.name)
            info = pcb_layer or _stackup_layer_as_pcb_layer(
                stack_layer.layer_type, stack_layer.side
            )
            rows.append(
                _LayerRow(
                    position=index,
                    name=stack_layer.name,
                    roles=list(info.role_values),
                    side=info.side,
                    number=None if pcb_layer is None else pcb_layer.number,
                    thickness_mm=_null_if_unset(stack_layer.thickness_mm),
                    material=_null_if_unset(stack_layer.material),
                    epsilon_r=_null_if_unset(stack_layer.epsilon_r),
                    loss_tangent=_null_if_unset(stack_layer.loss_tangent),
                    layer_type=stack_layer.layer_type,
                    copper_orientation=_null_if_unset(stack_layer.copper_orientation),
                ),
            )

    for layer in pcb.layers:
        if layer.name in stackup_map:
            continue
        rows.append(
            _LayerRow(
                position=None,
                name=layer.name,
                roles=list(layer.role_values),
                side=layer.side,
                number=layer.number,
                thickness_mm=None,
                material=None,
                epsilon_r=None,
                loss_tangent=None,
                layer_type=None,
                copper_orientation=None,
            ),
        )
    _LAYERS.bulk_insert(con, rows)


def _load_board(con: duckdb.DuckDBPyConnection, pcb: Board) -> None:
    outline = board_outline_polygon(pcb.board_profile) if pcb.board_profile is not None else None
    _BOARD.bulk_insert(con, [_BoardRow(board=pcb, geom=outline)])


def _load_boards(con: duckdb.DuckDBPyConnection, project: Project) -> None:
    _BOARDS.bulk_insert(
        con,
        (
            _BoardsRow(board_id=f"board:{index:04d}", board=board)
            for index, board in enumerate(project.boards, start=1)
        ),
    )


def _load_net_classes(con: duckdb.DuckDBPyConnection, project: Project) -> None:
    member_rows: list[tuple[str, str]] = []
    for net_class in project.net_classes:
        for member in net_class.members:
            member_rows.append((member, net_class.name))
    _NET_CLASSES.bulk_insert(con, project.net_classes)
    _NET_CLASS_MEMBERS.bulk_insert(con, member_rows)


def _load_design_rules(con: duckdb.DuckDBPyConnection, project: Project) -> None:
    _DESIGN_RULES.bulk_insert(con, project.design_rules)


def _load_diff_pairs(con: duckdb.DuckDBPyConnection, project: Project) -> None:
    _DIFF_PAIRS.bulk_insert(con, project.diff_pairs)


def _load_project_documents(con: duckdb.DuckDBPyConnection, project: Project) -> None:
    _PROJECT_DOCUMENTS.bulk_insert(con, project.documents)


def _load_project_parameters(con: duckdb.DuckDBPyConnection, project: Project) -> None:
    _PROJECT_PARAMETERS.bulk_insert(con, sorted(project.parameters.items()))


def _load_project_variants(con: duckdb.DuckDBPyConnection, project: Project) -> None:
    override_rows: list[_VariantOverrideRow] = []
    for variant in project.variants:
        for index, override in enumerate(variant.overrides, start=1):
            override_rows.append(
                _VariantOverrideRow(variant_name=variant.name, ord=index, override=override)
            )
    _PROJECT_VARIANTS.bulk_insert(con, project.variants)
    _VARIANT_OVERRIDES.bulk_insert(con, override_rows)


def _load_components(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    _COMPONENTS.bulk_insert(con, schematic.components)


def _load_component_enrichment(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    parameter_rows: list[_ComponentParameterRow] = []
    footprint_rows: list[_ComponentFootprintRow] = []
    part_number_rows: list[_ComponentPartNumberRow] = []
    for comp in schematic.components:
        for ord_, parameter in enumerate(comp.parameters, start=1):
            parameter_rows.append(
                _ComponentParameterRow(component_id=comp.id, ord=ord_, parameter=parameter)
            )
        for ord_, model in enumerate(comp.footprints, start=1):
            footprint_rows.append(
                _ComponentFootprintRow(component_id=comp.id, ord=ord_, model=model)
            )
        for ord_, part_number in enumerate(comp.part_numbers, start=1):
            part_number_rows.append(
                _ComponentPartNumberRow(component_id=comp.id, ord=ord_, part_number=part_number),
            )
    _COMPONENT_PARAMETERS.bulk_insert(con, parameter_rows)
    _COMPONENT_FOOTPRINTS.bulk_insert(con, footprint_rows)
    _COMPONENT_PART_NUMBERS.bulk_insert(con, part_number_rows)


def _load_title_blocks(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    _TITLE_BLOCKS.bulk_insert(
        con,
        (
            _TitleBlockRow(page=page, block=page.title_block)
            for page in schematic.pages
            if page.title_block is not None
        ),
    )


def _load_page_annotations(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    rows: list[_PageAnnotationRow] = []
    for page in schematic.pages:
        for index, annotation in enumerate(page.annotations, start=1):
            rows.append(
                _PageAnnotationRow(page=page, ord=index, text=annotation),
            )
    _PAGE_ANNOTATIONS.bulk_insert(con, rows)


def _load_component_pages(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    rows: list[_CompPageRow] = []
    for comp in schematic.components:
        for page in _unique_pages(comp.pages):
            rows.append(_CompPageRow(component=comp, page=page))
    _COMPONENT_PAGES.bulk_insert(con, rows)


def _load_component_occurrences(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    rows: list[ComponentOccurrence] = []
    for comp in schematic.components:
        for occurrence in comp.occurrences:
            rows.append(occurrence)
    _COMPONENT_OCCURRENCES.bulk_insert(con, rows)


def _load_component_occurrence_metadata(
    con: duckdb.DuckDBPyConnection, schematic: Schematic
) -> None:
    rows: list[_OccKeyValueRow] = []
    for comp in schematic.components:
        for occurrence in comp.occurrences:
            for key, value in occurrence.metadata.items():
                rows.append(
                    _OccKeyValueRow(
                        occurrence_id=occurrence.id, owner_id=comp.id, key=key, value=value
                    ),
                )
    _COMPONENT_OCCURRENCE_METADATA.bulk_insert(con, rows)


def _load_component_metadata(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    rows: list[_KeyValueRow] = []
    for comp in schematic.components:
        for key, value in comp.metadata.items():
            rows.append(_KeyValueRow(owner_id=comp.id, ref=comp.reference, key=key, value=value))
    _COMPONENT_METADATA.bulk_insert(con, rows)


def _load_pins(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    rows: list[Pin] = []
    for comp in schematic.components:
        for pin in comp.pins:
            rows.append(pin)
    _PINS.bulk_insert(con, rows)


def _load_pin_occurrences(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    occurrence_rows: list[PinOccurrence] = []
    metadata_rows: list[_OccKeyValueRow] = []
    for comp in schematic.components:
        for pin in comp.pins:
            for occurrence in pin.occurrences:
                occurrence_rows.append(occurrence)
                for key, value in occurrence.metadata.items():
                    metadata_rows.append(
                        _OccKeyValueRow(
                            occurrence_id=occurrence.id, owner_id=pin.id, key=key, value=value
                        ),
                    )
    _PIN_OCCURRENCES.bulk_insert(con, occurrence_rows)
    _PIN_OCCURRENCE_METADATA.bulk_insert(con, metadata_rows)


def _load_nets(con: duckdb.DuckDBPyConnection, schematic: Schematic, project: Project) -> None:
    net_to_class: dict[str, str] = {}
    for nc in project.net_classes:
        for member in nc.members:
            net_to_class[member] = nc.name

    net_to_diff_pair: dict[str, tuple[str, str]] = {}
    for dp in project.diff_pairs:
        net_to_diff_pair[dp.positive_net] = (dp.name, "+")
        net_to_diff_pair[dp.negative_net] = (dp.name, "-")

    rows: list[_NetRow] = []
    for net in schematic.nets:
        dp_info = net_to_diff_pair.get(net.name)
        rows.append(
            _NetRow(
                net=net,
                is_power=is_power_net(net.name, net),
                net_class=net_to_class.get(net.name),
                diff_pair=dp_info[0] if dp_info else None,
                diff_pair_polarity=dp_info[1] if dp_info else None,
            ),
        )
    _NETS.bulk_insert(con, rows)


def _load_net_pages(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    rows: list[_NetPageRow] = []
    for net in schematic.nets:
        for page in _unique_pages(net.pages):
            rows.append(_NetPageRow(net=net, page=page))
    _NET_PAGES.bulk_insert(con, rows)


def _load_net_aliases(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    rows: list[_AliasRow] = []
    for net in schematic.nets:
        for alias in sorted(net.aliases):
            rows.append(_AliasRow(net_id=net.id, net_name=net.name, alias=alias))
    _NET_ALIASES.bulk_insert(con, rows)


def _load_net_occurrences(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    rows: list[NetOccurrence] = []
    for net in schematic.nets:
        for occurrence in net.occurrences:
            rows.append(occurrence)
    _NET_OCCURRENCES.bulk_insert(con, rows)


def _load_net_occurrence_source_names(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    rows: list[_SourceNameRow] = []
    for net in schematic.nets:
        for occurrence in net.occurrences:
            for source_name in sorted(occurrence.source_names):
                rows.append(
                    _SourceNameRow(
                        occurrence_id=occurrence.id, net_id=net.id, source_name=source_name
                    ),
                )
    _NET_OCCURRENCE_SOURCE_NAMES.bulk_insert(con, rows)


def _load_schematic_directives(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    rows: list[_SchematicDirectiveRow] = []
    for net in schematic.nets:
        for occurrence in net.occurrences:
            for index, directive in enumerate(occurrence.directives, start=1):
                rows.append(
                    _SchematicDirectiveRow(
                        occurrence=occurrence,
                        ord=index,
                        directive=directive,
                    ),
                )
    _SCHEMATIC_DIRECTIVES.bulk_insert(con, rows)


def _load_net_occurrence_metadata(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    rows: list[_OccKeyValueRow] = []
    for net in schematic.nets:
        for occurrence in net.occurrences:
            for key, value in occurrence.metadata.items():
                rows.append(
                    _OccKeyValueRow(
                        occurrence_id=occurrence.id, owner_id=net.id, key=key, value=value
                    ),
                )
    _NET_OCCURRENCE_METADATA.bulk_insert(con, rows)


def _load_net_metadata(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    rows: list[_KeyValueRow] = []
    for net in schematic.nets:
        for key, value in net.metadata.items():
            rows.append(_KeyValueRow(owner_id=net.id, ref=net.name, key=key, value=value))
    _NET_METADATA.bulk_insert(con, rows)


def _load_buses(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    member_rows: list[_BusMemberRow] = []
    for bus in schematic.buses:
        for index, net in enumerate(bus.members, start=1):
            member_rows.append(_BusMemberRow(bus=bus, net=net, ord=index))
    _BUSES.bulk_insert(con, schematic.buses)
    _BUS_MEMBERS.bulk_insert(con, member_rows)


def _load_pages(con: duckdb.DuckDBPyConnection, schematic: Schematic) -> None:
    _PAGES.bulk_insert(con, schematic.pages)


def _load_project_metadata(con: duckdb.DuckDBPyConnection, project: Project) -> None:
    meta = project.metadata
    entries = [
        ("name", project.name),
        ("revision", meta.revision),
        ("author", meta.author),
        ("date", meta.date),
        ("organization", meta.organization),
        ("format", meta.format),
        ("selected_variant", project.selected_variant_name),
    ]
    _PROJECT.bulk_insert(con, ((key, value) for key, value in entries if value))
