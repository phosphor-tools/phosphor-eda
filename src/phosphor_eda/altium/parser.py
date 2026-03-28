"""Parse Altium .SchDoc files into SchematicPage objects."""

from pathlib import Path

from phosphor_eda.altium.project import parse_prjpcb_file
from phosphor_eda.altium.record_parser import read_schematic_records
from phosphor_eda.models import (
    GraphicInst,
    ParsedDesign,
    PinConnection,
    PlacedInstance,
    SchematicPage,
    Wire,
)

# Altium SheetStyle -> size name mapping
_SHEET_SIZES = {
    "0": "A4",
    "1": "A3",
    "2": "A2",
    "3": "A1",
    "4": "A0",
    "5": "A",
    "6": "B",
    "7": "C",
    "8": "D",
    "9": "E",
    "10": "Letter",
    "11": "Legal",
    "12": "Tabloid",
    "13": "OrCAD-A",
    "14": "OrCAD-B",
    "15": "OrCAD-C",
    "16": "OrCAD-D",
    "17": "OrCAD-E",
}


def _int(props: dict[str, str], key: str, default: int = 0) -> int:
    """Get an integer property, returning default if missing or invalid."""
    val = props.get(key, "")
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def parse_schematic_sheet(schdoc_path: Path) -> SchematicPage:
    """Parse a single .SchDoc file into a SchematicPage."""
    records = read_schematic_records(str(schdoc_path))

    page = SchematicPage()
    page.name = schdoc_path.stem

    # Index records for parent-child lookups.
    # OwnerIndex in Altium is 0-based AFTER the header record (records[0]),
    # so OwnerIndex=N refers to records[N+1]. We store components keyed by
    # their OwnerIndex-compatible index (i - 1).
    components_by_index: dict[int, PlacedInstance] = {}
    # Active display mode per component (keyed by OwnerIndex-compatible index).
    # Altium components can have multiple display mode variants (Normal,
    # Small, etc.) with separate pin records per variant.  Only pins matching
    # the active DisplayMode should be included.
    component_display_mode: dict[int, int] = {}
    designators: list[dict[str, str]] = []
    pin_records: list[dict[str, str]] = []

    for i, rec in enumerate(records):
        rid = rec.get("record", "")

        if rid == "31":
            # Sheet metadata
            style = rec.get("sheetstyle", "10")
            if rec.get("usecustomsheet") == "T":
                cx = rec.get("customx", "?")
                cy = rec.get("customy", "?")
                page.size = f"Custom ({cx}x{cy})"
            else:
                page.size = _SHEET_SIZES.get(style, f"Style-{style}")

        elif rid == "1":
            # Component instance
            inst = PlacedInstance()
            inst.package_name = rec.get("libreference", "")
            inst.loc_x = _int(rec, "location.x")
            inst.loc_y = _int(rec, "location.y")
            components_by_index[i - 1] = inst
            component_display_mode[i - 1] = _int(rec, "displaymode")
            page.instances.append(inst)

        elif rid == "2":
            pin_records.append(rec)

        elif rid == "34":
            designators.append(rec)

        elif rid == "27":
            # Wire — may have multiple points
            wire = Wire()
            loc_count = _int(rec, "locationcount", 2)
            if loc_count >= 2:
                wire.start_x = _int(rec, "x1")
                wire.start_y = _int(rec, "y1")
                wire.end_x = _int(rec, "x2")
                wire.end_y = _int(rec, "y2")
            # Store all points for net resolution
            points: list[tuple[int, int]] = []
            for idx in range(1, loc_count + 1):
                x = _int(rec, f"x{idx}")
                y = _int(rec, f"y{idx}")
                points.append((x, y))
            wire._points = points  # type: ignore[attr-defined]
            page.wires.append(wire)

        elif rid == "17":
            # Power port -> global
            g = GraphicInst()
            g.name = rec.get("text", "")
            g.loc_x = _int(rec, "location.x")
            g.loc_y = _int(rec, "location.y")
            page.globals.append(g)

        elif rid == "18":
            # Port
            p = GraphicInst()
            p.name = rec.get("name", "")
            p.loc_x = _int(rec, "location.x")
            p.loc_y = _int(rec, "location.y")
            page.ports.append(p)

    # Assign designators to components
    for drec in designators:
        owner_idx = _int(drec, "ownerindex", -1)
        if owner_idx in components_by_index:
            components_by_index[owner_idx].reference = drec.get("text", "")

    # Assign pins to components.
    # Pin Location.X/Y is the body-side origin. The wire connects at the tip,
    # offset by PinLength in the direction given by PinConglomerate & 0x03:
    #   0=right (+X), 1=up (+Y), 2=left (-X), 3=down (-Y)
    for prec in pin_records:
        owner_idx = _int(prec, "ownerindex", -1)
        if owner_idx in components_by_index:
            # Skip pins belonging to inactive display mode variants.
            # A component with DisplayModeCount > 1 has separate pin records
            # for each visual variant (e.g. Normal vs Small symbol); only
            # pins matching the active DisplayMode are electrically relevant.
            pin_display_mode = _int(prec, "ownerpartdisplaymode")
            active_display_mode = component_display_mode.get(owner_idx, 0)
            if pin_display_mode != active_display_mode:
                continue

            pin = PinConnection()
            pin.pin_number = prec.get("designator", "")
            origin_x = _int(prec, "location.x")
            origin_y = _int(prec, "location.y")
            length = _int(prec, "pinlength")
            orientation = _int(prec, "pinconglomerate") & 0x03
            if orientation == 0:  # right
                pin.pin_x = origin_x + length
                pin.pin_y = origin_y
            elif orientation == 1:  # up
                pin.pin_x = origin_x
                pin.pin_y = origin_y + length
            elif orientation == 2:  # left
                pin.pin_x = origin_x - length
                pin.pin_y = origin_y
            else:  # down
                pin.pin_x = origin_x
                pin.pin_y = origin_y - length
            components_by_index[owner_idx].pin_connections.append(pin)

    # Store path for net resolution to re-read raw records
    page._schdoc_path = schdoc_path  # type: ignore[attr-defined]
    return page


def parse_altium(path: Path) -> ParsedDesign:
    """Parse an Altium project (.PrjPcb) or single sheet (.SchDoc).

    Returns a ParsedDesign with one SchematicPage per sheet.
    """
    design = ParsedDesign()

    if path.suffix.lower() == ".prjpcb":
        project = parse_prjpcb_file(str(path))
        project_dir = path.parent
        for rel_path in project.schematic_paths:
            schdoc = project_dir / rel_path
            if schdoc.exists():
                page = parse_schematic_sheet(schdoc)
                design.pages.append(page)
    else:
        page = parse_schematic_sheet(path)
        design.pages.append(page)

    return design
