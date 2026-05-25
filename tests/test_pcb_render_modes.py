"""Tests for derived-layer PCB render mode projections."""

from __future__ import annotations

from shapely import GeometryCollection, Point, Polygon

from phosphor_eda.pcb import PcbArc, PcbLine, PcbPad, PcbText, PcbVia, PcbZone
from phosphor_eda.pcb_render_artwork import geometry_to_artwork
from phosphor_eda.pcb_render_geometry import (
    GeometryKind,
    GeometryLayer,
    GeometryTags,
    PcbGeometryStore,
    RenderableGeometry,
)
from phosphor_eda.pcb_render_modes import build_cad_layers
from phosphor_eda.pcb_render_settings import (
    LayerMatch,
    LayerSelectionRule,
    RenderSettings,
    SourceSelection,
)
from phosphor_eda.pcb_render_tokens import ResolvedStyle


def test_cad_front_copper_artwork_collapses_to_one_union_layer() -> None:
    store = PcbGeometryStore(
        items=(
            _board_outline(),
            _renderable(
                "pad-1",
                GeometryKind.PAD,
                "F.Cu",
                "copper",
                "front",
                geometry=_pad(x=1.0, y=1.0, width=2.0, height=2.0),
            ),
            _renderable(
                "trace-1",
                GeometryKind.TRACE,
                "F.Cu",
                "copper",
                "front",
                geometry=Point(1.0, 1.0).buffer(1.0),
            ),
            _renderable(
                "zone-1",
                GeometryKind.ZONE,
                "F.Cu",
                "copper",
                "front",
                geometry=PcbZone(
                    net_number=1,
                    net_name="GND",
                    layer="F.Cu",
                    boundary=[(3.0, 1.0), (4.0, 1.0), (4.0, 2.0), (3.0, 2.0)],
                ),
            ),
            _renderable(
                "via-1",
                GeometryKind.VIA,
                "vias",
                "via",
                "",
                geometry=PcbVia(5.0, 1.0, 1.0, 0.4, ["F.Cu", "B.Cu"], 1),
            ),
            _renderable(
                "text-1",
                GeometryKind.USER_TEXT,
                "F.Cu",
                "copper",
                "front",
                geometry=PcbText("A", 6.0, 1.0, 0.0, "F.Cu", 1.0, kind="user"),
            ),
        )
    )

    layers = build_cad_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(name="F.Cu")),),
            tokens={
                "cad.copper.front.fill": "#d17a22",
                "cad.copper.front.opacity": 0.35,
            },
        ),
        warn=lambda _message: None,
    )

    assert len(layers) == 1
    layer = layers[0]
    assert layer.id == "cad:copper:front"
    assert layer.role.namespace == "cad"
    assert layer.role.function == "copper"
    assert layer.role.side == "front"
    assert layer.source_layers == ("F.Cu",)
    assert layer.source_ids == ("pad-1", "trace-1", "zone-1", "via-1", "text-1")
    assert layer.style == ResolvedStyle(fill="#d17a22", opacity=0.35)
    assert not isinstance(layer.geometry, GeometryCollection)
    additive_area = sum(
        artwork.geometry.area
        for item in store.items
        if item.kind is not GeometryKind.BOARD_OUTLINE
        for artwork in (geometry_to_artwork(item),)
        if artwork is not None
    )
    assert layer.geometry.area < additive_area


def test_cad_inner_copper_uses_indexed_roles_and_default_style_fallback() -> None:
    store = PcbGeometryStore(
        items=(
            _board_outline(),
            _renderable(
                "inner-trace",
                GeometryKind.TRACE,
                "In2.Cu",
                "copper",
                "inner",
                stack_index=2,
                geometry=Point(2.0, 2.0).buffer(1.0),
            ),
        )
    )

    layers = build_cad_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(name="In2.Cu")),),
            tokens={"cad.copper.inner.default.fill": "#7fc87f"},
        ),
        warn=lambda _message: None,
    )

    assert len(layers) == 1
    assert layers[0].id == "cad:copper:inner:2"
    assert layers[0].role.inner_index == 2
    assert layers[0].style == ResolvedStyle(fill="#7fc87f")


def test_cad_exact_native_layer_selection_builds_layer_with_native_token_style() -> None:
    store = PcbGeometryStore(
        items=(
            _board_outline(),
            _renderable(
                "mech-1",
                GeometryKind.MECHANICAL,
                "Mechanical 13",
                "mechanical",
                "",
                geometry=Polygon([(1.0, 1.0), (4.0, 1.0), (4.0, 2.0), (1.0, 2.0)]),
            ),
        )
    )

    layers = build_cad_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(name="Mechanical 13")),),
            tokens={"cad.layer[Mechanical 13].fill": "#55ccff"},
        ),
        warn=lambda _message: None,
    )

    assert len(layers) == 1
    assert layers[0].id == "cad:mechanical"
    assert layers[0].role.source_layer_name == "Mechanical 13"
    assert layers[0].source_layers == ("Mechanical 13",)
    assert layers[0].style == ResolvedStyle(fill="#55ccff")


