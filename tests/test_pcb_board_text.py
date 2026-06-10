"""Board text renders as native, selectable ``<text>`` (plan 12).

These assert via the public render API: text content survives into the SVG
as real text elements, the embedded font subset covers the characters used
(including non-ASCII), and the rotation/mirror/justify transforms land in
the element attributes.
"""

from __future__ import annotations

import re

from conftest import build_render_test_board

from phosphor_eda.domain.pcb import (
    LayerRole,
    PcbArtwork,
    PcbArtworkKind,
    PcbArtworkPurpose,
    PcbLayer,
    PcbText,
)
from phosphor_eda.render.api import render_pcb_svg
from phosphor_eda.render.settings import (
    CliOverrides,
    RenderSettings,
    load_render_settings_json,
    resolve_effective_settings,
)

_BACK_SILK = PcbLayer("B.SilkS", (LayerRole.SILKSCREEN, LayerRole.BACK), number=34)


def _design_settings(*, side: str = "front") -> RenderSettings:
    base = load_render_settings_json('{"extends": "phosphor:design"}')
    return resolve_effective_settings(base, CliOverrides(side=side))


def _render_with_text(text: PcbText, *, layer: PcbLayer, side: str = "front") -> str:
    """Render the shared board with one silkscreen text artwork swapped in."""
    board = build_render_test_board()
    if layer.name not in {existing.name for existing in board.layers}:
        board.layers.append(layer)
    board.artwork = [
        PcbArtwork(
            id="text:probe",
            kind=PcbArtworkKind.TEXT,
            purpose=PcbArtworkPurpose.DESIGNATOR,
            layer=layer,
            data=text,
            footprint=board.footprints[0],
        )
    ]
    return render_pcb_svg(board, _design_settings(side=side)).svg


def _front_silk(board_layers: list[PcbLayer]) -> PcbLayer:
    return next(layer for layer in board_layers if layer.name == "F.SilkS")


def test_designator_renders_as_literal_selectable_text() -> None:
    """The whole point: a designator is searchable literal text, not polygons."""
    board = build_render_test_board()
    svg = render_pcb_svg(board, _design_settings()).svg
    # Shared board carries designator "U1" on F.SilkS.
    assert "<text" in svg
    assert ">U1<" in svg
    # The embedded face must be present so the text renders.
    assert "@font-face" in svg
    assert 'font-family: "InterEmbed"' in svg


def test_text_carries_data_attributes() -> None:
    board = build_render_test_board()
    svg = render_pcb_svg(board, _design_settings()).svg
    match = re.search(r"<text[^>]*>U1</text>", svg)
    assert match is not None
    element = match.group(0)
    for attr in (
        'data-text-kind="designator"',
        'data-component-ref="U1"',
        'data-kind="artwork"',
        'data-source-layer="F.SilkS"',
        'data-purpose="designator"',
    ):
        assert attr in element, f"missing {attr}"


def test_rotated_text_uses_rotate_transform() -> None:
    board = build_render_test_board()
    svg = _render_with_text(
        PcbText("R12", 5.0, 5.0, 90.0, 1.0),
        layer=_front_silk(board.layers),
    )
    match = re.search(r"<text[^>]*>R12</text>", svg)
    assert match is not None
    assert "rotate(90.0000 5.0000 5.0000)" in match.group(0)
    assert "scale(-1 1)" not in match.group(0)


def test_unrotated_text_has_no_transform() -> None:
    board = build_render_test_board()
    svg = _render_with_text(
        PcbText("R12", 5.0, 5.0, 0.0, 1.0),
        layer=_front_silk(board.layers),
    )
    match = re.search(r"<text[^>]*>R12</text>", svg)
    assert match is not None
    assert "transform=" not in match.group(0)


def test_back_side_text_is_mirrored_about_its_anchor() -> None:
    """Back-side text flips horizontally; the anchor stays fixed."""
    svg = _render_with_text(
        PcbText("R12", 5.0, 5.0, 0.0, 1.0),
        layer=_BACK_SILK,
        side="back",
    )
    match = re.search(r"<text[^>]*>R12</text>", svg)
    assert match is not None
    element = match.group(0)
    # translate to anchor, rotate, mirror x, translate back — anchor invariant.
    assert (
        "translate(5.0000 5.0000) rotate(0.0000) scale(-1 1) translate(-5.0000 -5.0000)" in element
    )


def test_back_side_text_composes_rotation_with_mirror() -> None:
    svg = _render_with_text(
        PcbText("R12", 5.0, 5.0, 45.0, 1.0),
        layer=_BACK_SILK,
        side="back",
    )
    match = re.search(r"<text[^>]*>R12</text>", svg)
    assert match is not None
    element = match.group(0)
    assert (
        "translate(5.0000 5.0000) rotate(45.0000) scale(-1 1) translate(-5.0000 -5.0000)" in element
    )


def test_left_justified_text_uses_start_anchor() -> None:
    board = build_render_test_board()
    svg = _render_with_text(
        PcbText("R12", 5.0, 5.0, 0.0, 1.0, justify="left"),
        layer=_front_silk(board.layers),
    )
    match = re.search(r"<text[^>]*>R12</text>", svg)
    assert match is not None
    assert 'text-anchor="start"' in match.group(0)


def test_right_justified_text_uses_end_anchor() -> None:
    board = build_render_test_board()
    svg = _render_with_text(
        PcbText("R12", 5.0, 5.0, 0.0, 1.0, justify="right"),
        layer=_front_silk(board.layers),
    )
    match = re.search(r"<text[^>]*>R12</text>", svg)
    assert match is not None
    assert 'text-anchor="end"' in match.group(0)


def test_unjustified_text_uses_middle_anchor() -> None:
    board = build_render_test_board()
    svg = _render_with_text(
        PcbText("R12", 5.0, 5.0, 0.0, 1.0),
        layer=_front_silk(board.layers),
    )
    match = re.search(r"<text[^>]*>R12</text>", svg)
    assert match is not None
    element = match.group(0)
    assert 'text-anchor="middle"' in element
    # Center anchor sits exactly at the authored x.
    assert 'x="5.0000"' in element


def test_left_and_right_anchors_shift_x_in_opposite_directions() -> None:
    board = build_render_test_board()
    left = _render_with_text(
        PcbText("R12", 5.0, 5.0, 0.0, 1.0, justify="left"),
        layer=_front_silk(board.layers),
    )
    right = _render_with_text(
        PcbText("R12", 5.0, 5.0, 0.0, 1.0, justify="right"),
        layer=_front_silk(board.layers),
    )
    # Start anchor sits left of center, end anchor right of center.
    assert _text_x(left) < 5.0 < _text_x(right)


def _text_x(svg: str) -> float:
    match = re.search(r'<text x="([-0-9.]+)"[^>]*>R12</text>', svg)
    assert match is not None
    return float(match.group(1))


def test_non_ascii_glyph_survives_font_subsetting() -> None:
    """A character outside the base subset (Ω) is added to the embedded face."""
    board = build_render_test_board()
    svg = _render_with_text(
        PcbText("100Ω", 5.0, 5.0, 0.0, 1.0),
        layer=_front_silk(board.layers),
    )
    assert ">100Ω<" in svg
    assert "@font-face" in svg


def test_non_subsettable_charset_falls_back_to_full_face() -> None:
    """A CJK-heavy label exceeds the subset cap and embeds the full face."""
    from phosphor_eda.geometry.text_metrics import (
        _full_face_base64,
        embedded_font_base64,
    )

    big_charset = frozenset(chr(0x4E00 + i) for i in range(600))
    assert embedded_font_base64(big_charset) == _full_face_base64()
