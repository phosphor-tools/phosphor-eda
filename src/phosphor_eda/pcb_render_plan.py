from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from phosphor_eda.pcb import (
    LayerFunction,
    Pcb,
    PcbArc,
    PcbLayer,
    PcbLine,
    PcbPad,
    PcbSegment,
    PcbTraceArc,
    PcbVia,
)
from phosphor_eda.pcb_render_geometry import (
    GeometryKind as StoreGeometryKind,
)
from phosphor_eda.pcb_render_geometry import (
    PcbGeometryStore,
    RenderableGeometry,
    build_geometry_store,
)
from phosphor_eda.pcb_render_settings import is_json_dict

if TYPE_CHECKING:
    from phosphor_eda.pcb_render_settings import LayerIncludeRule, RenderSettings, StyleRule

_DESIGN_INNER_COPPER_COLOR_TOKEN = "phosphor:design-inner-copper"
_DESIGN_INNER_COPPER_COLORS = (
    "#7fc87f",
    "#ce7d2c",
    "#4fcbcb",
    "#db628b",
    "#c8c83e",
    "#a18d3e",
    "#3ec8c8",
    "#c83ec8",
)


class GeometryKind(StrEnum):
    BOARD_MATERIAL = "board_material"
    BOARD_OUTLINE = "board_outline"
    PAD = "pad"
    TRACE = "trace"
    TRACE_ARC = "trace_arc"
    ZONE = "zone"
    VIA = "via"
    SILK = "silk"
    BODY = "body"
    REF_TEXT = "ref_text"
    VALUE_TEXT = "value_text"
    USER_TEXT = "user_text"
    BOARD_GRAPHIC_TEXT = "board_graphic_text"


class InclusionReason(StrEnum):
    VISIBLE = "visible"
    HIGHLIGHT = "highlight"
    ANNOTATION_TARGET = "annotation_target"


@dataclass(frozen=True)
class ViewBox:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class RenderPoint:
    x: float
    y: float


@dataclass
class EmittedGeometry:
    kind: GeometryKind
    layer: str
    attrs: dict[str, str]
    reason: InclusionReason
    source: object | None = None
    points: tuple[RenderPoint, ...] = ()
    clipped: bool = True
    style: dict[str, object] = field(default_factory=dict)


@dataclass
class ClipPlan:
    board_path_d: str
    drill_path_d: str = ""


@dataclass
class PcbRenderPlan:
    side: str
    width_px: int
    height_px: int
    view_box: ViewBox
    board_bbox: tuple[float, float, float, float]
    base: list[EmittedGeometry] = field(default_factory=list)
    overlay: list[EmittedGeometry] = field(default_factory=list)
    omitted_count: int = 0
    clip: ClipPlan | None = None
    annotations: object | None = None
    annotation_style: dict[str, object] = field(default_factory=dict)
    custom_css: str = ""


def layer_role(layer: PcbLayer) -> str:
    if layer.function == LayerFunction.COPPER:
        return "copper"
    if layer.function == LayerFunction.SILKSCREEN:
        return "silkscreen"
    if layer.function == LayerFunction.FAB:
        return "fabrication"
    if layer.function == LayerFunction.SOLDER_MASK:
        return "mask"
    if layer.function == LayerFunction.SOLDER_PASTE:
        return "paste"
    if layer.function == LayerFunction.MECHANICAL:
        return "mechanical"
    return "unknown"


def layer_matches_rule(layer: PcbLayer, rule: LayerIncludeRule, active_side: str) -> bool:
    if rule.name and rule.name != layer.name:
        return False
    if rule.role and rule.role != layer_role(layer):
        return False
    if rule.side in ("", "any"):
        return True
    if rule.side == "active":
        return layer.side == active_side
    if rule.side == "opposite":
        return layer.side in ("front", "back") and layer.side != active_side
    return layer.side == rule.side


@dataclass(frozen=True)
class _RenderedViewTransform:
    mirror_x: float | None = None

    def point(self, x: float, y: float) -> RenderPoint:
        return RenderPoint(self.x(x), y)

    def x(self, value: float) -> float:
        if self.mirror_x is None:
            return value
        return self.mirror_x - value


def _rendered_view_transform(
    *,
    side: str,
    board_bbox: tuple[float, float, float, float],
) -> _RenderedViewTransform:
    if side != "back":
        return _RenderedViewTransform()
    bx0, _by0, bx1, _by1 = board_bbox
    return _RenderedViewTransform(mirror_x=bx0 + bx1)


