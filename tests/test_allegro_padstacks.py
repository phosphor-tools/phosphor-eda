from __future__ import annotations

from pathlib import Path

from phosphor_eda.domain.pcb import PcbDrillPlating
from phosphor_eda.formats.allegro.padstacks import expand_allegro_padstack
from phosphor_eda.formats.allegro.parser import parse_allegro_records

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BREAKOUT_BOARD = (
    FIXTURES
    / "orcad"
    / "opencellular-breakout"
    / "allegro/OpenCellular/electronics/breakout/board"
    / "OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
)


def test_allegro_padstack_expansion_preserves_drill_and_copper_geometry() -> None:
    record_set = parse_allegro_records(BREAKOUT_BOARD.read_bytes(), source_name=BREAKOUT_BOARD.name)
    assert record_set.header is not None
    unit_to_mm = 0.0254 / record_set.header.unit_divisor
    padstack_record = next(
        record
        for record in record_set.records
        if record.tag == 0x1C
        and isinstance(record.payload.get("drill_size"), int)
        and record.payload["drill_size"] > 0
    )

    expanded = expand_allegro_padstack(
        padstack_record,
        name="fixture-padstack",
        unit_to_mm=unit_to_mm,
    )

    assert expanded.drill_diameter > 0.0
    assert expanded.stack.outer.size_x > 0.0
    assert expanded.stack.outer.size_y > 0.0
    assert expanded.plating in {
        PcbDrillPlating.PLATED,
        PcbDrillPlating.NON_PLATED,
        PcbDrillPlating.UNKNOWN,
    }
    assert expanded.metadata["native_padstack_key"] == str(padstack_record.key)
    assert expanded.metadata["native_component_count"] == str(
        padstack_record.payload["component_count"]
    )
