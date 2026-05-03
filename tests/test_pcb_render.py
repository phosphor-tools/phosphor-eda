"""Tests for the PCB SVG renderer — structural and CSS assertions."""

import json
import re
from pathlib import Path

import pytest

from phosphor_eda.kicad.pcb_parser import parse_kicad_pcb
from phosphor_eda.pcb import (
    LayerFunction,
    Pcb,
    PcbFootprint,
    PcbLayer,
    PcbLine,
    PcbModel3D,
    PcbNet,
    PcbPad,
    PcbSegment,
    PcbVia,
)
from phosphor_eda.pcb_annotations import (
    LegendEntry,
    ResolvedAnnotations,
    ResolvedBox,
    ResolvedLabel,
    ResolvedLegend,
    ResolvedPointer,
)
from phosphor_eda.pcb_render import (
    HighlightSpec,
    _fmt_attrs,  # pyright: ignore[reportPrivateUsage]
    parse_render_settings,
    render_pcb_svg,
)

FIXTURE = Path(__file__).parent / "fixtures" / "swd_switch.kicad_pcb"
ORANGECRAB_FIXTURE = Path(__file__).parent / "fixtures" / "orangecrab.kicad_pcb"


@pytest.fixture(scope="module")
def board() -> Pcb:
    return parse_kicad_pcb(FIXTURE)


@pytest.fixture(scope="module")
def orangecrab_board() -> Pcb:
    return parse_kicad_pcb(ORANGECRAB_FIXTURE)


# ---------------------------------------------------------------------------
# Structure tests
# ---------------------------------------------------------------------------


def test_valid_svg(board: Pcb) -> None:
    svg = render_pcb_svg(board)
    assert svg.startswith("<svg")
    assert svg.strip().endswith("</svg>")


def test_has_theme_style(board: Pcb) -> None:
    svg = render_pcb_svg(board)
    assert '<style id="theme">' in svg


def test_has_board_clip(board: Pcb) -> None:
    svg = render_pcb_svg(board)
    assert "board-clip" in svg


def test_has_drill_clip(board: Pcb) -> None:
    svg = render_pcb_svg(board)
    assert "drill-clip" in svg


def test_has_copper_layer_groups(board: Pcb) -> None:
    svg = render_pcb_svg(board)
    assert 'data-layer="F.Cu"' in svg
    assert 'data-layer="B.Cu"' in svg


def test_layer_paint_order(board: Pcb) -> None:
    """B.Cu should appear before F.Cu in document order (painter's model)."""
    svg = render_pcb_svg(board)
    assert svg.index('data-layer="B.Cu"') < svg.index('data-layer="F.Cu"')


def test_silk_after_copper(board: Pcb) -> None:
    """Silkscreen layer group appears after copper layer groups."""
    svg = render_pcb_svg(board)
    fcu_pos = svg.index('data-layer="F.Cu"')
    silk_names = ["F.SilkS", "F.Silkscreen", "B.SilkS", "B.Silkscreen"]
    found = False
    for name in silk_names:
        marker = f'data-layer="{name}"'
        if marker in svg:
            assert svg.index(marker) > fcu_pos
            found = True
    assert found, "No silkscreen layer group found"


# ---------------------------------------------------------------------------
# Data attribute tests
# ---------------------------------------------------------------------------


def test_pad_attributes(board: Pcb) -> None:
    svg = render_pcb_svg(board)
    assert 'data-type="pad"' in svg
    assert "data-component=" in svg
    assert "data-pad=" in svg
    assert "data-net=" in svg


def test_trace_attributes(board: Pcb) -> None:
    """All traces are always present (visibility controlled via CSS)."""
    svg = render_pcb_svg(board)
    assert 'data-type="trace"' in svg
    assert "data-net-number=" in svg


def test_via_attributes(board: Pcb) -> None:
    svg = render_pcb_svg(board)
    assert 'data-type="via"' in svg
    assert 'class="via"' in svg


def test_via_annular_ring_uses_size() -> None:
    """Annular ring radius should be via.size / 2, not drill / 2 + constant."""
    fp = PcbFootprint(
        reference="U1",
        footprint_lib="test",
        x=5.0,
        y=5.0,
        rotation=0.0,
        layer="F.Cu",
        pads=[
            PcbPad(
                number="1",
                x=5.0,
                y=5.0,
                width=1.0,
                height=1.0,
                shape="rect",
                layers=["F.Cu"],
                net_number=1,
                net_name="SIG",
                footprint_ref="U1",
            )
        ],
        fab_lines=[PcbLine(4, 4, 6, 4, "F.Fab", 0.1)],
    )
    board = Pcb(
        name="via-size-test",
        nets={0: PcbNet(0, ""), 1: PcbNet(1, "SIG")},
        footprints=[fp],
        segments=[PcbSegment(5.0, 5.0, 10.0, 5.0, 0.25, "F.Cu", 1)],
        vias=[PcbVia(10.0, 5.0, size=0.8, drill=0.4, layers=["F.Cu", "B.Cu"], net_number=1)],
        outline_lines=[
            PcbLine(0, 0, 15, 0, "Edge.Cuts", 0.1),
            PcbLine(15, 0, 15, 10, "Edge.Cuts", 0.1),
            PcbLine(15, 10, 0, 10, "Edge.Cuts", 0.1),
            PcbLine(0, 10, 0, 0, "Edge.Cuts", 0.1),
        ],
        outline_arcs=[],
        layers=[
            PcbLayer("F.Cu", LayerFunction.COPPER, side="front"),
            PcbLayer("B.Cu", LayerFunction.COPPER, side="back"),
            PcbLayer("F.Fab", LayerFunction.FAB, side="front"),
        ],
    )
    svg = render_pcb_svg(board)
    # Annular ring radius should be size/2 = 0.4, not drill/2 + 0.05 = 0.25
    assert 'r="0.4000"' in svg
    assert 'r="0.2500"' not in svg  # old hardcoded formula


