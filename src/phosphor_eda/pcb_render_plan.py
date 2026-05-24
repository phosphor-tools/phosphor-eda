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
    PcbPolygon,
    PcbSegment,
    PcbTraceArc,
    PcbVia,
    PcbZone,
)

if TYPE_CHECKING:
    from phosphor_eda.pcb_render_settings import LayerIncludeRule, RenderSettings, StyleRule


class GeometryKind(StrEnum):
    BOARD_OUTLINE = "board_outline"
    PAD = "pad"
    TRACE = "trace"
    TRACE_ARC = "trace_arc"
    ZONE = "zone"
    VIA = "via"
    SILK = "silk"
    BODY = "body"
    REF_TEXT = "ref_text"


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


def _transform_render_points(
    points: tuple[RenderPoint, ...],
    transform: _RenderedViewTransform,
) -> tuple[RenderPoint, ...]:
    if transform.mirror_x is None:
        return points
    return tuple(transform.point(point.x, point.y) for point in points)


def _transform_pad(pad: PcbPad, transform: _RenderedViewTransform) -> PcbPad:
    if transform.mirror_x is None:
        return pad
    rotation = (-pad.rotation) % 360 if pad.rotation else 0.0
    return replace(pad, x=transform.x(pad.x), rotation=rotation)


def _transform_segment(segment: PcbSegment, transform: _RenderedViewTransform) -> PcbSegment:
    if transform.mirror_x is None:
        return segment
    return replace(
        segment,
        start_x=transform.x(segment.start_x),
        end_x=transform.x(segment.end_x),
    )


def _transform_trace_arc(
    trace_arc: PcbTraceArc,
    transform: _RenderedViewTransform,
) -> PcbTraceArc:
    if transform.mirror_x is None:
        return trace_arc
    return replace(
        trace_arc,
        start_x=transform.x(trace_arc.start_x),
        mid_x=transform.x(trace_arc.mid_x),
        end_x=transform.x(trace_arc.end_x),
    )


def _transform_via(via: PcbVia, transform: _RenderedViewTransform) -> PcbVia:
    if transform.mirror_x is None:
        return via
    return replace(via, x=transform.x(via.x))


def _transform_zone(
    zone: PcbPolygon | PcbZone,
    transform: _RenderedViewTransform,
) -> PcbPolygon | PcbZone:
    if transform.mirror_x is None:
        return zone
    if isinstance(zone, PcbPolygon):
        return replace(
            zone,
            points=[(transform.x(x), y) for x, y in zone.points],
            holes=[[(transform.x(x), y) for x, y in hole] for hole in zone.holes],
        )
    return replace(zone, boundary=[(transform.x(x), y) for x, y in zone.boundary])


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
        custom_css=settings.custom_css,
    )

    hl_net_nums, hl_refs, hl_pad_targets = _highlight_targets(board, settings)
    layer_lookup = {layer.name: layer for layer in board.layers}
    pads_by_layer = _pads_by_layer(board, layer_lookup)

    if settings.include.board_outline == "visible":
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
                ),
            )
        )
    elif settings.include.board_outline != "never":
        plan.omitted_count += len(board.outline_lines) + len(board.outline_arcs)

    for layer in _ordered_layers(board):
        if layer.function != LayerFunction.COPPER:
            continue

        for pad, fp_ref in pads_by_layer.get(layer.name, []):
            _include_geometry(
                plan,
                layer=layer,
                kind=GeometryKind.PAD,
                object_name="pads",
                source=_transform_pad(pad, transform),
                attrs={
                    "data-type": "pad",
                    "data-component": fp_ref,
                    "data-pad": pad.number,
                    "data-net": pad.net_name or _net_name(board, pad.net_number),
                    "data-net-number": str(pad.net_number),
                },
                points=(transform.point(pad.x, pad.y),),
                highlighted=(
                    pad.net_number in hl_net_nums
                    or fp_ref in hl_refs
                    or (fp_ref, pad.number) in hl_pad_targets
                ),
                settings=settings,
                active_side=side,
            )

        for segment in [seg for seg in board.segments if seg.layer == layer.name]:
            _include_geometry(
                plan,
                layer=layer,
                kind=GeometryKind.TRACE,
                object_name="traces",
                source=_transform_segment(segment, transform),
                attrs=_net_attrs(board, "trace", segment.net_number),
                points=(
                    transform.point(segment.start_x, segment.start_y),
                    transform.point(segment.end_x, segment.end_y),
                ),
                highlighted=segment.net_number in hl_net_nums,
                settings=settings,
                active_side=side,
            )

        for trace_arc in [arc for arc in board.trace_arcs if arc.layer == layer.name]:
            _include_geometry(
                plan,
                layer=layer,
                kind=GeometryKind.TRACE_ARC,
                object_name="traces",
                source=_transform_trace_arc(trace_arc, transform),
                attrs=_net_attrs(board, "trace", trace_arc.net_number),
                points=(
                    transform.point(trace_arc.start_x, trace_arc.start_y),
                    transform.point(trace_arc.mid_x, trace_arc.mid_y),
                    transform.point(trace_arc.end_x, trace_arc.end_y),
                ),
                highlighted=trace_arc.net_number in hl_net_nums,
                settings=settings,
                active_side=side,
            )

        for zone in _zones_for_layer(board, layer.name):
            net_number = zone.net_number
            net_name = _zone_net_name(board, zone)
            _include_geometry(
                plan,
                layer=layer,
                kind=GeometryKind.ZONE,
                object_name="zones",
                source=_transform_zone(zone, transform),
                attrs={
                    "data-type": "zone",
                    "data-net": net_name,
                    "data-net-number": str(net_number),
                },
                points=_transform_render_points(_zone_points(zone), transform),
                highlighted=net_number in hl_net_nums,
                settings=settings,
                active_side=side,
            )

    for via in board.vias:
        state = settings.include.vias
        highlighted = via.net_number in hl_net_nums
        geometry = EmittedGeometry(
            kind=GeometryKind.VIA,
            layer="vias",
            attrs=_net_attrs(board, "via", via.net_number),
            reason=InclusionReason.VISIBLE,
            source=_transform_via(via, transform),
            points=(transform.point(via.x, via.y),),
        )
        if state == "visible":
            geometry.style = _style_for_geometry(
                settings,
                kind=geometry.kind,
                layer=None,
                attrs=geometry.attrs,
                highlighted=highlighted,
                active_side=side,
            )
            plan.base.append(geometry)
        elif state == "when-highlighted" and highlighted:
            plan.overlay.append(
                EmittedGeometry(
                    kind=geometry.kind,
                    layer=geometry.layer,
                    attrs=geometry.attrs,
                    reason=InclusionReason.HIGHLIGHT,
                    source=geometry.source,
                    points=geometry.points,
                    style=_style_for_geometry(
                        settings,
                        kind=geometry.kind,
                        layer=None,
                        attrs=geometry.attrs,
                        highlighted=True,
                        active_side=side,
                    ),
                )
            )
        elif state != "never":
            plan.omitted_count += 1

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


