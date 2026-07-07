"""Board coordinate conversion for Allegro records.

Allegro stores coordinates in native board units with a Y-up frame. The PCB
domain model uses millimeters with a Y-down frame, so every board coordinate is
scaled by ``unit_to_mm`` and every Y value is negated. ``BoardFrame`` is the one
place that negation lives; length-like quantities (widths, radii, drill sizes)
scale without a sign.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from phosphor_eda.formats.allegro.constants import allegro_unit_to_mm

if TYPE_CHECKING:
    from phosphor_eda.formats.allegro.records import AllegroHeader


@dataclass(frozen=True)
class BoardFrame:
    """Convert native Allegro coordinates into domain millimeters."""

    unit_to_mm: float

    def x(self, value: float | int) -> float:
        return value * self.unit_to_mm

    def y(self, value: float | int) -> float:
        return -(value * self.unit_to_mm)

    def length(self, value: float | int) -> float:
        return value * self.unit_to_mm

    def point(self, x: float | int, y: float | int) -> tuple[float, float]:
        return self.x(x), self.y(y)


def board_frame(header: AllegroHeader | None) -> BoardFrame | None:
    """Build the board frame for a header, or ``None`` when the header is absent."""
    if header is None:
        return None
    return BoardFrame(allegro_unit_to_mm(header.board_units, header.unit_divisor))
