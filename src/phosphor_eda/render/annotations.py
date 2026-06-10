"""PCB annotation data model, JSON parsing, target resolution, and placement.

Annotations let the agent draw boxes, pointers, labels, and legends on
rendered PCB SVGs using schematic vocabulary (component refs, net names,
pad numbers) without needing to know coordinates. Placement is automatic
by default; the model can override with position hints.

The pipeline is:

1. ``parse_annotations(data)`` — validate JSON → ``AnnotationSpec``
2. ``resolve_annotations(spec, board, side, width_px)`` — resolve targets,
   convert to pixel space, run placement solver, compute connectors
   → ``ResolvedAnnotations`` (coordinates in display pixels)

All resolved coordinates are in **display pixel space**, not board mm.
The renderer wraps the annotation ``<g>`` with
``transform="scale(px_scale)"`` where ``px_scale = board_width / width_px``
to map pixel coordinates back into the SVG viewBox.  This means CSS
properties like ``font-size: 14px`` are actual screen pixels regardless
of board physical size.

Labels are placed in clear margins outside the board outline, connected
to their targets by orthogonal (right-angle) connector paths.  The
CP-SAT solver from OR-Tools ensures labels never overlap within a margin.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from ortools.sat.python import cp_model

from phosphor_eda.geometry.text_metrics import measure_text

if TYPE_CHECKING:
    from collections.abc import Callable

    from phosphor_eda.domain.pcb import Pcb

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
# Resolved types (coordinates in display pixels, ready for SVG emission)
# ---------------------------------------------------------------------------


@dataclass
class ResolvedBox:
    """Box annotation with computed coordinates.

    The box rect sits on the board.  The label goes in a margin
    and is connected to the box by an orthogonal connector path.
    """

    x: float
    y: float
    width: float
    height: float
    label_text: str
    label_x: float
    label_y: float
    label_width: float
    label_height: float
    connector_path: list[tuple[float, float]]
    color: str
    text_anchor: str = "middle"


@dataclass
class ResolvedPointer:
    """Pointer annotation with computed coordinates.

    The label is in a margin, connected to the target point
    by an orthogonal connector with an arrowhead at the target.
    """

    target_x: float
    target_y: float
    label_text: str
    label_x: float
    label_y: float
    label_width: float
    label_height: float
    connector_path: list[tuple[float, float]]
    color: str
    text_anchor: str = "middle"


@dataclass
class ResolvedLabel:
    """Label annotation with computed coordinates.

    The label is in a margin, connected to the target point
    by an orthogonal connector path.
    """

    label_text: str
    label_x: float
    label_y: float
    label_width: float
    label_height: float
    connector_path: list[tuple[float, float]]
    text_anchor: str = "middle"


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
# JSON parsing
# ---------------------------------------------------------------------------


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
    ref: str, board: Pcb
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


def _resolve_pad_target(ref_pad: str, board: Pcb) -> tuple[float, float]:
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


def _resolve_net_target(net_name: str, near_ref: str, board: Pcb) -> tuple[float, float]:
    """Find the pad on ``near_ref`` that connects to ``net_name``.

    Raises ``ValueError`` if no matching pad is found.
    """
    fp = board.footprint_by_ref(near_ref)
    if fp is None:
        msg = f"Component '{near_ref}' not found on board"
        raise ValueError(msg)
    requested_net_name = net_name
    needle = requested_net_name.upper()
    for pad in board.pads_for_footprint(fp):
        if pad.net is not None and pad.net.name.upper() == needle:
            return (pad.x, pad.y)
    msg = f"Net '{requested_net_name}' not found on component '{near_ref}'"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# Font size and label measurement
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Pixel-space constants
# ---------------------------------------------------------------------------

# Annotation font size in display pixels.  The annotation layer uses a
# transform so that 1 unit = 1 screen pixel regardless of board size.
ANNOTATION_FONT_PX = 10.0

# Margin gap between board edge and label column (pixels)
_MARGIN_GAP_PX = 16.0

# Margin gap for legends (pixels)
_LEGEND_GAP_PX = 10.0

# Minimum gap between labels in the same margin (pixels)
_LABEL_SPACING_PX = 12.0

# Label pill padding (as multiples of font size)
_PAD_H_PX = 6.0  # horizontal padding on each side (pixels)
_PAD_V_PX = 4.0  # vertical padding on each side (pixels)

# Box padding around target components (pixels)
_BOX_PAD_PX = 6.0


def _px_scale(
    board_bbox: tuple[float, float, float, float],
    width_px: int,
) -> float:
    """Compute the board-mm-to-pixel scale factor.

    Returns the number of board mm per display pixel.
    Multiply a pixel value by this to get board mm.
    Divide a board-mm value by this to get pixels.
    """
    bw = board_bbox[2] - board_bbox[0]
    return bw / width_px if width_px > 0 else 1.0


def _to_rendered_view_x(
    x_mm: float,
    board_bbox: tuple[float, float, float, float],
    side: str,
) -> float:
    """Convert a physical board x-coordinate to rendered-view x-coordinate."""
    if side == "back":
        return board_bbox[0] + board_bbox[2] - x_mm
    return x_mm


def _to_rendered_view_bbox(
    bbox: tuple[float, float, float, float],
    board_bbox: tuple[float, float, float, float],
    side: str,
) -> tuple[float, float, float, float]:
    """Convert a physical board bbox to rendered-view coordinates."""
    x1, y1, x2, y2 = bbox
    rx1 = _to_rendered_view_x(x2, board_bbox, side)
    rx2 = _to_rendered_view_x(x1, board_bbox, side)
    return (min(rx1, rx2), y1, max(rx1, rx2), y2)


def _measure_label(text: str, font_size: float) -> tuple[float, float]:
    """Measure a label pill including padding.

    Returns (width, height) in the same units as ``font_size``.
    """
    if not text:
        return (0.0, 0.0)
    tw, th = measure_text(text, font_size)
    return (tw + 2 * _PAD_H_PX, th + 2 * _PAD_V_PX)


# ---------------------------------------------------------------------------
# Margin assignment
# ---------------------------------------------------------------------------

_MARGIN_SIDES = ("left", "right", "top", "bottom")


def _hint_to_margin(hint: str) -> str:
    """Convert a position hint to a margin side.

    Explicit margin hints: "left", "right", "above"/"top", "below"/"bottom",
    "board-left", "board-right", "board-top", "board-bottom".
    Returns "" if the hint doesn't specify a margin.
    """
    h = hint.lower().replace("board-", "")
    if h in ("right",):
        return "right"
    if h in ("left",):
        return "left"
    if h in ("above", "top"):
        return "top"
    if h in ("below", "bottom"):
        return "bottom"
    return ""


def _auto_assign_margin(
    target_x: float,
    target_y: float,
    board_bbox: tuple[float, float, float, float],
) -> str:
    """Pick which margin to place a label in based on target position.

    Routes the label to the nearest board edge so connectors are short and
    don't cross the board interior.  Ties (equal distance to two edges)
    prefer horizontal edges (right/left) over vertical, matching the
    natural reading direction for labels.
    """
    bx1, by1, bx2, by2 = board_bbox

    distances = {
        "right": bx2 - target_x,
        "left": target_x - bx1,
        "bottom": by2 - target_y,
        "top": target_y - by1,
    }

    return min(distances, key=distances.__getitem__)


def _text_anchor_for_margin(margin: str) -> str:
    """Return SVG text-anchor for text inside a pill placed in a board margin."""
    if margin == "left":
        return "end"
    if margin == "right":
        return "start"
    return "middle"


# ---------------------------------------------------------------------------
# Placement solver (CP-SAT)
# ---------------------------------------------------------------------------


@dataclass
class _PlacementItem:
    """Internal item for the margin placement solver."""

    label_width: float
    label_height: float
    target_x: float
    target_y: float
    margin: str  # "left", "right", "top", "bottom"
    margin_gap: float = 0.0  # 0 = use the default gap


@dataclass
class _PlacedResult:
    """Solved position for a placement item."""

    label_x: float
    label_y: float


# Integer coordinate scale: 1 unit = 0.01 mm
_COORD_SCALE = 100


def _solve_margin_placement(
    items: list[_PlacementItem],
    board_bbox: tuple[float, float, float, float],
    margin_gap: float,
    warn: Callable[[str], None] | None = None,
) -> list[_PlacedResult]:
    """Position all labels in their assigned margins using CP-SAT.

    Each label gets a fixed position in one dimension (determined by
    its margin side) and a variable position in the other dimension.
    The solver minimizes total connector length while preventing
    label overlaps.

    Falls back to greedy stacking if the solver doesn't find a
    solution in time.
    """
    if not items:
        return []

    bx1, by1, bx2, by2 = board_bbox
    bw = bx2 - bx1
    bh = by2 - by1
    spacing = _LABEL_SPACING_PX

    # Domain bounds for the variable dimension
    y_lo = int((by1 - bh) * _COORD_SCALE)
    y_hi = int((by2 + bh) * _COORD_SCALE)
    x_lo = int((bx1 - bw) * _COORD_SCALE)
    x_hi = int((bx2 + bw) * _COORD_SCALE)

    model = cp_model.CpModel()

    # Per-item variables and intervals, grouped by margin
    item_vars: list[cp_model.IntVar] = []  # variable dimension for each item
    item_fixed: list[float] = []  # fixed dimension for each item

    margin_intervals: dict[str, list[cp_model.IntervalVar]] = {m: [] for m in _MARGIN_SIDES}

    for i, it in enumerate(items):
        margin = it.margin
        mg = it.margin_gap if it.margin_gap > 0 else margin_gap
        if margin in ("left", "right"):
            # Variable: y position
            size = int(it.label_height * _COORD_SCALE)
            gap = int(spacing * _COORD_SCALE)
            v = model.new_int_var(y_lo, y_hi, f"y_{i}")
            item_vars.append(v)
            # Fixed: x position
            if margin == "right":
                item_fixed.append(bx2 + mg)
            else:
                item_fixed.append(bx1 - mg - it.label_width)
            # 1D interval for non-overlap along y axis
            interval = model.new_fixed_size_interval_var(v, size + gap, f"iy_{i}")
            margin_intervals[margin].append(interval)
        else:
            # Variable: x position
            size = int(it.label_width * _COORD_SCALE)
            gap = int(spacing * _COORD_SCALE)
            v = model.new_int_var(x_lo, x_hi, f"x_{i}")
            item_vars.append(v)
            # Fixed: y position
            if margin == "bottom":
                item_fixed.append(by2 + mg)
            else:
                item_fixed.append(by1 - mg - it.label_height)
            # 1D interval for non-overlap along x axis
            interval = model.new_fixed_size_interval_var(v, size + gap, f"ix_{i}")
            margin_intervals[margin].append(interval)

    # Non-overlap within each margin
    for m in _MARGIN_SIDES:
        intervals = margin_intervals[m]
        if len(intervals) >= 2:
            _ = model.add_no_overlap(intervals)

    # Objective: minimize total distance from label center to target
    abs_vars: list[cp_model.IntVar] = []
    for i, it in enumerate(items):
        if it.margin in ("left", "right"):
            # Variable is y; target is target_y
            target_scaled = int((it.target_y - it.label_height / 2) * _COORD_SCALE)
            diff = model.new_int_var(-(y_hi - y_lo), y_hi - y_lo, f"diff_{i}")
            _ = model.add(diff == item_vars[i] - target_scaled)
            abs_v = model.new_int_var(0, y_hi - y_lo, f"abs_{i}")
            _ = model.add_abs_equality(abs_v, diff)
            abs_vars.append(abs_v)
        else:
            # Variable is x; target is target_x
            target_scaled = int((it.target_x - it.label_width / 2) * _COORD_SCALE)
            diff = model.new_int_var(-(x_hi - x_lo), x_hi - x_lo, f"diff_{i}")
            _ = model.add(diff == item_vars[i] - target_scaled)
            abs_v = model.new_int_var(0, x_hi - x_lo, f"abs_{i}")
            _ = model.add_abs_equality(abs_v, diff)
            abs_vars.append(abs_v)

    model.minimize(sum(abs_vars))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 1.0
    status = solver.solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        results: list[_PlacedResult] = []
        for i, it in enumerate(items):
            v = solver.value(item_vars[i]) / _COORD_SCALE
            fx = item_fixed[i]
            if it.margin in ("left", "right"):
                results.append(_PlacedResult(label_x=fx, label_y=v))
            else:
                results.append(_PlacedResult(label_x=v, label_y=fx))
        return results

    # Fallback: greedy stacking sorted by target position
    if warn is not None:
        warn(
            "Annotation label placement solver found no solution; "
            "using greedy fallback (labels may overlap)"
        )
    return _fallback_placement(items, board_bbox, margin_gap)


def _fallback_placement(
    items: list[_PlacementItem],
    board_bbox: tuple[float, float, float, float],
    margin_gap: float,
) -> list[_PlacedResult]:
    """Simple sorted-stacking when the solver fails."""
    bx1, by1, bx2, by2 = board_bbox
    spacing = margin_gap * 0.3

    results: list[_PlacedResult] = [_PlacedResult(0.0, 0.0)] * len(items)

    # Group by margin, sort by target position, stack
    for margin in _MARGIN_SIDES:
        indices = [i for i, it in enumerate(items) if it.margin == margin]
        if not indices:
            continue

        if margin in ("left", "right"):
            # Sort by target_y, stack vertically
            indices.sort(key=lambda i: items[i].target_y)
            cursor_y = by1
            for idx in indices:
                it = items[idx]
                mg = it.margin_gap if it.margin_gap > 0 else margin_gap
                fx = bx2 + mg if margin == "right" else bx1 - mg - it.label_width
                results[idx] = _PlacedResult(label_x=fx, label_y=cursor_y)
                cursor_y += it.label_height + spacing
        else:
            # Sort by target_x, stack horizontally
            indices.sort(key=lambda i: items[i].target_x)
            cursor_x = bx1
            for idx in indices:
                it = items[idx]
                mg = it.margin_gap if it.margin_gap > 0 else margin_gap
                fy = by2 + mg if margin == "bottom" else by1 - mg - it.label_height
                results[idx] = _PlacedResult(label_x=cursor_x, label_y=fy)
                cursor_x += it.label_width + spacing

    return results


# ---------------------------------------------------------------------------
# Orthogonal connector paths
# ---------------------------------------------------------------------------


def _compute_connector(
    label_x: float,
    label_y: float,
    label_w: float,
    label_h: float,
    target_x: float,
    target_y: float,
    margin: str,
    board_bbox: tuple[float, float, float, float],
    margin_gap: float,
    target_rect: tuple[float, float, float, float] | None = None,
) -> list[tuple[float, float]]:
    """Compute an orthogonal (right-angle) connector path.

    Returns a list of (x, y) waypoints from the label edge to the
    target point.  The path has at most two right-angle turns.

    When *target_rect* ``(x, y, w, h)`` is given, the endpoint is
    clipped to the nearest edge of that rect instead of going to the
    center (used for box annotations).
    """
    bx1, by1, bx2, by2 = board_bbox
    label_cy = label_y + label_h / 2
    label_cx = label_x + label_w / 2
    # Routing column/row sits halfway between board edge and label column
    route_offset = margin_gap * 0.45

    # When targeting a box rect, clip the endpoint to the box edge.
    # The last segment is horizontal for left/right margins, vertical
    # for top/bottom — so we clamp the appropriate axis.
    end_x = target_x
    end_y = target_y
    if target_rect is not None:
        rx, ry, rw, rh = target_rect
        if margin in ("left", "right"):
            # Last segment is horizontal — clip x to box edge
            end_x = rx + rw if margin == "right" else rx
            # Clamp y to stay within the box vertically
            end_y = max(ry, min(ry + rh, target_y))
        else:
            # Last segment is vertical — clip y to box edge
            end_y = ry + rh if margin == "bottom" else ry
            # Clamp x to stay within the box horizontally
            end_x = max(rx, min(rx + rw, target_x))

    if margin == "right":
        start_x = label_x
        start_y = label_cy
        route_x = bx2 + route_offset
        return [
            (start_x, start_y),
            (route_x, start_y),
            (route_x, end_y),
            (end_x, end_y),
        ]

    if margin == "left":
        start_x = label_x + label_w
        start_y = label_cy
        route_x = bx1 - route_offset
        return [
            (start_x, start_y),
            (route_x, start_y),
            (route_x, end_y),
            (end_x, end_y),
        ]

    if margin == "bottom":
        start_x = label_cx
        start_y = label_y
        route_y = by2 + route_offset
        return [
            (start_x, start_y),
            (start_x, route_y),
            (end_x, route_y),
            (end_x, end_y),
        ]

    # top
    start_x = label_cx
    start_y = label_y + label_h
    route_y = by1 - route_offset
    return [
        (start_x, start_y),
        (start_x, route_y),
        (end_x, route_y),
        (end_x, end_y),
    ]


# ---------------------------------------------------------------------------
# Legend measurement
# ---------------------------------------------------------------------------


def _measure_legend(
    spec: LegendSpec,
    font_size: float,
) -> tuple[float, float]:
    """Compute legend box size from title and entries.

    Returns (width, height) in the same units as ``font_size``.
    """
    title_w = 0.0
    title_h = 0.0
    if spec.title:
        tw, th = measure_text(spec.title, font_size * 0.85)
        title_w = tw
        title_h = th + font_size * 0.3  # gap below title

    swatch_size = font_size * 0.8
    swatch_gap = font_size * 0.4
    entry_gap = font_size * 0.2
    max_entry_w = 0.0
    total_entry_h = 0.0
    for i, entry in enumerate(spec.entries):
        ew, eh = measure_text(entry.label, font_size)
        row_w = (swatch_size + swatch_gap + ew) if entry.color else ew
        max_entry_w = max(max_entry_w, row_w)
        total_entry_h += max(eh, swatch_size)
        if i > 0:
            total_entry_h += entry_gap

    pad_h = font_size * 0.6
    pad_v = font_size * 0.5
    width = max(title_w, max_entry_w) + 2 * pad_h
    height = title_h + total_entry_h + 2 * pad_v

    return (width, height)


# ---------------------------------------------------------------------------
# Main resolution pipeline
# ---------------------------------------------------------------------------


_COLOR_RE = re.compile(
    r"^(#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}|rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+)",
)


def _warn_unparseable_color(color: str, context: str, warnings: list[str]) -> None:
    """Record a warning when a user color string won't render as intended."""
    if color and not _COLOR_RE.match(color):
        warnings.append(f"{context}: unparseable color {color!r}; using fallback orange")


