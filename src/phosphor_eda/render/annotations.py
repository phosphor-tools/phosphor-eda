"""Resolve an annotation spec to pixel-space coordinates for SVG rendering.

This module owns the resolved types and the resolution pipeline: it maps
annotation targets to board geometry, hands labels to the margin placement
solver, and routes connectors. Spec parsing lives in ``annotation_spec``;
the solver and connector geometry live in ``annotation_placement``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from phosphor_eda.render.annotation_placement import (
    ANNOTATION_FONT_PX,
    BOX_PAD_PX,
    LEGEND_GAP_PX,
    MARGIN_GAP_PX,
    PlacedResult,
    PlacementItem,
    auto_assign_margin,
    compute_connector,
    hint_to_margin,
    measure_label,
    measure_legend,
    px_scale,
    solve_margin_placement,
    text_anchor_for_margin,
    to_rendered_view_bbox,
    to_rendered_view_x,
)
from phosphor_eda.render.annotation_spec import (
    AnnotationSpec,
    LegendEntry,
    parse_annotations,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from phosphor_eda.domain.pcb import Board

__all__ = [
    "ANNOTATION_FONT_PX",
    "AnnotationSpec",
    "ResolvedAnnotations",
    "ResolvedBox",
    "ResolvedCallout",
    "ResolvedLabel",
    "ResolvedLegend",
    "ResolvedPointer",
    "parse_annotations",
    "resolve_annotations",
]


# ---------------------------------------------------------------------------
# Resolved types (coordinates in display pixels, ready for SVG emission)
# ---------------------------------------------------------------------------


@dataclass
class ResolvedCallout:
    """A margin label with its connector back to an annotation target.

    Shared by boxes, pointers, and labels: when an annotation has no
    label text its ``callout`` is ``None`` instead of a zeroed-out
    duplicate of these fields.
    """

    text: str
    x: float
    y: float
    width: float
    height: float
    connector_path: list[tuple[float, float]]
    text_anchor: str = "middle"


@dataclass
class ResolvedBox:
    """Box annotation with computed coordinates.

    The box rect sits on the board.  When labeled, ``callout`` carries the
    margin label and the connector from it to the box edge.
    """

    x: float
    y: float
    width: float
    height: float
    color: str
    callout: ResolvedCallout | None = None


@dataclass
class ResolvedPointer:
    """Pointer annotation with computed coordinates.

    ``callout`` carries the margin label and the connector from it to the
    target point (with an arrowhead at the target).
    """

    target_x: float
    target_y: float
    color: str
    callout: ResolvedCallout | None = None


@dataclass
class ResolvedLabel:
    """Label annotation with computed coordinates.

    ``callout`` carries the margin label; the connector is empty when the
    label has no board target.
    """

    callout: ResolvedCallout | None = None


@dataclass
class ResolvedLegend:
    """Legend with computed position and size."""

    title: str
    entries: list[LegendEntry]
    x: float
    y: float
    width: float
    height: float


@dataclass
class ResolvedAnnotations:
    """All resolved annotations ready for SVG rendering.

    Coordinates are in display pixel space.  The renderer applies
    ``transform="scale(px_scale)"`` to map back to SVG viewBox units.
    ``content_bbox`` is in **board mm** (for viewBox expansion).
    """

    boxes: list[ResolvedBox] = field(default_factory=list)
    pointers: list[ResolvedPointer] = field(default_factory=list)
    labels: list[ResolvedLabel] = field(default_factory=list)
    legend: ResolvedLegend | None = None
    font_size: float = 10.0
    px_scale: float = 1.0
    content_bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    warnings: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


def resolve_component_target(
    ref: str, board: Board
) -> tuple[tuple[float, float], tuple[float, float, float, float]]:
    """Resolve a component ref to (center, bbox).

    Raises ``ValueError`` if the component is not found.
    """
    fp = board.footprint_by_ref(ref)
    if fp is None:
        msg = f"Component '{ref}' not found on board"
        raise ValueError(msg)
    if fp.bbox is not None:
        bx1, by1, bx2, by2 = fp.bbox
        center = ((bx1 + bx2) / 2, (by1 + by2) / 2)
        return center, fp.bbox
    # Fallback to footprint position with a small default bbox
    return (fp.x, fp.y), (fp.x - 1, fp.y - 1, fp.x + 1, fp.y + 1)


def resolve_pad_target(ref_pad: str, board: Board) -> tuple[float, float]:
    """Resolve "U7.10" → pad center coordinates.

    Raises ``ValueError`` if the component or pad is not found.
    """
    parts = ref_pad.rsplit(".", 1)
    if len(parts) != 2:
        msg = f"Invalid pad target '{ref_pad}': expected 'REF.PAD'"
        raise ValueError(msg)
    ref, pad_num = parts
    fp = board.footprint_by_ref(ref)
    if fp is None:
        msg = f"Component '{ref}' not found on board"
        raise ValueError(msg)
    for pad in board.pads_for_footprint(fp):
        if pad.number == pad_num:
            return (pad.x, pad.y)
    msg = f"Pad '{pad_num}' not found on component '{ref}'"
    raise ValueError(msg)


def resolve_net_target(net_name: str, near_ref: str, board: Board) -> tuple[float, float]:
    """Find the pad on ``near_ref`` that connects to ``net_name``.

    Raises ``ValueError`` if no matching pad is found.
    """
    fp = board.footprint_by_ref(near_ref)
    if fp is None:
        msg = f"Component '{near_ref}' not found on board"
        raise ValueError(msg)
    needle = net_name.upper()
    for pad in board.pads_for_footprint(fp):
        if pad.net is not None and pad.net.name.upper() == needle:
            return (pad.x, pad.y)
    msg = f"Net '{net_name}' not found on component '{near_ref}'"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# Color validation
# ---------------------------------------------------------------------------


_COLOR_RE = re.compile(
    r"^(#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}|rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+)",
)


def _warn_unparseable_color(color: str, context: str, warnings: list[str]) -> None:
    """Record a warning when a user color string won't render as intended."""
    if color and not _COLOR_RE.match(color):
        warnings.append(f"{context}: unparseable color {color!r}; using fallback orange")