def test_zone_attributes(board: Pcb) -> None:
    """swd_switch has zones on inner copper layers."""
    svg = render_pcb_svg(board)
    assert 'data-type="zone"' in svg


def test_component_body_attributes(board: Pcb) -> None:
    svg = render_pcb_svg(board)
    assert 'data-type="body"' in svg


# ---------------------------------------------------------------------------
# Highlight tests
# ---------------------------------------------------------------------------


def test_highlight_adds_style(board: Pcb) -> None:
    svg = render_pcb_svg(board, highlight_nets=["VCC"])
    assert '<style id="highlight">' in svg


def test_highlight_css_targets_net(board: Pcb) -> None:
    """Highlight CSS should contain data-net-number selector for VCC (net 1)."""
    svg = render_pcb_svg(board, highlight_nets=["VCC"])
    assert 'data-net-number="1"' in svg


def test_highlight_component_restores_by_ref(board: Pcb) -> None:
    """Component highlight CSS restores elements by data-component, not net."""
    svg = render_pcb_svg(board, highlight_components=["TP3"])
    assert 'data-component="TP3"' in svg
    assert "Restore highlighted components" in svg


def test_highlight_component_does_not_highlight_nets(board: Pcb) -> None:
    """-c alone should not produce net-number restore rules."""
    svg = render_pcb_svg(board, highlight_components=["TP3"])
    assert "Restore highlighted nets" not in svg


def test_no_highlight_without_args(board: Pcb) -> None:
    svg = render_pcb_svg(board)
    assert '<style id="highlight">' not in svg


# ---------------------------------------------------------------------------
# Highlight + inner-layer visibility
# ---------------------------------------------------------------------------


def _make_board_with_inner_layers() -> Pcb:
    """Board with front, inner, and back copper plus traces on all three."""
    fp = PcbFootprint(
        reference="U1",
        footprint_lib="test",
        x=5.0,
        y=10.0,
        rotation=0.0,
        layer="F.Cu",
        pads=[
            PcbPad(
                number="1",
                x=5.0,
                y=10.0,
                width=1.0,
                height=1.0,
                shape="rect",
                layers=["F.Cu"],
                net_number=1,
                net_name="SIG",
                footprint_ref="U1",
            ),
        ],
        fab_lines=[
            PcbLine(4, 9, 6, 9, "F.Fab", 0.1),
            PcbLine(6, 9, 6, 11, "F.Fab", 0.1),
            PcbLine(6, 11, 4, 11, "F.Fab", 0.1),
            PcbLine(4, 11, 4, 9, "F.Fab", 0.1),
        ],
    )
    return Pcb(
        name="inner-test",
        nets={0: PcbNet(0, ""), 1: PcbNet(1, "SIG")},
        footprints=[fp],
        segments=[
            PcbSegment(5.0, 10.0, 10.0, 10.0, 0.25, "F.Cu", 1),
            PcbSegment(10.0, 10.0, 15.0, 10.0, 0.25, "In1.Cu", 1),
            PcbSegment(15.0, 10.0, 15.0, 5.0, 0.25, "B.Cu", 1),
        ],
        vias=[
            PcbVia(10.0, 10.0, 0.6, 0.3, ["F.Cu", "In1.Cu"], 1),
        ],
        outline_lines=[
            PcbLine(0, 0, 20, 0, "Edge.Cuts", 0.1),
            PcbLine(20, 0, 20, 20, "Edge.Cuts", 0.1),
            PcbLine(20, 20, 0, 20, "Edge.Cuts", 0.1),
            PcbLine(0, 20, 0, 0, "Edge.Cuts", 0.1),
        ],
        outline_arcs=[],
        layers=[
            PcbLayer("F.Cu", LayerFunction.COPPER, side="front"),
            PcbLayer("In1.Cu", LayerFunction.COPPER, side="", number=1),
            PcbLayer("B.Cu", LayerFunction.COPPER, side="back"),
            PcbLayer("F.SilkS", LayerFunction.SILKSCREEN, side="front"),
            PcbLayer("F.Fab", LayerFunction.FAB, side="front"),
        ],
    )


