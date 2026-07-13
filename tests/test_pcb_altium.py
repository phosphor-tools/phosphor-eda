"""Tests for the Altium .PcbDoc parser."""

from pathlib import Path

import pytest
from pcb_layer_helpers import make_pcb_layer

from phosphor_eda.domain.pcb import (
    Board,
    LayerRole,
    PadStack,
    PadStackMode,
    PcbArtworkKind,
    PcbArtworkPurpose,
    PcbCircle,
    PcbConductorKind,
    PcbDrillPlating,
    PcbLayer,
    PcbLine,
    PcbModel3D,
    PcbNet,
    PcbPadType,
    PcbPolygon,
)
from phosphor_eda.formats.altium.pcb_build import build_pcb_from_parsed_primitives
from phosphor_eda.formats.altium.pcb_layers import (
    build_layer_map,
)
from phosphor_eda.formats.altium.pcb_parser import parse_altium_pcb
from phosphor_eda.formats.altium.pcb_primitives import (
    ParsedObjectKind,
    ParsedPadPayload,
    ParsedPrimitive,
    ParsedRole,
    ParsedShapeKind,
    ParsedViaPayload,
    int_to_mm,
    parse_mil,
)
from phosphor_eda.formats.altium.pcb_streams import (
    arc_to_three_point,
    parse_board6_outline,
    parse_component_bodies,
    parse_pads,
    parse_vias,
)
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.render.inventory import InventoryItemKind, InventoryPurpose, build_inventory

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PI_MX8_PCBDOC = FIXTURES / "altium/pi-mx8/PCB/PiMX8MP_r0.3.PcbDoc"


@pytest.fixture(scope="module")
def board() -> Board:
    return parse_altium_pcb(PI_MX8_PCBDOC)


def test_int_to_mm_basic() -> None:
    assert int_to_mm(10_000_000) == pytest.approx(25.4, rel=1e-6)


def test_parse_mil() -> None:
    assert parse_mil("1000mil") == pytest.approx(25.4, rel=1e-6)
    assert parse_mil("1000") == pytest.approx(25.4, rel=1e-6)
    assert parse_mil("12.3mil") == pytest.approx(0.31242, rel=1e-3)


def test_build_layer_map_does_not_create_fallback_names() -> None:
    assert build_layer_map({}) == {}

    layer_map = build_layer_map(
        {
            "layer1name": "Top Layer",
            "layer32name": "Bottom Layer",
            "layer69name": "Assembly Top",
            "layer69mechkind": "AssemblyTop",
        }
    )

    assert set(layer_map) == {1, 32, 69}
    assert layer_map[1].has_role(LayerRole.COPPER)
    assert layer_map[1].side == "front"
    assert layer_map[32].side == "back"
    assert set(layer_map[69].roles) >= {
        LayerRole.MECHANICAL,
        LayerRole.FABRICATION,
        LayerRole.ASSEMBLY,
        LayerRole.FRONT,
    }


def test_v9_stack_layer_names_create_explicit_layers() -> None:
    layer_map = build_layer_map(
        {
            "v9_stack_layer0_layerid": "16973832",
            "v9_stack_layer0_name": "Top Paste Renamed",
            "v9_stack_layer1_layerid": "16777218",
            "v9_stack_layer1_name": "Signal 1",
        }
    )

    assert set(layer_map) == {2, 35}
    assert layer_map[35].name == "Top Paste Renamed"
    assert layer_map[35].has_role(LayerRole.SOLDER_PASTE)
    assert layer_map[2].name == "Signal 1"
    assert layer_map[2].has_role(LayerRole.COPPER)


def test_arc_conversion_semicircle() -> None:
    sx, sy, mx, my, ex, ey = arc_to_three_point(0, 0, 1.0, 0, 180)

    assert sx == pytest.approx(1.0, abs=1e-6)
    assert sy == pytest.approx(0.0, abs=1e-6)
    assert mx == pytest.approx(0.0, abs=1e-6)
    assert my == pytest.approx(1.0, abs=1e-6)
    assert ex == pytest.approx(-1.0, abs=1e-6)
    assert ey == pytest.approx(0.0, abs=1e-6)


