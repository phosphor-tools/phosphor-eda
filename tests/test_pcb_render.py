"""Tests for the PCB SVG renderer — structural and CSS assertions."""

import json
import re
from pathlib import Path

import pytest

from phosphor_eda.kicad.pcb_parser import parse_kicad_pcb
from phosphor_eda.pcb import (
    LayerFunction,
    PcbBoard,
    PcbFootprint,
    PcbLayer,
    PcbLine,
    PcbModel3D,
    PcbNet,
)
from phosphor_eda.pcb_render import (
    _fmt_attrs,  # pyright: ignore[reportPrivateUsage]
    render_pcb_svg,
)

FIXTURE = Path(__file__).parent / "fixtures" / "swd_switch.kicad_pcb"
ORANGECRAB_FIXTURE = Path(__file__).parent / "fixtures" / "orangecrab.kicad_pcb"


@pytest.fixture(scope="module")
def board() -> PcbBoard:
    return parse_kicad_pcb(FIXTURE)


@pytest.fixture(scope="module")
def orangecrab_board() -> PcbBoard:
    return parse_kicad_pcb(ORANGECRAB_FIXTURE)


# ---------------------------------------------------------------------------
# Structure tests
# ---------------------------------------------------------------------------


def test_valid_svg(board: PcbBoard) -> None:
    svg = render_pcb_svg(board)
    assert svg.startswith("<svg")
    assert svg.strip().endswith("</svg>")


def test_has_theme_style(board: PcbBoard) -> None:
    svg = render_pcb_svg(board)
    assert '<style id="theme">' in svg


def test_has_board_clip(board: PcbBoard) -> None:
    svg = render_pcb_svg(board)
    assert "board-clip" in svg


def test_has_drill_clip(board: PcbBoard) -> None:
    svg = render_pcb_svg(board)
    assert "drill-clip" in svg


def test_has_copper_layer_groups(board: PcbBoard) -> None:
    svg = render_pcb_svg(board)
    assert 'data-layer="F.Cu"' in svg
    assert 'data-layer="B.Cu"' in svg


def test_layer_paint_order(board: PcbBoard) -> None:
    """B.Cu should appear before F.Cu in document order (painter's model)."""
    svg = render_pcb_svg(board)
    assert svg.index('data-layer="B.Cu"') < svg.index('data-layer="F.Cu"')


def test_silk_after_copper(board: PcbBoard) -> None:
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


def test_pad_attributes(board: PcbBoard) -> None:
    svg = render_pcb_svg(board)
    assert 'data-type="pad"' in svg
    assert "data-component=" in svg
    assert "data-pad=" in svg
    assert "data-net=" in svg


def test_trace_attributes(board: PcbBoard) -> None:
    """All traces are always present (visibility controlled via CSS)."""
    svg = render_pcb_svg(board)
    assert 'data-type="trace"' in svg
    assert "data-net-number=" in svg


def test_via_attributes(board: PcbBoard) -> None:
    svg = render_pcb_svg(board)
    assert 'data-type="via"' in svg
    assert 'class="via"' in svg


def test_zone_attributes(board: PcbBoard) -> None:
    """swd_switch has zones on inner copper layers."""
    svg = render_pcb_svg(board)
    assert 'data-type="zone"' in svg


def test_component_body_attributes(board: PcbBoard) -> None:
    svg = render_pcb_svg(board)
    assert 'data-type="body"' in svg


# ---------------------------------------------------------------------------
# Highlight tests
# ---------------------------------------------------------------------------


def test_highlight_adds_style(board: PcbBoard) -> None:
    svg = render_pcb_svg(board, highlight_nets=["VCC"])
    assert '<style id="highlight">' in svg


def test_highlight_css_targets_net(board: PcbBoard) -> None:
    """Highlight CSS should contain data-net-number selector for VCC (net 1)."""
    svg = render_pcb_svg(board, highlight_nets=["VCC"])
    assert 'data-net-number="1"' in svg


def test_highlight_component_restores_by_ref(board: PcbBoard) -> None:
    """Component highlight CSS restores elements by data-component, not net."""
    svg = render_pcb_svg(board, highlight_components=["TP3"])
    assert 'data-component="TP3"' in svg
    assert "Restore highlighted components" in svg


def test_highlight_component_does_not_highlight_nets(board: PcbBoard) -> None:
    """-c alone should not produce net-number restore rules."""
    svg = render_pcb_svg(board, highlight_components=["TP3"])
    assert "Restore highlighted nets" not in svg


def test_no_highlight_without_args(board: PcbBoard) -> None:
    svg = render_pcb_svg(board)
    assert '<style id="highlight">' not in svg


# ---------------------------------------------------------------------------
# Side tests
# ---------------------------------------------------------------------------


def test_back_mirror(board: PcbBoard) -> None:
    svg = render_pcb_svg(board, side="back")
    assert "scale(-1" in svg


def test_front_no_mirror(board: PcbBoard) -> None:
    svg = render_pcb_svg(board, side="front")
    assert "scale(-1" not in svg


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_unknown_net_no_error(board: PcbBoard) -> None:
    svg = render_pcb_svg(board, highlight_nets=["NONEXISTENT_NET_XYZ"])
    assert svg.startswith("<svg")


def test_unknown_component_no_error(board: PcbBoard) -> None:
    svg = render_pcb_svg(board, highlight_components=["NONEXISTENT"])
    assert svg.startswith("<svg")


def test_both_highlight_types(board: PcbBoard) -> None:
    svg = render_pcb_svg(board, highlight_nets=["GND"], highlight_components=["TP3"])
    assert svg.startswith("<svg")
    assert "TP3" in svg
    assert '<style id="highlight">' in svg


# ---------------------------------------------------------------------------
# OrangeCrab integration
# ---------------------------------------------------------------------------


def test_orangecrab_renders(orangecrab_board: PcbBoard) -> None:
    svg = render_pcb_svg(orangecrab_board)
    assert svg.startswith("<svg")
    assert svg.strip().endswith("</svg>")


def test_orangecrab_has_zones(orangecrab_board: PcbBoard) -> None:
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
) -> PcbBoard:
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
    return PcbBoard(
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
) -> PcbBoard:
    """Board with one footprint that has lib/value metadata and a pad + fab line."""
    from phosphor_eda.pcb import PcbPad

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
    return PcbBoard(
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


def test_swd_switch_has_footprint_lib_attr(board: PcbBoard) -> None:
    """Rendered SVG should contain data-footprint-lib for real footprints."""
    svg = render_pcb_svg(board)
    assert "data-footprint-lib=" in svg


def test_swd_switch_metadata_has_lib(board: PcbBoard) -> None:
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


def test_swd_switch_has_data_value(board: PcbBoard) -> None:
    """At least some elements should carry data-value for components with values."""
    svg = render_pcb_svg(board)
    assert "data-value=" in svg