def _transform_lines(
    lines: list[PcbLine],
    transform: _RenderedViewTransform,
) -> list[PcbLine]:
    if transform.mirror_x is None:
        return lines
    return [
        replace(line, start_x=transform.x(line.start_x), end_x=transform.x(line.end_x))
        for line in lines
    ]


def _transform_arcs(
    arcs: list[PcbArc],
    transform: _RenderedViewTransform,
) -> list[PcbArc]:
    if transform.mirror_x is None:
        return arcs
    return [
        replace(
            arc,
            start_x=transform.x(arc.start_x),
            mid_x=transform.x(arc.mid_x),
            end_x=transform.x(arc.end_x),
        )
        for arc in arcs
    ]


def build_render_plan(
    board: Pcb,
    *,
    settings: RenderSettings,
    side: str,
    width_px: int,
) -> PcbRenderPlan:
    bx0, by0, bx1, by1 = board.bbox()
    transform = _rendered_view_transform(side=side, board_bbox=(bx0, by0, bx1, by1))
    pad_mm = 2.0
    vb_x = bx0 - pad_mm
    vb_y = by0 - pad_mm
    vb_w = (bx1 - bx0) + 2 * pad_mm
    vb_h = (by1 - by0) + 2 * pad_mm
    height_px = int(width_px * vb_h / vb_w) if vb_w > 0 else width_px
    outline_lines = _transform_lines(board.outline_lines, transform)
    outline_arcs = _transform_arcs(board.outline_arcs, transform)
    board_path_d = _build_outline_path(outline_lines, outline_arcs) or (
        f"M {bx0:.4f} {by0:.4f} L {bx1:.4f} {by0:.4f} L {bx1:.4f} {by1:.4f} L {bx0:.4f} {by1:.4f} Z"
    )
    plan = PcbRenderPlan(
        side=side,
        width_px=width_px,
        height_px=height_px,
        view_box=ViewBox(vb_x, vb_y, vb_w, vb_h),
        board_bbox=(bx0, by0, bx1, by1),
        clip=ClipPlan(board_path_d=board_path_d, drill_path_d=_build_drill_path(board, transform)),
        annotation_style=_annotation_style_for_settings(settings),
        custom_css=settings.custom_css,
    )

    hl_net_nums, hl_refs, hl_pad_targets, net_colors, component_colors, pad_colors = (
        _highlight_targets(board, settings)
    )
    geometry_store = build_geometry_store(board, side=side)
    inner_copper_indexes = _inner_copper_indexes(board)

    if settings.include.board_outline == "visible":
        board_material_style = _style_for_geometry(
            settings,
            kind=GeometryKind.BOARD_MATERIAL,
            layer=PcbLayer("Edge.Cuts", LayerFunction.EDGE),
            attrs={"data-type": "board-material"},
            highlighted=False,
            active_side=side,
            inner_copper_indexes=inner_copper_indexes,
        )
        if board_material_style:
            plan.base.append(
                EmittedGeometry(
                    kind=GeometryKind.BOARD_MATERIAL,
                    layer="Edge.Cuts",
                    attrs={"data-type": "board-material"},
                    reason=InclusionReason.VISIBLE,
                    points=_outline_points(outline_lines, outline_arcs),
                    clipped=False,
                    style=board_material_style,
                )
            )
        plan.base.append(
            EmittedGeometry(
                kind=GeometryKind.BOARD_OUTLINE,
                layer="Edge.Cuts",
                attrs={"data-type": "board-outline"},
                reason=InclusionReason.VISIBLE,
                points=_outline_points(outline_lines, outline_arcs),
                clipped=False,
                style=_style_for_geometry(
                    settings,
                    kind=GeometryKind.BOARD_OUTLINE,
                    layer=PcbLayer("Edge.Cuts", LayerFunction.EDGE),
                    attrs={"data-type": "board-outline"},
                    highlighted=False,
                    active_side=side,
                    inner_copper_indexes=inner_copper_indexes,
                ),
            )
        )
    elif settings.include.board_outline != "never":
        plan.omitted_count += len(board.outline_lines) + len(board.outline_arcs)

    for layer in _ordered_layers(board):
        if layer.function != LayerFunction.COPPER:
            continue

        for store_item in _store_items_for_layer(
            geometry_store,
            layer.name,
            {StoreGeometryKind.TRACE},
        ):
            segment = store_item.source
            if not isinstance(segment, PcbSegment):
                continue
            _include_geometry(
                plan,
                layer=layer,
                kind=GeometryKind.TRACE,
                object_name="traces",
                source=segment,
                attrs=_attrs_for_store_geometry(store_item, data_type="trace"),
                points=_plan_points(store_item),
                highlighted=store_item.tags.net_number in hl_net_nums,
                settings=settings,
                active_side=side,
                inner_copper_indexes=inner_copper_indexes,
                net_colors=net_colors,
                component_colors=component_colors,
                pad_colors=pad_colors,
            )

        for store_item in _store_items_for_layer(
            geometry_store,
            layer.name,
            {StoreGeometryKind.TRACE_ARC},
        ):
            trace_arc = store_item.source
            if not isinstance(trace_arc, PcbTraceArc):
                continue
            _include_geometry(
                plan,
                layer=layer,
                kind=GeometryKind.TRACE_ARC,
                object_name="traces",
                source=trace_arc,
                attrs=_attrs_for_store_geometry(store_item, data_type="trace"),
                points=_plan_points(store_item),
                highlighted=store_item.tags.net_number in hl_net_nums,
                settings=settings,
                active_side=side,
                inner_copper_indexes=inner_copper_indexes,
                net_colors=net_colors,
                component_colors=component_colors,
                pad_colors=pad_colors,
            )

        for store_item in _store_items_for_layer(
            geometry_store,
            layer.name,
            {StoreGeometryKind.ZONE},
        ):
            _include_geometry(
                plan,
                layer=layer,
                kind=GeometryKind.ZONE,
                object_name="zones",
                source=store_item.source,
                attrs=_attrs_for_store_geometry(store_item, data_type="zone"),
                points=_plan_points(store_item),
                highlighted=store_item.tags.net_number in hl_net_nums,
                settings=settings,
                active_side=side,
                inner_copper_indexes=inner_copper_indexes,
                net_colors=net_colors,
                component_colors=component_colors,
                pad_colors=pad_colors,
            )

        for store_item in geometry_store.by_kind(StoreGeometryKind.VIA):
            via = store_item.source
            if not isinstance(via, PcbVia):
                continue
            if not _copper_layer_selected_for_vias(settings, layer, side):
                continue
            if layer.name not in _via_copper_layer_names(board, via):
                continue
            state = settings.include.vias
            highlighted = store_item.tags.net_number in hl_net_nums
            geometry = EmittedGeometry(
                kind=GeometryKind.VIA,
                layer=layer.name,
                attrs=_attrs_for_store_geometry(store_item, data_type="via"),
                reason=InclusionReason.VISIBLE,
                source=via,
                points=_plan_points(store_item),
            )
            if state == "visible":
                geometry.style = _style_for_geometry(
                    settings,
                    kind=geometry.kind,
                    layer=layer,
                    attrs=geometry.attrs,
                    highlighted=highlighted,
                    active_side=side,
                    inner_copper_indexes=inner_copper_indexes,
                )
                plan.base.append(geometry)
                if highlighted:
                    overlay_geometry = EmittedGeometry(
                        kind=geometry.kind,
                        layer=layer.name,
                        attrs=geometry.attrs,
                        reason=InclusionReason.HIGHLIGHT,
                        source=geometry.source,
                        points=geometry.points,
                        style=_style_for_geometry(
                            settings,
                            kind=geometry.kind,
                            layer=layer,
                            attrs=geometry.attrs,
                            highlighted=True,
                            active_side=side,
                            inner_copper_indexes=inner_copper_indexes,
                        ),
                    )
                    overlay_geometry.style.update(
                        _highlight_style_for_geometry(
                            geometry.kind,
                            geometry.attrs,
                            net_colors=net_colors,
                            component_colors=component_colors,
                            pad_colors=pad_colors,
                        )
                    )
                    plan.overlay.append(overlay_geometry)
            elif state == "when-highlighted" and highlighted:
                overlay_geometry = EmittedGeometry(
                    kind=geometry.kind,
                    layer=layer.name,
                    attrs=geometry.attrs,
                    reason=InclusionReason.HIGHLIGHT,
                    source=geometry.source,
                    points=geometry.points,
                    style=_style_for_geometry(
                        settings,
                        kind=geometry.kind,
                        layer=layer,
                        attrs=geometry.attrs,
                        highlighted=True,
                        active_side=side,
                        inner_copper_indexes=inner_copper_indexes,
                    ),
                )
                overlay_geometry.style.update(
                    _highlight_style_for_geometry(
                        geometry.kind,
                        geometry.attrs,
                        net_colors=net_colors,
                        component_colors=component_colors,
                        pad_colors=pad_colors,
                    )
                )
                plan.overlay.append(overlay_geometry)
            elif state != "never":
                plan.omitted_count += 1

        for store_item in _store_items_for_layer(
            geometry_store,
            layer.name,
            {StoreGeometryKind.PAD},
        ):
            pad = store_item.source
            if not isinstance(pad, PcbPad):
                continue
            _include_geometry(
                plan,
                layer=layer,
                kind=GeometryKind.PAD,
                object_name="pads",
                source=pad,
                attrs=_attrs_for_store_geometry(store_item, data_type="pad"),
                points=_plan_points(store_item),
                highlighted=(
                    store_item.tags.net_number in hl_net_nums
                    or store_item.tags.component_ref in hl_refs
                    or (store_item.tags.component_ref, store_item.tags.pad_number) in hl_pad_targets
                ),
                settings=settings,
                active_side=side,
                inner_copper_indexes=inner_copper_indexes,
                net_colors=net_colors,
                component_colors=component_colors,
                pad_colors=pad_colors,
            )

    for layer in _ordered_layers(board):
        if layer.function != LayerFunction.SILKSCREEN:
            continue
        for store_item in _store_items_for_layer(
            geometry_store,
            layer.name,
            {
                StoreGeometryKind.SILK_LINE,
                StoreGeometryKind.SILK_POLYGON,
                StoreGeometryKind.BOARD_GRAPHIC_TEXT,
            },
        ):
            kind = (
                GeometryKind.BOARD_GRAPHIC_TEXT
                if store_item.kind == StoreGeometryKind.BOARD_GRAPHIC_TEXT
                else GeometryKind.SILK
            )
            _include_geometry(
                plan,
                layer=layer,
                kind=kind,
                object_name="silk",
                source=store_item.source,
                attrs=_attrs_for_store_geometry(
                    store_item,
                    data_type=kind.value if kind is not GeometryKind.SILK else "silk",
                ),
                points=_plan_points(store_item),
                highlighted=store_item.tags.component_ref in hl_refs,
                settings=settings,
                active_side=side,
                inner_copper_indexes=inner_copper_indexes,
                net_colors=net_colors,
                component_colors=component_colors,
                pad_colors=pad_colors,
            )

    for layer in _ordered_layers(board):
        if layer.function != LayerFunction.FAB:
            continue
        for store_item in _store_items_for_layer(
            geometry_store,
            layer.name,
            {
                StoreGeometryKind.FAB_LINE,
                StoreGeometryKind.FAB_CIRCLE,
                StoreGeometryKind.FAB_ARC,
                StoreGeometryKind.FAB_POLYGON,
                StoreGeometryKind.REF_TEXT,
                StoreGeometryKind.VALUE_TEXT,
                StoreGeometryKind.USER_TEXT,
            },
        ):
            if _component_prefix_excluded(settings, store_item.tags.component_prefix):
                continue
            if store_item.kind == StoreGeometryKind.VALUE_TEXT:
                kind = GeometryKind.VALUE_TEXT
            elif store_item.kind == StoreGeometryKind.REF_TEXT:
                kind = GeometryKind.REF_TEXT
            elif store_item.kind == StoreGeometryKind.USER_TEXT:
                kind = GeometryKind.USER_TEXT
            else:
                kind = GeometryKind.BODY
            _include_geometry(
                plan,
                layer=layer,
                kind=kind,
                object_name="body",
                source=store_item.source,
                attrs=_attrs_for_store_geometry(
                    store_item,
                    data_type=kind.value if kind is not GeometryKind.BODY else "body",
                ),
                points=_plan_points(store_item),
                highlighted=store_item.tags.component_ref in hl_refs,
                settings=settings,
                active_side=side,
                inner_copper_indexes=inner_copper_indexes,
                net_colors=net_colors,
                component_colors=component_colors,
                pad_colors=pad_colors,
            )

    return plan


