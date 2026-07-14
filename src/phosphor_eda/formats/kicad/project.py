"""KiCad project assembly."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from phosphor_eda.domain.project import (
    DesignRule,
    DocumentKind,
    NetClass,
    Project,
    ProjectDocument,
    ProjectMetadata,
)
from phosphor_eda.domain.variants import Variant, VariantOverride
from phosphor_eda.formats.kicad.board import parse_kicad_pcb
from phosphor_eda.formats.kicad.dru_parser import parse_kicad_dru
from phosphor_eda.formats.kicad.errors import KiCadParseError
from phosphor_eda.formats.kicad.pro_parser import (
    parse_kicad_pro,
    parse_kicad_text_variables,
    parse_kicad_variants,
)
from phosphor_eda.formats.kicad.to_schematic import kicad_to_design
from phosphor_eda.formats.kicad.variants import parse_kicad_schematic_variant_overrides

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.domain.pcb import Board


@dataclass
class _ProjectData:
    """A parse result plus the metadata recorded when it degraded."""

    board: Board | None = None
    net_classes: list[NetClass] = field(default_factory=list)
    variants: list[Variant] = field(default_factory=list)
    text_variables: dict[str, str] = field(default_factory=dict)
    design_rules: list[DesignRule] | None = None
    pro_parsed: bool = False
    pro_error: str = ""
    pcb_error: str = ""
    dru_error: str = ""


def load_kicad_project(pro_path: Path) -> Project:
    """Assemble a KiCad project from a .kicad_pro file."""
    stem = pro_path.stem
    parent = pro_path.parent

    pcb_path = parent / f"{stem}.kicad_pcb"
    dru_path = parent / f"{stem}.kicad_dru"
    sch_path = parent / f"{stem}.kicad_sch"

    data = _ProjectData()
    if pro_path.exists():
        _parse_pro(pro_path, data)
    if pcb_path.exists():
        _parse_pcb(pcb_path, data)
    if dru_path.exists():
        _parse_dru(dru_path, data)
    schematic = kicad_to_design(sch_path, name=stem) if sch_path.exists() else None
    if data.variants and sch_path.exists():
        _merge_variant_overrides(data.variants, parse_kicad_schematic_variant_overrides(sch_path))
    documents = [
        _project_document(
            pro_path,
            raw_path=pro_path.name,
            kind=DocumentKind.OTHER,
            native_kind=".kicad_pro",
            parsed=data.pro_parsed,
            order=1,
            parse_error=data.pro_error,
        ),
        _project_document(
            sch_path,
            raw_path=sch_path.name,
            kind=DocumentKind.SCHEMATIC,
            native_kind=".kicad_sch",
            parsed=schematic is not None,
            order=2,
        ),
        _project_document(
            pcb_path,
            raw_path=pcb_path.name,
            kind=DocumentKind.PCB,
            native_kind=".kicad_pcb",
            parsed=data.board is not None,
            order=3,
            parse_error=data.pcb_error,
        ),
        _project_document(
            dru_path,
            raw_path=dru_path.name,
            kind=DocumentKind.OTHER,
            native_kind=".kicad_dru",
            parsed=data.design_rules is not None,
            order=4,
            parse_error=data.dru_error,
        ),
    ]

    return Project(
        name=stem,
        metadata=ProjectMetadata(
            name=stem,
            format="kicad",
            source_paths=[str(pro_path)],
        ),
        parameters=data.text_variables,
        documents=documents,
        schematic=schematic,
        boards=[data.board] if data.board else [],
        net_classes=data.net_classes,
        design_rules=data.design_rules or [],
        variants=data.variants,
    )


def _parse_pro(pro_path: Path, data: _ProjectData) -> None:
    """Parse the .kicad_pro net classes, variants, and text variables.

    A corrupt project JSON degrades to an unparsed document rather than
    aborting the whole project load.
    """
    try:
        data.net_classes = parse_kicad_pro(pro_path)
        data.variants = parse_kicad_variants(pro_path)
        data.text_variables = parse_kicad_text_variables(pro_path)
    except json.JSONDecodeError as exc:
        data.pro_error = f"{pro_path.name}: invalid JSON: {exc}"
    else:
        data.pro_parsed = True


def _parse_pcb(pcb_path: Path, data: _ProjectData) -> None:
    """Parse the sibling .kicad_pcb, degrading a malformed board to none."""
    try:
        data.board = parse_kicad_pcb(pcb_path)
    except (KiCadParseError, ValueError) as exc:
        data.pcb_error = f"{pcb_path.name}: {exc}"


def _parse_dru(dru_path: Path, data: _ProjectData) -> None:
    """Parse the sibling .kicad_dru, degrading malformed rules to none."""
    try:
        data.design_rules = parse_kicad_dru(dru_path)
    except (KiCadParseError, ValueError) as exc:
        data.dru_error = f"{dru_path.name}: {exc}"


def _merge_variant_overrides(
    variants: list[Variant],
    overrides: list[VariantOverride],
) -> None:
    variants_by_name = {variant.name: variant for variant in variants}
    for override in overrides:
        variant = variants_by_name.get(override.variant_name)
        if variant is None:
            variant = Variant(name=override.variant_name, order=len(variants) + 1)
            variants.append(variant)
            variants_by_name[variant.name] = variant
        variant.overrides.append(override)


def _project_document(
    resolved_path: Path,
    *,
    raw_path: str,
    kind: DocumentKind,
    native_kind: str,
    parsed: bool,
    order: int,
    parse_error: str = "",
) -> ProjectDocument:
    metadata = {"resolved_path": str(resolved_path)}
    if parse_error:
        # Surface why a present-but-unparsed document degraded so callers can
        # distinguish a corrupt file from an absent one.
        metadata["parse_error"] = parse_error
    return ProjectDocument(
        path=raw_path,
        kind=kind,
        native_kind=native_kind,
        order=order,
        exists=resolved_path.exists(),
        parsed=parsed,
        metadata=metadata,
    )
