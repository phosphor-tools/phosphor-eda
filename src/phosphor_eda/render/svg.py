"""Tiny SVG string builder shared by the render serializers.

No external dependencies — SVG is built via string formatting.
"""

from __future__ import annotations

from xml.sax.saxutils import escape as xml_escape


def fmt_attrs(attrs: dict[str, str] | None) -> str:
    """Format a dict of attributes into an SVG attribute string."""
    if not attrs:
        return ""
    return " " + " ".join(f'{k}="{xml_escape(v, {chr(34): "&quot;"})}"' for k, v in attrs.items())


class Svg:
    """Tiny SVG string builder with data-attribute support."""

    def __init__(self) -> None:
        self._parts: list[str] = []

    def raw(self, s: str) -> None:
        self._parts.append(s)

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        stroke_width: float,
        attrs: dict[str, str] | None = None,
    ) -> None:
        self._parts.append(
            "".join(
                (
                    f'<line x1="{x1:.4f}" y1="{y1:.4f}" x2="{x2:.4f}" y2="{y2:.4f}" ',
                    f'stroke-width="{stroke_width:.4f}"{fmt_attrs(attrs)}/>',
                )
            )
        )

    def circle(
        self,
        cx: float,
        cy: float,
        r: float,
        attrs: dict[str, str] | None = None,
    ) -> None:
        self._parts.append(f'<circle cx="{cx:.4f}" cy="{cy:.4f}" r="{r:.4f}"{fmt_attrs(attrs)}/>')

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        rx: float = 0,
        attrs: dict[str, str] | None = None,
    ) -> None:
        s = f'<rect x="{x:.4f}" y="{y:.4f}" width="{w:.4f}" height="{h:.4f}"'
        if rx > 0:
            s += f' rx="{rx:.4f}"'
        s += f"{fmt_attrs(attrs)}/>"
        self._parts.append(s)

    def polygon(
        self,
        points: list[tuple[float, float]],
        attrs: dict[str, str] | None = None,
    ) -> None:
        pts = " ".join(f"{x:.3f},{y:.3f}" for x, y in points)
        self._parts.append(f'<polygon points="{pts}"{fmt_attrs(attrs)}/>')

    def text(
        self,
        x: float,
        y: float,
        content: str,
        font_size: float,
        attrs: dict[str, str] | None = None,
        bold: bool = False,
        rotation: float = 0.0,
    ) -> None:
        weight = ' font-weight="bold"' if bold else ""
        rot = f' transform="rotate({rotation:.1f} {x:.4f} {y:.4f})"' if rotation else ""
        self._parts.append(
            "".join(
                (
                    f'<text x="{x:.4f}" y="{y:.4f}" font-size="{font_size:.2f}" ',
                    'text-anchor="middle" ',
                    'dominant-baseline="central" font-family="sans-serif"',
                    f"{weight}{rot}{fmt_attrs(attrs)}>",
                    f"{xml_escape(content)}</text>",
                )
            )
        )

    def group_start(
        self,
        transform: str | None = None,
        attrs: dict[str, str] | None = None,
    ) -> None:
        s = "<g"
        if transform:
            s += f' transform="{transform}"'
        s += f"{fmt_attrs(attrs)}>"
        self._parts.append(s)

    def group_end(self) -> None:
        self._parts.append("</g>")

    def path(self, d: str, attrs: dict[str, str] | None = None) -> None:
        self._parts.append(f'<path d="{d}"{fmt_attrs(attrs)}/>')

    def build(self) -> str:
        return "\n".join(self._parts)
