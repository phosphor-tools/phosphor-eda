"""Text serializer for the schematic domain model.

Produces grep-friendly, LLM-optimized text output in three sections:
design summary, component-centric view, and net-centric view.
"""

from __future__ import annotations

import re
from pathlib import Path

from ecad_tools.schematic import Component, Design, Net, Pin
from ecad_tools.validate import Severity, validate_design

_MAJOR_IC_PIN_THRESHOLD = 4

_POWER_NET_RE = re.compile(r"^P?\d+V\d*$")

_PASSIVE_PREFIXES = ("R", "C", "L", "D", "FB", "F", "Y")

_IC_METADATA_ALLOWLIST = frozenset({
    "mfr", "mfr_pn", "mfr_abbrev", "fp_disp_name",
    "value", "temp_min", "temp_max",
})


def _ref_prefix(reference: str) -> str:
    """Extract alpha prefix from a reference designator ("R10" -> "R", "FB1" -> "FB")."""
    prefix = ""
    for ch in reference:
        if ch.isalpha():
            prefix += ch
        else:
            break
    return prefix


def _filter_metadata(comp: Component) -> dict[str, str]:
    """Filter component metadata based on component type."""
    prefix = _ref_prefix(comp.reference)
    if prefix in _PASSIVE_PREFIXES:
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


def _is_power_net(name: str, net: Net | None = None) -> bool:
    upper = name.upper()
    if upper in ("GND", "VCC", "VDD", "VSS", "VBAT"):
        return True
    if _POWER_NET_RE.match(upper):
        return True
    if net is not None and net.metadata.get("ClassName") == "PWR":
        return True
    return False


def _format_summary(design: Design) -> list[str]:
    lines = ["=== DESIGN SUMMARY ==="]
    n_comp = len(design.components)
    n_nets = len(design.nets)
    n_pages = len(design.pages)
    lines.append(f"Design: {design.name} | {n_pages} pages | {n_comp} components | {n_nets} nets")

    meta_parts = []
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
        page_meta_parts = []
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

    power_names = sorted(n.name for n in design.nets if _is_power_net(n.name, n))
    if power_names:
        lines.append(f"Power Rails: {', '.join(power_names)}")
        lines.append("")

    return lines


def _format_components(design: Design) -> list[str]:
    net_pin_index = _build_net_pin_index(design)

    lines = ["=== COMPONENTS ===", ""]
    for comp in sorted(design.components, key=lambda c: c.reference):
        page_names = ", ".join(p.name for p in comp.pages)
        lines.append(f"COMPONENT: {comp.reference} | {comp.part} | {comp.description} | Pages: {page_names}")

        for key, value in sorted(_filter_metadata(comp).items()):
            lines.append(f"  {key}: {value}")

        for pin in sorted(comp.pins, key=lambda p: p.designator):
            net_str = _pin_net_str(pin)
            meta_str = ""
            # Filter out default/noise metadata values:
            # - electrical=passive is the default for 88%+ of pins
            filtered = {
                k: v for k, v in pin.metadata.items()
                if not (k == "electrical" and v == "passive")
            }
            if filtered:
                meta_str = "  " + "  ".join(
                    f"{k}={v}" for k, v in sorted(filtered.items())
                )

            # Inline destinations
            dest_str = ""
            if pin.net is not None and not _is_power_net(pin.net.name, pin.net):
                all_refs = net_pin_index.get(id(pin.net), [])
                other_refs = [
                    f"{r}.{d}" for r, d in all_refs
                    if not (r == comp.reference and d == pin.designator)
                ]
                if other_refs:
                    dest_str = "  [" + ", ".join(other_refs) + "]"

            if pin.name:
                lines.append(f"  Pin {pin.designator:<5s}  {pin.name:<15s} -> {net_str}{meta_str}{dest_str}")
            else:
                lines.append(f"  Pin {pin.designator:<5s}  {'':<15s} -> {net_str}{meta_str}{dest_str}")

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

        for pin in sorted(net.pins, key=lambda p: (p.component.reference, p.designator)):
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


# ---- List/show formatters for CLI ----


def _natural_sort_key(ref: str, desig: str) -> list[str | int]:
    """Sort key for natural ordering so C2 < C10."""
    parts: list[str | int] = []
    for token in re.split(r"(\d+)", ref + "." + desig):
        if token.isdigit():
            parts.append(int(token))
        else:
            parts.append(token)
    return parts


def _build_net_pin_index(design: Design) -> dict[int, list[tuple[str, str]]]:
    """Map id(net) to sorted list of (component_ref, pin_designator)."""
    index: dict[int, list[tuple[str, str]]] = {}
    for net in design.nets:
        refs = [(p.component.reference, p.designator) for p in net.pins]
        refs.sort(key=lambda r: _natural_sort_key(r[0], r[1]))
        index[id(net)] = refs
    return index


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


def format_component_table(design: Design) -> str:
    """Format a table of all components: REF | PART | DESCRIPTION | PINS."""
    rows = [
        (c.reference, c.part, c.description, str(len(c.pins)))
        for c in sorted(design.components, key=lambda c: c.reference)
    ]
    if not rows:
        return "No components found."
    return _tabulate(("REF", "PART", "DESCRIPTION", "PINS"), rows)


def format_net_table(design: Design) -> str:
    """Format a table of all nets: NET | ALIASES | PINS | PAGES."""
    rows: list[tuple[str, ...]] = []
    for net in sorted(design.nets, key=lambda n: n.name):
        aliases = ", ".join(sorted(net.aliases)) if net.aliases else ""
        pages = sorted({p.name for pin in net.pins for p in pin.component.pages})
        rows.append((net.name, aliases, str(len(net.pins)), ", ".join(pages)))
    if not rows:
        return "No nets found."
    return _tabulate(("NET", "ALIASES", "PINS", "PAGES"), rows)


def format_page_table(design: Design) -> str:
    """Format a table of all pages: PAGE | COMPONENTS | NETS."""
    rows = [
        (p.name, str(len(p.components)), str(len(p.nets)))
        for p in design.pages
    ]
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

    net_pin_index = _build_net_pin_index(design)
    page_names = ", ".join(p.name for p in comp.pages)
    lines = [f"COMPONENT: {comp.reference} | {comp.part} | {comp.description} | Pages: {page_names}"]

    for key, value in sorted(comp.metadata.items()):
        lines.append(f"  {key}: {value}")

    for pin in sorted(comp.pins, key=lambda p: p.designator):
        net_str = _pin_net_str(pin)
        dest_str = ""
        if pin.net is not None and not _is_power_net(pin.net.name, pin.net):
            all_refs = net_pin_index.get(id(pin.net), [])
            other_refs = [
                f"{r}.{d}" for r, d in all_refs
                if not (r == comp.reference and d == pin.designator)
            ]
            if other_refs:
                dest_str = "  [" + ", ".join(other_refs) + "]"
        if pin.name:
            lines.append(f"  Pin {pin.designator:<5s}  {pin.name:<15s} -> {net_str}{dest_str}")
        else:
            lines.append(f"  Pin {pin.designator:<5s}  {'':<15s} -> {net_str}{dest_str}")

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