def _ordered_layers(board: Pcb) -> list[PcbLayer]:
    def sort_key(layer: PcbLayer) -> tuple[int, int, str]:
        if layer.function != LayerFunction.COPPER:
            return (2, 0, layer.name)
        if layer.side == "back":
            return (0, 0, layer.name)
        if layer.side == "front":
            return (1, 10_000, layer.name)
        return (1, layer.number if layer.number is not None else 5_000, layer.name)

    return sorted(board.layers, key=sort_key)


def _inner_copper_indexes(board: Pcb) -> dict[str, int]:
    indexes: dict[str, int] = {}
    for layer in _ordered_layers(board):
        if layer.function == LayerFunction.COPPER and layer.side == "":
            indexes[layer.name] = len(indexes)
    return indexes


def _via_copper_layer_names(board: Pcb, via: PcbVia) -> set[str]:
    copper_layers = [
        layer for layer in _ordered_layers(board) if layer.function == LayerFunction.COPPER
    ]
    copper_names = [layer.name for layer in copper_layers]
    via_indexes = [copper_names.index(name) for name in via.layers if name in copper_names]
    if not via_indexes:
        return {name for name in via.layers if name}
    start = min(via_indexes)
    end = max(via_indexes)
    return set(copper_names[start : end + 1])


def _copper_layer_selected_for_vias(
    settings: RenderSettings, layer: PcbLayer, active_side: str
) -> bool:
    for rule in settings.include.layers:
        if not layer_matches_rule(layer, rule, active_side):
            continue
        return any(state in ("visible", "when-highlighted") for state in rule.objects.values())
    return False