def test_component_body_parser_extracts_models() -> None:
    payload = (
        "MODELID={ABC-123}|COMPONENT=2|MODEL.2D.X=1000mil|MODEL.2D.Y=500mil|"
        "MODEL.3D.DZ=100mil|MODEL.3D.ROTZ=90|"
    ).encode("ascii")
    data = len(payload).to_bytes(4, "little") + payload

    result = parse_component_bodies(data)

    assert 2 in result
    assert len(result[2]) == 1
    model = result[2][0].data
    assert isinstance(model, PcbModel3D)
    assert model.source == "{ABC-123}"
    assert model.offset == pytest.approx((25.4, -12.7, 2.54), rel=1e-3)
    assert model.rotation == (0.0, 0.0, 90.0)


def test_altium_parser_emits_typed_domain_collections(board: Board) -> None:
    assert not hasattr(board, "geometry")
    assert len(board.layers) == 74
    assert len(board.nets) > 400
    assert 0 not in board.nets
    assert len(board.footprints) > 500
    assert len(board.pads) > 2_000
    assert len(board.vias) > 2_000
    assert len(board.drills) > 2_000
    assert len(board.conductors) > 10_000
    assert len(board.artwork) > 10_000
    assert len(board.pours) > 40
    assert board.board_profile is not None
    assert len(board.board_profile.elements) > 0


def test_altium_layers_have_roles_but_no_primary_role(board: Board) -> None:
    assert all(not hasattr(layer, "primary_role") for layer in board.layers)
    assert any(layer.has_role(LayerRole.COPPER) for layer in board.layers)
    assert any(layer.has_role(LayerRole.SOLDER_MASK) for layer in board.layers)
    assert any(layer.has_role(LayerRole.MECHANICAL) for layer in board.layers)


def test_altium_free_pads_are_board_level_not_fake_footprints(board: Board) -> None:
    free_pads = [pad for pad in board.pads if pad.footprint is None]

    assert free_pads
    assert "FREEPADS" not in {footprint.reference for footprint in board.footprints}
    assert all(pad.net is None for pad in free_pads)


def test_altium_footprint_fills_are_attributed_to_footprints(board: Board) -> None:
    fill_conductors = [c for c in board.conductors if c.metadata.native_type == "FILL"]

    assert fill_conductors, "expected copper fills in the fixture"
    owned = [c for c in fill_conductors if c.metadata.native_component_index is not None]
    assert owned, "footprint-owned fills should carry a component index"
    assert all(c.footprint is not None for c in owned), (
        "footprint-owned fills should attach to their footprint, not the board"
    )


def test_altium_pad_rotation_survives_domain_conversion(board: Board) -> None:
    pad = next(item for item in board.pads if item.id == "pad:0:1")

    assert pad.footprint is not None
    assert pad.footprint.reference == "R153"
    assert pad.rotation == pytest.approx(270.0)


def test_altium_pad_mask_apertures_survive_domain_conversion(board: Board) -> None:
    apertures = [
        pad
        for pad in board.pads
        if pad.mask_aperture is not None and pad.mask_aperture.aperture_width is not None
    ]

    assert len(apertures) == 4
    assert all(pad.footprint is None for pad in apertures)
    assert {pad.number for pad in apertures} == {"MT"}
    assert all(
        pad.mask_aperture is not None and pad.mask_aperture.aperture_width == pytest.approx(5.8)
        for pad in apertures
    )
    assert all(
        pad.mask_aperture is not None and pad.mask_aperture.aperture_height == pytest.approx(5.85)
        for pad in apertures
    )
    assert all(
        pad.mask_aperture is not None
        and pad.mask_aperture.source.startswith("altium:drill-manager-template:")
        for pad in apertures
    )


