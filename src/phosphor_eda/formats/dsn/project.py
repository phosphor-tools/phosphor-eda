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
from phosphor_eda.formats.dsn.package_netlist import (
    apply_packaged_no_connects,
    apply_packaged_pin_names,
)
from phosphor_eda.formats.dsn.parser import parse_dsn
from phosphor_eda.formats.dsn.to_schematic import dsn_to_design
from phosphor_eda.formats.dsn.variants import map_orcad_cis_not_fitted_variants

if TYPE_CHECKING:
    from phosphor_eda.domain.schematic import Schematic
    from phosphor_eda.domain.variants import Variant
    from phosphor_eda.formats.kicad.sexp import SExpItem, SExpNode


@dataclass
class OrCadProject:
    name: str = ""
    version: str = ""
    project_type: str = ""
    parameters: dict[str, str] = field(default_factory=dict)
    documents: list[ProjectDocument] = field(default_factory=list)
    hierarchy_documents: list[OrCadHierarchyDocument] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OrCadHierarchyDocument:
    path: str
    schematic: str
    page: str


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
        _ = schematic_by_path.setdefault(resolved_path, doc)
    schematic_docs = list(schematic_by_path.values())
    if len(schematic_docs) > 1:
        paths = ", ".join(doc.path for doc in schematic_docs)
        raise ValueError(
            f"{opj_path.name} references multiple existing schematic DSN files: {paths}"
        )

    schematic: Schematic | None = None
    variants: list[Variant] = []
    if schematic_docs:
        dsn_path = Path(schematic_docs[0].metadata["resolved_path"])
        ctx = ParseContext()
        try:
            raw = parse_dsn(dsn_path, ctx)
            netlist_dir = _select_packaged_netlist_dir(
                _packaged_netlist_dirs(project_info, dsn_path)
            )
            if netlist_dir is not None:
                apply_packaged_pin_names(raw, netlist_dir, ctx)
                apply_packaged_no_connects(raw, netlist_dir, ctx)
            schematic = dsn_to_design(raw, name=project_info.name or opj_path.stem, ctx=ctx)
            variants = map_orcad_cis_not_fitted_variants(raw, schematic, ctx)
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
        variants=variants,
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
    _attach_hierarchy_view_metadata(project)
    _add_allegro_board_documents(project, base_path=base_path)
    return project


_PACKAGED_NETLIST_FILES = frozenset({"pstxnet.dat", "pstxprt.dat", "pstchip.dat"})


def _packaged_netlist_dirs(project_info: OrCadProject, dsn_path: Path) -> tuple[Path, ...]:
    candidates: list[Path] = []
    for doc in project_info.documents:
        resolved_path = doc.metadata.get("resolved_path")
        if not resolved_path:
            continue
        path = Path(resolved_path)
        if path.name.casefold() in _PACKAGED_NETLIST_FILES:
            candidates.append(path.parent)
    candidates.extend((dsn_path.parent.parent / "Netlist", dsn_path.parent / "Allegro"))

    result: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if any((candidate / name).exists() for name in _PACKAGED_NETLIST_FILES):
            result.append(candidate)
    return tuple(result)


def _select_packaged_netlist_dir(candidates: tuple[Path, ...]) -> Path | None:
    for required_names in (
        ("pstxnet.dat", "pstxprt.dat", "pstchip.dat"),
        ("pstxprt.dat", "pstchip.dat"),
        ("pstxnet.dat",),
    ):
        for candidate in candidates:
            if all((candidate / name).exists() for name in required_names):
                return candidate
    return None


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
        return _resolve_case_insensitive(candidate)
    return _resolve_case_insensitive(base_path.parent / candidate)


def _resolve_case_insensitive(path: Path) -> Path:
    """Resolve Windows-authored OPJ paths on case-sensitive filesystems."""
    parent = path.parent
    if parent == path:
        return path
    resolved_parent = _resolve_case_insensitive(parent)
    if resolved_parent.exists():
        target = path.name.casefold()
        try:
            children = sorted(resolved_parent.iterdir(), key=lambda child: child.name.casefold())
        except OSError:
            return path
        for child in children:
            if child.name.casefold() == target:
                return child
    if path.exists():
        return path
    return path


def _walk(node: object, project: OrCadProject, *, base_path: Path | None) -> None:
    if not isinstance(node, list):
        return
    items = cast("SExpNode", node)
    if _tag(items) == "File":
        doc = _document_from_file_node(items, len(project.documents) + 1, base_path=base_path)
        if doc is not None:
            project.documents.append(doc)
    if _tag(items) == "Doc":
        hierarchy_doc = _hierarchy_document_from_doc_node(items)
        if hierarchy_doc is not None:
            project.hierarchy_documents.append(hierarchy_doc)
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


def _hierarchy_document_from_doc_node(node: SExpNode) -> OrCadHierarchyDocument | None:
    if _child_value(node, "Type") != "COrSchematicDoc":
        return None
    schematic = _child_value(node, "Schematic")
    page = _child_value(node, "Page")
    path = _child_value(node, "Path")
    if not schematic or not page:
        return None
    return OrCadHierarchyDocument(
        path=path,
        schematic=schematic,
        page=page,
    )


def _attach_hierarchy_view_metadata(project: OrCadProject) -> None:
    if not project.hierarchy_documents:
        return

    schematic_docs = [doc for doc in project.documents if doc.kind is DocumentKind.SCHEMATIC]
    schematic_docs_by_basename: dict[str, list[ProjectDocument]] = {}
    for doc in schematic_docs:
        names = {_path_basename(doc.path)}
        resolved_path = doc.metadata.get("resolved_path")
        if resolved_path:
            names.add(_path_basename(resolved_path))
        for name in names:
            if name:
                schematic_docs_by_basename.setdefault(name, []).append(doc)

    # Key by the document's index in project.documents rather than id(doc): the
    # id of a short-lived object can be reused, and ProjectDocument is unhashable.
    hierarchy_by_index: dict[int, list[OrCadHierarchyDocument]] = {}
    for hierarchy_doc in project.hierarchy_documents:
        matches = schematic_docs_by_basename.get(_path_basename(hierarchy_doc.path), [])
        if not matches and len(schematic_docs) == 1:
            matches = schematic_docs
        if len(matches) != 1:
            project.diagnostics.append(
                f"hierarchy view {hierarchy_doc.path!r} matched {len(matches)} schematic "
                "documents; view metadata not attached"
            )
            continue
        index = next(i for i, doc in enumerate(project.documents) if doc is matches[0])
        hierarchy_by_index.setdefault(index, []).append(hierarchy_doc)

    for index, doc in enumerate(project.documents):
        hierarchy_docs = hierarchy_by_index.get(index, [])
        if not hierarchy_docs:
            continue
        doc.metadata["hierarchy_view_document_count"] = str(len(hierarchy_docs))
        doc.metadata["hierarchy_view_pages"] = ";".join(
            f"{hierarchy_doc.schematic}/{hierarchy_doc.page}" for hierarchy_doc in hierarchy_docs
        )
        doc.metadata["hierarchy_view_paths"] = ";".join(
            hierarchy_doc.path for hierarchy_doc in hierarchy_docs if hierarchy_doc.path
        )


def _path_basename(path: str) -> str:
    normalized = path.replace("\\", "/")
    return Path(normalized).name.casefold()


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
    for child in node[1:]:
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