def _highlight_targets(
    board: Pcb,
    settings: RenderSettings,
) -> tuple[
    set[int],
    set[str],
    set[tuple[str, str]],
    dict[int, str],
    dict[str, str],
    dict[tuple[str, str], str],
]:
    net_numbers: set[int] = set()
    refs: set[str] = set()
    pad_targets: set[tuple[str, str]] = set()
    net_colors: dict[int, str] = {}
    component_colors: dict[str, str] = {}
    pad_colors: dict[tuple[str, str], str] = {}
    for highlight in settings.highlights:
        if highlight.net:
            nums = board.net_numbers_by_name(highlight.net)
            net_numbers |= nums
            if highlight.color:
                for net_number in nums:
                    net_colors[net_number] = highlight.color
        elif highlight.component:
            footprint = board.footprint_by_ref(highlight.component)
            if footprint is not None:
                refs.add(footprint.reference)
                if highlight.color:
                    component_colors[footprint.reference] = highlight.color
        elif highlight.pad:
            ref, separator, pad_number = highlight.pad.partition(".")
            if not separator or not ref or not pad_number:
                continue
            footprint = board.footprint_by_ref(ref)
            if footprint is not None:
                for pad in footprint.pads:
                    if pad.number == pad_number:
                        target = (footprint.reference, pad.number)
                        pad_targets.add(target)
                        if highlight.color:
                            pad_colors[target] = highlight.color
    return net_numbers, refs, pad_targets, net_colors, component_colors, pad_colors


