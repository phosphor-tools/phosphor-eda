"""Text serializer for the schematic domain model.

Produces grep-friendly, LLM-optimized text output in three sections:
design summary, component-centric view, and net-centric view.
"""

from __future__ import annotations

import re
from pathlib import Path

from ecad_tools.schematic import Design, Pin
from ecad_tools.validate import Severity, validate_design

_MAJOR_IC_PIN_THRESHOLD = 4

_POWER_NET_RE = re.compile(r"^P?\d+V\d*$")


def _pin_net_str(pin: Pin) -> str:
    if pin.no_connect:
        return "(no-connect)"
    if pin.net is None:
        return "(unconnected)"
    return pin.net.name


def _is_power_net(name: str) -> bool:
    upper = name.upper()
    if upper in ("GND", "VCC", "VDD", "VSS", "VBAT"):
        return True
    return bool(_POWER_NET_RE.match(upper))


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

    power_names = sorted(n.name for n in design.nets if _is_power_net(n.name))
    if power_names:
        lines.append(f"Power Rails: {', '.join(power_names)}")
        lines.append("")

    return lines


def _format_components(design: Design) -> list[str]:
    lines = ["=== COMPONENTS ===", ""]
    for comp in sorted(design.components, key=lambda c: c.reference):
        page_names = ", ".join(p.name for p in comp.pages)
        lines.append(f"COMPONENT: {comp.reference} | {comp.part} | {comp.description} | Pages: {page_names}")

        for key, value in sorted(comp.metadata.items()):
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
            if pin.name:
                lines.append(f"  Pin {pin.designator:<5s}  {pin.name:<15s} -> {net_str}{meta_str}")
            else:
                lines.append(f"  Pin {pin.designator:<5s}  {'':<15s} -> {net_str}{meta_str}")

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
