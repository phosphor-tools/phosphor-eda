"""Margin assignment, the CP-SAT placement solver, and connector routing.

Everything here operates in display-pixel space. The resolver converts
board-mm targets to pixels before calling in, and converts the resolved
coordinates back to board mm for the viewBox.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ortools.sat.python import cp_model

if TYPE_CHECKING:
    from collections.abc import Callable

ANNOTATION_FONT_PX = 10.0

# Margin gap between board edge and label column (pixels)
MARGIN_GAP_PX = 16.0

# Margin gap for legends (pixels)
LEGEND_GAP_PX = 10.0

# Minimum gap between labels in the same margin (pixels)
_LABEL_SPACING_PX = 12.0

# Box padding around target components (pixels)
BOX_PAD_PX = 6.0


def px_scale(
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


# ---------------------------------------------------------------------------
# Margin assignment
# ---------------------------------------------------------------------------

_MARGIN_SIDES = ("left", "right", "top", "bottom")


def hint_to_margin(hint: str) -> str:
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


def auto_assign_margin(
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


def text_anchor_for_margin(margin: str) -> str:
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
class PlacementItem:
    """Internal item for the margin placement solver."""

    label_width: float
    label_height: float
    target_x: float
    target_y: float
    margin: str  # "left", "right", "top", "bottom"
    margin_gap: float = 0.0  # 0 = use the default gap


@dataclass
class PlacedResult:
    """Solved position for a placement item."""

    label_x: float
    label_y: float


# Integer coordinate scale for the CP-SAT solver, which needs integer
# variables: 1 unit = 0.01 display px (the solver works in pixel space).
_COORD_SCALE = 100


def solve_margin_placement(
    items: list[PlacementItem],
    board_bbox: tuple[float, float, float, float],
    margin_gap: float,
    warn: Callable[[str], None] | None = None,
) -> list[PlacedResult]:
    """Place labels in board margins, minimizing total connector length.

    Each label is fixed to its margin side (one dimension) and gets a
    variable position in the other dimension. The solver minimizes total
    connector length while preventing label overlaps.

    Falls back to greedy stacking if the solver doesn't find a solution
    in time.
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
        results: list[PlacedResult] = []
        for i, it in enumerate(items):
            v = solver.value(item_vars[i]) / _COORD_SCALE
            fx = item_fixed[i]
            if it.margin in ("left", "right"):
                results.append(PlacedResult(label_x=fx, label_y=v))
            else:
                results.append(PlacedResult(label_x=v, label_y=fx))
        return results

    # Fallback: greedy stacking sorted by target position
    if warn is not None:
        warn(
            "Annotation label placement solver found no solution; "
            "using greedy fallback (labels may overlap)"
        )
    return _fallback_placement(items, board_bbox, margin_gap)


def _fallback_placement(
    items: list[PlacementItem],
    board_bbox: tuple[float, float, float, float],
    margin_gap: float,
) -> list[PlacedResult]:
    """Simple sorted-stacking when the solver fails."""
    bx1, by1, bx2, by2 = board_bbox
    spacing = margin_gap * 0.3

    results: list[PlacedResult] = [PlacedResult(0.0, 0.0)] * len(items)

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
                results[idx] = PlacedResult(label_x=fx, label_y=cursor_y)
                cursor_y += it.label_height + spacing
        else:
            # Sort by target_x, stack horizontally
            indices.sort(key=lambda i: items[i].target_x)
            cursor_x = bx1
            for idx in indices:
                it = items[idx]
                mg = it.margin_gap if it.margin_gap > 0 else margin_gap
                fy = by2 + mg if margin == "bottom" else by1 - mg - it.label_height
                results[idx] = PlacedResult(label_x=cursor_x, label_y=fy)
                cursor_x += it.label_width + spacing

    return results


# ---------------------------------------------------------------------------
# Orthogonal connector paths
# ---------------------------------------------------------------------------


def compute_connector(
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
