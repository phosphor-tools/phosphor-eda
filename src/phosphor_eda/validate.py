"""Smoke-test validation for a merged Schematic.

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
from typing import TYPE_CHECKING, final, override

if TYPE_CHECKING:
    from phosphor_eda.schematic import Net, Page, Pin, Schematic

# ---------------------------------------------------------------------------
# Finding data types
# ---------------------------------------------------------------------------


class Severity(StrEnum):
    ERROR = auto()  # almost certainly a parser bug
    WARNING = auto()  # suspicious, worth investigating
    INFO = auto()  # notable but may be legitimate


class Category(StrEnum):
    DUPLICATE_ID = auto()
    RELATIONSHIP_MISMATCH = auto()
    EMPTY_NET = auto()
    SINGLE_PIN_NET = auto()
    EMPTY_NET_NAME = auto()
    DUPLICATE_PIN_ON_NET = auto()
    DUPLICATE_PIN_DESIGNATOR = auto()
    COMPONENT_NO_PINS = auto()
    COMPONENT_ALL_UNCONNECTED = auto()
    POWER_PIN_UNCONNECTED = auto()
    HIGH_UNCONNECTED_RATIO = auto()
    NAME_RESIDUAL_MARKUP = auto()


@final
class Finding:
    """A single validation finding."""

    __slots__: tuple[str, ...] = ("severity", "category", "message", "component", "net", "pin")

    severity: Severity
    category: Category
    message: str
    component: str
    net: str
    pin: str

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

    @override
    def __repr__(self) -> str:
        parts = [
            f"severity={self.severity.value!r}",
            f"category={self.category.value!r}",
        ]
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


def _append_duplicate_id_findings(
    items: list[tuple[str, str]],
    field_name: str,
    findings: list[Finding],
) -> None:
    seen: set[str] = set()
    reported: set[str] = set()
    for item_id, label in items:
        if item_id in seen and item_id not in reported:
            findings.append(
                Finding(
                    Severity.ERROR,
                    Category.DUPLICATE_ID,
                    f"duplicate {field_name}: {item_id}",
                    component=label if field_name == "Component.id" else "",
                    net=label if field_name == "Net.id" else "",
                    pin=label if field_name == "Pin.id" else "",
                )
            )
            reported.add(item_id)
        seen.add(item_id)


def _all_component_pins(design: Schematic) -> list[Pin]:
    pins: list[Pin] = []
    for comp in design.components:
        pins.extend(comp.pins)
    return pins


def _find_page_by_id(design: Schematic, page_id: str) -> Page | None:
    for page in design.pages:
        if page.id == page_id:
            return page
    return None


def _find_net_by_id(design: Schematic, net_id: str) -> Net | None:
    for net in design.nets:
        if net.id == net_id:
            return net
    return None


def _has_component_id(page: Page, component_id: str) -> bool:
    return any(comp.id == component_id for comp in page.components)


def _has_page_id(pages: list[Page], page_id: str) -> bool:
    return any(page.id == page_id for page in pages)


def _has_pin_id(net: Net, pin_id: str) -> bool:
    return any(pin.id == pin_id for pin in net.pins)


def _check_identity_and_links(design: Schematic, findings: list[Finding]) -> None:
    """Check stable IDs and bidirectional object links."""
    _append_duplicate_id_findings(
        [(page.id, page.name) for page in design.pages],
        "Page.id",
        findings,
    )
    _append_duplicate_id_findings(
        [(comp.id, comp.reference) for comp in design.components],
        "Component.id",
        findings,
    )
    _append_duplicate_id_findings(
        [(net.id, net.name) for net in design.nets],
        "Net.id",
        findings,
    )
    _append_duplicate_id_findings(
        [
            (pin.id, f"{pin.component.reference}.{pin.designator}")
            for pin in _all_component_pins(design)
        ],
        "Pin.id",
        findings,
    )

    for comp in design.components:
        for page in comp.pages:
            target_page = _find_page_by_id(design, page.id) or page
            if not _has_component_id(target_page, comp.id):
                message = (
                    f"Component.pages does not match Page.components: {comp.reference} "
                    + f"lists page '{page.name}', but page does not list component"
                )
                findings.append(
                    Finding(
                        Severity.ERROR,
                        Category.RELATIONSHIP_MISMATCH,
                        message,
                        component=comp.reference,
                    )
                )

    for page in design.pages:
        for comp in page.components:
            if not _has_page_id(comp.pages, page.id):
                message = (
                    f"Page.components does not match Component.pages: page '{page.name}' "
                    + f"lists {comp.reference}, but component does not list page"
                )
                findings.append(
                    Finding(
                        Severity.ERROR,
                        Category.RELATIONSHIP_MISMATCH,
                        message,
                        component=comp.reference,
                    )
                )

    for pin in _all_component_pins(design):
        if pin.net is not None:
            target_net = _find_net_by_id(design, pin.net.id) or pin.net
            if _has_pin_id(target_net, pin.id):
                continue
            message = (
                f"Pin.net does not match Net.pins: {pin.component.reference}."
                + f"{pin.designator} points to net '{pin.net.name}', but the net does not "
                + "list the pin"
            )
            findings.append(
                Finding(
                    Severity.ERROR,
                    Category.RELATIONSHIP_MISMATCH,
                    message,
                    component=pin.component.reference,
                    net=pin.net.name,
                    pin=pin.designator,
                )
            )

    for net in design.nets:
        for pin in net.pins:
            if pin.net is None or pin.net.id != net.id:
                actual = pin.net.name if pin.net is not None else "(none)"
                message = (
                    f"Net.pins does not match Pin.net: net '{net.name}' lists "
                    + f"{pin.component.reference}.{pin.designator}, but Pin.net is {actual}"
                )
                findings.append(
                    Finding(
                        Severity.ERROR,
                        Category.RELATIONSHIP_MISMATCH,
                        message,
                        component=pin.component.reference,
                        net=net.name,
                        pin=pin.designator,
                    )
                )


def _check_nets(design: Schematic, findings: list[Finding]) -> None:
    """Check net-level anomalies."""
    for net in design.nets:
        # Empty net name
        if not net.name:
            findings.append(
                Finding(
                    Severity.ERROR,
                    Category.EMPTY_NET_NAME,
                    "Net with empty name",
                    net="(empty)",
                )
            )
            continue

        # Residual markup in net name
        if _BAD_NAME_RE.search(net.name):
            findings.append(
                Finding(
                    Severity.ERROR,
                    Category.NAME_RESIDUAL_MARKUP,
                    f"Net name contains residual markup: {net.name!r}",
                    net=net.name,
                )
            )

        # Empty net (0 pins)
        if len(net.pins) == 0:
            findings.append(
                Finding(
                    Severity.ERROR,
                    Category.EMPTY_NET,
                    f"Net '{net.name}' has 0 pins",
                    net=net.name,
                )
            )

        # Single-pin net (skip if pin is intentionally no-connect)
        elif len(net.pins) == 1:
            pin = net.pins[0]
            if not pin.no_connect:
                message = (
                    f"Net '{net.name}' has only 1 pin: "
                    + f"{pin.component.reference}.{pin.designator}"
                )
                findings.append(
                    Finding(
                        Severity.WARNING,
                        Category.SINGLE_PIN_NET,
                        message,
                        net=net.name,
                        component=pin.component.reference,
                        pin=pin.designator,
                    )
                )

        # Duplicate logical component pin identity on the same net
        seen: set[tuple[str, str]] = set()
        for pin in net.pins:
            key = (pin.component.id, pin.designator)
            if key in seen:
                message = (
                    f"Net '{net.name}' has duplicate pin {pin.component.reference}."
                    + f"{pin.designator} (component.id, pin.designator)"
                )
                findings.append(
                    Finding(
                        Severity.ERROR,
                        Category.DUPLICATE_PIN_ON_NET,
                        message,
                        net=net.name,
                        component=pin.component.reference,
                        pin=pin.designator,
                    )
                )
            seen.add(key)


def _check_components(design: Schematic, findings: list[Finding]) -> None:
    """Check component-level anomalies."""
    for comp in design.components:
        n_pins = len(comp.pins)

        # No pins at all (DNI components and non-electrical items like PCB
        # outlines legitimately have no pins)
        if n_pins == 0:
            if not comp.metadata.get("dni") and not comp.reference.startswith("."):
                findings.append(
                    Finding(
                        Severity.WARNING,
                        Category.COMPONENT_NO_PINS,
                        f"Component {comp.reference} ({comp.part}) has 0 pins",
                        component=comp.reference,
                    )
                )
            continue

        # Duplicate pin designators within a component
        desig_counts: dict[str, int] = {}
        for pin in comp.pins:
            desig_counts[pin.designator] = desig_counts.get(pin.designator, 0) + 1
        for desig, count in desig_counts.items():
            if count > 1:
                message = (
                    f"Component {comp.reference} has {count}x pin '{desig}' "
                    + "duplicate (component.id, pin.designator)"
                )
                findings.append(
                    Finding(
                        Severity.ERROR,
                        Category.DUPLICATE_PIN_DESIGNATOR,
                        message,
                        component=comp.reference,
                        pin=desig,
                    )
                )

        # Count connection states
        n_connected = sum(1 for p in comp.pins if p.net is not None)
        n_nc = sum(1 for p in comp.pins if p.no_connect)
        n_unconnected = n_pins - n_connected - n_nc

        # All pins unconnected (no net, no no-connect) on non-trivial components
        if n_connected == 0 and n_nc == 0 and n_pins > 1:
            findings.append(
                Finding(
                    Severity.WARNING,
                    Category.COMPONENT_ALL_UNCONNECTED,
                    f"Component {comp.reference} ({comp.part}): all {n_pins} pins unconnected",
                    component=comp.reference,
                )
            )

        # High unconnected ratio on major ICs
        elif n_pins > _MAJOR_IC_THRESHOLD and n_unconnected > 0:
            ratio = n_unconnected / n_pins
            if ratio > _HIGH_UNCONNECTED_FRACTION:
                message = (
                    f"Component {comp.reference} ({comp.part}): {n_unconnected}/{n_pins} "
                    + f"pins ({ratio:.0%}) unconnected"
                )
                findings.append(
                    Finding(
                        Severity.WARNING,
                        Category.HIGH_UNCONNECTED_RATIO,
                        message,
                        component=comp.reference,
                    )
                )

        # Power pins with no net
        for pin in comp.pins:
            elec = pin.metadata.get("electrical")
            if elec == "power" and pin.net is None and not pin.no_connect:
                message = (
                    f"{comp.reference}.{pin.designator} ({pin.name}) is a power pin "
                    + "with no net"
                )
                findings.append(
                    Finding(
                        Severity.WARNING,
                        Category.POWER_PIN_UNCONNECTED,
                        message,
                        component=comp.reference,
                        pin=pin.designator,
                    )
                )


def _check_pin_names(design: Schematic, findings: list[Finding]) -> None:
    """Check for residual markup in pin/component names."""
    for comp in design.components:
        for pin in comp.pins:
            if pin.name and _BAD_NAME_RE.search(pin.name):
                message = (
                    f"Pin name contains residual markup: {comp.reference}."
                    + f"{pin.designator} = {pin.name!r}"
                )
                findings.append(
                    Finding(
                        Severity.ERROR,
                        Category.NAME_RESIDUAL_MARKUP,
                        message,
                        component=comp.reference,
                        pin=pin.designator,
                    )
                )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_design(design: Schematic) -> list[Finding]:
    """Run all smoke-test checks on a merged Schematic.

    Returns a list of findings sorted by severity (errors first),
    then by category, then by message.
    """
    findings: list[Finding] = []
    _check_identity_and_links(design, findings)
    _check_nets(design, findings)
    _check_components(design, findings)
    _check_pin_names(design, findings)

    # Sort: errors first, then warnings, then info; within each, by category
    severity_order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    findings.sort(key=lambda f: (severity_order[f.severity], f.category, f.message))
    return findings
