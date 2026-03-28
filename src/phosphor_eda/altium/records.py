"""Altium schematic record type definitions.

Record types sourced from the KiCad Altium importer (altium_parser_sch.h)
and the python-altium project (format.md).

Typed dataclasses provide structured access to raw record properties.
All coordinates use normalized Altium units (1 unit = 1/100 inch = 10 mils).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


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


# ---------------------------------------------------------------------------
# Typed record dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AltiumRecord:
    """Base for all typed Altium records."""

    record_type: RecordType
    index: int  # position in the flat record list
    owner_index: int = -1  # OwnerIndex value, -1 if not applicable


@dataclass
class HeaderRec(AltiumRecord):
    """RECORD=0 — sheet header / metadata."""


@dataclass
class ComponentRec(AltiumRecord):
    """RECORD=1 — component instance."""

    location: tuple[int, int] = (0, 0)
    lib_reference: str = ""
    unique_id: str = ""
    description: str = ""  # from %UTF8%ComponentDescription or ComponentDescription
    database_table: str = ""
    design_item_id: str = ""
    current_part_id: int = 1
    part_count: int = 1
    display_mode: int = 0  # active display mode (0=Normal, 1+=alternates)
    display_mode_count: int = 1  # total number of display mode variants
    orientation: int = 0  # 0=0°, 1=90°, 2=180°, 3=270°
    is_mirrored: bool = False


@dataclass
class PinRec(AltiumRecord):
    """RECORD=2 — component pin.

    ``location`` is the body-side origin. ``tip`` is the wire-connection
    point, computed from location + pin_length in the pin's orientation.
    """

    location: tuple[int, int] = (0, 0)
    pin_length: int = 0
    orientation: int = 0  # 0=right, 1=up, 2=left, 3=down
    designator: str = ""
    name: str = ""
    has_overline: bool = False  # True if name had overline markup (active-low)
    tip: tuple[int, int] = (0, 0)
    unique_id: str = ""
    electrical: int = 0  # 0=input, 1=IO, 2=output, 3=OC, 4=passive, 5=HiZ, 6=OE, 7=power
    owner_part_id: int = 0  # multi-part component part number
    owner_part_display_mode: int = 0  # display mode variant (0=Normal, 1+=alternates)


@dataclass
class SheetSymbolRec(AltiumRecord):
    """RECORD=15 — sheet symbol (hierarchical block)."""

    location: tuple[int, int] = (0, 0)
    x_size: int = 0
    y_size: int = 0


@dataclass
class SheetEntryRec(AltiumRecord):
    """RECORD=16 — entry on a sheet symbol.

    ``coord`` is computed from the parent SheetSymbol's location/size and
    this entry's Side/DistanceFromTop during the linking phase.
    """

    name: str = ""
    has_overline: bool = False  # True if name had overline markup
    side: int = 0  # 0=left, 1=right
    distance_from_top: int = 0  # normalized units
    harness_type: str = ""
    coord: tuple[int, int] = (0, 0)  # computed from parent
    io_type: int = 0  # 0=unspecified, 1=output, 2=input, 3=bidirectional


@dataclass
class PowerPortRec(AltiumRecord):
    """RECORD=17 — power port (global net connection)."""

    location: tuple[int, int] = (0, 0)
    text: str = ""
    has_overline: bool = False  # True if text had overline markup
    style: int = 0  # power port symbol style
    orientation: int = 0  # 0=right, 1=up, 2=left, 3=down
    show_net_name: bool = True


@dataclass
class PortRec(AltiumRecord):
    """RECORD=18 — port (cross-page connection).

    ``style`` controls port shape/orientation:
      0=none_horizontal, 1=left, 2=right, 3=left_right,
      4=none_vertical, 5=top, 6=bottom, 7=top_bottom.
    Styles 0-3 are horizontal; 4-7 are vertical.

    ``alignment`` controls text alignment (0=center, 1=right, 2=left).
    """

    location: tuple[int, int] = (0, 0)
    name: str = ""
    has_overline: bool = False  # True if name had overline markup
    harness_type: str = ""
    io_type: int = 0  # 0=unspecified, 1=output, 2=input, 3=bidirectional
    style: int = 0  # 0-3=horizontal, 4-7=vertical
    alignment: int = 0
    width: int = 0
    height: int = 0


@dataclass
class NoConnectRec(AltiumRecord):
    """RECORD=22 — no-connect marker."""

    location: tuple[int, int] = (0, 0)


@dataclass
class NetLabelRec(AltiumRecord):
    """RECORD=25 — net label."""

    location: tuple[int, int] = (0, 0)
    text: str = ""
    has_overline: bool = False  # True if text had overline markup


@dataclass
class WireRec(AltiumRecord):
    """RECORD=27 — wire (one or more axis-aligned segments).

    ``segments`` is derived from ``points``: each consecutive pair of
    points forms a segment.
    """

    points: list[tuple[int, int]] = field(default_factory=list)

    @property
    def segments(self) -> list[tuple[tuple[int, int], tuple[int, int]]]:
        return [(self.points[i], self.points[i + 1]) for i in range(len(self.points) - 1)]


@dataclass
class JunctionRec(AltiumRecord):
    """RECORD=29 — explicit junction marker."""

    location: tuple[int, int] = (0, 0)


@dataclass
class FileNameRec(AltiumRecord):
    """RECORD=33 — filename annotation on a sheet symbol."""

    text: str = ""


@dataclass
class DesignatorRec(AltiumRecord):
    """RECORD=34 — designator text (e.g., 'U1', 'R5')."""

    text: str = ""
    has_overline: bool = False  # True if text had overline markup


@dataclass
class ParameterRec(AltiumRecord):
    """RECORD=41 — parameter (key-value metadata on a component or sheet)."""

    name: str = ""
    text: str = ""
    has_overline: bool = False  # True if text had overline markup
    is_hidden: bool = False


@dataclass
class LabelRec(AltiumRecord):
    """RECORD=4 — text label (includes ``=PageTitle`` template variables)."""

    location: tuple[int, int] = (0, 0)
    text: str = ""
    has_overline: bool = False  # True if text had overline markup
    orientation: int = 0  # 0=0°, 1=90°, 2=180°, 3=270°


@dataclass
class TextFrameRec(AltiumRecord):
    """RECORD=28 — text frame (revision notes, block annotations).

    ``~1`` in text represents a newline.
    """

    location: tuple[int, int] = (0, 0)
    corner: tuple[int, int] = (0, 0)
    text: str = ""


@dataclass
class SheetRec(AltiumRecord):
    """RECORD=31 — sheet properties (size, style, template)."""

    sheet_style: int = 0
    use_custom_sheet: bool = False
    custom_x: int = 0
    custom_y: int = 0
    template_file_name: str = ""


@dataclass
class SheetNameRec(AltiumRecord):
    """RECORD=32 — sheet name label on a sheet symbol."""

    text: str = ""
    has_overline: bool = False  # True if text had overline markup


@dataclass
class ParameterSetRec(AltiumRecord):
    """RECORD=43 — parameter set anchor (net class annotations on wires)."""

    location: tuple[int, int] = (0, 0)
    name: str = ""
    style: int = 0
    orientation: int = 0


@dataclass
class ImplementationRec(AltiumRecord):
    """RECORD=45 — implementation model (PCB footprint, simulation, etc.)."""

    model_name: str = ""
    model_type: str = ""


@dataclass
class BlanketRec(AltiumRecord):
    """RECORD=225 — blanket (visual grouping rectangle)."""

    location: tuple[int, int] = (0, 0)
    corner: tuple[int, int] = (0, 0)


@dataclass
class HarnessConnectorRec(AltiumRecord):
    """RECORD=215 — signal harness connector."""

    location: tuple[int, int] = (0, 0)
    x_size: int = 0
    y_size: int = 0


@dataclass
class HarnessEntryRec(AltiumRecord):
    """RECORD=216 — entry on a harness connector.

    ``coord`` is computed from the parent HarnessConnector's location/size
    and this entry's Side/DistanceFromTop during the linking phase.
    """

    name: str = ""
    has_overline: bool = False  # True if name had overline markup
    side: int = 0
    distance_from_top: int = 0  # normalized units
    coord: tuple[int, int] = (0, 0)  # computed from parent


@dataclass
class HarnessTypeRec(AltiumRecord):
    """RECORD=217 — harness type label on a harness connector."""

    text: str = ""


@dataclass
class SignalHarnessRec(AltiumRecord):
    """RECORD=218 — signal harness wire."""

    points: list[tuple[int, int]] = field(default_factory=list)

    @property
    def segments(self) -> list[tuple[tuple[int, int], tuple[int, int]]]:
        return [(self.points[i], self.points[i + 1]) for i in range(len(self.points) - 1)]


@dataclass
class UnknownRecord(AltiumRecord):
    """Catch-all for record types we don't actively parse."""

    raw: dict[str, str] = field(default_factory=dict)
