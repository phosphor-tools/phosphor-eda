"""KiCad project assembly."""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.domain.project import DocumentKind, Project, ProjectDocument, ProjectMetadata
from phosphor_eda.formats.kicad.board import parse_kicad_pcb
from phosphor_eda.formats.kicad.dru_parser import parse_kicad_dru
from phosphor_eda.formats.kicad.pro_parser import parse_kicad_pro, parse_kicad_text_variables
from phosphor_eda.formats.kicad.to_schematic import kicad_to_design

if TYPE_CHECKING:
    from pathlib import Path


def load_kicad_project(pro_path: Path) -> Project:
    """Assemble a KiCad project from a .kicad_pro file."""
    stem = pro_path.stem
    parent = pro_path.parent

    pcb_path = parent / f"{stem}.kicad_pcb"
    dru_path = parent / f"{stem}.kicad_dru"
    sch_path = parent / f"{stem}.kicad_sch"

    board = parse_kicad_pcb(pcb_path) if pcb_path.exists() else None
    net_classes = parse_kicad_pro(pro_path) if pro_path.exists() else []
    design_rules = parse_kicad_dru(dru_path) if dru_path.exists() else []
    schematic = kicad_to_design(sch_path, name=stem) if sch_path.exists() else None
    documents = [
        _project_document(
            pro_path,
            base=parent,
            raw_path=pro_path.name,
            kind=DocumentKind.OTHER,
            native_kind=".kicad_pro",
            parsed=True,
            order=1,
        ),
        _project_document(
            sch_path,
            base=parent,
            raw_path=sch_path.name,
            kind=DocumentKind.SCHEMATIC,
            native_kind=".kicad_sch",
            parsed=schematic is not None,
            order=2,
        ),
        _project_document(
            pcb_path,
            base=parent,
            raw_path=pcb_path.name,
            kind=DocumentKind.PCB,
            native_kind=".kicad_pcb",
            parsed=board is not None,
            order=3,
        ),
        _project_document(
            dru_path,
            base=parent,
            raw_path=dru_path.name,
            kind=DocumentKind.OTHER,
            native_kind=".kicad_dru",
            parsed=bool(design_rules),
            order=4,
        ),
    ]

    return Project(
        name=stem,
        metadata=ProjectMetadata(
            name=stem,
            format="kicad",
            source_paths=[str(pro_path)],
        ),
        parameters=parse_kicad_text_variables(pro_path),
        documents=documents,
        schematic=schematic,
        boards=[board] if board else [],
        net_classes=net_classes,
        design_rules=design_rules,
    )


def _project_document(
    resolved_path: Path,
    *,
    base: Path,
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
        metadata={"resolved_path": str(base / raw_path.replace("\\", "/"))},
    )