def _store_items_for_layer(
    store: PcbGeometryStore,
    layer_name: str,
    kinds: set[StoreGeometryKind],
) -> list[RenderableGeometry]:
    return [item for item in store.items if item.layer.name == layer_name and item.kind in kinds]


def _plan_points(item: RenderableGeometry) -> tuple[RenderPoint, ...]:
    return tuple(RenderPoint(point.x, point.y) for point in item.points)


def _attrs_for_store_geometry(item: RenderableGeometry, *, data_type: str) -> dict[str, str]:
    attrs = {
        "data-type": data_type,
        "data-source-geometry-id": item.id,
        "data-layer-role": item.layer.role,
        "data-side": item.layer.side,
    }
    if item.tags.component_ref:
        attrs["data-component"] = item.tags.component_ref
    if item.tags.component_prefix:
        attrs["data-component-prefix"] = item.tags.component_prefix
    if item.tags.footprint_lib:
        attrs["data-footprint-lib"] = item.tags.footprint_lib
    if item.tags.value:
        attrs["data-value"] = item.tags.value
    if item.tags.pad_number:
        attrs["data-pad"] = item.tags.pad_number
    if item.tags.net_name:
        attrs["data-net"] = item.tags.net_name
    if item.tags.net_number is not None:
        attrs["data-net-number"] = str(item.tags.net_number)
    if item.tags.text_kind:
        attrs["data-text-kind"] = item.tags.text_kind
    return attrs


def _component_prefix_excluded(settings: RenderSettings, prefix: str) -> bool:
    return bool(prefix) and prefix in settings.exclude_component_prefixes


