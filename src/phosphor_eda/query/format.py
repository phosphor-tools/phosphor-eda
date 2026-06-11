"""Text formatters for the schematic domain model.

Two output styles share the same labelling/ambiguity helpers:

- ``serialize_design`` produces the grep-friendly, LLM-optimized full dump
  (summary, components, nets, validation).
- the ``format_*`` functions back the CLI ``list``/``show``/``trace`` commands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.formats.common.electrical import ELECTRICAL_KEY, PinElectrical
from phosphor_eda.query.classify import PASSIVE_PREFIXES, is_power_net, ref_prefix
from phosphor_eda.query.query import (
    component_physical_designator,
    find_component,
    find_net,
    net_page_names,
)
from phosphor_eda.query.trace import (
    find_paths,
    is_two_pin_passive,
    other_pin,
    trace_from_net,
)
from phosphor_eda.query.validate import Severity, validate_design

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.domain.schematic import Component, Net, Page, Pin, Schematic

_MAJOR_IC_PIN_THRESHOLD = 4

_IC_METADATA_ALLOWLIST = frozenset(
    {
        "mfr",
        "mfr_pn",
        "mfr_abbrev",
        "fp_disp_name",
        "value",
        "temp_min",
        "temp_max",
    }
)

_DEFAULT_NET_METADATA_HIDDEN = frozenset(
    {
        "altium_root_local_net_id",
        "kicad_root_local_net_id",
        "selected_name_source_id",
        "selected_name_source",
        "source_format",
        "source_local_net_ids",
        "source_scope_ids",
    }
)


def _filter_metadata(comp: Component) -> dict[str, str]:
    """Filter component metadata based on component type."""
    prefix = ref_prefix(comp.reference)
    if prefix in PASSIVE_PREFIXES:
        value = comp.metadata.get("value", "")
        if value and value not in (comp.description or ""):
            return {"value": value}
        return {}
    # ICs and everything else: allowlisted keys + URL values
    result: dict[str, str] = {}
    for key, value in comp.metadata.items():
        if key in _IC_METADATA_ALLOWLIST or value.startswith("http"):
            result[key] = value
    return result


def _filter_default_net_metadata(net: Net) -> dict[str, str]:
    return {
        key: value for key, value in net.metadata.items() if key not in _DEFAULT_NET_METADATA_HIDDEN
    }


def _pin_belongs_to_page(pin: Pin, page: Page) -> bool:
    if pin.occurrences:
        return any(occurrence.page.id == page.id for occurrence in pin.occurrences)

    return any(component_page.id == page.id for component_page in pin.component.pages)


def _page_net_pins(design: Schematic, page: Page, net: Net) -> list[Pin]:
    return sorted(
        (pin for pin in net.pins if _pin_belongs_to_page(pin, page)),
        key=lambda pin: (_pin_label(design, pin, net), pin.designator),
    )


def _pin_net_str(pin: Pin) -> str:
    if pin.no_connect:
        return "(no-connect)"
    if pin.net is None:
        return "(unconnected)"
    return pin.net.name


def _component_reference_is_ambiguous(design: Schematic, comp: Component) -> bool:
    return sum(1 for candidate in design.components if candidate.reference == comp.reference) > 1


def _component_block_label(design: Schematic, comp: Component) -> str:
    """Component header reference, qualified by physical designator when needed.

    For a logical reference that is ambiguous across instances (Case B/C), append
    the physical designator (``U1 [U1.3]``) so each instance block is
    distinguishable. Unambiguous references and un-annotated designs show no
    suffix.
    """
    designator = component_physical_designator(comp)
    if designator and _component_reference_is_ambiguous(design, comp):
        return f"{comp.reference} [{designator}]"
    return comp.reference


def _same_component(left: Component, right: Component) -> bool:
    return left.id == right.id


def _page_names(pages: list[Page]) -> list[str]:
    return sorted({page.name for page in pages})


def _page_name_is_ambiguous(design: Schematic, page: Page) -> bool:
    return sum(1 for candidate in design.pages if candidate.name == page.name) > 1


def _page_label(design: Schematic, page: Page) -> str:
    if _page_name_is_ambiguous(design, page):
        return page.id
    return page.name


def _pin_context_page(design: Schematic, pin: Pin, net: Net | None = None) -> str:
    pages = pin.component.pages
    if net is not None and net.pages:
        net_page_ids = {page.id for page in net.pages}
        matching_pages = [page for page in pages if page.id in net_page_ids]
        if matching_pages:
            pages = matching_pages
    labels = sorted({_page_label(design, page) for page in pages})
    if len(labels) == 1:
        return labels[0]
    if labels:
        return "/".join(labels)
    return "?"


def _pin_label(design: Schematic, pin: Pin, net: Net | None = None) -> str:
    label = f"{pin.component.reference}.{pin.designator}"
    if _component_reference_is_ambiguous(design, pin.component):
        return f"{_pin_context_page(design, pin, net)}/{label}"
    return label


def _component_label(design: Schematic, comp: Component) -> str:
    if _component_reference_is_ambiguous(design, comp):
        labels = sorted({_page_label(design, page) for page in comp.pages})
        page_label = labels[0] if labels else "?"
        return f"{page_label}/{comp.reference}"
    return comp.reference


def _net_name_is_ambiguous(design: Schematic, net: Net) -> bool:
    return sum(1 for candidate in design.nets if candidate.name == net.name) > 1


def _format_pin_line(design: Schematic, pin: Pin, comp: Component, *, with_metadata: bool) -> str:
    """Render one pin line for the components section / component detail.

    ``with_metadata`` controls the inline ``key=value`` block shown in the full
    dump but omitted from the single-component detail view.
    """
    net_str = _pin_net_str(pin)
    meta_str = ""
    if with_metadata:
        # electrical=passive is the default for 88%+ of pins, so it is noise.
        filtered = {
            k: v
            for k, v in pin.metadata.items()
            if not (k == ELECTRICAL_KEY and v == PinElectrical.PASSIVE)
        }
        if filtered:
            meta_str = "  " + "  ".join(f"{k}={v}" for k, v in sorted(filtered.items()))
    dest_str = _trace_destinations(design, pin, comp)
    return f"  Pin {pin.designator:<5s}  {pin.name:<15s} -> {net_str}{meta_str}{dest_str}"


def _format_summary(design: Schematic) -> list[str]:
    lines = ["=== DESIGN SUMMARY ==="]
    n_comp = len(design.components)
    n_nets = len(design.nets)
    n_pages = len(design.pages)
    lines.append(f"Design: {design.name} | {n_pages} pages | {n_comp} components | {n_nets} nets")

    meta_parts: list[str] = []
    for key in ("Author", "Engineer", "Revision", "Date", "Organization"):
        if key in design.metadata:
            meta_parts.append(f"{key}: {design.metadata[key]}")
    if meta_parts:
        lines.append(" | ".join(meta_parts))

    # Output remaining design metadata keys not already shown
    _SHOWN_KEYS = {"Author", "Engineer", "Revision", "Date", "Organization"}
    for key in sorted(design.metadata):
        if key not in _SHOWN_KEYS:
            lines.append(f"  {key}: {design.metadata[key]}")

    lines.append("")

    # Pages with metadata
    for page in design.pages:
        page_meta_parts: list[str] = []
        for key in ("SheetSize", "SheetNumber", "PageTitle"):
            if key in page.metadata:
                page_meta_parts.append(f"{key}={page.metadata[key]}")
        if page_meta_parts:
            lines.append(f"  Page: {page.name} [{', '.join(page_meta_parts)}]")
        else:
            lines.append(f"  Page: {page.name}")
    lines.append("")

    major = [c for c in design.components if len(c.pins) > _MAJOR_IC_PIN_THRESHOLD]
    if major:
        lines.append("Major ICs:")
        for comp in sorted(major, key=lambda c: c.reference):
            desc = comp.description or comp.part
            lines.append(f"  {comp.reference:6s}  {comp.part:20s}  {desc}")
        lines.append("")

    power_names = sorted(n.name for n in design.nets if is_power_net(n.name, n))
    if power_names:
        lines.append(f"Power Rails: {', '.join(power_names)}")
        lines.append("")

    return lines


def _format_components(design: Schematic) -> list[str]:
    lines = ["=== COMPONENTS ===", ""]
    for comp in sorted(design.components, key=lambda c: c.reference):
        page_names = ", ".join(_page_names(comp.pages))
        label = _component_block_label(design, comp)
        lines.append(f"COMPONENT: {label} | {comp.part} | {comp.description} | Pages: {page_names}")

        for key, value in sorted(_filter_metadata(comp).items()):
            lines.append(f"  {key}: {value}")

        for pin in sorted(comp.pins, key=lambda p: p.designator):
            lines.append(_format_pin_line(design, pin, comp, with_metadata=True))

        lines.append("")

    return lines


def _format_nets(design: Schematic) -> list[str]:
    lines = ["=== NETS ===", ""]
    for net in sorted(design.nets, key=lambda n: n.name):
        net_pages = net_page_names(net)
        if len(net_pages) > 5:
            page_str = ", ".join(net_pages[:4]) + f", ... ({len(net_pages)} pages)"
        else:
            page_str = ", ".join(net_pages)

        alias_str = f" | Also: {', '.join(sorted(net.aliases))}" if net.aliases else ""
        lines.append(f"NET: {net.name}{alias_str} | Pages: {page_str}")

        if _net_name_is_ambiguous(design, net):
            lines.append("  [name_not_unique: true]")

        for key, value in sorted(_filter_default_net_metadata(net).items()):
            lines.append(f"  [{key}: {value}]")

        for pin in sorted(net.pins, key=lambda p: (_pin_label(design, p, net), p.designator)):
            ref_pin = _pin_label(design, pin, net)
            if pin.name:
                lines.append(f"  {ref_pin:<10s} {pin.name}")
            else:
                lines.append(f"  {ref_pin}")

        lines.append("")

    return lines


def _format_validation(design: Schematic) -> list[str]:
    findings = validate_design(design)
    if not findings:
        return ["=== VALIDATION ===", "", "No issues found.", ""]

    errors = [f for f in findings if f.severity == Severity.ERROR]
    warnings = [f for f in findings if f.severity == Severity.WARNING]

    lines = ["=== VALIDATION ===", ""]
    if errors:
        lines.append(f"Errors ({len(errors)}):")
        for f in errors:
            lines.append(f"  ERROR  [{f.category.value}]  {f.message}")
        lines.append("")
    if warnings:
        lines.append(f"Warnings ({len(warnings)}):")
        for f in warnings:
            lines.append(f"  WARN   [{f.category.value}]  {f.message}")
        lines.append("")
    if not errors and not warnings:
        lines.append("No issues found.")
        lines.append("")

    return lines


def serialize_design(design: Schematic) -> str:
    """Serialize a Schematic to a grep-friendly text string."""
    lines: list[str] = []
    lines.extend(_format_summary(design))
    lines.append("")
    lines.extend(_format_components(design))
    lines.append("")
    lines.extend(_format_nets(design))
    lines.append("")
    lines.extend(_format_validation(design))
    return "\n".join(lines)


def write_design(design: Schematic, output_path: Path) -> None:
    """Write a Schematic to a text file."""
    _ = output_path.write_text(serialize_design(design), encoding="utf-8")


# ---- Trace-aware inline destinations ----


def _trace_destinations(design: Schematic, pin: Pin, comp: Component) -> str:
    """Format inline destinations, tracing through 2-pin passives."""
    if pin.net is None or is_power_net(pin.net.name, pin.net):
        return ""

    parts: list[str] = []
    for p in sorted(pin.net.pins, key=lambda p: (p.component.reference, p.designator)):
        if _same_component(p.component, comp):
            continue
        if is_two_pin_passive(p.component):
            continue
        parts.append(_pin_label(design, p, pin.net))

    # Trace through passives to find active endpoints
    for tr in trace_from_net(pin.net, origin_comp=comp):
        if tr.terminal_pin is None:
            continue
        waypoints = ", ".join(_component_label(design, w.component) for w in tr.series_path)
        dest = _pin_label(design, tr.terminal_pin, tr.terminal_pin.net)
        parts.append(f"{waypoints} -> {dest}")

    # Shunt passives on this net
    shunt_parts: list[str] = []
    for p in pin.net.pins:
        if _same_component(p.component, comp):
            continue
        if not is_two_pin_passive(p.component):
            continue

        other = other_pin(p.component, p)
        if other.net is not None and is_power_net(other.net.name, other.net):
            shunt_parts.append(f"{_component_label(design, p.component)} to {other.net.name}")

    result = ""
    if parts:
        result = "  [" + ", ".join(parts) + "]"
    if shunt_parts:
        result += "  (" + ", ".join(shunt_parts) + ")"
    return result


# ---- Trace command formatter ----


def format_trace(design: Schematic, ref_a: str, ref_b: str) -> str:
    """Format signal paths between two components."""
    paths = find_paths(design, ref_a, ref_b)
    if not paths:
        return f"No signal paths between {ref_a} and {ref_b}."

    lines: list[str] = []
    for path in paths:
        left = _pin_label(design, path.left_pin, path.left_pin.net)
        left_name = path.left_pin.name or ""
        right = _pin_label(design, path.right_pin, path.right_pin.net)
        right_name = path.right_pin.name or ""

        if path.series:
            via = " -- " + " -- ".join(_component_label(design, c) for c in path.series) + " -- "
        else:
            via = " ---------- "

        line = f"{left:<10s} {left_name:<15s}{via}{right:<10s} {right_name}"
        if path.shunts:
            shunt_strs = [f"{_component_label(design, c)} to {n.name}" for c, n in path.shunts]
            line += f"  ({', '.join(shunt_strs)})"
        lines.append(line)

    return "\n".join(lines)


# ---- List/show formatters for CLI ----


def tabulate(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    """Format rows as an aligned table."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(val))

    def fmt(row: tuple[str, ...]) -> str:
        return "  ".join(val.ljust(widths[i]) for i, val in enumerate(row))

    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines.extend(fmt(row) for row in rows)
    return "\n".join(lines)


