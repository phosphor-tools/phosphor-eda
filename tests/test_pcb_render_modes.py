"""Tests for derived-layer PCB render mode projections."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from shapely import GeometryCollection, Point, Polygon

import phosphor_eda.pcb_render_modes as render_modes
from phosphor_eda.kicad.pcb_parser import parse_kicad_pcb
from phosphor_eda.pcb import PcbArc, PcbLine, PcbPad, PcbSegment, PcbText, PcbVia, PcbZone
from phosphor_eda.pcb_render_artwork import DerivedLayer, geometry_to_artwork
from phosphor_eda.pcb_render_geometry import (
    GeometryKind,
    GeometryLayer,
    GeometryTags,
    PcbGeometryStore,
    RenderableGeometry,
    build_geometry_store,
)
from phosphor_eda.pcb_render_modes import (
    build_cad_layers,
    build_highlight_layers,
    build_realistic_layers,
)
from phosphor_eda.pcb_render_profile import RenderProfiler
from phosphor_eda.pcb_render_settings import (
    DimmingSettings,
    HighlightSpec,
    LayerMatch,
    LayerSelectionRule,
    RenderSettings,
    SourceSelection,
)
from phosphor_eda.pcb_render_skia import geometry_to_skia_artwork
from phosphor_eda.pcb_render_tokens import ResolvedStyle

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    import pytest
    from shapely.geometry.base import BaseGeometry

    from phosphor_eda.pcb_render_skia import SkiaArtwork


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


def test_cad_copper_layer_uses_skia_path_data_for_pad_and_via() -> None:
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
            _renderable(
                "via-1",
                GeometryKind.VIA,
                "vias",
                "via",
                "",
                geometry=PcbVia(2.0, 1.0, 0.8, 0.3, ["F.Cu", "B.Cu"], 1),
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
    layer = layers[0]
    assert layer.id == "cad:copper:front"
    assert layer.source_ids == ("pad-1", "via-1")
    assert layer.path_data.startswith("M ")
    assert not isinstance(layer.geometry, GeometryCollection)


def test_cad_copper_skia_fallback_warns_and_reports_unsupported_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            _renderable(
                "via-1",
                GeometryKind.VIA,
                "vias",
                "via",
                "",
                geometry=PcbVia(2.0, 1.0, 0.8, 0.3, ["F.Cu"], 1),
            ),
        )
    )
    _force_skia_failure_for_ids(monkeypatch, ("via-1",))
    profiler = RenderProfiler()
    warnings: list[str] = []

    layers = build_cad_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(name="F.Cu")),),
            tokens={"cad.copper.front.fill": "#d17a22"},
        ),
        warn=warnings.append,
        profiler=profiler,
    )

    assert len(layers) == 1
    layer = layers[0]
    assert layer.id == "cad:copper:front"
    assert layer.source_ids == ("pad-1", "via-1")
    assert not layer.geometry.is_empty
    assert layer.path_data == ""
    assert len(warnings) == 1
    assert "CAD copper" in warnings[0]
    assert "F.Cu" in warnings[0]
    assert "1 unsupported" in warnings[0]
    _assert_skia_fallback_profile(
        profiler,
        "cad.skia.fallback",
        converted_items=1,
        unsupported_items=1,
    )


def test_cad_copper_skia_union_failure_warns_and_uses_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    def fail_union(_artwork: object) -> object:
        raise RuntimeError("pathops failed")

    monkeypatch.setattr(render_modes, "union_skia_artwork", fail_union)
    profiler = RenderProfiler()
    warnings: list[str] = []

    layers = build_cad_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(name="F.Cu")),),
            tokens={"cad.copper.front.fill": "#d17a22"},
        ),
        warn=warnings.append,
        profiler=profiler,
    )

    assert len(layers) == 1
    layer = layers[0]
    assert layer.id == "cad:copper:front"
    assert layer.source_ids == ("pad-1",)
    assert not layer.geometry.is_empty
    assert layer.path_data == ""
    assert len(warnings) == 1
    assert "CAD copper" in warnings[0]
    assert "Skia union failed with RuntimeError" in warnings[0]
    _assert_skia_fallback_profile(
        profiler,
        "cad.skia.fallback",
        converted_items=1,
        unsupported_items=0,
    )


def test_realistic_covered_copper_skia_fallback_warns_and_uses_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            _renderable(
                "via-1",
                GeometryKind.VIA,
                "vias",
                "via",
                "",
                geometry=PcbVia(2.0, 1.0, 0.8, 0.3, ["F.Cu"], 1),
            ),
        )
    )
    _force_skia_failure_for_ids(monkeypatch, ("via-1",))
    profiler = RenderProfiler()
    warnings: list[str] = []

    layers = build_realistic_layers(
        store,
        _settings(
            side="front",
            rules=(LayerSelectionRule(match=LayerMatch(function="copper")),),
            tokens=_realistic_tokens(),
        ),
        warn=warnings.append,
        profiler=profiler,
    )

    covered_copper = next(layer for layer in layers if layer.id == "realistic:coveredCopper")
    assert covered_copper.source_ids == ("pad-1", "via-1")
    assert not covered_copper.geometry.is_empty
    assert covered_copper.path_data == ""
    assert len(warnings) == 1
    assert "realistic covered copper" in warnings[0]
    assert "F.Cu" in warnings[0]
    assert "1 unsupported" in warnings[0]
    _assert_skia_fallback_profile(
        profiler,
        "realistic.covered_copper.skia.fallback",
        converted_items=1,
        unsupported_items=1,
    )


def test_highlight_copper_skia_fallback_warns_and_uses_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PcbGeometryStore(
        items=(
            _board_outline(),
            _renderable(
                "pad-1",
                GeometryKind.PAD,
                "F.Cu",
                "copper",
                "front",
                geometry=_pad(x=1.0, y=1.0, net_name="SIG"),
                tags=GeometryTags(source_collection="pads", net_name="SIG"),
            ),
            _renderable(
                "via-1",
                GeometryKind.VIA,
                "vias",
                "via",
                "",
                geometry=PcbVia(2.0, 1.0, 0.8, 0.3, ["F.Cu"], 1),
                tags=GeometryTags(source_collection="vias", net_name="SIG"),
            ),
        )
    )
    _force_skia_failure_for_ids(monkeypatch, ("via-1",))
    profiler = RenderProfiler()
    warnings: list[str] = []

    groups = build_highlight_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(name="F.Cu")),),
            tokens={"highlight.copper.front.fill": "#ff8a00"},
            highlights=(HighlightSpec(net="SIG"),),
        ),
        warn=warnings.append,
        profiler=profiler,
    )

    layer = groups[0].layers[0]
    assert layer.id == "highlight:copper:front"
    assert layer.source_ids == ("pad-1", "via-1")
    assert not layer.geometry.is_empty
    assert layer.path_data == ""
    assert len(warnings) == 1
    assert "highlight copper" in warnings[0]
    assert "F.Cu" in warnings[0]
    assert "1 unsupported" in warnings[0]
    _assert_skia_fallback_profile(
        profiler,
        "highlight.skia.fallback",
        converted_items=1,
        unsupported_items=1,
    )


def test_cad_geometry_keeps_unclipped_artwork_and_tracks_layer_clip() -> None:
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

    assert not _board_polygon().covers(geometry)
    assert geometry.contains(Point(0.0, 0.0))
    assert geometry.area == 16.0
    assert layers[0].clip is not None
    assert layers[0].clip.board.equals(_board_polygon())
    assert layers[0].clip.drills.contains(Point(0.0, 0.0))


def test_cad_drill_clipping_handles_kicad_symbol_layer_names() -> None:
    fixture = Path(__file__).parent / "fixtures" / "orangecrab.kicad_pcb"
    board = parse_kicad_pcb(fixture)
    store = build_geometry_store(board, side="front")

    layers = build_cad_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(name="F.Cu")),),
            tokens={"cad.copper.front.fill": "#d17a22"},
        ),
        warn=lambda _message: None,
    )

    front_copper = next(
        layer for layer in layers if layer.role.function == "copper" and layer.role.side == "front"
    )

    assert front_copper.geometry.covers(Point(137.05, 111.84))
    assert front_copper.geometry.covers(Point(137.05, 94.06))
    assert front_copper.clip is not None
    assert not front_copper.clip.drills.is_empty
    assert all(layer.role.inner_index != 5000 for layer in layers)


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


def test_realistic_front_layers_project_physical_stack_in_order() -> None:
    mask_opening = Polygon([(0.25, 0.25), (1.75, 0.25), (1.75, 1.75), (0.25, 1.75)])
    bare_substrate_opening = Polygon([(3.0, 0.5), (4.0, 0.5), (4.0, 1.5), (3.0, 1.5)])
    copper = _pad(x=1.0, y=1.0, width=1.0, height=1.0)
    silk = Polygon([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)])
    drill_pad = _pad(x=1.0, y=1.0, width=1.0, height=1.0, drill=0.6)
    store = PcbGeometryStore(
        items=(
            _board_outline(),
            _renderable(
                "mask-copper-opening",
                GeometryKind.MASK,
                "F.Mask",
                "mask",
                "front",
                geometry=mask_opening,
            ),
            _renderable(
                "mask-substrate-opening",
                GeometryKind.MASK,
                "F.Mask",
                "mask",
                "front",
                geometry=bare_substrate_opening,
            ),
            _renderable(
                "copper-pad",
                GeometryKind.PAD,
                "F.Cu",
                "copper",
                "front",
                geometry=copper,
            ),
            _renderable(
                "silk-fill",
                GeometryKind.SILK_POLYGON,
                "F.SilkS",
                "silkscreen",
                "front",
                geometry=silk,
            ),
            _renderable(
                "drill-1",
                GeometryKind.DRILL,
                "drills",
                "drill",
                "",
                geometry=drill_pad,
            ),
        )
    )

    layers = build_realistic_layers(
        store,
        _settings(
            side="front",
            rules=(
                LayerSelectionRule(match=LayerMatch(function="solder_mask", side="front")),
                LayerSelectionRule(match=LayerMatch(function="copper", side="front")),
                LayerSelectionRule(match=LayerMatch(function="silkscreen", side="front")),
            ),
            tokens=_realistic_tokens(),
        ),
        warn=lambda _message: None,
    )

    assert [layer.id for layer in layers] == [
        "realistic:substrate",
        "realistic:solderMask",
        "realistic:coveredCopper",
        "realistic:exposedCopper",
        "realistic:silkscreen",
        "realistic:boardOutline",
    ]
    assert [layer.role.function for layer in layers] == [
        "substrate",
        "solderMask",
        "coveredCopper",
        "exposedCopper",
        "silkscreen",
        "boardOutline",
    ]
    assert [_require_style(layer).fill for layer in layers] == [
        "#2d2118",
        "#194d2e",
        "#6d4b1f",
        "#d6a13d",
        "#ffffff",
        "none",
    ]

    by_id = {layer.id: layer for layer in layers}
    substrate = by_id["realistic:substrate"].geometry
    solder_mask = by_id["realistic:solderMask"].geometry
    covered_copper = by_id["realistic:coveredCopper"].geometry
    exposed_copper = by_id["realistic:exposedCopper"].geometry
    silkscreen = by_id["realistic:silkscreen"].geometry
    board_outline = by_id["realistic:boardOutline"].geometry

    assert _board_polygon().covers(substrate)
    drill_center = Point(1.0, 1.0)
    assert substrate.contains(drill_center)
    assert not solder_mask.contains(drill_center)
    assert by_id["realistic:substrate"].clip is not None
    assert by_id["realistic:substrate"].clip.drills.contains(drill_center)
    assert solder_mask.intersection(mask_opening).area == 0.0
    assert solder_mask.intersection(bare_substrate_opening).area == 0.0
    copper_artwork = geometry_to_artwork(_require_renderable(store, "copper-pad"))
    assert copper_artwork is not None
    assert exposed_copper.area == copper_artwork.geometry.area
    assert covered_copper.area == copper_artwork.geometry.area
    assert exposed_copper.contains(drill_center)
    assert covered_copper.contains(drill_center)
    assert silkscreen.intersection(mask_opening).area == 0.0
    assert not silkscreen.contains(drill_center)
    assert board_outline.equals(_board_polygon().boundary)

    assert by_id["realistic:solderMask"].source_layers == ("F.Mask",)
    assert by_id["realistic:coveredCopper"].source_ids == ("copper-pad",)
    assert by_id["realistic:coveredCopper"].path_data.startswith("M ")
    assert by_id["realistic:exposedCopper"].source_ids == (
        "mask-copper-opening",
        "mask-substrate-opening",
        "copper-pad",
    )
    assert by_id["realistic:exposedCopper"].path_data == ""


def test_realistic_projection_uses_visible_side_only() -> None:
    store = PcbGeometryStore(
        items=(
            _board_outline(),
            _renderable(
                "front-copper",
                GeometryKind.PAD,
                "F.Cu",
                "copper",
                "front",
                geometry=_pad(x=1.0, y=1.0),
            ),
            _renderable(
                "back-copper",
                GeometryKind.PAD,
                "B.Cu",
                "copper",
                "back",
                geometry=_pad(x=3.0, y=1.0),
            ),
        )
    )

    layers = build_realistic_layers(
        store,
        _settings(
            side="front",
            rules=(LayerSelectionRule(match=LayerMatch(function="copper")),),
            tokens=_realistic_tokens(),
        ),
        warn=lambda _message: None,
    )

    covered_copper = next(layer for layer in layers if layer.id == "realistic:coveredCopper")
    assert covered_copper.source_layers == ("F.Cu",)
    assert covered_copper.source_ids == ("front-copper",)
    assert covered_copper.geometry.contains(Point(1.0, 1.0))
    assert not covered_copper.geometry.contains(Point(3.0, 1.0))


def test_highlight_projection_creates_one_group_per_request_with_layers() -> None:
    store = PcbGeometryStore(
        items=(
            _board_outline(),
            _renderable(
                "front-pad",
                GeometryKind.PAD,
                "F.Cu",
                "copper",
                "front",
                geometry=_pad(x=1.0, y=1.0, net_name="SIG", footprint_ref="J1"),
                tags=GeometryTags(
                    source_collection="pads",
                    component_ref="J1",
                    pad_number="1",
                    net_name="SIG",
                ),
            ),
            _renderable(
                "back-pad",
                GeometryKind.PAD,
                "B.Cu",
                "copper",
                "back",
                geometry=_pad(x=3.0, y=1.0, net_name="SIG", footprint_ref="J2"),
                tags=GeometryTags(
                    source_collection="pads",
                    component_ref="J2",
                    pad_number="1",
                    net_name="SIG",
                ),
            ),
            _renderable(
                "component-pad",
                GeometryKind.PAD,
                "F.Cu",
                "copper",
                "front",
                geometry=_pad(x=5.0, y=1.0, net_name="GND", footprint_ref="U1"),
                tags=GeometryTags(
                    source_collection="pads",
                    component_ref="U1",
                    pad_number="1",
                    net_name="GND",
                ),
            ),
        )
    )

    groups = build_highlight_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(function="copper")),),
            tokens={
                "highlight.copper.front.fill": "#ff8a00",
                "highlight.copper.back.fill": "#0095ff",
            },
            highlights=(
                HighlightSpec(net="SIG"),
                HighlightSpec(component="U1", color="#ff3b30"),
            ),
        ),
        warn=lambda _message: None,
    )

    assert len(groups) == 2
    assert groups[0].target == "net:SIG"
    assert [layer.id for layer in groups[0].layers] == [
        "highlight:copper:back",
        "highlight:copper:front",
    ]
    assert [layer.source_ids for layer in groups[0].layers] == [("back-pad",), ("front-pad",)]
    assert [layer.style.fill for layer in groups[0].layers if layer.style is not None] == [
        "#0095ff",
        "#ff8a00",
    ]
    assert groups[1].target == "component:U1"
    assert len(groups[1].layers) == 1
    assert groups[1].layers[0].style == ResolvedStyle(fill="#ff3b30")
    assert groups[1].layers[0].data["data-highlight-target"] == "component:U1"


def test_highlight_projection_supports_pad_targets_stroke_tokens_and_drill_clipping() -> None:
    store = PcbGeometryStore(
        items=(
            _board_outline(),
            _renderable(
                "pad-1",
                GeometryKind.PAD,
                "F.Cu",
                "copper",
                "front",
                geometry=_pad(
                    x=0.0,
                    y=0.0,
                    width=4.0,
                    height=4.0,
                    drill=0.8,
                    footprint_ref="J1",
                    net_name="SIG",
                ),
                tags=GeometryTags(
                    source_collection="pads",
                    component_ref="J1",
                    pad_number="1",
                    net_name="SIG",
                ),
            ),
            _renderable(
                "drill-1",
                GeometryKind.DRILL,
                "drills",
                "drill",
                "",
                geometry=_pad(
                    x=0.0,
                    y=0.0,
                    width=4.0,
                    height=4.0,
                    drill=0.8,
                    footprint_ref="J1",
                    net_name="SIG",
                ),
            ),
        )
    )

    groups = build_highlight_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(function="copper")),),
            tokens={
                "highlight.copper.front.fill": "#ff8a00",
                "highlight.copper.front.opacity": 0.85,
                "highlight.copper.front.stroke": "none",
                "highlight.copper.front.strokeWidthMm": 0,
            },
            highlights=(HighlightSpec(pad="J1.1"),),
        ),
        warn=lambda _message: None,
    )

    layer = groups[0].layers[0]

    assert groups[0].target == "pad:J1.1"
    assert layer.style == ResolvedStyle(
        fill="#ff8a00",
        stroke="none",
        opacity=0.85,
        stroke_width_mm=0.0,
    )
    assert not _board_polygon().covers(layer.geometry)
    assert layer.geometry.contains(Point(0.0, 0.0))
    assert layer.geometry.area == 16.0
    assert layer.clip is not None
    assert layer.clip.drills.contains(Point(0.0, 0.0))


def test_highlight_copper_layer_uses_skia_path_data_for_pad_and_via() -> None:
    store = PcbGeometryStore(
        items=(
            _board_outline(),
            _renderable(
                "pad-1",
                GeometryKind.PAD,
                "F.Cu",
                "copper",
                "front",
                geometry=_pad(x=1.0, y=1.0, footprint_ref="J1", net_name="SIG"),
                tags=GeometryTags(
                    source_collection="pads",
                    component_ref="J1",
                    pad_number="1",
                    net_name="SIG",
                ),
            ),
            _renderable(
                "via-1",
                GeometryKind.VIA,
                "vias",
                "via",
                "",
                geometry=PcbVia(2.0, 1.0, 0.8, 0.3, ["F.Cu"], 1),
                tags=GeometryTags(source_collection="vias", net_number=1, net_name="SIG"),
            ),
        )
    )

    groups = build_highlight_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(name="F.Cu")),),
            tokens={"highlight.copper.front.fill": "#ff8a00"},
            highlights=(HighlightSpec(net="SIG"),),
        ),
        warn=lambda _message: None,
    )

    layer = groups[0].layers[0]

    assert groups[0].target == "net:SIG"
    assert layer.id == "highlight:copper:front"
    assert layer.source_layers == ("F.Cu",)
    assert layer.source_ids == ("pad-1", "via-1")
    assert layer.path_data.startswith("M ")
    assert not isinstance(layer.geometry, GeometryCollection)


def test_base_layers_dim_only_when_dimming_enabled_and_highlights_exist() -> None:
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
                tags=GeometryTags(source_collection="pads", net_name="SIG"),
            ),
        )
    )
    tokens: dict[str, str | int | float | bool] = {
        "cad.copper.front.fill": "#d17a22",
        "cad.dimmed.copper.front.fill": "#6f5b48",
    }

    without_highlights = build_cad_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(name="F.Cu")),),
            tokens=tokens,
            dimming_enabled=True,
        ),
        warn=lambda _message: None,
    )
    with_highlights = build_cad_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(name="F.Cu")),),
            tokens=tokens,
            dimming_enabled=True,
            highlights=(HighlightSpec(net="SIG"),),
        ),
        warn=lambda _message: None,
    )

    assert without_highlights[0].style == ResolvedStyle(fill="#d17a22")
    assert with_highlights[0].style == ResolvedStyle(fill="#6f5b48")


def test_render_mode_union_can_prefer_disjoint_subset_union(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    def fake_robust_union(
        geometries: Iterable[BaseGeometry],
        *,
        prefer_disjoint_subsets: bool = False,
    ) -> BaseGeometry:
        calls.append(prefer_disjoint_subsets)
        return GeometryCollection(tuple(geometries))

    monkeypatch.setattr(render_modes, "robust_union", fake_robust_union)

    union_or_empty = cast(
        "Callable[..., BaseGeometry]",
        render_modes.__dict__["_union_or_empty"],
    )

    _ = union_or_empty(
        (Point(1, 1), Point(2, 2)),
        prefer_disjoint_subsets=True,
    )

    assert calls == [True]


def test_cad_layer_processing_keeps_union_strategy_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            _renderable(
                "pad-2",
                GeometryKind.PAD,
                "F.Cu",
                "copper",
                "front",
                geometry=_pad(x=3.0, y=1.0),
            ),
        )
    )
    calls: list[bool] = []

    def fake_robust_union(
        geometries: tuple[object, ...],
        *,
        prefer_disjoint_subsets: bool = False,
    ) -> Polygon:
        calls.append(prefer_disjoint_subsets)
        assert len(geometries) == 2
        return Polygon([(0.5, 0.5), (3.5, 0.5), (3.5, 1.5), (0.5, 1.5)])

    monkeypatch.setattr(render_modes, "robust_union", fake_robust_union)

    _ = build_cad_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(name="F.Cu")),),
            tokens={"cad.copper.front.fill": "#d17a22"},
        ),
        warn=lambda _message: None,
    )

    assert calls[0] is False


def test_realistic_layer_processing_prefers_disjoint_subset_union(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            _renderable(
                "pad-2",
                GeometryKind.PAD,
                "F.Cu",
                "copper",
                "front",
                geometry=_pad(x=3.0, y=1.0),
            ),
        )
    )
    calls: list[bool] = []

    def fake_robust_union(
        geometries: tuple[object, ...],
        *,
        prefer_disjoint_subsets: bool = False,
    ) -> Polygon:
        calls.append(prefer_disjoint_subsets)
        assert len(geometries) == 2
        return Polygon([(0.5, 0.5), (3.5, 0.5), (3.5, 1.5), (0.5, 1.5)])

    monkeypatch.setattr(render_modes, "robust_union", fake_robust_union)

    _ = build_realistic_layers(
        store,
        _settings(
            side="front",
            rules=(LayerSelectionRule(match=LayerMatch(function="copper", side="front")),),
            tokens=_realistic_tokens(),
        ),
        warn=lambda _message: None,
    )

    assert calls[0] is True


def test_layer_processing_profiles_input_and_output_geometry_complexity() -> None:
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
            _renderable(
                "pad-2",
                GeometryKind.PAD,
                "F.Cu",
                "copper",
                "front",
                geometry=_pad(x=3.0, y=1.0),
            ),
        )
    )
    profiler = RenderProfiler()

    _ = build_cad_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(name="F.Cu")),),
            tokens={"cad.copper.front.fill": "#d17a22"},
        ),
        warn=lambda _message: None,
        profiler=profiler,
    )

    events = cast("list[dict[str, object]]", profiler.to_dict()["events"])
    input_event = next(
        event for event in events if event["name"] == "cad.resolve_group.input_geometry"
    )
    output_event = next(
        event for event in events if event["name"] == "cad.resolve_group.output_geometry"
    )
    input_data = cast("dict[str, object]", input_event["data"])
    output_data = cast("dict[str, object]", output_event["data"])

    assert input_data["geometries"] == 2
    assert input_data["coordinates"] == 10
    assert isinstance(output_data["geometries"], int)
    assert isinstance(output_data["coordinates"], int)
    assert output_data["geometries"] > 0
    assert output_data["coordinates"] > 0


def test_profiler_reports_converted_artwork_complexity_by_source_kind() -> None:
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
            _renderable(
                "trace-1",
                GeometryKind.TRACE,
                "F.Cu",
                "copper",
                "front",
                geometry=PcbSegment(1.0, 1.0, 4.0, 1.0, 0.2, "F.Cu", 1),
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
                geometry=PcbVia(5.0, 1.0, 0.6, 0.3, ["F.Cu"], 1),
                tags=GeometryTags(source_collection="vias", net_number=1, net_name="GND"),
            ),
        )
    )
    profiler = RenderProfiler()

    _ = build_cad_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(name="F.Cu")),),
            tokens={"cad.copper.front.fill": "#d17a22"},
        ),
        warn=lambda _message: None,
        profiler=profiler,
    )

    profile_events = cast("list[dict[str, object]]", profiler.to_dict()["events"])
    events: list[dict[str, object]] = []
    by_kind: dict[str, dict[str, object]] = {}
    for event in profile_events:
        if event["name"] != "artwork.converted_by_kind":
            continue
        data = cast("dict[str, object]", event["data"])
        kind = data["kind"]
        assert isinstance(kind, str)
        events.append(event)
        by_kind[kind] = data

    assert {"pad", "trace", "zone", "via"}.issubset(by_kind)
    assert len(events) == 4
    assert by_kind["pad"]["sourceItems"] == 1
    assert by_kind["trace"]["sourceItems"] == 1
    assert isinstance(by_kind["zone"]["coordinates"], int)
    assert by_kind["zone"]["coordinates"] > 0
    assert by_kind["via"]["layer"] == "F.Cu"


def test_profiler_reports_skia_path_metrics_for_cad_copper() -> None:
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
            _renderable(
                "via-1",
                GeometryKind.VIA,
                "vias",
                "via",
                "",
                geometry=PcbVia(2.0, 1.0, 0.8, 0.3, ["F.Cu"], 1),
            ),
        )
    )
    profiler = RenderProfiler()

    _ = build_cad_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(name="F.Cu")),),
            tokens={"cad.copper.front.fill": "#d17a22"},
        ),
        warn=lambda _message: None,
        profiler=profiler,
    )

    profile_events = cast("list[dict[str, object]]", profiler.to_dict()["events"])
    by_name = {event["name"]: event for event in profile_events}
    input_data = cast("dict[str, object]", by_name["cad.skia.input"]["data"])
    output_data = cast("dict[str, object]", by_name["cad.skia.output"]["data"])

    assert "cad.skia.union" in by_name
    assert input_data["sourceItems"] == 2
    assert isinstance(output_data["pathCharacters"], int)
    assert isinstance(output_data["moveCommands"], int)
    assert output_data["pathCharacters"] > 0
    assert output_data["moveCommands"] > 0


def test_highlight_layers_reuse_surface_drill_clip_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PcbGeometryStore(
        items=(
            _board_outline(),
            _renderable(
                "pad-1",
                GeometryKind.PAD,
                "F.Cu",
                "copper",
                "front",
                geometry=_pad(x=1.0, y=1.0, net_name="SIG1"),
                tags=GeometryTags(source_collection="pads", net_name="SIG1"),
            ),
            _renderable(
                "pad-2",
                GeometryKind.PAD,
                "F.Cu",
                "copper",
                "front",
                geometry=_pad(x=3.0, y=1.0, net_name="SIG2"),
                tags=GeometryTags(source_collection="pads", net_name="SIG2"),
            ),
        )
    )
    requested_layers: list[str | None] = []

    def fake_drill_geometry_for_layer(
        _store: PcbGeometryStore,
        *,
        layer_name: str | None = None,
    ) -> GeometryCollection:
        requested_layers.append(layer_name)
        return GeometryCollection()

    monkeypatch.setattr(
        render_modes,
        "drill_geometry_for_layer",
        fake_drill_geometry_for_layer,
    )

    _ = build_highlight_layers(
        store,
        _settings(
            rules=(LayerSelectionRule(match=LayerMatch(name="F.Cu")),),
            tokens={
                "highlight.copper.front.fill": "#ff0000",
            },
            highlights=(HighlightSpec(net="SIG1"), HighlightSpec(net="SIG2")),
        ),
        warn=lambda _message: None,
    )

    assert requested_layers == [None]


def _settings(
    *,
    rules: tuple[LayerSelectionRule, ...],
    tokens: dict[str, str | int | float | bool],
    side: str = "",
    highlights: tuple[HighlightSpec, ...] = (),
    dimming_enabled: bool = False,
) -> RenderSettings:
    return RenderSettings(
        side=side,
        source=SourceSelection(layers=list(rules)),
        tokens=tokens,
        dimming=DimmingSettings(enabled=dimming_enabled),
        highlights=list(highlights),
    )


def _realistic_tokens() -> dict[str, str | int | float | bool]:
    return {
        "realistic.substrate.fill": "#2d2118",
        "realistic.solderMask.fill": "#194d2e",
        "realistic.coveredCopper.fill": "#6d4b1f",
        "realistic.exposedCopper.fill": "#d6a13d",
        "realistic.silkscreen.fill": "#ffffff",
        "realistic.boardOutline.fill": "none",
        "realistic.boardOutline.stroke": "#111111",
        "realistic.boardOutline.strokeWidthMm": 0.08,
    }


def _renderable(
    geometry_id: str,
    kind: GeometryKind,
    layer_name: str,
    layer_role: str,
    side: str,
    *,
    stack_index: int = 0,
    geometry: object | None = None,
    tags: GeometryTags | None = None,
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
        tags=GeometryTags(source_collection=kind.value) if tags is None else tags,
        payload=Point(1, 1) if geometry is None else geometry,
        source=geometry,
    )


def _require_renderable(store: PcbGeometryStore, geometry_id: str) -> RenderableGeometry:
    item = store.by_id(geometry_id)
    if item is None:
        msg = f"missing test geometry {geometry_id}"
        raise AssertionError(msg)
    return item


def _require_style(layer: DerivedLayer) -> ResolvedStyle:
    if layer.style is None:
        msg = "missing test layer style"
        raise AssertionError(msg)
    return layer.style


def _force_skia_failure_for_ids(
    monkeypatch: pytest.MonkeyPatch,
    failing_ids: Iterable[str],
) -> None:
    failing_id_set = frozenset(failing_ids)

    def fake_geometry_to_skia_artwork(
        item: RenderableGeometry,
        *,
        target_layer_name: str,
    ) -> SkiaArtwork | None:
        if item.id in failing_id_set:
            return None
        return geometry_to_skia_artwork(
            item,
            target_layer_name=target_layer_name,
        )

    monkeypatch.setattr(
        render_modes,
        "geometry_to_skia_artwork",
        fake_geometry_to_skia_artwork,
    )


def _assert_skia_fallback_profile(
    profiler: RenderProfiler,
    event_name: str,
    *,
    converted_items: int,
    unsupported_items: int,
) -> None:
    profile_events = cast("list[dict[str, object]]", profiler.to_dict()["events"])
    fallback_event = next(event for event in profile_events if event["name"] == event_name)
    fallback_data = cast("dict[str, object]", fallback_event["data"])
    assert fallback_data["unsupportedItems"] == unsupported_items
    assert fallback_data["convertedItems"] == converted_items


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
    footprint_ref: str = "J1",
    net_name: str = "GND",
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
        net_name=net_name,
        footprint_ref=footprint_ref,
        drill=drill,
    )
