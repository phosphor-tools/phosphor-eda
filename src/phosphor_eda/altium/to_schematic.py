"""Convert raw Altium parse results into the schematic domain model."""

from __future__ import annotations

from phosphor_eda.altium.sheet_builder import (
    build_page,
    collect_harness_port_nets,
    collect_harness_type_members,
    compute_harness_entry_coords,
    load_sheet,
    resolve_nets,
)
from phosphor_eda.models import ParsedDesign as RawDesign
from phosphor_eda.schematic import Design, merge_pages


def altium_to_design(raw: RawDesign, name: str = "") -> Design:
    """Convert a raw Altium ParsedDesign to a schematic Design."""
    # Phase 1: Load all sheets into typed records with spatial indices
    from phosphor_eda.altium.sheet_builder import SheetRecords

    sheets: dict[str, SheetRecords] = {}
    for raw_page in raw.pages:
        schdoc_path = getattr(raw_page, "_schdoc_path", None)
        if schdoc_path is None:
            continue
        sheets[raw_page.name] = load_sheet(str(schdoc_path))

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
            sheet, coord_to_net, harness_port_nets, page_name,
        )

    # Phase 3: Build domain model pages
    pages = []
    for raw_page in raw.pages:
        if raw_page.name not in sheets:
            continue
        sheet = sheets[raw_page.name]
        coord_to_net = coord_to_nets[raw_page.name]
        page = build_page(
            sheet, coord_to_net,
            harness_port_nets, harness_members_by_type,
            raw_page=raw_page,
            nc_wire_coords=nc_coords_by_page.get(raw_page.name),
        )
        pages.append(page)

    # Phase 4: Hoist common page metadata to design level
    design_meta: dict[str, str] = {}
    _DESIGN_LEVEL_KEYS = (
        "Engineer", "Organization", "Address1", "Address2",
        "Address3", "Address4", "SheetTotal",
    )
    for key in _DESIGN_LEVEL_KEYS:
        for page in pages:
            if key in page.metadata:
                design_meta[key] = page.metadata[key]
                break

    return merge_pages(name, pages, metadata=design_meta)
