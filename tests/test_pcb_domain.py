import pytest

from phosphor_eda.domain.pcb import (
    Board,
    LayerRole,
    PadStack,
    PadStackLayer,
    PadStackMode,
    PcbArc,
    PcbArtwork,
    PcbArtworkKind,
    PcbArtworkPurpose,
    PcbBoardProfile,
    PcbBoardProfileElement,
    PcbClosedPath,
    PcbConductor,
    PcbConductorKind,
    PcbDrill,
    PcbDrillPlating,
    PcbDrillShape,
    PcbFootprint,
    PcbKeepout,
    PcbLayer,
    PcbLine,
    PcbNet,
    PcbPad,
    PcbPadType,
    PcbPathSegment,
    PcbPathSegmentKind,
    PcbPolygon,
    PcbPour,
    PcbVia,
    PcbViaType,
    artwork_purpose_for_layer,
    copper_layers,
)
from phosphor_eda.domain.pcb_builder import PcbBuilder, PcbBuildError


def _layer(*roles: LayerRole) -> PcbLayer:
    return PcbLayer(name="L", roles=roles)


def test_artwork_purpose_for_layer_returns_none_without_a_layer() -> None:
    assert artwork_purpose_for_layer(None) is None


def test_artwork_purpose_for_layer_returns_none_for_unmapped_role() -> None:
    assert artwork_purpose_for_layer(_layer(LayerRole.UNKNOWN)) is None


def test_artwork_purpose_for_layer_maps_single_roles() -> None:
    assert artwork_purpose_for_layer(_layer(LayerRole.COPPER)) is PcbArtworkPurpose.COPPER
    assert artwork_purpose_for_layer(_layer(LayerRole.KEEPOUT)) is PcbArtworkPurpose.KEEPOUT
    assert artwork_purpose_for_layer(_layer(LayerRole.SILKSCREEN)) is PcbArtworkPurpose.SILKSCREEN
    assert artwork_purpose_for_layer(_layer(LayerRole.COMMENT)) is PcbArtworkPurpose.USER


def test_artwork_purpose_for_layer_prioritises_text_roles_over_silkscreen() -> None:
    layer = _layer(LayerRole.DESIGNATOR, LayerRole.SILKSCREEN)
    assert artwork_purpose_for_layer(layer) is PcbArtworkPurpose.DESIGNATOR


def test_artwork_purpose_for_layer_prefers_mechanical_over_user() -> None:
    # Reconciliation: KiCad ranked MECHANICAL above USER, Allegro the reverse.
    # The unified table keeps the more specific MECHANICAL.
    layer = _layer(LayerRole.MECHANICAL, LayerRole.USER)
    assert artwork_purpose_for_layer(layer) is PcbArtworkPurpose.MECHANICAL


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
        stack=PadStack.simple("rect", 0.8, 0.6),
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
        stack=PadStack.simple("circle", 0.8, 0.8),
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
    board = Board(
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
        stack=PadStack.simple("rect", 2.0, 4.0),
        pad_type=PcbPadType.SMD,
        layers=(layer,),
    )
    board = Board(
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


def test_pcb_bbox_is_none_for_empty_board() -> None:
    board = Board(
        name="empty",
        layers=[],
        nets={},
        footprints=[],
        pads=[],
        vias=[],
        drills=[],
        conductors=[],
        artwork=[],
        pours=[],
        keepouts=[],
    )

    assert board.bbox() is None


def _degenerate_boundary() -> PcbClosedPath:
    # Two segments — below the 3-point minimum — built directly to bypass
    # PcbClosedPath.from_points, which would reject it on its own.
    return PcbClosedPath(
        segments=(
            PcbPathSegment(PcbPathSegmentKind.LINE, 0.0, 0.0, 1.0, 0.0),
            PcbPathSegment(PcbPathSegmentKind.LINE, 1.0, 0.0, 0.0, 0.0),
        )
    )


def test_pcb_builder_rejects_pour_with_degenerate_boundary() -> None:
    layer = PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT))
    builder = PcbBuilder("bad-pour")
    builder.add_layer(layer)
    pour = PcbPour(id="pour:1", boundary=_degenerate_boundary(), layers=(layer,))
    with pytest.raises(PcbBuildError, match="at least 3 segments"):
        builder.add_pour_object(pour, source="pour:1")


def test_pcb_builder_rejects_keepout_with_degenerate_boundary() -> None:
    layer = PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT))
    builder = PcbBuilder("bad-keepout")
    builder.add_layer(layer)
    keepout = PcbKeepout(id="keepout:1", boundary=_degenerate_boundary(), layers=(layer,))
    with pytest.raises(PcbBuildError, match="at least 3 segments"):
        builder.add_keepout_object(keepout, source="keepout:1")


