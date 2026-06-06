"""Renderable geometry inventory for PCB SVG rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from phosphor_eda.pcb import (
    LayerRole,
    Pcb,
    PcbArcGeometry,
    PcbCircleGeometry,
    PcbDimensionGeometry,
    PcbFootprint,
    PcbGeometryObject,
    PcbGeometryRole,
    PcbGeometryShape,
    PcbKeepoutGeometry,
    PcbLayer,
    PcbLineGeometry,
    PcbPadGeometry,
    PcbPolygonGeometry,
    PcbTextGeometry,
    PcbViaGeometry,
    PcbZoneGeometry,
)
from phosphor_eda.pcb import (
    PcbGeometry as DomainPcbGeometry,
)
from phosphor_eda.pcb_render_drills import pad_drill_geometry

SYNTHETIC_BOARD_MATERIAL_ROLE = "board_material"
SYNTHETIC_BOARD_OUTLINE_ROLE = "board_outline"
SYNTHETIC_DRILL_ROLE = "drill"


@dataclass(frozen=True)
class RenderPoint:
    x: float
    y: float


@dataclass(frozen=True)
class GeometryLayer:
    name: str
    role: str
    side: str = ""
    stack_index: int = 0
    source: PcbLayer | None = None


@dataclass(frozen=True)
class GeometryTags:
    source_collection: str = ""
    source_index: int = 0
    component_ref: str = ""
    component_prefix: str = ""
    pad_number: str = ""
    net_number: int | None = None
    net_name: str = ""
    text_kind: str = ""
    footprint_lib: str = ""
    value: str = ""


@dataclass(frozen=True)
class RenderableGeometry:
    id: str
    object_type: PcbGeometryObject
    shape: PcbGeometryShape
    roles: tuple[PcbGeometryRole, ...]
    display_role: str
    layer: GeometryLayer
    tags: GeometryTags
    payload: object
    source: object | None = None
    points: tuple[RenderPoint, ...] = ()
    bbox: tuple[float, float, float, float] | None = None
    clipped: bool = True


@dataclass(frozen=True)
class PcbGeometryStore:
    items: tuple[RenderableGeometry, ...]

    def by_id(self, geometry_id: str) -> RenderableGeometry | None:
        for item in self.items:
            if item.id == geometry_id:
                return item
        return None

    def by_display_role(self, display_role: str) -> tuple[RenderableGeometry, ...]:
        return tuple(item for item in self.items if item.display_role == display_role)

    def by_object_type(
        self, object_type: PcbGeometryObject | str
    ) -> tuple[RenderableGeometry, ...]:
        normalized = PcbGeometryObject(object_type)
        return tuple(item for item in self.items if item.object_type == normalized)


@dataclass(frozen=True)
class GeometrySelector:
    object_types: frozenset[PcbGeometryObject] = frozenset()
    shapes: frozenset[PcbGeometryShape] = frozenset()
    roles: frozenset[PcbGeometryRole] = frozenset()
    display_roles: frozenset[str] = frozenset()
    layer_role: str = ""
    side: str = ""
    layer_name: str = ""
    net_name: str = ""
    net_number: int | None = None
    component_ref: str = ""
    component_prefixes: tuple[str, ...] = ()
    pad_number: str = ""
    text_kinds: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _RenderedViewTransform:
    mirror_x: float | None = None

    def point(self, x: float, y: float) -> RenderPoint:
        return RenderPoint(self.x(x), y)

    def x(self, value: float) -> float:
        if self.mirror_x is None:
            return value
        return self.mirror_x - value


def build_geometry_store(board: Pcb, *, side: str) -> PcbGeometryStore:
    """Build a complete, style-neutral inventory of renderable PCB geometry."""
    board_bbox = board.bbox()
    transform = _rendered_view_transform(side=side, board_bbox=board_bbox)
    layer_lookup = {layer.name: layer for layer in board.layers}
    layers = _geometry_layers(board)
    items: list[RenderableGeometry] = []

    edge_layer = _board_edge_layer(board, layers, layer_lookup)
    outline_geometry = board.board_outline_geometry()
    outline_points = _outline_points_from_geometry(outline_geometry, transform)
    items.append(
        RenderableGeometry(
            id="board_material:0",
            object_type=PcbGeometryObject.GROUP,
            shape=PcbGeometryShape.POLYGON,
            roles=(PcbGeometryRole.GENERATED,),
            display_role=SYNTHETIC_BOARD_MATERIAL_ROLE,
            layer=edge_layer,
            tags=GeometryTags(source_collection="board"),
            payload=board_bbox,
            source=board,
            points=outline_points,
            bbox=board_bbox,
        )
    )
    items.append(
        RenderableGeometry(
            id="board_outline:0",
            object_type=PcbGeometryObject.GRAPHIC,
            shape=PcbGeometryShape.GROUP,
            roles=(PcbGeometryRole.EDGE, PcbGeometryRole.BOARD_OUTLINE, PcbGeometryRole.GENERATED),
            display_role=SYNTHETIC_BOARD_OUTLINE_ROLE,
            layer=edge_layer,
            tags=GeometryTags(source_collection="outline"),
            payload=tuple(
                _transform_geometry_payload(item.data, transform) for item in outline_geometry
            ),
            source=tuple(outline_geometry),
            points=outline_points,
            bbox=board_bbox,
            clipped=False,
        )
    )

    footprints_by_ref = {footprint.reference: footprint for footprint in board.footprints}
    for index, geometry in enumerate(board.geometry):
        items.extend(
            _renderable_items_for_geometry(
                board,
                geometry,
                index,
                layers,
                layer_lookup,
                footprints_by_ref,
                transform,
            )
        )

    return PcbGeometryStore(items=tuple(items))


def _renderable_items_for_geometry(
    board: Pcb,
    geometry: DomainPcbGeometry,
    index: int,
    layers: dict[str, GeometryLayer],
    layer_lookup: dict[str, PcbLayer],
    footprints_by_ref: dict[str, PcbFootprint],
    transform: _RenderedViewTransform,
) -> list[RenderableGeometry]:
    if geometry.has_role(PcbGeometryRole.BOARD_OUTLINE):
        return []

    payload = _transform_geometry_payload(geometry.data, transform)
    footprint = footprints_by_ref.get(geometry.footprint_ref)
    source_collection = geometry.metadata.source_collection
    source_index = geometry.metadata.source_index
    if source_index is None:
        source_index = (
            geometry.metadata.native_index if geometry.metadata.native_index is not None else index
        )
    tags = _geometry_tags(board, geometry, footprint, source_collection, source_index)

    if geometry.object_type == PcbGeometryObject.PAD and isinstance(payload, PcbPadGeometry):
        return _renderable_pad_items(geometry, payload, tags, layers, layer_lookup, transform)

    if geometry.object_type == PcbGeometryObject.VIA and isinstance(payload, PcbViaGeometry):
        return [
            RenderableGeometry(
                id=geometry.id,
                object_type=geometry.object_type,
                shape=geometry.shape,
                roles=geometry.roles,
                display_role=geometry.display_role,
                layer=_synthetic_layer("vias", "via", "", 800),
                tags=tags,
                payload=payload,
                source=geometry,
                points=(transform.point(payload.x, payload.y),),
                bbox=_circle_bbox(payload.x, payload.y, payload.size / 2),
            )
        ]

    display_role = _geometry_display_role(geometry)
    if not display_role:
        return []
    layer_name = geometry.primary_layer
    layer = layers.get(layer_name, _layer_for_name(layer_name, layer_lookup))
    if geometry.object_type == PcbGeometryObject.KEEP_OUT:
        layer = replace(layer, role="keepout")

    return [
        RenderableGeometry(
            id=geometry.id,
            object_type=geometry.object_type,
            shape=geometry.shape,
            roles=geometry.roles,
            display_role=display_role,
            layer=layer,
            tags=tags,
            payload=payload,
            source=geometry,
            points=_geometry_points(geometry.data, transform),
            bbox=_geometry_bbox(payload),
        )
    ]


def _geometry_tags(
    board: Pcb,
    geometry: DomainPcbGeometry,
    footprint: PcbFootprint | None,
    source_collection: str,
    source_index: int,
) -> GeometryTags:
    pad_number = geometry.data.number if isinstance(geometry.data, PcbPadGeometry) else ""
    text_kind = ""
    if geometry.has_role(PcbGeometryRole.DESIGNATOR):
        text_kind = "reference"
    elif geometry.has_role(PcbGeometryRole.VALUE):
        text_kind = "value"
    elif geometry.has_role(PcbGeometryRole.USER_TEXT):
        text_kind = "user"
    elif geometry.has_role(PcbGeometryRole.COMMENT):
        text_kind = "board"
    return GeometryTags(
        source_collection=source_collection,
        source_index=source_index,
        component_ref=geometry.footprint_ref,
        component_prefix=_component_prefix(geometry.footprint_ref),
        pad_number=pad_number,
        net_number=geometry.net_number,
        net_name=geometry.net_name or _net_name(board, geometry.net_number),
        text_kind=text_kind,
        footprint_lib=footprint.footprint_lib if footprint else "",
        value=footprint.value if footprint else "",
    )


def _renderable_pad_items(
    geometry: DomainPcbGeometry,
    pad: PcbPadGeometry,
    tags: GeometryTags,
    layers: dict[str, GeometryLayer],
    layer_lookup: dict[str, PcbLayer],
    transform: _RenderedViewTransform,
) -> list[RenderableGeometry]:
    items: list[RenderableGeometry] = []
    pad_layer_names = _pad_copper_layers(geometry.layers, layer_lookup)
    for layer_name in pad_layer_names:
        items.append(
            RenderableGeometry(
                id=_pad_geometry_id(
                    geometry.footprint_ref,
                    pad.number,
                    tags.source_index,
                    layer_name=layer_name,
                    layer_count=len(pad_layer_names),
                ),
                object_type=geometry.object_type,
                shape=geometry.shape,
                roles=geometry.roles,
                display_role=geometry.display_role,
                layer=layers.get(layer_name, _layer_for_name(layer_name, layer_lookup)),
                tags=tags,
                payload=pad,
                source=geometry,
                points=(transform.point(pad.x, pad.y),),
                bbox=_pad_bbox(pad),
            )
        )
    if pad.drill > 0:
        items.append(
            RenderableGeometry(
                id=f"drill:{geometry.footprint_ref}:{pad.number}:{tags.source_index}",
                object_type=PcbGeometryObject.PAD,
                shape=PcbGeometryShape.CIRCLE,
                roles=(*geometry.roles, PcbGeometryRole.DRILL),
                display_role=SYNTHETIC_DRILL_ROLE,
                layer=_synthetic_layer("drills", "drill", "", 900),
                tags=replace(tags, source_collection="pad_drills"),
                payload=pad,
                source=geometry,
                points=(transform.point(pad.x, pad.y),),
                bbox=_pad_drill_bbox(pad),
            )
        )
    _append_pad_mask_apertures_for_geometry(
        items, geometry, pad, tags, layers, layer_lookup, transform
    )
    return items


def _geometry_display_role(geometry: DomainPcbGeometry) -> str:
    if geometry.object_type == PcbGeometryObject.TRACK:
        return PcbGeometryRole.TRACE.value
    if geometry.object_type == PcbGeometryObject.ZONE:
        return geometry.primary_role.value
    if geometry.object_type == PcbGeometryObject.KEEP_OUT:
        return PcbGeometryRole.KEEPOUT.value
    if geometry.object_type == PcbGeometryObject.DIMENSION:
        return PcbGeometryRole.DIMENSION.value
    if geometry.object_type == PcbGeometryObject.TEXT:
        if geometry.has_role(PcbGeometryRole.DESIGNATOR):
            return PcbGeometryRole.DESIGNATOR.value
        if geometry.has_role(PcbGeometryRole.VALUE):
            return PcbGeometryRole.VALUE.value
        if not geometry.footprint_ref:
            return (
                PcbGeometryRole.COMMENT.value
                if geometry.has_role(PcbGeometryRole.COMMENT)
                else PcbGeometryRole.TEXT.value
            )
        return PcbGeometryRole.USER_TEXT.value
    if geometry.object_type == PcbGeometryObject.GRAPHIC:
        if geometry.has_role(PcbGeometryRole.SOLDER_MASK):
            return PcbGeometryRole.SOLDER_MASK.value
        if geometry.has_role(PcbGeometryRole.SOLDER_PASTE):
            return PcbGeometryRole.SOLDER_PASTE.value
        if geometry.has_role(PcbGeometryRole.SILKSCREEN):
            return PcbGeometryRole.SILKSCREEN.value
        if geometry.has_role(PcbGeometryRole.FABRICATION):
            return geometry.primary_role.value
        if geometry.shape == PcbGeometryShape.POLYGON:
            return PcbGeometryRole.MECHANICAL.value
        if geometry.shape == PcbGeometryShape.ARC:
            return (
                PcbGeometryRole.COMPONENT_BODY.value
                if geometry.footprint_ref
                else PcbGeometryRole.MECHANICAL.value
            )
        return (
            PcbGeometryRole.COMPONENT_BODY.value
            if geometry.footprint_ref
            else PcbGeometryRole.MECHANICAL.value
        )
    return geometry.display_role


def geometry_matches_selector(
    geometry: RenderableGeometry,
    selector: GeometrySelector,
    *,
    active_side: str,
) -> bool:
    if selector.object_types and geometry.object_type not in selector.object_types:
        return False
    if selector.shapes and geometry.shape not in selector.shapes:
        return False
    if selector.roles and not selector.roles.intersection(geometry.roles):
        return False
    if selector.display_roles and geometry.display_role not in selector.display_roles:
        return False
    if selector.layer_role and geometry.layer.role != selector.layer_role:
        return False
    if selector.side and not _side_matches(geometry.layer.side, selector.side, active_side):
        return False
    if selector.layer_name and geometry.layer.name != selector.layer_name:
        return False
    if selector.net_name and geometry.tags.net_name != selector.net_name:
        return False
    if selector.net_number is not None and geometry.tags.net_number != selector.net_number:
        return False
    if selector.component_ref and geometry.tags.component_ref != selector.component_ref:
        return False
    if (
        selector.component_prefixes
        and geometry.tags.component_prefix not in selector.component_prefixes
    ):
        return False
    if selector.pad_number and geometry.tags.pad_number != selector.pad_number:
        return False
    return not (selector.text_kinds and geometry.tags.text_kind not in selector.text_kinds)


def layer_role(layer: PcbLayer) -> str:
    return layer.primary_role.value


def _pad_mask_aperture_layers(
    pad_layers: tuple[str, ...],
    layer_lookup: dict[str, PcbLayer],
) -> tuple[str, ...]:
    pad_layer_names = {str(layer_name) for layer_name in pad_layers}
    copper_layers = [layer for layer in layer_lookup.values() if layer.has_role(LayerRole.COPPER)]
    mask_layers = [
        layer for layer in layer_lookup.values() if layer.has_role(LayerRole.SOLDER_MASK)
    ]
    sides: list[str] = []
    if "*.Cu" in pad_layer_names:
        sides.extend(("front", "back"))
    else:
        for layer in copper_layers:
            if layer.name in pad_layer_names and layer.side in {"front", "back"}:
                sides.append(layer.side)
    if not sides:
        sides.append("front")
    return tuple(
        layer.name for side in dict.fromkeys(sides) for layer in mask_layers if layer.side == side
    )


def _append_pad_mask_apertures_for_geometry(
    items: list[RenderableGeometry],
    geometry: DomainPcbGeometry,
    pad: PcbPadGeometry,
    tags: GeometryTags,
    layers: dict[str, GeometryLayer],
    layer_lookup: dict[str, PcbLayer],
    transform: _RenderedViewTransform,
) -> None:
    if pad.mask_aperture_width is None or pad.mask_aperture_height is None:
        return
    if pad.mask_aperture_width <= 0.0 or pad.mask_aperture_height <= 0.0:
        return
    for mask_layer_name in _pad_mask_aperture_layers(geometry.layers, layer_lookup):
        aperture = replace(
            pad,
            width=pad.mask_aperture_width,
            height=pad.mask_aperture_height,
        )
        layer = layers.get(mask_layer_name, _layer_for_name(mask_layer_name, layer_lookup))
        items.append(
            RenderableGeometry(
                id=(
                    "pad_mask_aperture:"
                    f"{geometry.footprint_ref}:{pad.number}:{tags.source_index}:{mask_layer_name}"
                ),
                object_type=PcbGeometryObject.PAD,
                shape=geometry.shape,
                roles=(*geometry.roles, PcbGeometryRole.SOLDER_MASK),
                display_role=PcbGeometryRole.SOLDER_MASK.value,
                layer=layer,
                tags=replace(tags, source_collection="pad_mask_apertures"),
                payload=aperture,
                source=geometry,
                points=(transform.point(aperture.x, aperture.y),),
                bbox=_pad_bbox(aperture),
            )
        )


def _transform_geometry_payload(data: object, transform: _RenderedViewTransform) -> object:
    if transform.mirror_x is None:
        return data
    if isinstance(data, PcbPadGeometry):
        return replace(
            data,
            x=transform.x(data.x),
            rotation=(-data.rotation) % 360 if data.rotation else 0.0,
        )
    if isinstance(data, PcbViaGeometry):
        return replace(data, x=transform.x(data.x))
    if isinstance(data, PcbLineGeometry):
        return replace(data, start_x=transform.x(data.start_x), end_x=transform.x(data.end_x))
    if isinstance(data, PcbArcGeometry):
        return replace(
            data,
            start_x=transform.x(data.start_x),
            mid_x=transform.x(data.mid_x),
            end_x=transform.x(data.end_x),
        )
    if isinstance(data, PcbCircleGeometry):
        return replace(data, cx=transform.x(data.cx))
    if isinstance(data, PcbPolygonGeometry):
        return replace(
            data,
            points=[(transform.x(x), y) for x, y in data.points],
            holes=[[(transform.x(x), y) for x, y in hole] for hole in data.holes],
        )
    if isinstance(data, PcbKeepoutGeometry):
        return replace(
            data,
            boundary=[(transform.x(x), y) for x, y in data.boundary],
            holes=[[(transform.x(x), y) for x, y in hole] for hole in data.holes],
        )
    if isinstance(data, PcbZoneGeometry):
        return replace(data, boundary=[(transform.x(x), y) for x, y in data.boundary])
    if isinstance(data, PcbTextGeometry):
        return replace(data, x=transform.x(data.x), rotation=180.0 - data.rotation)
    if isinstance(data, PcbDimensionGeometry):
        return replace(data, start_x=transform.x(data.start_x), end_x=transform.x(data.end_x))
    return data


def _geometry_points(data: object, transform: _RenderedViewTransform) -> tuple[RenderPoint, ...]:
    if isinstance(data, PcbLineGeometry):
        return (
            transform.point(data.start_x, data.start_y),
            transform.point(data.end_x, data.end_y),
        )
    if isinstance(data, PcbArcGeometry):
        return (
            transform.point(data.start_x, data.start_y),
            transform.point(data.mid_x, data.mid_y),
            transform.point(data.end_x, data.end_y),
        )
    if isinstance(data, PcbPolygonGeometry):
        return tuple(RenderPoint(transform.x(x), y) for x, y in data.points)
    if isinstance(data, PcbKeepoutGeometry):
        return tuple(RenderPoint(transform.x(x), y) for x, y in data.boundary)
    if isinstance(data, PcbZoneGeometry):
        return tuple(RenderPoint(transform.x(x), y) for x, y in data.boundary)
    if isinstance(data, PcbCircleGeometry):
        return (transform.point(data.cx, data.cy),)
    if isinstance(data, PcbTextGeometry):
        return (transform.point(data.x, data.y),)
    if isinstance(data, PcbDimensionGeometry):
        return (
            transform.point(data.start_x, data.start_y),
            transform.point(data.end_x, data.end_y),
        )
    return ()


def _geometry_bbox(data: object) -> tuple[float, float, float, float] | None:
    if isinstance(data, PcbLineGeometry):
        return _line_bbox(data.start_x, data.start_y, data.end_x, data.end_y)
    if isinstance(data, PcbArcGeometry):
        return _points_bbox(
            ((data.start_x, data.start_y), (data.mid_x, data.mid_y), (data.end_x, data.end_y))
        )
    if isinstance(data, PcbPolygonGeometry):
        return _points_bbox(data.points)
    if isinstance(data, PcbKeepoutGeometry):
        return _points_bbox(data.boundary)
    if isinstance(data, PcbZoneGeometry):
        return _points_bbox(data.boundary)
    if isinstance(data, PcbCircleGeometry):
        return _circle_bbox(data.cx, data.cy, data.radius)
    if isinstance(data, PcbPadGeometry):
        return _pad_bbox(data)
    if isinstance(data, PcbViaGeometry):
        return _circle_bbox(data.x, data.y, data.size / 2)
    if isinstance(data, PcbDimensionGeometry):
        return _line_bbox(data.start_x, data.start_y, data.end_x, data.end_y)
    return None


def _outline_points_from_geometry(
    outline: list[DomainPcbGeometry],
    transform: _RenderedViewTransform,
) -> tuple[RenderPoint, ...]:
    points: list[RenderPoint] = []
    for item in outline:
        points.extend(_geometry_points(item.data, transform))
    return tuple(points)


def _geometry_layers(board: Pcb) -> dict[str, GeometryLayer]:
    return {
        layer.name: GeometryLayer(
            name=layer.name,
            role=layer_role(layer),
            side=layer.side,
            stack_index=index,
            source=layer,
        )
        for index, layer in enumerate(board.layers)
    }


def _layer_for_name(name: str, layer_lookup: dict[str, PcbLayer]) -> GeometryLayer:
    layer = layer_lookup.get(name)
    if layer is None:
        return _synthetic_layer(name, "unknown", "", 10_000)
    return GeometryLayer(
        name=layer.name,
        role=layer_role(layer),
        side=layer.side,
        stack_index=layer.number if layer.number is not None else 10_000,
        source=layer,
    )


def _synthetic_layer(name: str, role: str, side: str, stack_index: int) -> GeometryLayer:
    return GeometryLayer(name=name, role=role, side=side, stack_index=stack_index)


def _board_edge_layer(
    board: Pcb,
    layers: dict[str, GeometryLayer],
    layer_lookup: dict[str, PcbLayer],
) -> GeometryLayer:
    for layer_name in _board_outline_layer_names(board):
        if layer_name in layers:
            return replace(layers[layer_name], role="edge", side="")
        if layer_name in layer_lookup:
            return replace(_layer_for_name(layer_name, layer_lookup), role="edge", side="")
    for layer in layers.values():
        if layer.role == "edge":
            return layer
    return _synthetic_layer("Edge.Cuts", "edge", "", -300)


def _board_outline_layer_names(board: Pcb) -> tuple[str, ...]:
    names: list[str] = [
        item.primary_layer for item in board.board_outline_geometry() if item.primary_layer
    ]
    return tuple(dict.fromkeys(name for name in names if name))


def _rendered_view_transform(
    *,
    side: str,
    board_bbox: tuple[float, float, float, float],
) -> _RenderedViewTransform:
    if side != "back":
        return _RenderedViewTransform()
    bx0, _by0, bx1, _by1 = board_bbox
    return _RenderedViewTransform(mirror_x=bx0 + bx1)


def _pad_copper_layers(
    pad_layers: tuple[str, ...],
    layer_lookup: dict[str, PcbLayer],
) -> tuple[str, ...]:
    layer_names: list[str] = []
    for raw_layer_name in pad_layers:
        layer_name = str(raw_layer_name)
        if layer_name == "*.Cu":
            layer_names.extend(_all_copper_layer_names(layer_lookup))
            continue
        layer = layer_lookup.get(layer_name)
        if layer and layer.has_role(LayerRole.COPPER):
            layer_names.append(layer_name)
    unique_layer_names = tuple(dict.fromkeys(layer_names))
    if unique_layer_names:
        return unique_layer_names
    return tuple(pad_layers[:1])


def _all_copper_layer_names(layer_lookup: dict[str, PcbLayer]) -> tuple[str, ...]:
    return tuple(
        layer.name
        for layer in sorted(
            (layer for layer in layer_lookup.values() if layer.has_role(LayerRole.COPPER)),
            key=_layer_stack_sort_key,
        )
    )


def _layer_stack_sort_key(layer: PcbLayer) -> tuple[int, str]:
    return (layer.number if layer.number is not None else 10_000, layer.name)


def _pad_geometry_id(
    footprint_ref: str,
    pad_number: str,
    pad_index: int,
    *,
    layer_name: str,
    layer_count: int,
) -> str:
    base_id = f"pad:{footprint_ref}:{pad_number}:{pad_index}"
    if layer_count == 1:
        return base_id
    safe_layer_name = layer_name.replace(":", "_")
    return f"{base_id}:{safe_layer_name}"


def _side_matches(layer_side: str, expected: str, active_side: str) -> bool:
    if expected in ("", "any"):
        return True
    if expected == "active":
        return layer_side == active_side
    if expected == "opposite":
        return layer_side in ("front", "back") and layer_side != active_side
    return layer_side == expected


def _component_prefix(ref: str) -> str:
    match = re.match(r"[A-Za-z]+", ref)
    return match.group(0).upper() if match else ""


def _net_name(board: Pcb, net_number: int) -> str:
    net = board.nets.get(net_number)
    return net.name if net else ""


def _pad_bbox(pad: PcbPadGeometry) -> tuple[float, float, float, float]:
    return (
        pad.x - pad.width / 2,
        pad.y - pad.height / 2,
        pad.x + pad.width / 2,
        pad.y + pad.height / 2,
    )


def _pad_drill_bbox(pad: PcbPadGeometry) -> tuple[float, float, float, float]:
    geometry = pad_drill_geometry(pad)
    if geometry is None or geometry.is_empty:
        return _circle_bbox(pad.x, pad.y, pad.drill / 2)
    min_x, min_y, max_x, max_y = geometry.bounds
    return (min_x, min_y, max_x, max_y)


def _circle_bbox(x: float, y: float, radius: float) -> tuple[float, float, float, float]:
    return (x - radius, y - radius, x + radius, y + radius)


def _line_bbox(x1: float, y1: float, x2: float, y2: float) -> tuple[float, float, float, float]:
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def _points_bbox(
    points: tuple[tuple[float, float], ...] | list[tuple[float, float]],
) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))
