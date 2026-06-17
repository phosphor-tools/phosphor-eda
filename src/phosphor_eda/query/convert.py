"""Schematic, PCB, and project loading.

Dispatches on file extension to parse schematics from Altium, KiCad,
OrCAD, and Eagle into a unified Schematic model. Project loading is
project-file-first: ``load_project()`` accepts project manifests only.
"""

from __future__ import annotations

from pathlib import Path
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
from phosphor_eda.domain.schematic import NetName, NetNameKind
from phosphor_eda.formats.altium.pcb_parser import parse_altium_pcb
from phosphor_eda.formats.altium.pcb_project import load_altium_enrichment
from phosphor_eda.formats.altium.project import parse_prjpcb_file
from phosphor_eda.formats.altium.to_schematic import altium_to_design
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.dsn.errors import DsnFormatError
from phosphor_eda.formats.dsn.package_netlist import apply_packaged_pin_names
from phosphor_eda.formats.dsn.parser import parse_dsn
from phosphor_eda.formats.dsn.project import parse_opj_file
from phosphor_eda.formats.dsn.to_schematic import dsn_to_design
from phosphor_eda.formats.eagle.to_schematic import eagle_to_design
from phosphor_eda.formats.kicad.board import parse_kicad_pcb
from phosphor_eda.formats.kicad.dru_parser import parse_kicad_dru
from phosphor_eda.formats.kicad.pro_parser import parse_kicad_pro, parse_kicad_text_variables
from phosphor_eda.formats.kicad.to_schematic import kicad_to_design

if TYPE_CHECKING:
    from collections.abc import Callable

    from phosphor_eda.domain.pcb import Board
    from phosphor_eda.domain.schematic import Schematic


def _load_altium(path: Path) -> Schematic:
    return altium_to_design(path, name=path.stem)


def _load_dsn(path: Path) -> Schematic:
    ctx = ParseContext()
    raw = parse_dsn(path, ctx)
    return dsn_to_design(raw, name=path.stem, ctx=ctx)


def _load_eagle(path: Path) -> Schematic:
    return eagle_to_design(path, name=path.stem)


def _load_kicad(path: Path) -> Schematic:
    return kicad_to_design(path, name=path.stem)


_DESIGN_LOADERS: dict[str, Callable[[Path], Schematic]] = {
    ".schdoc": _load_altium,
    ".prjpcb": _load_altium,
    ".dsn": _load_dsn,
    ".kicad_sch": _load_kicad,
    ".sch": _load_eagle,
}

SCHEMATIC_EXTENSIONS: frozenset[str] = frozenset(_DESIGN_LOADERS)


def load_design(path: Path) -> Schematic:
    """Parse a schematic file into a Schematic (no serialization)."""
    ext = path.suffix.lower()
    loader = _DESIGN_LOADERS.get(ext)
    if loader is None:
        supported = ", ".join(sorted(SCHEMATIC_EXTENSIONS))
        raise ValueError(f"Unsupported schematic format: '{ext}'. Supported: {supported}")
    return loader(path)


# ---------------------------------------------------------------------------
# PCB board loading
# ---------------------------------------------------------------------------


def _load_kicad_pcb(path: Path) -> Board:
    return parse_kicad_pcb(path)


def _load_altium_pcb(path: Path) -> Board:
    return parse_altium_pcb(path)


def _load_prjpcb(path: Path) -> Board:
    return parse_altium_pcb(resolve_prjpcb_pcbdoc(path))


_PCB_LOADERS: dict[str, Callable[[Path], Board]] = {
    ".kicad_pcb": _load_kicad_pcb,
    ".pcbdoc": _load_altium_pcb,
    ".prjpcb": _load_prjpcb,
}

PCB_EXTENSIONS: frozenset[str] = frozenset(_PCB_LOADERS)
PROJECT_EXTENSIONS: frozenset[str] = frozenset({".kicad_pro", ".prjpcb", ".opj"})


def load_pcb(path: Path) -> Board:
    """Parse a PCB layout file into a Board board.

    Dispatches on extension; ``.prjpcb`` resolves to its referenced
    ``.PcbDoc`` first. Raises ``ValueError`` for unsupported formats.
    """
    ext = path.suffix.lower()
    loader = _PCB_LOADERS.get(ext)
    if loader is None:
        supported = ", ".join(sorted(PCB_EXTENSIONS))
        raise ValueError(f"Unsupported PCB format: '{path.suffix}'. Supported: {supported}")
    return loader(path)


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
            + "Pass a .PcbDoc directly or update the project DocumentPath."
        )
    if len(existing_pcbdocs) > 1:
        boards = ", ".join(str(path) for path in existing_pcbdocs)
        raise ValueError(
            f"{prj_path.name} references multiple existing .PcbDoc files: {boards}. "
            + "Pass the intended .PcbDoc directly."
        )
    return existing_pcbdocs[0]


def load_project(path: Path) -> Project:
    """Load a complete project from a project manifest file."""
    ext = path.suffix.lower()

    if ext == ".kicad_pro":
        project = _load_kicad_project(path)
    elif ext == ".prjpcb":
        project = _load_altium_project_from_prj(path)
    elif ext == ".opj":
        project = _load_orcad_project(path)
    else:
        supported = ", ".join(sorted(PROJECT_EXTENSIONS))
        raise ValueError(
            f"project file required: '{path.suffix}' is not a project entry point. "
            + f"Supported: {supported}"
        )

    _fill_metadata_from_title_block(project)
    return project


