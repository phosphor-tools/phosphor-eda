"""Altium schematic record type definitions.

Record types sourced from the KiCad Altium importer (altium_parser_sch.h)
and the python-altium project (format.md).

Typed dataclasses provide structured access to raw record properties.
All coordinates use normalized Altium units (1 unit = 1/100 inch = 10 mils).

Each record type has a ``from_properties()`` classmethod that parses a raw
property dict into a typed instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Self

from phosphor_eda.formats.altium._helpers import (
    compute_pin_tip,
    distance_from_top,
    prop_bool,
    prop_int,
    prop_location,
    prop_points,
    prop_str,
)
from phosphor_eda.formats.altium.enums import (
    LabelJustification,
    PinElectrical,
    PolylineStyle,
    PortIOType,
    PortStyle,
    PowerPortStyle,
    RecordOrientation,
    SheetEntrySide,
    SheetSize,
    TextFrameAlignment,
)
from phosphor_eda.formats.common.text import strip_overline

if TYPE_CHECKING:
    from phosphor_eda.formats.common.diagnostics import ParseContext


class RecordType(IntEnum):
    """Record types found in Altium .SchDoc FileHeader and Additional streams.

    Records 0-226 appear in the FileHeader stream (main schematic objects).
    Records 215-218 appear in the Additional stream (signal harness objects).
    """

    HEADER = 0
    COMPONENT = 1
    PIN = 2
    IEEE_SYMBOL = 3
    LABEL = 4
    BEZIER = 5
    POLYLINE = 6
    POLYGON = 7
    ELLIPSE = 8
    PIECHART = 9
    ROUND_RECTANGLE = 10
    ELLIPTICAL_ARC = 11
    ARC = 12
    LINE = 13
    RECTANGLE = 14
    SHEET_SYMBOL = 15
    SHEET_ENTRY = 16
    POWER_PORT = 17
    PORT = 18
    NO_ERC = 22
    NET_LABEL = 25
    BUS = 26
    WIRE = 27
    TEXT_FRAME = 28
    JUNCTION = 29
    IMAGE = 30
    SHEET = 31
    SHEET_NAME = 32
    FILE_NAME = 33
    DESIGNATOR = 34
    BUS_ENTRY = 37
    TEMPLATE = 39
    PARAMETER = 41
    PARAMETER_SET = 43
    IMPLEMENTATION_LIST = 44
    IMPLEMENTATION = 45
    MAP_DEFINER_LIST = 46
    MAP_DEFINER = 47
    IMPL_PARAMS = 48
    NOTE = 209
    COMPILE_MASK = 211
    HARNESS_CONNECTOR = 215
    HARNESS_ENTRY = 216
    HARNESS_TYPE = 217
    SIGNAL_HARNESS = 218
    BLANKET = 225
    HYPERLINK = 226
    # Sentinel for records the factory cannot classify (unknown or non-integer
    # RECORD field). Not a real Altium record id.
    UNKNOWN = -1


# ---------------------------------------------------------------------------
# Base and helpers
# ---------------------------------------------------------------------------

# Owner index for records in the Additional (harness) stream.
# Children often omit OwnerIndex, which defaults to 0 (first connector).
_HARNESS_OWNER_DEFAULT = 0


def _owner(props: dict[str, str], default: int = -1) -> int:
    return prop_int(props, "ownerindex", default)


def _overline_str(props: dict[str, str], key: str) -> tuple[str, bool]:
    """Read a string property and strip overline markup."""
    return strip_overline(props.get(key, ""))


def _parse_angle(props: dict[str, str], key: str) -> float:
    """Read an angle property as float (stored as integer degrees in some records)."""
    val = props.get(key, "0")
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# Typed record dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AltiumRecord:
    """Base for all typed Altium records."""

    record_type: RecordType
    index: int  # position in the flat record list
    owner_index: int = -1  # OwnerIndex value, -1 if not applicable

    @property
    def owner_key(self) -> int:
        """Key under which children reference this record by OwnerIndex.

        Altium stores a child's ``OwnerIndex`` as the parent's record position
        minus one, so a parent is keyed by ``index - 1``. Children join to it
        via their ``owner_index``.
        """
        return self.index - 1


@dataclass
class HeaderRec(AltiumRecord):
    """RECORD=0 — sheet header / metadata."""

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(record_type=RecordType.HEADER, index=index, owner_index=_owner(props))


@dataclass
class ComponentRec(AltiumRecord):
    """RECORD=1 — component instance."""

    location: tuple[int, int] = (0, 0)
    lib_reference: str = ""
    unique_id: str = ""
    description: str = ""
    database_table: str = ""
    design_item_id: str = ""
    current_part_id: int = 1
    part_count: int = 1
    display_mode: int = 0
    display_mode_count: int = 1
    orientation: RecordOrientation = RecordOrientation.RIGHTWARDS
    is_mirrored: bool = False

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], ctx: ParseContext) -> Self:
        desc = prop_str(props, "componentdescription", utf8=True)
        return cls(
            record_type=RecordType.COMPONENT,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            lib_reference=prop_str(props, "libreference"),
            unique_id=prop_str(props, "uniqueid"),
            description=desc,
            database_table=prop_str(props, "databasetablename"),
            design_item_id=prop_str(props, "designitemid"),
            current_part_id=prop_int(props, "currentpartid", 1),
            part_count=prop_int(props, "partcount", 1),
            display_mode=prop_int(props, "displaymode"),
            display_mode_count=prop_int(props, "displaymodecount", 1),
            orientation=ctx.require_enum(
                prop_int(props, "orientation"),
                RecordOrientation,
                "orientation",
                record_index=index,
                default=RecordOrientation.RIGHTWARDS,
            ),
            is_mirrored=prop_bool(props, "ismirrored"),
        )


@dataclass
class PinRec(AltiumRecord):
    """RECORD=2 — component pin.

    ``location`` is the body-side origin. ``tip`` is the wire-connection
    point, computed from location + pin_length in the pin's orientation.
    """

    location: tuple[int, int] = (0, 0)
    pin_length: int = 0
    orientation: RecordOrientation = RecordOrientation.RIGHTWARDS
    designator: str = ""
    name: str = ""
    has_overline: bool = False
    tip: tuple[int, int] = (0, 0)
    unique_id: str = ""
    electrical: PinElectrical | None = PinElectrical.PASSIVE
    owner_part_id: int = 0
    owner_part_display_mode: int = 0

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], ctx: ParseContext) -> Self:
        loc = prop_location(props)
        pin_length = prop_int(props, "pinlength")
        orientation_raw = prop_int(props, "pinconglomerate") & 0x03
        orientation = ctx.require_enum(
            orientation_raw,
            RecordOrientation,
            "orientation",
            record_index=index,
            default=RecordOrientation.RIGHTWARDS,
        )
        tip = compute_pin_tip(loc, pin_length, orientation_raw)
        pin_name, pin_ol = _overline_str(props, "name")
        electrical = ctx.require_enum(
            prop_int(props, "electrical"),
            PinElectrical,
            "electrical",
            record_index=index,
            default=None,
        )
        return cls(
            record_type=RecordType.PIN,
            index=index,
            owner_index=_owner(props),
            location=loc,
            pin_length=pin_length,
            orientation=orientation,
            designator=prop_str(props, "designator"),
            name=pin_name,
            has_overline=pin_ol,
            tip=tip,
            unique_id=prop_str(props, "uniqueid"),
            electrical=electrical,
            owner_part_id=prop_int(props, "ownerpartid"),
            owner_part_display_mode=prop_int(props, "ownerpartdisplaymode"),
        )


@dataclass
class IeeeSymbolRec(AltiumRecord):
    """RECORD=3 — IEEE symbol graphic."""

    location: tuple[int, int] = (0, 0)

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.IEEE_SYMBOL,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
        )


@dataclass
class LabelRec(AltiumRecord):
    """RECORD=4 — text label (includes ``=PageTitle`` template variables)."""

    location: tuple[int, int] = (0, 0)
    text: str = ""
    has_overline: bool = False
    orientation: RecordOrientation = RecordOrientation.RIGHTWARDS
    justification: LabelJustification = LabelJustification.BOTTOM_LEFT

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], ctx: ParseContext) -> Self:
        text, ol = _overline_str(props, "text")
        return cls(
            record_type=RecordType.LABEL,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            text=text,
            has_overline=ol,
            orientation=ctx.require_enum(
                prop_int(props, "orientation"),
                RecordOrientation,
                "orientation",
                record_index=index,
                default=RecordOrientation.RIGHTWARDS,
            ),
            justification=ctx.require_enum(
                prop_int(props, "justification"),
                LabelJustification,
                "justification",
                record_index=index,
                default=LabelJustification.BOTTOM_LEFT,
            ),
        )


@dataclass
class BezierRec(AltiumRecord):
    """RECORD=5 — Bézier curve."""

    points: list[tuple[int, int]] = field(default_factory=list)
    line_width: int = 0

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.BEZIER,
            index=index,
            owner_index=_owner(props),
            points=prop_points(props),
            line_width=prop_int(props, "linewidth"),
        )


@dataclass
class PolylineRec(AltiumRecord):
    """RECORD=6 — polyline."""

    points: list[tuple[int, int]] = field(default_factory=list)
    line_width: int = 0
    line_style: PolylineStyle = PolylineStyle.SOLID

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.POLYLINE,
            index=index,
            owner_index=_owner(props),
            points=prop_points(props),
            line_width=prop_int(props, "linewidth"),
            line_style=ctx.require_enum(
                prop_int(props, "linestyle"),
                PolylineStyle,
                "linestyle",
                record_index=index,
                default=PolylineStyle.SOLID,
            ),
        )


@dataclass
class PolygonRec(AltiumRecord):
    """RECORD=7 — filled polygon."""

    points: list[tuple[int, int]] = field(default_factory=list)
    line_width: int = 0
    is_solid: bool = False

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.POLYGON,
            index=index,
            owner_index=_owner(props),
            points=prop_points(props),
            line_width=prop_int(props, "linewidth"),
            is_solid=prop_bool(props, "issolid"),
        )


@dataclass
class EllipseRec(AltiumRecord):
    """RECORD=8 — ellipse."""

    location: tuple[int, int] = (0, 0)
    radius: int = 0
    secondary_radius: int = 0
    is_solid: bool = False
    line_width: int = 0

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.ELLIPSE,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            radius=prop_int(props, "radius"),
            secondary_radius=prop_int(props, "secondaryradius"),
            is_solid=prop_bool(props, "issolid"),
            line_width=prop_int(props, "linewidth"),
        )


@dataclass
class PieChartRec(AltiumRecord):
    """RECORD=9 — pie chart graphic."""

    location: tuple[int, int] = (0, 0)

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.PIECHART,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
        )


@dataclass
class RoundRectangleRec(AltiumRecord):
    """RECORD=10 — rounded rectangle."""

    location: tuple[int, int] = (0, 0)
    corner: tuple[int, int] = (0, 0)
    corner_x_radius: int = 0
    corner_y_radius: int = 0
    is_solid: bool = False
    line_width: int = 0

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.ROUND_RECTANGLE,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            corner=(prop_int(props, "corner.x"), prop_int(props, "corner.y")),
            corner_x_radius=prop_int(props, "cornerxradius"),
            corner_y_radius=prop_int(props, "corneryradius"),
            is_solid=prop_bool(props, "issolid"),
            line_width=prop_int(props, "linewidth"),
        )


@dataclass
class EllipticalArcRec(AltiumRecord):
    """RECORD=11 — elliptical arc."""

    location: tuple[int, int] = (0, 0)
    radius: int = 0
    secondary_radius: int = 0
    start_angle: float = 0.0
    end_angle: float = 0.0
    line_width: int = 0

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.ELLIPTICAL_ARC,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            radius=prop_int(props, "radius"),
            secondary_radius=prop_int(props, "secondaryradius"),
            start_angle=_parse_angle(props, "startangle"),
            end_angle=_parse_angle(props, "endangle"),
            line_width=prop_int(props, "linewidth"),
        )


@dataclass
class ArcRec(AltiumRecord):
    """RECORD=12 — circular arc."""

    location: tuple[int, int] = (0, 0)
    radius: int = 0
    start_angle: float = 0.0
    end_angle: float = 0.0
    line_width: int = 0

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.ARC,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            radius=prop_int(props, "radius"),
            start_angle=_parse_angle(props, "startangle"),
            end_angle=_parse_angle(props, "endangle"),
            line_width=prop_int(props, "linewidth"),
        )


@dataclass
class LineRec(AltiumRecord):
    """RECORD=13 — line segment."""

    location: tuple[int, int] = (0, 0)
    corner: tuple[int, int] = (0, 0)
    line_width: int = 0

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.LINE,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            corner=(prop_int(props, "corner.x"), prop_int(props, "corner.y")),
            line_width=prop_int(props, "linewidth"),
        )


@dataclass
class RectangleRec(AltiumRecord):
    """RECORD=14 — rectangle."""

    location: tuple[int, int] = (0, 0)
    corner: tuple[int, int] = (0, 0)
    line_width: int = 0
    is_solid: bool = False

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.RECTANGLE,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            corner=(prop_int(props, "corner.x"), prop_int(props, "corner.y")),
            line_width=prop_int(props, "linewidth"),
            is_solid=prop_bool(props, "issolid"),
        )


@dataclass
class SheetSymbolRec(AltiumRecord):
    """RECORD=15 — sheet symbol (hierarchical block)."""

    location: tuple[int, int] = (0, 0)
    x_size: int = 0
    y_size: int = 0

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.SHEET_SYMBOL,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            x_size=prop_int(props, "xsize"),
            y_size=prop_int(props, "ysize"),
        )


@dataclass
class SheetEntryRec(AltiumRecord):
    """RECORD=16 — entry on a sheet symbol.

    ``coord`` is computed from the parent SheetSymbol's location/size and
    this entry's Side/DistanceFromTop during the linking phase.
    """

    name: str = ""
    has_overline: bool = False
    side: SheetEntrySide = SheetEntrySide.LEFT
    distance_from_top: int = 0
    harness_type: str = ""
    coord: tuple[int, int] = (0, 0)
    io_type: PortIOType = PortIOType.UNSPECIFIED

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], ctx: ParseContext) -> Self:
        name, ol = _overline_str(props, "name")
        return cls(
            record_type=RecordType.SHEET_ENTRY,
            index=index,
            owner_index=_owner(props),
            name=name,
            has_overline=ol,
            side=ctx.require_enum(
                prop_int(props, "side"),
                SheetEntrySide,
                "side",
                record_index=index,
                default=SheetEntrySide.LEFT,
            ),
            distance_from_top=distance_from_top(props),
            harness_type=prop_str(props, "harnesstype"),
            io_type=ctx.require_enum(
                prop_int(props, "iotype"),
                PortIOType,
                "iotype",
                record_index=index,
                default=PortIOType.UNSPECIFIED,
            ),
        )


@dataclass
class PowerPortRec(AltiumRecord):
    """RECORD=17 — power port (global net connection)."""

    location: tuple[int, int] = (0, 0)
    text: str = ""
    has_overline: bool = False
    style: PowerPortStyle = PowerPortStyle.CIRCLE
    orientation: RecordOrientation = RecordOrientation.RIGHTWARDS
    show_net_name: bool = True

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], ctx: ParseContext) -> Self:
        text, ol = _overline_str(props, "text")
        return cls(
            record_type=RecordType.POWER_PORT,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            text=text,
            has_overline=ol,
            style=ctx.require_enum(
                prop_int(props, "style"),
                PowerPortStyle,
                "style",
                record_index=index,
                default=PowerPortStyle.CIRCLE,
            ),
            orientation=ctx.require_enum(
                prop_int(props, "orientation"),
                RecordOrientation,
                "orientation",
                record_index=index,
                default=RecordOrientation.RIGHTWARDS,
            ),
            show_net_name=props.get("shownetname", "").upper() != "F",
        )


@dataclass
class PortRec(AltiumRecord):
    """RECORD=18 — port (cross-page connection).

    ``style`` controls port shape/orientation:
      0=none_horizontal, 1=left, 2=right, 3=left_right,
      4=none_vertical, 5=top, 6=bottom, 7=top_bottom.
    Styles 0-3 are horizontal; 4-7 are vertical.
    """

    location: tuple[int, int] = (0, 0)
    name: str = ""
    has_overline: bool = False
    harness_type: str = ""
    io_type: PortIOType = PortIOType.UNSPECIFIED
    style: PortStyle = PortStyle.NONE_HORIZONTAL
    alignment: int = 0
    width: int = 0
    height: int = 0

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], ctx: ParseContext) -> Self:
        name, ol = _overline_str(props, "name")
        return cls(
            record_type=RecordType.PORT,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            name=name,
            has_overline=ol,
            harness_type=prop_str(props, "harnesstype"),
            io_type=ctx.require_enum(
                prop_int(props, "iotype"),
                PortIOType,
                "iotype",
                record_index=index,
                default=PortIOType.UNSPECIFIED,
            ),
            style=ctx.require_enum(
                prop_int(props, "style"),
                PortStyle,
                "style",
                record_index=index,
                default=PortStyle.NONE_HORIZONTAL,
            ),
            alignment=prop_int(props, "alignment"),
            width=prop_int(props, "width"),
            height=prop_int(props, "height"),
        )


@dataclass
class NoConnectRec(AltiumRecord):
    """RECORD=22 — no-connect marker."""

    location: tuple[int, int] = (0, 0)

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.NO_ERC,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
        )


@dataclass
class NetLabelRec(AltiumRecord):
    """RECORD=25 — net label."""

    location: tuple[int, int] = (0, 0)
    text: str = ""
    has_overline: bool = False

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        text, ol = _overline_str(props, "text")
        return cls(
            record_type=RecordType.NET_LABEL,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            text=text,
            has_overline=ol,
        )


@dataclass
class BusRec(AltiumRecord):
    """RECORD=26 — bus wire (multi-signal bundle)."""

    points: list[tuple[int, int]] = field(default_factory=list)

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.BUS,
            index=index,
            owner_index=_owner(props),
            points=prop_points(props),
        )

    @property
    def segments(self) -> list[tuple[tuple[int, int], tuple[int, int]]]:
        return [(self.points[i], self.points[i + 1]) for i in range(len(self.points) - 1)]


@dataclass
class WireRec(AltiumRecord):
    """RECORD=27 — wire (one or more axis-aligned segments).

    ``segments`` is derived from ``points``: each consecutive pair of
    points forms a segment.
    """

    points: list[tuple[int, int]] = field(default_factory=list)

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.WIRE,
            index=index,
            owner_index=_owner(props),
            points=prop_points(props),
        )

    @property
    def segments(self) -> list[tuple[tuple[int, int], tuple[int, int]]]:
        return [(self.points[i], self.points[i + 1]) for i in range(len(self.points) - 1)]


@dataclass
class TextFrameRec(AltiumRecord):
    """RECORD=28 — text frame (revision notes, block annotations).

    ``~1`` in text represents a newline.
    """

    location: tuple[int, int] = (0, 0)
    corner: tuple[int, int] = (0, 0)
    text: str = ""
    alignment: TextFrameAlignment = TextFrameAlignment.LEFT

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.TEXT_FRAME,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            corner=(prop_int(props, "corner.x"), prop_int(props, "corner.y")),
            text=prop_str(props, "text"),
            alignment=ctx.require_enum(
                prop_int(props, "alignment", 1),
                TextFrameAlignment,
                "alignment",
                record_index=index,
                default=TextFrameAlignment.LEFT,
            ),
        )


@dataclass
class JunctionRec(AltiumRecord):
    """RECORD=29 — explicit junction marker."""

    location: tuple[int, int] = (0, 0)

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.JUNCTION,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
        )


@dataclass
class ImageRec(AltiumRecord):
    """RECORD=30 — embedded image."""

    location: tuple[int, int] = (0, 0)
    corner: tuple[int, int] = (0, 0)
    filename: str = ""

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.IMAGE,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            corner=(prop_int(props, "corner.x"), prop_int(props, "corner.y")),
            filename=prop_str(props, "filename"),
        )


@dataclass
class SheetRec(AltiumRecord):
    """RECORD=31 — sheet properties (size, style, template)."""

    sheet_style: SheetSize = SheetSize.A4
    use_custom_sheet: bool = False
    custom_x: int = 0
    custom_y: int = 0
    template_file_name: str = ""

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.SHEET,
            index=index,
            owner_index=_owner(props),
            sheet_style=ctx.require_enum(
                prop_int(props, "sheetstyle"),
                SheetSize,
                "sheetstyle",
                record_index=index,
                default=SheetSize.A4,
            ),
            use_custom_sheet=prop_bool(props, "usecustomsheet"),
            custom_x=prop_int(props, "customx"),
            custom_y=prop_int(props, "customy"),
            template_file_name=prop_str(props, "templatefilename"),
        )


@dataclass
class SheetNameRec(AltiumRecord):
    """RECORD=32 — sheet name label on a sheet symbol."""

    text: str = ""
    has_overline: bool = False

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        text, ol = _overline_str(props, "text")
        return cls(
            record_type=RecordType.SHEET_NAME,
            index=index,
            owner_index=_owner(props),
            text=text,
            has_overline=ol,
        )


@dataclass
class FileNameRec(AltiumRecord):
    """RECORD=33 — filename annotation on a sheet symbol."""

    text: str = ""

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.FILE_NAME,
            index=index,
            owner_index=_owner(props),
            text=prop_str(props, "text"),
        )


@dataclass
class DesignatorRec(AltiumRecord):
    """RECORD=34 — designator text (e.g., 'U1', 'R5')."""

    text: str = ""
    has_overline: bool = False

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        text, ol = _overline_str(props, "text")
        return cls(
            record_type=RecordType.DESIGNATOR,
            index=index,
            owner_index=_owner(props),
            text=text,
            has_overline=ol,
        )


@dataclass
class BusEntryRec(AltiumRecord):
    """RECORD=37 — bus entry (connection between bus and wire)."""

    location: tuple[int, int] = (0, 0)
    corner: tuple[int, int] = (0, 0)

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.BUS_ENTRY,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            corner=(prop_int(props, "corner.x"), prop_int(props, "corner.y")),
        )


@dataclass
class TemplateRec(AltiumRecord):
    """RECORD=39 — schematic template reference."""

    filename: str = ""

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.TEMPLATE,
            index=index,
            owner_index=_owner(props),
            filename=prop_str(props, "filename"),
        )


@dataclass
class ParameterRec(AltiumRecord):
    """RECORD=41 — parameter (key-value metadata on a component or sheet)."""

    name: str = ""
    text: str = ""
    has_overline: bool = False
    is_hidden: bool = False

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        name, ol_name = _overline_str(props, "name")
        text, ol_text = _overline_str(props, "text")
        return cls(
            record_type=RecordType.PARAMETER,
            index=index,
            owner_index=_owner(props),
            name=name,
            text=text,
            has_overline=ol_name or ol_text,
            is_hidden=prop_bool(props, "ishidden"),
        )


@dataclass
class ParameterSetRec(AltiumRecord):
    """RECORD=43 — parameter set anchor (net class annotations on wires)."""

    location: tuple[int, int] = (0, 0)
    name: str = ""
    style: int = 0
    orientation: int = 0

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.PARAMETER_SET,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            name=prop_str(props, "name"),
            style=prop_int(props, "style"),
            orientation=prop_int(props, "orientation"),
        )


@dataclass
class ImplementationListRec(AltiumRecord):
    """RECORD=44 — implementation list container."""

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.IMPLEMENTATION_LIST,
            index=index,
            owner_index=_owner(props),
        )


@dataclass
class ImplementationRec(AltiumRecord):
    """RECORD=45 — implementation model (PCB footprint, simulation, etc.)."""

    model_name: str = ""
    model_type: str = ""

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.IMPLEMENTATION,
            index=index,
            owner_index=_owner(props),
            model_name=prop_str(props, "modelname"),
            model_type=prop_str(props, "modeltype"),
        )


@dataclass
class MapDefinerListRec(AltiumRecord):
    """RECORD=46 — map definer list container."""

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.MAP_DEFINER_LIST,
            index=index,
            owner_index=_owner(props),
        )


@dataclass
class MapDefinerRec(AltiumRecord):
    """RECORD=47 — map definer entry."""

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.MAP_DEFINER,
            index=index,
            owner_index=_owner(props),
        )


@dataclass
class ImplParamsRec(AltiumRecord):
    """RECORD=48 — implementation parameters."""

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.IMPL_PARAMS,
            index=index,
            owner_index=_owner(props),
        )


@dataclass
class NoteRec(AltiumRecord):
    """RECORD=209 — note annotation."""

    location: tuple[int, int] = (0, 0)
    text: str = ""

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.NOTE,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            text=prop_str(props, "text"),
        )


@dataclass
class CompileMaskRec(AltiumRecord):
    """RECORD=211 — compile mask (controls compilation visibility)."""

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.COMPILE_MASK,
            index=index,
            owner_index=_owner(props),
        )


@dataclass
class HarnessConnectorRec(AltiumRecord):
    """RECORD=215 — signal harness connector."""

    location: tuple[int, int] = (0, 0)
    x_size: int = 0
    y_size: int = 0

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.HARNESS_CONNECTOR,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            x_size=prop_int(props, "xsize"),
            y_size=prop_int(props, "ysize"),
        )


@dataclass
class HarnessEntryRec(AltiumRecord):
    """RECORD=216 — entry on a harness connector.

    ``coord`` is computed from the parent HarnessConnector's location/size
    and this entry's Side/DistanceFromTop during the linking phase.
    """

    name: str = ""
    has_overline: bool = False
    side: int = 0
    distance_from_top: int = 0
    coord: tuple[int, int] = (0, 0)

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        name, ol = _overline_str(props, "name")
        return cls(
            record_type=RecordType.HARNESS_ENTRY,
            index=index,
            owner_index=_owner(props, default=_HARNESS_OWNER_DEFAULT),
            name=name,
            has_overline=ol,
            side=prop_int(props, "side"),
            distance_from_top=distance_from_top(props),
        )


@dataclass
class HarnessTypeRec(AltiumRecord):
    """RECORD=217 — harness type label on a harness connector."""

    text: str = ""

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.HARNESS_TYPE,
            index=index,
            owner_index=_owner(props, default=_HARNESS_OWNER_DEFAULT),
            text=prop_str(props, "text"),
        )


@dataclass
class SignalHarnessRec(AltiumRecord):
    """RECORD=218 — signal harness wire."""

    points: list[tuple[int, int]] = field(default_factory=list)

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.SIGNAL_HARNESS,
            index=index,
            owner_index=_owner(props),
            points=prop_points(props),
        )

    @property
    def segments(self) -> list[tuple[tuple[int, int], tuple[int, int]]]:
        return [(self.points[i], self.points[i + 1]) for i in range(len(self.points) - 1)]


@dataclass
class BlanketRec(AltiumRecord):
    """RECORD=225 — blanket (visual grouping rectangle)."""

    location: tuple[int, int] = (0, 0)
    corner: tuple[int, int] = (0, 0)

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.BLANKET,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            corner=(prop_int(props, "corner.x"), prop_int(props, "corner.y")),
        )


@dataclass
class HyperlinkRec(AltiumRecord):
    """RECORD=226 — hyperlink annotation."""

    location: tuple[int, int] = (0, 0)
    url: str = ""

    @classmethod
    def from_properties(cls, index: int, props: dict[str, str], _ctx: ParseContext) -> Self:
        return cls(
            record_type=RecordType.HYPERLINK,
            index=index,
            owner_index=_owner(props),
            location=prop_location(props),
            url=prop_str(props, "url"),
        )


@dataclass
class UnknownRecord(AltiumRecord):
    """Catch-all for record types we don't actively parse.

    ``raw_record_id`` is the integer RECORD value when it parsed but matched no
    known type, or ``None`` when the RECORD field was missing or non-integer.
    """

    raw: dict[str, str] = field(default_factory=dict)
    raw_record_id: int | None = None
