from __future__ import annotations

import json
from importlib.resources import as_file, files
from pathlib import Path
from typing import cast

import pytest
from conftest import build_render_test_board

from phosphor_eda.domain.pcb import (
    LayerRole,
    PcbArtwork,
    PcbArtworkKind,
    PcbArtworkPurpose,
    PcbCircle,
    PcbConductorKind,
    PcbDrill,
    PcbLayer,
    PcbLine,
    PcbObjectMetadata,
    PcbPolygon,
    PcbText,
)
from phosphor_eda.query.project_loader import load_pcb
from phosphor_eda.render.api import render_pcb_svg
from phosphor_eda.render.inventory import (
    InventoryItemKind,
    InventoryPurpose,
    build_inventory,
    select_inventory_items,
)
from phosphor_eda.render.settings import (
    BUNDLED_PRESETS,
    MAX_CUSTOM_CSS_LENGTH,
    CliOverrides,
    HighlightSpec,
    RenderSettings,
    load_render_settings_file,
    load_render_settings_json,
    resolve_effective_settings,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ALLEGRO_BREAKOUT_BRD = (
    FIXTURES
    / "orcad/opencellular-breakout/allegro/OpenCellular/electronics/breakout/board"
    / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
)
ALLEGRO_ROHM_STEPPER_BRD = (
    FIXTURES
    / "orcad/rohm-stepper-driver-ctrl/Design Files for Rev 1.0"
    / "STEPPER EVAL BRD - PCB Board File - Rev 1.0.brd"
)


def _design_settings(
    *,
    side: str = "front",
    highlight_nets: tuple[str, ...] = (),
) -> RenderSettings:
    """Resolve the bundled ``phosphor:design`` settings for render tests."""
    base = load_render_settings_json('{"extends": "phosphor:design"}')
    overrides = CliOverrides(
        side=side,
        highlights=tuple(HighlightSpec(net=net) for net in highlight_nets),
    )
    return resolve_effective_settings(base, overrides)


def _realistic_settings(*, side: str = "front") -> RenderSettings:
    base = load_render_settings_json('{"extends": "phosphor:realistic"}')
    return resolve_effective_settings(base, CliOverrides(side=side))


def test_render_result_carries_unknown_highlight_warning() -> None:
    """render_pcb_svg returns warnings for an unresolved highlight target."""
    result = render_pcb_svg(
        _board(),
        _design_settings(highlight_nets=("DOES_NOT_EXIST",)),
    )

    assert "<svg" in result.svg
    assert any("Highlight target not found" in w for w in result.warnings)
    assert any("DOES_NOT_EXIST" in w for w in result.warnings)


def test_render_result_no_warnings_on_clean_render() -> None:
    result = render_pcb_svg(_board(), _design_settings())
    assert result.warnings == ()


def test_render_svg_uses_typed_inventory_metadata() -> None:
    svg = render_pcb_svg(_board(), _design_settings()).svg

    assert 'data-kind="pad"' in svg
    assert 'data-kind="via"' in svg
    assert 'data-kind="drill"' in svg
    assert 'data-kind="conductor"' in svg
    assert 'data-kind="artwork"' in svg
    assert 'data-purpose="copper"' in svg
    assert 'data-content-kind="trace"' in svg
    assert 'data-source-collection="pads"' in svg
    assert "display_role" not in svg


def test_render_traces_use_native_stroked_centerlines() -> None:
    """Width-bearing copper traces emit stroked centerlines, not polygons."""
    import re

    svg = render_pcb_svg(_board(), _design_settings()).svg

    conductor_paths = re.findall(
        r'<path d="([^"]*)"[^>]*stroke-linecap: round[^>]*data-kind="conductor"', svg
    )
    assert conductor_paths, "expected at least one stroked conductor path"
    assert "fill: none" in svg
    for d in conductor_paths:
        # A stroked centerline is short: a move plus a line/arc, no polygon ring.
        assert d.startswith("M ")


def test_render_pads_use_native_arcs() -> None:
    """Circular pads render as two-arc native circles rather than polygons."""
    import re

    svg = render_pcb_svg(_board(), _design_settings()).svg
    pad_paths = re.findall(r'<path d="([^"]*)"[^>]*data-kind="pad"', svg)
    assert pad_paths
    assert any(d.count(" A ") == 2 and d.endswith("Z") for d in pad_paths)


def test_render_outline_circles_as_stroked_paths() -> None:
    import re

    board = _board()
    front_silk = next(layer for layer in board.layers if layer.has_role(LayerRole.SILKSCREEN))
    board.artwork.append(
        PcbArtwork(
            id="silk:outline-circle",
            kind=PcbArtworkKind.CIRCLE,
            purpose=PcbArtworkPurpose.SILKSCREEN,
            layer=front_silk,
            data=PcbCircle(5.0, 7.0, 1.0, fill=False, width=0.25),
            footprint=board.footprints[0],
        )
    )

    svg = render_pcb_svg(board, _design_settings()).svg

    match = re.search(r'<path [^>]*data-source-id="silk:outline-circle"[^>]*/>', svg)
    assert match is not None
    path = match.group(0)
    assert 'style="fill: none; stroke: #ffffff; stroke-width: 0.2500' in path
    assert 'fill-rule="evenodd"' not in path


def test_render_zero_width_lines_use_layer_stroke_width() -> None:
    import re

    board = _board()
    front_silk = next(layer for layer in board.layers if layer.has_role(LayerRole.SILKSCREEN))
    board.artwork.append(
        PcbArtwork(
            id="silk:zero-width-line",
            kind=PcbArtworkKind.LINE,
            purpose=PcbArtworkPurpose.SILKSCREEN,
            layer=front_silk,
            data=PcbLine(1.0, 1.0, 3.0, 1.0, 0.0),
            footprint=board.footprints[0],
        )
    )

    for settings in (_design_settings(), _realistic_settings()):
        svg = render_pcb_svg(board, settings).svg
        match = re.search(r'<path [^>]*data-source-id="silk:zero-width-line"[^>]*/>', svg)
        assert match is not None
        path = match.group(0)
        assert 'style="fill: none; stroke: #ffffff; stroke-width: 0.0800' in path
        assert 'fill-rule="evenodd"' not in path


def test_render_outline_polygons_as_stroked_closed_paths() -> None:
    import re

    board = _board()
    front_silk = next(layer for layer in board.layers if layer.has_role(LayerRole.SILKSCREEN))
    board.artwork.append(
        PcbArtwork(
            id="silk:outline-polygon",
            kind=PcbArtworkKind.POLYGON,
            purpose=PcbArtworkPurpose.SILKSCREEN,
            layer=front_silk,
            data=PcbPolygon(
                points=[(1.0, 1.0), (3.0, 1.0), (3.0, 2.0), (1.0, 2.0)],
                width=0.2,
                fill=False,
            ),
            footprint=board.footprints[0],
        )
    )

    svg = render_pcb_svg(board, _design_settings()).svg

    match = re.search(r'<path [^>]*data-source-id="silk:outline-polygon"[^>]*/>', svg)
    assert match is not None
    path = match.group(0)
    assert 'style="fill: none; stroke: #ffffff; stroke-width: 0.2000' in path
    assert 'fill-rule="evenodd"' not in path


def test_allegro_realistic_silkscreen_rectangles_render_as_outlines() -> None:
    import re

    board = load_pcb(ALLEGRO_ROHM_STEPPER_BRD)
    svg = render_pcb_svg(board, _realistic_settings()).svg

    match = re.search(
        r'<path [^>]*data-source-id="allegro:109336496"[^>]*/>',
        svg,
    )
    assert match is not None
    path = match.group(0)
    assert 'data-purpose="silkscreen"' in path
    assert "fill: none" in path
    assert "stroke: #ffffff" in path
    assert "stroke-width:" in path


def test_mask_viewports_cover_full_board_bbox() -> None:
    import re

    board = _board()
    svg = render_pcb_svg(board, _design_settings()).svg

    bbox = board.bbox()
    assert bbox is not None
    min_x, min_y, max_x, max_y = bbox
    masks = re.findall(r"<mask ([^>]*)>", svg)
    assert masks, "expected at least one solder/board mask"
    for attr_str in masks:
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', attr_str))
        vx = float(attrs["x"])
        vy = float(attrs["y"])
        vw = float(attrs["width"])
        vh = float(attrs["height"])
        # Board-material masks must enclose the whole board; the pad-opening
        # mask covers a subset, so only assert containment where the mask's
        # white region is the board itself.
        if vw >= (max_x - min_x) and vh >= (max_y - min_y):
            assert vx <= min_x
            assert vy <= min_y
            assert vx + vw >= max_x
            assert vy + vh >= max_y


def test_inventory_builder_emits_typed_items() -> None:
    inventory = build_inventory(_board(), side="front")

    assert any(
        item.item_kind == InventoryItemKind.BOARD_PROFILE
        and item.purpose == InventoryPurpose.BOARD_MATERIAL
        for item in inventory.items
    )
    assert any(
        item.item_kind == InventoryItemKind.PAD and item.purpose == InventoryPurpose.COPPER
        for item in inventory.items
    )
    assert any(
        item.item_kind == InventoryItemKind.PAD and item.purpose == InventoryPurpose.SOLDER_MASK
        for item in inventory.items
    )
    assert any(item.item_kind == InventoryItemKind.VIA for item in inventory.items)
    assert any(item.item_kind == InventoryItemKind.DRILL for item in inventory.items)
    assert any(
        item.item_kind == InventoryItemKind.CONDUCTOR
        and item.content_kind == PcbConductorKind.TRACE
        for item in inventory.items
    )
    assert any(
        item.item_kind == InventoryItemKind.ARTWORK
        and item.purpose == InventoryPurpose.DESIGNATOR
        and item.content_kind == PcbArtworkKind.TEXT
        for item in inventory.items
    )


def test_allegro_breakout_render_inventory_uses_typed_domain_collections() -> None:
    board = load_pcb(ALLEGRO_BREAKOUT_BRD)
    inventory = build_inventory(board, side="front")
    counts: dict[InventoryItemKind, int] = {}
    for item in inventory.items:
        counts[item.item_kind] = counts.get(item.item_kind, 0) + 1

    assert counts == {
        InventoryItemKind.BOARD_PROFILE: 5,
        InventoryItemKind.PAD: 1134,
        InventoryItemKind.VIA: 1424,
        InventoryItemKind.DRILL: 288,
        InventoryItemKind.CONDUCTOR: 219,
        InventoryItemKind.ARTWORK: 20713,
        InventoryItemKind.KEEPOUT: 131,
    }


def test_inventory_builder_omits_hidden_domain_sources() -> None:
    board = _board()
    board.pads[0].metadata.hidden = True
    board.vias[0].metadata.hidden = True
    board.drills.append(
        PcbDrill(
            "drill:mounting:hidden",
            2.0,
            2.0,
            0.8,
            metadata=PcbObjectMetadata(hidden=True),
        )
    )
    board.conductors[0].metadata.hidden = True
    board.artwork[0].metadata.hidden = True
    assert board.board_profile is not None
    hidden_profile = board.board_profile.elements[0]
    hidden_profile.metadata.hidden = True

    inventory = build_inventory(board, side="front")
    inventory_ids = {item.id for item in inventory.items}

    assert "pad:U1:1:F.Cu:copper" not in inventory_ids
    assert "pad:U1:1:F.Mask:solder_mask" not in inventory_ids
    assert "via:1:F.Cu:copper" not in inventory_ids
    assert "drill:pad:U1:1" not in inventory_ids
    assert "drill:via:1" not in inventory_ids
    assert "drill:mounting:hidden" not in inventory_ids
    assert "trace:1" not in inventory_ids
    assert "silk:1" not in inventory_ids
    assert hidden_profile.id not in inventory_ids
    assert "board:material" in inventory_ids


def test_render_settings_accept_typed_source_filters() -> None:
    settings = load_render_settings_json(
        json.dumps(
            {
                "source": {
                    "layers": [
                        {
                            "match": {"role": "copper"},
                            "itemKinds": ["pad", "via", "conductor"],
                            "purposes": ["copper"],
                        },
                        {
                            "match": {"role": "silkscreen"},
                            "purposes": ["silkscreen", "designator", "value", "user_text"],
                            "contentKinds": ["line", "text"],
                        },
                    ]
                }
            }
        )
    )

    assert settings.source.layers[0].item_kinds == ("pad", "via", "conductor")
    assert settings.source.layers[0].purposes == ("copper",)
    assert settings.source.layers[1].content_kinds == ("line", "text")


def test_render_settings_reject_old_objects_filter() -> None:
    with pytest.raises(ValueError, match="objects is no longer supported"):
        load_render_settings_json(
            json.dumps({"source": {"layers": [{"match": {"role": "copper"}, "objects": ["pad"]}]}})
        )


@pytest.mark.parametrize("name", BUNDLED_PRESETS)
def test_builtin_render_settings_use_typed_source_filters(name: str) -> None:
    settings_file = files("phosphor_eda.render.profiles").joinpath(f"{name}.json")
    with as_file(settings_file) as path:
        settings = load_render_settings_file(path)

    assert all(
        rule.item_kinds or rule.purposes or rule.content_kinds or rule.match
        for rule in settings.source.layers
    )


@pytest.mark.parametrize("name", BUNDLED_PRESETS)
def test_builtin_render_settings_hide_non_silkscreen_footprint_text(name: str) -> None:
    board = _board()
    copper_layer = next(layer for layer in board.layers if layer.name == "F.Cu")
    edge_layer = next(layer for layer in board.layers if layer.has_role(LayerRole.EDGE))
    fab_layer = PcbLayer(
        "F.Fab",
        (LayerRole.FABRICATION, LayerRole.FRONT),
        number=45,
    )
    mechanical_layer = PcbLayer(
        "Mechanical 1",
        (LayerRole.MECHANICAL, LayerRole.FRONT),
        number=46,
    )
    footprint = board.footprints[0]
    board.layers.extend([fab_layer, mechanical_layer])
    board.artwork.extend(
        [
            PcbArtwork(
                id="text:U1:copper-user",
                kind=PcbArtworkKind.TEXT,
                purpose=PcbArtworkPurpose.USER_TEXT,
                layer=copper_layer,
                data=PcbText("DEBUG", 3.0, 8.0, 0.0, 1.0),
                footprint=footprint,
            ),
            PcbArtwork(
                id="text:board:edge-user",
                kind=PcbArtworkKind.TEXT,
                purpose=PcbArtworkPurpose.USER_TEXT,
                layer=edge_layer,
                data=PcbText("OUTLINE NOTE", 3.0, 9.0, 0.0, 1.0),
                footprint=None,
            ),
            PcbArtwork(
                id="text:U1:fab-ref",
                kind=PcbArtworkKind.TEXT,
                purpose=PcbArtworkPurpose.DESIGNATOR,
                layer=fab_layer,
                data=PcbText("U1", 5.0, 3.0, 0.0, 1.0),
                footprint=footprint,
            ),
            PcbArtwork(
                id="text:U1:mech-user",
                kind=PcbArtworkKind.TEXT,
                purpose=PcbArtworkPurpose.USER_TEXT,
                layer=mechanical_layer,
                data=PcbText("PN123", 5.0, 8.0, 0.0, 1.0),
                footprint=footprint,
            ),
        ]
    )

    settings_file = files("phosphor_eda.render.profiles").joinpath(f"{name}.json")
    with as_file(settings_file) as path:
        settings = load_render_settings_file(path)

    selected = select_inventory_items(
        build_inventory(board, side="front"),
        settings.source.layers,
        active_side="front",
    )
    selected_ids = {item.id for item in selected}

    assert "text:U1:copper-user" not in selected_ids
    assert "text:board:edge-user" not in selected_ids
    assert "text:U1:fab-ref" not in selected_ids
    assert "text:U1:mech-user" not in selected_ids


@pytest.mark.parametrize("name", BUNDLED_PRESETS)
def test_presets_hide_mechanical_artwork_by_default(name: str) -> None:
    board = _board()
    mechanical_layer = PcbLayer(
        "Top 3D Body",
        (LayerRole.MECHANICAL, LayerRole.THREE_D_BODY, LayerRole.FRONT),
        number=69,
    )
    footprint = board.footprints[0]
    board.layers.append(mechanical_layer)
    board.artwork.append(
        PcbArtwork(
            id="line:U1:body",
            kind=PcbArtworkKind.LINE,
            purpose=PcbArtworkPurpose.COMPONENT_BODY,
            layer=mechanical_layer,
            data=PcbLine(1.0, 1.0, 4.0, 1.0, 0.1),
            footprint=footprint,
        )
    )

    settings_file = files("phosphor_eda.render.profiles").joinpath(f"{name}.json")
    with as_file(settings_file) as path:
        settings = load_render_settings_file(path)

    selected = select_inventory_items(
        build_inventory(board, side="front"),
        settings.source.layers,
        active_side="front",
    )

    assert "line:U1:body" not in {item.id for item in selected}


def test_render_settings_validate_annotations_at_parse() -> None:
    # A box with no targets is invalid; parse_render_settings must reject it
    # at load, not defer the failure to render time.
    with pytest.raises(ValueError, match="target"):
        load_render_settings_json(json.dumps({"annotations": {"boxes": [{"label": "no targets"}]}}))


def test_render_settings_accept_valid_annotations() -> None:
    settings = load_render_settings_json(
        json.dumps({"annotations": {"labels": [{"target": "U1", "content": "MCU"}]}})
    )
    assert settings.annotations["labels"] == [{"target": "U1", "content": "MCU"}]


def test_render_settings_reject_oversized_custom_css() -> None:
    oversized = "a" * (MAX_CUSTOM_CSS_LENGTH + 1)
    with pytest.raises(ValueError, match="custom_css must be at most"):
        load_render_settings_json(json.dumps({"custom_css": oversized}))


def test_render_settings_schema_advertises_custom_css_max_length() -> None:
    from phosphor_eda.render.api import render_settings_schema

    schema = render_settings_schema()
    raw_properties = schema["properties"]
    assert isinstance(raw_properties, dict)
    properties = cast("dict[str, object]", raw_properties)
    custom_css = properties.get("custom_css")
    assert isinstance(custom_css, dict)
    assert custom_css["maxLength"] == MAX_CUSTOM_CSS_LENGTH


def test_effective_settings_compose_base_and_cli_custom_css() -> None:
    base = RenderSettings(custom_css=".base {}")
    settings = resolve_effective_settings(base, CliOverrides(custom_css=".cli {}"))
    assert settings.custom_css == ".base {}\n.cli {}"


def test_effective_settings_keep_base_custom_css_when_cli_flag_omitted() -> None:
    base = RenderSettings(custom_css=".base {}")
    settings = resolve_effective_settings(base, CliOverrides(custom_css=None))
    assert settings.custom_css == ".base {}"


def test_effective_settings_explicit_empty_cli_custom_css_clears_base() -> None:
    base = RenderSettings(custom_css=".base {}")
    settings = resolve_effective_settings(base, CliOverrides(custom_css=""))
    assert settings.custom_css == ""


def test_effective_settings_reject_oversized_combined_custom_css() -> None:
    base = RenderSettings(custom_css="a" * MAX_CUSTOM_CSS_LENGTH)
    overrides = CliOverrides(custom_css="b")
    with pytest.raises(ValueError, match="custom_css must be at most"):
        resolve_effective_settings(base, overrides)


# Documents the imperative parser rejects; the JSON schema must reject the
# same set (legacy keys via additionalProperties: False, value constraints
# via enums/types/minimums).
_REJECTED_SETTINGS_DOCUMENTS: list[dict[str, object]] = [
    {"theme": "dark"},
    {"font_size": 12},
    {"font_size_px": 12},
    {"include": ["copper"]},
    {"highlight_behavior": "dim"},
    {"style_rules": []},
    {"exclude_component_prefixes": ["R"]},
    {"renderMode": "sketch"},
    {"side": "left"},
    {"width": 0},
    {"fontSizePx": 0},
]


def _documentation_settings(
    *,
    highlights: tuple[HighlightSpec, ...],
    tokens: dict[str, object] | None = None,
) -> RenderSettings:
    payload: dict[str, object] = {"extends": "phosphor:documentation"}
    if tokens:
        payload["tokens"] = tokens
    base = load_render_settings_json(json.dumps(payload))
    return resolve_effective_settings(base, CliOverrides(side="front", highlights=highlights))


def _marker_paths(svg: str) -> list[str]:
    import re

    return re.findall(r'<g[^>]*data-role="highlight\.marker"[^>]*>(.*?)</g>', svg, re.S)


def test_pad_highlight_draws_marker_ring_when_enabled() -> None:
    settings = _documentation_settings(highlights=(HighlightSpec(pad="U1.1"),))
    svg = render_pcb_svg(_board(), settings).svg

    markers = _marker_paths(svg)
    assert len(markers) == 1
    assert "A " in markers[0]


def test_marker_ring_enforces_minimum_screen_diameter() -> None:
    import re

    settings = _documentation_settings(
        highlights=(HighlightSpec(pad="U1.1"),),
        tokens={"highlight.marker.minDiameterPx": 200},
    )
    svg = render_pcb_svg(_board(), settings).svg

    (marker,) = _marker_paths(svg)
    radii = [float(value) for value in re.findall(r"A ([\d.]+)", marker)]
    assert radii
    view_box = re.search(r'viewBox="[\d.\-]+ [\d.\-]+ ([\d.]+)', svg)
    assert view_box is not None
    width = re.search(r'width="(\d+)"', svg)
    assert width is not None
    px_per_mm = float(width.group(1)) / float(view_box.group(1))
    assert radii[0] * px_per_mm >= 100  # half of minDiameterPx

    # The ring must not collapse to the pad's own size when the minimum is large.
    assert radii[0] > 1.0


def test_marker_ring_absent_for_net_and_component_highlights() -> None:
    settings = _documentation_settings(
        highlights=(HighlightSpec(net="VCC"), HighlightSpec(component="U1")),
    )
    svg = render_pcb_svg(_board(), settings).svg
    assert not _marker_paths(svg)


def test_marker_ring_disabled_in_realistic_preset() -> None:
    base = load_render_settings_json(json.dumps({"extends": "phosphor:realistic"}))
    settings = resolve_effective_settings(
        base,
        CliOverrides(side="front", highlights=(HighlightSpec(pad="U1.1"),)),
    )
    svg = render_pcb_svg(_board(), settings).svg
    assert not _marker_paths(svg)


def test_marker_ring_uses_highlight_color() -> None:
    settings = _documentation_settings(
        highlights=(HighlightSpec(pad="U1.1", color="#00aa11"),),
    )
    svg = render_pcb_svg(_board(), settings).svg
    (marker,) = _marker_paths(svg)
    assert "#00aa11" in marker


def test_hidden_pill_label_text_defaults_to_dark_fill() -> None:
    """With the pill hidden, text must not keep the white-on-pill contrast color."""
    from phosphor_eda.render.annotations import parse_annotations, resolve_annotations

    base = load_render_settings_json(
        json.dumps(
            {
                "extends": "phosphor:documentation",
                "annotations": {"pointers": [{"target": "U1.1", "label": "VCC"}]},
            }
        )
    )
    settings = resolve_effective_settings(base, CliOverrides(side="front"))
    board = _board()
    annotations = resolve_annotations(
        parse_annotations(settings.annotations),
        board,
        settings.side,
        settings.width,
        settings.font_size,
    )
    svg = render_pcb_svg(board, settings, annotations=annotations).svg

    label_texts = [
        chunk.split(">")[0]
        for chunk in svg.split("<text ")
        if 'class="annotation-label-text"' in chunk.split(">")[0]
    ]
    assert label_texts
    for attrs in label_texts:
        assert 'fill="#fff"' not in attrs


def test_dimming_mode_parses() -> None:
    for mode in ("off", "on", "auto"):
        settings = load_render_settings_json(json.dumps({"dimming": {"mode": mode}}))
        assert settings.dimming.mode == mode


def test_dimming_defaults_to_auto() -> None:
    settings = load_render_settings_json("{}")
    assert settings.dimming.mode == "auto"


def test_dimming_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match=r"dimming\.mode"):
        _ = load_render_settings_json(json.dumps({"dimming": {"mode": "sometimes"}}))