def _highlight_targets(
    board: Pcb,
    settings: RenderSettings,
) -> tuple[set[int], set[str], set[tuple[str, str]]]:
    net_numbers: set[int] = set()
    refs: set[str] = set()
    pad_targets: set[tuple[str, str]] = set()
    for highlight in settings.highlights:
        if highlight.net:
            net_numbers |= board.net_numbers_by_name(highlight.net)
        elif highlight.component:
            footprint = board.footprint_by_ref(highlight.component)
            if footprint is not None:
                refs.add(footprint.reference)
        elif highlight.pad:
            ref, separator, pad_number = highlight.pad.partition(".")
            if not separator or not ref or not pad_number:
                continue
            footprint = board.footprint_by_ref(ref)
            if footprint is not None:
                for pad in footprint.pads:
                    if pad.number == pad_number:
                        pad_targets.add((footprint.reference, pad.number))
    return net_numbers, refs, pad_targets


def _pads_by_layer(
    board: Pcb,
    layer_lookup: dict[str, PcbLayer],
) -> dict[str, list[tuple[PcbPad, str]]]:
    pads: dict[str, list[tuple[PcbPad, str]]] = {}
    for footprint in board.footprints:
        for pad in footprint.pads:
            layer = _pad_copper_layer(pad, footprint.layer, layer_lookup)
            pads.setdefault(layer, []).append((pad, footprint.reference))
    return pads


def _pad_copper_layer(pad: PcbPad, footprint_layer: str, layer_lookup: dict[str, PcbLayer]) -> str:
    for layer_name in pad.layers:
        if layer_name == "*.Cu":
            return footprint_layer
        layer = layer_lookup.get(layer_name)
        if layer and layer.function == LayerFunction.COPPER:
            return layer_name
    return footprint_layer


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
        ),
    )
    if state == "visible":
        plan.base.append(visible_geometry)
        if highlighted:
            plan.overlay.append(
                EmittedGeometry(
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
                    ),
                )
            )
        return
    if state == "when-highlighted" and highlighted:
        plan.overlay.append(
            EmittedGeometry(
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
                ),
            )
        )
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


def _style_for_geometry(
    settings: RenderSettings,
    *,
    kind: GeometryKind,
    layer: PcbLayer | None,
    attrs: dict[str, str],
    highlighted: bool,
    active_side: str,
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
    return style


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
    if expected == "active":
        return layer.side == active_side
    if expected == "opposite":
        return layer.side in ("front", "back") and layer.side != active_side
    return expected == layer.side


def _net_attrs(board: Pcb, data_type: str, net_number: int) -> dict[str, str]:
    return {
        "data-type": data_type,
        "data-net": _net_name(board, net_number),
        "data-net-number": str(net_number),
    }


def _net_name(board: Pcb, net_number: int) -> str:
    net = board.nets.get(net_number)
    return net.name if net else ""


def _zones_for_layer(board: Pcb, layer: str) -> list[PcbPolygon | PcbZone]:
    zones: list[PcbPolygon | PcbZone] = []
    zones.extend(poly for poly in board.polygons if poly.layer == layer)
    zones.extend(zone for zone in board.zones if zone.layer == layer)
    return zones


def _zone_net_name(board: Pcb, zone: PcbPolygon | PcbZone) -> str:
    if zone.net_name:
        return zone.net_name
    return _net_name(board, zone.net_number)


def _zone_points(zone: PcbPolygon | PcbZone) -> tuple[RenderPoint, ...]:
    raw_points = zone.points if isinstance(zone, PcbPolygon) else zone.boundary
    return tuple(RenderPoint(x, y) for x, y in raw_points)


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
    mask_layers = {"F.Mask", "B.Mask", "*.Mask"}
    holes: list[tuple[float, float, float]] = []
    for footprint in board.footprints:
        for pad in footprint.pads:
            if pad.drill > 0:
                holes.append((transform.x(pad.x), pad.y, pad.drill / 2))
    for via in board.vias:
        if via.drill > 0 and set(via.layers) & mask_layers:
            holes.append((transform.x(via.x), via.y, via.drill / 2))
    return "".join(
        f" M {x - r:.4f} {y:.4f}"
        f" A {r:.4f} {r:.4f} 0 1 0 {x + r:.4f} {y:.4f}"
        f" A {r:.4f} {r:.4f} 0 1 0 {x - r:.4f} {y:.4f} Z"
        for x, y, r in holes
    )
