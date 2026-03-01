"""Materialize raw Altium record dicts into typed dataclasses.

The factory reads each raw ``dict[str, str]`` produced by ``record_parser``
and dispatches on the ``RECORD`` field to build the appropriate typed
dataclass from ``records.py``.

Coordinate normalization happens here: fractional fields like
``DistanceFromTop_Frac1`` are folded into standard Altium units.
"""

from __future__ import annotations

from ecad_tools.altium.records import (
    AltiumRecord,
    BlanketRec,
    ComponentRec,
    DesignatorRec,
    FileNameRec,
    HarnessConnectorRec,
    HarnessEntryRec,
    HarnessTypeRec,
    HeaderRec,
    ImplementationRec,
    JunctionRec,
    LabelRec,
    NetLabelRec,
    NoConnectRec,
    ParameterRec,
    ParameterSetRec,
    PinRec,
    PortRec,
    PowerPortRec,
    RecordType,
    SheetEntryRec,
    SheetNameRec,
    SheetRec,
    SheetSymbolRec,
    SignalHarnessRec,
    TextFrameRec,
    UnknownRecord,
    WireRec,
)

# DistanceFromTop fractional properties use 1/100000 resolution.
_FRAC_DENOM = 100_000


def strip_overline(name: str) -> tuple[str, bool]:
    """Strip Altium overline markup from a name.

    Altium toggles character-level overline with ``\\`` to indicate
    active-low signals (e.g., ``D\\R\\D\\Y\\`` = DRDY with overline).

    Returns ``(clean_name, had_overline)``.
    """
    if "\\" not in name:
        return name, False
    return name.replace("\\", ""), True


def _int(props: dict[str, str], key: str, default: int = 0) -> int:
    val = props.get(key, "")
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _distance_from_top(rec: dict[str, str]) -> int:
    """Compute DistanceFromTop in standard Altium units.

    DistanceFromTop is stored in x10 encoding (mils).
    The _Frac1 suffix adds sub-unit precision at 1/100000 resolution.
    """
    dist = _int(rec, "DistanceFromTop")
    frac = _int(rec, "DistanceFromTop_Frac1")
    return round(dist * 10 + frac / _FRAC_DENOM)


def _parse_points(rec: dict[str, str]) -> list[tuple[int, int]]:
    """Parse LocationCount / X1,Y1 / X2,Y2 / ... into a list of points."""
    loc_count = _int(rec, "LocationCount", 2)
    points: list[tuple[int, int]] = []
    for i in range(1, loc_count + 1):
        x = _int(rec, f"X{i}")
        y = _int(rec, f"Y{i}")
        points.append((x, y))
    return points


def _compute_pin_tip(
    location: tuple[int, int], pin_length: int, orientation: int,
) -> tuple[int, int]:
    """Compute pin wire-connection point from body origin + length + direction."""
    ox, oy = location
    if orientation == 0:  # right
        return (ox + pin_length, oy)
    elif orientation == 1:  # up
        return (ox, oy + pin_length)
    elif orientation == 2:  # left
        return (ox - pin_length, oy)
    else:  # down
        return (ox, oy - pin_length)


