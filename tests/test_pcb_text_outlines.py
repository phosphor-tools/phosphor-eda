from __future__ import annotations

from phosphor_eda.pcb import PcbTextGeometry
from phosphor_eda.text_outlines import text_outline_geometry


def test_text_outline_geometry_accepts_normalized_text_payload() -> None:
    outline = text_outline_geometry(PcbTextGeometry("U1", 10.0, 20.0, 0.0, 1.2))

    assert not outline.is_empty
    min_x, min_y, max_x, max_y = outline.bounds
    assert min_x < 10.0 < max_x
    assert min_y < 20.0 < max_y


def test_text_outline_rotation_changes_bounds() -> None:
    unrotated = text_outline_geometry(PcbTextGeometry("PCB", 10.0, 20.0, 0.0, 1.0))
    rotated = text_outline_geometry(PcbTextGeometry("PCB", 10.0, 20.0, 90.0, 1.0))

    assert unrotated.bounds != rotated.bounds
