"""Tests for Altium schematic record from_properties() classmethods and dispatch table."""

from phosphor_eda.formats.altium.enums import (
    LabelJustification,
    PinElectrical,
    PortIOType,
    PortStyle,
    PowerPortStyle,
    RecordOrientation,
    SheetEntrySide,
)
from phosphor_eda.formats.altium.record_factory import materialize_records
from phosphor_eda.formats.altium.records import (
    ArcRec,
    BezierRec,
    BusEntryRec,
    BusRec,
    ComponentRec,
    EllipseRec,
    ImageRec,
    LabelRec,
    LineRec,
    NoteRec,
    PinRec,
    PolygonRec,
    PolylineRec,
    PortRec,
    PowerPortRec,
    RecordType,
    RectangleRec,
    RoundRectangleRec,
    SheetEntryRec,
    SheetRec,
    UnknownRecord,
    WireRec,
)
from phosphor_eda.formats.common.diagnostics import ParseContext

# ---------------------------------------------------------------------------
# ComponentRec.from_properties
# ---------------------------------------------------------------------------


def test_component_from_properties():
    ctx = ParseContext()
    props = {
        "record": "1",
        "ownerindex": "0",
        "location.x": "500",
        "location.y": "300",
        "libreference": "STM32F405",
        "uniqueid": "ABC123",
        "%utf8%componentdescription": "MCU",
        "databasetablename": "Parts",
        "designitemid": "STM32F405RGT6",
        "currentpartid": "2",
        "partcount": "4",
        "displaymode": "1",
        "displaymodecount": "2",
        "orientation": "1",
        "ismirrored": "T",
    }
    rec = ComponentRec.from_properties(1, props, ctx)
    assert rec.location == (500, 300)
    assert rec.lib_reference == "STM32F405"
    assert rec.unique_id == "ABC123"
    assert rec.description == "MCU"
    assert rec.database_table == "Parts"
    assert rec.design_item_id == "STM32F405RGT6"
    assert rec.current_part_id == 2
    assert rec.part_count == 4
    assert rec.display_mode == 1
    assert rec.display_mode_count == 2
    assert rec.orientation == RecordOrientation.UPWARDS
    assert rec.is_mirrored is True
    assert rec.record_type == RecordType.COMPONENT
    assert rec.index == 1
    assert rec.owner_index == 0
    assert len(ctx.issues) == 0


def test_component_orientation_is_enum():
    ctx = ParseContext()
    props = {"record": "1", "orientation": "2"}
    rec = ComponentRec.from_properties(0, props, ctx)
    assert isinstance(rec.orientation, RecordOrientation)
    assert rec.orientation == RecordOrientation.LEFTWARDS


# ---------------------------------------------------------------------------
# PinRec.from_properties
# ---------------------------------------------------------------------------


def test_pin_from_properties():
    ctx = ParseContext()
    props = {
        "record": "2",
        "ownerindex": "3",
        "location.x": "100",
        "location.y": "200",
        "pinlength": "30",
        "pinconglomerate": "1",  # orientation=1 (up), low 2 bits
        "designator": "PA0",
        "name": "GPIO",
        "uniqueid": "XYZ",
        "electrical": "4",  # passive
        "ownerpartid": "1",
        "ownerpartdisplaymode": "0",
    }
    rec = PinRec.from_properties(5, props, ctx)
    assert rec.location == (100, 200)
    assert rec.pin_length == 30
    assert rec.orientation == RecordOrientation.UPWARDS
    # Tip: orientation 1 (up) → tip = (100, 200+30) = (100, 230)
    assert rec.tip == (100, 230)
    assert rec.designator == "PA0"
    assert rec.name == "GPIO"
    assert rec.has_overline is False
    assert rec.unique_id == "XYZ"
    assert rec.electrical == PinElectrical.PASSIVE
    assert rec.owner_part_id == 1
    assert rec.owner_part_display_mode == 0
    assert len(ctx.issues) == 0