def _materialize_one(i: int, rec: dict[str, str]) -> AltiumRecord:
    """Convert a single raw record dict into a typed dataclass."""
    rid_str = rec.get("RECORD", "")
    owner = _int(rec, "OwnerIndex", -1)

    try:
        rid = int(rid_str)
    except (ValueError, TypeError):
        return UnknownRecord(
            record_type=RecordType.HEADER, index=i, owner_index=owner, raw=rec,
        )

    # Try to map to RecordType enum; fall back to UnknownRecord
    try:
        rt = RecordType(rid)
    except ValueError:
        return UnknownRecord(
            record_type=RecordType.HEADER, index=i, owner_index=owner, raw=rec,
        )

    if rt == RecordType.HEADER:
        return HeaderRec(record_type=rt, index=i, owner_index=owner)

    if rt == RecordType.COMPONENT:
        loc = (_int(rec, "Location.X"), _int(rec, "Location.Y"))
        desc = rec.get("%UTF8%ComponentDescription") or rec.get("ComponentDescription", "")
        return ComponentRec(
            record_type=rt, index=i, owner_index=owner,
            location=loc,
            lib_reference=rec.get("LibReference", ""),
            unique_id=rec.get("UniqueID", ""),
            description=desc,
            database_table=rec.get("DatabaseTableName", ""),
            design_item_id=rec.get("DesignItemId", ""),
            current_part_id=_int(rec, "CurrentPartId", 1),
            part_count=_int(rec, "PartCount", 1),
            display_mode=_int(rec, "DisplayMode"),
            display_mode_count=_int(rec, "DisplayModeCount", 1),
            orientation=_int(rec, "Orientation"),
            is_mirrored=rec.get("IsMirrored", "").upper() == "T",
        )

    if rt == RecordType.PIN:
        loc = (_int(rec, "Location.X"), _int(rec, "Location.Y"))
        pin_length = _int(rec, "PinLength")
        orientation = _int(rec, "PinConglomerate") & 0x03
        tip = _compute_pin_tip(loc, pin_length, orientation)
        pin_name, pin_ol = strip_overline(rec.get("Name", ""))
        return PinRec(
            record_type=rt, index=i, owner_index=owner,
            location=loc,
            pin_length=pin_length,
            orientation=orientation,
            designator=rec.get("Designator", ""),
            name=pin_name,
            has_overline=pin_ol,
            tip=tip,
            unique_id=rec.get("UniqueID", ""),
            electrical=_int(rec, "Electrical"),
            owner_part_id=_int(rec, "OwnerPartId"),
            owner_part_display_mode=_int(rec, "OwnerPartDisplayMode"),
        )

    if rt == RecordType.SHEET_SYMBOL:
        loc = (_int(rec, "Location.X"), _int(rec, "Location.Y"))
        return SheetSymbolRec(
            record_type=rt, index=i, owner_index=owner,
            location=loc,
            x_size=_int(rec, "XSize"),
            y_size=_int(rec, "YSize"),
        )

    if rt == RecordType.SHEET_ENTRY:
        entry_name, entry_ol = strip_overline(rec.get("Name", ""))
        return SheetEntryRec(
            record_type=rt, index=i, owner_index=owner,
            name=entry_name,
            has_overline=entry_ol,
            side=_int(rec, "Side"),
            distance_from_top=_distance_from_top(rec),
            harness_type=rec.get("HarnessType", ""),
            io_type=_int(rec, "IOType"),
            # coord is computed later during link_children
        )

    if rt == RecordType.POWER_PORT:
        loc = (_int(rec, "Location.X"), _int(rec, "Location.Y"))
        pp_text, pp_ol = strip_overline(rec.get("Text", ""))
        return PowerPortRec(
            record_type=rt, index=i, owner_index=owner,
            location=loc,
            text=pp_text,
            has_overline=pp_ol,
            style=_int(rec, "Style"),
            orientation=_int(rec, "Orientation"),
            show_net_name=rec.get("ShowNetName", "").upper() != "F",
        )

    if rt == RecordType.PORT:
        loc = (_int(rec, "Location.X"), _int(rec, "Location.Y"))
        port_name, port_ol = strip_overline(rec.get("Name", ""))
        return PortRec(
            record_type=rt, index=i, owner_index=owner,
            location=loc,
            name=port_name,
            has_overline=port_ol,
            harness_type=rec.get("HarnessType", ""),
            io_type=_int(rec, "IOType"),
            style=_int(rec, "Style"),
            alignment=_int(rec, "Alignment"),
            width=_int(rec, "Width"),
            height=_int(rec, "Height"),
        )

    if rt == RecordType.NO_ERC:
        loc = (_int(rec, "Location.X"), _int(rec, "Location.Y"))
        return NoConnectRec(
            record_type=rt, index=i, owner_index=owner,
            location=loc,
        )

    if rt == RecordType.NET_LABEL:
        loc = (_int(rec, "Location.X"), _int(rec, "Location.Y"))
        nl_text, nl_ol = strip_overline(rec.get("Text", ""))
        return NetLabelRec(
            record_type=rt, index=i, owner_index=owner,
            location=loc,
            text=nl_text,
            has_overline=nl_ol,
        )

    if rt == RecordType.WIRE:
        return WireRec(
            record_type=rt, index=i, owner_index=owner,
            points=_parse_points(rec),
        )

    if rt == RecordType.JUNCTION:
        loc = (_int(rec, "Location.X"), _int(rec, "Location.Y"))
        return JunctionRec(
            record_type=rt, index=i, owner_index=owner,
            location=loc,
        )

    if rt == RecordType.FILE_NAME:
        return FileNameRec(
            record_type=rt, index=i, owner_index=owner,
            text=rec.get("Text", ""),
        )

    if rt == RecordType.DESIGNATOR:
        desig_text, desig_ol = strip_overline(rec.get("Text", ""))
        return DesignatorRec(
            record_type=rt, index=i, owner_index=owner,
            text=desig_text,
            has_overline=desig_ol,
        )

    if rt == RecordType.LABEL:
        loc = (_int(rec, "Location.X"), _int(rec, "Location.Y"))
        lbl_text, lbl_ol = strip_overline(rec.get("Text", ""))
        return LabelRec(
            record_type=rt, index=i, owner_index=owner,
            location=loc,
            text=lbl_text,
            has_overline=lbl_ol,
            orientation=_int(rec, "Orientation"),
        )

    if rt == RecordType.TEXT_FRAME:
        loc = (_int(rec, "Location.X"), _int(rec, "Location.Y"))
        corner = (_int(rec, "Corner.X"), _int(rec, "Corner.Y"))
        return TextFrameRec(
            record_type=rt, index=i, owner_index=owner,
            location=loc,
            corner=corner,
            text=rec.get("Text", ""),
        )

    if rt == RecordType.SHEET:
        return SheetRec(
            record_type=rt, index=i, owner_index=owner,
            sheet_style=_int(rec, "SheetStyle"),
            use_custom_sheet=rec.get("UseCustomSheet", "").upper() == "T",
            custom_x=_int(rec, "CustomX"),
            custom_y=_int(rec, "CustomY"),
            template_file_name=rec.get("TemplateFileName", ""),
        )

    if rt == RecordType.SHEET_NAME:
        sn_text, sn_ol = strip_overline(rec.get("Text", ""))
        return SheetNameRec(
            record_type=rt, index=i, owner_index=owner,
            text=sn_text,
            has_overline=sn_ol,
        )

    if rt == RecordType.PARAMETER:
        param_name, ol_name = strip_overline(rec.get("Name", ""))
        param_text, ol_text = strip_overline(rec.get("Text", ""))
        return ParameterRec(
            record_type=rt, index=i, owner_index=owner,
            name=param_name,
            text=param_text,
            has_overline=ol_name or ol_text,
            is_hidden=rec.get("ISHIDDEN", "").upper() == "T",
        )

    if rt == RecordType.PARAMETER_SET:
        loc = (_int(rec, "Location.X"), _int(rec, "Location.Y"))
        return ParameterSetRec(
            record_type=rt, index=i, owner_index=owner,
            location=loc,
            name=rec.get("Name", ""),
            style=_int(rec, "Style"),
            orientation=_int(rec, "Orientation"),
        )

    if rt == RecordType.IMPLEMENTATION:
        return ImplementationRec(
            record_type=rt, index=i, owner_index=owner,
            model_name=rec.get("ModelName", ""),
            model_type=rec.get("ModelType", ""),
        )

    if rt == RecordType.BLANKET:
        loc = (_int(rec, "Location.X"), _int(rec, "Location.Y"))
        corner = (_int(rec, "Corner.X"), _int(rec, "Corner.Y"))
        return BlanketRec(
            record_type=rt, index=i, owner_index=owner,
            location=loc,
            corner=corner,
        )

    if rt == RecordType.HARNESS_CONNECTOR:
        loc = (_int(rec, "Location.X"), _int(rec, "Location.Y"))
        return HarnessConnectorRec(
            record_type=rt, index=i, owner_index=owner,
            location=loc,
            x_size=_int(rec, "XSize"),
            y_size=_int(rec, "YSize"),
        )

    if rt == RecordType.HARNESS_ENTRY:
        # Additional stream OwnerIndex defaults to 0 (not -1) because
        # the first connector's children often omit the field entirely.
        harness_owner = _int(rec, "OwnerIndex", 0)
        he_name, he_ol = strip_overline(rec.get("Name", ""))
        return HarnessEntryRec(
            record_type=rt, index=i, owner_index=harness_owner,
            name=he_name,
            has_overline=he_ol,
            side=_int(rec, "Side"),
            distance_from_top=_distance_from_top(rec),
            # coord is computed later during link_children
        )

    if rt == RecordType.HARNESS_TYPE:
        harness_owner = _int(rec, "OwnerIndex", 0)
        return HarnessTypeRec(
            record_type=rt, index=i, owner_index=harness_owner,
            text=rec.get("Text", ""),
        )

    if rt == RecordType.SIGNAL_HARNESS:
        return SignalHarnessRec(
            record_type=rt, index=i, owner_index=owner,
            points=_parse_points(rec),
        )

    # All other record types
    return UnknownRecord(
        record_type=rt, index=i, owner_index=owner, raw=rec,
    )


