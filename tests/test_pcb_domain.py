import pytest

from phosphor_eda.pcb import (
    LayerRole,
    Pcb,
    PcbArc,
    PcbArtwork,
    PcbArtworkKind,
    PcbArtworkPurpose,
    PcbBoardProfile,
    PcbBoardProfileElement,
    PcbConductor,
    PcbConductorKind,
    PcbDrill,
    PcbDrillPlating,
    PcbDrillShape,
    PcbFootprint,
    PcbLayer,
    PcbLine,
    PcbNet,
    PcbPad,
    PcbPadType,
    PcbPolygon,
    PcbVia,
    PcbViaType,
)
from phosphor_eda.pcb_builder import PcbBuilder, PcbBuildError


def test_pcb_domain_has_typed_collections_without_generic_geometry_api() -> None:
    top = PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT))
    edge = PcbLayer("Edge.Cuts", (LayerRole.EDGE,))
    net = PcbNet(number=1, name="VCC")
    footprint = PcbFootprint("U1", "Package_SO:SOIC-8", 1.0, 2.0, 0.0, top)
    drill = PcbDrill("drill:U1:1", 1.0, 2.0, 0.4, PcbDrillShape.ROUND, PcbDrillPlating.PLATED)
    pad = PcbPad(
        id="pad:U1:1",
        number="1",
        x=1.0,
        y=2.0,
        width=0.8,
        height=0.6,
        shape="rect",
        pad_type=PcbPadType.THROUGH_HOLE,
        layers=(top,),
        net=net,
        footprint=footprint,
        drill=drill,
    )
    drill.owner = pad
    via_drill = PcbDrill(
        "drill:via:1",
        3.0,
        4.0,
        0.3,
        PcbDrillShape.ROUND,
        PcbDrillPlating.PLATED,
    )
    via = PcbVia(
        id="via:1",
        x=3.0,
        y=4.0,
        diameter=0.8,
        layers=(top,),
        drill=via_drill,
        via_type=PcbViaType.THROUGH,
        net=None,
    )
    via_drill.owner = via
    conductor = PcbConductor(
        id="trace:1",
        kind=PcbConductorKind.TRACE,
        layer=top,
        data=PcbLine(0.0, 0.0, 1.0, 0.0, 0.15),
        net=net,
    )
    artwork = PcbArtwork(
        id="text:1",
        kind=PcbArtworkKind.TEXT,
        purpose=PcbArtworkPurpose.DESIGNATOR,
        layer=edge,
        data=PcbPolygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]),
        footprint=footprint,
    )
    board = Pcb(
        name="typed",
        layers=[top, edge],
        nets={1: net},
        footprints=[footprint],
        pads=[pad],
        vias=[via],
        drills=[drill, via_drill],
        conductors=[conductor],
        artwork=[artwork],
        pours=[],
        keepouts=[],
        board_profile=PcbBoardProfile(
            elements=(
                PcbBoardProfileElement(
                    id="outline:1",
                    kind=PcbArtworkKind.LINE,
                    layer=edge,
                    data=PcbLine(-1.0, -2.0, 4.0, 6.0, 0.1),
                ),
            )
        ),
    )

    assert not hasattr(board, "geometry")
    assert not hasattr(top, "primary_role")
    assert board.pads_for_footprint("u1") == [pad]
    assert board.pads_for_net(net) == [pad]
    assert board.conductors_for_net(net) == [conductor]
    assert board.drills == [drill, via_drill]
    assert drill.owner is pad
    assert via_drill.owner is via
    assert board.bbox() == (-1.0, -2.0, 4.0, 6.0)


def test_pcb_bbox_falls_back_to_pad_extents_when_profile_is_absent() -> None:
    layer = PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT))
    pad = PcbPad(
        id="pad:1",
        number="1",
        x=5.0,
        y=7.0,
        width=2.0,
        height=4.0,
        shape="rect",
        pad_type=PcbPadType.SMD,
        layers=(layer,),
    )
    board = Pcb(
        name="pad-only",
        layers=[layer],
        nets={},
        footprints=[],
        pads=[pad],
        vias=[],
        drills=[],
        conductors=[],
        artwork=[],
        pours=[],
        keepouts=[],
    )

    assert board.bbox() == (4.0, 5.0, 6.0, 9.0)


def test_pcb_builder_rejects_unresolved_and_selector_references() -> None:
    builder = PcbBuilder("bad")
    builder.add_layer(PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT)))
    builder.add_net(PcbNet(1, "VCC"))

    with pytest.raises(PcbBuildError, match="Layer selector"):
        builder.resolve_layers(("*.Cu",), source="pad J1.1")

    with pytest.raises(PcbBuildError, match="unknown layer"):
        builder.resolve_layer("B.Cu", source="segment")

    with pytest.raises(PcbBuildError, match="net 0"):
        builder.resolve_net_number(0, source="pad J1.1")

    with pytest.raises(PcbBuildError, match="unknown footprint"):
        builder.resolve_footprint("U404", source="graphic")


def test_builder_accepts_unconnected_and_mechanical_drills() -> None:
    builder = PcbBuilder("mechanical")
    top = builder.add_layer(PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT)))
    edge = builder.add_layer(PcbLayer("Edge.Cuts", (LayerRole.EDGE,)))
    drill = builder.add_drill(
        id="mounting:1",
        x=0.0,
        y=0.0,
        diameter=3.2,
        shape=PcbDrillShape.ROUND,
        plating=PcbDrillPlating.NON_PLATED,
    )
    builder.add_pad(
        id="pad:free",
        number="MT",
        x=1.0,
        y=1.0,
        width=4.0,
        height=4.0,
        shape="circle",
        pad_type=PcbPadType.SMD,
        layers=(top,),
        net=None,
        footprint=None,
    )
    builder.set_board_profile(
        PcbBoardProfile(
            elements=(
                PcbBoardProfileElement(
                    id="outline:1",
                    kind=PcbArtworkKind.LINE,
                    layer=edge,
                    data=PcbArc(0.0, 0.0, 1.0, 1.0, 2.0, 0.0, 0.1),
                ),
            )
        )
    )

    board = builder.build()

    assert board.drills == [drill]
    assert drill.owner is None
    assert board.pads[0].net is None
    assert 0 not in board.nets
