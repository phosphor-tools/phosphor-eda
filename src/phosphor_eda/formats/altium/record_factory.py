"""Materialize raw Altium record dicts into typed dataclasses.

The factory reads each raw ``dict[str, str]`` produced by ``record_parser``
and dispatches on the ``RECORD`` field via a lookup table to build the
appropriate typed dataclass from ``records.py``.
"""

from __future__ import annotations

from collections.abc import Callable

from phosphor_eda.formats.altium._helpers import prop_int
from phosphor_eda.formats.altium.records import (
    AltiumRecord,
    ArcRec,
    BezierRec,
    BlanketRec,
    BusEntryRec,
    BusRec,
    CompileMaskRec,
    ComponentRec,
    DesignatorRec,
    EllipseRec,
    EllipticalArcRec,
    FileNameRec,
    HarnessConnectorRec,
    HarnessEntryRec,
    HarnessTypeRec,
    HeaderRec,
    HyperlinkRec,
    IeeeSymbolRec,
    ImageRec,
    ImplementationListRec,
    ImplementationRec,
    ImplParamsRec,
    JunctionRec,
    LabelRec,
    LineRec,
    MapDefinerListRec,
    MapDefinerRec,
    NetLabelRec,
    NoConnectRec,
    NoteRec,
    ParameterRec,
    ParameterSetRec,
    PieChartRec,
    PinRec,
    PolygonRec,
    PolylineRec,
    PortRec,
    PowerPortRec,
    RecordType,
    RectangleRec,
    RoundRectangleRec,
    SheetEntryRec,
    SheetNameRec,
    SheetRec,
    SheetSymbolRec,
    SignalHarnessRec,
    TemplateRec,
    TextFrameRec,
    UnknownRecord,
    WireRec,
)
from phosphor_eda.formats.common.diagnostics import ParseContext

# Factory callable: (index, props, ctx) → typed record.
_RecordFactory = Callable[[int, dict[str, str], ParseContext], AltiumRecord]

# RecordType → factory that parses a raw property dict.
_DISPATCH: dict[RecordType, _RecordFactory] = {
    RecordType.HEADER: HeaderRec.from_properties,
    RecordType.COMPONENT: ComponentRec.from_properties,
    RecordType.PIN: PinRec.from_properties,
    RecordType.IEEE_SYMBOL: IeeeSymbolRec.from_properties,
    RecordType.LABEL: LabelRec.from_properties,
    RecordType.BEZIER: BezierRec.from_properties,
    RecordType.POLYLINE: PolylineRec.from_properties,
    RecordType.POLYGON: PolygonRec.from_properties,
    RecordType.ELLIPSE: EllipseRec.from_properties,
    RecordType.PIECHART: PieChartRec.from_properties,
    RecordType.ROUND_RECTANGLE: RoundRectangleRec.from_properties,
    RecordType.ELLIPTICAL_ARC: EllipticalArcRec.from_properties,
    RecordType.ARC: ArcRec.from_properties,
    RecordType.LINE: LineRec.from_properties,
    RecordType.RECTANGLE: RectangleRec.from_properties,
    RecordType.SHEET_SYMBOL: SheetSymbolRec.from_properties,
    RecordType.SHEET_ENTRY: SheetEntryRec.from_properties,
    RecordType.POWER_PORT: PowerPortRec.from_properties,
    RecordType.PORT: PortRec.from_properties,
    RecordType.NO_ERC: NoConnectRec.from_properties,
    RecordType.NET_LABEL: NetLabelRec.from_properties,
    RecordType.BUS: BusRec.from_properties,
    RecordType.WIRE: WireRec.from_properties,
    RecordType.TEXT_FRAME: TextFrameRec.from_properties,
    RecordType.JUNCTION: JunctionRec.from_properties,
    RecordType.IMAGE: ImageRec.from_properties,
    RecordType.SHEET: SheetRec.from_properties,
    RecordType.SHEET_NAME: SheetNameRec.from_properties,
    RecordType.FILE_NAME: FileNameRec.from_properties,
    RecordType.DESIGNATOR: DesignatorRec.from_properties,
    RecordType.BUS_ENTRY: BusEntryRec.from_properties,
    RecordType.TEMPLATE: TemplateRec.from_properties,
    RecordType.PARAMETER: ParameterRec.from_properties,
    RecordType.PARAMETER_SET: ParameterSetRec.from_properties,
    RecordType.IMPLEMENTATION_LIST: ImplementationListRec.from_properties,
    RecordType.IMPLEMENTATION: ImplementationRec.from_properties,
    RecordType.MAP_DEFINER_LIST: MapDefinerListRec.from_properties,
    RecordType.MAP_DEFINER: MapDefinerRec.from_properties,
    RecordType.IMPL_PARAMS: ImplParamsRec.from_properties,
    RecordType.NOTE: NoteRec.from_properties,
    RecordType.COMPILE_MASK: CompileMaskRec.from_properties,
    RecordType.HARNESS_CONNECTOR: HarnessConnectorRec.from_properties,
    RecordType.HARNESS_ENTRY: HarnessEntryRec.from_properties,
    RecordType.HARNESS_TYPE: HarnessTypeRec.from_properties,
    RecordType.SIGNAL_HARNESS: SignalHarnessRec.from_properties,
    RecordType.BLANKET: BlanketRec.from_properties,
    RecordType.HYPERLINK: HyperlinkRec.from_properties,
}