def test_dimming_enabled_is_rejected_with_migration_message() -> None:
    with pytest.raises(ValueError, match=r"dimming\.enabled is no longer supported"):
        _ = load_render_settings_json(json.dumps({"dimming": {"enabled": True}}))


def test_dimming_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="unknown dimming key"):
        _ = load_render_settings_json(json.dumps({"dimming": {"mod": "off"}}))


def test_background_parses() -> None:
    settings = load_render_settings_json(json.dumps({"background": "#fafafa"}))
    assert settings.background == "#fafafa"


def test_background_defaults_to_white_when_resolved() -> None:
    base = load_render_settings_json("{}")
    resolved = resolve_effective_settings(base, CliOverrides())
    assert resolved.background == "#ffffff"


def test_background_rejects_empty_and_oversized_values() -> None:
    with pytest.raises(ValueError, match="background"):
        _ = load_render_settings_json(json.dumps({"background": ""}))
    with pytest.raises(ValueError, match="background"):
        _ = load_render_settings_json(json.dumps({"background": "x" * 65}))


def test_render_emits_background_rect_by_default() -> None:
    svg = render_pcb_svg(_board(), _design_settings()).svg
    assert 'class="canvas-background"' in svg
    assert "#ffffff" in svg


def test_background_none_omits_rect() -> None:
    base = load_render_settings_json(json.dumps({"background": "none"}))
    settings = resolve_effective_settings(base, CliOverrides(side="front"))
    svg = render_pcb_svg(_board(), settings).svg
    assert "canvas-background" not in svg