def format_component_table(
    design: Schematic,
    components: list[Component] | None = None,
) -> str:
    """Format a table of components: REF | PART | DESCRIPTION | PINS."""
    source = components if components is not None else design.components
    rows = [
        (c.reference, c.part, c.description, str(len(c.pins)))
        for c in sorted(source, key=lambda c: c.reference)
    ]
    if not rows:
        return "No components found."
    return tabulate(("REF", "PART", "DESCRIPTION", "PINS"), rows)


def format_net_table(
    design: Schematic,
    nets: list[Net] | None = None,
) -> str:
    """Format a table of nets: NET | ALIASES | PINS | PAGES."""
    source = nets if nets is not None else design.nets
    rows: list[tuple[str, ...]] = []
    for net in sorted(source, key=lambda n: n.name):
        aliases = ", ".join(sorted(net.aliases)) if net.aliases else ""
        pages = net_page_names(net)
        rows.append((net.name, aliases, str(len(net.pins)), ", ".join(pages)))
    if not rows:
        return "No nets found."
    return tabulate(("NET", "ALIASES", "PINS", "PAGES"), rows)


def format_page_table(
    design: Schematic,
    pages: list[Page] | None = None,
) -> str:
    """Format a table of pages: PAGE | COMPONENTS | NETS."""
    source = pages if pages is not None else design.pages
    duplicate_names = {
        page.name for page in source if sum(1 for p in source if p.name == page.name) > 1
    }
    if duplicate_names:
        rows = [
            (
                p.name,
                p.id if p.name in duplicate_names else "",
                str(len(p.components)),
                str(len(p.nets)),
            )
            for p in source
        ]
        if not rows:
            return "No pages found."
        return tabulate(("PAGE", "PAGE ID", "COMPONENTS", "NETS"), rows)

    rows = [(p.name, str(len(p.components)), str(len(p.nets))) for p in source]
    if not rows:
        return "No pages found."
    return tabulate(("PAGE", "COMPONENTS", "NETS"), rows)