def test_altium_pad_mask_apertures_emit_solder_mask_inventory(board: Board) -> None:
    inventory = build_inventory(board, side="front")
    aperture_pad_ids = {
        pad.id
        for pad in board.pads
        if pad.mask_aperture is not None and pad.mask_aperture.aperture_width is not None
    }
    mask_items = [
        item
        for item in inventory.items
        if item.item_kind == InventoryItemKind.PAD
        and item.purpose == InventoryPurpose.SOLDER_MASK
        and item.source.id in aperture_pad_ids
    ]

    assert aperture_pad_ids
    assert {item.source.id for item in mask_items} == aperture_pad_ids
    assert {item.layer.side for item in mask_items if item.layer is not None} == {"front", "back"}


def test_altium_pads_and_vias_reference_first_class_drills(board: Board) -> None:
    drilled_pads = [pad for pad in board.pads if pad.drill is not None]

    assert drilled_pads
    assert all(pad.pad_type == PcbPadType.THROUGH_HOLE for pad in drilled_pads)
    assert all(pad.drill in board.drills for pad in drilled_pads if pad.drill is not None)
    assert all(pad.drill is None or pad.drill.owner is pad for pad in drilled_pads)
    assert all(via.drill in board.drills for via in board.vias[:100])
    assert all(via.drill.owner is via for via in board.vias[:100])


def test_altium_conductors_and_pours_use_real_net_refs(board: Board) -> None:
    routes = [
        item
        for item in board.conductors
        if item.kind in {PcbConductorKind.TRACE, PcbConductorKind.TRACE_ARC}
    ]
    fills = [item for item in board.conductors if item.kind == PcbConductorKind.POUR_FILL]

    assert routes
    assert fills
    assert all(item.layer in board.layers for item in board.conductors[:500])
    assert all(item.net is None or item.net.number in board.nets for item in board.conductors[:500])
    assert all(fill.pour in board.pours for fill in fills)
    assert any(pour.fills for pour in board.pours)


def test_altium_artwork_and_board_profile_are_typed(board: Board) -> None:
    assert board.board_profile is not None
    assert all(
        element.layer in board.layers
        for element in board.board_profile.elements
        if element.layer is not None
    )
    assert any(item.kind == PcbArtworkKind.TEXT for item in board.artwork)
    assert all(item.layer is None or item.layer in board.layers for item in board.artwork[:500])


def test_altium_full_circle_arcs_are_circle_artwork(board: Board) -> None:
    full_circle = next(item for item in board.artwork if item.id == "arc:33:24")

    assert full_circle.kind == PcbArtworkKind.CIRCLE
    assert isinstance(full_circle.data, PcbCircle)
    assert full_circle.data.cx == pytest.approx(90.871, abs=1e-3)
    assert full_circle.data.cy == pytest.approx(-142.379, abs=1e-3)
    # radius is the stroke centerline (outer radius 0.175 minus width/2).
    assert full_circle.data.radius == pytest.approx(0.050, abs=1e-3)
    assert full_circle.data.width == pytest.approx(0.250, abs=1e-3)
    assert full_circle.data.fill is False


def test_altium_component_visibility_flags_hide_designator_and_value_text(board: Board) -> None:
    component_labels = [
        item
        for item in board.artwork
        if item.footprint is not None
        and item.purpose in {PcbArtworkPurpose.DESIGNATOR, PcbArtworkPurpose.VALUE}
    ]
    inventory_ids = {item.id for item in build_inventory(board, side="front").items}

    assert component_labels
    assert all(item.metadata.hidden for item in component_labels)
    assert {item.id for item in component_labels}.isdisjoint(inventory_ids)


def test_altium_roundrect_pads_carry_shape_and_radius(board: Board) -> None:
    # pi-mx8 has 18 pads whose sub6 alt-shape is roundrect (value 9).
    roundrect = [pad for pad in board.pads if pad.shape == "roundrect"]
    assert len(roundrect) == 18
    assert all(0 < pad.roundrect_rratio <= 0.5 for pad in roundrect)