def test_pin_tip_orientations():
    """Verify pin tip computation for all four orientations."""
    ctx = ParseContext()
    base = {"record": "2", "location.x": "100", "location.y": "100", "pinlength": "20"}

    # Right
    rec = PinRec.from_properties(0, {**base, "pinconglomerate": "0"}, ctx)
    assert rec.tip == (120, 100)

    # Up
    rec = PinRec.from_properties(0, {**base, "pinconglomerate": "1"}, ctx)
    assert rec.tip == (100, 120)

    # Left
    rec = PinRec.from_properties(0, {**base, "pinconglomerate": "2"}, ctx)
    assert rec.tip == (80, 100)

    # Down
    rec = PinRec.from_properties(0, {**base, "pinconglomerate": "3"}, ctx)
    assert rec.tip == (100, 80)


def test_pin_overline():
    """Pins with backslash-delimited names have overline stripped."""
    ctx = ParseContext()
    props = {"record": "2", "name": "R\\E\\S\\E\\T\\"}
    rec = PinRec.from_properties(0, props, ctx)
    assert rec.name == "RESET"
    assert rec.has_overline is True


# ---------------------------------------------------------------------------
# SheetEntryRec.from_properties
# ---------------------------------------------------------------------------


def test_sheet_entry_from_properties():
    ctx = ParseContext()
    props = {
        "record": "16",
        "ownerindex": "5",
        "name": "SPI_CLK",
        "side": "1",
        "distancefromtop": "4",
        "distancefromtop_frac1": "50000",
        "harnesstype": "",
        "iotype": "2",
    }
    rec = SheetEntryRec.from_properties(10, props, ctx)
    assert rec.name == "SPI_CLK"
    assert rec.has_overline is False
    assert rec.side == SheetEntrySide.RIGHT
    # distance = 4*10 + 50000/100000 = 40.5 → round = 40 (Python banker's)
    assert rec.distance_from_top == 40
    assert rec.io_type == PortIOType.INPUT
    assert len(ctx.issues) == 0


# ---------------------------------------------------------------------------
# PowerPortRec.from_properties
# ---------------------------------------------------------------------------


def test_power_port_from_properties():
    ctx = ParseContext()
    props = {
        "record": "17",
        "location.x": "50",
        "location.y": "60",
        "text": "GND",
        "style": "4",
        "orientation": "3",
        "shownetname": "F",
    }
    rec = PowerPortRec.from_properties(0, props, ctx)
    assert rec.location == (50, 60)
    assert rec.text == "GND"
    assert rec.style == PowerPortStyle.POWER_GROUND
    assert rec.orientation == RecordOrientation.DOWNWARDS
    assert rec.show_net_name is False
    assert len(ctx.issues) == 0


# ---------------------------------------------------------------------------
# WireRec.from_properties
# ---------------------------------------------------------------------------


def test_wire_from_properties():
    ctx = ParseContext()
    props = {
        "record": "27",
        "locationcount": "3",
        "x1": "10",
        "y1": "20",
        "x2": "30",
        "y2": "20",
        "x3": "30",
        "y3": "40",
    }
    rec = WireRec.from_properties(0, props, ctx)
    assert rec.points == [(10, 20), (30, 20), (30, 40)]
    assert rec.segments == [((10, 20), (30, 20)), ((30, 20), (30, 40))]


# ---------------------------------------------------------------------------
# PortRec.from_properties
# ---------------------------------------------------------------------------


def test_port_from_properties():
    ctx = ParseContext()
    props = {
        "record": "18",
        "location.x": "200",
        "location.y": "300",
        "name": "DATA_BUS",
        "iotype": "3",
        "style": "3",
        "alignment": "0",
        "width": "50",
        "height": "20",
    }
    rec = PortRec.from_properties(0, props, ctx)
    assert rec.name == "DATA_BUS"
    assert rec.io_type == PortIOType.BIDI
    assert rec.style == PortStyle.LEFT_RIGHT
    assert rec.width == 50
    assert rec.height == 20


