"""Tests for schematic validation smoke checks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from phosphor_eda.domain.schematic import (
    Component as DomainComponent,
)
from phosphor_eda.domain.schematic import (
    Net as DomainNet,
)
from phosphor_eda.domain.schematic import (
    Page as DomainPage,
)
from phosphor_eda.domain.schematic import (
    Pin as DomainPin,
)
from phosphor_eda.domain.schematic import (
    Schematic,
    ScopeId,
)
from phosphor_eda.query.validate import Category, Finding, Severity, validate_design

if TYPE_CHECKING:
    from collections.abc import Sequence


class Page(DomainPage):
    def __init__(
        self,
        *,
        name: str,
        id: str = "",
        source_file: str = "",
        scope_id: ScopeId | None = None,
        components: list[DomainComponent] | None = None,
        nets: list[DomainNet] | None = None,
        annotations: list[str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            id=id or f"page:{name}",
            name=name,
            source_file=source_file,
            scope_id=scope_id or ScopeId(path=()),
            components=components or [],
            nets=nets or [],
            annotations=annotations or [],
            metadata=metadata or {},
        )


class Component(DomainComponent):
    def __init__(
        self,
        *,
        reference: str,
        part: str,
        description: str,
        id: str = "",
        pins: list[DomainPin] | None = None,
        pages: list[DomainPage] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            id=id or f"component:{reference}",
            reference=reference,
            part=part,
            description=description,
            pins=pins or [],
            pages=pages or [],
            metadata=metadata or {},
        )


class Net(DomainNet):
    def __init__(
        self,
        *,
        name: str,
        id: str = "",
        pins: list[DomainPin] | None = None,
        pages: list[DomainPage] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            id=id or f"net:{name}",
            name=name,
            pins=pins or [],
            pages=pages or [],
            metadata=metadata or {},
        )


class Pin(DomainPin):
    def __init__(
        self,
        *,
        designator: str,
        name: str,
        component: DomainComponent,
        id: str = "",
        net: DomainNet | None = None,
        no_connect: bool = False,
        metadata: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            id=id or f"pin:{component.id}:{designator}",
            designator=designator,
            name=name,
            component=component,
            net=net,
            no_connect=no_connect,
            metadata=metadata or {},
        )


def _make_pin(comp: Component, desig: str, name: str, net: Net | None) -> None:
    pin = Pin(designator=desig, name=name, component=comp, net=net, metadata={})
    comp.pins.append(pin)
    if net is not None:
        net.pins.append(pin)


def _simple_design(
    nets: Sequence[DomainNet] | None = None,
    components: Sequence[DomainComponent] | None = None,
    pages: Sequence[DomainPage] | None = None,
) -> Schematic:
    return Schematic(
        name="test",
        nets=list(nets) if nets is not None else [],
        components=list(components) if components is not None else [],
        pages=list(pages) if pages is not None else [Page(name="Main")],
    )


def _errors(findings: Sequence[Finding]) -> list[Finding]:
    return [finding for finding in findings if finding.severity == Severity.ERROR]


def _assert_error(findings: Sequence[Finding], category: Category, message_part: str) -> None:
    matches = [
        finding
        for finding in findings
        if finding.severity == Severity.ERROR
        and finding.category == category
        and message_part in finding.message
    ]
    assert matches, findings


# --- Structural identity/link checks ---


def test_duplicate_component_id_is_error():
    page = Page(name="A")
    c1 = Component(
        id="component:duplicate",
        reference="U1",
        part="IC",
        description="",
        pages=[page],
    )
    c2 = Component(
        id="component:duplicate",
        reference="U2",
        part="IC",
        description="",
        pages=[page],
    )
    page.components = [c1, c2]
    findings = validate_design(_simple_design(components=[c1, c2], pages=[page]))
    _assert_error(findings, Category.DUPLICATE_ID, "duplicate Component.id")


def test_duplicate_page_id_is_error():
    page_a = Page(id="page:duplicate", name="A")
    page_b = Page(id="page:duplicate", name="B")
    findings = validate_design(_simple_design(pages=[page_a, page_b]))
    _assert_error(findings, Category.DUPLICATE_ID, "duplicate Page.id")


def test_duplicate_pin_id_is_error():
    page = Page(name="A")
    net = Net(name="SIG", pages=[page])
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    pin_a = Pin(id="pin:duplicate", designator="1", name="A", component=comp, net=net)
    pin_b = Pin(id="pin:duplicate", designator="2", name="B", component=comp, net=net)
    comp.pins = [pin_a, pin_b]
    net.pins = [pin_a, pin_b]
    page.components = [comp]
    page.nets = [net]
    findings = validate_design(_simple_design(nets=[net], components=[comp], pages=[page]))
    _assert_error(findings, Category.DUPLICATE_ID, "duplicate Pin.id")


def test_duplicate_net_id_is_error():
    page = Page(name="A")
    net_a = Net(id="net:duplicate", name="A", pages=[page])
    net_b = Net(id="net:duplicate", name="B", pages=[page])
    page.nets = [net_a, net_b]
    findings = validate_design(_simple_design(nets=[net_a, net_b], pages=[page]))
    _assert_error(findings, Category.DUPLICATE_ID, "duplicate Net.id")


def test_duplicate_component_references_across_independent_scopes_are_allowed():
    page_a = Page(name="MCU_A", id="page:mcu-a", scope_id=ScopeId(path=("mcu_a",)))
    page_b = Page(name="MCU_B", id="page:mcu-b", scope_id=ScopeId(path=("mcu_b",)))
    comp_a = Component(
        id="component:mcu-a:u7",
        reference="U7",
        part="MCU",
        description="",
        pages=[page_a],
    )
    comp_b = Component(
        id="component:mcu-b:u7",
        reference="U7",
        part="MCU",
        description="",
        pages=[page_b],
    )
    net_a = Net(id="net:mcu-a:sig", name="SIG", pages=[page_a])
    net_b = Net(id="net:mcu-b:sig", name="SIG", pages=[page_b])
    _make_pin(comp_a, "1", "SIG", net_a)
    _make_pin(comp_b, "1", "SIG", net_b)
    page_a.components = [comp_a]
    page_b.components = [comp_b]
    page_a.nets = [net_a]
    page_b.nets = [net_b]
    findings = validate_design(
        _simple_design(
            nets=[net_a, net_b],
            components=[comp_a, comp_b],
            pages=[page_a, page_b],
        )
    )
    assert not _errors(findings)


def test_duplicate_component_id_pin_designator_is_error():
    page = Page(name="A")
    net = Net(name="SIG", pages=[page])
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    _make_pin(comp, "1", "A", net)
    _make_pin(comp, "1", "B", net)
    page.components = [comp]
    page.nets = [net]
    findings = validate_design(_simple_design(nets=[net], components=[comp], pages=[page]))
    _assert_error(findings, Category.DUPLICATE_PIN_DESIGNATOR, "(component.id, pin.designator)")


def test_pin_net_missing_from_net_pins_is_error():
    page = Page(name="A")
    net = Net(name="SIG", pages=[page])
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    pin = Pin(designator="1", name="SIG", component=comp, net=net)
    comp.pins = [pin]
    page.components = [comp]
    page.nets = [net]
    findings = validate_design(_simple_design(nets=[net], components=[comp], pages=[page]))
    _assert_error(findings, Category.RELATIONSHIP_MISMATCH, "Pin.net does not match Net.pins")


def test_net_pin_missing_pin_net_is_error():
    page = Page(name="A")
    net = Net(name="SIG", pages=[page])
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    pin = Pin(designator="1", name="SIG", component=comp, net=None)
    comp.pins = [pin]
    net.pins = [pin]
    page.components = [comp]
    page.nets = [net]
    findings = validate_design(_simple_design(nets=[net], components=[comp], pages=[page]))
    _assert_error(findings, Category.RELATIONSHIP_MISMATCH, "Net.pins does not match Pin.net")


def test_component_page_missing_from_page_components_is_error():
    page = Page(name="A")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    findings = validate_design(_simple_design(components=[comp], pages=[page]))
    _assert_error(
        findings,
        Category.RELATIONSHIP_MISMATCH,
        "Component.pages does not match Page.components",
    )


def test_page_component_missing_from_component_pages_is_error():
    page = Page(name="A")
    comp = Component(reference="U1", part="IC", description="", pages=[])
    page.components = [comp]
    findings = validate_design(_simple_design(components=[comp], pages=[page]))
    _assert_error(
        findings,
        Category.RELATIONSHIP_MISMATCH,
        "Page.components does not match Component.pages",
    )


def test_page_net_missing_from_net_pages_is_error():
    page = Page(name="A")
    net = Net(name="SIG", pages=[])
    page.nets = [net]
    findings = validate_design(_simple_design(nets=[net], pages=[page]))
    _assert_error(
        findings,
        Category.RELATIONSHIP_MISMATCH,
        "Page.nets does not match Net.pages",
    )


def test_net_page_missing_from_page_nets_is_error():
    page = Page(name="A")
    net = Net(name="SIG", pages=[page])
    findings = validate_design(_simple_design(nets=[net], pages=[page]))
    _assert_error(
        findings,
        Category.RELATIONSHIP_MISMATCH,
        "Net.pages does not match Page.nets",
    )


def test_pin_net_relationship_uses_net_id_identity():
    page = Page(name="A")
    net = Net(id="net:sig", name="SIG", pages=[page])
    same_net = Net(id=net.id, name=net.name, pages=[page])
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    pin = Pin(designator="1", name="SIG", component=comp, net=same_net)
    comp.pins = [pin]
    net.pins = [pin]
    page.components = [comp]
    page.nets = [net]

    findings = validate_design(_simple_design(nets=[net], components=[comp], pages=[page]))

    assert not any(f.category == Category.RELATIONSHIP_MISMATCH for f in findings)


def test_component_page_relationship_uses_page_id_identity():
    page = Page(id="page:a", name="A")
    same_page = Page(id=page.id, name=page.name)
    comp = Component(reference="U1", part="IC", description="", pages=[same_page])
    page.components = [comp]

    findings = validate_design(_simple_design(components=[comp], pages=[page]))

    assert not any(f.category == Category.RELATIONSHIP_MISMATCH for f in findings)


# --- Net checks ---


def test_empty_net():
    net = Net(name="ORPHAN")
    findings = validate_design(_simple_design(nets=[net]))
    cats = {f.category for f in findings}
    assert Category.EMPTY_NET in cats


def test_single_pin_net():
    page = Page(name="A")
    net = Net(name="LONELY")
    comp = Component(reference="R1", part="R", description="", pages=[page])
    _make_pin(comp, "1", "", net)
    design = _simple_design(nets=[net], components=[comp], pages=[page])
    findings = validate_design(design)
    single = [f for f in findings if f.category == Category.SINGLE_PIN_NET]
    assert len(single) == 1
    assert "LONELY" in single[0].message


def test_two_pin_net_is_fine():
    page = Page(name="A")
    net = Net(name="SIG")
    c1 = Component(reference="R1", part="R", description="", pages=[page])
    c2 = Component(reference="R2", part="R", description="", pages=[page])
    _make_pin(c1, "1", "", net)
    _make_pin(c2, "1", "", net)
    design = _simple_design(nets=[net], components=[c1, c2], pages=[page])
    findings = validate_design(design)
    net_findings = [
        f
        for f in findings
        if f.category
        in (
            Category.EMPTY_NET,
            Category.SINGLE_PIN_NET,
        )
    ]
    assert len(net_findings) == 0


def test_empty_net_name():
    net = Net(name="")
    findings = validate_design(_simple_design(nets=[net]))
    assert any(f.category == Category.EMPTY_NET_NAME for f in findings)


def test_duplicate_pin_on_net():
    page = Page(name="A")
    net = Net(name="SIG")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    # Manually add the same designator twice
    p1 = Pin(designator="1", name="A", component=comp, net=net, metadata={})
    p2 = Pin(designator="1", name="A", component=comp, net=net, metadata={})
    comp.pins.extend([p1, p2])
    net.pins.extend([p1, p2])
    design = _simple_design(nets=[net], components=[comp], pages=[page])
    findings = validate_design(design)
    assert any(f.category == Category.DUPLICATE_PIN_ON_NET for f in findings)


# --- Component checks ---


def test_component_no_pins():
    page = Page(name="A")
    comp = Component(reference="TP1", part="TestPoint", description="", pages=[page])
    design = _simple_design(components=[comp], pages=[page])
    findings = validate_design(design)
    assert any(f.category == Category.COMPONENT_NO_PINS for f in findings)


def test_duplicate_pin_designator():
    page = Page(name="A")
    net = Net(name="SIG")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    _make_pin(comp, "1", "A", net)
    _make_pin(comp, "1", "B", net)  # duplicate designator
    design = _simple_design(nets=[net], components=[comp], pages=[page])
    findings = validate_design(design)
    assert any(f.category == Category.DUPLICATE_PIN_DESIGNATOR for f in findings)


def test_all_pins_unconnected():
    page = Page(name="A")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    _make_pin(comp, "1", "A", None)
    _make_pin(comp, "2", "B", None)
    design = _simple_design(components=[comp], pages=[page])
    findings = validate_design(design)
    assert any(f.category == Category.COMPONENT_ALL_UNCONNECTED for f in findings)


def test_power_pin_unconnected():
    page = Page(name="A")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    pin = Pin(
        designator="1",
        name="VCC",
        component=comp,
        net=None,
        metadata={"electrical": "power"},
    )
    comp.pins.append(pin)
    # Add a connected pin so it doesn't also trigger all-unconnected
    net = Net(name="SIG")
    _make_pin(comp, "2", "OUT", net)
    design = _simple_design(nets=[net], components=[comp], pages=[page])
    findings = validate_design(design)
    assert any(f.category == Category.POWER_PIN_UNCONNECTED for f in findings)


def test_power_pin_connected_is_fine():
    page = Page(name="A")
    net = Net(name="VCC")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    pin = Pin(
        designator="1",
        name="VCC",
        component=comp,
        net=net,
        metadata={"electrical": "power"},
    )
    comp.pins.append(pin)
    net.pins.append(pin)
    design = _simple_design(nets=[net], components=[comp], pages=[page])
    findings = validate_design(design)
    assert not any(f.category == Category.POWER_PIN_UNCONNECTED for f in findings)


# --- Name checks ---


def test_residual_backslash_in_net_name():
    net = Net(name="D\\R\\D\\Y\\")
    findings = validate_design(_simple_design(nets=[net]))
    assert any(f.category == Category.NAME_RESIDUAL_MARKUP for f in findings)


def test_residual_backslash_in_pin_name():
    page = Page(name="A")
    net = Net(name="SIG")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    pin = Pin(
        designator="1",
        name="D\\R\\D\\Y\\",
        component=comp,
        net=net,
        metadata={},
    )
    comp.pins.append(pin)
    net.pins.append(pin)
    design = _simple_design(nets=[net], components=[comp], pages=[page])
    findings = validate_design(design)
    assert any(f.category == Category.NAME_RESIDUAL_MARKUP for f in findings)


def test_clean_names_no_markup_findings():
    page = Page(name="A")
    net = Net(name="ADC_SCLK")
    comp = Component(reference="U1", part="IC", description="", pages=[page])
    _make_pin(comp, "1", "SCLK", net)
    _make_pin(comp, "2", "MISO", net)
    design = _simple_design(nets=[net], components=[comp], pages=[page])
    findings = validate_design(design)
    assert not any(f.category == Category.NAME_RESIDUAL_MARKUP for f in findings)


# --- Removed port checks ---


def test_orphan_port_validation_category_is_gone():
    assert "ORPHAN_PORT" not in Category.__members__


# --- Sorting ---


def test_findings_sorted_errors_first():
    page = Page(name="A")
    net_empty_name = Net(name="")  # error: empty name
    net_single = Net(name="LONELY")  # warning: single-pin
    comp = Component(reference="R1", part="R", description="", pages=[page])
    _make_pin(comp, "1", "", net_single)
    design = _simple_design(
        nets=[net_empty_name, net_single],
        components=[comp],
        pages=[page],
    )
    findings = validate_design(design)
    severities = [f.severity for f in findings]
    # All errors should come before all warnings
    error_indices = [i for i, s in enumerate(severities) if s == Severity.ERROR]
    warning_indices = [i for i, s in enumerate(severities) if s == Severity.WARNING]
    if error_indices and warning_indices:
        assert max(error_indices) < min(warning_indices)


# --- Suppression tests ---


def test_component_no_pins_suppressed_for_dni():
    """DNI (Do Not Install) components with 0 pins should not warn."""
    page = Page(name="A")
    comp = Component(
        reference="FD1",
        part="Fiducial",
        description="",
        pages=[page],
        metadata={"dni": "true"},
    )
    design = _simple_design(components=[comp], pages=[page])
    findings = validate_design(design)
    assert not any(f.category == Category.COMPONENT_NO_PINS for f in findings)


def test_component_no_pins_still_warns_without_dni():
    """Non-DNI components with 0 pins should still warn."""
    page = Page(name="A")
    comp = Component(
        reference="TP1",
        part="TestPoint",
        description="",
        pages=[page],
    )
    design = _simple_design(components=[comp], pages=[page])
    findings = validate_design(design)
    assert any(f.category == Category.COMPONENT_NO_PINS for f in findings)


def test_single_pin_net_suppressed_for_no_connect():
    """Single-pin nets where the pin is no_connect should not warn."""
    page = Page(name="A")
    net = Net(name="JTAG_TDI")
    comp = Component(reference="U1", part="MCU", description="", pages=[page])
    pin = Pin(
        designator="F14",
        name="TDI",
        component=comp,
        net=net,
        no_connect=True,
        metadata={},
    )
    comp.pins.append(pin)
    net.pins.append(pin)
    design = _simple_design(nets=[net], components=[comp], pages=[page])
    findings = validate_design(design)
    assert not any(f.category == Category.SINGLE_PIN_NET for f in findings)


def test_single_pin_net_still_warns_without_no_connect():
    """Single-pin nets without no_connect should still warn."""
    page = Page(name="A")
    net = Net(name="ORPHAN_SIG")
    comp = Component(reference="U1", part="MCU", description="", pages=[page])
    _make_pin(comp, "1", "SIG", net)
    design = _simple_design(nets=[net], components=[comp], pages=[page])
    findings = validate_design(design)
    assert any(f.category == Category.SINGLE_PIN_NET for f in findings)