def test_altium_non_plated_holes_are_marked(board: Board) -> None:
    # pi-mx8's only holed pads are its 4 NPTH mounting holes (sub5 plated
    # flag cleared); every other pad is SMD. Vias stay plated.
    pad_platings = {pad.drill.plating for pad in board.pads if pad.drill is not None}
    assert pad_platings == {PcbDrillPlating.NON_PLATED}
    npth = [
        pad
        for pad in board.pads
        if pad.drill is not None and pad.drill.plating == PcbDrillPlating.NON_PLATED
    ]
    assert len(npth) == 4
    assert all(via.drill.plating == PcbDrillPlating.PLATED for via in board.vias)


def test_altium_slot_pad_parses_slot_drill() -> None:
    # Captured from LimeSDR-USB_1v4s.PcbDoc: slot hole, 1.0mm wide,
    # 2.5mm long, rotated 90 degrees.
    data = (FIXTURES / "altium" / "pad_slot.bin").read_bytes()
    nets = {403: PcbNet(number=403, name="SLOT_NET")}
    layer_map = {1: make_pcb_layer("Top", LayerRole.COPPER, side="front", number=1)}
    pads = parse_pads(data, nets, layer_map, ParseContext())

    assert len(pads) == 1
    payload = pads[0][1].data
    assert isinstance(payload, ParsedPadPayload)
    assert payload.hole_is_slot
    assert payload.drill == pytest.approx(1.0, rel=1e-3)
    assert payload.slot_length == pytest.approx(2.5, rel=1e-3)
    assert payload.slot_rotation == 90.0


# ---------------------------------------------------------------------------
# Padstacks — stacked pad records captured from cyber60-mxhs.PcbDoc, plus
# synthetic via records (the corpus has no non-simple vias to capture).
# ---------------------------------------------------------------------------

_STACK_NETS = {137: PcbNet(number=137, name="STACK_NET")}


def _stack_layer_map(*, inner: int = 1) -> dict[int, PcbLayer]:
    """Copper layer map: Top, Mid1..Mid<inner>, Bottom (Altium numbering)."""
    layer_map = {1: make_pcb_layer("Top", LayerRole.COPPER, side="front", number=1)}
    for index in range(inner):
        layer_map[2 + index] = make_pcb_layer(
            f"Mid{index + 1}", LayerRole.COPPER, side="inner", number=2 + index
        )
    layer_map[32] = make_pcb_layer("Bottom", LayerRole.COPPER, side="back", number=32)
    return layer_map


def _stack_geometries(stack: PadStack) -> list[tuple[str, str, float, float]]:
    return [
        (entry.layer, entry.shape, round(entry.size_x, 3), round(entry.size_y, 3))
        for entry in stack.layers
    ]


def test_altium_pad_stack_top_mid_bottom_payload() -> None:
    # Captured from cyber60-mxhs.PcbDoc pad 732: padmode 1, circles —
    # top/bottom 1.3x2.0mm, mid 1.0x1.6mm.
    data = (FIXTURES / "altium" / "pad_stack_top_mid_bottom.bin").read_bytes()
    pads = parse_pads(data, _STACK_NETS, _stack_layer_map(), ParseContext())

    assert len(pads) == 1
    assert pads[0][1].metadata.properties["pad_mode"] == "TOP_MIDDLE_BOTTOM"
    payload = pads[0][1].data
    assert isinstance(payload, ParsedPadPayload)
    stack = payload.stack
    assert stack is not None
    assert stack.mode is PadStackMode.TOP_MID_BOTTOM
    assert _stack_geometries(stack) == [
        ("top", "circle", 1.3, 2.0),
        ("mid", "circle", 1.0, 1.6),
        ("bottom", "circle", 1.3, 2.0),
    ]
    # The outer entry is the scalar geometry 2D consumers keep seeing.
    assert stack.outer.size_x == payload.width
    assert stack.outer.size_y == payload.height
    assert stack.outer.shape == payload.shape