def _materialize_one(i: int, rec: dict[str, str], ctx: ParseContext) -> AltiumRecord:
    """Convert a single raw record dict into a typed dataclass."""
    rid_str = rec.get("record", "")
    owner = prop_int(rec, "ownerindex", -1)

    try:
        rid = int(rid_str)
    except (ValueError, TypeError):
        ctx.warn(
            "invalid_record",
            f"Non-integer RECORD field: {rid_str!r}",
            record_index=i,
        )
        return UnknownRecord(
            record_type=RecordType.UNKNOWN,
            index=i,
            owner_index=owner,
            raw=rec,
            raw_record_id=None,
        )

    try:
        rt = RecordType(rid)
    except ValueError:
        ctx.warn(
            "unknown_record",
            f"Unknown record type: {rid}",
            record_index=i,
        )
        return UnknownRecord(
            record_type=RecordType.UNKNOWN,
            index=i,
            owner_index=owner,
            raw=rec,
            raw_record_id=rid,
        )

    factory = _DISPATCH.get(rt)
    if factory is None:
        return UnknownRecord(
            record_type=rt,
            index=i,
            owner_index=owner,
            raw=rec,
            raw_record_id=rid,
        )

    return factory(i, rec, ctx)


def materialize_records(
    raw_records: list[dict[str, str]],
    ctx: ParseContext | None = None,
) -> list[AltiumRecord]:
    """Convert raw record dicts into typed dataclasses.

    Each record's ``index`` field corresponds to its position in the input
    list.  OwnerIndex references use the Altium convention: OwnerIndex=N
    refers to ``records[N+1]``, so the lookup key for a record at position
    ``i`` is ``i - 1``.
    """
    if ctx is None:
        ctx = ParseContext()
    return [_materialize_one(i, rec, ctx) for i, rec in enumerate(raw_records)]


def compute_entry_coord(
    parent_location: tuple[int, int],
    parent_x_size: int,
    side: int,
    distance_from_top: int,
    parent_y_size: int = 0,
) -> tuple[int, int]:
    """Compute the wire-side coordinate for a sheet or harness entry.

    The parent symbol's ``location`` is its top-left corner (Altium Y-up).
    ``distance_from_top`` measures from the top edge for Left/Right sides,
    from the left edge for Top/Bottom sides.

    Matches the KiCad Altium importer (sch_io_altium.cpp):

    ======  ============================  ============================
    Side    X                             Y
    ======  ============================  ============================
    0 Left  parent.x                      parent.y - distance_from_top
    1 Right parent.x + x_size             parent.y - distance_from_top
    2 Top   parent.x + distance_from_top  parent.y
    3 Bot   parent.x + distance_from_top  parent.y - y_size
    ======  ============================  ============================
    """
    sx, sy = parent_location
    if side == 0:  # Left
        return (sx, sy - distance_from_top)
    if side == 1:  # Right
        return (sx + parent_x_size, sy - distance_from_top)
    if side == 2:  # Top
        return (sx + distance_from_top, sy)
    # side == 3: Bottom
    return (sx + distance_from_top, sy - parent_y_size)


def link_children(
    records: list[AltiumRecord],
) -> dict[int, list[AltiumRecord]]:
    """Group records by owner_index and compute derived coordinates.

    Returns a dict mapping owner record index → list of child records.
    Also computes ``coord`` for SheetEntryRec from its parent sheet
    symbol's location and size. Harness entries are not linked here:
    they live in the Additional stream, where OwnerIndex is relative to
    the Additional records — ``parse_harness_groups`` resolves them.
    """
    # Build index lookup: record.owner_key → record (for owner resolution).
    by_key: dict[int, AltiumRecord] = {}
    for rec in records:
        by_key[rec.owner_key] = rec

    # Group children by owner_index
    children: dict[int, list[AltiumRecord]] = {}
    for rec in records:
        if rec.owner_index >= 0:
            children.setdefault(rec.owner_index, []).append(rec)

    # Compute derived coordinates for entries
    for rec in records:
        if isinstance(rec, SheetEntryRec) and rec.owner_index >= 0:
            parent = by_key.get(rec.owner_index)
            if isinstance(parent, SheetSymbolRec):
                rec.coord = compute_entry_coord(
                    parent.location,
                    parent.x_size,
                    rec.side,
                    rec.distance_from_top,
                    parent.y_size,
                )

    return children
