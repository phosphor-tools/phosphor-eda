"""Board-polygon dedupe must not collapse distinct shapes sharing a bbox."""

from phosphor_eda.altium.pcb_parser import (
    _dedupe_shape_based_board_polygons,  # pyright: ignore[reportPrivateUsage]
    _ParsedObjectKind,  # pyright: ignore[reportPrivateUsage]
    _ParsedPrimitive,  # pyright: ignore[reportPrivateUsage]
    _ParsedRole,  # pyright: ignore[reportPrivateUsage]
    _ParsedShapeKind,  # pyright: ignore[reportPrivateUsage]
)
from phosphor_eda.pcb import PcbPolygon


def _polygon(prim_id: str, points: list[tuple[float, float]]) -> _ParsedPrimitive:
    return _ParsedPrimitive(
        id=prim_id,
        object_type=_ParsedObjectKind.REGION,
        shape=_ParsedShapeKind.POLYGON,
        roles=(_ParsedRole.BOARD_LEVEL,),
        data=PcbPolygon(points=points),
        layers=("Top Layer",),
    )


def test_same_bbox_different_vertices_both_survive() -> None:
    # A rectangular frame and an inscribed triangle share the same bbox.
    frame = _polygon("region:0", [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])
    triangle = _polygon("shape:0", [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)])

    result = _dedupe_shape_based_board_polygons([frame], [triangle])

    assert len(result) == 1, "a different-vertex polygon sharing the bbox must not be dropped"
    assert result[0].id == "shape:0"


def test_true_duplicate_is_dropped() -> None:
    pts = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    region = _polygon("region:0", pts)
    duplicate = _polygon("shape:0", list(pts))

    result = _dedupe_shape_based_board_polygons([region], [duplicate])

    assert result == []
