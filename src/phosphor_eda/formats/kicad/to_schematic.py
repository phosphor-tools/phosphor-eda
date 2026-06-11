"""Extract KiCad-native schematic source objects.

The source extractor keeps KiCad labels, sheet pins, sheet instances, power
symbols, and local wire groups separate. It intentionally does not construct
the public schematic graph; the resolver converts these source objects into
the public ``Schematic`` model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import phosphor_eda.formats.kicad.sexp as sexp
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.kicad.resolver import resolve_kicad_source
from phosphor_eda.formats.kicad.sheet_loader import (
    ParseContextSheetWarningReporter,
    load_sheet_tree,
)
from phosphor_eda.formats.kicad.source import (
    KiCadGlobalLabel,
    KiCadHierarchicalLabel,
    KiCadLocalLabel,
    KiCadLocalNet,
    KiCadPinOccurrence,
    KiCadPowerSymbol,
    KiCadSheetPin,
    KiCadSheetSymbol,
    KiCadSourceDesign,
)
from phosphor_eda.formats.kicad.source_extractor import extract_sheet_sources

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.domain.schematic import Schematic
    from phosphor_eda.formats.kicad.sheet_loader import LoadedSheetTree


def kicad_to_design(path: Path, name: str = "") -> Schematic:
    """Parse a KiCad schematic into the public model."""
    ctx = ParseContext()
    source = kicad_to_source(path, name, ctx)
    return resolve_kicad_source(source, ctx)


def kicad_to_source(
    path: Path, name: str = "", ctx: ParseContext | None = None
) -> KiCadSourceDesign:
    """Extract KiCad-native source connectivity from a root schematic file.

    Missing or cyclic child-sheet references are recorded on *ctx* when
    provided, mirroring the other parser pipelines.
    """
    reporter = ParseContextSheetWarningReporter(ctx) if ctx is not None else None
    sheet_tree = load_sheet_tree(path, name, reporter)

    local_nets: list[KiCadLocalNet] = []
    pin_occurrences: list[KiCadPinOccurrence] = []
    local_labels: list[KiCadLocalLabel] = []
    global_labels: list[KiCadGlobalLabel] = []
    hierarchical_labels: list[KiCadHierarchicalLabel] = []
    power_symbols: list[KiCadPowerSymbol] = []
    sheet_symbols: list[KiCadSheetSymbol] = []
    sheet_pins: list[KiCadSheetPin] = []

    # Sheet symbols/pins reference child scopes by UUID, but a child whose file
    # was missing or formed a cycle is never loaded and so has no scope. Drop
    # those dangling references so resolution does not see a sheet pin pointing
    # at a non-existent child page.
    loaded_scopes = {instance.scope_id for instance in sheet_tree.sheet_instances}
    root_uuid = _root_uuid(sheet_tree)

    for loaded in sheet_tree.sheets:
        extracted = extract_sheet_sources(
            loaded,
            sheet_tree.lib_pins,
            sheet_tree.lib_descs,
            loaded_scopes,
            root_uuid,
        )
        local_nets.extend(extracted.local_nets)
        pin_occurrences.extend(extracted.pin_occurrences)
        local_labels.extend(extracted.local_labels)
        global_labels.extend(extracted.global_labels)
        hierarchical_labels.extend(extracted.hierarchical_labels)
        power_symbols.extend(extracted.power_symbols)
        sheet_symbols.extend(extracted.sheet_symbols)
        sheet_pins.extend(extracted.sheet_pins)

    return KiCadSourceDesign(
        name=name or path.stem,
        root_source_file=str(path),
        root_scope_id=sheet_tree.root_scope_id,
        sheet_instances=sheet_tree.sheet_instances,
        local_nets=local_nets,
        pin_occurrences=pin_occurrences,
        local_labels=local_labels,
        global_labels=global_labels,
        hierarchical_labels=hierarchical_labels,
        power_symbols=power_symbols,
        sheet_symbols=sheet_symbols,
        sheet_pins=sheet_pins,
    )


def _root_uuid(sheet_tree: LoadedSheetTree) -> str:
    """The root schematic's uuid — the first segment of every instance path."""
    for loaded in sheet_tree.sheets:
        if loaded.instance.scope_id == sheet_tree.root_scope_id:
            uuid_node = sexp.find(list(loaded.data[1:]), "uuid")
            return sexp.val(uuid_node) if uuid_node is not None else ""
    return ""