def test_pcb_builder_rejects_single_arc_boundary() -> None:
    # A single arc segment carries curvature but cannot enclose area on its
    # own; a real circle is two complementary half-arcs.
    layer = PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT))
    builder = PcbBuilder("bad-arc")
    builder.add_layer(layer)
    single_arc = PcbClosedPath(
        segments=(PcbPathSegment(PcbPathSegmentKind.ARC, 1.0, 0.0, 1.0, 0.0, mid_x=-1.0),)
    )
    pour = PcbPour(id="pour:arc", boundary=single_arc, layers=(layer,))
    with pytest.raises(PcbBuildError, match="at least 2 segments"):
        builder.add_pour_object(pour, source="pour:arc")


def test_pcb_builder_rejects_unresolved_and_selector_references() -> None:
    top = PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT))
    builder = PcbBuilder("bad")
    builder.add_layer(top)
    builder.add_net(PcbNet(1, "VCC"))

    with pytest.raises(PcbBuildError, match="Layer selector"):
        builder.resolve_layers(("*.Cu",), source="pad J1.1")

    with pytest.raises(PcbBuildError, match="unknown layer"):
        builder.resolve_layer("B.Cu", source="segment")

    with pytest.raises(PcbBuildError, match="net 0"):
        builder.resolve_net_number(0, source="pad J1.1")

    unregistered = PcbFootprint("U404", "Package:Unknown", 0.0, 0.0, 0.0, top)
    pad = PcbPad(
        id="pad:U404:1",
        number="1",
        x=0.0,
        y=0.0,
        stack=PadStack.simple("circle", 1.0, 1.0),
        pad_type=PcbPadType.SMD,
        layers=(top,),
        footprint=unregistered,
    )
    with pytest.raises(PcbBuildError, match="unknown footprint"):
        builder.add_pad_object(pad, source="graphic")


def test_pcb_builder_rejects_duplicate_drill_id() -> None:
    top = PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT))
    builder = PcbBuilder("dup-drill")
    builder.add_layer(top)
    builder.add_drill_object(PcbDrill("drill:1", 0.0, 0.0, 0.3))

    with pytest.raises(PcbBuildError, match="duplicate drill"):
        builder.add_drill_object(PcbDrill("drill:1", 1.0, 1.0, 0.3))


def test_pcb_builder_rejects_empty_required_board_profile() -> None:
    builder = PcbBuilder("empty-profile")
    builder.add_layer(PcbLayer("Edge.Cuts", (LayerRole.EDGE,)))
    builder.set_board_profile(PcbBoardProfile(elements=()))

    with pytest.raises(PcbBuildError, match="board profile is required"):
        builder.build(require_board_profile=True)


def test_builder_accepts_unconnected_and_mechanical_drills() -> None:
    builder = PcbBuilder("mechanical")
    top = builder.add_layer(PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT)))
    edge = builder.add_layer(PcbLayer("Edge.Cuts", (LayerRole.EDGE,)))
    drill = builder.add_drill_object(
        PcbDrill(
            id="mounting:1",
            x=0.0,
            y=0.0,
            diameter=3.2,
            shape=PcbDrillShape.ROUND,
            plating=PcbDrillPlating.NON_PLATED,
        )
    )
    builder.add_pad_object(
        PcbPad(
            id="pad:free",
            number="MT",
            x=1.0,
            y=1.0,
            stack=PadStack.simple("circle", 4.0, 4.0),
            pad_type=PcbPadType.SMD,
            layers=(top,),
            net=None,
            footprint=None,
        )
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


def _four_layer_board(vias: list[PcbVia], conductors: list[PcbConductor]) -> Board:
    layers = [
        PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT)),
        PcbLayer("In1.Cu", (LayerRole.COPPER, LayerRole.INNER)),
        PcbLayer("In2.Cu", (LayerRole.COPPER, LayerRole.INNER)),
        PcbLayer("B.Cu", (LayerRole.COPPER, LayerRole.BACK)),
    ]
    return Board(
        name="stack-test",
        layers=layers,
        nets={},
        footprints=[],
        pads=[],
        vias=vias,
        drills=[],
        conductors=conductors,
        artwork=[],
        pours=[],
        keepouts=[],
    )


