"""Tests for text measurement using fonttools."""

import pytest
from fontTools.ttLib.tables import _h_e_a_d

from phosphor_eda.geometry.text_metrics import (
    _BASE_SUBSET_CHARS,
    GLYPH_FALLBACK_CHAR,
    _embedded_font_base64,
    _full_face_base64,
    _subset_font_base64,
    embedded_font_base64,
    measure_text,
    normalize_glyphs,
)


class TestMeasureText:
    def test_empty_string(self) -> None:
        w, h = measure_text("", 1.0)
        assert w == 0.0
        assert h > 0  # one line height even for empty

    def test_single_character(self) -> None:
        w, h = measure_text("A", 1.0)
        assert w > 0
        assert h > 0

    def test_width_scales_with_length(self) -> None:
        w1, _ = measure_text("A", 1.0)
        w5, _ = measure_text("AAAAA", 1.0)
        assert w5 == pytest.approx(w1 * 5, rel=0.01)

    def test_width_scales_with_font_size(self) -> None:
        w1, h1 = measure_text("Hello", 1.0)
        w2, h2 = measure_text("Hello", 2.0)
        assert w2 == pytest.approx(w1 * 2, rel=0.01)
        assert h2 == pytest.approx(h1 * 2, rel=0.01)

    def test_multiline_via_br(self) -> None:
        """<br> tags produce multiple lines — height grows, width is max line."""
        _, h1 = measure_text("Line one", 1.0)
        _, h2 = measure_text("Line one<br>Line two", 1.0)
        assert h2 > h1
        # Two lines should be roughly double one line
        assert h2 == pytest.approx(h1 * 2, rel=0.15)

    def test_multiline_width_uses_longest_line(self) -> None:
        w_short, _ = measure_text("Hi", 1.0)
        w_multi, _ = measure_text("Hi<br>Hello World", 1.0)
        w_long, _ = measure_text("Hello World", 1.0)
        assert w_multi == pytest.approx(w_long, rel=0.01)
        assert w_multi > w_short

    def test_html_tags_stripped(self) -> None:
        """HTML tags like <b> should not affect measured width."""
        w_plain, _ = measure_text("bold text", 1.0)
        w_tagged, _ = measure_text("<b>bold text</b>", 1.0)
        assert w_plain == pytest.approx(w_tagged, rel=0.01)

    def test_br_variants(self) -> None:
        """Various <br> spellings should all produce line breaks."""
        _, h_br = measure_text("A<br>B", 1.0)
        _, h_br_slash = measure_text("A<br/>B", 1.0)
        _, h_br_space = measure_text("A<br />B", 1.0)
        assert h_br == pytest.approx(h_br_slash, rel=0.01)
        assert h_br == pytest.approx(h_br_space, rel=0.01)

    def test_proportional_widths(self) -> None:
        """'W' should be wider than 'i' — proves per-glyph measurement."""
        w_wide, _ = measure_text("WWWWW", 1.0)
        w_narrow, _ = measure_text("iiiii", 1.0)
        assert w_wide > w_narrow

    def test_returns_reasonable_aspect_ratio(self) -> None:
        """A typical word should be wider than tall."""
        w, h = measure_text("Hello", 1.0)
        assert w > h


class TestNormalizeGlyphs:
    def test_supported_text_unchanged(self) -> None:
        assert normalize_glyphs("R12 100Ω ±5%") == "R12 100Ω ±5%"

    def test_unsupported_chars_become_fallback(self) -> None:
        # Inter-Regular has no CJK coverage.
        assert normalize_glyphs("A中B") == f"A{GLYPH_FALLBACK_CHAR}B"

    def test_fallback_char_is_renderable(self) -> None:
        """The fallback itself must have a glyph, or normalization is pointless."""
        assert normalize_glyphs(GLYPH_FALLBACK_CHAR) == GLYPH_FALLBACK_CHAR

    def test_whitespace_preserved(self) -> None:
        """Glyphless whitespace (newlines) collapses in SVG — never tofu."""
        assert normalize_glyphs("GND\nVREF") == "GND\nVREF"

    def test_glyphless_whitespace_measures_as_space(self) -> None:
        w_newline, _ = measure_text("A\nB", 1.0)
        w_space, _ = measure_text("A B", 1.0)
        assert w_newline == w_space

    def test_measurement_matches_normalized_text(self) -> None:
        """Missing glyphs measure exactly like the fallback char that renders."""
        w_raw, _ = measure_text("中中", 1.0)
        w_norm, _ = measure_text(GLYPH_FALLBACK_CHAR * 2, 1.0)
        assert w_raw == w_norm
        w_space, _ = measure_text("  ", 1.0)
        assert w_raw != w_space


def test_oversized_charsets_share_one_cache_entry() -> None:
    """Distinct over-cap charsets must not each memoize a full-face copy."""
    big_a = frozenset(chr(0x4E00 + i) for i in range(600))
    big_b = frozenset(chr(0x5E00 + i) for i in range(600))
    baseline = _embedded_font_base64.cache_info().currsize
    assert embedded_font_base64(big_a) == _full_face_base64()
    assert embedded_font_base64(big_b) == _full_face_base64()
    assert _embedded_font_base64.cache_info().currsize == baseline


def test_embedded_font_subset_does_not_depend_on_generation_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_h_e_a_d, "timestampNow", lambda: 1)
    first = _subset_font_base64(_BASE_SUBSET_CHARS)

    monkeypatch.setattr(_h_e_a_d, "timestampNow", lambda: 2)
    second = _subset_font_base64(_BASE_SUBSET_CHARS)

    assert second == first
