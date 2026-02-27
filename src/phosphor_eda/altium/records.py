"""Altium schematic record type definitions.

Record types sourced from the KiCad Altium importer (altium_parser_sch.h)
and the python-altium project (format.md).
"""

from enum import IntEnum, auto


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
