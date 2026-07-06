"""Rendered-view coordinate mapping: back-side mirror and view rotation.

The rendered view is the picture of the board the SVG presents: the back
side mirrors horizontally about the board bbox center (as if physically
flipping the board over), and an optional rotation turns the mirrored view
clockwise about the board center. Board geometry gets the equivalent SVG
transform (see ``plan.py``); annotations are resolved directly in
rendered-view coordinates so their labels stay upright.

This module is pure math with no rendering dependencies so both the plan
builder and the annotation resolver can use it.
"""

from __future__ import annotations

VIEW_ROTATIONS = (0, 90, 180, 270)


def to_rendered_view_x(
    x_mm: float,
    board_bbox: tuple[float, float, float, float],
    side: str,
) -> float:
    """Convert a physical board x-coordinate to rendered-view x (mirror only)."""
    if side == "back":
        return board_bbox[0] + board_bbox[2] - x_mm
    return x_mm


def to_rendered_view_point(
    x_mm: float,
    y_mm: float,
    board_bbox: tuple[float, float, float, float],
    side: str,
    rotation: int = 0,
) -> tuple[float, float]:
    """Convert a physical board point to rendered-view coordinates.

    The back side mirrors horizontally about the board bbox center first,
    then ``rotation`` turns the point clockwise about the board center
    (SVG y-down, matching ``rotate(θ cx cy)``).
    """
    x = to_rendered_view_x(x_mm, board_bbox, side)
    y = y_mm
    if not rotation:
        return (x, y)
    bx1, by1, bx2, by2 = board_bbox
    cx = (bx1 + bx2) / 2
    cy = (by1 + by2) / 2
    dx = x - cx
    dy = y - cy
    if rotation == 90:
        return (cx - dy, cy + dx)
    if rotation == 180:
        return (cx - dx, cy - dy)
    return (cx + dy, cy - dx)


def to_rendered_view_bbox(
    bbox: tuple[float, float, float, float],
    board_bbox: tuple[float, float, float, float],
    side: str,
    rotation: int = 0,
) -> tuple[float, float, float, float]:
    """Convert a physical board bbox to rendered-view coordinates."""
    x1, y1, x2, y2 = bbox
    ax, ay = to_rendered_view_point(x1, y1, board_bbox, side, rotation)
    bx, by = to_rendered_view_point(x2, y2, board_bbox, side, rotation)
    return (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))


def rendered_view_board_bbox(
    board_bbox: tuple[float, float, float, float],
    rotation: int = 0,
) -> tuple[float, float, float, float]:
    """Board bbox as it appears in the rendered view.

    The mirror maps the bbox onto itself; a 90/270 rotation swaps the
    extents about the board center.
    """
    if rotation not in (90, 270):
        return board_bbox
    bx1, by1, bx2, by2 = board_bbox
    cx = (bx1 + bx2) / 2
    cy = (by1 + by2) / 2
    half_w = (bx2 - bx1) / 2
    half_h = (by2 - by1) / 2
    return (cx - half_h, cy - half_w, cx + half_h, cy + half_w)


def board_view_transform(
    board_bbox: tuple[float, float, float, float],
    side: str,
    rotation: int = 0,
) -> str:
    """SVG transform that maps board geometry into the rendered view.

    Returns "" for the identity view (front, no rotation). SVG applies the
    transform list right-to-left, so the mirror comes last in the string
    and is applied to the geometry first, matching
    ``to_rendered_view_point``.
    """
    parts: list[str] = []
    if rotation:
        cx = (board_bbox[0] + board_bbox[2]) / 2
        cy = (board_bbox[1] + board_bbox[3]) / 2
        parts.append(f"rotate({rotation} {cx:.4f} {cy:.4f})")
    if side == "back":
        shift = board_bbox[0] + board_bbox[2]
        parts.append(f"translate({shift:.4f} 0) scale(-1 1)")
    return " ".join(parts)
