"""Altium Designer enum types ported from KiCad's Altium importer headers.

Schematic enums from eeschema/sch_io/altium/altium_parser_sch.h.
PCB enums from pcbnew/pcb_io/altium/altium_parser_pcb.h.
"""

from __future__ import annotations

from enum import IntEnum

# ---------------------------------------------------------------------------
# Schematic enums
# ---------------------------------------------------------------------------


class RecordOrientation(IntEnum):
    """Component/label rotation in 90-degree increments."""

    RIGHTWARDS = 0  # 0 degrees
    UPWARDS = 1  # 90 degrees
    LEFTWARDS = 2  # 180 degrees
    DOWNWARDS = 3  # 270 degrees


class PinElectrical(IntEnum):
    """Pin electrical type."""

    INPUT = 0
    BIDI = 1
    OUTPUT = 2
    OPEN_COLLECTOR = 3
    PASSIVE = 4
    TRISTATE = 5
    OPEN_EMITTER = 6
    POWER = 7


class PinSymbol(IntEnum):
    """Pin symbol decoration (edge or inner graphics).

    Some values are intentionally missing in the Altium format (7, 14-16, etc.).
    """

    NO_SYMBOL = 0
    NEGATED = 1
    RIGHTLEFT = 2
    CLOCK = 3
    LOW_INPUT = 4
    ANALOG_IN = 5
    NOLOGICCONNECT = 6
    POSTPONE_OUTPUT = 8
    OPEN_COLLECTOR = 9
    HIZ = 10
    HIGH_CURRENT = 11
    PULSE = 12
    SCHMITT = 13
    LOW_OUTPUT = 17
    OPEN_COLLECTOR_PULL_UP = 22
    OPEN_EMITTER = 23
    OPEN_EMITTER_PULL_UP = 24
    DIGITAL_IN = 25
    SHIFT_LEFT = 30
    OPEN_OUTPUT = 32
    LEFTRIGHT = 33
    BIDI = 34


class LabelJustification(IntEnum):
    """Text justification for labels and designators."""

    BOTTOM_LEFT = 0
    BOTTOM_CENTER = 1
    BOTTOM_RIGHT = 2
    CENTER_LEFT = 3
    CENTER_CENTER = 4
    CENTER_RIGHT = 5
    TOP_LEFT = 6
    TOP_CENTER = 7
    TOP_RIGHT = 8


class TextFrameAlignment(IntEnum):
    """Text alignment within a text frame."""

    LEFT = 1
    CENTER = 2
    RIGHT = 3


class PortAlignment(IntEnum):
    """Port text alignment."""

    CENTER = 0
    RIGHT = 1
    LEFT = 2


class PortIOType(IntEnum):
    """Port input/output direction."""

    UNSPECIFIED = 0
    OUTPUT = 1
    INPUT = 2
    BIDI = 3


class PortStyle(IntEnum):
    """Port visual style. Styles 0-3 are horizontal; 4-7 are vertical."""

    NONE_HORIZONTAL = 0
    LEFT = 1
    RIGHT = 2
    LEFT_RIGHT = 3
    NONE_VERTICAL = 4
    TOP = 5
    BOTTOM = 6
    TOP_BOTTOM = 7


class SheetEntrySide(IntEnum):
    """Which edge of a sheet symbol the entry sits on."""

    LEFT = 0
    RIGHT = 1
    TOP = 2
    BOTTOM = 3


class PowerPortStyle(IntEnum):
    """Power port symbol shape."""

    CIRCLE = 0
    ARROW = 1
    BAR = 2
    WAVE = 3
    POWER_GROUND = 4
    SIGNAL_GROUND = 5
    EARTH = 6
    GOST_ARROW = 7
    GOST_POWER_GROUND = 8
    GOST_EARTH = 9
    GOST_BAR = 10


