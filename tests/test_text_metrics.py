"""Tests for text measurement using fonttools."""

import pytest
from fontTools.ttLib.tables import _h_e_a_d

from phosphor_eda.geometry.text_metrics import _build_embedded_font, measure_text


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


def test_embedded_font_subset_does_not_depend_on_generation_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_h_e_a_d, "timestampNow", lambda: 1)
    first = _build_embedded_font()

    monkeypatch.setattr(_h_e_a_d, "timestampNow", lambda: 2)
    second = _build_embedded_font()

    assert second == first
