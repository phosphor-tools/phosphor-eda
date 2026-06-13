"""Tests for format-neutral bus expansion helpers."""

from phosphor_eda.domain.buses import expand_bus_members, expand_group_bus, expand_vector_bus


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


def test_expand_bus_members_returns_none_for_scalar_name() -> None:
    assert expand_bus_members("RESET") is None
