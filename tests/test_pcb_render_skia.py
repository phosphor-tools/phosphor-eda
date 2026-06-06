from __future__ import annotations

from test_pcb_render import _board

from phosphor_eda.pcb import (
    PcbGeometry,
    PcbGeometryObject,
    PcbGeometryRole,
    PcbGeometryShape,
    PcbZoneGeometry,
)
from phosphor_eda.pcb_render_geometry import build_geometry_store
from phosphor_eda.pcb_render_skia import geometry_to_skia_artwork, skia_path_to_svg_d


def test_skia_converts_pad_trace_via_and_mask_aperture_renderables() -> None:
    store = build_geometry_store(_board(), side="front")

    for display_role in ("pad", "trace", "via", "solder_mask"):
        item = next(item for item in store.items if item.display_role == display_role)
        artwork = geometry_to_skia_artwork(item, target_layer_name=item.layer.name)

        assert artwork is not None
        assert skia_path_to_svg_d(artwork.path)


def test_skia_converts_zone_geometry_from_normalized_object_type() -> None:
    board = _board()
    board.geometry.append(
        PcbGeometry(
            id="zone:1",
            object_type=PcbGeometryObject.ZONE,
            shape=PcbGeometryShape.POLYGON,
            roles=(
                PcbGeometryRole.COPPER,
                PcbGeometryRole.POUR,
                PcbGeometryRole.ZONE_FILL,
                PcbGeometryRole.BOARD_LEVEL,
            ),
            data=PcbZoneGeometry([(1.0, 1.0), (4.0, 1.0), (4.0, 4.0), (1.0, 4.0)]),
            layers=("F.Cu",),
            net_number=1,
        )
    )
    store = build_geometry_store(board, side="front")
    zone = next(item for item in store.items if item.object_type == PcbGeometryObject.ZONE)

    artwork = geometry_to_skia_artwork(zone, target_layer_name="F.Cu")

    assert artwork is not None
    assert skia_path_to_svg_d(artwork.path)