def format_component_detail(design: Schematic, ref: str) -> str:
    """Format full detail for a single component. Raises ValueError if not found.

    ``ref`` may be a logical reference (``U1``) or an exact per-instance physical
    designator (``U1.3``); see ``find_component``.
    """
    comp = find_component(design, ref)

    page_names = ", ".join(_page_names(comp.pages))
    header = (
        f"COMPONENT: {_component_block_label(design, comp)} | {comp.part} |"
        f" {comp.description} | Pages: {page_names}"
    )
    lines = [header]

    for key, value in sorted(_filter_metadata(comp).items()):
        lines.append(f"  {key}: {value}")

    for pin in sorted(comp.pins, key=lambda p: p.designator):
        lines.append(_format_pin_line(design, pin, comp, with_metadata=False))

    return "\n".join(lines)


def format_net_detail(design: Schematic, name: str) -> str:
    """Format full detail for a single net. Raises ValueError if not found."""
    net = find_net(design, name)

    net_pages = net_page_names(net)
    alias_str = f" | Also: {', '.join(sorted(net.aliases))}" if net.aliases else ""
    lines = [f"NET: {net.name}{alias_str} | Pages: {', '.join(net_pages)}"]

    if _net_name_is_ambiguous(design, net):
        lines.append("  [name_not_unique: true]")

    for key, value in sorted(_filter_default_net_metadata(net).items()):
        lines.append(f"  [{key}: {value}]")

    for pin in sorted(net.pins, key=lambda p: (_pin_label(design, p, net), p.designator)):
        ref_pin = _pin_label(design, pin, net)
        comp_desc = pin.component.description or pin.component.part
        if pin.name:
            lines.append(f"  {ref_pin:<12s} {pin.name:<15s} ({comp_desc})")
        else:
            lines.append(f"  {ref_pin:<12s} {'':15s} ({comp_desc})")

    return "\n".join(lines)