def test_review_theme_hides_inner_copper() -> None:
    """Review theme hides inner copper layers when no highlights are active."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(board, theme="review")
    assert "g.layer-In1-Cu { display: none; }" in svg


def test_review_highlight_restores_inner_layer_visibility() -> None:
    """Highlighting a net in review theme overrides display:none on inner layers."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(board, theme="review", highlight_nets=["SIG"])
    assert "display: inline !important" in svg
    assert "g.layer-In1-Cu { display: inline !important; }" in svg


def test_clean_highlight_restores_all_copper_visibility() -> None:
    """Highlighting a net in clean theme restores all copper and via groups."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(board, theme="clean", highlight_nets=["SIG"])
    assert "g.layer-F-Cu { display: inline !important; }" in svg
    assert "g.layer-In1-Cu { display: inline !important; }" in svg
    assert "g.layer-B-Cu { display: inline !important; }" in svg
    assert "g.layer-vias { display: inline !important; }" in svg


def test_clean_highlight_component_restores_copper() -> None:
    """Component highlight in clean theme still restores copper for pads."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(board, theme="clean", highlight_components=["U1"])
    assert "g.layer-F-Cu { display: inline !important; }" in svg


def test_design_highlight_no_visibility_override() -> None:
    """Design theme never hides copper, so highlights don't need overrides."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(board, theme="design", highlight_nets=["SIG"])
    assert "display: inline !important" not in svg


def test_no_visibility_override_without_highlights() -> None:
    """Without highlights, no display overrides are emitted for any theme."""
    board = _make_board_with_inner_layers()
    for theme in ("design", "review", "clean"):
        svg = render_pcb_svg(board, theme=theme)
        assert "display: inline !important" not in svg, f"theme={theme}"


# ---------------------------------------------------------------------------
# Side tests
# ---------------------------------------------------------------------------


def test_back_mirror(board: Pcb) -> None:
    svg = render_pcb_svg(board, side="back")
    assert "scale(-1" in svg


def test_front_no_mirror(board: Pcb) -> None:
    svg = render_pcb_svg(board, side="front")
    assert "scale(-1" not in svg


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_unknown_net_no_error(board: Pcb) -> None:
    svg = render_pcb_svg(board, highlight_nets=["NONEXISTENT_NET_XYZ"])
    assert svg.startswith("<svg")


def test_unknown_component_no_error(board: Pcb) -> None:
    svg = render_pcb_svg(board, highlight_components=["NONEXISTENT"])
    assert svg.startswith("<svg")


def test_both_highlight_types(board: Pcb) -> None:
    svg = render_pcb_svg(board, highlight_nets=["GND"], highlight_components=["TP3"])
    assert svg.startswith("<svg")
    assert "TP3" in svg
    assert '<style id="highlight">' in svg


# ---------------------------------------------------------------------------
# OrangeCrab integration
# ---------------------------------------------------------------------------


def test_orangecrab_renders(orangecrab_board: Pcb) -> None:
    svg = render_pcb_svg(orangecrab_board)
    assert svg.startswith("<svg")
    assert svg.strip().endswith("</svg>")


def test_orangecrab_has_zones(orangecrab_board: Pcb) -> None:
    svg = render_pcb_svg(orangecrab_board)
    assert 'data-type="zone"' in svg


# ---------------------------------------------------------------------------
# XML escaping (_fmt_attrs)
# ---------------------------------------------------------------------------


def test_fmt_attrs_escapes_special_chars() -> None:
    """Attribute values with quotes, ampersands, and angle brackets are escaped."""
    attrs = {"data-x": 'he said "hi" & <bye>'}
    result = _fmt_attrs(attrs)
    assert "&quot;" in result
    assert "&amp;" in result
    assert "&lt;" in result


def test_fmt_attrs_empty() -> None:
    assert _fmt_attrs(None) == ""
    assert _fmt_attrs({}) == ""


# ---------------------------------------------------------------------------
# 3D model metadata in SVG
# ---------------------------------------------------------------------------


def _make_board_with_models(
    models: list[PcbModel3D],
    *,
    fab_lines: list[PcbLine] | None = None,
) -> Pcb:
    """Create a minimal board with one footprint carrying the given models."""
    fp = PcbFootprint(
        reference="U1",
        footprint_lib="test",
        x=10.0,
        y=10.0,
        rotation=0.0,
        layer="F.Cu",
        models_3d=models,
        fab_lines=fab_lines or [],
    )
    return Pcb(
        name="test",
        nets={0: PcbNet(0, "")},
        footprints=[fp],
        segments=[],
        vias=[],
        outline_lines=[
            PcbLine(0, 0, 20, 0, "Edge.Cuts", 0.1),
            PcbLine(20, 0, 20, 20, "Edge.Cuts", 0.1),
            PcbLine(20, 20, 0, 20, "Edge.Cuts", 0.1),
            PcbLine(0, 20, 0, 0, "Edge.Cuts", 0.1),
        ],
        outline_arcs=[],
    )


def test_body_group_has_data_models() -> None:
    """Footprint with a cached model gets data-models on the body group."""
    model = PcbModel3D(
        source="test.step",
        offset=(1.0, 2.0, 3.0),
        rotation=(0.0, 0.0, 90.0),
        scale=(1.0, 1.0, 1.0),
        cache_key="abc123",
    )
    fab_line = PcbLine(9, 9, 11, 9, "F.Fab", 0.1)
    board = _make_board_with_models([model], fab_lines=[fab_line])
    svg = render_pcb_svg(board)
    assert "data-models=" in svg


def test_data_models_json_valid() -> None:
    """The data-models attribute contains valid JSON with the expected schema."""
    model = PcbModel3D(
        source="test.step",
        offset=(1.0, 2.0, 3.0),
        rotation=(0.0, 0.0, 90.0),
        scale=(1.0, 1.0, 1.0),
        cache_key="abc123",
    )
    fab_line = PcbLine(9, 9, 11, 9, "F.Fab", 0.1)
    board = _make_board_with_models([model], fab_lines=[fab_line])
    svg = render_pcb_svg(board)

    # Extract the data-models attribute value (XML-escaped JSON)
    match = re.search(r'data-models="([^"]*)"', svg)
    assert match is not None
    # The value is XML-escaped, but since we use compact JSON with no quotes
    # in values, the main escaping is &quot; for the JSON internal quotes.
    raw = match.group(1).replace("&quot;", '"').replace("&amp;", "&")
    parsed = json.loads(raw)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0]["key"] == "abc123"
    assert parsed[0]["offset"] == [1.0, 2.0, 3.0]
    assert parsed[0]["rotation"] == [0.0, 0.0, 90.0]
    assert parsed[0]["scale"] == [1.0, 1.0, 1.0]


def test_no_data_models_when_empty() -> None:
    """Footprint with no models → no data-models attribute."""
    fab_line = PcbLine(9, 9, 11, 9, "F.Fab", 0.1)
    board = _make_board_with_models([], fab_lines=[fab_line])
    svg = render_pcb_svg(board)
    assert "data-models" not in svg


def test_no_data_models_when_no_cache_key() -> None:
    """Models without cache_key are excluded from data-models."""
    model = PcbModel3D(source="test.step", cache_key="")
    fab_line = PcbLine(9, 9, 11, 9, "F.Fab", 0.1)
    board = _make_board_with_models([model], fab_lines=[fab_line])
    svg = render_pcb_svg(board)
    assert "data-models" not in svg


def test_model_only_footprint_gets_body_group() -> None:
    """Footprint with 3D model but no fab geometry still gets a body group."""
    model = PcbModel3D(source="test.step", cache_key="def456")
    board = _make_board_with_models([model])
    svg = render_pcb_svg(board)
    assert "data-models=" in svg
    assert 'data-component="U1"' in svg


# ---------------------------------------------------------------------------
# Component metadata attributes (data-footprint-lib, data-value)
# ---------------------------------------------------------------------------


def _make_board_with_component(
    *,
    ref: str = "U1",
    lib: str = "Package_SO:SOIC-8",
    value: str = "SN74LVC2G66",
) -> Pcb:
    """Board with one footprint that has lib/value metadata and a pad + fab line."""
    fp = PcbFootprint(
        reference=ref,
        footprint_lib=lib,
        x=10.0,
        y=10.0,
        rotation=0.0,
        layer="F.Cu",
        value=value,
        pads=[
            PcbPad(
                number="1",
                x=10.0,
                y=10.0,
                width=1.0,
                height=1.0,
                shape="rect",
                layers=["F.Cu"],
                net_number=1,
                net_name="VCC",
                footprint_ref=ref,
            )
        ],
        silkscreen_lines=[
            PcbLine(9, 9, 11, 9, "F.SilkS", 0.12, footprint_ref=ref),
        ],
        fab_lines=[
            PcbLine(9, 9, 11, 9, "F.Fab", 0.1, footprint_ref=ref),
            PcbLine(11, 9, 11, 11, "F.Fab", 0.1, footprint_ref=ref),
            PcbLine(11, 11, 9, 11, "F.Fab", 0.1, footprint_ref=ref),
            PcbLine(9, 11, 9, 9, "F.Fab", 0.1, footprint_ref=ref),
        ],
        texts=[],
    )
    return Pcb(
        name="test",
        nets={0: PcbNet(0, ""), 1: PcbNet(1, "VCC")},
        footprints=[fp],
        segments=[],
        vias=[],
        outline_lines=[
            PcbLine(0, 0, 20, 0, "Edge.Cuts", 0.1),
            PcbLine(20, 0, 20, 20, "Edge.Cuts", 0.1),
            PcbLine(20, 20, 0, 20, "Edge.Cuts", 0.1),
            PcbLine(0, 20, 0, 0, "Edge.Cuts", 0.1),
        ],
        outline_arcs=[],
        layers=[
            PcbLayer("F.Cu", LayerFunction.COPPER, side="front"),
            PcbLayer("F.SilkS", LayerFunction.SILKSCREEN, side="front"),
            PcbLayer("F.Fab", LayerFunction.FAB, side="front"),
        ],
    )


def test_pad_has_footprint_lib_and_value() -> None:
    """Pads carry data-footprint-lib and data-value from the footprint."""
    board = _make_board_with_component()
    svg = render_pcb_svg(board)
    assert 'data-footprint-lib="Package_SO:SOIC-8"' in svg
    assert 'data-value="SN74LVC2G66"' in svg


def test_silk_has_footprint_lib() -> None:
    """Silkscreen lines with a footprint_ref carry lib/value attributes."""
    board = _make_board_with_component()
    svg = render_pcb_svg(board)
    # Silk lines should have component attrs
    silk_pattern = re.compile(r'class="silk"[^/]*data-footprint-lib="Package_SO:SOIC-8"')
    assert silk_pattern.search(svg)


def test_body_group_has_lib_and_value() -> None:
    """Body group <g> carries data-footprint-lib and data-value."""
    board = _make_board_with_component()
    svg = render_pcb_svg(board)
    body_pattern = re.compile(r'data-type="body"[^>]*data-footprint-lib="Package_SO:SOIC-8"')
    assert body_pattern.search(svg)


def test_ref_text_has_lib_and_value() -> None:
    """Ref text labels carry data-footprint-lib and data-value."""
    from phosphor_eda.pcb import PcbText

    board = _make_board_with_component()
    # Add a visible ref text so it renders
    board.footprints[0].texts.append(
        PcbText(
            text="U1",
            x=10.0,
            y=8.0,
            rotation=0.0,
            layer="F.Fab",
            font_size=0.5,
            kind="reference",
            footprint_ref="U1",
        )
    )
    svg = render_pcb_svg(board)
    ref_pattern = re.compile(r'class="ref-text"[^>]*data-footprint-lib="Package_SO:SOIC-8"')
    assert ref_pattern.search(svg)


def test_no_lib_attr_when_empty() -> None:
    """No data-footprint-lib if the footprint has no lib string."""
    board = _make_board_with_component(lib="", value="")
    svg = render_pcb_svg(board)
    assert "data-footprint-lib" not in svg
    assert "data-value" not in svg


# ---------------------------------------------------------------------------
# Component metadata JSON block
# ---------------------------------------------------------------------------


def test_pcb_metadata_json_block() -> None:
    """SVG contains a JSON metadata block with component lib/value info."""
    board = _make_board_with_component()
    svg = render_pcb_svg(board)
    assert '<script type="application/json" id="pcb-metadata">' in svg
    match = re.search(
        r'<script type="application/json" id="pcb-metadata">\n(.*?)\n</script>',
        svg,
        re.DOTALL,
    )
    assert match is not None
    parsed = json.loads(match.group(1))
    assert "U1" in parsed
    assert parsed["U1"]["lib"] == "Package_SO:SOIC-8"
    assert parsed["U1"]["value"] == "SN74LVC2G66"


def test_no_metadata_when_no_lib_or_value() -> None:
    """No metadata block if all footprints lack lib and value."""
    board = _make_board_with_component(lib="", value="")
    svg = render_pcb_svg(board)
    assert "pcb-metadata" not in svg


# ---------------------------------------------------------------------------
# Custom CSS injection
# ---------------------------------------------------------------------------


def test_custom_css_injected() -> None:
    """Custom CSS appears in a dedicated <style id="custom"> block."""
    board = _make_board_with_component()
    css = ".board-fill { fill: purple; }"
    svg = render_pcb_svg(board, custom_css=css)
    assert '<style id="custom">' in svg
    assert "fill: purple;" in svg


def test_custom_css_not_present_when_empty() -> None:
    """No custom style block when no custom CSS provided."""
    board = _make_board_with_component()
    svg = render_pcb_svg(board)
    assert '<style id="custom">' not in svg


# ---------------------------------------------------------------------------
# Real-fixture render integration (swd_switch)
# ---------------------------------------------------------------------------


def test_swd_switch_has_footprint_lib_attr(board: Pcb) -> None:
    """Rendered SVG should contain data-footprint-lib for real footprints."""
    svg = render_pcb_svg(board)
    assert "data-footprint-lib=" in svg


def test_swd_switch_metadata_has_lib(board: Pcb) -> None:
    """The pcb-metadata JSON block should include entries with non-empty lib."""
    svg = render_pcb_svg(board)
    match = re.search(
        r'<script type="application/json" id="pcb-metadata">\n(.*?)\n</script>',
        svg,
        re.DOTALL,
    )
    assert match is not None
    parsed = json.loads(match.group(1))
    libs = [v["lib"] for v in parsed.values() if v.get("lib")]
    assert len(libs) >= 3


def test_swd_switch_has_data_value(board: Pcb) -> None:
    """At least some elements should carry data-value for components with values."""
    svg = render_pcb_svg(board)
    assert "data-value=" in svg


# ---------------------------------------------------------------------------
# Annotation rendering
# ---------------------------------------------------------------------------


def _make_resolved_box() -> ResolvedBox:
    return ResolvedBox(
        x=9.0,
        y=9.0,
        width=4.0,
        height=4.0,
        label_text="MCU",
        label_x=25.0,
        label_y=9.5,
        label_width=6.0,
        label_height=2.0,
        connector_path=[(25.0, 10.5), (22.0, 10.5), (22.0, 11.0), (11.0, 11.0)],
        color="rgba(255,107,53,0.9)",
    )


def _make_resolved_pointer() -> ResolvedPointer:
    return ResolvedPointer(
        target_x=10.0,
        target_y=10.0,
        label_text="Clock",
        label_x=25.0,
        label_y=13.0,
        label_width=7.0,
        label_height=2.0,
        connector_path=[(25.0, 14.0), (22.0, 14.0), (22.0, 10.0), (10.0, 10.0)],
        color="rgba(255,107,53,0.9)",
    )


def _make_resolved_legend() -> ResolvedLegend:
    return ResolvedLegend(
        title="SPI Signals",
        entries=[
            LegendEntry(color="#4488ff", label="SCLK"),
            LegendEntry(color="#e8922e", label="MOSI"),
        ],
        x=5.0,
        y=22.0,
        width=10.0,
        height=4.0,
    )


def _make_annotations(
    *,
    boxes: bool = False,
    pointers: bool = False,
    legend: bool = False,
    labels: bool = False,
) -> ResolvedAnnotations:
    return ResolvedAnnotations(
        boxes=[_make_resolved_box()] if boxes else [],
        pointers=[_make_resolved_pointer()] if pointers else [],
        labels=[
            ResolvedLabel(
                label_text="Main MCU",
                label_x=25.0,
                label_y=16.0,
                label_width=8.0,
                label_height=2.0,
                connector_path=[(25.0, 17.0), (22.0, 17.0), (22.0, 10.0), (10.0, 10.0)],
            )
        ]
        if labels
        else [],
        legend=_make_resolved_legend() if legend else None,
        font_size=1.0,
        content_bbox=(5.0, 5.0, 33.0, 26.0),
    )


def test_annotation_box_rendered() -> None:
    """SVG should contain annotation box rect and pill label."""
    board = _make_board_with_component()
    annotations = _make_annotations(boxes=True)
    svg = render_pcb_svg(board, annotations=annotations)
    assert 'class="annotation-box"' in svg
    assert "annotation-pill" in svg
    assert "MCU" in svg


def test_annotation_pointer_rendered() -> None:
    """SVG should contain connector path and pill label."""
    board = _make_board_with_component()
    annotations = _make_annotations(pointers=True)
    svg = render_pcb_svg(board, annotations=annotations)
    assert "annotation-connector" in svg
    assert "annotation-dot" in svg
    assert "Clock" in svg


def test_annotation_legend_rendered() -> None:
    """SVG should contain legend box with title and entries."""
    board = _make_board_with_component()
    annotations = _make_annotations(legend=True)
    svg = render_pcb_svg(board, annotations=annotations)
    assert "legend-bg" in svg
    assert "SPI Signals" in svg
    assert "SCLK" in svg
    assert "#4488ff" in svg


def test_annotation_legend_text_only_entry() -> None:
    """Legend entries without a color should render text without a swatch."""
    board = _make_board_with_component()
    legend = ResolvedLegend(
        title="Notes",
        entries=[
            LegendEntry(color="", label="Bypass caps within 5mm"),
            LegendEntry(color="#ff0000", label="CLK"),
        ],
        x=5.0,
        y=22.0,
        width=10.0,
        height=4.0,
    )
    annotations = ResolvedAnnotations(
        boxes=[],
        pointers=[],
        labels=[],
        legend=legend,
        font_size=1.0,
        px_scale=0.025,
        content_bbox=(0, 0, 20, 20),
    )
    svg = render_pcb_svg(board, annotations=annotations)
    assert "Bypass caps within 5mm" in svg
    assert "CLK" in svg
    assert "#ff0000" in svg
    # Only one swatch rect (for CLK), none for the text-only entry
    assert svg.count("fill: #ff0000") == 1


def test_annotation_label_with_connector() -> None:
    """Label annotation should have a connector and the label content."""
    board = _make_board_with_component()
    annotations = _make_annotations(labels=True)
    svg = render_pcb_svg(board, annotations=annotations)
    assert "annotation-connector" in svg
    assert "Main MCU" in svg


def test_no_annotations_no_group() -> None:
    """Without annotations, no annotation group or style block should appear."""
    board = _make_board_with_component()
    svg = render_pcb_svg(board)
    assert "annotations" not in svg or "pcb-metadata" in svg
    assert '<style id="annotations">' not in svg


def test_annotation_css_present() -> None:
    """Annotation CSS block appears when annotations are provided."""
    board = _make_board_with_component()
    annotations = _make_annotations(boxes=True)
    svg = render_pcb_svg(board, annotations=annotations)
    assert '<style id="annotations">' in svg
    assert ".annotation-box" in svg


def test_viewbox_expands_for_annotations() -> None:
    """ViewBox should expand to include off-board annotation content."""
    board = _make_board_with_component()
    # Default viewBox is around (0,0)-(20,20) with 2mm padding
    svg_default = render_pcb_svg(board)
    # Annotation with content below the board
    annotations = ResolvedAnnotations(
        content_bbox=(-10.0, -10.0, 30.0, 40.0),
    )
    svg_annotated = render_pcb_svg(board, annotations=annotations)
    # Extract viewBox values
    vb_default = re.search(r'viewBox="([^"]+)"', svg_default)
    vb_annotated = re.search(r'viewBox="([^"]+)"', svg_annotated)
    assert vb_default is not None and vb_annotated is not None
    # Annotated viewBox should be larger
    def_vals = [float(x) for x in vb_default.group(1).split()]
    ann_vals = [float(x) for x in vb_annotated.group(1).split()]
    # Width and height should be larger
    assert ann_vals[2] > def_vals[2] or ann_vals[3] > def_vals[3]


def test_back_side_annotations_not_mirrored() -> None:
    """Annotations should render outside the mirror group."""
    board = _make_board_with_component()
    annotations = _make_annotations(boxes=True)
    svg = render_pcb_svg(board, side="back", annotations=annotations)
    # The annotation group element should appear after the mirror group
    scale_pos = svg.index("scale(-1")
    # Look for the <g class="annotations"> group, not the CSS class name
    annotation_group_pos = svg.index('class="annotations"')
    assert annotation_group_pos > scale_pos


def test_no_foreign_object() -> None:
    """Pure SVG rendering should not use foreignObject."""
    board = _make_board_with_component()
    annotations = _make_annotations(boxes=True, pointers=True, legend=True, labels=True)
    svg = render_pcb_svg(board, annotations=annotations)
    assert "<foreignObject" not in svg


# ---------------------------------------------------------------------------
# End-to-end: parse + resolve + render on real fixture
# ---------------------------------------------------------------------------


def test_swd_switch_annotation_end_to_end(board: Pcb) -> None:
    """Full annotation pipeline on a real board: parse → resolve → render."""
    from phosphor_eda.pcb_annotations import parse_annotations, resolve_annotations

    data = {
        "boxes": [{"targets": ["D1"], "label": "Status LED"}],
        "pointers": [{"target": "TP3", "label": "SWD Enable"}],
    }
    spec = parse_annotations(data)
    resolved = resolve_annotations(spec, board, "front")
    svg = render_pcb_svg(board, annotations=resolved)
    assert 'class="annotation-box"' in svg
    assert "Status LED" in svg
    assert "annotation-connector" in svg
    assert "SWD Enable" in svg


# ---------------------------------------------------------------------------
# parse_render_settings
# ---------------------------------------------------------------------------


class TestParseRenderSettings:
    def test_empty_object(self) -> None:
        settings = parse_render_settings({})
        assert settings.theme == ""
        assert settings.side == ""
        assert settings.width == 0
        assert settings.highlights == []
        assert settings.annotations == {}
        assert settings.custom_css == ""

    def test_all_fields(self) -> None:
        data = {
            "theme": "review",
            "side": "back",
            "width": 1200,
            "highlights": [
                {"net": "VBUS", "color": "#ff0000"},
                {"component": "U1"},
            ],
            "annotations": {"boxes": [{"targets": ["U1"], "label": "MCU"}]},
            "custom_css": ".board-fill { fill: red; }",
        }
        settings = parse_render_settings(data)
        assert settings.theme == "review"
        assert settings.side == "back"
        assert settings.width == 1200
        assert len(settings.highlights) == 2
        assert settings.highlights[0].net == "VBUS"
        assert settings.highlights[0].color == "#ff0000"
        assert settings.highlights[1].component == "U1"
        assert settings.highlights[1].color == ""
        assert settings.annotations == data["annotations"]
        assert settings.custom_css == ".board-fill { fill: red; }"

    def test_invalid_theme(self) -> None:
        with pytest.raises(ValueError, match="theme"):
            parse_render_settings({"theme": "neon"})

    def test_invalid_side(self) -> None:
        with pytest.raises(ValueError, match="side"):
            parse_render_settings({"side": "top"})

    def test_invalid_width(self) -> None:
        with pytest.raises(ValueError, match="width"):
            parse_render_settings({"width": -10})

    def test_highlight_missing_net_and_component(self) -> None:
        with pytest.raises(ValueError, match="must have 'net' or 'component'"):
            parse_render_settings({"highlights": [{"color": "#ff0000"}]})

    def test_highlight_both_net_and_component(self) -> None:
        with pytest.raises(ValueError, match="cannot have both"):
            parse_render_settings({"highlights": [{"net": "GND", "component": "U1"}]})

    def test_highlights_not_array(self) -> None:
        with pytest.raises(ValueError, match="highlights must be an array"):
            parse_render_settings({"highlights": "GND"})

    def test_annotations_not_object(self) -> None:
        with pytest.raises(ValueError, match="annotations must be an object"):
            parse_render_settings({"annotations": "bad"})

    def test_custom_css_not_string(self) -> None:
        with pytest.raises(ValueError, match="custom_css must be a string"):
            parse_render_settings({"custom_css": 42})


# ---------------------------------------------------------------------------
# Highlight colors
# ---------------------------------------------------------------------------


def test_highlight_net_with_color() -> None:
    """A highlight spec with a color applies that color to traces and pads."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(
        board,
        theme="review",
        highlight_specs=[HighlightSpec(net="SIG", color="#d4a843")],
    )
    assert 'style id="highlight"' in svg
    # The custom color should appear in the CSS
    assert "#d4a843" in svg
    # Traces and pads with the net should get the custom color
    assert "stroke: #d4a843 !important" in svg
    assert "fill: #d4a843 !important" in svg