def test_altium_pad_stack_full_payload() -> None:
    # Captured from cyber60-mxhs.PcbDoc pad 729: padmode 2 (full stack) —
    # top/bottom 1.3x2.4mm; Mid1 comes from the sub5 mid size and Mid2 from
    # the sub6 inner-size array, both 1.0x2.1mm.
    data = (FIXTURES / "altium" / "pad_stack_full.bin").read_bytes()
    pads = parse_pads(data, _STACK_NETS, _stack_layer_map(inner=2), ParseContext())

    assert len(pads) == 1
    payload = pads[0][1].data
    assert isinstance(payload, ParsedPadPayload)
    stack = payload.stack
    assert stack is not None
    assert stack.mode is PadStackMode.PER_LAYER
    assert _stack_geometries(stack) == [
        ("Top", "circle", 1.3, 2.4),
        ("Mid1", "circle", 1.0, 2.1),
        ("Mid2", "circle", 1.0, 2.1),
        ("Bottom", "circle", 1.3, 2.4),
    ]


def _make_stacked_pad_record(
    mode: int,
    top: tuple[int, int],
    mid: tuple[int, int],
    bot: tuple[int, int],
    shapes: tuple[int, int, int] = (1, 1, 1),
) -> bytes:
    """Synthetic Pads6 record with a 63-byte sub5 carrying the stack mode."""
    record = bytearray()
    record.append(2)  # record type
    name = b"\x011"  # Pascal string "1"
    record.extend(len(name).to_bytes(4, "little"))
    record.extend(name)
    for _ in range(3):  # sub2-4 empty
        record.extend((0).to_bytes(4, "little"))
    sub5 = bytearray(63)
    sub5[0] = 74  # multi-layer
    sub5[3:5] = (136).to_bytes(2, "little")
    sub5[7:9] = (0xFFFF).to_bytes(2, "little")
    sub5[21:25] = top[0].to_bytes(4, "little")
    sub5[25:29] = top[1].to_bytes(4, "little")
    sub5[29:33] = mid[0].to_bytes(4, "little")
    sub5[33:37] = mid[1].to_bytes(4, "little")
    sub5[37:41] = bot[0].to_bytes(4, "little")
    sub5[41:45] = bot[1].to_bytes(4, "little")
    sub5[49], sub5[50], sub5[51] = shapes
    sub5[60] = 1  # plated
    sub5[62] = mode
    record.extend(len(sub5).to_bytes(4, "little"))
    record.extend(sub5)
    record.extend((0).to_bytes(4, "little"))  # sub6 empty
    return bytes(record)


def test_altium_pad_stack_uniform_geometry_stays_simple() -> None:
    data = _make_stacked_pad_record(
        mode=1, top=(511811, 787402), mid=(511811, 787402), bot=(511811, 787402)
    )
    pads = parse_pads(data, _STACK_NETS, _stack_layer_map(), ParseContext())

    assert len(pads) == 1
    payload = pads[0][1].data
    assert isinstance(payload, ParsedPadPayload)
    assert payload.stack is None


def _make_via_stream(
    body: bytes | None = None,
    *,
    mode: int | None = None,
    diameters: tuple[int, ...] = (),
    diameter: int = 600000,
) -> bytes:
    """Synthetic Vias6 stream with one record (type 3 + length + body)."""
    if body is None:
        raw = bytearray(203 if mode is not None else 31)
        raw[3:5] = (0xFFFF).to_bytes(2, "little")  # unconnected
        raw[7:9] = (0xFFFF).to_bytes(2, "little")  # free via
        raw[21:25] = diameter.to_bytes(4, "little")
        raw[25:29] = (300000).to_bytes(4, "little")
        raw[29] = 1
        raw[30] = 32
        if mode is not None:
            raw[74] = mode
            for index, layer_diameter in enumerate(diameters):
                raw[75 + index * 4 : 79 + index * 4] = layer_diameter.to_bytes(4, "little")
        body = bytes(raw)
    return bytes([3]) + len(body).to_bytes(4, "little") + body


