"""Project loading and selection helpers shared by the CLI commands."""

from pathlib import Path
from typing import TYPE_CHECKING

import click

from phosphor_eda.domain.project import DocumentKind, Project, ProjectMetadata
from phosphor_eda.query.project_loader import (
    PCB_EXTENSIONS,
    PROJECT_EXTENSIONS,
    load_pcb,
    load_project,
)

if TYPE_CHECKING:
    from phosphor_eda.domain.pcb import Board
    from phosphor_eda.domain.schematic import Schematic

PCB_FORMAT_BY_EXTENSION = {
    ".brd": "allegro",
    ".kicad_pcb": "kicad",
    ".pcbdoc": "altium",
    ".prjpcb": "altium",
}


def project_path_required() -> Path:
    root = click.get_current_context().find_root()
    project_file = root.params.get("project_file")
    if not isinstance(project_file, Path):
        raise click.ClickException("missing -P/--project.")
    if project_file.suffix.lower() not in PROJECT_EXTENSIONS:
        supported = ", ".join(sorted(PROJECT_EXTENSIONS))
        raise click.ClickException(
            f"project file required: '{project_file.suffix}' is not a project entry point. "
            f"Supported: {supported}"
        )
    return project_file


def load_project_or_die() -> Project:
    project_file = project_path_required()
    root = click.get_current_context().find_root()
    variant_name = root.params.get("variant_name")
    base_variant = bool(root.params.get("base_variant"))
    try:
        return load_project(
            project_file,
            variant_name=variant_name if isinstance(variant_name, str) else None,
            base_variant=base_variant,
        )
    except click.ClickException:
        raise
    except Exception as exc:
        raise click.ClickException(f"failed to parse {project_file}: {exc}") from exc


def load_render_project_or_die(source_path: Path | None) -> Project:
    root = click.get_current_context().find_root()
    project_file = root.params.get("project_file")
    if source_path is not None and isinstance(project_file, Path):
        raise click.ClickException(
            "provide either a render source argument or -P/--project, not both."
        )
    if source_path is None:
        return load_project_or_die()

    ext = source_path.suffix.lower()
    try:
        if ext in PROJECT_EXTENSIONS:
            variant_name = root.params.get("variant_name")
            base_variant = bool(root.params.get("base_variant"))
            return load_project(
                source_path,
                variant_name=variant_name if isinstance(variant_name, str) else None,
                base_variant=base_variant,
            )
        if ext in PCB_EXTENSIONS:
            board = load_pcb(source_path)
            return Project(
                name=board.name or source_path.stem,
                metadata=ProjectMetadata(
                    name=board.name or source_path.stem,
                    format=PCB_FORMAT_BY_EXTENSION[ext],
                    source_paths=[str(source_path)],
                ),
                boards=[board],
            )
    except click.ClickException:
        raise
    except Exception as exc:
        raise click.ClickException(f"failed to parse {source_path}: {exc}") from exc

    supported = ", ".join(sorted(PROJECT_EXTENSIONS | PCB_EXTENSIONS))
    raise click.ClickException(
        f"unsupported render source: '{source_path.suffix}'. Supported: {supported}"
    )


def missing_document_error(
    project: Project, message: str, *, kinds: frozenset[DocumentKind]
) -> click.ClickException:
    """Build the error for a missing schematic/board, naming any degraded documents.

    A document that degraded records why in its metadata; surfacing that makes a
    corrupt file distinguishable from a project that simply lacks the document.
    Only failures of the given *kinds* are named, so an unrelated degraded
    sibling does not get blamed for the missing document.
    """
    parse_errors = [
        error
        for doc in project.documents
        if doc.kind in kinds and (error := doc.metadata.get("parse_error"))
    ]
    if parse_errors:
        return click.ClickException(f"{message}; failed to parse: " + "; ".join(parse_errors))
    return click.ClickException(f"{message}.")


# A corrupt project entry file (DocumentKind.OTHER) can explain either missing
# document, so both scopes include it alongside their own kind.
_SCHEMATIC_ERROR_KINDS = frozenset({DocumentKind.SCHEMATIC, DocumentKind.OTHER})
_BOARD_ERROR_KINDS = frozenset({DocumentKind.PCB, DocumentKind.OTHER})


def schematic_or_die(project: Project) -> "Schematic":
    if project.schematic is None:
        raise missing_document_error(
            project,
            "project contains no loadable schematic",
            kinds=_SCHEMATIC_ERROR_KINDS,
        )
    return project.schematic


def missing_board_error(project: Project) -> click.ClickException:
    return missing_document_error(
        project,
        "project contains no renderable PCB board",
        kinds=_BOARD_ERROR_KINDS,
    )


def select_project_board(project: Project, selector: str | None) -> "Board":
    boards = project.boards
    if not boards:
        raise missing_board_error(project)
    if selector is None:
        if len(boards) == 1:
            return boards[0]
        raise click.ClickException(
            "project contains multiple boards; use --board with one of: "
            + ", ".join(board_label(board) for board in boards)
        )

    exact_name = [board for board in boards if board.name == selector]
    if len(exact_name) == 1:
        return exact_name[0]
    if len(exact_name) > 1:
        _raise_ambiguous_board(selector, exact_name)

    exact_source = [board for board in boards if Path(board.source_path).name == selector]
    if len(exact_source) == 1:
        return exact_source[0]
    if len(exact_source) > 1:
        _raise_ambiguous_board(selector, exact_source)

    normalized_selector = selector.replace("\\", "/").lower()
    suffix_matches = [
        board
        for board in boards
        if board.source_path.replace("\\", "/").lower().endswith(normalized_selector)
    ]
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    if len(suffix_matches) > 1:
        _raise_ambiguous_board(selector, suffix_matches)

    raise click.ClickException(
        f"board '{selector}' not found. Available boards: "
        + ", ".join(board_label(board) for board in boards)
    )


def _raise_ambiguous_board(selector: str, boards: list["Board"]) -> None:
    raise click.ClickException(
        f"board selector '{selector}' is ambiguous. Matches: "
        + ", ".join(board_label(board) for board in boards)
    )


def board_label(board: "Board") -> str:
    source = Path(board.source_path).name if board.source_path else ""
    return f"{board.name} ({source})" if source and source != board.name else board.name