def _include_geometry(
    plan: PcbRenderPlan,
    *,
    layer: PcbLayer,
    kind: GeometryKind,
    object_name: str,
    source: object,
    attrs: dict[str, str],
    points: tuple[RenderPoint, ...],
    highlighted: bool,
    settings: RenderSettings,
    active_side: str,
    inner_copper_indexes: dict[str, int],
    net_colors: dict[int, str],
    component_colors: dict[str, str],
    pad_colors: dict[tuple[str, str], str],
) -> None:
    state = _object_include_state(layer, object_name, settings, active_side)
    visible_geometry = EmittedGeometry(
        kind=kind,
        layer=layer.name,
        attrs=attrs,
        reason=InclusionReason.VISIBLE,
        source=source,
        points=points,
        style=_style_for_geometry(
            settings,
            kind=kind,
            layer=layer,
            attrs=attrs,
            highlighted=highlighted,
            active_side=active_side,
            inner_copper_indexes=inner_copper_indexes,
        ),
    )
    if state == "visible":
        plan.base.append(visible_geometry)
        if highlighted:
            overlay_geometry = EmittedGeometry(
                kind=kind,
                layer=layer.name,
                attrs=attrs,
                reason=InclusionReason.HIGHLIGHT,
                source=source,
                points=points,
                style=_style_for_geometry(
                    settings,
                    kind=kind,
                    layer=layer,
                    attrs=attrs,
                    highlighted=True,
                    active_side=active_side,
                    inner_copper_indexes=inner_copper_indexes,
                ),
            )
            overlay_geometry.style.update(
                _highlight_style_for_geometry(
                    kind,
                    attrs,
                    net_colors=net_colors,
                    component_colors=component_colors,
                    pad_colors=pad_colors,
                )
            )
            plan.overlay.append(overlay_geometry)
        return
    if state == "when-highlighted" and highlighted:
        overlay_geometry = EmittedGeometry(
            kind=kind,
            layer=layer.name,
            attrs=attrs,
            reason=InclusionReason.HIGHLIGHT,
            source=source,
            points=points,
            style=_style_for_geometry(
                settings,
                kind=kind,
                layer=layer,
                attrs=attrs,
                highlighted=True,
                active_side=active_side,
                inner_copper_indexes=inner_copper_indexes,
            ),
        )
        overlay_geometry.style.update(
            _highlight_style_for_geometry(
                kind,
                attrs,
                net_colors=net_colors,
                component_colors=component_colors,
                pad_colors=pad_colors,
            )
        )
        plan.overlay.append(overlay_geometry)
        return
    if state != "never":
        plan.omitted_count += 1


def _object_include_state(
    layer: PcbLayer,
    object_name: str,
    settings: RenderSettings,
    active_side: str,
) -> str:
    for rule in settings.include.layers:
        if not layer_matches_rule(layer, rule, active_side):
            continue
        return rule.objects.get(object_name, rule.objects.get("*", "hidden"))
    return "hidden"


def _highlight_style_for_geometry(
    kind: GeometryKind,
    attrs: dict[str, str],
    *,
    net_colors: dict[int, str],
    component_colors: dict[str, str],
    pad_colors: dict[tuple[str, str], str],
) -> dict[str, object]:
    color = _highlight_color_for_attrs(
        attrs,
        net_colors=net_colors,
        component_colors=component_colors,
        pad_colors=pad_colors,
    )
    if not color:
        return {}
    if kind in (GeometryKind.TRACE, GeometryKind.TRACE_ARC):
        return {"stroke": color}
    if kind is GeometryKind.PAD:
        return {"fill": color, "stroke": color}
    if kind in (GeometryKind.ZONE, GeometryKind.VIA):
        return {"fill": color}
    return {}


def _highlight_color_for_attrs(
    attrs: dict[str, str],
    *,
    net_colors: dict[int, str],
    component_colors: dict[str, str],
    pad_colors: dict[tuple[str, str], str],
) -> str:
    component = attrs.get("data-component")
    pad = attrs.get("data-pad")
    if component is not None and pad is not None:
        pad_color = pad_colors.get((component, pad))
        if pad_color:
            return pad_color
    if component is not None:
        component_color = component_colors.get(component)
        if component_color:
            return component_color
    net_number = attrs.get("data-net-number")
    if net_number is not None:
        try:
            return net_colors.get(int(net_number), "")
        except ValueError:
            return ""
    return ""