def _via(stack: PadStack, layers: tuple[PcbLayer, ...], net: PcbNet | None = None) -> PcbVia:
    drill = PcbDrill(
        id="d:1",
        x=0.0,
        y=0.0,
        diameter=0.3,
        shape=PcbDrillShape.ROUND,
        plating=PcbDrillPlating.PLATED,
    )
    return PcbVia(id="v:1", x=0.0, y=0.0, stack=stack, layers=layers, drill=drill, net=net)


class TestPadStack:
    def test_simple_wrap_exposes_scalars(self) -> None:
        pad = PcbPad(
            id="p:1",
            number="1",
            x=0.0,
            y=0.0,
            stack=PadStack.simple("roundrect", 1.2, 0.8, corner_radius_ratio=0.25),
            pad_type=PcbPadType.SMD,
            layers=(),
        )
        assert pad.width == 1.2
        assert pad.height == 0.8
        assert pad.shape == "roundrect"
        assert pad.roundrect_rratio == 0.25
        assert pad.stack.mode.value == "simple"

    def test_via_diameter_reads_outer(self) -> None:
        board = _four_layer_board([], [])
        via = _via(PadStack.simple("circle", 0.6, 0.6), tuple(board.layers))
        assert via.diameter == 0.6

    def test_empty_stack_rejected(self) -> None:
        with pytest.raises(PcbBuildError):
            PadStack(mode=PadStackMode.SIMPLE, layers=())


class TestCopperLayers:
    def test_smd_pad_keeps_own_copper_layer(self) -> None:
        board = _four_layer_board([], [])
        pad = PcbPad(
            id="p:1",
            number="1",
            x=0.0,
            y=0.0,
            stack=PadStack.simple("rect", 1.0, 1.0),
            pad_type=PcbPadType.SMD,
            layers=(board.layers[0],),
        )
        assert copper_layers(pad, board) == ["F.Cu"]

    def test_through_pad_spans_all_copper(self) -> None:
        board = _four_layer_board([], [])
        drill = PcbDrill(
            id="d:p",
            x=0.0,
            y=0.0,
            diameter=0.3,
            shape=PcbDrillShape.ROUND,
            plating=PcbDrillPlating.PLATED,
        )
        pad = PcbPad(
            id="p:1",
            number="1",
            x=0.0,
            y=0.0,
            stack=PadStack.simple("circle", 1.0, 1.0),
            pad_type=PcbPadType.THROUGH_HOLE,
            layers=tuple(board.layers),
            drill=drill,
        )
        assert copper_layers(pad, board) == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]

    def test_via_span_between_start_and_end(self) -> None:
        board = _four_layer_board([], [])
        via = _via(
            PadStack.simple("circle", 0.6, 0.6),
            (board.layers[0], board.layers[2]),
        )
        assert copper_layers(via, board) == ["F.Cu", "In1.Cu", "In2.Cu"]

    def test_pruning_keeps_connected_and_end_layers(self) -> None:
        net = PcbNet(number=1, name="SIG")
        stack = PadStack(
            mode=PadStackMode.SIMPLE,
            layers=(PadStackLayer(layer="", shape="circle", size_x=0.6, size_y=0.6),),
            remove_unused_layers=True,
            keep_end_layers=True,
            zone_connected_layers=("In2.Cu",),
        )
        board = _four_layer_board([], [])
        via = _via(stack, tuple(board.layers), net=net)
        trace = PcbConductor(
            id="t:1",
            kind=PcbConductorKind.TRACE,
            layer=board.layers[1],
            data=PcbLine(start_x=0.0, start_y=0.0, end_x=5.0, end_y=0.0, width=0.2),
            net=net,
        )
        board.conductors.append(trace)
        # In1.Cu has a connected trace endpoint, In2.Cu is zone-connected,
        # F.Cu/B.Cu survive as kept end layers.
        assert copper_layers(via, board) == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]

    def test_pruning_drops_unused_layers(self) -> None:
        net = PcbNet(number=1, name="SIG")
        stack = PadStack(
            mode=PadStackMode.SIMPLE,
            layers=(PadStackLayer(layer="", shape="circle", size_x=0.6, size_y=0.6),),
            remove_unused_layers=True,
            keep_end_layers=False,
        )
        board = _four_layer_board([], [])
        via = _via(stack, tuple(board.layers), net=net)
        trace = PcbConductor(
            id="t:1",
            kind=PcbConductorKind.TRACE,
            layer=board.layers[0],
            data=PcbLine(start_x=0.0, start_y=0.0, end_x=5.0, end_y=0.0, width=0.2),
            net=net,
        )
        board.conductors.append(trace)
        assert copper_layers(via, board) == ["F.Cu"]