def test_altium_via_stack_top_mid_bottom() -> None:
    diameters = (600000,) + (500000,) * 30 + (700000,)
    data = _make_via_stream(mode=1, diameters=diameters)
    vias = parse_vias(data, _stack_layer_map(), ParseContext())

    assert len(vias) == 1
    payload = vias[0].data
    assert isinstance(payload, ParsedViaPayload)
    stack = payload.stack
    assert stack is not None
    assert stack.mode is PadStackMode.TOP_MID_BOTTOM
    assert _stack_geometries(stack) == [
        ("top", "circle", 1.524, 1.524),
        ("mid", "circle", 1.27, 1.27),
        ("bottom", "circle", 1.778, 1.778),
    ]


def test_altium_via_stack_full_per_layer() -> None:
    diameters = (600000, 500000) + (400000,) * 29 + (700000,)
    data = _make_via_stream(mode=2, diameters=diameters)
    vias = parse_vias(data, _stack_layer_map(inner=2), ParseContext())

    assert len(vias) == 1
    payload = vias[0].data
    assert isinstance(payload, ParsedViaPayload)
    stack = payload.stack
    assert stack is not None
    assert stack.mode is PadStackMode.PER_LAYER
    assert _stack_geometries(stack) == [
        ("Top", "circle", 1.524, 1.524),
        ("Mid1", "circle", 1.27, 1.27),
        ("Mid2", "circle", 1.016, 1.016),
        ("Bottom", "circle", 1.778, 1.778),
    ]


def test_altium_via_stack_uniform_stays_simple() -> None:
    data = _make_via_stream(mode=1, diameters=(600000,) * 32)
    vias = parse_vias(data, _stack_layer_map(), ParseContext())

    assert len(vias) == 1
    payload = vias[0].data
    assert isinstance(payload, ParsedViaPayload)
    assert payload.stack is None


def test_altium_via_unknown_stack_mode_records_diagnostic() -> None:
    # Stop condition: undocumented stack-mode bytes get a diagnostic and the
    # via keeps its simple outer geometry rather than guessing a layout.
    ctx = ParseContext()
    data = _make_via_stream(mode=7, diameters=(600000,) * 32)
    vias = parse_vias(data, _stack_layer_map(), ctx)

    assert len(vias) == 1
    payload = vias[0].data
    assert isinstance(payload, ParsedViaPayload)
    assert payload.stack is None
    assert any(issue.category == "unsupported_padstack" for issue in ctx.issues)


def test_altium_via_stack_mode_without_diameters_records_diagnostic() -> None:
    # A non-simple mode byte without the per-layer diameter array is an
    # undocumented layout: diagnostic + simple stack.
    ctx = ParseContext()
    body = _make_via_stream(mode=1, diameters=(600000,) * 32)[5:][:100]
    data = _make_via_stream(body)
    vias = parse_vias(data, _stack_layer_map(), ctx)

    assert len(vias) == 1
    payload = vias[0].data
    assert isinstance(payload, ParsedViaPayload)
    assert payload.stack is None
    assert any(issue.category == "unsupported_padstack" for issue in ctx.issues)


def test_altium_pi_mx8_padstacks_all_simple(board: Board) -> None:
    # pi-mx8 declares padmode/viamode 0 on every record; the parsed stacks
    # must stay SIMPLE so outer-layer scalars are unchanged.
    assert all(pad.stack.mode is PadStackMode.SIMPLE for pad in board.pads)
    assert all(via.stack.mode is PadStackMode.SIMPLE for via in board.vias)