def _style_for_geometry(
    settings: RenderSettings,
    *,
    kind: GeometryKind,
    layer: PcbLayer | None,
    attrs: dict[str, str],
    highlighted: bool,
    active_side: str,
    inner_copper_indexes: dict[str, int],
) -> dict[str, object]:
    style: dict[str, object] = {}
    for rule in settings.style_rules:
        if _style_rule_matches(
            rule,
            kind=kind,
            layer=layer,
            attrs=attrs,
            highlighted=highlighted,
            active_side=active_side,
        ):
            style.update(rule.style)
    return _resolve_style_tokens(style, layer, inner_copper_indexes)


def _resolve_style_tokens(
    style: dict[str, object],
    layer: PcbLayer | None,
    inner_copper_indexes: dict[str, int],
) -> dict[str, object]:
    if layer is None:
        return style
    resolved: dict[str, object] = {}
    for key, value in style.items():
        if value == _DESIGN_INNER_COPPER_COLOR_TOKEN:
            inner_index = inner_copper_indexes.get(layer.name, 0)
            resolved[key] = _DESIGN_INNER_COPPER_COLORS[
                inner_index % len(_DESIGN_INNER_COPPER_COLORS)
            ]
        else:
            resolved[key] = value
    return resolved


def _annotation_style_for_settings(settings: RenderSettings) -> dict[str, object]:
    styles: dict[str, object] = {}
    for rule in settings.style_rules:
        annotation = rule.match.get("annotation")
        if not isinstance(annotation, str) or not rule.style:
            continue
        current = styles.get(annotation)
        merged: dict[str, object] = dict(current) if is_json_dict(current) else {}
        merged.update(rule.style)
        styles[annotation] = merged
    return styles


def _style_rule_matches(
    rule: StyleRule,
    *,
    kind: GeometryKind,
    layer: PcbLayer | None,
    attrs: dict[str, str],
    highlighted: bool,
    active_side: str,
) -> bool:
    for key, expected in rule.match.items():
        if not _style_match_field(
            key,
            expected,
            kind=kind,
            layer=layer,
            attrs=attrs,
            highlighted=highlighted,
            active_side=active_side,
        ):
            return False
    return True


def _style_match_field(
    key: str,
    expected: object,
    *,
    kind: GeometryKind,
    layer: PcbLayer | None,
    attrs: dict[str, str],
    highlighted: bool,
    active_side: str,
) -> bool:
    if key == "object":
        return expected == kind.value
    if key == "role":
        return layer is not None and expected == layer_role(layer)
    if key == "side":
        return _style_side_matches(expected, layer, active_side)
    if key == "name":
        return layer is not None and expected == layer.name
    if key == "net":
        return expected in (attrs.get("data-net"), attrs.get("data-net-number"))
    if key == "component":
        return expected == attrs.get("data-component")
    if key == "pad":
        component = attrs.get("data-component")
        pad = attrs.get("data-pad")
        return component is not None and pad is not None and expected == f"{component}.{pad}"
    if key == "highlight":
        return expected is highlighted
    if key == "annotation":
        return False
    return False


def _style_side_matches(expected: object, layer: PcbLayer | None, active_side: str) -> bool:
    if expected in ("", "any"):
        return True
    if layer is None:
        return expected == active_side
    if expected == "inner":
        return layer.function == LayerFunction.COPPER and layer.side == ""
    if expected == "active":
        return layer.side == active_side
    if expected == "opposite":
        return layer.side in ("front", "back") and layer.side != active_side
    return expected == layer.side


def _outline_points(lines: list[PcbLine], arcs: list[PcbArc]) -> tuple[RenderPoint, ...]:
    if not lines and not arcs:
        return ()
    first_x = lines[0].start_x if lines else arcs[0].start_x
    first_y = lines[0].start_y if lines else arcs[0].start_y
    points = [RenderPoint(first_x, first_y)]
    points.extend(RenderPoint(line.end_x, line.end_y) for line in lines)
    points.extend(RenderPoint(arc.mid_x, arc.mid_y) for arc in arcs)
    points.extend(RenderPoint(arc.end_x, arc.end_y) for arc in arcs)
    return tuple(points)


