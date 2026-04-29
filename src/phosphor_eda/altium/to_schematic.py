"""Convert Altium schematics into the schematic domain model."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from phosphor_eda.altium.errors import ParseContext
from phosphor_eda.altium.project import parse_prjpcb_file
from phosphor_eda.altium.sheet_builder import (
    SheetRecords,
    build_page,
    collect_harness_port_nets,
    collect_harness_type_members,
    compute_harness_entry_coords,
    load_sheet,
    resolve_nets,
)
from phosphor_eda.schematic import Design, Page, merge_pages

if TYPE_CHECKING:
    from pathlib import Path


def load_project_sheets(
    path: Path,
    ctx: ParseContext | None = None,
) -> dict[str, SheetRecords]:
    """Load all schematic sheets from a .PrjPcb project or single .SchDoc.

    Returns an ordered dict mapping sheet name → SheetRecords.  For
    ``.PrjPcb`` files, sheets are loaded in project file order.  Missing
    sheets print a warning to stderr and are skipped.
    """
    if ctx is None:
        ctx = ParseContext()
    sheets: dict[str, SheetRecords] = {}

    if path.suffix.lower() == ".prjpcb":
        project = parse_prjpcb_file(str(path))
        project_dir = path.parent
        for rel_path in project.schematic_paths:
            # Altium stores paths with Windows backslashes; normalize so
            # pathlib treats separators correctly on all platforms.
            schdoc = project_dir / rel_path.replace("\\", "/")
            if schdoc.exists():
                sheet = load_sheet(str(schdoc), ctx=ctx)
                sheets[sheet.name] = sheet
            else:
                ctx.warn(
                    "missing_sheet",
                    f"Schematic sheet not found: {rel_path} (resolved to {schdoc})",
                )
                print(
                    f"Warning: schematic sheet not found: {rel_path} (resolved to {schdoc})",
                    file=sys.stderr,
                )
    else:
        sheet = load_sheet(str(path), ctx=ctx)
        sheets[sheet.name] = sheet

    return sheets


def altium_to_design(path: Path, name: str = "") -> Design:
    """Convert an Altium .PrjPcb or single .SchDoc to a schematic Design.

    This is the single entry point for Altium schematic conversion.  It
    handles project file dispatch, sheet loading, net resolution, harness
    scanning, and domain model construction.
    """
    ctx = ParseContext()

    # Phase 1: Load all sheets into typed records with spatial indices
    sheets = load_project_sheets(path, ctx=ctx)

    # Phase 2: Pre-scan harness info across all pages
    harness_port_nets: dict[str, list[tuple[str, dict[str, str]]]] = {}
    harness_members_by_type: dict[str, list[str]] = {}
    coord_to_nets: dict[str, dict[tuple[int, int], str]] = {}
    nc_coords_by_page: dict[str, set[tuple[int, int]]] = {}

    for page_name, sheet in sheets.items():
        harness_entry_coords = compute_harness_entry_coords(sheet)
        coord_to_net, nc_wires = resolve_nets(sheet, extra_named_coords=harness_entry_coords)
        coord_to_nets[page_name] = coord_to_net
        nc_coords_by_page[page_name] = nc_wires
        collect_harness_type_members(sheet, harness_members_by_type)
        collect_harness_port_nets(
            sheet,
            coord_to_net,
            harness_port_nets,
            page_name,
        )

    # Phase 3: Build domain model pages
    pages: list[Page] = []
    for page_name, sheet in sheets.items():
        coord_to_net = coord_to_nets[page_name]
        page = build_page(
            sheet,
            coord_to_net,
            harness_port_nets,
            harness_members_by_type,
            nc_wire_coords=nc_coords_by_page.get(page_name),
        )
        pages.append(page)

    # Phase 4: Hoist common page metadata to design level
    design_meta: dict[str, str] = {}
    _DESIGN_LEVEL_KEYS = (
        "Engineer",
        "Organization",
        "Address1",
        "Address2",
        "Address3",
        "Address4",
        "SheetTotal",
    )
    for key in _DESIGN_LEVEL_KEYS:
        for page in pages:
            if key in page.metadata:
                design_meta[key] = page.metadata[key]
                break

    # Surface parse issues count in design metadata
    if ctx.issues:
        design_meta["parse_issue_count"] = str(len(ctx.issues))

    return merge_pages(name or path.stem, pages, metadata=design_meta)