# ---------------------------------------------------------------------------
# Main resolution pipeline
# ---------------------------------------------------------------------------

_FALLBACK_COLOR = "rgba(255,107,53,0.9)"
_LABEL_LINK_COLOR = "rgba(180,180,200,0.5)"
_LABEL_PILL_COLOR = "rgba(60,60,80,0.9)"
_CONTENT_PAD_PX = 6.0  # covers stroke-width + dot radius


@dataclass
class _PendingPlacement:
    """A label awaiting placement, with how to attach the solved result."""

    item: PlacementItem
    attach: Callable[[PlacedResult], None]
    target_rect: tuple[float, float, float, float] | None = None


def resolve_annotations(
    spec: AnnotationSpec,
    board: Board,
    side: str,
    width_px: int = 800,
    font_size: float = ANNOTATION_FONT_PX,
) -> ResolvedAnnotations:
    """Resolve annotation spec to concrete pixel-space coordinates.

    Board-mm targets are converted to display pixel space so that all
    annotation sizes (fonts, padding, margins) are independent of board
    physical dimensions.  The renderer applies
    ``transform="scale(px_scale)"`` to map back to the SVG viewBox.

    ``content_bbox`` is returned in board mm for viewBox expansion.
    """
    board_bbox = board.bbox()
    if board_bbox is None:
        msg = "cannot resolve annotations for an empty board: no geometry to bound the view"
        raise ValueError(msg)
    scale = px_scale(board_bbox, width_px)
    warnings: list[str] = []

    # Board bbox in pixel space for the placement engine
    px_board_bbox = (
        board_bbox[0] / scale,
        board_bbox[1] / scale,
        board_bbox[2] / scale,
        board_bbox[3] / scale,
    )

    bounds = _Bounds()
    pending: list[_PendingPlacement] = []

    resolved_boxes = _resolve_boxes(spec, board, board_bbox, side, scale, font_size, warnings)
    for i, box in enumerate(resolved_boxes):
        box_spec = spec.boxes[i]
        if box_spec.label:
            _queue_callout(
                pending,
                box_spec.label,
                box_spec.label_position,
                (box.x + box.width / 2, box.y + box.height / 2),
                font_size,
                px_board_bbox,
                target_rect=(box.x, box.y, box.width, box.height),
                attach=lambda callout, box=box: setattr(box, "callout", callout),
            )

    resolved_pointers = _resolve_pointers(spec, board, board_bbox, side, scale, warnings)
    for i, pointer in enumerate(resolved_pointers):
        ptr_spec = spec.pointers[i]
        if ptr_spec.label:
            _queue_callout(
                pending,
                ptr_spec.label,
                ptr_spec.position,
                (pointer.target_x, pointer.target_y),
                font_size,
                px_board_bbox,
                attach=lambda callout, ptr=pointer: setattr(ptr, "callout", callout),
            )

    resolved_labels, label_targets = _resolve_labels(spec, board, board_bbox, side, scale)
    for i, label in enumerate(resolved_labels):
        label_spec = spec.labels[i]
        if label_spec.content:
            _queue_callout(
                pending,
                label_spec.content,
                label_spec.position,
                label_targets[i],
                font_size,
                px_board_bbox,
                has_connector=bool(label_spec.target),
                attach=lambda callout, lbl=label: setattr(lbl, "callout", callout),
            )

    resolved_legend = _resolve_legend(spec, board_bbox, px_board_bbox, scale, font_size, pending)

    # Solve placement for all queued labels at once (global non-overlap).
    placed = solve_margin_placement(
        [p.item for p in pending],
        px_board_bbox,
        MARGIN_GAP_PX,
        warn=warnings.append,
    )
    for pend, result in zip(pending, placed, strict=True):
        pend.attach(result)

    _accumulate_bounds(bounds, resolved_boxes, resolved_pointers, resolved_labels, resolved_legend)

    content_bbox = bounds.to_board_mm(scale) or board_bbox
    return ResolvedAnnotations(
        boxes=resolved_boxes,
        pointers=resolved_pointers,
        labels=resolved_labels,
        legend=resolved_legend,
        font_size=font_size,
        px_scale=scale,
        content_bbox=content_bbox,
        warnings=tuple(warnings),
    )


