"""Text serializer for the schematic domain model.

Produces grep-friendly, LLM-optimized text output in three sections:
design summary, component-centric view, and net-centric view.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from phosphor_eda.validate import Severity, validate_design

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.schematic import Component, Design, Net, Page, Pin

_MAJOR_IC_PIN_THRESHOLD = 4

_POWER_NET_RE = re.compile(r"^P?\d+V\d*$")

PASSIVE_PREFIXES = ("R", "C", "L", "D", "FB", "F", "Y")

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


def ref_prefix(reference: str) -> str:
    """Extract alpha prefix from a reference designator
    ("R10" -> "R", "FB1" -> "FB")."""
    prefix = ""
    for ch in reference:
        if ch.isalpha():
            prefix += ch
        else:
            break
    return prefix


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


def _pin_net_str(pin: Pin) -> str:
    if pin.no_connect:
        return "(no-connect)"
    if pin.net is None:
        return "(unconnected)"
    return pin.net.name


def is_power_net(name: str, net: Net | None = None) -> bool:
    upper = name.upper()
    if upper in ("GND", "VCC", "VDD", "VSS", "VBAT"):
        return True
    if _POWER_NET_RE.match(upper):
        return True
    return bool(net is not None and net.metadata.get("ClassName") == "PWR")


def _format_summary(design: Design) -> list[str]:
    lines = ["=== DESIGN SUMMARY ==="]
    n_comp = len(design.components)
    n_nets = len(design.nets)
    n_pages = len(design.pages)
    lines.append(
        f"Design: {design.name} | {n_pages} pages | {n_comp} components | {n_nets} nets"
    )

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


def _format_components(design: Design) -> list[str]:
    lines = ["=== COMPONENTS ===", ""]
    for comp in sorted(design.components, key=lambda c: c.reference):
        page_names = ", ".join(p.name for p in comp.pages)
        lines.append(
            f"COMPONENT: {comp.reference} | {comp.part}"
            f" | {comp.description} | Pages: {page_names}"
        )

        for key, value in sorted(_filter_metadata(comp).items()):
            lines.append(f"  {key}: {value}")

        for pin in sorted(comp.pins, key=lambda p: p.designator):
            net_str = _pin_net_str(pin)
            meta_str = ""
            # Filter out default/noise metadata values:
            # - electrical=passive is the default for 88%+ of pins
            filtered = {
                k: v
                for k, v in pin.metadata.items()
                if not (k == "electrical" and v == "passive")
            }
            if filtered:
                meta_str = "  " + "  ".join(
                    f"{k}={v}" for k, v in sorted(filtered.items())
                )

            dest_str = _trace_destinations(pin, comp)

            if pin.name:
                lines.append(
                    f"  Pin {pin.designator:<5s}  {pin.name:<15s}"
                    f" -> {net_str}{meta_str}{dest_str}"
                )
            else:
                lines.append(
                    f"  Pin {pin.designator:<5s}  {'':<15s}"
                    f" -> {net_str}{meta_str}{dest_str}"
                )

        lines.append("")

    return lines


def _format_nets(design: Design) -> list[str]:
    lines = ["=== NETS ===", ""]
    for net in sorted(design.nets, key=lambda n: n.name):
        net_pages = sorted({p.name for pin in net.pins for p in pin.component.pages})
        if len(net_pages) > 5:
            page_str = ", ".join(net_pages[:4]) + f", ... ({len(net_pages)} pages)"
        else:
            page_str = ", ".join(net_pages)

        alias_str = f" | Also: {', '.join(sorted(net.aliases))}" if net.aliases else ""
        lines.append(f"NET: {net.name}{alias_str} | Pages: {page_str}")

        for key, value in sorted(net.metadata.items()):
            lines.append(f"  [{key}: {value}]")

        for pin in sorted(
            net.pins, key=lambda p: (p.component.reference, p.designator)
        ):
            ref_pin = f"{pin.component.reference}.{pin.designator}"
            if pin.name:
                lines.append(f"  {ref_pin:<10s} {pin.name}")
            else:
                lines.append(f"  {ref_pin}")

        lines.append("")

    return lines


def _format_validation(design: Design) -> list[str]:
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


def serialize_design(design: Design) -> str:
    """Serialize a Design to a grep-friendly text string."""
    lines: list[str] = []
    lines.extend(_format_summary(design))
    lines.append("")
    lines.extend(_format_components(design))
    lines.append("")
    lines.extend(_format_nets(design))
    lines.append("")
    lines.extend(_format_validation(design))
    return "\n".join(lines)


def write_design(design: Design, output_path: Path) -> None:
    """Write a Design to a text file."""
    output_path.write_text(serialize_design(design))


# ---- Filters ----


def _net_pages(net: Net) -> set[str]:
    """Page names a net spans."""
    return {p.name for pin in net.pins for p in pin.component.pages}


def _net_components(net: Net) -> set[str]:
    """Component references on a net."""
    return {pin.component.reference for pin in net.pins}


def filter_nets(
    design: Design,
    *,
    components: list[str] | None = None,
    pages: list[str] | None = None,
    power: bool | None = None,
    min_pins: int | None = None,
    multi_page: bool = False,
    trace: bool = False,
) -> list[Net]:
    """Filter nets from a design.  All criteria are AND-composed."""
    from phosphor_eda.trace import trace_from_net

    result = list(design.nets)

    if power is True:
        result = [n for n in result if is_power_net(n.name, n)]
    elif power is False:
        result = [n for n in result if not is_power_net(n.name, n)]

    if pages:
        page_set = set(pages)
        result = [n for n in result if _net_pages(n) & page_set]

    if min_pins is not None:
        result = [n for n in result if len(n.pins) >= min_pins]

    if multi_page:
        result = [n for n in result if len(_net_pages(n)) > 1]

    if components:
        comp_set = set(components)
        if trace:
            # Expand each net's component reach through 2-pin passives
            def _reaches(net: Net) -> set[str]:
                refs = _net_components(net)
                for tr in trace_from_net(net):
                    if tr.terminal_pin is not None:
                        refs.add(tr.terminal_pin.component.reference)
                return refs

            result = [n for n in result if comp_set <= _reaches(n)]
        else:
            result = [n for n in result if comp_set <= _net_components(n)]

    return result


def filter_components(
    design: Design,
    *,
    pages: list[str] | None = None,
    prefixes: list[str] | None = None,
    passive: bool | None = None,
    min_pins: int | None = None,
    net: str | None = None,
) -> list[Component]:
    """Filter components from a design.  All criteria are AND-composed."""
    result = list(design.components)

    if pages:
        page_set = set(pages)
        result = [c for c in result if page_set & {p.name for p in c.pages}]

    if prefixes:
        prefix_set = set(prefixes)
        result = [c for c in result if ref_prefix(c.reference) in prefix_set]

    if passive is True:
        result = [c for c in result if ref_prefix(c.reference) in PASSIVE_PREFIXES]
    elif passive is False:
        result = [c for c in result if ref_prefix(c.reference) not in PASSIVE_PREFIXES]

    if min_pins is not None:
        result = [c for c in result if len(c.pins) >= min_pins]

    if net is not None:
        net_obj = _find_net(design, net)
        refs_on_net = {pin.component.reference for pin in net_obj.pins}
        result = [c for c in result if c.reference in refs_on_net]

    return result


def filter_pages(
    design: Design,
    *,
    nets: list[str] | None = None,
    components: list[str] | None = None,
) -> list[Page]:
    """Filter pages from a design.  All criteria are AND-composed."""
    result = list(design.pages)

    if nets:
        net_set = set(nets)
        result = [p for p in result if net_set & {n.name for n in p.nets}]

    if components:
        comp_set = set(components)
        result = [p for p in result if comp_set & {c.reference for c in p.components}]

    return result


def _find_net(design: Design, name: str) -> Net:
    """Find a net by name or alias.  Raises ValueError if not found."""
    for n in design.nets:
        if n.name == name:
            return n
    for n in design.nets:
        if name in n.aliases:
            return n
    raise ValueError(f"Net '{name}' not found in design.")


# ---- Trace-aware inline destinations ----


def _trace_destinations(pin: Pin, comp: Component) -> str:
    """Format inline destinations, tracing through 2-pin passives."""
    from phosphor_eda.trace import is_two_pin_passive, trace_from_net

    if pin.net is None or is_power_net(pin.net.name, pin.net):
        return ""

    parts: list[str] = []
    for p in sorted(pin.net.pins, key=lambda p: (p.component.reference, p.designator)):
        if p.component is comp:
            continue
        if is_two_pin_passive(p.component):
            continue
        parts.append(f"{p.component.reference}.{p.designator}")

    # Trace through passives to find active endpoints
    for tr in trace_from_net(pin.net, origin_comp=comp):
        if tr.terminal_pin is None:
            continue
        waypoints = ", ".join(w.component.reference for w in tr.series_path)
        dest = f"{tr.terminal_pin.component.reference}.{tr.terminal_pin.designator}"
        parts.append(f"{waypoints} -> {dest}")

    # Shunt passives on this net
    shunt_parts: list[str] = []
    for p in pin.net.pins:
        if p.component is comp:
            continue
        if not is_two_pin_passive(p.component):
            continue
        from phosphor_eda.trace import other_pin

        other = other_pin(p.component, p)
        if other.net is not None and is_power_net(other.net.name, other.net):
            shunt_parts.append(f"{p.component.reference} to {other.net.name}")

    result = ""
    if parts:
        result = "  [" + ", ".join(parts) + "]"
    if shunt_parts:
        result += "  (" + ", ".join(shunt_parts) + ")"
    return result


# ---- Trace command formatter ----


def format_trace(design: Design, ref_a: str, ref_b: str) -> str:
    """Format signal paths between two components."""
    from phosphor_eda.trace import find_paths

    paths = find_paths(design, ref_a, ref_b)
    if not paths:
        return f"No signal paths between {ref_a} and {ref_b}."

    lines: list[str] = []
    for path in paths:
        left = f"{path.left_pin.component.reference}.{path.left_pin.designator}"
        left_name = path.left_pin.name or ""
        right = f"{path.right_pin.component.reference}.{path.right_pin.designator}"
        right_name = path.right_pin.name or ""

        if path.series:
            via = " -- " + " -- ".join(c.reference for c in path.series) + " -- "
        else:
            via = " ---------- "

        line = f"{left:<10s} {left_name:<15s}{via}{right:<10s} {right_name}"
        if path.shunts:
            shunt_strs = [f"{c.reference} to {n.name}" for c, n in path.shunts]
            line += f"  ({', '.join(shunt_strs)})"
        lines.append(line)

    return "\n".join(lines)


# ---- List/show formatters for CLI ----


def _tabulate(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
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
    design: Design,
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
    return _tabulate(("REF", "PART", "DESCRIPTION", "PINS"), rows)


def format_net_table(
    design: Design,
    nets: list[Net] | None = None,
) -> str:
    """Format a table of nets: NET | ALIASES | PINS | PAGES."""
    source = nets if nets is not None else design.nets
    rows: list[tuple[str, ...]] = []
    for net in sorted(source, key=lambda n: n.name):
        aliases = ", ".join(sorted(net.aliases)) if net.aliases else ""
        pages = sorted({p.name for pin in net.pins for p in pin.component.pages})
        rows.append((net.name, aliases, str(len(net.pins)), ", ".join(pages)))
    if not rows:
        return "No nets found."
    return _tabulate(("NET", "ALIASES", "PINS", "PAGES"), rows)


def format_page_table(
    design: Design,
    pages: list[Page] | None = None,
) -> str:
    """Format a table of pages: PAGE | COMPONENTS | NETS."""
    source = pages if pages is not None else design.pages
    rows = [(p.name, str(len(p.components)), str(len(p.nets))) for p in source]
    if not rows:
        return "No pages found."
    return _tabulate(("PAGE", "COMPONENTS", "NETS"), rows)


def format_component_detail(design: Design, ref: str) -> str:
    """Format full detail for a single component. Raises ValueError if not found."""
    comp: Component | None = None
    for c in design.components:
        if c.reference == ref:
            comp = c
            break
    if comp is None:
        raise ValueError(f"Component '{ref}' not found in design.")

    page_names = ", ".join(p.name for p in comp.pages)
    lines = [
        f"COMPONENT: {comp.reference} | {comp.part}"
        f" | {comp.description} | Pages: {page_names}"
    ]

    for key, value in sorted(comp.metadata.items()):
        lines.append(f"  {key}: {value}")

    for pin in sorted(comp.pins, key=lambda p: p.designator):
        net_str = _pin_net_str(pin)
        dest_str = _trace_destinations(pin, comp)
        if pin.name:
            lines.append(
                f"  Pin {pin.designator:<5s}  {pin.name:<15s} -> {net_str}{dest_str}"
            )
        else:
            lines.append(
                f"  Pin {pin.designator:<5s}  {'':<15s} -> {net_str}{dest_str}"
            )

    return "\n".join(lines)


def format_net_detail(design: Design, name: str) -> str:
    """Format full detail for a single net. Raises ValueError if not found."""
    net: Net | None = None
    for n in design.nets:
        if n.name == name:
            net = n
            break
    if net is None:
        for n in design.nets:
            if name in n.aliases:
                net = n
                break
    if net is None:
        raise ValueError(f"Net '{name}' not found in design.")

    net_pages = sorted({p.name for pin in net.pins for p in pin.component.pages})
    alias_str = f" | Also: {', '.join(sorted(net.aliases))}" if net.aliases else ""
    lines = [f"NET: {net.name}{alias_str} | Pages: {', '.join(net_pages)}"]

    for key, value in sorted(net.metadata.items()):
        lines.append(f"  [{key}: {value}]")

    for pin in sorted(net.pins, key=lambda p: (p.component.reference, p.designator)):
        ref_pin = f"{pin.component.reference}.{pin.designator}"
        comp_desc = pin.component.description or pin.component.part
        if pin.name:
            lines.append(f"  {ref_pin:<12s} {pin.name:<15s} ({comp_desc})")
        else:
            lines.append(f"  {ref_pin:<12s} {'':15s} ({comp_desc})")

    return "\n".join(lines)


def format_page_detail(design: Design, page_name: str) -> str:
    """Format full detail for a single page. Raises ValueError if not found."""
    page = None
    for p in design.pages:
        if p.name == page_name:
            page = p
            break
    if page is None:
        raise ValueError(f"Page '{page_name}' not found in design.")

    lines = [f"PAGE: {page.name}"]
    for key, value in sorted(page.metadata.items()):
        lines.append(f"  {key}: {value}")

    if page.components:
        lines.append("")
        lines.append("Components:")
        for comp in sorted(page.components, key=lambda c: c.reference):
            lines.append(f"  {comp.reference:8s} {comp.part:20s} {comp.description}")

    if page.nets:
        lines.append("")
        lines.append("Nets:")
        for net in sorted(page.nets, key=lambda n: n.name):
            pin_strs = [f"{p.component.reference}.{p.designator}" for p in net.pins]
            lines.append(f"  {net.name:20s} {', '.join(pin_strs)}")

    return "\n".join(lines)
