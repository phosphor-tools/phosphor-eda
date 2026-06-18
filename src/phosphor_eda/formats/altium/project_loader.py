"""Altium project assembly."""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.domain.project import (
    DesignRule,
    DiffPair,
    DocumentKind,
    NetClass,
    Project,
    ProjectDocument,
    ProjectMetadata,
)
from phosphor_eda.formats.altium.pcb_parser import parse_altium_pcb
from phosphor_eda.formats.altium.pcb_project import load_altium_enrichment
from phosphor_eda.formats.altium.project import parse_prjpcb_file
from phosphor_eda.formats.altium.to_schematic import altium_to_design
from phosphor_eda.formats.common.diagnostics import ParseContext

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.domain.pcb import Board


def load_altium_project(prj_path: Path) -> Project:
    """Load an Altium project starting from a .PrjPcb file."""
    project_info = parse_prjpcb_file(str(prj_path))

    boards: list[Board] = []
    net_classes: list[NetClass] = []
    design_rules: list[DesignRule] = []
    diff_pairs: list[DiffPair] = []
    documents: list[ProjectDocument] = [
        _project_document(
            prj_path,
            raw_path=prj_path.name,
            kind=DocumentKind.OTHER,
            native_kind=".PrjPcb",
            parsed=True,
            order=1,
        )
    ]
    seen_pcbdocs: set[Path] = set()
    order = 2
    for sch_rel in project_info.schematic_paths:
        sch_abs = prj_path.parent / sch_rel.replace("\\", "/")
        documents.append(
            _project_document(
                sch_abs,
                raw_path=sch_rel,
                kind=DocumentKind.SCHEMATIC,
                native_kind="SchDoc",
                parsed=sch_abs.exists(),
                order=order,
            )
        )
        order += 1
    for pcb_rel in project_info.pcb_paths:
        pcb_abs = prj_path.parent / pcb_rel.replace("\\", "/")
        documents.append(
            _project_document(
                pcb_abs,
                raw_path=pcb_rel,
                kind=DocumentKind.PCB,
                native_kind="PcbDoc",
                parsed=pcb_abs.exists(),
                order=order,
            )
        )
        order += 1
        if not pcb_abs.exists():
            continue
        resolved = pcb_abs.resolve()
        if resolved in seen_pcbdocs:
            continue
        seen_pcbdocs.add(resolved)
        pcb_project = load_altium_pcb_project(pcb_abs)
        boards.extend(pcb_project.boards)
        net_classes.extend(pcb_project.net_classes)
        design_rules.extend(pcb_project.design_rules)
        diff_pairs.extend(pcb_project.diff_pairs)

    schematic = None
    if project_info.schematic_paths:
        schematic = altium_to_design(prj_path, name=prj_path.stem)

    return Project(
        name=prj_path.stem,
        metadata=ProjectMetadata(
            name=prj_path.stem,
            format="altium",
            source_paths=[str(prj_path)],
        ),
        parameters={
            "HierarchyMode": str(int(project_info.hierarchy_mode)),
            "AllowPortNetNames": str(project_info.allow_port_net_names),
            "AllowSheetEntryNetNames": str(project_info.allow_sheet_entry_net_names),
            "AppendSheetNumberToLocalNets": str(project_info.append_sheet_number_to_local_nets),
            "NameNetsHierarchically": str(project_info.name_nets_hierarchically),
            "NetlistSinglePinNets": str(project_info.netlist_single_pin_nets),
            "PowerPortNamesTakePriority": str(project_info.power_port_names_take_priority),
        },
        documents=documents,
        schematic=schematic,
        boards=boards,
        net_classes=net_classes,
        design_rules=design_rules,
        diff_pairs=diff_pairs,
    )


def load_altium_pcb_project(pcb_path: Path) -> Project:
    """Load Altium board-side project data starting from a .PcbDoc file."""
    ctx = ParseContext()
    board = parse_altium_pcb(pcb_path, ctx)
    enrichment = load_altium_enrichment(pcb_path, ctx)

    return Project(
        name=pcb_path.stem,
        metadata=ProjectMetadata(name=pcb_path.stem, format="altium", source_paths=[str(pcb_path)]),
        boards=[board],
        net_classes=enrichment.net_classes,
        design_rules=enrichment.design_rules,
        diff_pairs=enrichment.diff_pairs,
    )


def resolve_prjpcb_pcbdoc(prj_path: Path) -> Path:
    """Resolve a .PrjPcb to exactly one existing referenced .PcbDoc."""
    project = parse_prjpcb_file(str(prj_path))
    existing_pcbdocs: list[Path] = []
    seen_resolved: set[Path] = set()

    for pcb_rel in project.pcb_paths:
        pcb_path = prj_path.parent / pcb_rel.replace("\\", "/")
        if not pcb_path.exists():
            continue
        resolved = pcb_path.resolve()
        if resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)
        existing_pcbdocs.append(pcb_path)

    if not existing_pcbdocs:
        raise ValueError(
            f"{prj_path.name} does not reference an existing .PcbDoc. "
            "Pass a .PcbDoc directly or update the project DocumentPath."
        )
    if len(existing_pcbdocs) > 1:
        boards = ", ".join(str(path) for path in existing_pcbdocs)
        raise ValueError(
            f"{prj_path.name} references multiple existing .PcbDoc files: {boards}. "
            "Pass the intended .PcbDoc directly."
        )
    return existing_pcbdocs[0]


def _project_document(
    resolved_path: Path,
    *,
    raw_path: str,
    kind: DocumentKind,
    native_kind: str,
    parsed: bool,
    order: int,
) -> ProjectDocument:
    return ProjectDocument(
        path=raw_path,
        kind=kind,
        native_kind=native_kind,
        order=order,
        exists=resolved_path.exists(),
        parsed=parsed,
        metadata={"resolved_path": str(resolved_path)},
    )
