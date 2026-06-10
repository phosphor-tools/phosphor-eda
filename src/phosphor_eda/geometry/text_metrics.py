"""Text measurement and font embedding for SVG annotations and board text.

Uses the bundled Inter-Regular.ttf font to compute text dimensions,
giving the SVG renderer accurate bounding boxes for label placement.
Also provides a base64-encoded subset of the font for embedding in SVG
``@font-face`` rules, guaranteeing the rendered font matches what we
measure.

Font loading and subsetting are lazy (``functools.cache``): the font
file is only parsed when text actually needs measuring or embedding, and
each distinct glyph set is subset once.
"""

from __future__ import annotations

import base64
import functools
import io
import re

from fontTools import subset as ft_subset  # pyright: ignore[reportMissingTypeStubs]
from fontTools.ttLib import TTFont  # pyright: ignore[reportMissingTypeStubs]

from phosphor_eda.geometry.fonts import INTER_REGULAR

# Compile once: matches any HTML tag
HTML_TAG_RE = re.compile(r"<[^>]+>")
# Matches <br> in any form: <br>, <br/>, <br />
BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)

# Codepoints always present in the embedded subset, regardless of the
# glyphs actually rendered: printable ASCII, Latin Extended-A, and the
# symbols annotations commonly use.
_BASE_SUBSET_CHARS: frozenset[str] = frozenset(
    [chr(cp) for cp in range(0x20, 0x7F)]  # Basic ASCII
    + [chr(cp) for cp in range(0x00C0, 0x0100)]  # Latin Extended-A
    + list("°µΩ±×÷≤≥≠←→↑↓•–—''")
)

# Above this many distinct codepoints the subsetter's value (small output,
# fast build) breaks down — e.g. a board labeled in CJK would pull in
# thousands of glyphs. Past the cap we embed the full face once instead of
# repeatedly subsetting huge glyph sets.
_FULL_FACE_CHAR_CAP = 512


@functools.cache
def _load_font() -> TTFont:
    """Load the bundled Inter-Regular face once, lazily.

    fontTools has no type stubs — all table access returns untyped objects.
    Callers extract typed constants via the metric helpers below.
    """
    return TTFont(INTER_REGULAR)


@functools.cache
def _cmap() -> dict[int, str]:
    return _load_font().getBestCmap() or {}  # pyright: ignore[reportAny]


@functools.cache
def _units_per_em() -> int:
    head = _load_font()["head"]  # pyright: ignore[reportAny]
    return int(head.unitsPerEm)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]


@functools.cache
def _vertical_metrics() -> tuple[int, int, int]:
    """Return ``(ascender, descender, line_gap)`` in font design units."""
    os2 = _load_font()["OS/2"]  # pyright: ignore[reportAny]
    ascender = int(os2.sTypoAscender)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
    descender = abs(int(os2.sTypoDescender))  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
    line_gap = int(os2.sTypoLineGap)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
    return ascender, descender, line_gap


def _line_height_ratio() -> float:
    ascender, descender, line_gap = _vertical_metrics()
    return (ascender + descender + line_gap) / _units_per_em()


def baseline_center_offset() -> float:
    """Vertical offset from the center of a single text line to its baseline.

    Expressed as a fraction of ``font_size``: ``baseline_y = center_y +
    baseline_center_offset() * font_size`` vertically centers one line.
    Lazy so importers don't trigger font loading at import time.
    """
    ascender, descender, _ = _vertical_metrics()
    return (ascender - descender) / (2 * _units_per_em())


EMBEDDED_FONT_FAMILY = "InterEmbed"
"""``font-family`` name of the embedded face, shared by all SVG text."""


def embedded_font_base64(charset: frozenset[str] = frozenset()) -> str:
    """Return base64 of an Inter-Regular subset covering *charset*.

    The base subset (printable ASCII + Latin Extended + common symbols) is
    always included; *charset* adds any extra glyphs board text or labels
    actually use. Results are cached per glyph set, so repeated renders with
    the same characters reuse one subset.
    """
    return _embedded_font_base64(_BASE_SUBSET_CHARS | charset)


def embedded_font_css(charset: frozenset[str] = frozenset()) -> str:
    """Return an ``@font-face`` rule embedding the subset for *charset*.

    One rule serves every ``<text>`` element in the document — annotations
    and board text alike all reference :data:`EMBEDDED_FONT_FAMILY`.
    """
    data = embedded_font_base64(charset)
    return (
        f'@font-face {{ font-family: "{EMBEDDED_FONT_FAMILY}"; font-weight: 400;\n'
        f'  src: url("data:font/truetype;base64,{data}") format("truetype"); }}'
    )


@functools.cache
def _embedded_font_base64(chars: frozenset[str]) -> str:
    return _subset_font_base64(chars)


def _subset_font_base64(chars: frozenset[str]) -> str:
    """Subset the face to *chars* and return base64 (uncached).

    ``recalcTimestamp=False`` keeps the output stable across builds so the
    embedded data is deterministic; the cached wrapper avoids re-running this
    per render.
    """
    codepoints = {ord(c) for c in chars}
    # CJK and other large scripts would balloon the subset; past the cap we
    # embed the full face once rather than subsetting a huge glyph set.
    if len(codepoints) > _FULL_FACE_CHAR_CAP:
        return _full_face_base64()
    font = TTFont(INTER_REGULAR, recalcTimestamp=False)
    subsetter = ft_subset.Subsetter()  # pyright: ignore[reportUnknownMemberType]
    subsetter.populate(unicodes=sorted(codepoints))  # pyright: ignore[reportUnknownMemberType]
    subsetter.subset(font)  # pyright: ignore[reportUnknownMemberType]
    buf = io.BytesIO()
    font.save(buf)
    return base64.b64encode(buf.getvalue()).decode("ascii")


@functools.cache
def _full_face_base64() -> str:
    return base64.b64encode(INTER_REGULAR.read_bytes()).decode("ascii")


def _measure_line_width(text: str) -> int:
    """Measure a single line of plain text in font design units."""
    cmap = _cmap()
    hmtx = _load_font()["hmtx"]  # pyright: ignore[reportAny]
    total: int = 0
    for char in text:
        glyph_id = cmap.get(ord(char))
        if glyph_id is not None:
            advance: int = int(hmtx[glyph_id][0])  # pyright: ignore[reportUnknownArgumentType]
            total += advance
        else:
            # Fallback: use space width for unknown glyphs
            space_id = cmap.get(ord(" "))
            if space_id is not None:
                total += int(hmtx[space_id][0])  # pyright: ignore[reportUnknownArgumentType]
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
    lines = BR_RE.split(text)
    num_lines = max(len(lines), 1)

    # Measure each line (strip HTML tags for character measurement)
    max_width_units = 0
    for line in lines:
        plain = HTML_TAG_RE.sub("", line)
        width_units = _measure_line_width(plain)
        max_width_units = max(max_width_units, width_units)

    width = max_width_units * font_size / _units_per_em()
    height = num_lines * _line_height_ratio() * font_size

    return (width, height)