def test_auto_dimming_emits_scrim_only_when_highlights_resolve() -> None:
    plain = render_pcb_svg(_board(), _design_settings()).svg
    assert "dim-scrim" not in plain

    highlighted = render_pcb_svg(_board(), _design_settings(highlight_nets=("VCC",))).svg
    assert 'class="dim-scrim"' in highlighted
    # The scrim paints after base layers and before the highlight overlay.
    assert highlighted.index("dim-scrim") < highlighted.index("highlight-overlay")

    unresolved = render_pcb_svg(
        _board(),
        _design_settings(highlight_nets=("DOES_NOT_EXIST",)),
    ).svg
    assert "dim-scrim" not in unresolved


def test_dimming_off_suppresses_scrim() -> None:
    base = load_render_settings_json(
        json.dumps({"extends": "phosphor:design", "dimming": {"mode": "off"}})
    )
    settings = resolve_effective_settings(
        base,
        CliOverrides(side="front", highlights=(HighlightSpec(net="VCC"),)),
    )
    svg = render_pcb_svg(_board(), settings).svg
    assert "dim-scrim" not in svg


def test_dimming_on_emits_scrim_without_highlights() -> None:
    base = load_render_settings_json(
        json.dumps({"extends": "phosphor:design", "dimming": {"mode": "on"}})
    )
    settings = resolve_effective_settings(base, CliOverrides(side="front"))
    svg = render_pcb_svg(_board(), settings).svg
    assert 'class="dim-scrim"' in svg