def materialize_records(raw_records: list[dict[str, str]]) -> list[AltiumRecord]:
    """Convert raw record dicts into typed dataclasses.

    Each record's ``index`` field corresponds to its position in the input
    list.  OwnerIndex references use the Altium convention: OwnerIndex=N
    refers to ``records[N+1]``, so the lookup key for a record at position
    ``i`` is ``i - 1``.
    """
    return [_materialize_one(i, rec) for i, rec in enumerate(raw_records)]


def _compute_entry_coord(
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
    Also computes ``coord`` for SheetEntryRec and HarnessEntryRec from
    their parent's location and size.
    """
    # Build index lookup: record.index → record (for owner resolution)
    # OwnerIndex=N refers to records[N+1], so the key is index - 1
    by_key: dict[int, AltiumRecord] = {}
    for rec in records:
        by_key[rec.index - 1] = rec

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
                rec.coord = _compute_entry_coord(
                    parent.location, parent.x_size,
                    rec.side, rec.distance_from_top,
                    parent.y_size,
                )

        elif isinstance(rec, HarnessEntryRec) and rec.owner_index >= 0:
            parent = by_key.get(rec.owner_index)
            if isinstance(parent, HarnessConnectorRec):
                rec.coord = _compute_entry_coord(
                    parent.location, parent.x_size,
                    rec.side, rec.distance_from_top,
                    parent.y_size,
                )

    return children
