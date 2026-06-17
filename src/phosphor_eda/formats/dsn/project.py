"""Parse OrCAD Capture .OPJ project files.

OPJ files are plain S-expressions. This parser keeps the surface deliberately
small: project identity, manifest files, and flat project settings needed to
load the referenced DSN in project context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

import sexpdata

from phosphor_eda.domain.project import DocumentKind, Project, ProjectDocument, ProjectMetadata
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.dsn.errors import DsnFormatError
from phosphor_eda.formats.dsn.parser import parse_dsn
from phosphor_eda.formats.dsn.to_schematic import dsn_to_design

if TYPE_CHECKING:
    from phosphor_eda.formats.kicad.sexp import SExpItem, SExpNode


@dataclass
class OrCadProject:
    name: str = ""
    version: str = ""
    project_type: str = ""
    parameters: dict[str, str] = field(default_factory=dict)
    documents: list[ProjectDocument] = field(default_factory=list)


_PARAMETER_PREFIXES = (
    "ANNOTATE_",
    "Annotate_",
    "DRC_",
    "BOM_",
    "CrossRef_",
    "Crossref_",
    "OTHER_",
    "FLDSTUFF_",
)
_PARAMETER_KEYS = {
    "ProjectVersion",
    "ProjectType",
    "Create Allegro Netlist",
    "Netlist_TAB",
    "Open_BOM_in_Excel",
}


def parse_opj_file(path: Path) -> OrCadProject:
    """Read and parse an OrCAD Capture .OPJ project file."""
    return parse_opj(path.read_text(encoding="utf-8", errors="replace"), base_path=path)


def load_orcad_project(opj_path: Path) -> Project:
    """Load an OrCAD project from a .OPJ manifest."""
    project_info = parse_opj_file(opj_path)
    schematic_by_path: dict[str, ProjectDocument] = {}
    for doc in project_info.documents:
        if doc.kind is not DocumentKind.SCHEMATIC or not doc.exists:
            continue
        resolved_path = doc.metadata.get("resolved_path")
        if not resolved_path:
            continue
        schematic_by_path.setdefault(resolved_path, doc)
    schematic_docs = list(schematic_by_path.values())
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


def parse_opj(text: str, *, base_path: Path | None = None) -> OrCadProject:
    """Parse OPJ text into a small source project model."""
    data = cast("SExpNode", sexpdata.loads(text))
    if _tag(data) != "ExpressProject":
        raise ValueError("OPJ root must be ExpressProject")

    project = OrCadProject(name=_scalar(data[1]) if len(data) > 1 else "")
    _walk(data, project, base_path=base_path)
    project.version = project.parameters.get("ProjectVersion", "")
    project.project_type = project.parameters.get("ProjectType", "")
    _add_allegro_board_documents(project, base_path=base_path)
    return project


def resolve_opj_path(base_path: Path, raw_path: str) -> Path | None:
    """Resolve an OPJ path when it is local to the project.

    Windows absolute paths from saved user environments are preserved in the
    manifest but are not treated as local paths on POSIX.
    """
    normalized = raw_path.replace("\\", "/")
    if not normalized:
        return None
    if _is_windows_absolute(normalized):
        return None
    candidate = Path(normalized)
    if candidate.is_absolute():
        return candidate
    return base_path.parent / candidate


def _walk(node: object, project: OrCadProject, *, base_path: Path | None) -> None:
    if not isinstance(node, list):
        return
    items = cast("SExpNode", node)
    if _tag(items) == "File":
        doc = _document_from_file_node(items, len(project.documents) + 1, base_path=base_path)
        if doc is not None:
            project.documents.append(doc)
    _collect_parameter(items, project)
    for child in items[1:]:
        _walk(child, project, base_path=base_path)


def _document_from_file_node(
    node: SExpNode,
    order: int,
    *,
    base_path: Path | None,
) -> ProjectDocument | None:
    if len(node) < 2:
        return None
    raw_path = _scalar(node[1])
    native_kind = _child_value(node, "Type")
    display_name = _child_value(node, "DisplayName")
    kind = _document_kind(raw_path, native_kind)
    local_path = resolve_opj_path(base_path, raw_path) if base_path else None
    exists = local_path.exists() if local_path is not None else False
    metadata = {"resolved_path": str(local_path)} if local_path is not None else {}
    return ProjectDocument(
        path=raw_path,
        kind=kind,
        native_kind=native_kind,
        description=display_name,
        order=order,
        exists=exists,
        metadata=metadata,
    )


def _document_kind(raw_path: str, native_kind: str) -> DocumentKind:
    lower_kind = native_kind.lower()
    suffix = Path(raw_path.replace("\\", "/")).suffix.lower()
    if "schematic design" in lower_kind or suffix == ".dsn":
        return DocumentKind.SCHEMATIC
    if "schematic library" in lower_kind or suffix == ".olb":
        return DocumentKind.LIBRARY
    if suffix == ".brd":
        return DocumentKind.PCB
    if suffix == ".bom":
        return DocumentKind.BOM
    if "report" in lower_kind:
        return DocumentKind.REPORT
    return DocumentKind.OTHER


def _add_allegro_board_documents(project: OrCadProject, *, base_path: Path | None) -> None:
    board_keys = (
        "Allegro Netlist Output Board File",
        "Allegro Netlist Input Board File",
    )
    seen = {doc.path for doc in project.documents}
    for key in board_keys:
        raw_path = project.parameters.get(key)
        if not raw_path or raw_path in seen:
            continue
        local_path = resolve_opj_path(base_path, raw_path) if base_path else None
        metadata = {"parameter": key}
        if local_path is not None:
            metadata["resolved_path"] = str(local_path)
        project.documents.append(
            ProjectDocument(
                path=raw_path,
                kind=DocumentKind.PCB,
                native_kind=key,
                order=len(project.documents) + 1,
                exists=local_path.exists() if local_path is not None else False,
                metadata=metadata,
            )
        )
        seen.add(raw_path)


def _collect_parameter(node: SExpNode, project: OrCadProject) -> None:
    if len(node) < 2:
        return
    key = _tag(node)
    if key is None or not _is_scalar(node[1]):
        return
    if not _is_parameter_key(key):
        return
    project.parameters[key] = _scalar(node[1])


def _is_parameter_key(key: str) -> bool:
    return (
        key in _PARAMETER_KEYS or key.startswith(_PARAMETER_PREFIXES) or key.startswith("Allegro ")
    )


def _child_value(node: SExpNode, tag_name: str) -> str:
    for child in node[2:]:
        if isinstance(child, list) and _tag(child) == tag_name and len(child) > 1:
            return _scalar(child[1])
    return ""


def _tag(item: object) -> str | None:
    if not isinstance(item, list) or not item:
        return None
    items = cast("SExpNode", item)
    first = items[0]
    if isinstance(first, sexpdata.Symbol):
        return first.value()
    if isinstance(first, str):
        return first
    return None


def _scalar(item: SExpItem) -> str:
    if isinstance(item, sexpdata.Symbol):
        return item.value()
    if isinstance(item, str):
        return _restore_control_escapes(item)
    return str(item)


def _restore_control_escapes(value: str) -> str:
    """Restore common backslash escapes sexpdata decodes inside OPJ strings."""
    return (
        value.replace("\a", r"\a")
        .replace("\b", r"\b")
        .replace("\f", r"\f")
        .replace("\n", r"\n")
        .replace("\r", r"\r")
        .replace("\t", r"\t")
        .replace("\v", r"\v")
    )


def _is_scalar(item: object) -> bool:
    return isinstance(item, (sexpdata.Symbol, str, int, float))


def _is_windows_absolute(path: str) -> bool:
    return len(path) >= 3 and path[1] == ":" and path[2] == "/" and path[0].isalpha()
