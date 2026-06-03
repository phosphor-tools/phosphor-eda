"""Render-mode projections from PCB source artwork to derived visual layers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from shapely import GeometryCollection, LineString, MultiLineString, MultiPolygon, Polygon
from shapely.ops import linemerge

from phosphor_eda.pcb import PcbSegment, PcbTraceArc, PcbVia
from phosphor_eda.pcb_render_artwork import (
    ArtworkItem,
    DerivedLayer,
    board_outline_geometry,
    drill_geometry_for_layer,
    geometry_to_artwork,
    select_source_artwork,
)
from phosphor_eda.pcb_render_geometry import GeometryKind, GeometryLayer, GeometryTags
from phosphor_eda.pcb_render_primitives import (
    LayerMask,
    SvgPrimitive,
    geometry_to_svg_primitive,
    svg_primitives_from_geometry,
)
from phosphor_eda.pcb_render_tokens import VisualRole, resolve_layer_style
from phosphor_eda.shapely_geometry import (
    robust_difference,
    robust_intersection,
    robust_union,
)
from phosphor_eda.sql.geometry import arc_to_polyline

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from shapely.geometry.base import BaseGeometry

    from phosphor_eda.pcb_render_geometry import PcbGeometryStore, RenderableGeometry
    from phosphor_eda.pcb_render_profile import RenderProfiler
    from phosphor_eda.pcb_render_settings import HighlightSpec, LayerSelectionRule, RenderSettings


@dataclass(frozen=True)
class _LayerGroupKey:
    function: str
    side: str
    inner_index: int | None
    source_layer_name: str


@dataclass(frozen=True)
class _GroupedArtwork:
    artwork: ArtworkItem
    layer: GeometryLayer
    source_items: tuple[RenderableGeometry, ...]


@dataclass(frozen=True)
class _ProfiledArtwork:
    kind: GeometryKind
    grouped: _GroupedArtwork


@dataclass(frozen=True)
class _TraceArtworkKey:
    layer_name: str
    net_number: int | None
    net_name: str
    width: float


@dataclass(frozen=True)
class _LayerProcessingProfile:
    event_prefix: str
    layer: str
    function: str
    side: str
    items: int


@dataclass(frozen=True)
class _GeometryComplexity:
    geometries: int = 0
    polygons: int = 0
    rings: int = 0
    coordinates: int = 0
    max_coordinates: int = 0

    def plus(self, other: _GeometryComplexity) -> _GeometryComplexity:
        return _GeometryComplexity(
            geometries=self.geometries + other.geometries,
            polygons=self.polygons + other.polygons,
            rings=self.rings + other.rings,
            coordinates=self.coordinates + other.coordinates,
            max_coordinates=max(self.max_coordinates, other.max_coordinates),
        )

    def profile_data(self) -> dict[str, int]:
        return {
            "geometries": self.geometries,
            "polygons": self.polygons,
            "rings": self.rings,
            "coordinates": self.coordinates,
            "maxCoordinates": self.max_coordinates,
        }


@dataclass(frozen=True)
class HighlightGroup:
    target: str
    layers: tuple[DerivedLayer, ...]


@dataclass(frozen=True)
class _PrimitiveLayerItem:
    source: RenderableGeometry
    layer: GeometryLayer


_SOURCE_FUNCTION_TO_LAYER_ROLE = {
    "copper": "copper",
    "silkscreen": "silkscreen",
    "solder_mask": "mask",
    "solder_paste": "paste",
    "fab": "fabrication",
    "courtyard": "courtyard",
    "edge": "edge",
    "mechanical": "mechanical",
    "other": "unknown",
}

_REALISTIC_LAYER_ORDER = (
    "substrate",
    "solderMask",
    "coveredCopper",
    "exposedCopper",
    "silkscreen",
    "boardOutline",
)


def build_cad_layers(
    store: PcbGeometryStore,
    settings: RenderSettings,
    *,
    warn: Callable[[str], None],
    profiler: RenderProfiler | None = None,
) -> tuple[DerivedLayer, ...]:
    """Build CAD derived layers from selected PCB source artwork."""
    selected_items = _filter_excluded_components(
        select_source_artwork(store, settings.source.layers, active_side=settings.side),
        settings.source.exclude_components,
    )
    if profiler is not None:
        profiler.metric("cad.selected_items", count=len(selected_items))
    layer_items = _primitive_layer_items(
        store,
        selected_items,
        settings.source.layers,
        active_side=settings.side,
        profiler=profiler,
    )
    if profiler is not None:
        profiler.metric("cad.layer_items", count=len(layer_items))
    board = board_outline_geometry(store)
    layer_mask = _layer_mask(store, board)
    dimmed = _should_dim_base_layers(store, settings)
    warned_missing_dimmed_tokens: set[str] = set()

    groups: dict[_LayerGroupKey, list[_PrimitiveLayerItem]] = defaultdict(list)
    for item in layer_items:
        groups[_group_key(item.layer)].append(item)

    layers: list[DerivedLayer] = []
    for key, group in sorted(groups.items(), key=_group_sort_key):
        primitives = _primitive_layer_primitives(
            key,
            group,
            profiler=profiler,
        )
        if not primitives:
            continue
        role = VisualRole(
            namespace="cad",
            function=key.function,
            side=key.side,
            inner_index=key.inner_index,
            source_layer_name=key.source_layer_name,
        )
        style = resolve_layer_style(
            settings.tokens,
            role,
            dimmed=dimmed,
            warn=warn,
            warned_missing_dimmed_tokens=warned_missing_dimmed_tokens,
        )
        source_layers = _unique_ordered(primitive.source_layer for primitive in primitives)
        source_ids = _source_ids_by_store_order_for_primitives(store, primitives)
        layers.append(
            DerivedLayer(
                id=_derived_layer_id(role),
                role=role,
                primitives=primitives,
                source_layers=source_layers,
                source_ids=source_ids,
                style=style,
                data={"source-layer": key.source_layer_name},
                mask=None if key.function == "edge" else layer_mask,
            )
        )

    return tuple(layers)


def build_realistic_layers(
    store: PcbGeometryStore,
    settings: RenderSettings,
    *,
    warn: Callable[[str], None],
    profiler: RenderProfiler | None = None,
) -> tuple[DerivedLayer, ...]:
    """Build front/back realistic derived layers from selected source artwork."""
    side = settings.side or "front"
    selected_items = _filter_excluded_components(
        select_source_artwork(store, settings.source.layers, active_side=side),
        settings.source.exclude_components,
    )
    if profiler is not None:
        profiler.metric("realistic.selected_items", count=len(selected_items))
    grouped_artwork = _groupable_artwork(
        store,
        selected_items,
        settings.source.layers,
        active_side=side,
        profiler=profiler,
    )
    if profiler is not None:
        profiler.metric("realistic.grouped_artwork", count=len(grouped_artwork))
    board = board_outline_geometry(store)
    surface_drills = _surface_drill_geometry(store)
    layer_mask = LayerMask(
        board=_mask_primitives_from_geometry(
            board,
            kind=GeometryKind.BOARD_MATERIAL,
            source_ids=_board_source_ids(store),
            source_layers=_board_source_layers(store),
        ),
        drills=_mask_primitives_from_geometry(
            surface_drills,
            kind=GeometryKind.DRILL,
            source_ids=_drill_source_ids(store),
            source_layers=("drills",),
        ),
    )
    dimmed = _should_dim_base_layers(store, settings)
    warned_missing_dimmed_tokens: set[str] = set()

    mask_artwork = _side_artwork(selected_items, role="mask", side=side)
    copper_artwork = tuple(
        item.artwork
        for item in grouped_artwork
        if item.layer.role == "copper" and item.layer.side == side
    )
    silkscreen_artwork = _side_artwork(selected_items, role="silkscreen", side=side)

    if profiler is not None:
        profiler.metric("realistic.mask_artwork", count=len(mask_artwork))
        profiler.metric("realistic.copper_artwork", count=len(copper_artwork))
        profiler.metric("realistic.silkscreen_artwork", count=len(silkscreen_artwork))
    if profiler is None:
        mask_openings = _process_artwork_layer(
            store,
            (item.geometry for item in mask_artwork),
            prefer_disjoint_subsets=True,
        )
        outer_copper = _process_artwork_layer(
            store,
            (item.geometry for item in copper_artwork),
            prefer_disjoint_subsets=True,
        )
        silkscreen = _process_artwork_layer(
            store,
            (item.geometry for item in silkscreen_artwork),
            prefer_disjoint_subsets=True,
        )
    else:
        mask_openings = _process_artwork_layer(
            store,
            (item.geometry for item in mask_artwork),
            prefer_disjoint_subsets=True,
            profiler=profiler,
            profile=_LayerProcessingProfile(
                event_prefix="realistic.mask_openings",
                layer="mask",
                function="mask",
                side=side,
                items=len(mask_artwork),
            ),
        )
        outer_copper = _process_artwork_layer(
            store,
            (item.geometry for item in copper_artwork),
            prefer_disjoint_subsets=True,
            profiler=profiler,
            profile=_LayerProcessingProfile(
                event_prefix="realistic.outer_copper",
                layer="copper",
                function="copper",
                side=side,
                items=len(copper_artwork),
            ),
        )
        silkscreen = _process_artwork_layer(
            store,
            (item.geometry for item in silkscreen_artwork),
            prefer_disjoint_subsets=True,
            profiler=profiler,
            profile=_LayerProcessingProfile(
                event_prefix="realistic.silkscreen",
                layer="silkscreen",
                function="silkscreen",
                side=side,
                items=len(silkscreen_artwork),
            ),
        )

    layer_inputs = {
        "substrate": _RealisticLayerInput(
            geometry=board,
            primitives=_mask_primitives_from_geometry(
                board,
                kind=GeometryKind.BOARD_MATERIAL,
                source_ids=_board_source_ids(store),
                source_layers=_board_source_layers(store),
            ),
            source_layers=_board_source_layers(store),
            source_ids=_board_source_ids(store),
        ),
        "solderMask": _RealisticLayerInput(
            geometry=_difference(board, mask_openings),
            primitives=_mask_primitives_from_geometry(
                _difference(board, mask_openings),
                kind=GeometryKind.MASK,
                source_ids=_source_ids_from_artwork(mask_artwork),
                source_layers=_source_layers_from_artwork(mask_artwork),
            ),
            source_layers=_source_layers_from_artwork(mask_artwork),
            source_ids=_source_ids_from_artwork(mask_artwork),
        ),
        "coveredCopper": _RealisticLayerInput(
            geometry=outer_copper,
            primitives=_realistic_copper_primitives(grouped_artwork, side=side),
            source_layers=_source_layers_from_artwork(copper_artwork),
            source_ids=_source_ids_by_store_order_for_artwork(store, copper_artwork),
        ),
        "exposedCopper": _RealisticLayerInput(
            geometry=_intersection(outer_copper, mask_openings),
            primitives=_mask_primitives_from_geometry(
                _intersection(outer_copper, mask_openings),
                kind=GeometryKind.PAD,
                source_ids=_source_ids_by_store_order_for_artwork(
                    store,
                    (*copper_artwork, *mask_artwork),
                ),
                source_layers=_unique_ordered(
                    (
                        *_source_layers_from_artwork(copper_artwork),
                        *_source_layers_from_artwork(mask_artwork),
                    )
                ),
            ),
            source_layers=_unique_ordered(
                (
                    *_source_layers_from_artwork(copper_artwork),
                    *_source_layers_from_artwork(mask_artwork),
                )
            ),
            source_ids=_source_ids_by_store_order_for_artwork(
                store,
                (*copper_artwork, *mask_artwork),
            ),
        ),
        "silkscreen": _RealisticLayerInput(
            geometry=_difference(silkscreen, mask_openings),
            primitives=_mask_primitives_from_geometry(
                _difference(silkscreen, mask_openings),
                kind=GeometryKind.SILK_POLYGON,
                source_ids=_source_ids_by_store_order_for_artwork(store, silkscreen_artwork),
                source_layers=_source_layers_from_artwork(silkscreen_artwork),
            ),
            source_layers=_source_layers_from_artwork(silkscreen_artwork),
            source_ids=_source_ids_by_store_order_for_artwork(store, silkscreen_artwork),
        ),
        "boardOutline": _RealisticLayerInput(
            geometry=_outline_geometry(board),
            primitives=_mask_primitives_from_geometry(
                _outline_geometry(board),
                kind=GeometryKind.BOARD_OUTLINE,
                source_ids=_board_source_ids(store),
                source_layers=_board_source_layers(store),
            ),
            source_layers=_board_source_layers(store),
            source_ids=_board_source_ids(store),
        ),
    }

    layers: list[DerivedLayer] = []
    for function in _REALISTIC_LAYER_ORDER:
        layer_input = layer_inputs[function]
        geometry = layer_input.geometry
        if geometry.is_empty:
            continue

        role = VisualRole(namespace="realistic", function=function)
        style = resolve_layer_style(
            settings.tokens,
            role,
            dimmed=dimmed,
            warn=warn,
            warned_missing_dimmed_tokens=warned_missing_dimmed_tokens,
        )
        layers.append(
            DerivedLayer(
                id=_derived_layer_id(role),
                role=role,
                primitives=layer_input.primitives,
                source_layers=layer_input.source_layers,
                source_ids=layer_input.source_ids,
                style=style,
                mask=None if function == "boardOutline" else layer_mask,
            )
        )
    return tuple(layers)


def build_highlight_layers(
    store: PcbGeometryStore,
    settings: RenderSettings,
    *,
    warn: Callable[[str], None],
    profiler: RenderProfiler | None = None,
) -> tuple[HighlightGroup, ...]:
    """Build derived highlight overlay groups from selected raw source geometry."""
    board = board_outline_geometry(store)
    layer_mask = _layer_mask(store, board)
    groups: list[HighlightGroup] = []

    for highlight in settings.highlights:
        selected_items = _selected_highlight_items(store, settings, highlight)
        selected_vias = _selected_highlight_vias(store, settings, highlight)
        layer_items = _primitive_layer_items(
            store,
            selected_items,
            settings.source.layers,
            active_side=settings.side,
            via_items=selected_vias,
            profiler=profiler,
        )
        if profiler is not None:
            profiler.metric(
                "highlight.selected_items",
                target=_highlight_target(highlight),
                items=len(selected_items),
                vias=len(selected_vias),
                grouped=len(layer_items),
            )
        if not layer_items:
            continue

        by_layer: dict[_LayerGroupKey, list[_PrimitiveLayerItem]] = defaultdict(list)
        for item in layer_items:
            by_layer[_group_key(item.layer)].append(item)

        target = _highlight_target(highlight)
        layers: list[DerivedLayer] = []
        for key, layer_group in sorted(by_layer.items(), key=_group_sort_key):
            primitives = _primitive_layer_primitives(
                key,
                layer_group,
                profiler=profiler,
            )
            if not primitives:
                continue
            role = VisualRole(
                namespace="highlight",
                function=key.function,
                side=key.side,
                inner_index=key.inner_index,
                source_layer_name=key.source_layer_name,
            )
            style = resolve_layer_style(
                settings.tokens,
                role,
                dimmed=False,
                warn=warn,
                highlight_color=highlight.color,
            )
            layers.append(
                DerivedLayer(
                    id=_derived_layer_id(role),
                    role=role,
                    primitives=primitives,
                    source_layers=_unique_ordered(
                        primitive.source_layer for primitive in primitives
                    ),
                    source_ids=_source_ids_by_store_order_for_primitives(
                        store,
                        primitives,
                    ),
                    style=style,
                    data={
                        "data-highlight-target": target,
                        "source-layer": key.source_layer_name,
                    },
                    mask=None if key.function == "edge" else layer_mask,
                )
            )

        if layers:
            groups.append(HighlightGroup(target=target, layers=tuple(layers)))

    return tuple(groups)


@dataclass(frozen=True)
class _RealisticLayerInput:
    geometry: BaseGeometry
    primitives: tuple[SvgPrimitive, ...]
    source_layers: tuple[str, ...]
    source_ids: tuple[str, ...]


def _primitive_layer_items(
    store: PcbGeometryStore,
    selected_items: Iterable[RenderableGeometry],
    rules: Iterable[LayerSelectionRule],
    *,
    active_side: str,
    via_items: Iterable[RenderableGeometry] | None = None,
    profiler: RenderProfiler | None = None,
) -> tuple[_PrimitiveLayerItem, ...]:
    selected_non_vias = tuple(item for item in selected_items if item.kind is not GeometryKind.VIA)
    selected_copper_layers = _selected_copper_layers(store, rules, active_side=active_side)
    selected_via_items = store.by_kind(GeometryKind.VIA) if via_items is None else tuple(via_items)
    items: list[_PrimitiveLayerItem] = [
        _PrimitiveLayerItem(source=item, layer=item.layer) for item in selected_non_vias
    ]

    if profiler is not None:
        profiler.metric(
            "primitive.selected_source_items",
            nonVias=len(selected_non_vias),
            vias=len(selected_via_items),
            copperLayers=len(selected_copper_layers),
        )

    for item in selected_via_items:
        via_layers = _via_layers(item)
        for layer in selected_copper_layers:
            if layer.name in via_layers or "*.Cu" in via_layers:
                items.append(_PrimitiveLayerItem(source=item, layer=layer))
    return tuple(items)


def _groupable_artwork(
    store: PcbGeometryStore,
    selected_items: Iterable[RenderableGeometry],
    rules: Iterable[LayerSelectionRule],
    *,
    active_side: str,
    via_items: Iterable[RenderableGeometry] | None = None,
    profiler: RenderProfiler | None = None,
) -> tuple[_GroupedArtwork, ...]:
    grouped: list[_GroupedArtwork] = []
    profiled_artwork: list[_ProfiledArtwork] = []
    selected_non_vias = tuple(item for item in selected_items if item.kind is not GeometryKind.VIA)
    trace_groups: dict[_TraceArtworkKey, list[RenderableGeometry]] = defaultdict(list)
    selected_other_items: list[RenderableGeometry] = []
    for item in selected_non_vias:
        trace_key = _trace_artwork_key(item)
        if trace_key is None:
            selected_other_items.append(item)
        else:
            trace_groups[trace_key].append(item)

    if profiler is None:
        grouped.extend(_trace_grouped_artwork(trace_groups))
        for item in selected_other_items:
            artwork = geometry_to_artwork(item)
            if artwork is None:
                continue
            grouped.append(_GroupedArtwork(artwork=artwork, layer=item.layer, source_items=(item,)))
    else:
        with profiler.span("artwork.convert_non_vias", items=len(selected_non_vias)):
            trace_artwork = _trace_grouped_artwork(trace_groups)
            grouped.extend(trace_artwork)
            profiled_artwork.extend(
                _ProfiledArtwork(kind=GeometryKind.TRACE, grouped=item) for item in trace_artwork
            )
            for item in selected_other_items:
                artwork = geometry_to_artwork(item)
                if artwork is None:
                    continue
                grouped_item = _GroupedArtwork(
                    artwork=artwork,
                    layer=item.layer,
                    source_items=(item,),
                )
                grouped.append(grouped_item)
                profiled_artwork.append(_ProfiledArtwork(kind=item.kind, grouped=grouped_item))
        _profile_artwork_by_kind(profiler, profiled_artwork)

    selected_copper_layers = _selected_copper_layers(store, rules, active_side=active_side)
    selected_via_items = store.by_kind(GeometryKind.VIA) if via_items is None else tuple(via_items)

    def append_vias() -> None:
        via_profiled_artwork: list[_ProfiledArtwork] = []
        for item in selected_via_items:
            artwork = geometry_to_artwork(item)
            if artwork is None:
                continue
            via_layers = _via_layers(item)
            for layer in selected_copper_layers:
                if layer.name not in via_layers and "*.Cu" not in via_layers:
                    continue
                grouped_item = _GroupedArtwork(
                    artwork=ArtworkItem(
                        geometry=artwork.geometry,
                        source_ids=artwork.source_ids,
                        source_layers=(layer.name,),
                        tags=artwork.tags,
                    ),
                    layer=layer,
                    source_items=(item,),
                )
                grouped.append(grouped_item)
                via_profiled_artwork.append(
                    _ProfiledArtwork(kind=GeometryKind.VIA, grouped=grouped_item)
                )
        if profiler is not None:
            _profile_artwork_by_kind(profiler, via_profiled_artwork)

    if profiler is None:
        append_vias()
    else:
        with profiler.span(
            "artwork.convert_vias",
            vias=len(selected_via_items),
            copper_layers=len(selected_copper_layers),
        ):
            append_vias()
    return tuple(grouped)


def _trace_artwork_key(item: RenderableGeometry) -> _TraceArtworkKey | None:
    payload = item.payload if item.payload is not None else item.source
    if item.kind in (GeometryKind.TRACE, GeometryKind.TRACE_ARC) and isinstance(
        payload, PcbSegment | PcbTraceArc
    ):
        width = payload.width
    else:
        return None
    return _TraceArtworkKey(
        layer_name=item.layer.name,
        net_number=item.tags.net_number,
        net_name=item.tags.net_name,
        width=width,
    )


def _trace_grouped_artwork(
    trace_groups: dict[_TraceArtworkKey, list[RenderableGeometry]],
) -> tuple[_GroupedArtwork, ...]:
    grouped: list[_GroupedArtwork] = []
    for items in trace_groups.values():
        artwork = _trace_artwork_from_items(items)
        if artwork is not None:
            grouped.append(
                _GroupedArtwork(
                    artwork=artwork,
                    layer=items[0].layer,
                    source_items=tuple(items),
                )
            )
    return tuple(grouped)


def _profile_artwork_by_kind(
    profiler: RenderProfiler,
    items: Iterable[_ProfiledArtwork],
) -> None:
    grouped: dict[tuple[GeometryKind, str, str, str], list[ArtworkItem]] = defaultdict(list)
    for item in items:
        layer = item.grouped.layer
        grouped[(item.kind, layer.name, layer.role, layer.side)].append(item.grouped.artwork)

    for (kind, layer_name, function, side), artwork in sorted(
        grouped.items(),
        key=lambda item: (item[0][1], item[0][0].value),
    ):
        profiler.metric(
            "artwork.converted_by_kind",
            kind=kind.value,
            layer=layer_name,
            function=function,
            side=side,
            groups=len(artwork),
            sourceItems=sum(len(item.source_ids) for item in artwork),
            **_geometry_complexity(item.geometry for item in artwork).profile_data(),
        )


def _trace_artwork_from_items(items: list[RenderableGeometry]) -> ArtworkItem | None:
    centerlines: list[LineString] = []
    for item in items:
        centerline = _trace_centerline(item)
        if centerline is not None and not centerline.is_empty:
            centerlines.append(centerline)
    if not centerlines:
        return None

    unioned_centerlines = robust_union(centerlines)
    merged = (
        unioned_centerlines
        if isinstance(unioned_centerlines, LineString)
        else linemerge(cast("MultiLineString", unioned_centerlines))
    )
    geometry = merged.buffer(
        _trace_width(items[0]) / 2,
        cap_style="flat",
        join_style="mitre",
    )
    if geometry.is_empty:
        return None
    return ArtworkItem(
        geometry=geometry,
        source_ids=tuple(item.id for item in items),
        source_layers=_unique_ordered(item.layer.name for item in items),
        tags=items[0].tags,
    )


def _trace_centerline(item: RenderableGeometry) -> LineString | None:
    payload = item.payload if item.payload is not None else item.source
    if item.kind is GeometryKind.TRACE and isinstance(payload, PcbSegment):
        return LineString([(payload.start_x, payload.start_y), (payload.end_x, payload.end_y)])
    if item.kind is GeometryKind.TRACE_ARC and isinstance(payload, PcbTraceArc):
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
    return None


def _trace_width(item: RenderableGeometry) -> float:
    payload = item.payload if item.payload is not None else item.source
    if isinstance(payload, PcbSegment | PcbTraceArc):
        return payload.width
    return 0.0


def _should_dim_base_layers(store: PcbGeometryStore, settings: RenderSettings) -> bool:
    if not settings.dimming.enabled:
        return False
    return any(
        _selected_highlight_items(store, settings, highlight)
        or _selected_highlight_vias(store, settings, highlight)
        for highlight in settings.highlights
    )


def _selected_highlight_items(
    store: PcbGeometryStore,
    settings: RenderSettings,
    highlight: HighlightSpec,
) -> tuple[RenderableGeometry, ...]:
    selected_items = _filter_excluded_components(
        select_source_artwork(store, settings.source.layers, active_side=settings.side),
        settings.source.exclude_components,
    )
    return tuple(
        item
        for item in selected_items
        if item.kind is not GeometryKind.VIA and _matches_highlight(item, highlight)
    )


def _selected_highlight_vias(
    store: PcbGeometryStore,
    settings: RenderSettings,
    highlight: HighlightSpec,
) -> tuple[RenderableGeometry, ...]:
    selected_copper_layers = _selected_copper_layers(
        store,
        settings.source.layers,
        active_side=settings.side,
    )
    if not selected_copper_layers:
        return ()
    return tuple(
        item
        for item in store.by_kind(GeometryKind.VIA)
        if _matches_highlight(item, highlight)
        and _via_intersects_selected_layers(item, selected_copper_layers)
    )


def _matches_highlight(item: RenderableGeometry, highlight: HighlightSpec) -> bool:
    if highlight.net:
        return item.tags.net_name.casefold() == highlight.net.casefold()
    if highlight.component:
        return item.tags.component_ref.casefold() == highlight.component.casefold()
    if highlight.pad:
        component, _separator, pad_number = highlight.pad.partition(".")
        return (
            item.tags.component_ref.casefold() == component.casefold()
            and item.tags.pad_number.casefold() == pad_number.casefold()
        )
    return False


def _via_intersects_selected_layers(
    item: RenderableGeometry,
    selected_layers: Iterable[GeometryLayer],
) -> bool:
    via_layers = _via_layers(item)
    return any(layer.name in via_layers or "*.Cu" in via_layers for layer in selected_layers)


def _highlight_target(highlight: HighlightSpec) -> str:
    if highlight.net:
        return f"net:{highlight.net}"
    if highlight.component:
        return f"component:{highlight.component}"
    return f"pad:{highlight.pad}"


def _filter_excluded_components(
    items: Iterable[RenderableGeometry],
    excluded_prefixes: tuple[str, ...],
) -> tuple[RenderableGeometry, ...]:
    if not excluded_prefixes:
        return tuple(items)
    return tuple(
        item
        for item in items
        if not item.tags.component_prefix
        or item.tags.component_prefix.upper() not in excluded_prefixes
    )


def _selected_copper_layers(
    store: PcbGeometryStore,
    rules: Iterable[LayerSelectionRule],
    *,
    active_side: str,
) -> tuple[GeometryLayer, ...]:
    rule_tuple = tuple(rules)
    known_layers = {
        item.layer.name: item.layer for item in store.items if item.layer.role == "copper"
    }
    layers: dict[str, GeometryLayer] = {}
    for item in store.items:
        if item.layer.role != "copper":
            continue
        if not any(
            _rule_selects_layer(
                rule,
                item.layer,
                object_name="via",
                active_side=active_side,
            )
            for rule in rule_tuple
        ):
            continue
        layers[item.layer.name] = item.layer

    for item in store.by_kind(GeometryKind.VIA):
        for layer_name in _via_layers(item):
            if layer_name == "*.Cu":
                continue
            layer = known_layers.get(layer_name, _copper_layer_for_name(layer_name))
            if any(
                _rule_selects_layer(
                    rule,
                    layer,
                    object_name="via",
                    active_side=active_side,
                )
                for rule in rule_tuple
            ):
                layers[layer.name] = layer
    return tuple(layers.values())


def _rule_selects_layer(
    rule: LayerSelectionRule,
    layer: GeometryLayer,
    *,
    object_name: str,
    active_side: str,
) -> bool:
    if not rule.visible:
        return False
    if rule.match.name and layer.name != rule.match.name:
        return False
    if rule.match.function and layer.role != _source_function_layer_role(rule.match.function):
        return False
    match_side = active_side if rule.match.side == "active" else rule.match.side
    if match_side and layer.side != match_side:
        return False
    return not rule.objects or object_name in _normalized_objects(rule.objects)


def _source_function_layer_role(function: str) -> str:
    return _SOURCE_FUNCTION_TO_LAYER_ROLE.get(function, function)


def _normalized_objects(objects: Iterable[str]) -> frozenset[str]:
    return frozenset(obj.strip().lower().replace("-", "_") for obj in objects)


def _via_layers(item: RenderableGeometry) -> frozenset[str]:
    payload = item.payload if item.payload is not None else item.source
    if not isinstance(payload, PcbVia):
        return frozenset()
    return frozenset(str(layer) for layer in payload.layers)


def _copper_layer_for_name(name: str) -> GeometryLayer:
    if name in {"F.Cu", "Top Layer"}:
        return GeometryLayer(name=name, role="copper", side="front", stack_index=10_000)
    if name in {"B.Cu", "Bottom Layer"}:
        return GeometryLayer(name=name, role="copper", side="back", stack_index=0)
    return GeometryLayer(
        name=name,
        role="copper",
        side="inner",
        stack_index=_inner_layer_index(name),
    )


def _inner_layer_index(name: str) -> int:
    digits = "".join(character for character in name if character.isdigit())
    if not digits:
        return 5_000
    return int(digits)


def _group_key(layer: GeometryLayer) -> _LayerGroupKey:
    side = layer.side
    inner_index = layer.stack_index if side == "inner" else None
    return _LayerGroupKey(
        function=layer.role,
        side=side,
        inner_index=inner_index,
        source_layer_name=layer.name,
    )


def _group_sort_key(
    item: tuple[_LayerGroupKey, list[_GroupedArtwork] | list[_PrimitiveLayerItem]],
) -> tuple[int, str]:
    key, group = item
    stack_index = group[0].layer.stack_index if group else 0
    return (stack_index, key.source_layer_name)


def _primitive_layer_primitives(
    key: _LayerGroupKey,
    group: list[_PrimitiveLayerItem],
    *,
    profiler: RenderProfiler | None = None,
) -> tuple[SvgPrimitive, ...]:
    if profiler is None:
        return _primitive_layer_primitives_without_profiling(key, group)
    with profiler.span(
        "artwork.convert_primitives",
        layer=key.source_layer_name,
        function=key.function,
        side=key.side,
        items=len(group),
    ):
        primitives = _primitive_layer_primitives_without_profiling(key, group)
    profiler.metric(
        "artwork.converted_primitives",
        layer=key.source_layer_name,
        function=key.function,
        side=key.side,
        sourceItems=len(group),
        primitives=len(primitives),
        pathCharacters=sum(len(primitive.d) for primitive in primitives),
    )
    return primitives


def _primitive_layer_primitives_without_profiling(
    key: _LayerGroupKey,
    group: list[_PrimitiveLayerItem],
) -> tuple[SvgPrimitive, ...]:
    primitives: list[SvgPrimitive] = []
    for item in sorted(group, key=_primitive_layer_item_sort_key):
        primitive = geometry_to_svg_primitive(
            item.source,
            target_layer_name=key.source_layer_name,
        )
        if primitive is not None:
            primitives.append(primitive)
    return tuple(primitives)


def _group_primitives_without_profiling(
    key: _LayerGroupKey,
    group: list[_GroupedArtwork],
) -> tuple[SvgPrimitive, ...]:
    primitives: list[SvgPrimitive] = []
    for item in sorted(group, key=_grouped_artwork_primitive_sort_key):
        source_primitives = _source_item_primitives(item, target_layer_name=key.source_layer_name)
        if source_primitives is not None:
            primitives.extend(source_primitives)
            continue
        primitives.extend(
            svg_primitives_from_geometry(
                item.artwork.geometry,
                source_ids=item.artwork.source_ids,
                source_layers=item.artwork.source_layers,
                kind=_grouped_artwork_kind(item),
                tags=item.artwork.tags,
            )
        )
    return tuple(primitives)


def _source_item_primitives(
    item: _GroupedArtwork,
    *,
    target_layer_name: str,
) -> tuple[SvgPrimitive, ...] | None:
    if not item.source_items:
        return None
    primitives: list[SvgPrimitive] = []
    for source_item in item.source_items:
        primitive = geometry_to_svg_primitive(
            source_item,
            target_layer_name=target_layer_name,
        )
        if primitive is None:
            return None
        primitives.append(primitive)
    return tuple(primitives)


_PRIMITIVE_KIND_ORDER = {
    GeometryKind.TRACE: 0,
    GeometryKind.TRACE_ARC: 0,
    GeometryKind.ZONE: 1,
    GeometryKind.SILK_POLYGON: 1,
    GeometryKind.FAB_POLYGON: 1,
    GeometryKind.BODY_POLYGON: 1,
    GeometryKind.MASK: 1,
    GeometryKind.PASTE: 1,
    GeometryKind.MECHANICAL: 1,
    GeometryKind.PAD: 2,
    GeometryKind.VIA: 2,
}


def _primitive_layer_item_sort_key(item: _PrimitiveLayerItem) -> tuple[int, str]:
    return (_PRIMITIVE_KIND_ORDER.get(item.source.kind, 3), item.source.id)


def _grouped_artwork_primitive_sort_key(item: _GroupedArtwork) -> tuple[int, str]:
    kind = _grouped_artwork_kind(item)
    source_id = item.artwork.source_ids[0] if item.artwork.source_ids else ""
    return (_PRIMITIVE_KIND_ORDER.get(kind, 3), source_id)


def _realistic_copper_primitives(
    grouped_artwork: Iterable[_GroupedArtwork],
    *,
    side: str,
) -> tuple[SvgPrimitive, ...]:
    primitives: list[SvgPrimitive] = []
    for item in grouped_artwork:
        if item.layer.role != "copper" or item.layer.side != side:
            continue
        primitives.extend(_group_primitives_without_profiling(_group_key(item.layer), [item]))
    return tuple(primitives)


def _grouped_artwork_kind(item: _GroupedArtwork) -> GeometryKind:
    if item.source_items:
        return item.source_items[0].kind
    return GeometryKind.MECHANICAL


def _mask_primitives_from_geometry(
    geometry: BaseGeometry,
    *,
    kind: GeometryKind,
    source_ids: Iterable[str],
    source_layers: Iterable[str],
) -> tuple[SvgPrimitive, ...]:
    return svg_primitives_from_geometry(
        geometry,
        source_ids=source_ids,
        source_layers=source_layers,
        kind=kind,
        tags=GeometryTags(),
    )


def _process_artwork_layer(
    store: PcbGeometryStore,
    geometries: Iterable[BaseGeometry],
    *,
    layer_name: str | None = None,
    subtract_drills: bool = False,
    drill_cache: dict[str, BaseGeometry] | None = None,
    prefer_disjoint_subsets: bool = True,
    profiler: RenderProfiler | None = None,
    profile: _LayerProcessingProfile | None = None,
) -> BaseGeometry:
    geometry_tuple = tuple(geometries)
    profile_data = (
        {
            "layer": profile.layer,
            "function": profile.function,
            "side": profile.side,
            "items": profile.items,
        }
        if profile is not None
        else {}
    )

    if profiler is None or profile is None:
        geometry = _union_or_empty(
            geometry_tuple,
            prefer_disjoint_subsets=prefer_disjoint_subsets,
        )
    else:
        profiler.metric(
            f"{profile.event_prefix}.input_geometry",
            **profile_data,
            **_geometry_complexity(geometry_tuple).profile_data(),
        )
        with profiler.span(f"{profile.event_prefix}.union", **profile_data):
            geometry = _union_or_empty(
                geometry_tuple,
                prefer_disjoint_subsets=prefer_disjoint_subsets,
            )

    if subtract_drills and layer_name is not None:
        if profiler is None or profile is None:
            drills = _drill_geometry_for_layer(store, layer_name, drill_cache=drill_cache)
        else:
            with profiler.span(
                f"{profile.event_prefix}.drills",
                **profile_data,
                cached=drill_cache is not None and layer_name in drill_cache,
            ):
                drills = _drill_geometry_for_layer(store, layer_name, drill_cache=drill_cache)
        if not drills.is_empty:
            if profiler is None or profile is None:
                geometry = _difference(geometry, drills)
            else:
                with profiler.span(
                    f"{profile.event_prefix}.drill_difference",
                    **profile_data,
                ):
                    geometry = _difference(geometry, drills)
    if profiler is not None and profile is not None:
        profiler.metric(
            f"{profile.event_prefix}.output_geometry",
            **profile_data,
            **_geometry_complexity((geometry,)).profile_data(),
        )
    return geometry


def _geometry_complexity(geometries: Iterable[BaseGeometry]) -> _GeometryComplexity:
    complexity = _GeometryComplexity()
    for geometry in geometries:
        complexity = complexity.plus(_single_geometry_complexity(geometry))
    return complexity


def _single_geometry_complexity(geometry: BaseGeometry) -> _GeometryComplexity:
    if geometry.is_empty:
        return _GeometryComplexity(geometries=1)
    if isinstance(geometry, Polygon):
        rings = (geometry.exterior, *geometry.interiors)
        coordinates = sum(len(ring.coords) for ring in rings)
        return _GeometryComplexity(
            geometries=1,
            polygons=1,
            rings=len(rings),
            coordinates=coordinates,
            max_coordinates=coordinates,
        )
    if isinstance(geometry, MultiPolygon):
        complexity = _GeometryComplexity()
        for polygon in geometry.geoms:
            complexity = complexity.plus(_single_geometry_complexity(polygon))
        return complexity
    if isinstance(geometry, LineString):
        coordinates = len(geometry.coords)
        return _GeometryComplexity(
            geometries=1,
            rings=1,
            coordinates=coordinates,
            max_coordinates=coordinates,
        )
    if isinstance(geometry, MultiLineString):
        complexity = _GeometryComplexity()
        for part in cast("tuple[BaseGeometry, ...]", tuple(geometry.geoms)):
            complexity = complexity.plus(_single_geometry_complexity(part))
        return complexity
    if isinstance(geometry, GeometryCollection):
        complexity = _GeometryComplexity()
        collection = cast("GeometryCollection[BaseGeometry]", geometry)
        for part in tuple(collection.geoms):
            complexity = complexity.plus(_single_geometry_complexity(part))
        return complexity
    return _GeometryComplexity(geometries=1)


def _side_artwork(
    selected_items: Iterable[RenderableGeometry],
    *,
    role: str,
    side: str,
) -> tuple[ArtworkItem, ...]:
    artwork: list[ArtworkItem] = []
    for item in selected_items:
        if item.layer.role != role or item.layer.side != side:
            continue
        artwork_item = geometry_to_artwork(item)
        if artwork_item is not None:
            artwork.append(artwork_item)
    return tuple(artwork)


def _surface_drill_geometry(store: PcbGeometryStore) -> BaseGeometry:
    """Return drills visible through board surfaces.

    V1 treats parsed drills and vias as through-board openings. Span-aware
    filtering and tenting semantics can be added behind this helper later.
    """
    return drill_geometry_for_layer(store)


def _layer_mask(store: PcbGeometryStore, board: BaseGeometry) -> LayerMask | None:
    if board.is_empty:
        return None
    return LayerMask(
        board=_mask_primitives_from_geometry(
            board,
            kind=GeometryKind.BOARD_MATERIAL,
            source_ids=_board_source_ids(store),
            source_layers=_board_source_layers(store),
        ),
        drills=_mask_primitives_from_geometry(
            _surface_drill_geometry(store),
            kind=GeometryKind.DRILL,
            source_ids=_drill_source_ids(store),
            source_layers=("drills",),
        ),
    )


def _drill_geometry_for_layer(
    store: PcbGeometryStore,
    layer_name: str,
    *,
    drill_cache: dict[str, BaseGeometry] | None,
) -> BaseGeometry:
    if drill_cache is None:
        return drill_geometry_for_layer(store, layer_name=layer_name)
    if layer_name not in drill_cache:
        drill_cache[layer_name] = drill_geometry_for_layer(store, layer_name=layer_name)
    return drill_cache[layer_name]


def _difference(geometry: BaseGeometry, subtractive: BaseGeometry) -> BaseGeometry:
    if geometry.is_empty or subtractive.is_empty:
        return geometry
    return robust_difference(geometry, subtractive)


def _intersection(left: BaseGeometry, right: BaseGeometry) -> BaseGeometry:
    if left.is_empty or right.is_empty:
        return GeometryCollection()
    return robust_intersection(left, right)


def _outline_geometry(board: BaseGeometry) -> BaseGeometry:
    if board.is_empty:
        return GeometryCollection()
    return board.boundary


def _union_or_empty(
    geometries: Iterable[BaseGeometry],
    *,
    prefer_disjoint_subsets: bool = False,
) -> BaseGeometry:
    geometry_tuple = tuple(geometries)
    if not geometry_tuple:
        return GeometryCollection()
    if len(geometry_tuple) == 1:
        return geometry_tuple[0]
    return robust_union(geometry_tuple, prefer_disjoint_subsets=prefer_disjoint_subsets)


def _derived_layer_id(role: VisualRole) -> str:
    parts = [role.namespace, role.function]
    if role.side:
        parts.append(role.side)
    if role.side == "inner" and role.inner_index is not None:
        parts.append(str(role.inner_index))
    return ":".join(parts)


def _unique_ordered(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _source_ids_by_store_order_for_primitives(
    store: PcbGeometryStore,
    primitives: Iterable[SvgPrimitive],
) -> tuple[str, ...]:
    selected_ids = {primitive.source_id for primitive in primitives}
    return tuple(item.id for item in store.items if item.id in selected_ids)


def _source_layers_from_artwork(artwork: Iterable[ArtworkItem]) -> tuple[str, ...]:
    return _unique_ordered(layer for item in artwork for layer in item.source_layers)


def _source_ids_from_artwork(artwork: Iterable[ArtworkItem]) -> tuple[str, ...]:
    return _unique_ordered(source_id for item in artwork for source_id in item.source_ids)


def _source_ids_by_store_order_for_artwork(
    store: PcbGeometryStore,
    artwork: Iterable[ArtworkItem],
) -> tuple[str, ...]:
    selected_ids = {source_id for item in artwork for source_id in item.source_ids}
    return tuple(item.id for item in store.items if item.id in selected_ids)


def _board_source_layers(store: PcbGeometryStore) -> tuple[str, ...]:
    return _unique_ordered(item.layer.name for item in store.by_kind(GeometryKind.BOARD_OUTLINE))


def _board_source_ids(store: PcbGeometryStore) -> tuple[str, ...]:
    return tuple(item.id for item in store.by_kind(GeometryKind.BOARD_OUTLINE))


def _drill_source_ids(store: PcbGeometryStore) -> tuple[str, ...]:
    return tuple(
        item.id
        for item in store.items
        if item.kind is GeometryKind.DRILL or item.kind is GeometryKind.VIA
    )
