"""Render-mode projections from PCB source artwork to derived visual layers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from shapely import GeometryCollection, LineString, MultiLineString, MultiPolygon, Polygon
from shapely.ops import linemerge

from phosphor_eda.pcb import PcbPad, PcbSegment, PcbTraceArc, PcbVia
from phosphor_eda.pcb_render_artwork import (
    ArtworkItem,
    DerivedLayer,
    LayerClip,
    LayerClipCircle,
    board_outline_geometry,
    drill_geometry_for_layer,
    geometry_to_artwork,
    select_source_artwork,
)
from phosphor_eda.pcb_render_geometry import GeometryKind, GeometryLayer
from phosphor_eda.pcb_render_skia import SkiaArtwork, geometry_to_skia_artwork, union_skia_artwork
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
class _LayerSkiaSource:
    item: RenderableGeometry
    target_layer_name: str


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
    grouped_artwork = _groupable_artwork(
        store,
        selected_items,
        settings.source.layers,
        active_side=settings.side,
        profiler=profiler,
    )
    if profiler is not None:
        profiler.metric("cad.grouped_artwork", count=len(grouped_artwork))
    board = board_outline_geometry(store)
    layer_clip = _layer_clip(store, board)
    dimmed = _should_dim_base_layers(store, settings)
    warned_missing_dimmed_tokens: set[str] = set()
    drill_cache: dict[str, BaseGeometry] = {}

    groups: dict[_LayerGroupKey, list[_GroupedArtwork]] = defaultdict(list)
    for item in grouped_artwork:
        groups[_group_key(item.layer)].append(item)

    layers: list[DerivedLayer] = []
    for key, group in sorted(groups.items(), key=_group_sort_key):
        if profiler is None:
            geometry = _resolved_group_geometry(
                store,
                group,
                drill_cache=drill_cache,
            )
        else:
            with profiler.span(
                "cad.resolve_group",
                layer=key.source_layer_name,
                function=key.function,
                side=key.side,
                items=len(group),
            ):
                geometry = _resolved_group_geometry(
                    store,
                    group,
                    drill_cache=drill_cache,
                    profiler=profiler,
                    profile_prefix="cad",
                )
        if geometry.is_empty:
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
        source_layers = _unique_ordered(
            layer for item in group for layer in item.artwork.source_layers
        )
        source_ids = _source_ids_by_store_order(store, group)
        path_data = _cad_copper_path_data(
            key,
            group,
            warn=warn,
            profiler=profiler,
        )
        layers.append(
            DerivedLayer(
                id=_derived_layer_id(role),
                role=role,
                geometry=geometry,
                source_layers=source_layers,
                source_ids=source_ids,
                style=style,
                data={"source-layer": key.source_layer_name},
                clip=None if key.function == "edge" else layer_clip,
                path_data=path_data,
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
    layer_clip = LayerClip(
        board=board,
        drills=surface_drills,
        drill_circles=_surface_drill_circles(store),
    )
    dimmed = _should_dim_base_layers(store, settings)
    warned_missing_dimmed_tokens: set[str] = set()

    mask_artwork = _side_artwork(selected_items, role="mask", side=side)
    copper_artwork = tuple(
        item.artwork
        for item in grouped_artwork
        if item.layer.role == "copper" and item.layer.side == side
    )
    copper_skia_sources = tuple(
        _LayerSkiaSource(source_item, item.layer.name)
        for item in grouped_artwork
        if item.layer.role == "copper" and item.layer.side == side
        for source_item in item.source_items
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

    covered_copper_path_data = _copper_path_data(
        event_prefix="realistic.covered_copper.skia",
        layer=_profile_layer_name(copper_skia_sources),
        function="coveredCopper",
        side=side,
        items=len(copper_artwork),
        sources=copper_skia_sources,
        warn=warn,
        profiler=profiler,
    )
    layer_inputs = {
        "substrate": _RealisticLayerInput(
            geometry=board,
            source_layers=_board_source_layers(store),
            source_ids=_board_source_ids(store),
        ),
        "solderMask": _RealisticLayerInput(
            geometry=_difference(board, mask_openings),
            source_layers=_source_layers_from_artwork(mask_artwork),
            source_ids=_source_ids_from_artwork(mask_artwork),
        ),
        "coveredCopper": _RealisticLayerInput(
            geometry=outer_copper,
            source_layers=_source_layers_from_artwork(copper_artwork),
            source_ids=_source_ids_by_store_order_for_artwork(store, copper_artwork),
            path_data=covered_copper_path_data,
        ),
        "exposedCopper": _RealisticLayerInput(
            geometry=_intersection(outer_copper, mask_openings),
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
            source_layers=_source_layers_from_artwork(silkscreen_artwork),
            source_ids=_source_ids_by_store_order_for_artwork(store, silkscreen_artwork),
        ),
        "boardOutline": _RealisticLayerInput(
            geometry=_outline_geometry(board),
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
                geometry=geometry,
                source_layers=layer_input.source_layers,
                source_ids=layer_input.source_ids,
                style=style,
                clip=None if function == "boardOutline" else layer_clip,
                path_data=layer_input.path_data,
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
    layer_clip = _layer_clip(store, board)
    groups: list[HighlightGroup] = []
    drill_cache: dict[str, BaseGeometry] = {}

    for highlight in settings.highlights:
        selected_items = _selected_highlight_items(store, settings, highlight)
        selected_vias = _selected_highlight_vias(store, settings, highlight)
        grouped_artwork = _groupable_artwork(
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
                grouped=len(grouped_artwork),
            )
        if not grouped_artwork:
            continue

        by_layer: dict[_LayerGroupKey, list[_GroupedArtwork]] = defaultdict(list)
        for item in grouped_artwork:
            by_layer[_group_key(item.layer)].append(item)

        target = _highlight_target(highlight)
        layers: list[DerivedLayer] = []
        for key, layer_group in sorted(by_layer.items(), key=_group_sort_key):
            if profiler is None:
                geometry = _resolved_group_geometry(
                    store,
                    layer_group,
                    drill_cache=drill_cache,
                )
            else:
                with profiler.span(
                    "highlight.resolve_group",
                    target=target,
                    layer=key.source_layer_name,
                    function=key.function,
                    side=key.side,
                    items=len(layer_group),
                ):
                    geometry = _resolved_group_geometry(
                        store,
                        layer_group,
                        drill_cache=drill_cache,
                        profiler=profiler,
                        profile_prefix="highlight",
                    )
            if geometry.is_empty:
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
            path_data = _highlight_copper_path_data(
                key,
                layer_group,
                warn=warn,
                profiler=profiler,
            )
            layers.append(
                DerivedLayer(
                    id=_derived_layer_id(role),
                    role=role,
                    geometry=geometry,
                    source_layers=_unique_ordered(
                        layer for item in layer_group for layer in item.artwork.source_layers
                    ),
                    source_ids=_source_ids_by_store_order(store, layer_group),
                    style=style,
                    data={
                        "data-highlight-target": target,
                        "source-layer": key.source_layer_name,
                    },
                    clip=None if key.function == "edge" else layer_clip,
                    path_data=path_data,
                )
            )

        if layers:
            groups.append(HighlightGroup(target=target, layers=tuple(layers)))

    return tuple(groups)


@dataclass(frozen=True)
class _RealisticLayerInput:
    geometry: BaseGeometry
    source_layers: tuple[str, ...]
    source_ids: tuple[str, ...]
    path_data: str = ""


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


def _group_sort_key(item: tuple[_LayerGroupKey, list[_GroupedArtwork]]) -> tuple[int, int, str]:
    key, group = item
    stack_index = group[0].layer.stack_index if group else 0
    return (stack_index, len(group), key.source_layer_name)


def _cad_copper_path_data(
    key: _LayerGroupKey,
    group: list[_GroupedArtwork],
    *,
    warn: Callable[[str], None],
    profiler: RenderProfiler | None = None,
) -> str:
    if key.function != "copper":
        return ""

    return _copper_path_data(
        event_prefix="cad.skia",
        layer=key.source_layer_name,
        function=key.function,
        side=key.side,
        items=len(group),
        sources=(
            _LayerSkiaSource(source_item, key.source_layer_name)
            for item in group
            for source_item in item.source_items
        ),
        warn=warn,
        profiler=profiler,
    )


def _highlight_copper_path_data(
    key: _LayerGroupKey,
    group: list[_GroupedArtwork],
    *,
    warn: Callable[[str], None],
    profiler: RenderProfiler | None = None,
) -> str:
    if key.function != "copper":
        return ""

    return _copper_path_data(
        event_prefix="highlight.skia",
        layer=key.source_layer_name,
        function=key.function,
        side=key.side,
        items=len(group),
        sources=(
            _LayerSkiaSource(source_item, key.source_layer_name)
            for item in group
            for source_item in item.source_items
        ),
        warn=warn,
        profiler=profiler,
    )


def _copper_path_data(
    *,
    event_prefix: str,
    layer: str,
    function: str,
    side: str,
    items: int,
    sources: Iterable[_LayerSkiaSource],
    warn: Callable[[str], None],
    profiler: RenderProfiler | None = None,
) -> str:
    source_tuple = tuple(sources)
    if not source_tuple:
        return ""

    profile_data = {
        "layer": layer,
        "function": function,
        "side": side,
        "items": items,
        "sourceItems": len(source_tuple),
    }
    if profiler is not None:
        profiler.metric(f"{event_prefix}.input", **profile_data)

    if profiler is None:
        skia_artwork = _skia_artwork_for_layer_sources(source_tuple)
    else:
        with profiler.span(f"{event_prefix}.convert", **profile_data):
            skia_artwork = _skia_artwork_for_layer_sources(source_tuple)

    if len(skia_artwork) != len(source_tuple):
        unsupported_items = len(source_tuple) - len(skia_artwork)
        if profiler is not None:
            profiler.metric(
                f"{event_prefix}.fallback",
                **profile_data,
                convertedItems=len(skia_artwork),
                unsupportedItems=unsupported_items,
            )
        friendly_label = _skia_warning_label(event_prefix)
        warning = (
            f"Skia copper path fallback for {friendly_label} layer {layer} "
            f"({function}/{side or 'all'}): {unsupported_items} unsupported "
            f"of {len(source_tuple)} source items; using Shapely geometry for whole layer."
        )
        warn(warning)
        return ""

    try:
        if profiler is None:
            skia_path_data = union_skia_artwork(skia_artwork)
        else:
            with profiler.span(f"{event_prefix}.union", **profile_data):
                skia_path_data = union_skia_artwork(skia_artwork)
            profiler.metric(
                f"{event_prefix}.output",
                **profile_data,
                pathCharacters=skia_path_data.path_characters,
                moveCommands=skia_path_data.move_commands,
                lineCommands=skia_path_data.line_commands,
                curveCommands=skia_path_data.curve_commands,
            )
    except Exception as exc:
        if profiler is not None:
            profiler.metric(
                f"{event_prefix}.fallback",
                **profile_data,
                convertedItems=len(skia_artwork),
                unsupportedItems=0,
                errorType=type(exc).__name__,
            )
        warning = (
            f"Skia copper path fallback for {_skia_warning_label(event_prefix)} layer {layer} "
            f"({function}/{side or 'all'}): Skia union failed with {type(exc).__name__}; "
            f"using Shapely geometry for whole layer."
        )
        warn(warning)
        return ""
    return skia_path_data.d


def _skia_artwork_for_layer_sources(
    sources: Iterable[_LayerSkiaSource],
) -> tuple[SkiaArtwork, ...]:
    artwork: list[SkiaArtwork] = []
    for source in sources:
        skia_artwork = geometry_to_skia_artwork(
            source.item,
            target_layer_name=source.target_layer_name,
        )
        if skia_artwork is not None:
            artwork.append(skia_artwork)
    return tuple(artwork)


def _skia_warning_label(event_prefix: str) -> str:
    labels = {
        "cad.skia": "CAD copper",
        "highlight.skia": "highlight copper",
        "realistic.covered_copper.skia": "realistic covered copper",
    }
    return labels.get(event_prefix, "copper")


def _profile_layer_name(sources: Iterable[_LayerSkiaSource]) -> str:
    names = tuple(dict.fromkeys(source.target_layer_name for source in sources))
    return ",".join(names)


def _resolved_group_geometry(
    store: PcbGeometryStore,
    group: list[_GroupedArtwork],
    *,
    drill_cache: dict[str, BaseGeometry] | None = None,
    profiler: RenderProfiler | None = None,
    profile_prefix: str = "",
) -> BaseGeometry:
    layer = group[0].layer
    if layer.role == "edge":
        board = board_outline_geometry(store)
        return _outline_geometry(board)

    return _process_artwork_layer(
        store,
        (item.artwork.geometry for item in group),
        layer_name=layer.name,
        subtract_drills=False,
        drill_cache=drill_cache,
        prefer_disjoint_subsets=False,
        profiler=profiler,
        profile=(
            _LayerProcessingProfile(
                event_prefix=f"{profile_prefix}.resolve_group",
                layer=layer.name,
                function=layer.role,
                side=layer.side,
                items=len(group),
            )
            if profiler is not None
            else None
        ),
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


def _layer_clip(store: PcbGeometryStore, board: BaseGeometry) -> LayerClip | None:
    if board.is_empty:
        return None
    return LayerClip(
        board=board,
        drills=_surface_drill_geometry(store),
        drill_circles=_surface_drill_circles(store),
    )


def _surface_drill_circles(store: PcbGeometryStore) -> tuple[LayerClipCircle, ...]:
    circles: list[LayerClipCircle] = []
    for item in store.items:
        payload = item.payload if item.payload is not None else item.source
        is_drilled_pad = item.kind is GeometryKind.DRILL and isinstance(payload, PcbPad)
        is_drilled_via = item.kind is GeometryKind.VIA and isinstance(payload, PcbVia)
        if not (is_drilled_pad or is_drilled_via):
            continue
        drill_payload = cast("PcbPad | PcbVia", payload)
        if drill_payload.drill > 0:
            circles.append(
                LayerClipCircle(drill_payload.x, drill_payload.y, drill_payload.drill / 2)
            )
    return tuple(circles)


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


def _source_ids_by_store_order(
    store: PcbGeometryStore,
    group: Iterable[_GroupedArtwork],
) -> tuple[str, ...]:
    selected_ids = {source_id for item in group for source_id in item.artwork.source_ids}
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