# ---------------------------------------------------------------------------
# Unknown enum values → warning
# ---------------------------------------------------------------------------


def test_unknown_enum_value_warns():
    """Unknown enum values should produce a warning, not crash."""
    ctx = ParseContext()
    props = {"record": "17", "style": "99", "orientation": "7"}
    rec = PowerPortRec.from_properties(0, props, ctx)
    # Should have warnings for both unknown enum values
    assert len(ctx.issues) >= 2
    assert any("PowerPortStyle" in issue.message for issue in ctx.issues)
    assert any("RecordOrientation" in issue.message for issue in ctx.issues)
    # Should still return a valid record (with None fallback enum values)
    assert rec.record_type == RecordType.POWER_PORT


def test_unknown_pin_electrical_warns():
    ctx = ParseContext()
    props = {"record": "2", "electrical": "42"}
    rec = PinRec.from_properties(0, props, ctx)
    assert len(ctx.issues) == 1
    assert "PinElectrical" in ctx.issues[0].message
    assert rec.electrical is None


# ---------------------------------------------------------------------------
# Missing required keys → sensible defaults
# ---------------------------------------------------------------------------


def test_component_defaults():
    """Missing keys should produce sensible defaults, not crash."""
    ctx = ParseContext()
    props = {"record": "1"}
    rec = ComponentRec.from_properties(0, props, ctx)
    assert rec.location == (0, 0)
    assert rec.lib_reference == ""
    assert rec.current_part_id == 1
    assert rec.part_count == 1
    assert rec.orientation == RecordOrientation.RIGHTWARDS
    assert rec.is_mirrored is False


# ---------------------------------------------------------------------------
# Dispatch table covers RecordType members
# ---------------------------------------------------------------------------


def test_dispatch_table_covers_all_record_types():
    """Every RecordType member should be handled (typed record or UnknownRecord)."""
    ctx = ParseContext()
    for rt in RecordType:
        if rt is RecordType.UNKNOWN:
            continue  # sentinel for unclassifiable records, not a real type
        raw = [{"record": str(rt.value)}]
        records = materialize_records(raw, ctx)
        assert len(records) == 1
        # Every known RecordType should produce a typed record (not UnknownRecord)
        assert not isinstance(records[0], UnknownRecord), (
            f"RecordType.{rt.name} ({rt.value}) produced UnknownRecord — "
            f"add it to the dispatch table"
        )


def test_materialize_unknown_record_type():
    """Numeric RECORD values outside RecordType → UnknownRecord + warning."""
    ctx = ParseContext()
    raw = [{"record": "9999"}]
    records = materialize_records(raw, ctx)
    assert len(records) == 1
    assert isinstance(records[0], UnknownRecord)
    assert len(ctx.issues) == 1


def test_materialize_invalid_record_field():
    """Non-integer RECORD field → UnknownRecord + warning."""
    ctx = ParseContext()
    raw = [{"record": "abc"}]
    records = materialize_records(raw, ctx)
    assert len(records) == 1
    assert isinstance(records[0], UnknownRecord)
    assert len(ctx.issues) == 1


# ---------------------------------------------------------------------------
# New record types from KiCad
# ---------------------------------------------------------------------------


def test_bus_from_properties():
    ctx = ParseContext()
    props = {"record": "26", "locationcount": "2", "x1": "10", "y1": "20", "x2": "30", "y2": "20"}
    rec = BusRec.from_properties(0, props, ctx)
    assert rec.points == [(10, 20), (30, 20)]
    assert rec.record_type == RecordType.BUS


def test_bus_entry_from_properties():
    ctx = ParseContext()
    props = {
        "record": "37",
        "location.x": "10",
        "location.y": "20",
        "corner.x": "20",
        "corner.y": "30",
    }
    rec = BusEntryRec.from_properties(0, props, ctx)
    assert rec.location == (10, 20)
    assert rec.corner == (20, 30)