def _fill_metadata_from_title_block(project: Project) -> None:
    """Fill empty ProjectMetadata fields from the root page's title block.

    The root page is the shallowest scope; project files rarely carry
    name/revision/date themselves, the title block is where designers put
    them.
    """
    schematic = project.schematic
    if schematic is None or not schematic.pages:
        return
    root_page = min(schematic.pages, key=lambda page: len(page.scope_id.path))
    block = root_page.title_block
    if block is None:
        return
    metadata = project.metadata
    metadata.name = metadata.name or block.title
    metadata.revision = metadata.revision or block.revision
    metadata.date = metadata.date or block.date
    metadata.organization = metadata.organization or block.organization
    metadata.author = metadata.author or block.author or block.metadata.get("Author", "")


def _load_kicad_project(pro_path: Path) -> Project:
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


def _load_altium_project_from_pcb(pcb_path: Path) -> Project:
    """Load an Altium project starting from a .PcbDoc file."""
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


def _load_altium_project_from_prj(prj_path: Path) -> Project:
    """Load an Altium project starting from a .PrjPcb file."""
    project_info = parse_prjpcb_file(str(prj_path))

    # Find and parse every referenced PCB that exists.
    boards: list[Board] = []
    net_classes: list[NetClass] = []
    design_rules: list[DesignRule] = []
    diff_pairs: list[DiffPair] = []
    documents: list[ProjectDocument] = [
        _project_document(
            prj_path,
            base=prj_path.parent,
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
                base=prj_path.parent,
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
                base=prj_path.parent,
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
        pcb_project = _load_altium_project_from_pcb(pcb_abs)
        boards.extend(pcb_project.boards)
        net_classes.extend(pcb_project.net_classes)
        design_rules.extend(pcb_project.design_rules)
        diff_pairs.extend(pcb_project.diff_pairs)

    # Parse schematic if available
    schematic = None
    if project_info.schematic_paths:
        schematic = altium_to_design(prj_path, name=prj_path.stem)
        if boards:
            _align_schematic_net_names_to_board(schematic, boards[0])

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


def _align_schematic_net_names_to_board(schematic: Schematic, board: Board) -> None:
    """Use Altium's packaged PCB net names for exact schematic/PCB pin matches."""
    schematic_by_members: dict[frozenset[tuple[str, str]], list[int]] = {}
    for index, net in enumerate(schematic.nets):
        members = frozenset((pin.component.reference, pin.designator) for pin in net.pins)
        if not members:
            continue
        schematic_by_members.setdefault(members, []).append(index)

    for board_net in board.nets.values():
        if not board_net.name:
            continue
        members = frozenset(
            (pad.footprint.reference, pad.number)
            for pad in board.pads
            if pad.net is board_net and pad.footprint is not None
        )
        if not members:
            continue
        indexes = schematic_by_members.get(members, [])
        if len(indexes) != 1:
            continue
        net = schematic.nets[indexes[0]]
        if net.name == board_net.name:
            continue
        old_name = net.name
        net.name = board_net.name
        net.aliases.discard(board_net.name)
        net.aliases.add(old_name)
        if all(name.name != board_net.name for name in net.names):
            net.names.append(
                NetName(
                    name=board_net.name,
                    kind=NetNameKind.TOOL_AUTO,
                    source="altium:Nets6/Data",
                )
            )
        net.metadata["altium_pcb_net_name"] = board_net.name
        net.metadata["altium_schematic_net_name"] = old_name


def _load_orcad_project(opj_path: Path) -> Project:
    """Load an OrCAD project from a .OPJ manifest."""
    project_info = parse_opj_file(opj_path)
    schematic_docs = [
        doc
        for doc in project_info.documents
        if doc.kind is DocumentKind.SCHEMATIC and doc.exists and doc.metadata.get("resolved_path")
    ]
    if len(schematic_docs) > 1:
        paths = ", ".join(doc.path for doc in schematic_docs)
        raise ValueError(
            f"{opj_path.name} references multiple existing schematic DSN files: {paths}"
        )

    schematic = None
    if schematic_docs:
        dsn_path = Path(schematic_docs[0].metadata["resolved_path"])
        ctx = ParseContext()
        try:
            raw = parse_dsn(dsn_path, ctx)
            apply_packaged_pin_names(raw, dsn_path.parent.parent / "Netlist")
            schematic = dsn_to_design(raw, name=project_info.name or opj_path.stem, ctx=ctx)
            schematic_docs[0].parsed = True
        except (DsnFormatError, OSError, ValueError) as exc:
            schematic_docs[0].metadata["parse_error"] = str(exc)

    name = project_info.name or opj_path.stem
    return Project(
        name=name,
        metadata=ProjectMetadata(
            name=name,
            format="orcad",
            format_version=project_info.version,
            source_paths=[str(opj_path)],
        ),
        parameters=project_info.parameters,
        documents=project_info.documents,
        schematic=schematic,
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
