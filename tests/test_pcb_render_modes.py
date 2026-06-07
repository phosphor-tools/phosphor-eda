from __future__ import annotations

from collections import Counter
from pathlib import Path

from test_pcb_render import _board

from phosphor_eda.altium.pcb_parser import parse_altium_pcb
from phosphor_eda.kicad.pcb_parser import parse_kicad_pcb
from phosphor_eda.pcb import (
    LayerRole,
    Pcb,
    PcbArcGeometry,
    PcbGeometry,
    PcbGeometryObject,
    PcbGeometryRole,
    PcbGeometryShape,
    PcbLayer,
    PcbLineGeometry,
)
from phosphor_eda.pcb_render_geometry import build_geometry_store
from phosphor_eda.pcb_render_modes import (
    build_eda_layers,
    build_highlight_layers,
    build_realistic_layers,
)
from phosphor_eda.pcb_render_settings import (
    HighlightSpec,
    LayerMatch,
    LayerSelectionRule,
    RenderSettings,
    SourceSelection,
    load_render_settings_json,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_eda_layers_are_built_from_normalized_geometry() -> None:
    store = build_geometry_store(_board(), side="front")
    settings = RenderSettings(
        render_mode="eda",
        side="front",
        source=SourceSelection(
            layers=[
                LayerSelectionRule(match=LayerMatch(role="copper")),
                LayerSelectionRule(
                    match=LayerMatch(role="silkscreen", side="front"),
                    objects=("graphic", "text"),
                ),
                LayerSelectionRule(match=LayerMatch(role="edge")),
            ]
        ),
    )

    layers = build_eda_layers(store, settings, warn=lambda _message: None)

    roles = {(layer.role.function, layer.role.side) for layer in layers}
    assert ("copper", "front") in roles
    assert ("copper", "back") in roles
    assert ("silkscreen", "front") in roles
    assert ("edge", "") in roles
    assert any(primitive.kind == "via" for layer in layers for primitive in layer.primitives)


def test_realistic_layers_use_board_material_mask_and_silkscreen() -> None:
    store = build_geometry_store(_board(), side="front")
    settings = load_render_settings_json('{"extends": "phosphor:review"}')
    settings.source = SourceSelection(
        layers=[
            LayerSelectionRule(match=LayerMatch(role="copper")),
            LayerSelectionRule(match=LayerMatch(role="solder_mask", side="front")),
            LayerSelectionRule(
                match=LayerMatch(role="silkscreen", side="front"),
                objects=("graphic", "text"),
            ),
        ]
    )
    settings.render_mode = "realistic"
    settings.side = "front"

    layers = build_realistic_layers(store, settings, warn=lambda _message: None)

    layer_ids = {layer.id for layer in layers}
    assert {
        "realistic:substrate",
        "realistic:solderMask",
        "realistic:coveredCopper",
        "realistic:silkscreen",
    }.issubset(layer_ids)


def test_realistic_board_material_uses_profile_path_for_orangecrab() -> None:
    board = parse_kicad_pcb(FIXTURES / "orangecrab.kicad_pcb")
    store = build_geometry_store(board, side="front")
    settings = load_render_settings_json('{"extends": "phosphor:review", "side": "front"}')

    layers = build_realistic_layers(store, settings, warn=lambda _message: None)
    substrate = next(layer for layer in layers if layer.id == "realistic:substrate")
    board_path = substrate.primitives[0].d
    min_x, min_y, max_x, max_y = board.bbox()
    bbox_path = (
        f"M {min_x:.4f} {min_y:.4f} L {max_x:.4f} {min_y:.4f} "
        f"L {max_x:.4f} {max_y:.4f} L {min_x:.4f} {max_y:.4f} Z"
    )

    assert board_path != bbox_path
    assert " A " in board_path


def test_realistic_altium_copper_graphics_are_not_displayed_as_mechanical() -> None:
    board = parse_altium_pcb(FIXTURES / "altium/pi-mx8/PCB/PiMX8MP_r0.3.PcbDoc")
    store = build_geometry_store(board, side="front")
    settings = load_render_settings_json('{"extends": "phosphor:review", "side": "front"}')

    layers = build_realistic_layers(store, settings, warn=lambda _message: None)
    copper_layer_ids = {"realistic:coveredCopper", "realistic:exposedCopper"}
    copper_layers = [layer for layer in layers if layer.id in copper_layer_ids]

    assert copper_layers
    for layer in copper_layers:
        kinds = Counter(primitive.kind for primitive in layer.primitives)
        assert kinds["mechanical"] == 0
        assert kinds["copper"] > 0 or kinds["pour"] > 0


def test_realistic_altium_board_material_uses_single_connected_profile_contour() -> None:
    board = parse_altium_pcb(FIXTURES / "altium/pi-mx8/PCB/PiMX8MP_r0.3.PcbDoc")
    store = build_geometry_store(board, side="front")
    settings = load_render_settings_json('{"extends": "phosphor:review", "side": "front"}')

    layers = build_realistic_layers(store, settings, warn=lambda _message: None)
    substrate = next(layer for layer in layers if layer.id == "realistic:substrate")
    board_path = substrate.primitives[0].d

    assert board_path.count("M ") == 1
    assert board_path.count("Z") == 1
    assert " Z M " not in board_path


def test_realistic_board_profile_orders_unordered_reversed_line_segments() -> None:
    board = _profile_board(
        [
            _edge_line("right", (10.0, 10.0), (10.0, 0.0)),
            _edge_line("bottom", (10.0, 10.0), (0.0, 10.0)),
            _edge_line("top", (0.0, 0.0), (10.0, 0.0)),
            _edge_line("left", (0.0, 10.0), (0.0, 0.0)),
        ]
    )

    board_path = _realistic_board_material_path(board)

    assert board_path.count("M ") == 1
    assert board_path.count("Z") == 1
    assert " Z M " not in board_path


def test_realistic_board_profile_orders_unordered_line_and_arc_segments() -> None:
    board = _profile_board(
        [
            _edge_line("right", (10.0, 7.0), (10.0, 3.0)),
            _edge_arc("bottom-left", (3.0, 10.0), (0.8787, 9.1213), (0.0, 7.0)),
            _edge_line("top", (7.0, 0.0), (3.0, 0.0)),
            _edge_arc("top-right", (7.0, 0.0), (9.1213, 0.8787), (10.0, 3.0)),
            _edge_line("left", (0.0, 3.0), (0.0, 7.0)),
            _edge_arc("bottom-right", (10.0, 7.0), (9.1213, 9.1213), (7.0, 10.0)),
            _edge_arc("top-left", (3.0, 0.0), (0.8787, 0.8787), (0.0, 3.0)),
            _edge_line("bottom", (3.0, 10.0), (7.0, 10.0)),
        ]
    )

    board_path = _realistic_board_material_path(board)

    assert board_path.count("M ") == 1
    assert board_path.count("Z") == 1
    assert board_path.count(" A ") == 4
    assert " Z M " not in board_path


def test_realistic_board_profile_tolerates_native_endpoint_rounding() -> None:
    board = _profile_board(
        [
            _edge_line("top", (0.0, 0.0), (10.0, 0.0)),
            _edge_line("right", (10.000005, 0.0), (10.0, 10.0)),
            _edge_line("bottom", (10.0, 10.0), (0.0, 10.0)),
            _edge_line("left", (0.0, 10.0), (0.0, 0.0)),
        ]
    )

    board_path = _realistic_board_material_path(board)

    assert board_path.count("M ") == 1
    assert board_path.count("Z") == 1
    assert " Z M " not in board_path


def test_realistic_board_profile_does_not_close_open_fragments() -> None:
    board = _profile_board(
        [
            _edge_line("top", (0.0, 0.0), (10.0, 0.0)),
            _edge_line("diagonal", (10.0, 0.0), (5.0, 8.0)),
        ]
    )

    board_path = _realistic_board_material_path(board)

    assert board_path == _bbox_path(board)


def test_highlights_match_normalized_geometry_tags() -> None:
    store = build_geometry_store(_board(), side="front")
    settings = load_render_settings_json('{"extends": "phosphor:review"}')
    settings.render_mode = "eda"
    settings.side = "front"
    settings.source = SourceSelection(layers=[LayerSelectionRule(match=LayerMatch(role="copper"))])
    settings.highlights = [HighlightSpec(net="VCC")]

    groups = build_highlight_layers(store, settings, warn=lambda _message: None)

    assert len(groups) == 1
    assert groups[0].target == "net:VCC"
    assert any(layer.primitives for layer in groups[0].layers)


def _profile_board(geometry: list[PcbGeometry]) -> Pcb:
    return Pcb(
        name="profile-test",
        nets={},
        footprints=[],
        pours=[],
        keepouts=[],
        geometry=geometry,
        layers=[PcbLayer("Edge.Cuts", (LayerRole.EDGE,))],
    )


def _edge_line(
    item_id: str,
    start: tuple[float, float],
    end: tuple[float, float],
) -> PcbGeometry:
    return PcbGeometry(
        id=f"edge:{item_id}",
        object_type=PcbGeometryObject.GRAPHIC,
        shape=PcbGeometryShape.LINE,
        roles=(
            PcbGeometryRole.EDGE,
            PcbGeometryRole.BOARD_OUTLINE,
            PcbGeometryRole.BOARD_LEVEL,
        ),
        data=PcbLineGeometry(start[0], start[1], end[0], end[1], 0.1),
        layers=("Edge.Cuts",),
    )


def _edge_arc(
    item_id: str,
    start: tuple[float, float],
    mid: tuple[float, float],
    end: tuple[float, float],
) -> PcbGeometry:
    return PcbGeometry(
        id=f"edge:{item_id}",
        object_type=PcbGeometryObject.GRAPHIC,
        shape=PcbGeometryShape.ARC,
        roles=(
            PcbGeometryRole.EDGE,
            PcbGeometryRole.BOARD_OUTLINE,
            PcbGeometryRole.BOARD_LEVEL,
        ),
        data=PcbArcGeometry(start[0], start[1], mid[0], mid[1], end[0], end[1], 0.1),
        layers=("Edge.Cuts",),
    )


def _realistic_board_material_path(board: Pcb) -> str:
    store = build_geometry_store(board, side="front")
    settings = load_render_settings_json('{"extends": "phosphor:review", "side": "front"}')
    layers = build_realistic_layers(store, settings, warn=lambda _message: None)
    substrate = next(layer for layer in layers if layer.id == "realistic:substrate")
    return substrate.primitives[0].d


def _bbox_path(board: Pcb) -> str:
    min_x, min_y, max_x, max_y = board.bbox()
    return (
        f"M {min_x:.4f} {min_y:.4f} L {max_x:.4f} {min_y:.4f} "
        f"L {max_x:.4f} {max_y:.4f} L {min_x:.4f} {max_y:.4f} Z"
    )