def test_polyline_from_properties():
    ctx = ParseContext()
    props = {
        "record": "6",
        "locationcount": "2",
        "x1": "10",
        "y1": "20",
        "x2": "30",
        "y2": "40",
        "linewidth": "2",
    }
    rec = PolylineRec.from_properties(0, props, ctx)
    assert rec.points == [(10, 20), (30, 40)]
    assert rec.line_width == 2


def test_polygon_from_properties():
    ctx = ParseContext()
    props = {
        "record": "7",
        "locationcount": "3",
        "x1": "0",
        "y1": "0",
        "x2": "10",
        "y2": "0",
        "x3": "5",
        "y3": "10",
        "linewidth": "1",
    }
    rec = PolygonRec.from_properties(0, props, ctx)
    assert len(rec.points) == 3
    assert rec.line_width == 1


def test_rectangle_from_properties():
    ctx = ParseContext()
    props = {
        "record": "14",
        "location.x": "10",
        "location.y": "20",
        "corner.x": "30",
        "corner.y": "40",
        "linewidth": "1",
        "issolid": "T",
    }
    rec = RectangleRec.from_properties(0, props, ctx)
    assert rec.location == (10, 20)
    assert rec.corner == (30, 40)
    assert rec.line_width == 1
    assert rec.is_solid is True


def test_line_from_properties():
    ctx = ParseContext()
    props = {
        "record": "13",
        "location.x": "0",
        "location.y": "0",
        "corner.x": "100",
        "corner.y": "100",
        "linewidth": "2",
    }
    rec = LineRec.from_properties(0, props, ctx)
    assert rec.location == (0, 0)
    assert rec.corner == (100, 100)
    assert rec.line_width == 2


def test_arc_from_properties():
    ctx = ParseContext()
    props = {
        "record": "12",
        "location.x": "50",
        "location.y": "50",
        "radius": "25",
        "startangle": "0",
        "endangle": "180",
        "linewidth": "1",
    }
    rec = ArcRec.from_properties(0, props, ctx)
    assert rec.location == (50, 50)
    assert rec.radius == 25
    assert rec.start_angle == 0.0
    assert rec.end_angle == 180.0


def test_ellipse_from_properties():
    ctx = ParseContext()
    props = {
        "record": "8",
        "location.x": "50",
        "location.y": "50",
        "radius": "20",
        "secondaryradius": "10",
        "issolid": "T",
    }
    rec = EllipseRec.from_properties(0, props, ctx)
    assert rec.location == (50, 50)
    assert rec.radius == 20
    assert rec.secondary_radius == 10
    assert rec.is_solid is True


def test_round_rectangle_from_properties():
    ctx = ParseContext()
    props = {
        "record": "10",
        "location.x": "10",
        "location.y": "20",
        "corner.x": "30",
        "corner.y": "40",
        "cornerxradius": "5",
        "corneryradius": "5",
        "issolid": "T",
    }
    rec = RoundRectangleRec.from_properties(0, props, ctx)
    assert rec.location == (10, 20)
    assert rec.corner == (30, 40)
    assert rec.corner_x_radius == 5
    assert rec.corner_y_radius == 5
    assert rec.is_solid is True


def test_image_from_properties():
    ctx = ParseContext()
    props = {
        "record": "30",
        "location.x": "100",
        "location.y": "200",
        "corner.x": "300",
        "corner.y": "400",
        "filename": "logo.bmp",
    }
    rec = ImageRec.from_properties(0, props, ctx)
    assert rec.location == (100, 200)
    assert rec.corner == (300, 400)
    assert rec.filename == "logo.bmp"


def test_note_from_properties():
    ctx = ParseContext()
    props = {
        "record": "209",
        "location.x": "10",
        "location.y": "20",
        "text": "Important note",
    }
    rec = NoteRec.from_properties(0, props, ctx)
    assert rec.location == (10, 20)
    assert rec.text == "Important note"


