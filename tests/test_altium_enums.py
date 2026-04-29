"""Tests for Altium enum types."""

from __future__ import annotations

import pytest

from phosphor_eda.altium.enums import (
    AltiumLayer,
    MechKind,
    PadShape,
    PadShapeAlt,
    PcbRecordType,
    PinElectrical,
    PinSymbol,
    PolygonHatchStyle,
    PolylineStyle,
    PortStyle,
    PowerPortStyle,
    RecordOrientation,
    RegionKind,
    SheetSize,
)

# ---------------------------------------------------------------------------
# Schematic enum values
# ---------------------------------------------------------------------------


class TestRecordOrientation:
    def test_rightwards(self) -> None:
        assert RecordOrientation.RIGHTWARDS == 0

    def test_downwards(self) -> None:
        assert RecordOrientation.DOWNWARDS == 3

    def test_from_int(self) -> None:
        assert RecordOrientation(1) == RecordOrientation.UPWARDS


class TestPinElectrical:
    def test_input(self) -> None:
        assert PinElectrical.INPUT == 0

    def test_power(self) -> None:
        assert PinElectrical.POWER == 7

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            PinElectrical(99)


class TestPinSymbol:
    def test_no_symbol(self) -> None:
        assert PinSymbol.NO_SYMBOL == 0

    def test_gap_at_7(self) -> None:
        """Value 7 is intentionally missing in the Altium format."""
        with pytest.raises(ValueError):
            PinSymbol(7)

    def test_postpone_output(self) -> None:
        assert PinSymbol.POSTPONE_OUTPUT == 8

    def test_bidi(self) -> None:
        assert PinSymbol.BIDI == 34


class TestPortStyle:
    def test_horizontal_range(self) -> None:
        for v in range(4):
            style = PortStyle(v)
            assert style.value < 4

    def test_vertical_range(self) -> None:
        assert PortStyle.NONE_VERTICAL == 4
        assert PortStyle.TOP_BOTTOM == 7


class TestPowerPortStyle:
    def test_circle(self) -> None:
        assert PowerPortStyle.CIRCLE == 0

    def test_gost_bar(self) -> None:
        assert PowerPortStyle.GOST_BAR == 10


class TestSheetSize:
    def test_a4(self) -> None:
        assert SheetSize.A4 == 0

    def test_orcad_e(self) -> None:
        assert SheetSize.ORCAD_E == 17


class TestPolylineStyle:
    def test_solid(self) -> None:
        assert PolylineStyle.SOLID == 0

    def test_dash_dotted(self) -> None:
        assert PolylineStyle.DASH_DOTTED == 3


# ---------------------------------------------------------------------------
# PCB enum values
# ---------------------------------------------------------------------------


class TestAltiumLayer:
    def test_top_layer(self) -> None:
        assert AltiumLayer.TOP_LAYER == 1

    def test_bottom_layer(self) -> None:
        assert AltiumLayer.BOTTOM_LAYER == 32

    def test_mid_layers_contiguous(self) -> None:
        for i in range(1, 31):
            layer = AltiumLayer(i + 1)
            assert layer.name == f"MID_LAYER_{i}"

    def test_mechanical_range(self) -> None:
        assert AltiumLayer.MECHANICAL_1 == 57
        assert AltiumLayer.MECHANICAL_16 == 72

    def test_internal_plane_range(self) -> None:
        assert AltiumLayer.INTERNAL_PLANE_1 == 39
        assert AltiumLayer.INTERNAL_PLANE_16 == 54

    def test_via_holes(self) -> None:
        assert AltiumLayer.VIA_HOLES == 82

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            AltiumLayer(999)


class TestPcbRecordType:
    def test_arc(self) -> None:
        assert PcbRecordType.ARC == 1

    def test_model(self) -> None:
        assert PcbRecordType.MODEL == 12


class TestPadShape:
    def test_circle(self) -> None:
        assert PadShape.CIRCLE == 1

    def test_roundrect_in_alt(self) -> None:
        assert PadShapeAlt.ROUNDRECT == 9


class TestRegionKind:
    def test_copper(self) -> None:
        assert RegionKind.COPPER == 0

    def test_board_cutout(self) -> None:
        assert RegionKind.BOARD_CUTOUT == 5


class TestMechKind:
    def test_assembly_top(self) -> None:
        assert MechKind.ASSEMBLY_TOP == 0x01

    def test_board_shape(self) -> None:
        assert MechKind.BOARD_SHAPE == 0x1E

    def test_courtyard_bot(self) -> None:
        assert MechKind.COURTYARD_BOT == 0x0C


class TestPolygonHatchStyle:
    def test_solid(self) -> None:
        assert PolygonHatchStyle.SOLID == 1

    def test_none(self) -> None:
        assert PolygonHatchStyle.NONE == 6
