"""Locks for the resolver helpers shared by the altium/kicad/dsn backends."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from phosphor_eda.domain.schematic import ScopeId
from phosphor_eda.formats.common.net_union import NetUnion
from phosphor_eda.formats.common.resolved_graph import (
    ResolutionInputError,
    component_source_ids_by_component_id,
    dedupe,
    merge_ids,
    merge_repeated_logical_pins,
    scope_key,
    validate_pin_ref,
)


@dataclass(frozen=True)
class _Pin:
    component_source_id: str
    local_net_id: str | None
    pin_designator: str = "1"


@dataclass(frozen=True)
class _LocalNet:
    scope_id: ScopeId


def test_merge_ids_unions_onto_first() -> None:
    union = NetUnion(["a", "b", "c", "d"])
    merge_ids(union, ["a", "b", "c"])
    merge_ids(union, ["d"])
    assert union.find("b") == union.find("a")
    assert union.find("c") == union.find("a")
    assert union.find("d") != union.find("a")


def test_dedupe_keeps_first_occurrence_and_drops_empty() -> None:
    assert dedupe(["GND", "", "VCC", "GND", "VCC"]) == ["GND", "VCC"]


def test_scope_key_root_and_path() -> None:
    assert scope_key(ScopeId(path=())) == "root"
    assert scope_key(ScopeId(path=("a", "b"))) == "a/b"


def test_validate_pin_ref_accepts_consistent_input() -> None:
    scope = ScopeId(path=("root",))
    validate_pin_ref(
        id_="pin:1",
        scope_id=scope,
        local_net_id="net:1",
        scopes={scope},
        local_nets_by_id={"net:1": _LocalNet(scope_id=scope)},
    )


def test_validate_pin_ref_rejects_unknown_scope_net_and_mismatch() -> None:
    scope = ScopeId(path=("root",))
    other = ScopeId(path=("child",))
    nets = {"net:1": _LocalNet(scope_id=scope)}

    with pytest.raises(ResolutionInputError, match="references unknown scope"):
        validate_pin_ref(
            id_="pin:1", scope_id=other, local_net_id="net:1", scopes={scope}, local_nets_by_id=nets
        )
    with pytest.raises(ResolutionInputError, match="references unknown local net"):
        validate_pin_ref(
            id_="pin:1", scope_id=scope, local_net_id="net:9", scopes={scope}, local_nets_by_id=nets
        )
    with pytest.raises(ResolutionInputError, match="does not match"):
        validate_pin_ref(
            id_="pin:1",
            scope_id=other,
            local_net_id="net:1",
            scopes={scope, other},
            local_nets_by_id=nets,
        )


def test_merge_repeated_logical_pins_unions_same_logical_pin() -> None:
    union = NetUnion(["n1", "n2", "n3"])
    pins = [
        _Pin("u1", "n1"),
        _Pin("u1", "n2"),
        _Pin("u2", "n3"),
        _Pin("u1", None),
    ]
    merge_repeated_logical_pins(
        union,
        pins,
        lambda pin: (pin.component_source_id, pin.pin_designator),
    )
    assert union.find("n2") == union.find("n1")
    assert union.find("n3") != union.find("n1")


def test_component_source_ids_grouped_uniquely_per_identity() -> None:
    pins = [
        _Pin("src-a", "n1"),
        _Pin("src-a", "n2"),
        _Pin("src-b", "n3"),
        _Pin("", "n4"),
    ]
    grouped = component_source_ids_by_component_id(pins, lambda pin: "component:u1")
    assert grouped == {"component:u1": ["src-a", "src-b"]}