def resolve_annotations(
    spec: AnnotationSpec,
    board: Pcb,
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
    scale = _px_scale(board_bbox, width_px)
    margin_gap = _MARGIN_GAP_PX
    box_pad = _BOX_PAD_PX
    warnings: list[str] = []

    # Convert board bbox to pixel space for placement engine
    pbx1 = board_bbox[0] / scale
    pby1 = board_bbox[1] / scale
    pbx2 = board_bbox[2] / scale
    pby2 = board_bbox[3] / scale
    px_board_bbox = (pbx1, pby1, pbx2, pby2)

    # Phase 1: Resolve all targets and measure all labels
    # Collect placement items for the margin solver

    placement_items: list[_PlacementItem] = []
    # Back-references: (source_type, source_index) for each placement item
    placement_refs: list[tuple[str, int]] = []

    # Pre-resolve data for each annotation type (pixel space)
    box_data: list[tuple[float, float, float, float, str]] = []  # (x, y, w, h, color)
    ptr_data: list[tuple[float, float, str]] = []  # (target_x, target_y, color)
    lbl_data: list[tuple[float, float]] = []  # (target_x, target_y)

    # --- Boxes ---
    for i, box_spec in enumerate(spec.boxes):
        # Compute box rect from target union (board mm → pixels)
        min_x = float("inf")
        min_y = float("inf")
        max_x = float("-inf")
        max_y = float("-inf")
        for ref in box_spec.targets:
            _center, bbox = _resolve_component_target(ref, board)
            bx1, by1, bx2, by2 = _to_rendered_view_bbox(bbox, board_bbox, side)
            min_x = min(min_x, bx1 / scale)
            min_y = min(min_y, by1 / scale)
            max_x = max(max_x, bx2 / scale)
            max_y = max(max_y, by2 / scale)

        box_x = min_x - box_pad
        box_y = min_y - box_pad
        box_w = (max_x - min_x) + 2 * box_pad
        box_h = (max_y - min_y) + 2 * box_pad
        _warn_unparseable_color(box_spec.color, f"box {i}", warnings)
        color = box_spec.color or "rgba(255,107,53,0.9)"
        box_data.append((box_x, box_y, box_w, box_h, color))

        if box_spec.label:
            lw, lh = _measure_label(box_spec.label, font_size)
            target_cx = box_x + box_w / 2
            target_cy = box_y + box_h / 2

            margin = _hint_to_margin(box_spec.label_position)
            if not margin:
                margin = _auto_assign_margin(target_cx, target_cy, px_board_bbox)

            placement_items.append(_PlacementItem(lw, lh, target_cx, target_cy, margin))
            placement_refs.append(("box", i))

    # --- Pointers ---
    for i, ptr_spec in enumerate(spec.pointers):
        if ptr_spec.target:
            if "." in ptr_spec.target:
                tx_mm, ty_mm = _resolve_pad_target(ptr_spec.target, board)
            else:
                center, _bbox = _resolve_component_target(ptr_spec.target, board)
                tx_mm, ty_mm = center
        else:
            tx_mm, ty_mm = _resolve_net_target(ptr_spec.target_net, ptr_spec.target_near, board)
        tx_mm = _to_rendered_view_x(tx_mm, board_bbox, side)
        tx = tx_mm / scale
        ty = ty_mm / scale
        _warn_unparseable_color(ptr_spec.color, f"pointer {i}", warnings)
        color = ptr_spec.color or "rgba(255,107,53,0.9)"
        ptr_data.append((tx, ty, color))

        if ptr_spec.label:
            lw, lh = _measure_label(ptr_spec.label, font_size)
            margin = _hint_to_margin(ptr_spec.position)
            if not margin:
                margin = _auto_assign_margin(tx, ty, px_board_bbox)

            placement_items.append(_PlacementItem(lw, lh, tx, ty, margin))
            placement_refs.append(("pointer", i))

    # --- Labels ---
    for i, label_spec in enumerate(spec.labels):
        if label_spec.target:
            center, _bbox = _resolve_component_target(label_spec.target, board)
            tx = _to_rendered_view_x(center[0], board_bbox, side) / scale
            ty = center[1] / scale
        else:
            tx = (board_bbox[0] + board_bbox[2]) / 2 / scale
            ty = (board_bbox[1] + board_bbox[3]) / 2 / scale
        lbl_data.append((tx, ty))

        if label_spec.content:
            lw, lh = _measure_label(label_spec.content, font_size)
            margin = _hint_to_margin(label_spec.position)
            if not margin:
                margin = _auto_assign_margin(tx, ty, px_board_bbox)

            placement_items.append(_PlacementItem(lw, lh, tx, ty, margin))
            placement_refs.append(("label", i))

    # --- Legend ---
    legend_width = 0.0
    legend_height = 0.0
    legend_margin = ""
    if spec.legend is not None:
        legend_width, legend_height = _measure_legend(spec.legend, font_size)
        legend_margin = _hint_to_margin(spec.legend.position)
        if not legend_margin:
            # Default: bottom for wide boards, right for tall boards
            bw = board_bbox[2] - board_bbox[0]
            bh = board_bbox[3] - board_bbox[1]
            legend_margin = "bottom" if bw >= bh else "right"

        placement_items.append(
            _PlacementItem(
                legend_width,
                legend_height,
                (pbx1 + pbx2) / 2,
                (pby1 + pby2) / 2,
                legend_margin,
                margin_gap=_LEGEND_GAP_PX,
            )
        )
        placement_refs.append(("legend", 0))

    # Phase 2: Run placement solver (all in pixel space)
    placed = _solve_margin_placement(
        placement_items, px_board_bbox, margin_gap, warn=warnings.append
    )

    # Phase 3: Build resolved annotations from placement results

    # Index placement results by (source_type, source_index)
    placed_by_ref: dict[tuple[str, int], tuple[_PlacedResult, _PlacementItem]] = {}
    for ref, result, item in zip(placement_refs, placed, placement_items, strict=True):
        placed_by_ref[ref] = (result, item)

    all_xs: list[float] = []
    all_ys: list[float] = []

    # --- Build resolved boxes ---
    resolved_boxes: list[ResolvedBox] = []
    for i, (bx, by, bw, bh, color) in enumerate(box_data):
        box_spec_i = spec.boxes[i]
        if box_spec_i.label and ("box", i) in placed_by_ref:
            result, item = placed_by_ref[("box", i)]
            connector = _compute_connector(
                result.label_x,
                result.label_y,
                item.label_width,
                item.label_height,
                item.target_x,
                item.target_y,
                item.margin,
                px_board_bbox,
                margin_gap,
                target_rect=(bx, by, bw, bh),
            )
            resolved_boxes.append(
                ResolvedBox(
                    x=bx,
                    y=by,
                    width=bw,
                    height=bh,
                    label_text=box_spec_i.label,
                    label_x=result.label_x,
                    label_y=result.label_y,
                    label_width=item.label_width,
                    label_height=item.label_height,
                    connector_path=connector,
                    color=color,
                    text_anchor=_text_anchor_for_margin(item.margin),
                )
            )
            all_xs.extend([bx, bx + bw, result.label_x, result.label_x + item.label_width])
            all_ys.extend([by, by + bh, result.label_y, result.label_y + item.label_height])
        else:
            resolved_boxes.append(
                ResolvedBox(
                    x=bx,
                    y=by,
                    width=bw,
                    height=bh,
                    label_text="",
                    label_x=bx,
                    label_y=by,
                    label_width=0,
                    label_height=0,
                    connector_path=[],
                    color=color,
                    text_anchor="middle",
                )
            )
            all_xs.extend([bx, bx + bw])
            all_ys.extend([by, by + bh])

    # --- Build resolved pointers ---
    resolved_pointers: list[ResolvedPointer] = []
    for i, (tx, ty, color) in enumerate(ptr_data):
        ptr_spec_i = spec.pointers[i]
        if ptr_spec_i.label and ("pointer", i) in placed_by_ref:
            result, item = placed_by_ref[("pointer", i)]
            connector = _compute_connector(
                result.label_x,
                result.label_y,
                item.label_width,
                item.label_height,
                tx,
                ty,
                item.margin,
                px_board_bbox,
                margin_gap,
            )
            resolved_pointers.append(
                ResolvedPointer(
                    target_x=tx,
                    target_y=ty,
                    label_text=ptr_spec_i.label,
                    label_x=result.label_x,
                    label_y=result.label_y,
                    label_width=item.label_width,
                    label_height=item.label_height,
                    connector_path=connector,
                    color=color,
                    text_anchor=_text_anchor_for_margin(item.margin),
                )
            )
            all_xs.extend([tx, result.label_x, result.label_x + item.label_width])
            all_ys.extend([ty, result.label_y, result.label_y + item.label_height])
        else:
            resolved_pointers.append(
                ResolvedPointer(
                    target_x=tx,
                    target_y=ty,
                    label_text="",
                    label_x=tx,
                    label_y=ty,
                    label_width=0,
                    label_height=0,
                    connector_path=[],
                    color=color,
                    text_anchor="middle",
                )
            )
            all_xs.append(tx)
            all_ys.append(ty)

    # --- Build resolved labels ---
    resolved_labels: list[ResolvedLabel] = []
    for i, (tx, ty) in enumerate(lbl_data):
        label_spec_i = spec.labels[i]
        if label_spec_i.content and ("label", i) in placed_by_ref:
            result, item = placed_by_ref[("label", i)]
            has_target = bool(label_spec_i.target)
            connector = (
                _compute_connector(
                    result.label_x,
                    result.label_y,
                    item.label_width,
                    item.label_height,
                    tx,
                    ty,
                    item.margin,
                    px_board_bbox,
                    margin_gap,
                )
                if has_target
                else []
            )
            resolved_labels.append(
                ResolvedLabel(
                    label_text=label_spec_i.content,
                    label_x=result.label_x,
                    label_y=result.label_y,
                    label_width=item.label_width,
                    label_height=item.label_height,
                    connector_path=connector,
                    text_anchor=_text_anchor_for_margin(item.margin),
                )
            )
            all_xs.extend([result.label_x, result.label_x + item.label_width])
            all_ys.extend([result.label_y, result.label_y + item.label_height])
        else:
            resolved_labels.append(
                ResolvedLabel(
                    label_text="",
                    label_x=tx,
                    label_y=ty,
                    label_width=0,
                    label_height=0,
                    connector_path=[],
                    text_anchor="middle",
                )
            )

    # --- Build resolved legend ---
    resolved_legend: ResolvedLegend | None = None
    if spec.legend is not None and ("legend", 0) in placed_by_ref:
        result, item = placed_by_ref[("legend", 0)]
        resolved_legend = ResolvedLegend(
            title=spec.legend.title,
            entries=spec.legend.entries,
            x=result.label_x,
            y=result.label_y,
            width=legend_width,
            height=legend_height,
        )
        all_xs.extend([result.label_x, result.label_x + legend_width])
        all_ys.extend([result.label_y, result.label_y + legend_height])

    # Content bbox — add padding for outward strokes and dots, then
    # convert from pixel space back to board mm for viewBox expansion.
    _CONTENT_PAD_PX = 6.0  # covers stroke-width + dot radius
    if all_xs and all_ys:
        content_bbox = (
            (min(all_xs) - _CONTENT_PAD_PX) * scale,
            (min(all_ys) - _CONTENT_PAD_PX) * scale,
            (max(all_xs) + _CONTENT_PAD_PX) * scale,
            (max(all_ys) + _CONTENT_PAD_PX) * scale,
        )
    else:
        content_bbox = board_bbox

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