class SheetSize(IntEnum):
    """Predefined sheet sizes (dimensions in Altium units)."""

    A4 = 0
    A3 = 1
    A2 = 2
    A1 = 3
    A0 = 4
    A = 5
    B = 6
    C = 7
    D = 8
    E = 9
    LETTER = 10
    LEGAL = 11
    TABLOID = 12
    ORCAD_A = 13
    ORCAD_B = 14
    ORCAD_C = 15
    ORCAD_D = 16
    ORCAD_E = 17


class SheetOrientation(IntEnum):
    """Sheet workspace orientation."""

    LANDSCAPE = 0
    PORTRAIT = 1


class PolylineStyle(IntEnum):
    """Line dash style for polylines and lines."""

    SOLID = 0
    DASHED = 1
    DOTTED = 2
    DASH_DOTTED = 3


# ---------------------------------------------------------------------------
# PCB enums
# ---------------------------------------------------------------------------


class AltiumLayer(IntEnum):
    """Altium PCB layer numbers."""

    TOP_LAYER = 1
    MID_LAYER_1 = 2
    MID_LAYER_2 = 3
    MID_LAYER_3 = 4
    MID_LAYER_4 = 5
    MID_LAYER_5 = 6
    MID_LAYER_6 = 7
    MID_LAYER_7 = 8
    MID_LAYER_8 = 9
    MID_LAYER_9 = 10
    MID_LAYER_10 = 11
    MID_LAYER_11 = 12
    MID_LAYER_12 = 13
    MID_LAYER_13 = 14
    MID_LAYER_14 = 15
    MID_LAYER_15 = 16
    MID_LAYER_16 = 17
    MID_LAYER_17 = 18
    MID_LAYER_18 = 19
    MID_LAYER_19 = 20
    MID_LAYER_20 = 21
    MID_LAYER_21 = 22
    MID_LAYER_22 = 23
    MID_LAYER_23 = 24
    MID_LAYER_24 = 25
    MID_LAYER_25 = 26
    MID_LAYER_26 = 27
    MID_LAYER_27 = 28
    MID_LAYER_28 = 29
    MID_LAYER_29 = 30
    MID_LAYER_30 = 31
    BOTTOM_LAYER = 32
    TOP_OVERLAY = 33
    BOTTOM_OVERLAY = 34
    TOP_PASTE = 35
    BOTTOM_PASTE = 36
    TOP_SOLDER = 37
    BOTTOM_SOLDER = 38
    INTERNAL_PLANE_1 = 39
    INTERNAL_PLANE_2 = 40
    INTERNAL_PLANE_3 = 41
    INTERNAL_PLANE_4 = 42
    INTERNAL_PLANE_5 = 43
    INTERNAL_PLANE_6 = 44
    INTERNAL_PLANE_7 = 45
    INTERNAL_PLANE_8 = 46
    INTERNAL_PLANE_9 = 47
    INTERNAL_PLANE_10 = 48
    INTERNAL_PLANE_11 = 49
    INTERNAL_PLANE_12 = 50
    INTERNAL_PLANE_13 = 51
    INTERNAL_PLANE_14 = 52
    INTERNAL_PLANE_15 = 53
    INTERNAL_PLANE_16 = 54
    DRILL_GUIDE = 55
    KEEP_OUT_LAYER = 56
    MECHANICAL_1 = 57
    MECHANICAL_2 = 58
    MECHANICAL_3 = 59
    MECHANICAL_4 = 60
    MECHANICAL_5 = 61
    MECHANICAL_6 = 62
    MECHANICAL_7 = 63
    MECHANICAL_8 = 64
    MECHANICAL_9 = 65
    MECHANICAL_10 = 66
    MECHANICAL_11 = 67
    MECHANICAL_12 = 68
    MECHANICAL_13 = 69
    MECHANICAL_14 = 70
    MECHANICAL_15 = 71
    MECHANICAL_16 = 72
    DRILL_DRAWING = 73
    MULTI_LAYER = 74
    CONNECTIONS = 75
    BACKGROUND = 76
    DRC_ERROR_MARKERS = 77
    SELECTIONS = 78
    VISIBLE_GRID_1 = 79
    VISIBLE_GRID_2 = 80
    PAD_HOLES = 81
    VIA_HOLES = 82