def _resolve_boxes(
    spec: AnnotationSpec,
    board: Board,
    board_bbox: tuple[float, float, float, float],
    side: str,
    scale: float,
    font_size: float,
    warnings: list[str],
) -> list[ResolvedBox]:
    del font_size  # measurement happens when the callout is queued
    boxes: list[ResolvedBox] = []
    for i, box_spec in enumerate(spec.boxes):
        min_x = float("inf")
        min_y = float("inf")
        max_x = float("-inf")
        max_y = float("-inf")
        for ref in box_spec.targets:
            _center, bbox = resolve_component_target(ref, board)
            bx1, by1, bx2, by2 = to_rendered_view_bbox(bbox, board_bbox, side)
            min_x = min(min_x, bx1 / scale)
            min_y = min(min_y, by1 / scale)
            max_x = max(max_x, bx2 / scale)
            max_y = max(max_y, by2 / scale)

        box_x = min_x - BOX_PAD_PX
        box_y = min_y - BOX_PAD_PX
        box_w = (max_x - min_x) + 2 * BOX_PAD_PX
        box_h = (max_y - min_y) + 2 * BOX_PAD_PX
        _warn_unparseable_color(box_spec.color, f"box {i}", warnings)
        boxes.append(
            ResolvedBox(
                x=box_x,
                y=box_y,
                width=box_w,
                height=box_h,
                color=box_spec.color or _FALLBACK_COLOR,
            )
        )
    return boxes


def _resolve_pointers(
    spec: AnnotationSpec,
    board: Board,
    board_bbox: tuple[float, float, float, float],
    side: str,
    scale: float,
    warnings: list[str],
) -> list[ResolvedPointer]:
    pointers: list[ResolvedPointer] = []
    for i, ptr_spec in enumerate(spec.pointers):
        if ptr_spec.target:
            if "." in ptr_spec.target:
                tx_mm, ty_mm = resolve_pad_target(ptr_spec.target, board)
            else:
                center, _bbox = resolve_component_target(ptr_spec.target, board)
                tx_mm, ty_mm = center
        else:
            tx_mm, ty_mm = resolve_net_target(ptr_spec.target_net, ptr_spec.target_near, board)
        tx = to_rendered_view_x(tx_mm, board_bbox, side) / scale
        ty = ty_mm / scale
        _warn_unparseable_color(ptr_spec.color, f"pointer {i}", warnings)
        pointers.append(
            ResolvedPointer(
                target_x=tx,
                target_y=ty,
                color=ptr_spec.color or _FALLBACK_COLOR,
            )
        )
    return pointers


def _resolve_labels(
    spec: AnnotationSpec,
    board: Board,
    board_bbox: tuple[float, float, float, float],
    side: str,
    scale: float,
) -> tuple[list[ResolvedLabel], list[tuple[float, float]]]:
    labels: list[ResolvedLabel] = []
    targets: list[tuple[float, float]] = []
    for label_spec in spec.labels:
        if label_spec.target:
            center, _bbox = resolve_component_target(label_spec.target, board)
            tx = to_rendered_view_x(center[0], board_bbox, side) / scale
            ty = center[1] / scale
        else:
            tx = (board_bbox[0] + board_bbox[2]) / 2 / scale
            ty = (board_bbox[1] + board_bbox[3]) / 2 / scale
        targets.append((tx, ty))
        labels.append(ResolvedLabel())
    return labels, targets


