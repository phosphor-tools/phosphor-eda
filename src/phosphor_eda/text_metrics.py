"""Text measurement and font embedding for SVG annotations.

Uses the bundled Inter-Regular.ttf font to compute text dimensions,
giving the SVG annotation renderer accurate bounding boxes for label
placement.  Also provides a base64-encoded subset of the font for
embedding in SVG ``@font-face`` rules, guaranteeing the rendered font
matches what we measure.
"""

from __future__ import annotations

import base64
import io
import re

from fontTools import subset as ft_subset  # pyright: ignore[reportMissingTypeStubs]
from fontTools.ttLib import TTFont  # pyright: ignore[reportMissingTypeStubs]

from phosphor_eda.fonts import INTER_REGULAR

# Compile once: matches any HTML tag
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# Matches <br> in any form: <br>, <br/>, <br />
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)

# Load font metrics once at import time.
# fontTools has no type stubs — all table access returns untyped objects.
# We extract typed constants at startup and use those throughout.
_font = TTFont(INTER_REGULAR)
_cmap: dict[int, str] = _font.getBestCmap() or {}  # pyright: ignore[reportAny]
_hmtx = _font["hmtx"]  # pyright: ignore[reportAny]

_head = _font["head"]  # pyright: ignore[reportAny]
_units_per_em: int = int(_head.unitsPerEm)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]

_os2 = _font["OS/2"]  # pyright: ignore[reportAny]
_ascender: int = int(_os2.sTypoAscender)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
_descender: int = abs(int(_os2.sTypoDescender))  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
_line_gap: int = int(_os2.sTypoLineGap)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
_LINE_HEIGHT_RATIO: float = (_ascender + _descender + _line_gap) / _units_per_em

# Vertical offset from the center of the text block to the baseline,
# as a fraction of font_size.  Used to vertically center a single line
# of text: baseline_y = center_y + BASELINE_CENTER_OFFSET * font_size
BASELINE_CENTER_OFFSET: float = (_ascender - _descender) / (2 * _units_per_em)


def _build_embedded_font() -> str:
    """Subset Inter-Regular to printable ASCII + Latin Extended, return base64.

    The subset is small (~50KB TTF, ~67KB base64) and is embedded in the
    SVG via @font-face so the rendered font exactly matches our metrics.
    """
    font = TTFont(INTER_REGULAR)
    chars = set(range(0x20, 0x7F))  # Basic ASCII
    chars.update(range(0x00C0, 0x0100))  # Latin Extended-A
    chars.update(ord(c) for c in "°µΩ±×÷≤≥≠←→↑↓•–—''")
    subsetter = ft_subset.Subsetter()  # pyright: ignore[reportUnknownMemberType]
    subsetter.populate(unicodes=chars)  # pyright: ignore[reportUnknownMemberType]
    subsetter.subset(font)  # pyright: ignore[reportUnknownMemberType]
    buf = io.BytesIO()
    font.save(buf)
    return base64.b64encode(buf.getvalue()).decode("ascii")


INTER_REGULAR_BASE64: str = _build_embedded_font()
"""Base64-encoded subset of Inter-Regular.ttf for SVG @font-face embedding."""


def _measure_line_width(text: str) -> int:
    """Measure a single line of plain text in font design units."""
    total: int = 0
    for char in text:
        glyph_id = _cmap.get(ord(char))
        if glyph_id is not None:
            advance: int = int(_hmtx[glyph_id][0])  # pyright: ignore[reportUnknownArgumentType]
            total += advance
        else:
            # Fallback: use space width for unknown glyphs
            space_id = _cmap.get(ord(" "))
            if space_id is not None:
                total += int(_hmtx[space_id][0])  # pyright: ignore[reportUnknownArgumentType]
    return total


def measure_text(text: str, font_size: float) -> tuple[float, float]:
    """Measure text dimensions in the same units as *font_size*.

    Handles ``<br>`` line breaks and strips HTML tags for width
    measurement.  Returns ``(width, height)`` where width is the
    widest line and height accounts for all lines.

    Parameters
    ----------
    text:
        Plain text or simple HTML (``<b>``, ``<br>``, etc.).
    font_size:
        Font size in output units (typically board mm).
    """
    # Split on <br> variants first
    lines = _BR_RE.split(text)
    num_lines = max(len(lines), 1)

    # Measure each line (strip HTML tags for character measurement)
    max_width_units = 0
    for line in lines:
        plain = _HTML_TAG_RE.sub("", line)
        width_units = _measure_line_width(plain)
        max_width_units = max(max_width_units, width_units)

    width = max_width_units * font_size / _units_per_em
    height = num_lines * _LINE_HEIGHT_RATIO * font_size

    return (width, height)