def test_altium_build_carries_pad_and_via_stacks() -> None:
    # The domain build must adopt the parsed stack (not re-wrap SIMPLE).
    layer_map = _stack_layer_map()
    layer_map[57] = make_pcb_layer("Outline", LayerRole.EDGE, number=57)
    ctx = ParseContext()
    pad_data = (FIXTURES / "altium" / "pad_stack_top_mid_bottom.bin").read_bytes()
    pad_primitives = [prim for _, prim in parse_pads(pad_data, _STACK_NETS, layer_map, ctx)]
    via_diameters = (600000,) + (500000,) * 30 + (700000,)
    via_primitives = parse_vias(_make_via_stream(mode=1, diameters=via_diameters), layer_map, ctx)
    outline = ParsedPrimitive(
        id="outline:0",
        object_type=ParsedObjectKind.GRAPHIC,
        shape=ParsedShapeKind.LINE,
        roles=(ParsedRole.BOARD_OUTLINE,),
        data=PcbLine(0.0, 0.0, 10.0, 0.0, 0.1),
        layers=("Outline",),
    )

    board = build_pcb_from_parsed_primitives(
        name="stacks",
        layer_map=layer_map,
        nets=_STACK_NETS,
        footprints=[],
        pours=[],
        keepouts=[],
        primitives=[outline, *pad_primitives, *via_primitives],
        ctx=ctx,
    )

    assert len(board.pads) == 1
    pad = board.pads[0]
    assert pad.stack.mode is PadStackMode.TOP_MID_BOTTOM
    assert pad.width == pytest.approx(1.3, abs=1e-3)
    assert pad.height == pytest.approx(2.0, abs=1e-3)
    assert len(board.vias) == 1
    via = board.vias[0]
    assert via.stack.mode is PadStackMode.TOP_MID_BOTTOM
    assert via.diameter == pytest.approx(1.524, abs=1e-3)


# Outline-vertex props captured from ODrive v2 Inverter.PcbDoc Board6/Data —
# a 150mm x 100mm rectangle whose only outline source is Board6.
_ODRIVE_BOARD6_PROPS = {
    "originx": "1000mil",
    "originy": "1019.685mil",
    "kind0": "0",
    "vx0": "999.9999mil",
    "vy0": "1019.6849mil",
    "kind1": "0",
    "vx1": "6905.5115mil",
    "vy1": "1019.6848mil",
    "kind2": "0",
    "vx2": "6905.5111mil",
    "vy2": "4956.6927mil",
    "kind3": "0",
    "vx3": "999.9997mil",
    "vy3": "4956.6928mil",
    "kind4": "0",
    "vx4": "999.9999mil",
    "vy4": "1019.6849mil",
}


def test_board6_outline_vertices_synthesize_board_profile() -> None:
    ctx = ParseContext()
    layer_map = build_layer_map({}, ctx)
    outline = parse_board6_outline(_ODRIVE_BOARD6_PROPS, layer_map, ctx)

    assert len(outline) == 1
    assert outline[0].has_role(ParsedRole.BOARD_OUTLINE)
    polygon = outline[0].data
    assert isinstance(polygon, PcbPolygon)
    xs = [p[0] for p in polygon.points]
    ys = [p[1] for p in polygon.points]
    assert max(xs) - min(xs) == pytest.approx(150.0, abs=0.01)
    assert max(ys) - min(ys) == pytest.approx(100.0, abs=0.01)


def test_board6_outline_with_arc_vertices() -> None:
    # 100mil x 100mil square with a quarter-circle arc corner at the origin.
    props = {
        "kind0": "1",
        "vx0": "0mil",
        "vy0": "10mil",
        "cx0": "10mil",
        "cy0": "10mil",
        "r0": "10mil",
        "sa0": "90",
        "ea0": "180",
        "kind1": "0",
        "vx1": "100mil",
        "vy1": "0mil",
        "kind2": "0",
        "vx2": "100mil",
        "vy2": "100mil",
        "kind3": "0",
        "vx3": "0mil",
        "vy3": "100mil",
    }
    ctx = ParseContext()
    outline = parse_board6_outline(props, build_layer_map({}, ctx), ctx)

    assert len(outline) == 1
    polygon = outline[0].data
    assert isinstance(polygon, PcbPolygon)
    # The arc edge is linearized into multiple points.
    assert len(polygon.points) > 4


def test_board6_origin_stored_in_metadata(board: Board) -> None:
    assert "origin_x_mm" in board.metadata.properties
    assert "origin_y_mm" in board.metadata.properties
    assert float(board.metadata.properties["origin_x_mm"]) != 0.0