def test_cad_native_layer_token_override_wins_over_semantic_copper_token() -> None:
    store = PcbGeometryStore(
        items=(
            _board_outline(),
            _renderable(
                "pad-1",
                GeometryKind.PAD,
                "F.Cu",
                "copper",
                "front",
                geometry=_pad(x=1.0, y=1.0),
            ),
        )
    )

    layers = build_cad_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(name="F.Cu")),),
            tokens={
                "cad.layer[F.Cu].fill": "#ff0000",
                "cad.copper.front.fill": "#d17a22",
            },
        ),
        warn=lambda _message: None,
    )

    assert layers[0].style == ResolvedStyle(fill="#ff0000")


def test_cad_via_only_copper_selection_builds_layer_for_selected_source_layer() -> None:
    store = PcbGeometryStore(
        items=(
            _board_outline(),
            _renderable(
                "via-1",
                GeometryKind.VIA,
                "vias",
                "via",
                "",
                geometry=PcbVia(2.0, 2.0, 1.0, 0.4, ["F.Cu", "B.Cu"], 1),
            ),
        )
    )

    layers = build_cad_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(name="F.Cu")),),
            tokens={"cad.copper.front.fill": "#d17a22"},
        ),
        warn=lambda _message: None,
    )

    assert len(layers) == 1
    assert layers[0].id == "cad:copper:front"
    assert layers[0].source_layers == ("F.Cu",)
    assert layers[0].source_ids == ("via-1",)


def test_cad_geometry_is_clipped_to_board_and_cut_by_drill_holes() -> None:
    store = PcbGeometryStore(
        items=(
            _board_outline(),
            _renderable(
                "pad-1",
                GeometryKind.PAD,
                "F.Cu",
                "copper",
                "front",
                geometry=_pad(x=0.0, y=0.0, width=4.0, height=4.0, drill=0.8),
            ),
            _renderable(
                "drill-1",
                GeometryKind.DRILL,
                "drills",
                "drill",
                "",
                geometry=_pad(x=0.0, y=0.0, width=4.0, height=4.0, drill=0.8),
            ),
        )
    )

    layers = build_cad_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(name="F.Cu")),),
            tokens={"cad.copper.front.fill": "#d17a22"},
        ),
        warn=lambda _message: None,
    )

    geometry = layers[0].geometry

    assert _board_polygon().covers(geometry)
    assert not geometry.contains(Point(0.0, 0.0))
    assert geometry.area < 4.0


def test_cad_board_outline_is_outline_only() -> None:
    store = PcbGeometryStore(items=(_board_outline(),))

    layers = build_cad_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(name="Edge.Cuts")),),
            tokens={
                "cad.edge.fill": "none",
                "cad.edge.stroke": "#444444",
                "cad.edge.strokeWidthMm": 0.1,
            },
        ),
        warn=lambda _message: None,
    )

    assert len(layers) == 1
    assert layers[0].role.function == "edge"
    assert layers[0].geometry.equals(_board_polygon().boundary)
    assert layers[0].style == ResolvedStyle(
        fill="none",
        stroke="#444444",
        stroke_width_mm=0.1,
    )


def _settings(
    *,
    rules: tuple[LayerSelectionRule, ...],
    tokens: dict[str, str | int | float | bool],
) -> RenderSettings:
    return RenderSettings(
        source=SourceSelection(layers=list(rules)),
        tokens=tokens,
    )


def _renderable(
    geometry_id: str,
    kind: GeometryKind,
    layer_name: str,
    layer_role: str,
    side: str,
    *,
    stack_index: int = 0,
    geometry: object | None = None,
) -> RenderableGeometry:
    return RenderableGeometry(
        id=geometry_id,
        kind=kind,
        layer=GeometryLayer(
            name=layer_name,
            role=layer_role,
            side=side,
            stack_index=stack_index,
        ),
        tags=GeometryTags(source_collection=kind.value),
        payload=Point(1, 1) if geometry is None else geometry,
        source=geometry,
    )


def _board_outline() -> RenderableGeometry:
    outline_arcs: list[PcbArc] = []
    return _renderable(
        "board-outline",
        GeometryKind.BOARD_OUTLINE,
        "Edge.Cuts",
        "edge",
        "",
        stack_index=-300,
        geometry=(
            [
                PcbLine(0.0, 0.0, 10.0, 0.0, "Edge.Cuts", 0.1),
                PcbLine(10.0, 0.0, 10.0, 5.0, "Edge.Cuts", 0.1),
                PcbLine(10.0, 5.0, 0.0, 5.0, "Edge.Cuts", 0.1),
                PcbLine(0.0, 5.0, 0.0, 0.0, "Edge.Cuts", 0.1),
            ],
            outline_arcs,
        ),
    )


def _board_polygon() -> Polygon:
    return Polygon([(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)])


def _pad(
    *,
    x: float,
    y: float,
    width: float = 1.0,
    height: float = 1.0,
    drill: float = 0.0,
) -> PcbPad:
    return PcbPad(
        number="1",
        x=x,
        y=y,
        width=width,
        height=height,
        shape="rect",
        layers=["F.Cu", "B.Cu"],
        net_number=1,
        net_name="GND",
        footprint_ref="J1",
        drill=drill,
    )
