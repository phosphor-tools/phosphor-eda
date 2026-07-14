"""Tests for format-neutral bus expansion helpers."""

import pytest

from phosphor_eda.domain.buses import (
    MAX_VECTOR_BUS_MEMBERS,
    BusDefinition,
    expand_bus_members,
    expand_group_bus,
    expand_vector_bus,
)
from phosphor_eda.domain.schematic import BusKind


def test_vector_bus_rejects_oversized_range() -> None:
    with pytest.raises(ValueError, match="maximum"):
        expand_vector_bus("A[0..999999999]")


def test_vector_bus_allows_at_limit_range() -> None:
    members = expand_vector_bus(f"A[0..{MAX_VECTOR_BUS_MEMBERS - 1}]")
    assert members is not None
    assert len(members) == MAX_VECTOR_BUS_MEMBERS


def test_bus_definition_defensively_copies_metadata() -> None:
    source_metadata = {"source_format": "kicad"}
    definition = BusDefinition(
        id="bus:1", name="A[0..1]", kind=BusKind.VECTOR, metadata=source_metadata
    )

    source_metadata["source_format"] = "mutated"

    assert definition.metadata == {"source_format": "kicad"}


def test_vector_bus_expands_ascending_and_descending_ranges() -> None:
    assert expand_vector_bus("DATA[0..3]") == ["DATA0", "DATA1", "DATA2", "DATA3"]
    assert expand_vector_bus("DATA[3..1]") == ["DATA3", "DATA2", "DATA1"]


def test_vector_bus_accepts_comma_separated_mixed_members() -> None:
    assert expand_vector_bus("A[0..1],CLK,B[1..0]") == ["A0", "A1", "CLK", "B1", "B0"]
    assert expand_vector_bus("CLK,RESET") is None


def test_group_bus_prefixes_members_and_expands_aliases() -> None:
    aliases = {"ADDR": ("A[0..1]", "A2")}

    assert expand_group_bus("SOC{ADDR CLK}", aliases=aliases) == [
        "SOC.A0",
        "SOC.A1",
        "SOC.A2",
        "SOC.CLK",
    ]


def test_group_bus_alias_cycles_are_skipped() -> None:
    aliases = {"ADDR": ("CTRL",), "CTRL": ("ADDR",)}

    assert expand_group_bus("SOC{ADDR CLK}", aliases=aliases) == ["SOC.CLK"]


def test_expand_bus_members_returns_none_for_scalar_name() -> None:
    assert expand_bus_members("RESET") is None
