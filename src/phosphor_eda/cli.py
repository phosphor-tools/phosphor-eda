"""Command-line interface for phosphor-eda."""

import functools
import importlib.resources
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import click

from phosphor_eda.domain.variant_materializer import UnknownVariantError
from phosphor_eda.query.format import (
    format_bus_detail_for,
    format_bus_table,
    format_component_detail_for,
    format_component_table,
    format_net_detail_for,
    format_net_table,
    format_page_detail_for,
    format_page_table,
    format_trace,
)
from phosphor_eda.query.overview import format_project_overview, format_stackup_section
from phosphor_eda.query.project_loader import (
    PCB_EXTENSIONS,
    PROJECT_EXTENSIONS,
    load_pcb,
    load_project,
)
from phosphor_eda.query.query import (
    filter_buses as filter_bus_objects,
)
from phosphor_eda.query.query import (
    filter_components as filter_component_objects,
)
from phosphor_eda.query.query import (
    filter_nets as filter_net_objects,
)
from phosphor_eda.query.query import (
    filter_pages as filter_page_objects,
)
from phosphor_eda.query.selectors import (
    resolve_buses,
    resolve_components,
    resolve_nets,
    resolve_pages,
    resolve_string_selectors,
)
from phosphor_eda.query.variants import format_variant_detail, format_variant_table
from phosphor_eda.render.settings import render_settings_schema

if TYPE_CHECKING:
    from phosphor_eda.domain.pcb import Board
    from phosphor_eda.domain.project import Project
    from phosphor_eda.domain.schematic import Schematic
    from phosphor_eda.render.annotations import ResolvedAnnotations
    from phosphor_eda.render.profiler import RenderProfiler
    from phosphor_eda.render.settings import RenderSettings

_PCB_FORMAT_BY_EXTENSION = {
    ".brd": "allegro",
    ".kicad_pcb": "kicad",
    ".pcbdoc": "altium",
    ".prjpcb": "altium",
}


class _HasId(Protocol):
    id: str