def _build_outline_path(lines: list[PcbLine], arcs: list[PcbArc]) -> str:
    if not lines and not arcs:
        return ""

    EPS = 0.05
    segments: list[tuple[tuple[float, float], tuple[float, float], str]] = []

    for line in lines:
        start = (line.start_x, line.start_y)
        end = (line.end_x, line.end_y)
        segments.append((start, end, f"L {end[0]:.4f} {end[1]:.4f}"))

    for arc in arcs:
        start = (arc.start_x, arc.start_y)
        end = (arc.end_x, arc.end_y)
        radius, large_arc, sweep = _arc_svg_params(
            arc.start_x,
            arc.start_y,
            arc.mid_x,
            arc.mid_y,
            arc.end_x,
            arc.end_y,
        )
        if radius > 1e5:
            segments.append((start, end, f"L {end[0]:.4f} {end[1]:.4f}"))
        else:
            segments.append(
                (
                    start,
                    end,
                    f"A {radius:.4f} {radius:.4f} 0 {large_arc} {sweep} {end[0]:.4f} {end[1]:.4f}",
                )
            )

    if not segments:
        return ""

    def reverse_cmd(cmd: str, new_end: tuple[float, float]) -> str:
        if cmd.startswith("L"):
            return f"L {new_end[0]:.4f} {new_end[1]:.4f}"
        parts = cmd.split()
        sweep = 1 - int(parts[5])
        return (
            f"A {parts[1]} {parts[2]} {parts[3]} {parts[4]} {sweep} "
            f"{new_end[0]:.4f} {new_end[1]:.4f}"
        )

    def find_loop(
        start: tuple[float, float],
        current: tuple[float, float],
        used: set[int],
        commands: list[str],
    ) -> list[str] | None:
        if commands and abs(current[0] - start[0]) < EPS and abs(current[1] - start[1]) < EPS:
            return list(commands)
        for index, (seg_start, seg_end, command) in enumerate(segments):
            if index in used:
                continue
            if abs(seg_start[0] - current[0]) < EPS and abs(seg_start[1] - current[1]) < EPS:
                loop = find_loop(start, seg_end, used | {index}, [*commands, command])
                if loop is not None:
                    return loop
            if abs(seg_end[0] - current[0]) < EPS and abs(seg_end[1] - current[1]) < EPS:
                loop = find_loop(
                    start,
                    seg_start,
                    used | {index},
                    [*commands, reverse_cmd(command, seg_start)],
                )
                if loop is not None:
                    return loop
        return None

    start = segments[0][0]
    commands = find_loop(start, start, set(), [])
    if commands is None:
        return ""
    return f"M {start[0]:.4f} {start[1]:.4f} " + " ".join(commands) + " Z"


def _circumcircle(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    x3: float,
    y3: float,
) -> tuple[float, float, float]:
    ax, ay = x1, y1
    bx, by = x2, y2
    cx, cy = x3, y3
    denominator = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(denominator) < 1e-10:
        return ((x1 + x3) / 2, (y1 + y3) / 2, 1e6)
    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    c2 = cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / denominator
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / denominator
    radius = math.hypot(ax - ux, ay - uy)
    return (ux, uy, radius)


def _arc_svg_params(
    sx: float,
    sy: float,
    mx: float,
    my: float,
    ex: float,
    ey: float,
) -> tuple[float, int, int]:
    center_x, center_y, radius = _circumcircle(sx, sy, mx, my, ex, ey)
    if radius > 1e5:
        return (radius, 0, 0)

    start_angle = math.atan2(sy - center_y, sx - center_x)
    mid_angle = math.atan2(my - center_y, mx - center_x)
    end_angle = math.atan2(ey - center_y, ex - center_x)

    def positive_distance(angle: float, reference: float) -> float:
        return (angle - reference) % (2 * math.pi)

    clockwise_to_mid = positive_distance(mid_angle, start_angle)
    clockwise_to_end = positive_distance(end_angle, start_angle)
    if clockwise_to_mid < clockwise_to_end:
        sweep = 1
        span = clockwise_to_end
    else:
        sweep = 0
        span = 2 * math.pi - clockwise_to_end
    large_arc = 1 if span > math.pi else 0
    return (radius, large_arc, sweep)


def _build_drill_path(board: Pcb, transform: _RenderedViewTransform) -> str:
    holes: list[tuple[float, float, float]] = []
    for footprint in board.footprints:
        for pad in footprint.pads:
            if pad.drill > 0:
                holes.append((transform.x(pad.x), pad.y, pad.drill / 2))
    for via in board.vias:
        if via.drill > 0:
            holes.append((transform.x(via.x), via.y, via.drill / 2))
    return "".join(
        f" M {x - r:.4f} {y:.4f}"
        f" A {r:.4f} {r:.4f} 0 1 0 {x + r:.4f} {y:.4f}"
        f" A {r:.4f} {r:.4f} 0 1 0 {x - r:.4f} {y:.4f} Z"
        for x, y, r in holes
    )