def test_highlight_net_without_color_uses_layer_defaults() -> None:
    """A highlight spec without color falls back to per-layer copper colors."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(
        board,
        theme="review",
        highlight_specs=[HighlightSpec(net="SIG")],
    )
    assert 'style id="highlight"' in svg
    # Should have copper color rules, not a custom color
    assert "Restore vibrant copper colors" in svg


def test_highlight_mixed_colors_and_defaults() -> None:
    """Nets with colors get per-net rules; nets without get per-layer rules."""
    fp = PcbFootprint(
        reference="U1",
        footprint_lib="test",
        x=5.0,
        y=10.0,
        rotation=0.0,
        layer="F.Cu",
        pads=[
            PcbPad(
                number="1",
                x=5.0,
                y=10.0,
                width=1.0,
                height=1.0,
                shape="rect",
                layers=["F.Cu"],
                net_number=1,
                net_name="NET_A",
                footprint_ref="U1",
            ),
            PcbPad(
                number="2",
                x=7.0,
                y=10.0,
                width=1.0,
                height=1.0,
                shape="rect",
                layers=["F.Cu"],
                net_number=2,
                net_name="NET_B",
                footprint_ref="U1",
            ),
        ],
        fab_lines=[
            PcbLine(4, 9, 8, 9, "F.Fab", 0.1),
            PcbLine(8, 9, 8, 11, "F.Fab", 0.1),
            PcbLine(8, 11, 4, 11, "F.Fab", 0.1),
            PcbLine(4, 11, 4, 9, "F.Fab", 0.1),
        ],
    )
    board = Pcb(
        name="mixed-test",
        nets={0: PcbNet(0, ""), 1: PcbNet(1, "NET_A"), 2: PcbNet(2, "NET_B")},
        footprints=[fp],
        segments=[
            PcbSegment(5.0, 10.0, 10.0, 10.0, 0.25, "F.Cu", 1),
            PcbSegment(7.0, 10.0, 12.0, 10.0, 0.25, "F.Cu", 2),
        ],
        vias=[],
        outline_lines=[
            PcbLine(0, 0, 20, 0, "Edge.Cuts", 0.1),
            PcbLine(20, 0, 20, 20, "Edge.Cuts", 0.1),
            PcbLine(20, 20, 0, 20, "Edge.Cuts", 0.1),
            PcbLine(0, 20, 0, 0, "Edge.Cuts", 0.1),
        ],
        outline_arcs=[],
        layers=[
            PcbLayer("F.Cu", LayerFunction.COPPER, side="front"),
            PcbLayer("B.Cu", LayerFunction.COPPER, side="back"),
            PcbLayer("F.Fab", LayerFunction.FAB, side="front"),
        ],
    )
    svg = render_pcb_svg(
        board,
        theme="review",
        highlight_specs=[
            HighlightSpec(net="NET_A", color="#ff0000"),
            HighlightSpec(net="NET_B"),
        ],
    )
    # NET_A gets per-net color
    assert "stroke: #ff0000 !important" in svg
    # NET_B gets per-layer copper color rules
    assert "Restore vibrant copper colors" in svg


def test_highlight_component_with_color() -> None:
    """Component highlight with color applies to pads and body."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(
        board,
        theme="review",
        highlight_specs=[HighlightSpec(component="U1", color="#5b8abf")],
    )
    assert 'style id="highlight"' in svg
    assert "#5b8abf" in svg
    assert "fill: #5b8abf !important" in svg
    assert "stroke: #5b8abf !important" in svg


def test_highlight_specs_merge_with_flags() -> None:
    """highlight_specs merge with highlight_nets/highlight_components."""
    board = _make_board_with_inner_layers()
    svg = render_pcb_svg(
        board,
        theme="review",
        highlight_nets=["SIG"],
        highlight_specs=[HighlightSpec(component="U1")],
    )
    # Both net and component should be highlighted
    assert "Restore highlighted nets" in svg
    assert "Restore highlighted components" in svg
