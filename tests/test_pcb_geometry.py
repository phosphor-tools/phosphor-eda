from phosphor_eda.pcb import (
    LayerRole,
    Pcb,
    PcbGeometry,
    PcbGeometryObject,
    PcbGeometryRole,
    PcbGeometryShape,
    PcbLayer,
    PcbLineGeometry,
    PcbPadGeometry,
    PcbPolygonGeometry,
    normalize_geometry_roles,
)


def test_normalize_geometry_roles_removes_duplicates_and_uses_canonical_order() -> None:
    assert normalize_geometry_roles(
        PcbGeometryRole.TRACE,
        "copper",
        "route",
        "trace",
    ) == (
        PcbGeometryRole.COPPER,
        PcbGeometryRole.ROUTE,
        PcbGeometryRole.TRACE,
    )


def test_geometry_roles_and_display_role_are_normalized() -> None:
    pad = PcbGeometry(
        id="pad:U1:1",
        object_type=PcbGeometryObject.PAD,
        shape=PcbGeometryShape.RECTANGLE,
        roles=(PcbGeometryRole.CONDUCTOR, PcbGeometryRole.COPPER, PcbGeometryRole.SMD),
        data=PcbPadGeometry("1", 1.0, 2.0, 0.8, 0.6, "rect"),
        layers=("F.Cu",),
        net_number=1,
        net_name="VCC",
        footprint_ref="U1",
    )

    assert pad.has_role("copper")
    assert pad.role_values == ("copper", "conductor", "smd")
    assert pad.primary_role == PcbGeometryRole.COPPER
    assert pad.primary_layer == "F.Cu"
    assert pad.display_role == "pad"


def test_pcb_geometry_helpers_query_roles_type_shape_layer_footprint_and_net() -> None:
    outline = PcbGeometry(
        id="outline:0",
        object_type=PcbGeometryObject.GRAPHIC,
        shape=PcbGeometryShape.LINE,
        roles=(PcbGeometryRole.EDGE, PcbGeometryRole.BOARD_OUTLINE, PcbGeometryRole.BOARD_LEVEL),
        data=PcbLineGeometry(0.0, 0.0, 10.0, 0.0, 0.1),
        layers=("Edge.Cuts",),
    )
    pad = PcbGeometry(
        id="pad:U1:1",
        object_type=PcbGeometryObject.PAD,
        shape=PcbGeometryShape.RECTANGLE,
        roles=(PcbGeometryRole.COPPER, PcbGeometryRole.CONDUCTOR, PcbGeometryRole.SMD),
        data=PcbPadGeometry("1", 1.0, 2.0, 0.8, 0.6, "rect"),
        layers=("F.Cu",),
        net_number=1,
        net_name="VCC",
        footprint_ref="U1",
    )
    pour = PcbGeometry(
        id="zone:0",
        object_type=PcbGeometryObject.ZONE,
        shape=PcbGeometryShape.POLYGON,
        roles=(PcbGeometryRole.COPPER, PcbGeometryRole.POUR, PcbGeometryRole.ZONE_FILL),
        data=PcbPolygonGeometry([(0.0, 0.0), (3.0, 0.0), (3.0, 3.0), (0.0, 3.0)]),
        layers=("F.Cu",),
        net_number=1,
        net_name="VCC",
    )
    board = Pcb(
        name="geometry",
        nets={},
        footprints=[],
        geometry=[outline, pad, pour],
        layers=[
            PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT)),
            PcbLayer("Edge.Cuts", (LayerRole.EDGE,)),
        ],
    )

    assert [item.id for item in board.geometry_by_role("copper")] == ["pad:U1:1", "zone:0"]
    assert [item.id for item in board.geometry_by_object_type("pad")] == ["pad:U1:1"]
    assert [item.id for item in board.geometry_by_shape("line")] == ["outline:0"]
    assert [item.id for item in board.geometry_on_layer("F.Cu")] == ["pad:U1:1", "zone:0"]
    assert [item.id for item in board.geometry_for_footprint("U1")] == ["pad:U1:1"]
    assert [item.id for item in board.geometry_for_net(1)] == ["pad:U1:1", "zone:0"]
    assert [item.id for item in board.board_outline_geometry()] == ["outline:0"]


def test_pcb_bbox_uses_outline_geometry_and_falls_back_to_pads() -> None:
    board = Pcb(
        name="outline",
        nets={},
        footprints=[],
        geometry=[
            PcbGeometry(
                id="outline:0",
                object_type=PcbGeometryObject.GRAPHIC,
                shape=PcbGeometryShape.LINE,
                roles=(PcbGeometryRole.EDGE, PcbGeometryRole.BOARD_OUTLINE),
                data=PcbLineGeometry(-1.0, 2.0, 4.0, 6.0, 0.1),
                layers=("Edge.Cuts",),
            )
        ],
    )
    assert board.bbox() == (-1.0, 2.0, 4.0, 6.0)

    pad_only = Pcb(
        name="pad-only",
        nets={},
        footprints=[],
        geometry=[
            PcbGeometry(
                id="pad:U1:1",
                object_type=PcbGeometryObject.PAD,
                shape=PcbGeometryShape.RECTANGLE,
                roles=(PcbGeometryRole.COPPER,),
                data=PcbPadGeometry("1", 5.0, 7.0, 2.0, 4.0, "rect"),
                layers=("F.Cu",),
            )
        ],
    )
    assert pad_only.bbox() == (4.0, 5.0, 6.0, 9.0)
