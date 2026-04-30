"""PCB annotation data model, JSON parsing, target resolution, and placement.

Annotations let the agent draw boxes, pointers, labels, and legends on
rendered PCB SVGs using schematic vocabulary (component refs, net names,
pad numbers) without needing to know coordinates. Placement is automatic
by default; the model can override with position hints.

The pipeline is:

1. ``parse_annotations(data)`` — validate JSON → ``AnnotationSpec``
2. ``resolve_annotations(spec, board, side)`` — resolve targets, auto-place
   → ``ResolvedAnnotations`` (coordinates in board mm, ready for SVG)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from phosphor_eda.pcb import PcbBoard

# JSON data from json.loads() is inherently untyped — Any is the correct
# boundary type for validating external input before converting to dataclasses.
type JsonDict = dict[str, Any]


# ---------------------------------------------------------------------------
# Input types (parsed from JSON)
# ---------------------------------------------------------------------------


@dataclass
class BoxSpec:
    """Annotation box around one or more components."""

    targets: list[str]
    label: str = ""
    label_position: str = ""  # "" = auto, or "above"/"below"/"left"/"right"
    color: str = ""


@dataclass
class PointerSpec:
    """Arrow pointing at a component, pad, or net pad."""

    target: str = ""  # "U7", "U7.10", or ""
    target_net: str = ""  # net name (for net+near targeting)
    target_near: str = ""  # component ref (for net+near targeting)
    label: str = ""
    position: str = ""  # "" = auto, or position hint
    color: str = ""


@dataclass
class LabelSpec:
    """Text label attached to a component."""

    target: str = ""
    content: str = ""
    position: str = ""  # "" = auto, or hint incl. "board-*"


@dataclass
class LegendEntry:
    """Single entry in a color legend."""

    color: str
    label: str


@dataclass
class LegendSpec:
    """Color-keyed legend block."""

    title: str
    entries: list[LegendEntry]
    position: str = ""  # "" = auto, or "board-bottom" etc.


@dataclass
class AnnotationSpec:
    """Complete annotation specification parsed from JSON."""

    boxes: list[BoxSpec]
    pointers: list[PointerSpec]
    labels: list[LabelSpec]
    legend: LegendSpec | None = None


# ---------------------------------------------------------------------------
# Resolved types (coordinates in board mm, ready for SVG emission)
# ---------------------------------------------------------------------------


@dataclass
class ResolvedBox:
    """Box annotation with computed coordinates."""

    x: float
    y: float
    width: float
    height: float
    label_html: str
    label_x: float
    label_y: float
    label_position: str
    color: str


@dataclass
class ResolvedPointer:
    """Pointer annotation with computed coordinates."""

    target_x: float
    target_y: float
    label_html: str
    label_x: float
    label_y: float
    position: str
    color: str


@dataclass
class ResolvedLabel:
    """Label annotation with computed coordinates."""

    label_html: str
    label_x: float
    label_y: float
    position: str
    leader_target: tuple[float, float] | None = None


@dataclass
class ResolvedLegend:
    """Legend with computed position and size."""

    title: str
    entries: list[LegendEntry]
    x: float
    y: float
    width: float
    height: float
    position: str


@dataclass
class ResolvedAnnotations:
    """All resolved annotations ready for SVG rendering."""

    boxes: list[ResolvedBox] = field(default_factory=list)
    pointers: list[ResolvedPointer] = field(default_factory=list)
    labels: list[ResolvedLabel] = field(default_factory=list)
    legend: ResolvedLegend | None = None
    content_bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

# Matches HTML tags like <b>, </b>, <br>, <span class="x">
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def parse_annotations(data: JsonDict) -> AnnotationSpec:
    """Validate JSON dict and return an ``AnnotationSpec``.

    Raises ``ValueError`` for missing required fields.
    """
    boxes: list[BoxSpec] = []
    for i, raw in enumerate(_as_list(data.get("boxes"))):
        d = _as_dict(raw, f"boxes[{i}]")
        raw_targets = d.get("targets")
        if not raw_targets or not isinstance(raw_targets, list):
            msg = f"boxes[{i}]: 'targets' is required and must be a non-empty list"
            raise ValueError(msg)
        target_strs: list[str] = [str(t) for t in cast("list[object]", raw_targets)]
        boxes.append(
            BoxSpec(
                targets=target_strs,
                label=str(d.get("label", "")),
                label_position=str(d.get("label_position", "")),
                color=str(d.get("color", "")),
            )
        )

    pointers: list[PointerSpec] = []
    for i, raw in enumerate(_as_list(data.get("pointers"))):
        d = _as_dict(raw, f"pointers[{i}]")
        target = str(d.get("target", ""))
        target_net = str(d.get("target_net", ""))
        target_near = str(d.get("target_near", ""))
        if not target and not (target_net and target_near):
            msg = f"pointers[{i}]: 'target' or both 'target_net'+'target_near' required"
            raise ValueError(msg)
        pointers.append(
            PointerSpec(
                target=target,
                target_net=target_net,
                target_near=target_near,
                label=str(d.get("label", "")),
                position=str(d.get("position", "")),
                color=str(d.get("color", "")),
            )
        )

    labels: list[LabelSpec] = []
    for i, raw in enumerate(_as_list(data.get("labels"))):
        d = _as_dict(raw, f"labels[{i}]")
        labels.append(
            LabelSpec(
                target=str(d.get("target", "")),
                content=str(d.get("content", "")),
                position=str(d.get("position", "")),
            )
        )

    legend: LegendSpec | None = None
    raw_legend = data.get("legend")
    if raw_legend is not None:
        ld = _as_dict(raw_legend, "legend")
        raw_entries = ld.get("entries")
        if not raw_entries or not isinstance(raw_entries, list):
            msg = "legend: 'entries' is required and must be a non-empty list"
            raise ValueError(msg)
        entries: list[LegendEntry] = []
        for j, entry_raw in enumerate(cast("list[object]", raw_entries)):
            ed = _as_dict(entry_raw, f"legend.entries[{j}]")
            entries.append(
                LegendEntry(
                    color=str(ed.get("color", "")),
                    label=str(ed.get("label", "")),
                )
            )
        legend = LegendSpec(
            title=str(ld.get("title", "")),
            entries=entries,
            position=str(ld.get("position", "")),
        )

    return AnnotationSpec(boxes=boxes, pointers=pointers, labels=labels, legend=legend)


def _as_list(val: object) -> list[object]:
    if isinstance(val, list):
        return cast("list[object]", val)
    return []


def _as_dict(val: object, context: str) -> JsonDict:
    if isinstance(val, dict):
        return cast("JsonDict", val)
    msg = f"{context}: expected object, got {type(val).__name__}"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


def _resolve_component_target(
    ref: str, board: PcbBoard
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


def _resolve_pad_target(ref_pad: str, board: PcbBoard) -> tuple[float, float]:
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
    for pad in fp.pads:
        if pad.number == pad_num:
            return (pad.x, pad.y)
    msg = f"Pad '{pad_num}' not found on component '{ref}'"
    raise ValueError(msg)


def _resolve_net_target(net_name: str, near_ref: str, board: PcbBoard) -> tuple[float, float]:
    """Find the pad on ``near_ref`` that connects to ``net_name``.

    Raises ``ValueError`` if no matching pad is found.
    """
    fp = board.footprint_by_ref(near_ref)
    if fp is None:
        msg = f"Component '{near_ref}' not found on board"
        raise ValueError(msg)
    needle = net_name.upper()
    for pad in fp.pads:
        if pad.net_name.upper() == needle:
            return (pad.x, pad.y)
    msg = f"Net '{net_name}' not found on component '{near_ref}'"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# Size estimation
# ---------------------------------------------------------------------------


def _estimate_label_size(html: str, font_size: float) -> tuple[float, float]:
    """Estimate label dimensions from HTML content.

    Strips HTML tags for character counting; counts ``<br>`` for line breaks.
    Returns (width, height) in the same units as font_size.
    """
    # Count lines (split on <br> variants)
    lines = re.split(r"<br\s*/?>", html, flags=re.IGNORECASE)
    num_lines = max(len(lines), 1)

    # Strip tags for character counting — find the longest line
    max_chars = 0
    for line in lines:
        plain = _HTML_TAG_RE.sub("", line)
        max_chars = max(max_chars, len(plain))
    max_chars = max(max_chars, 1)

    width = max_chars * font_size * 0.55
    height = num_lines * font_size * 1.4
    return (width, height)


def compute_annotation_font_size(
    board_bbox: tuple[float, float, float, float],
) -> float:
    """Compute annotation font size from board diagonal.

    Scales linearly with diagonal (0.015×), clamped to [0.4, 3.0] mm
    to stay readable on very small boards and not overwhelm large ones.
    """
    x1, y1, x2, y2 = board_bbox
    diagonal = math.hypot(x2 - x1, y2 - y1)
    return max(0.4, min(3.0, diagonal * 0.015))


# ---------------------------------------------------------------------------
# Auto-placement heuristics
# ---------------------------------------------------------------------------


def _auto_place_pointer(
    target_x: float,
    target_y: float,
    label_w: float,
    label_h: float,
    board_bbox: tuple[float, float, float, float],
) -> str:
    """Pick which side to place a pointer label based on target position.

    Returns one of "above", "below", "left", "right".
    """
    bx1, by1, bx2, by2 = board_bbox
    center_x = (bx1 + bx2) / 2
    center_y = (by1 + by2) / 2

    dx = target_x - center_x
    dy = target_y - center_y

    # Normalize by board dimensions to handle non-square boards
    board_w = max(bx2 - bx1, 0.1)
    board_h = max(by2 - by1, 0.1)
    ndx = dx / board_w
    ndy = dy / board_h

    # Place label on the side facing away from center
    if abs(ndx) >= abs(ndy):
        return "right" if ndx >= 0 else "left"
    return "below" if ndy >= 0 else "above"


def _auto_place_box_label(
    box_bbox: tuple[float, float, float, float],
    label_w: float,
    label_h: float,
    board_bbox: tuple[float, float, float, float],
) -> tuple[str, float, float]:
    """Pick label position for a box annotation.

    Tries all four sides and picks the one with the most room between
    the box edge and the board edge.

    Returns (position, label_x, label_y).
    """
    bx1, by1, bx2, by2 = box_bbox
    bbx1, bby1, bbx2, bby2 = board_bbox
    margin = label_h * 0.3
    box_cx = (bx1 + bx2) / 2

    # Room on each side between box edge and board edge
    room: dict[str, float] = {
        "above": by1 - bby1,
        "below": bby2 - by2,
        "left": bx1 - bbx1,
        "right": bbx2 - bx2,
    }
    best = max(room, key=lambda k: room[k])

    if best == "above":
        return ("above", box_cx - label_w / 2, by1 - margin - label_h)
    if best == "below":
        return ("below", box_cx - label_w / 2, by2 + margin)
    if best == "left":
        return ("left", bx1 - margin - label_w, (by1 + by2) / 2 - label_h / 2)
    # right
    return ("right", bx2 + margin, (by1 + by2) / 2 - label_h / 2)


def _auto_place_legend(
    board_bbox: tuple[float, float, float, float],
    legend_w: float,
    legend_h: float,
) -> tuple[str, float, float]:
    """Pick legend position based on board aspect ratio.

    Wide boards → legend below; tall boards → legend to the right.
    Returns (position, x, y).
    """
    bx1, by1, bx2, by2 = board_bbox
    board_w = bx2 - bx1
    board_h = by2 - by1
    margin = max(board_w, board_h) * 0.03

    if board_w >= board_h:
        # Wide board: place legend below, centered
        x = bx1 + (board_w - legend_w) / 2
        y = by2 + margin
        return ("board-bottom", x, y)
    # Tall board: place legend to the right, vertically centered
    x = bx2 + margin
    y = by1 + (board_h - legend_h) / 2
    return ("board-right", x, y)


def _position_from_hint(
    hint: str,
    target_xy: tuple[float, float],
    label_wh: tuple[float, float],
    board_bbox: tuple[float, float, float, float],
    margin: float,
) -> tuple[float, float]:
    """Convert an explicit position hint to (x, y) coordinates."""
    tx, ty = target_xy
    lw, lh = label_wh
    bx1, by1, bx2, by2 = board_bbox

    if hint == "above":
        return (tx - lw / 2, ty - margin - lh)
    if hint == "below":
        return (tx - lw / 2, ty + margin)
    if hint == "left":
        return (tx - margin - lw, ty - lh / 2)
    if hint == "right":
        return (tx + margin, ty - lh / 2)
    if hint == "board-top":
        return (bx1 + (bx2 - bx1 - lw) / 2, by1 - margin - lh)
    if hint == "board-bottom":
        return (bx1 + (bx2 - bx1 - lw) / 2, by2 + margin)
    if hint == "board-left":
        return (bx1 - margin - lw, by1 + (by2 - by1 - lh) / 2)
    if hint == "board-right":
        return (bx2 + margin, by1 + (by2 - by1 - lh) / 2)
    # Unknown hint, treat as auto → use "right" as fallback
    return (tx + margin, ty - lh / 2)


# ---------------------------------------------------------------------------
# Main resolution pipeline
# ---------------------------------------------------------------------------


def resolve_annotations(
    spec: AnnotationSpec,
    board: PcbBoard,
    side: str,
) -> ResolvedAnnotations:
    """Resolve annotation spec to concrete coordinates.

    ``side`` is "front" or "back" — used for future back-side adjustments.
    """
    board_bbox = board.bbox()
    font_size = compute_annotation_font_size(board_bbox)
    margin = font_size * 1.5

    resolved_boxes: list[ResolvedBox] = []
    resolved_pointers: list[ResolvedPointer] = []
    resolved_labels: list[ResolvedLabel] = []
    resolved_legend: ResolvedLegend | None = None

    # Track all annotation positions for content_bbox
    all_xs: list[float] = []
    all_ys: list[float] = []

    # --- Boxes ---
    for box_spec in spec.boxes:
        resolved_box = _resolve_box(box_spec, board, board_bbox, font_size, margin)
        resolved_boxes.append(resolved_box)
        all_xs.extend([resolved_box.x, resolved_box.x + resolved_box.width])
        all_ys.extend([resolved_box.y, resolved_box.y + resolved_box.height])
        if resolved_box.label_html:
            lw, lh = _estimate_label_size(resolved_box.label_html, font_size)
            all_xs.extend([resolved_box.label_x, resolved_box.label_x + lw])
            all_ys.extend([resolved_box.label_y, resolved_box.label_y + lh])

    # --- Pointers ---
    for ptr_spec in spec.pointers:
        resolved_ptr = _resolve_pointer(ptr_spec, board, board_bbox, font_size, margin)
        resolved_pointers.append(resolved_ptr)
        all_xs.append(resolved_ptr.target_x)
        all_ys.append(resolved_ptr.target_y)
        if resolved_ptr.label_html:
            lw, lh = _estimate_label_size(resolved_ptr.label_html, font_size)
            all_xs.extend([resolved_ptr.label_x, resolved_ptr.label_x + lw])
            all_ys.extend([resolved_ptr.label_y, resolved_ptr.label_y + lh])

    # --- Labels ---
    for label_spec in spec.labels:
        resolved_label = _resolve_label(label_spec, board, board_bbox, font_size, margin)
        resolved_labels.append(resolved_label)
        if resolved_label.label_html:
            lw, lh = _estimate_label_size(resolved_label.label_html, font_size)
            all_xs.extend([resolved_label.label_x, resolved_label.label_x + lw])
            all_ys.extend([resolved_label.label_y, resolved_label.label_y + lh])

    # --- Legend ---
    if spec.legend is not None:
        resolved_legend = _resolve_legend_spec(spec.legend, board_bbox, font_size)
        all_xs.extend([resolved_legend.x, resolved_legend.x + resolved_legend.width])
        all_ys.extend([resolved_legend.y, resolved_legend.y + resolved_legend.height])

    # Compute content bbox
    if all_xs and all_ys:
        content_bbox = (min(all_xs), min(all_ys), max(all_xs), max(all_ys))
    else:
        content_bbox = board_bbox

    return ResolvedAnnotations(
        boxes=resolved_boxes,
        pointers=resolved_pointers,
        labels=resolved_labels,
        legend=resolved_legend,
        content_bbox=content_bbox,
    )


# ---------------------------------------------------------------------------
# Per-annotation resolution helpers
# ---------------------------------------------------------------------------


def _resolve_box(
    spec: BoxSpec,
    board: PcbBoard,
    board_bbox: tuple[float, float, float, float],
    font_size: float,
    margin: float,
) -> ResolvedBox:
    """Resolve a box spec to coordinates."""
    # Union of all target bboxes
    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")
    for ref in spec.targets:
        _center, bbox = _resolve_component_target(ref, board)
        bx1, by1, bx2, by2 = bbox
        min_x = min(min_x, bx1)
        min_y = min(min_y, by1)
        max_x = max(max_x, bx2)
        max_y = max(max_y, by2)

    # Add padding around the union bbox
    pad = font_size * 0.5
    box_x = min_x - pad
    box_y = min_y - pad
    box_w = (max_x - min_x) + 2 * pad
    box_h = (max_y - min_y) + 2 * pad
    box_bbox = (box_x, box_y, box_x + box_w, box_y + box_h)

    # Place label
    label_x = box_x
    label_y = box_y
    label_position = spec.label_position
    if spec.label:
        lw, lh = _estimate_label_size(spec.label, font_size)
        if label_position:
            label_x, label_y = _position_from_hint(
                label_position,
                ((box_x + box_x + box_w) / 2, (box_y + box_y + box_h) / 2),
                (lw, lh),
                board_bbox,
                margin,
            )
        else:
            label_position, label_x, label_y = _auto_place_box_label(box_bbox, lw, lh, board_bbox)

    color = spec.color or "rgba(255,107,53,0.9)"
    return ResolvedBox(
        x=box_x,
        y=box_y,
        width=box_w,
        height=box_h,
        label_html=spec.label,
        label_x=label_x,
        label_y=label_y,
        label_position=label_position,
        color=color,
    )


def _resolve_pointer(
    spec: PointerSpec,
    board: PcbBoard,
    board_bbox: tuple[float, float, float, float],
    font_size: float,
    margin: float,
) -> ResolvedPointer:
    """Resolve a pointer spec to coordinates."""
    # Determine target point
    if spec.target:
        if "." in spec.target:
            tx, ty = _resolve_pad_target(spec.target, board)
        else:
            center, _bbox = _resolve_component_target(spec.target, board)
            tx, ty = center
    else:
        tx, ty = _resolve_net_target(spec.target_net, spec.target_near, board)

    # Place label
    lw, lh = _estimate_label_size(spec.label, font_size) if spec.label else (0.0, 0.0)
    position = spec.position
    if position:
        lx, ly = _position_from_hint(position, (tx, ty), (lw, lh), board_bbox, margin)
    else:
        position = _auto_place_pointer(tx, ty, lw, lh, board_bbox)
        lx, ly = _position_from_hint(position, (tx, ty), (lw, lh), board_bbox, margin)

    color = spec.color or "rgba(255,107,53,0.9)"
    return ResolvedPointer(
        target_x=tx,
        target_y=ty,
        label_html=spec.label,
        label_x=lx,
        label_y=ly,
        position=position,
        color=color,
    )


def _resolve_label(
    spec: LabelSpec,
    board: PcbBoard,
    board_bbox: tuple[float, float, float, float],
    font_size: float,
    margin: float,
) -> ResolvedLabel:
    """Resolve a label spec to coordinates."""
    # Determine target and position
    target_xy: tuple[float, float] | None = None
    if spec.target:
        center, _bbox = _resolve_component_target(spec.target, board)
        target_xy = center

    lw, lh = _estimate_label_size(spec.content, font_size) if spec.content else (0.0, 0.0)

    position = spec.position
    is_board_position = position.startswith("board-") if position else False

    if target_xy is not None:
        if position:
            lx, ly = _position_from_hint(position, target_xy, (lw, lh), board_bbox, margin)
        else:
            # Auto-place: use pointer placement to pick a side
            position = _auto_place_pointer(target_xy[0], target_xy[1], lw, lh, board_bbox)
            lx, ly = _position_from_hint(position, target_xy, (lw, lh), board_bbox, margin)
        leader = target_xy if not is_board_position else None
    else:
        # No target — place at board edge
        if not position:
            position = "board-bottom"
        lx, ly = _position_from_hint(
            position,
            ((board_bbox[0] + board_bbox[2]) / 2, board_bbox[3]),
            (lw, lh),
            board_bbox,
            margin,
        )
        leader = None

    return ResolvedLabel(
        label_html=spec.content,
        label_x=lx,
        label_y=ly,
        position=position,
        leader_target=leader,
    )


def _resolve_legend_spec(
    spec: LegendSpec,
    board_bbox: tuple[float, float, float, float],
    font_size: float,
) -> ResolvedLegend:
    """Resolve a legend spec to coordinates."""
    # Estimate legend size from entries
    max_label_len = max((len(e.label) for e in spec.entries), default=5)
    title_len = len(spec.title) if spec.title else 0
    max_text_w = max(max_label_len, title_len) * font_size * 0.55
    # Each entry is one row: swatch + label
    swatch_w = font_size * 1.2
    legend_w = swatch_w + max_text_w + font_size * 2  # padding
    # Title line + one row per entry + padding
    num_rows = len(spec.entries) + (1 if spec.title else 0)
    legend_h = num_rows * font_size * 1.6 + font_size  # padding

    position = spec.position
    if position:
        margin = max(board_bbox[2] - board_bbox[0], board_bbox[3] - board_bbox[1]) * 0.03
        x, y = _position_from_hint(
            position,
            ((board_bbox[0] + board_bbox[2]) / 2, board_bbox[3]),
            (legend_w, legend_h),
            board_bbox,
            margin,
        )
    else:
        position, x, y = _auto_place_legend(board_bbox, legend_w, legend_h)

    return ResolvedLegend(
        title=spec.title,
        entries=spec.entries,
        x=x,
        y=y,
        width=legend_w,
        height=legend_h,
        position=position,
    )