class PcbRecordType(IntEnum):
    """Binary record type tags in PCB streams."""

    ARC = 1
    PAD = 2
    VIA = 3
    TRACK = 4
    TEXT = 5
    FILL = 6
    REGION = 11
    MODEL = 12


class PadShape(IntEnum):
    """Pad shape (base, from the main geometry subrecord)."""

    UNKNOWN = 0
    CIRCLE = 1
    RECT = 2
    OCTAGONAL = 3


class PadShapeAlt(IntEnum):
    """Pad shape (per-layer override from sub6 records)."""

    UNKNOWN = 0
    CIRCLE = 1
    RECT = 2
    OCTAGONAL = 3
    ROUNDRECT = 9


class PadHoleShape(IntEnum):
    """Pad hole shape."""

    ROUND = 0
    SQUARE = 1
    SLOT = 2


class PadMode(IntEnum):
    """Pad stackup mode."""

    SIMPLE = 0
    TOP_MIDDLE_BOTTOM = 1
    FULL_STACK = 2


class RegionKind(IntEnum):
    """Region type (copper pour, cutout, etc.)."""

    COPPER = 0
    POLYGON_CUTOUT = 1
    DASHED_OUTLINE = 2
    UNKNOWN_3 = 3
    CAVITY_DEFINITION = 4
    BOARD_CUTOUT = 5


class RuleKind(IntEnum):
    """Design rule category."""

    CLEARANCE = 1
    DIFF_PAIR_ROUTINGS = 2
    HEIGHT = 3
    HOLE_SIZE = 4
    HOLE_TO_HOLE_CLEARANCE = 5
    WIDTH = 6
    PASTE_MASK_EXPANSION = 7
    SOLDER_MASK_EXPANSION = 8
    PLANE_CLEARANCE = 9
    POLYGON_CONNECT = 10
    ROUTING_VIAS = 11


class ConnectStyle(IntEnum):
    """Polygon-to-pad connection style."""

    UNKNOWN = 0
    DIRECT = 1
    RELIEF = 2
    NONE = 3


class PolygonHatchStyle(IntEnum):
    """Polygon fill pattern."""

    SOLID = 1
    DEGREE_45 = 2
    DEGREE_90 = 3
    HORIZONTAL = 4
    VERTICAL = 5
    NONE = 6


class TextType(IntEnum):
    """PCB text rendering type."""

    STROKE = 0
    TRUETYPE = 1
    BARCODE = 2


class MechKind(IntEnum):
    """Mechanical layer purpose (from Board6 LayerKindMapping)."""

    ASSEMBLY_TOP = 0x01
    ASSEMBLY_BOT = 0x02
    ASSEMBLY_NOTES = 0x03
    BOARD = 0x04
    COATING_TOP = 0x05
    COATING_BOT = 0x06
    COMPONENT_CENTER_TOP = 0x07
    COMPONENT_CENTER_BOT = 0x08
    COMPONENT_OUTLINE_TOP = 0x09
    COMPONENT_OUTLINE_BOT = 0x0A
    COURTYARD_TOP = 0x0B
    COURTYARD_BOT = 0x0C
    DESIGNATOR_TOP = 0x0D
    DESIGNATOR_BOT = 0x0E
    DIMENSIONS = 0x0F
    DIMENSIONS_TOP = 0x10
    DIMENSIONS_BOT = 0x11
    FAB_NOTES = 0x12
    GLUE_POINTS_TOP = 0x13
    GLUE_POINTS_BOT = 0x14
    GOLD_PLATING_TOP = 0x15
    GOLD_PLATING_BOT = 0x16
    VALUE_TOP = 0x17
    VALUE_BOT = 0x18
    V_CUT = 0x19
    BODY_3D_TOP = 0x1A
    BODY_3D_BOT = 0x1B
    ROUTE_TOOL_PATH = 0x1C
    SHEET = 0x1D
    BOARD_SHAPE = 0x1E