def test_scrim_tokens_override_fill_and_opacity() -> None:
    base = load_render_settings_json(
        json.dumps(
            {
                "extends": "phosphor:design",
                "tokens": {"highlight.dim.fill": "#000000", "highlight.dim.opacity": 0.3},
            }
        )
    )
    settings = resolve_effective_settings(
        base,
        CliOverrides(side="front", highlights=(HighlightSpec(net="VCC"),)),
    )
    svg = render_pcb_svg(_board(), settings).svg
    assert 'class="dim-scrim"' in svg
    scrim_index = svg.index("dim-scrim")
    scrim_tag = svg[svg.rindex("<rect", 0, scrim_index) : svg.index(">", scrim_index) + 1]
    assert "#000000" in scrim_tag
    assert "0.3" in scrim_tag


@pytest.mark.parametrize("document", _REJECTED_SETTINGS_DOCUMENTS)
def test_parser_rejects_invalid_settings_documents(document: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        load_render_settings_json(json.dumps(document))


@pytest.mark.parametrize("document", _REJECTED_SETTINGS_DOCUMENTS)
def test_schema_rejects_invalid_settings_documents(document: dict[str, object]) -> None:
    from phosphor_eda.render.api import render_settings_schema

    schema = render_settings_schema()
    raw_properties = schema["properties"]
    assert isinstance(raw_properties, dict)
    properties = cast("dict[str, object]", raw_properties)
    (key,) = document
    if key not in properties:
        # Legacy keys: additionalProperties False makes the schema reject them.
        assert schema["additionalProperties"] is False
        return
    # Value-constrained keys: schema declares an enum or numeric minimum that
    # excludes the offending value.
    constraint = properties.get(key)
    assert isinstance(constraint, dict)
    assert "enum" in constraint or "minimum" in constraint


def test_annotation_style_tokens_resolve_typed() -> None:
    from phosphor_eda.render.plan import annotation_style_for_settings
    from phosphor_eda.render.settings import RenderSettings

    settings = RenderSettings(
        tokens={
            "annotation.label.fill": "#fff",
            "annotation.label.textHaloWidthPx": 3,
            "annotation.label.pillVisible": False,
            "annotation.connector.stroke": "#0f0",
            "annotation.connector.strokeWidthPx": 1.5,
        }
    )
    style = annotation_style_for_settings(settings)
    assert style.label.fill == "#fff"
    assert style.label.text_halo_width_px == 3.0
    assert style.label.pill_visible is False
    assert style.connector.stroke == "#0f0"
    assert style.connector.stroke_width_px == 1.5


def test_annotation_style_rejects_wrong_token_type() -> None:
    from phosphor_eda.render.plan import annotation_style_for_settings
    from phosphor_eda.render.settings import RenderSettings

    settings = RenderSettings(tokens={"annotation.label.fill": 42})
    with pytest.raises(ValueError, match="must be a string"):
        annotation_style_for_settings(settings)


def test_annotation_style_rejects_non_scalar_css_value_token() -> None:
    from phosphor_eda.render.plan import annotation_style_for_settings
    from phosphor_eda.render.settings import RenderSettings, TokenMap

    # Simulate untyped runtime data sneaking past the TokenMap type, e.g. a
    # caller constructing RenderSettings from unvalidated JSON.
    tokens = cast("TokenMap", {"annotation.label.fontWeight": ["bold"]})
    settings = RenderSettings(tokens=tokens)
    with pytest.raises(ValueError, match="must be a string or number"):
        annotation_style_for_settings(settings)


# The shared synthetic render board lives in conftest so every render test
# module reuses one builder instead of importing across test files.
_board = build_render_test_board
