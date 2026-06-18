from __future__ import annotations

import pytest

from phosphor_eda.domain.schematic import (
    Bus,
    BusKind,
    Component,
    ComponentOccurrence,
    Net,
    Page,
    Schematic,
    ScopeId,
)
from phosphor_eda.query.selectors import (
    parse_selector,
    resolve_components,
    resolve_nets,
    resolve_string_selectors,
)


def _selector_design() -> Schematic:
    scope = ScopeId(path=("root",))
    page = Page(id="page:main", name="Main", scope_id=scope)
    u1 = Component(id="component:u1", reference="U1", part="MCU", description="")
    u2 = Component(id="component:u2", reference="U2", part="MCU", description="")
    j1 = Component(id="component:j1", reference="J1", part="Conn", description="")
    literal_bang = Component(id="component:bang", reference="!RESET", part="TP", description="")
    u1.occurrences.append(
        ComponentOccurrence(
            id="occ:u1",
            component=u1,
            page=page,
            scope_id=scope,
            source_id="src:u1",
            physical_designator="U1.3",
        )
    )
    for component in (u1, u2, j1, literal_bang):
        component.pages.append(page)
    page.components.extend([u1, u2, j1, literal_bang])

    usb_dp = Net(id="net:usb-dp", name="USB_DP", pages=[page], aliases={"USB_D+"})
    usb_dm = Net(id="net:usb-dm", name="USB_DM", pages=[page], aliases={"USB_D-"})
    gnd = Net(id="net:gnd", name="GND", pages=[page])
    page.nets.extend([usb_dp, usb_dm, gnd])
    bus = Bus(id="bus:usb", name="USB", kind=BusKind.GROUP, members=[usb_dp, usb_dm])
    return Schematic(
        name="selectors",
        pages=[page],
        components=[u1, u2, j1, literal_bang],
        nets=[usb_dp, usb_dm, gnd],
        buses=[bus],
    )


def test_parse_selector_supports_negative_and_literal_bang() -> None:
    negative = parse_selector("!GND*")
    literal = parse_selector(r"\!RESET")

    assert negative.negative is True
    assert negative.pattern == "GND*"
    assert literal.negative is False
    assert literal.pattern == "!RESET"


def test_exact_selector_uses_existing_component_resolution() -> None:
    design = _selector_design()

    assert [component.id for component in resolve_components(design, ["U1.3"])] == ["component:u1"]


def test_glob_selectors_support_shell_patterns_and_exclusions() -> None:
    design = _selector_design()

    selected = resolve_components(design, ["U?", "!U2"])

    assert [component.reference for component in selected] == ["U1"]


def test_only_negative_selector_starts_from_all_objects() -> None:
    design = _selector_design()

    selected = resolve_nets(design, ["!USB*"])

    assert [net.name for net in selected] == ["GND"]


def test_glob_can_match_aliases_and_empty_globs_are_ok() -> None:
    design = _selector_design()

    assert [net.name for net in resolve_nets(design, ["USB_D[+-]"])] == ["USB_DP", "USB_DM"]
    assert resolve_nets(design, ["NO_MATCH*"]) == []


def test_unknown_exact_selector_errors() -> None:
    design = _selector_design()

    with pytest.raises(ValueError, match="Component 'NOPE' not found"):
        resolve_components(design, ["NOPE"])


def test_string_selectors_resolve_without_exact_miss_errors() -> None:
    assert resolve_string_selectors(["R*", "C*", "!C100"], ["R1", "C1", "C100", "U1"]) == (
        "R1",
        "C1",
    )
    assert resolve_string_selectors(["NO_MATCH"], ["R1"]) == ()