def cli_command[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """Wrap a command handler with a uniform error boundary."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

    return wrapper


def _print_skill(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    if not value:
        return
    skill_text = (
        importlib.resources.files("phosphor_eda").joinpath("skill.md").read_text(encoding="utf-8")
    )
    click.echo(skill_text, nl=False)
    ctx.exit()


@click.group()
@click.version_option(package_name="phosphor-eda")
@click.option(
    "--skill",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_print_skill,
    help="Print the LLM skill prompt and exit.",
)
@click.option(
    "-P",
    "--project",
    "project_file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Project entry file (.kicad_pro, .PrjPcb, .opj).",
)
@click.option("--variant", "variant_name", default=None, help="Apply this project variant.")
@click.option(
    "--base-variant",
    is_flag=True,
    default=False,
    help="Force the base design instead of a project variant.",
)
def main(project_file: Path | None, variant_name: str | None, base_variant: bool) -> None:
    """Query electronic schematics and PCB layouts."""
    if variant_name and base_variant:
        raise click.ClickException("--variant and --base-variant are mutually exclusive.")
    del project_file
    del variant_name
    del base_variant


def _project_path_required() -> Path:
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


def _load_project_or_die() -> "Project":
    project_file = _project_path_required()
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
    except UnknownVariantError as exc:
        raise click.ClickException(str(exc)) from exc
    except Exception as exc:
        raise click.ClickException(f"failed to parse {project_file}: {exc}") from exc


def _load_render_project_or_die(source_path: Path | None) -> "Project":
    from phosphor_eda.domain.project import Project, ProjectMetadata

    root = click.get_current_context().find_root()
    project_file = root.params.get("project_file")
    if source_path is not None and isinstance(project_file, Path):
        raise click.ClickException(
            "provide either a render source argument or -P/--project, not both."
        )
    if source_path is None:
        return _load_project_or_die()

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
                    format=_PCB_FORMAT_BY_EXTENSION[ext],
                    source_paths=[str(source_path)],
                ),
                boards=[board],
            )
    except click.ClickException:
        raise
    except UnknownVariantError as exc:
        raise click.ClickException(str(exc)) from exc
    except Exception as exc:
        raise click.ClickException(f"failed to parse {source_path}: {exc}") from exc

    supported = ", ".join(sorted(PROJECT_EXTENSIONS | PCB_EXTENSIONS))
    raise click.ClickException(
        f"unsupported render source: '{source_path.suffix}'. Supported: {supported}"
    )


def _schematic_or_die(project: "Project") -> "Schematic":
    if project.schematic is None:
        raise click.ClickException("project contains no loadable schematic.")
    return project.schematic


def _object_ids(items: Sequence[_HasId]) -> set[str]:
    return {item.id for item in items}


def _echo_detail_blocks(blocks: list[str], empty_message: str) -> None:
    if blocks:
        click.echo("\n\n".join(blocks))
    else:
        click.echo(empty_message)


@main.command()
@cli_command
def overview() -> None:
    """Show a bounded project overview."""
    project = _load_project_or_die()
    click.echo(format_project_overview(project))


@main.group(name="list")
def list_group() -> None:
    """List components, nets, pages, or buses in a schematic."""


@main.group(name="show")
def show_group() -> None:
    """Show detail for a specific component, net, page, or bus."""


@list_group.command()
@cli_command
def variants() -> None:
    """List project variants."""
    project = _load_project_or_die()
    click.echo(format_variant_table(project))


@show_group.command(name="variant")
@click.argument("name")
@cli_command
def show_variant(name: str) -> None:
    """Show details for a project variant."""
    project = _load_project_or_die()
    click.echo(format_variant_detail(project, name))


@list_group.command()
@click.option(
    "-c",
    "--component",
    "filter_components",
    multiple=True,
    help="Select components by shell-style selector (repeatable).",
)
@click.option(
    "-p",
    "--page",
    "filter_pages",
    multiple=True,
    help="Filter to components on selected pages (repeatable).",
)
@click.option("--passive/--no-passive", default=None, help="Passives only / exclude passives.")
@click.option("--min-pins", type=int, default=None, help="Components with at least N pins.")
@click.option(
    "-n",
    "--net",
    "filter_nets",
    multiple=True,
    help="Filter to components connected to selected nets (repeatable).",
)
@cli_command
def components(
    filter_components: tuple[str, ...],
    filter_pages: tuple[str, ...],
    passive: bool | None,
    min_pins: int | None,
    filter_nets: tuple[str, ...],
) -> None:
    """List components in a schematic."""
    design = _schematic_or_die(_load_project_or_die())
    selected_components = resolve_components(design, filter_components)
    selected_pages = resolve_pages(design, filter_pages)
    selected_nets = resolve_nets(design, filter_nets)

    filtered = filter_component_objects(
        design,
        component_ids=_object_ids(selected_components) if filter_components else None,
        page_ids=_object_ids(selected_pages) if filter_pages else None,
        passive=passive,
        min_pins=min_pins,
        net_ids=_object_ids(selected_nets) if filter_nets else None,
    )
    click.echo(format_component_table(design, components=filtered))


@list_group.command()
@click.option(
    "-n",
    "--net",
    "filter_nets",
    multiple=True,
    help="Select nets by shell-style selector (repeatable).",
)
@click.option(
    "-c",
    "--component",
    "filter_components",
    multiple=True,
    help="Filter to nets touching selected components (repeatable).",
)
@click.option(
    "-p",
    "--page",
    "filter_pages",
    multiple=True,
    help="Filter to nets on this page (repeatable).",
)
@click.option("--power/--no-power", default=None, help="Power nets only / exclude power nets.")
@click.option("--min-pins", type=int, default=None, help="Nets with at least N connections.")
@click.option("--bus", "filter_buses", multiple=True, help="Filter to nets in selected buses.")
@click.option(
    "--multi-page",
    is_flag=True,
    default=False,
    help="Only nets spanning multiple pages.",
)
@click.option(
    "--trace",
    is_flag=True,
    default=False,
    help="With --component, trace through 2-pin passives.",
)
@cli_command
def nets(
    filter_nets: tuple[str, ...],
    filter_components: tuple[str, ...],
    filter_pages: tuple[str, ...],
    power: bool | None,
    min_pins: int | None,
    filter_buses: tuple[str, ...],
    multi_page: bool,
    trace: bool,
) -> None:
    """List nets in a schematic."""
    design = _schematic_or_die(_load_project_or_die())
    selected_nets = resolve_nets(design, filter_nets)
    selected_components = resolve_components(design, filter_components)
    selected_pages = resolve_pages(design, filter_pages)
    selected_buses = resolve_buses(design, filter_buses)

    filtered = filter_net_objects(
        design,
        net_ids=_object_ids(selected_nets) if filter_nets else None,
        component_ids=_object_ids(selected_components) if filter_components else None,
        page_ids=_object_ids(selected_pages) if filter_pages else None,
        power=power,
        min_pins=min_pins,
        bus_ids=_object_ids(selected_buses) if filter_buses else None,
        multi_page=multi_page,
        trace=trace,
    )
    click.echo(format_net_table(design, nets=filtered))


@list_group.command()
@click.option(
    "-b",
    "--bus",
    "filter_buses",
    multiple=True,
    help="Select buses by shell-style selector (repeatable).",
)
@click.option(
    "--kind",
    "filter_kind",
    type=click.Choice(["vector", "group", "harness"]),
    default=None,
    help="Filter by bus kind.",
)
@click.option(
    "--net",
    "filter_nets",
    multiple=True,
    help="Filter to buses containing selected nets.",
)
@click.option(
    "--min-members",
    type=click.IntRange(min=0),
    default=None,
    help="Buses with at least N member nets.",
)
@cli_command
def buses(
    filter_buses: tuple[str, ...],
    filter_kind: str | None,
    filter_nets: tuple[str, ...],
    min_members: int | None,
) -> None:
    """List buses in a schematic."""
    design = _schematic_or_die(_load_project_or_die())
    selected_buses = resolve_buses(design, filter_buses)
    selected_nets = resolve_nets(design, filter_nets)

    filtered = filter_bus_objects(
        design,
        bus_ids=_object_ids(selected_buses) if filter_buses else None,
        kind=filter_kind,
        net_ids=_object_ids(selected_nets) if filter_nets else None,
        min_members=min_members,
    )
    click.echo(format_bus_table(design, buses=filtered))


@list_group.command()
@click.option(
    "-p",
    "--page",
    "filter_pages",
    multiple=True,
    help="Select pages by shell-style selector (repeatable).",
)
@click.option(
    "-n",
    "--net",
    "filter_nets",
    multiple=True,
    help="Filter to pages containing this net (repeatable).",
)
@click.option(
    "-c",
    "--component",
    "filter_components",
    multiple=True,
    help="Filter to pages containing this component (repeatable).",
)
@cli_command
def pages(
    filter_pages: tuple[str, ...],
    filter_nets: tuple[str, ...],
    filter_components: tuple[str, ...],
) -> None:
    """List pages in a schematic."""
    design = _schematic_or_die(_load_project_or_die())
    selected_pages = resolve_pages(design, filter_pages)
    selected_nets = resolve_nets(design, filter_nets)
    selected_components = resolve_components(design, filter_components)

    filtered = filter_page_objects(
        design,
        page_ids=_object_ids(selected_pages) if filter_pages else None,
        net_ids=_object_ids(selected_nets) if filter_nets else None,
        component_ids=_object_ids(selected_components) if filter_components else None,
    )
    click.echo(format_page_table(design, pages=filtered))


# ---- show commands ----


@show_group.command()
@click.argument("selectors", nargs=-1, required=True)
@cli_command
def component(selectors: tuple[str, ...]) -> None:
    """Show full detail for components by selector."""
    design = _schematic_or_die(_load_project_or_die())
    components = resolve_components(design, selectors)
    _echo_detail_blocks(
        [format_component_detail_for(design, component) for component in components],
        "No components found.",
    )


@show_group.command()
@click.argument("selectors", nargs=-1, required=True)
@cli_command
def net(selectors: tuple[str, ...]) -> None:
    """Show full detail for nets by selector."""
    design = _schematic_or_die(_load_project_or_die())
    nets = resolve_nets(design, selectors)
    _echo_detail_blocks(
        [format_net_detail_for(design, net) for net in nets],
        "No nets found.",
    )


@show_group.command()
@click.argument("selectors", nargs=-1, required=True)
@cli_command
def bus(selectors: tuple[str, ...]) -> None:
    """Show full detail for buses by selector."""
    design = _schematic_or_die(_load_project_or_die())
    buses = resolve_buses(design, selectors)
    _echo_detail_blocks(
        [format_bus_detail_for(design, bus) for bus in buses],
        "No buses found.",
    )


@show_group.command()
@click.argument("selectors", nargs=-1, required=True)
@cli_command
def page(selectors: tuple[str, ...]) -> None:
    """Show full detail for pages by selector."""
    design = _schematic_or_die(_load_project_or_die())
    pages = resolve_pages(design, selectors)
    _echo_detail_blocks(
        [format_page_detail_for(design, page) for page in pages],
        "No pages found.",
    )


# ---- trace command ----


@main.command()
@click.argument("ref_a")
@click.argument("ref_b")
@cli_command
def trace(ref_a: str, ref_b: str) -> None:
    """Show signal paths between two components."""
    design = _schematic_or_die(_load_project_or_die())
    click.echo(format_trace(design, ref_a, ref_b))


# ---- pcb commands ----


def _print_render_settings_schema(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    if not value:
        return
    click.echo(json.dumps(render_settings_schema(), indent=2))
    ctx.exit()


def _explicit_value[T](param: str, value: T) -> T | None:
    """Return *value* only if *param* was set on the command line.

    Lets settings-file values fill in flags the user didn't pass, while
    explicit CLI flags still take precedence.
    """
    ctx = click.get_current_context()
    if ctx.get_parameter_source(param) == click.core.ParameterSource.COMMANDLINE:
        return value
    return None


def _net_highlight_expansions(
    project: "Project",
    board: "Board",
    settings: "RenderSettings",
) -> dict[str, frozenset[str]]:
    """Expand net highlights through series passives via the schematic.

    Falls back to exact net-name matching when the project has no schematic.
    """
    from phosphor_eda.query.net_closure import connected_pcb_net_names

    nets = [h.net for h in settings.highlights if h.net and not h.exact]
    if not nets:
        return {}

    if project.schematic is None:
        click.echo(
            "Warning: project contains no loadable schematic; net highlights match exact names",
            err=True,
        )
        return {}

    board_net_names = tuple(net.name for net in board.nets.values())
    expansions: dict[str, frozenset[str]] = {}
    for selector in nets:
        matched_names = resolve_string_selectors((selector,), board_net_names)
        if not matched_names:
            continue
        expanded_names: set[str] = set()
        for net_name in matched_names:
            expanded_names.update(connected_pcb_net_names(board, project.schematic, net_name))
        expansions[selector] = frozenset(expanded_names)
    return expansions


def _select_project_board(project: "Project", selector: str | None) -> "Board":
    boards = project.boards
    if not boards:
        raise click.ClickException("project contains no renderable PCB board.")
    if selector is None:
        if len(boards) == 1:
            return boards[0]
        raise click.ClickException(
            "project contains multiple boards; use --board with one of: "
            + ", ".join(_board_label(board) for board in boards)
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
        + ", ".join(_board_label(board) for board in boards)
    )


def _raise_ambiguous_board(selector: str, boards: list["Board"]) -> None:
    raise click.ClickException(
        f"board selector '{selector}' is ambiguous. Matches: "
        + ", ".join(_board_label(board) for board in boards)
    )


def _board_label(board: "Board") -> str:
    source = Path(board.source_path).name if board.source_path else ""
    return f"{board.name} ({source})" if source and source != board.name else board.name


def _resolve_render_annotations(
    board: "Board",
    *,
    settings: "RenderSettings",
    cli_annotations: str,
    annotations_file: Path | None,
    profiler: "RenderProfiler | None",
) -> "ResolvedAnnotations | None":
    """Resolve annotations from CLI flags or the settings-file block.

    CLI ``--annotations``/``--annotations-file`` override the
    settings-file ``annotations`` block. Coordinates use the resolved
    side/font-size/rotation already on *settings*; sizing is anchored to
    a standard display width, independent of the render width.
    """
    # Lazy: annotations pulls in ortools (CP-SAT), too heavy for renders
    # without annotations.
    from phosphor_eda.render.annotations import parse_annotations, resolve_annotations
    from phosphor_eda.render.api import is_json_dict
    from phosphor_eda.render.profiler import profile_span

    annotations_json = cli_annotations
    if annotations_file:
        if annotations_json:
            raise click.ClickException("Cannot combine --annotations-file with --annotations")
        try:
            annotations_json = annotations_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise click.ClickException(f"Could not read {annotations_file}: {exc}") from exc
    if not annotations_json and settings.annotations:
        annotations_json = json.dumps(settings.annotations)
    if not annotations_json:
        return None

    try:
        data: object = json.loads(annotations_json)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid annotation JSON: {exc}") from exc
    if not is_json_dict(data):
        raise click.ClickException("Annotation error: top-level JSON value must be an object")
    try:
        spec = parse_annotations(data)
        with profile_span(profiler, "cli.resolve_annotations"):
            return resolve_annotations(
                spec,
                board,
                settings.side,
                font_size_pt=settings.font_size,
                rotation=settings.rotation,
            )
    except ValueError as exc:
        raise click.ClickException(f"Annotation error: {exc}") from exc


@main.group()
def pcb() -> None:
    """PCB layout commands."""


@pcb.command()
@click.argument("source_path", required=False, type=click.Path(exists=True, path_type=Path))
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Output SVG file (default: stdout).",
)
@click.option(
    "--board",
    "board_selector",
    default=None,
    help="Board name, source filename, or source path suffix for multi-board projects.",
)
@click.option(
    "--side", type=click.Choice(["front", "back"]), default="front", help="Board side to view."
)
@click.option(
    "--rotation",
    type=click.Choice(["0", "90", "180", "270"]),
    default="0",
    help="Rotate the rendered view clockwise (degrees).",
)
@click.option(
    "-n", "--net", "highlight_nets", multiple=True, help="Net name to highlight (repeatable)."
)
@click.option(
    "-c",
    "--component",
    "highlight_components",
    multiple=True,
    help="Component reference to highlight (repeatable).",
)
@click.option(
    "--highlight-pad",
    "highlight_pads",
    multiple=True,
    help="Pad target to highlight as <component>.<pad> (repeatable), e.g. CN11.30.",
)
@click.option("--width", type=int, default=800, help="SVG width in pixels.")
@click.option(
    "--custom-css",
    type=str,
    default="",
    help="Extra CSS injected after structured render styles (overrides built-in rules).",
)
@click.option(
    "--font-size",
    type=click.FloatRange(min=1, max=500),
    default=20.0,
    help=(
        "Annotation label font size in points, as seen at a standard "
        "content-column width; independent of render width and board size."
    ),
)
@click.option(
    "--annotations",
    type=str,
    default="",
    help="Annotation spec as inline JSON (boxes, pointers, labels, legend).",
)
@click.option(
    "--annotations-file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="File containing annotation JSON spec.",
)
@click.option(
    "--render-settings",
    "render_settings_file",
    type=str,
    default=None,
    help=(
        "JSON file with render settings (extends, highlights, annotations, CSS). Use '-' for stdin."
    ),
)
@click.option(
    "--debug-attributes",
    is_flag=True,
    default=False,
    help=(
        "Emit per-element data-* provenance attributes (component/net/pad "
        "identity) for CSS targeting and debugging; multiplies file size."
    ),
)
@click.option(
    "--render-settings-schema",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_print_render_settings_schema,
    help="Print the render settings JSON schema and exit.",
)
@click.option(
    "--profile-render",
    is_flag=True,
    help="Print PCB render performance metrics as JSON to stderr.",
)
@cli_command
def render(
    source_path: Path | None,
    output: Path | None,
    board_selector: str | None,
    side: str,
    rotation: str,
    highlight_nets: tuple[str, ...],
    highlight_components: tuple[str, ...],
    highlight_pads: tuple[str, ...],
    width: int,
    custom_css: str,
    font_size: float,
    annotations: str,
    annotations_file: Path | None,
    debug_attributes: bool,
    render_settings_file: str | None,
    profile_render: bool,
) -> None:
    """Render a PCB layout as SVG with optional highlighting."""
    from phosphor_eda.render.api import (
        DEFAULT_PRESET,
        load_bundled_render_settings,
        load_render_settings_file,
        load_render_settings_json,
        render_pcb_svg,
    )
    from phosphor_eda.render.profiler import RenderProfiler, profile_span
    from phosphor_eda.render.settings import (
        CliOverrides,
        HighlightSpec,
        parse_highlight_target,
        resolve_effective_settings,
    )

    profiler = RenderProfiler() if profile_render else None

    with profile_span(profiler, "cli.load_project"):
        project = _load_render_project_or_die(source_path)
        board = _select_project_board(project, board_selector)

    # -- Base render settings (file/stdin, or bundled default) -------------
    if render_settings_file == "-":
        try:
            with profile_span(profiler, "cli.load_render_settings", source="stdin"):
                base_settings = load_render_settings_json(sys.stdin.read())
        except ValueError as exc:
            raise click.ClickException(f"Render settings error: {exc}") from exc
    elif render_settings_file is not None:
        try:
            with profile_span(profiler, "cli.load_render_settings", source="file"):
                base_settings = load_render_settings_file(Path(render_settings_file))
        except ValueError as exc:
            raise click.ClickException(f"Render settings error: {exc}") from exc
    else:
        base_settings = load_bundled_render_settings(DEFAULT_PRESET)

    # -- CLI overrides (only flags the user explicitly set win over base) --
    cli_highlights: list[HighlightSpec] = []
    for net in highlight_nets:
        cli_highlights.append(HighlightSpec(net=net))
    for component in highlight_components:
        cli_highlights.append(HighlightSpec(component=component))
    for pad in highlight_pads:
        try:
            cli_highlights.append(parse_highlight_target(pad))
        except ValueError as exc:
            raise click.ClickException(f"Highlight pad error: {exc}") from exc

    explicit_rotation = _explicit_value("rotation", rotation)
    overrides = CliOverrides(
        side=_explicit_value("side", side),
        rotation=int(explicit_rotation) if explicit_rotation is not None else None,
        width=_explicit_value("width", width),
        font_size=_explicit_value("font_size", font_size),
        custom_css=_explicit_value("custom_css", custom_css),
        debug_attributes=_explicit_value("debug_attributes", debug_attributes),
        highlights=tuple(cli_highlights),
    )
    settings = resolve_effective_settings(base_settings, overrides)

    # -- Annotations (CLI flags override settings-file annotations) --------
    resolved_annotations = _resolve_render_annotations(
        board,
        settings=settings,
        cli_annotations=annotations,
        annotations_file=annotations_file,
        profiler=profiler,
    )

    with profile_span(profiler, "cli.net_highlight_expansions"):
        net_expansions = _net_highlight_expansions(project, board, settings)

    with profile_span(profiler, "cli.render_svg"):
        result = render_pcb_svg(
            board,
            settings,
            annotations=resolved_annotations,
            net_expansions=net_expansions,
            profiler=profiler,
        )
    svg = result.svg
    for warning in result.warnings:
        click.echo(f"Warning: {warning}", err=True)
    if output:
        with profile_span(profiler, "cli.write_output", bytes=len(svg.encode())):
            try:
                _ = output.write_text(svg, encoding="utf-8")
            except OSError as exc:
                raise click.ClickException(f"Could not write {output}: {exc}") from exc
        click.echo(f"Wrote {output}", err=True)
    else:
        click.echo(svg)
    if profiler is not None:
        click.echo(json.dumps(profiler.to_dict(), indent=2), err=True)


@pcb.command()
@cli_command
def stackup() -> None:
    """Show the physical PCB layer stackup, top to bottom.

    Prints the same stackup table as ``overview``: layer name, type,
    thickness, material, dielectric constant, and loss tangent for each
    physical layer, plus total board thickness. Placeholder layer slots
    that carry no physical construction are omitted.
    """
    project = _load_project_or_die()
    if not project.boards:
        raise click.ClickException("project contains no renderable PCB board.")
    section = format_stackup_section(project.boards)
    if not section:
        raise click.ClickException("project has no stackup metadata.")
    click.echo(section)


# ---- sql command ----


@main.command()
@click.argument("query", required=False)
@click.option("--schema", "show_schema", is_flag=True, help="Print table and view definitions.")
@cli_command
def sql(query: str | None, show_schema: bool) -> None:
    """Query project data with SQL (DuckDB + spatial)."""
    # Lazy: pulls in duckdb + shapely, only needed for the sql command.
    import duckdb

    from phosphor_eda.query.format import tabulate
    from phosphor_eda.query.sql import load_database, schema_text

    if show_schema:
        click.echo(schema_text())
        return

    if not query:
        raise click.ClickException("provide a SQL query or use --schema.")

    project = _load_project_or_die()
    con = load_database(project)

    try:
        result = con.execute(query)
    except duckdb.Error as exc:
        raise click.ClickException(f"SQL error: {exc}") from exc

    rows = result.fetchall()
    if not rows:
        click.echo("(0 rows)")
        return

    headers = tuple(col[0] for col in result.description)
    str_rows = [tuple(str(v) for v in row) for row in rows]
    click.echo(tabulate(headers, str_rows))
    click.echo(f"\n({len(rows)} row{'s' if len(rows) != 1 else ''})")