def _find_page_for_detail(design: Schematic, page_name: str) -> Page:
    id_matches = [page for page in design.pages if page.id == page_name]
    if len(id_matches) == 1:
        return id_matches[0]

    name_matches = [page for page in design.pages if page.name == page_name]
    if len(name_matches) == 1:
        return name_matches[0]
    if len(name_matches) > 1:
        choices = ", ".join(
            f"{page.id} ({page.name}, scope {page.scope_id})" for page in name_matches
        )
        raise ValueError(f"Page '{page_name}' is ambiguous; use a page id: {choices}")

    raise ValueError(f"Page '{page_name}' not found in design.")


def format_page_detail(design: Schematic, page_name: str) -> str:
    """Format full detail for a single page. Raises ValueError if not found."""
    page = _find_page_for_detail(design, page_name)

    lines = [f"PAGE: {page.name}"]
    for key, value in sorted(page.metadata.items()):
        lines.append(f"  {key}: {value}")

    if page.components:
        lines.append("")
        lines.append("Components:")
        for comp in sorted(page.components, key=lambda c: c.reference):
            lines.append(f"  {comp.reference:8s} {comp.part:20s} {comp.description}")

    if page.annotations:
        lines.append("")
        lines.append("Notes:")
        for annotation in page.annotations:
            for line in annotation.splitlines():
                lines.append(f"  {line}")
            lines.append("")

    if page.nets:
        lines.append("")
        lines.append("Nets:")
        for net in sorted(page.nets, key=lambda n: n.name):
            pin_strs = [_pin_label(design, pin, net) for pin in _page_net_pins(design, page, net)]
            lines.append(f"  {net.name:20s} {', '.join(pin_strs)}")

    return "\n".join(lines)
