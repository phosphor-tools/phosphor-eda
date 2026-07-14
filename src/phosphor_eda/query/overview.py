"""Project overview formatter for agent orientation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from phosphor_eda.query.classify import is_power_net, ref_prefix
from phosphor_eda.query.format import format_component_compact_line, single_line_text, tabulate
from phosphor_eda.query.lookup import net_page_names
from phosphor_eda.query.variants import variant_counts

if TYPE_CHECKING:
    from phosphor_eda.domain.pcb import Board
    from phosphor_eda.domain.project import Project, ProjectDocument, Stackup, StackupLayer
    from phosphor_eda.domain.schematic import Bus, Component, Page, Schematic, TitleBlock


_HIGH_PIN_THRESHOLD = 32
_CONNECTOR_PREFIXES = frozenset({"J", "P", "CN", "X"})
_TRUNCATION_SUFFIX = "..."


def format_project_overview(project: Project) -> str:
    """Format a bounded text overview of a loaded project."""
    sections: list[str] = [
        _project_section(project),
    ]
    variants_section = _variants_section(project)
    if variants_section:
        sections.append(variants_section)
    sections.append(_documents_section(project.documents))

    if project.schematic is not None:
        sections.extend(
            section
            for section in (
                _schematic_section(project.schematic),
                _schematic_pages_section(project.schematic, project),
            )
            if section
        )

    if project.boards:
        sections.append(_boards_section(project.boards))
        stackup_section = format_stackup_section(project.boards)
        if stackup_section:
            sections.append(stackup_section)

    if project.schematic is not None:
        sections.extend(
            section
            for section in (
                _important_components_section(project.schematic),
                _rails_section(project.schematic),
                _buses_section(project.schematic.buses),
                _notes_section(project.schematic),
            )
            if section
        )

    sections.append(_omitted_section())
    return "\n\n".join(sections)


def _project_section(project: Project) -> str:
    metadata = project.metadata
    lines = ["Project", f"  Name: {project.name}"]
    format_label = " ".join(part for part in (metadata.format, metadata.format_version) if part)
    if format_label:
        lines.append(f"  Format: {format_label}")

    root_block = _root_title_block(project)
    if root_block is not None and root_block.title and root_block.title != project.name:
        lines.append(f"  Title: {root_block.title}")

    for label, value in (
        ("Revision", metadata.revision),
        ("Date", metadata.date),
        ("Organization", metadata.organization),
    ):
        if value:
            lines.append(f"  {label}: {value}")
    return "\n".join(lines)


def _documents_section(documents: list[ProjectDocument]) -> str:
    lines = ["Documents"]
    rows: list[tuple[str, str, str, str]] = []
    errors: dict[int, str] = {}
    for index, document in enumerate(documents):
        rows.append(
            (
                document.kind.value,
                document.native_kind,
                document.path,
                _document_status(document),
            )
        )
        parse_error = document.metadata.get("parse_error")
        if parse_error:
            errors[index] = parse_error

    if rows:
        table_lines = tabulate(("KIND", "FORMAT/NATIVE KIND", "PATH", "STATUS"), rows).splitlines()
        for index, line in enumerate(table_lines):
            lines.append(f"  {line}")
            if index >= 2:
                row_index = index - 2
                if row_index in errors:
                    lines.append(f"    Error: {errors[row_index]}")
    else:
        lines.append("  No project documents found.")
    return "\n".join(lines)


def _variants_section(project: Project) -> str:
    if not project.variants:
        return ""
    active = project.active_variant.name if project.active_variant is not None else "base"
    rows: list[tuple[str, str, str, str, str, str, str]] = []
    for variant in project.variants:
        total, not_fitted, alternate, parameters, other = variant_counts(variant)
        rows.append(
            (
                variant.name,
                "yes" if project.active_variant is variant else "no",
                str(total),
                str(not_fitted),
                str(alternate),
                str(parameters),
                str(other),
            )
        )
    return "\n".join(
        [
            "Variants",
            f"  Active: {active}",
            *[
                f"  {line}"
                for line in tabulate(
                    ("NAME", "ACTIVE", "OVERRIDES", "NOT_FITTED", "ALT_PARTS", "PARAMS", "OTHER"),
                    rows,
                ).splitlines()
            ],
        ]
    )


def _document_status(document: ProjectDocument) -> str:
    exists = "exists" if document.exists else "missing"
    parsed = "parsed" if document.parsed else "not parsed"
    return f"{exists}, {parsed}"


def _schematic_section(schematic: Schematic) -> str:
    multi_page_signal_count = sum(
        1
        for net in schematic.nets
        if len(net_page_names(net)) > 1 and not is_power_net(net.name, net)
    )
    return "\n".join(
        [
            "Schematic",
            f"  Pages: {len(schematic.pages)}",
            f"  Components: {len(schematic.components)}",
            f"  Nets: {len(schematic.nets)}",
            f"  Buses: {len(schematic.buses)}",
            f"  Multi-page signal nets: {multi_page_signal_count}",
        ]
    )


def _schematic_pages_section(schematic: Schematic, project: Project) -> str:
    root_title = _root_title(project)
    repeated_titles = _repeated_page_titles(schematic)
    rows = [
        (
            page.name,
            Path(page.source_file).name if page.source_file else "",
            _page_title(page, root_title, repeated_titles),
            _sheet_number(page),
            str(len(page.components)),
            str(len(page.nets)),
            str(len(page.annotations)),
        )
        for page in schematic.pages
    ]
    if not rows:
        return ""
    return "\n".join(
        [
            "Schematic Pages",
            *[
                f"  {line}"
                for line in tabulate(
                    (
                        "PAGE",
                        "SOURCE FILE",
                        "TITLE/PAGE TITLE",
                        "SHEET",
                        "COMPONENTS",
                        "NETS",
                        "NOTES",
                    ),
                    rows,
                ).splitlines()
            ],
        ]
    )


def _boards_section(boards: list[Board]) -> str:
    rows = [
        (
            board.name,
            Path(board.source_path).name if board.source_path else "",
            _board_stackup_summary(board),
            str(len(board.footprints)),
            str(len(board.pads)),
            str(len(board.vias)),
            str(len(board.nets)),
        )
        for board in boards
    ]
    return "\n".join(
        [
            "Boards",
            *[
                f"  {line}"
                for line in tabulate(
                    ("NAME", "SOURCE FILE", "STACKUP", "FOOTPRINTS", "PADS", "VIAS", "NETS"),
                    rows,
                ).splitlines()
            ],
        ]
    )


def _board_stackup_summary(board: Board) -> str:
    stackup = board.stackup
    if stackup is None or not stackup.layers:
        return "No stackup metadata"

    copper_count = _stackup_layer_count(stackup, "copper")
    mask_count = _stackup_layer_count(stackup, "solder_mask")
    parts = [f"{copper_count} copper", f"{mask_count} solder mask"]
    thickness = _stackup_total_thickness_mm(stackup)
    if thickness > 0:
        parts.append(_format_mm(thickness))
    if stackup.copper_finish:
        parts.append(stackup.copper_finish)
    return ", ".join(parts)


def format_stackup_section(boards: list[Board]) -> str:
    """Render the physical stackup table shared by ``overview`` and ``pcb stackup``.

    Iterates each board's ``stackup.layers`` (the physical construction), so
    placeholder layer slots that only exist in the layer-slot inventory are
    naturally excluded. Returns an empty string when no board carries stackup
    metadata.
    """
    lines = ["Stackup"]
    found = False

    for board in boards:
        stackup = board.stackup
        if stackup is None or not stackup.layers:
            continue

        found = True
        lines.append(f"  {board.name}: {_stackup_detail_summary(stackup)}")
        rows = [_stackup_layer_row(layer) for layer in stackup.layers]
        lines.extend(f"  {line}" for line in tabulate(_STACKUP_HEADERS, rows).splitlines())

    if not found:
        return ""
    return "\n".join(lines)


_STACKUP_HEADERS = (
    "LAYER",
    "TYPE",
    "THICKNESS",
    "MATERIAL",
    "ER",
    "LOSS",
    "CU_OZ",
    "ORIENT",
)


def _stackup_detail_summary(stackup: Stackup) -> str:
    copper_count = _stackup_layer_count(stackup, "copper")
    layer_count = len(stackup.layers)
    parts = [
        f"{copper_count} copper layers",
        f"{layer_count} physical layers",
    ]
    thickness = _stackup_total_thickness_mm(stackup)
    if thickness > 0:
        parts.append(f"{_format_mm(thickness)} total")
    if stackup.copper_finish:
        parts.append(f"finish {stackup.copper_finish}")
    return ", ".join(parts)


def _stackup_layer_row(layer: StackupLayer) -> tuple[str, ...]:
    return (
        layer.name,
        layer.layer_type,
        _format_mm(layer.thickness_mm),
        layer.material,
        _format_number(layer.epsilon_r),
        _format_number(layer.loss_tangent),
        _format_copper_weight(layer.copper_weight_oz),
        layer.copper_orientation,
    )


def _stackup_layer_count(stackup: Stackup, layer_type: str) -> int:
    return sum(1 for layer in stackup.layers if layer.layer_type == layer_type)


def _stackup_total_thickness_mm(stackup: Stackup) -> float:
    if stackup.total_thickness_mm > 0:
        return stackup.total_thickness_mm
    return sum(layer.thickness_mm for layer in stackup.layers)


def _format_mm(value: float) -> str:
    if value <= 0:
        return ""
    return f"{value:.3f} mm"


def _format_number(value: float) -> str:
    if value <= 0:
        return ""
    return f"{value:g}"


def _format_copper_weight(value: float) -> str:
    if value <= 0:
        return ""
    return f"{value:.1f}"


def _important_components_section(schematic: Schematic) -> str:
    subsections = [
        (
            f"Components with >= {_HIGH_PIN_THRESHOLD} pins",
            [comp for comp in schematic.components if len(comp.pins) >= _HIGH_PIN_THRESHOLD],
        ),
        (
            "IC-like references (U*)",
            [comp for comp in schematic.components if ref_prefix(comp.reference) == "U"],
        ),
        (
            "Connectors (J*, P*, CN*, X*)",
            [
                comp
                for comp in schematic.components
                if ref_prefix(comp.reference) in _CONNECTOR_PREFIXES
            ],
        ),
        (
            "Test points (TP*)",
            [comp for comp in schematic.components if ref_prefix(comp.reference) == "TP"],
        ),
    ]
    lines = ["Important Components"]
    added = False
    for title, components in subsections:
        if not components:
            continue
        if added:
            lines.append("")
        lines.append(f"  {title}")
        lines.extend(
            f"    {format_component_compact_line(comp)}"
            for comp in sorted(components, key=_component_sort_key)
        )
        added = True
    return "\n".join(lines) if added else ""


def _rails_section(schematic: Schematic) -> str:
    rails = sorted(
        (net for net in schematic.nets if is_power_net(net.name, net)),
        key=lambda net: (-len(net.pins), net.name),
    )
    if not rails:
        return ""
    rows = [
        (
            net.name,
            str(len(net.pins)),
            str(len(net_page_names(net))),
            ", ".join(sorted(net.aliases)),
        )
        for net in rails
    ]
    return "\n".join(
        [
            "Rails",
            *[
                f"  {line}"
                for line in tabulate(("NET", "PINS", "PAGES", "ALIASES"), rows).splitlines()
            ],
        ]
    )


def _buses_section(buses: list[Bus]) -> str:
    if not buses:
        return ""
    lines = ["Buses"]
    for bus in sorted(buses, key=lambda item: (item.name, item.id)):
        lines.append(f"  {bus.name}  {bus.kind.value}  members={len(bus.members)}")
    return "\n".join(lines)


def _notes_section(schematic: Schematic) -> str:
    root = _root_page(schematic)
    root_comments = root.title_block.comments if root and root.title_block else {}
    annotated_pages = [page for page in schematic.pages if page.annotations]
    if not root_comments and not annotated_pages:
        return ""

    lines = ["Notes"]
    if root_comments:
        lines.append("  Project comments")
        for key in sorted(root_comments, key=_comment_sort_key):
            lines.append(f"    {key}: {_truncate(root_comments[key], 160)}")

    if annotated_pages:
        if root_comments:
            lines.append("")
        lines.append("  Page annotations")
        visible_pages = annotated_pages[:8]
        for page in visible_pages:
            lines.append(f"    {page.name}")
            for annotation in page.annotations[:2]:
                lines.append(f"      {_truncate(annotation, 240)}")
            omitted = len(page.annotations) - 2
            if omitted > 0:
                noun = "annotation" if omitted == 1 else "annotations"
                lines.append(f"      ... {omitted} more {noun} omitted")
        omitted_pages = len(annotated_pages) - len(visible_pages)
        if omitted_pages > 0:
            noun = "page" if omitted_pages == 1 else "pages"
            lines.append(f"    ... {omitted_pages} more {noun} with annotations omitted")
    return "\n".join(lines)


def _omitted_section() -> str:
    return "\n".join(
        [
            "Omitted",
            "  Full component list",
            "  Full net membership",
            "  Full pin lists",
            "  Full page annotations",
            "  Multi-page net list",
            "  PCB route geometry",
        ]
    )


def _root_page(schematic: Schematic) -> Page | None:
    if not schematic.pages:
        return None
    return min(schematic.pages, key=lambda page: len(page.scope_id.path))


def _root_title_block(project: Project) -> TitleBlock | None:
    if project.schematic is None:
        return None
    root = _root_page(project.schematic)
    return None if root is None else root.title_block


def _root_title(project: Project) -> str:
    block = _root_title_block(project)
    return "" if block is None else block.title


def _repeated_page_titles(schematic: Schematic) -> set[str]:
    counts: dict[str, int] = {}
    for page in schematic.pages:
        title = page.title_block.title if page.title_block else ""
        if title:
            counts[title] = counts.get(title, 0) + 1
    return {title for title, count in counts.items() if count == len(schematic.pages)}


def _page_title(page: Page, root_title: str, repeated_titles: set[str]) -> str:
    block = page.title_block
    if block is None:
        return ""
    page_title = block.metadata.get("PageTitle", "").strip()
    if page_title:
        return page_title
    title = block.title.strip()
    if not title or title == root_title or title in repeated_titles:
        return ""
    return title


def _sheet_number(page: Page) -> str:
    block = page.title_block
    if block is None:
        return ""
    if block.sheet_number and block.sheet_total:
        return f"{block.sheet_number}/{block.sheet_total}"
    return block.sheet_number


def _component_sort_key(component: Component) -> tuple[str, int, str]:
    prefix = ref_prefix(component.reference)
    suffix = component.reference[len(prefix) :]
    match = re.match(r"(\d+)", suffix)
    number = int(match.group(1)) if match else -1
    return (prefix, number, component.reference)


def _comment_sort_key(key: str) -> tuple[int, int | str]:
    return (0, int(key)) if key.isdigit() else (1, key)


def _truncate(value: str, max_chars: int) -> str:
    normalized = single_line_text(value)
    if len(normalized) <= max_chars:
        return normalized
    if max_chars <= len(_TRUNCATION_SUFFIX):
        return _TRUNCATION_SUFFIX[:max_chars]
    return normalized[: max_chars - len(_TRUNCATION_SUFFIX)].rstrip() + _TRUNCATION_SUFFIX