def test_bezier_from_properties():
    ctx = ParseContext()
    props = {
        "record": "5",
        "locationcount": "4",
        "x1": "0",
        "y1": "0",
        "x2": "10",
        "y2": "20",
        "x3": "20",
        "y3": "20",
        "x4": "30",
        "y4": "0",
        "linewidth": "1",
    }
    rec = BezierRec.from_properties(0, props, ctx)
    assert len(rec.points) == 4
    assert rec.line_width == 1


def test_label_justification_enum():
    ctx = ParseContext()
    props = {"record": "4", "text": "hello", "justification": "4"}
    rec = LabelRec.from_properties(0, props, ctx)
    assert rec.justification == LabelJustification.CENTER_CENTER


# ---------------------------------------------------------------------------
# Integration: materialize_records round-trip
# ---------------------------------------------------------------------------


def test_materialize_preserves_order():
    ctx = ParseContext()
    raw = [
        {"record": "31", "sheetstyle": "5"},
        {"record": "1", "libreference": "RES"},
        {"record": "27", "locationcount": "2", "x1": "0", "y1": "0", "x2": "10", "y2": "0"},
    ]
    records = materialize_records(raw, ctx)
    assert len(records) == 3
    assert isinstance(records[0], SheetRec)
    assert isinstance(records[1], ComponentRec)
    assert isinstance(records[2], WireRec)
    assert records[0].index == 0
    assert records[1].index == 1
    assert records[2].index == 2
    assert len(ctx.issues) == 0


def test_materialize_component_display_mode_fields() -> None:
    """ComponentRec should capture DisplayMode and DisplayModeCount from raw records."""
    raw = [
        {"record": "0"},
        {
            "record": "1",
            "location.x": "100",
            "location.y": "200",
            "libreference": "CAP",
            "partcount": "2",
            "currentpartid": "1",
            "displaymode": "0",
            "displaymodecount": "2",
        },
    ]
    records = materialize_records(raw)
    comp = records[1]
    assert isinstance(comp, ComponentRec)
    assert comp.display_mode == 0
    assert comp.display_mode_count == 2
    assert comp.part_count == 2
    assert comp.current_part_id == 1


def test_materialize_component_display_mode_defaults() -> None:
    """Missing DisplayMode/DisplayModeCount should default to 0/1."""
    raw = [
        {"record": "0"},
        {"record": "1", "location.x": "0", "location.y": "0"},
    ]
    records = materialize_records(raw)
    comp = records[1]
    assert isinstance(comp, ComponentRec)
    assert comp.display_mode == 0
    assert comp.display_mode_count == 1


def test_materialize_pin_owner_part_display_mode() -> None:
    """PinRec should capture OwnerPartDisplayMode from raw records."""
    raw = [
        {"record": "0"},
        {
            "record": "2",
            "location.x": "10",
            "location.y": "20",
            "pinlength": "30",
            "pinconglomerate": "0",
            "designator": "1",
            "ownerpartid": "1",
            "ownerpartdisplaymode": "1",
            "ownerindex": "0",
        },
    ]
    records = materialize_records(raw)
    pin = records[1]
    assert isinstance(pin, PinRec)
    assert pin.owner_part_display_mode == 1
    assert pin.owner_part_id == 1


def test_materialize_pin_owner_part_display_mode_default() -> None:
    """Missing OwnerPartDisplayMode should default to 0."""
    raw = [
        {"record": "0"},
        {
            "record": "2",
            "location.x": "0",
            "location.y": "0",
            "pinlength": "10",
            "pinconglomerate": "0",
            "designator": "A",
            "ownerindex": "0",
        },
    ]
    records = materialize_records(raw)
    pin = records[1]
    assert isinstance(pin, PinRec)
    assert pin.owner_part_display_mode == 0
