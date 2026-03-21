"""Smoke-test validation for a merged schematic Design.

Checks for structural anomalies that indicate parser bugs or unresolved
connections — things that should never (or rarely) appear in a correctly
parsed schematic.  Operates on the format-agnostic domain model, so it
works for Altium, OrCAD, or any future input format.

Usage::

    from phosphor_eda.validate import validate_design

    findings = validate_design(design)
    for f in findings:
        print(f"{f.severity.value:7s}  [{f.category.value}]  {f.message}")
"""

from __future__ import annotations

import re
from enum import StrEnum, auto

from phosphor_eda.schematic import Design

# ---------------------------------------------------------------------------
# Finding data types
# ---------------------------------------------------------------------------


class Severity(StrEnum):
    ERROR = auto()    # almost certainly a parser bug
    WARNING = auto()  # suspicious, worth investigating
    INFO = auto()     # notable but may be legitimate


class Category(StrEnum):
    EMPTY_NET = auto()
    SINGLE_PIN_NET = auto()
    EMPTY_NET_NAME = auto()
    DUPLICATE_PIN_ON_NET = auto()
    DUPLICATE_PIN_DESIGNATOR = auto()
    COMPONENT_NO_PINS = auto()
    COMPONENT_ALL_UNCONNECTED = auto()
    ORPHAN_PORT = auto()
    POWER_PIN_UNCONNECTED = auto()
    HIGH_UNCONNECTED_RATIO = auto()
    NAME_RESIDUAL_MARKUP = auto()


class Finding:
    """A single validation finding."""

    __slots__ = ("severity", "category", "message", "component", "net", "pin")

    def __init__(
        self,
        severity: Severity,
        category: Category,
        message: str,
        *,
        component: str = "",
        net: str = "",
        pin: str = "",
    ) -> None:
        self.severity = severity
        self.category = category
        self.message = message
        self.component = component
        self.net = net
        self.pin = pin

    def __repr__(self) -> str:
        parts = [f"severity={self.severity.value!r}", f"category={self.category.value!r}"]
        if self.component:
            parts.append(f"component={self.component!r}")
        if self.net:
            parts.append(f"net={self.net!r}")
        if self.pin:
            parts.append(f"pin={self.pin!r}")
        return f"Finding({', '.join(parts)}, message={self.message!r})"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

# Threshold: components with more pins than this are "major ICs"
_MAJOR_IC_THRESHOLD = 20

# If a major IC has more than this fraction unconnected, flag it
_HIGH_UNCONNECTED_FRACTION = 0.80

# Pattern for residual Altium markup or non-printable chars
_BAD_NAME_RE = re.compile(r"[\\~\x00-\x1f\x7f-\x9f]")


def _check_nets(design: Design, findings: list[Finding]) -> None:
    """Check net-level anomalies."""
    for net in design.nets:
        # Empty net name
        if not net.name:
            findings.append(Finding(
                Severity.ERROR, Category.EMPTY_NET_NAME,
                "Net with empty name",
                net="(empty)",
            ))
            continue

        # Residual markup in net name
        if _BAD_NAME_RE.search(net.name):
            findings.append(Finding(
                Severity.ERROR, Category.NAME_RESIDUAL_MARKUP,
                f"Net name contains residual markup: {net.name!r}",
                net=net.name,
            ))

        # Empty net (0 pins)
        if len(net.pins) == 0:
            findings.append(Finding(
                Severity.ERROR, Category.EMPTY_NET,
                f"Net '{net.name}' has 0 pins",
                net=net.name,
            ))

        # Single-pin net (skip if pin is intentionally no-connect)
        elif len(net.pins) == 1:
            pin = net.pins[0]
            if not pin.no_connect:
                findings.append(Finding(
                    Severity.WARNING, Category.SINGLE_PIN_NET,
                    f"Net '{net.name}' has only 1 pin: "
                    f"{pin.component.reference}.{pin.designator}",
                    net=net.name,
                    component=pin.component.reference,
                    pin=pin.designator,
                ))

        # Duplicate component.designator on the same net
        seen: set[tuple[str, str]] = set()
        for pin in net.pins:
            key = (pin.component.reference, pin.designator)
            if key in seen:
                findings.append(Finding(
                    Severity.ERROR, Category.DUPLICATE_PIN_ON_NET,
                    f"Net '{net.name}' has duplicate pin "
                    f"{pin.component.reference}.{pin.designator}",
                    net=net.name,
                    component=pin.component.reference,
                    pin=pin.designator,
                ))
            seen.add(key)