def _resolve_legend(
    spec: AnnotationSpec,
    board_bbox: tuple[float, float, float, float],
    px_board_bbox: tuple[float, float, float, float],
    scale: float,
    font_size: float,
    pending: list[_PendingPlacement],
) -> ResolvedLegend | None:
    del scale
    if spec.legend is None:
        return None
    legend_width, legend_height = measure_legend(spec.legend, font_size)
    legend_margin = hint_to_margin(spec.legend.position)
    if not legend_margin:
        bw = board_bbox[2] - board_bbox[0]
        bh = board_bbox[3] - board_bbox[1]
        legend_margin = "bottom" if bw >= bh else "right"

    pbx1, pby1, pbx2, pby2 = px_board_bbox
    legend = ResolvedLegend(
        title=spec.legend.title,
        entries=spec.legend.entries,
        x=0.0,
        y=0.0,
        width=legend_width,
        height=legend_height,
    )

    def attach(result: PlacedResult) -> None:
        legend.x = result.label_x
        legend.y = result.label_y

    pending.append(
        _PendingPlacement(
            item=PlacementItem(
                legend_width,
                legend_height,
                (pbx1 + pbx2) / 2,
                (pby1 + pby2) / 2,
                legend_margin,
                margin_gap=LEGEND_GAP_PX,
            ),
            attach=attach,
        )
    )
    return legend


def _queue_callout(
    pending: list[_PendingPlacement],
    text: str,
    position_hint: str,
    target: tuple[float, float],
    font_size: float,
    px_board_bbox: tuple[float, float, float, float],
    *,
    attach: Callable[[ResolvedCallout], None],
    target_rect: tuple[float, float, float, float] | None = None,
    has_connector: bool = True,
) -> None:
    """Queue a label for placement; build its ResolvedCallout when solved."""
    lw, lh = measure_label(text, font_size)
    target_x, target_y = target
    margin = hint_to_margin(position_hint)
    if not margin:
        margin = auto_assign_margin(target_x, target_y, px_board_bbox)

    def on_placed(result: PlacedResult) -> None:
        connector = (
            compute_connector(
                result.label_x,
                result.label_y,
                lw,
                lh,
                target_x,
                target_y,
                margin,
                px_board_bbox,
                MARGIN_GAP_PX,
                target_rect=target_rect,
            )
            if has_connector
            else []
        )
        attach(
            ResolvedCallout(
                text=text,
                x=result.label_x,
                y=result.label_y,
                width=lw,
                height=lh,
                connector_path=connector,
                text_anchor=text_anchor_for_margin(margin),
            )
        )

    pending.append(
        _PendingPlacement(
            item=PlacementItem(lw, lh, target_x, target_y, margin),
            attach=on_placed,
            target_rect=target_rect,
        )
    )


class _Bounds:
    """Accumulates the pixel-space bounding box of all annotation content."""

    def __init__(self) -> None:
        self.xs: list[float] = []
        self.ys: list[float] = []

    def add_rect(self, x: float, y: float, w: float, h: float) -> None:
        self.xs.extend([x, x + w])
        self.ys.extend([y, y + h])

    def add_point(self, x: float, y: float) -> None:
        self.xs.append(x)
        self.ys.append(y)

    def to_board_mm(self, scale: float) -> tuple[float, float, float, float] | None:
        if not self.xs or not self.ys:
            return None
        return (
            (min(self.xs) - _CONTENT_PAD_PX) * scale,
            (min(self.ys) - _CONTENT_PAD_PX) * scale,
            (max(self.xs) + _CONTENT_PAD_PX) * scale,
            (max(self.ys) + _CONTENT_PAD_PX) * scale,
        )


def _accumulate_bounds(
    bounds: _Bounds,
    boxes: list[ResolvedBox],
    pointers: list[ResolvedPointer],
    labels: list[ResolvedLabel],
    legend: ResolvedLegend | None,
) -> None:
    for box in boxes:
        bounds.add_rect(box.x, box.y, box.width, box.height)
        if box.callout is not None:
            bounds.add_rect(box.callout.x, box.callout.y, box.callout.width, box.callout.height)
    for pointer in pointers:
        bounds.add_point(pointer.target_x, pointer.target_y)
        if pointer.callout is not None:
            bounds.add_rect(
                pointer.callout.x,
                pointer.callout.y,
                pointer.callout.width,
                pointer.callout.height,
            )
    for label in labels:
        if label.callout is not None:
            bounds.add_rect(
                label.callout.x,
                label.callout.y,
                label.callout.width,
                label.callout.height,
            )
    if legend is not None:
        bounds.add_rect(legend.x, legend.y, legend.width, legend.height)