def _check_components(design: Design, findings: list[Finding]) -> None:
    """Check component-level anomalies."""
    for comp in design.components:
        n_pins = len(comp.pins)

        # No pins at all (DNI components and non-electrical items like PCB
        # outlines legitimately have no pins)
        if n_pins == 0:
            if not comp.metadata.get("dni") and not comp.reference.startswith("."):
                findings.append(Finding(
                    Severity.WARNING, Category.COMPONENT_NO_PINS,
                    f"Component {comp.reference} ({comp.part}) has 0 pins",
                    component=comp.reference,
                ))
            continue

        # Duplicate pin designators within a component
        desig_counts: dict[str, int] = {}
        for pin in comp.pins:
            desig_counts[pin.designator] = desig_counts.get(pin.designator, 0) + 1
        for desig, count in desig_counts.items():
            if count > 1:
                findings.append(Finding(
                    Severity.ERROR, Category.DUPLICATE_PIN_DESIGNATOR,
                    f"Component {comp.reference} has {count}x pin '{desig}'",
                    component=comp.reference,
                    pin=desig,
                ))

        # Count connection states
        n_connected = sum(1 for p in comp.pins if p.net is not None)
        n_nc = sum(1 for p in comp.pins if p.no_connect)
        n_unconnected = n_pins - n_connected - n_nc

        # All pins unconnected (no net, no no-connect) on non-trivial components
        if n_connected == 0 and n_nc == 0 and n_pins > 1:
            findings.append(Finding(
                Severity.WARNING, Category.COMPONENT_ALL_UNCONNECTED,
                f"Component {comp.reference} ({comp.part}): "
                f"all {n_pins} pins unconnected",
                component=comp.reference,
            ))

        # High unconnected ratio on major ICs
        elif n_pins > _MAJOR_IC_THRESHOLD and n_unconnected > 0:
            ratio = n_unconnected / n_pins
            if ratio > _HIGH_UNCONNECTED_FRACTION:
                findings.append(Finding(
                    Severity.WARNING, Category.HIGH_UNCONNECTED_RATIO,
                    f"Component {comp.reference} ({comp.part}): "
                    f"{n_unconnected}/{n_pins} pins "
                    f"({ratio:.0%}) unconnected",
                    component=comp.reference,
                ))

        # Power pins with no net
        for pin in comp.pins:
            elec = pin.metadata.get("electrical")
            if elec == "power" and pin.net is None and not pin.no_connect:
                findings.append(Finding(
                    Severity.WARNING, Category.POWER_PIN_UNCONNECTED,
                    f"{comp.reference}.{pin.designator} ({pin.name}) "
                    f"is a power pin with no net",
                    component=comp.reference,
                    pin=pin.designator,
                ))


def _check_pin_names(design: Design, findings: list[Finding]) -> None:
    """Check for residual markup in pin/component names."""
    for comp in design.components:
        for pin in comp.pins:
            if pin.name and _BAD_NAME_RE.search(pin.name):
                findings.append(Finding(
                    Severity.ERROR, Category.NAME_RESIDUAL_MARKUP,
                    f"Pin name contains residual markup: "
                    f"{comp.reference}.{pin.designator} = {pin.name!r}",
                    component=comp.reference,
                    pin=pin.designator,
                ))


def _check_ports(design: Design, findings: list[Finding]) -> None:
    """Check port-level anomalies."""
    # Count how many distinct pages each port name appears on
    port_pages: dict[str, set[str]] = {}
    for page in design.pages:
        for port in page.ports:
            port_pages.setdefault(port.name, set()).add(page.name)

    for port_name, pages in port_pages.items():
        if len(pages) < 2:
            findings.append(Finding(
                Severity.WARNING, Category.ORPHAN_PORT,
                f"Port '{port_name}' appears on only 1 page "
                f"({next(iter(pages))}), no bridging occurred",
                net=port_name,
            ))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_design(design: Design) -> list[Finding]:
    """Run all smoke-test checks on a merged Design.

    Returns a list of findings sorted by severity (errors first),
    then by category, then by message.
    """
    findings: list[Finding] = []
    _check_nets(design, findings)
    _check_components(design, findings)
    _check_pin_names(design, findings)
    _check_ports(design, findings)

    # Sort: errors first, then warnings, then info; within each, by category
    severity_order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    findings.sort(key=lambda f: (severity_order[f.severity], f.category, f.message))
    return findings
