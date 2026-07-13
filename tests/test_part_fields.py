"""Tests for the shared recognized-name resolution of part fields."""

from phosphor_eda.domain.part_fields import (
    is_dnp_value,
    resolve_part_fields,
)
from phosphor_eda.domain.schematic import Parameter, PartNumber


def _params(*pairs: tuple[str, str]) -> list[Parameter]:
    return [Parameter(name=name, value=value) for name, value in pairs]


class TestDnpValue:
    def test_recognized_tokens(self) -> None:
        for token in ("DNP", "DNI", "DNF", "NF", "NOPOP", "NO LOAD"):
            assert is_dnp_value(token)

    def test_case_insensitive_and_stripped(self) -> None:
        assert is_dnp_value("dnp")
        assert is_dnp_value("  Do Not Populate ")

    def test_substring_does_not_match(self) -> None:
        assert not is_dnp_value("DNP_0402")
        assert not is_dnp_value("100nF")
        assert not is_dnp_value("")


class TestDnpConvention:
    def test_value_parameter_match(self) -> None:
        fields = resolve_part_fields(_params(("Value", "DNP")))
        assert fields.dnp_convention

    def test_comment_match_via_part(self) -> None:
        fields = resolve_part_fields([], part="DO NOT PLACE")
        assert fields.dnp_convention

    def test_orcad_no_mount_parameter_name(self) -> None:
        fields = resolve_part_fields(_params(("No_Mount", "NO_MOUNT")))
        assert fields.dnp_convention

    def test_orcad_dnp_parameter_name(self) -> None:
        fields = resolve_part_fields(_params(("_DNP", "1")))
        assert fields.dnp_convention

    def test_no_mount_with_falsy_value_does_not_match(self) -> None:
        fields = resolve_part_fields(_params(("No_Mount", "")))
        assert not fields.dnp_convention

    def test_ordinary_component_is_not_dnp(self) -> None:
        fields = resolve_part_fields(_params(("Value", "100nF")), part="100nF")
        assert not fields.dnp_convention


class TestPartNumbers:
    def test_kicad_mpn_with_manufacturer(self) -> None:
        fields = resolve_part_fields(_params(("Manufacturer", "TI"), ("MPN", "SN74LVC2G66DCUR")))
        assert fields.part_numbers == [PartNumber(manufacturer="TI", number="SN74LVC2G66DCUR")]

    def test_altium_primary_manufacturer_part_number(self) -> None:
        fields = resolve_part_fields(
            _params(("Manufacturer", "Murata"), ("Manufacturer Part Number", "GRM155R71C104KA88D"))
        )
        assert fields.part_numbers == [
            PartNumber(manufacturer="Murata", number="GRM155R71C104KA88D")
        ]

    def test_altium_numbered_manufacturers(self) -> None:
        fields = resolve_part_fields(
            _params(
                ("Manufacturer", "Murata"),
                ("Manufacturer Part Number", "GRM155R71C104KA88D"),
                ("Manufacturer 2", "Samsung"),
                ("Manufacturer 2 Part Number", "CL05B104KO5NNNC"),
            )
        )
        assert fields.part_numbers == [
            PartNumber(manufacturer="Murata", number="GRM155R71C104KA88D"),
            PartNumber(manufacturer="Samsung", number="CL05B104KO5NNNC"),
        ]

    def test_altium_supplier_part_number(self) -> None:
        fields = resolve_part_fields(
            _params(("Supplier 1", "Digi-Key"), ("Supplier Part Number 1", "296-13272-1-ND"))
        )
        assert fields.part_numbers == [PartNumber(manufacturer="Digi-Key", number="296-13272-1-ND")]

    def test_orcad_cis_part_number_without_manufacturer(self) -> None:
        fields = resolve_part_fields(_params(("Part Number", "C-100N-0402-X7R")))
        assert fields.part_numbers == [PartNumber(manufacturer="", number="C-100N-0402-X7R")]

    def test_empty_number_skipped(self) -> None:
        fields = resolve_part_fields(_params(("MPN", ""), ("Manufacturer", "TI")))
        assert fields.part_numbers == []

    def test_duplicate_part_numbers_deduped(self) -> None:
        fields = resolve_part_fields(_params(("MPN", "ABC-123"), ("MPN", "ABC-123")))
        assert fields.part_numbers == [PartNumber(manufacturer="", number="ABC-123")]


class TestDatasheet:
    def test_datasheet_field(self) -> None:
        fields = resolve_part_fields(_params(("Datasheet", "https://example.com/ds.pdf")))
        assert fields.datasheet == "https://example.com/ds.pdf"

    def test_kicad_tilde_placeholder_ignored(self) -> None:
        fields = resolve_part_fields(_params(("Datasheet", "~")))
        assert fields.datasheet == ""

    def test_first_datasheet_wins(self) -> None:
        fields = resolve_part_fields(
            _params(("Datasheet", "https://a.pdf"), ("Datasheet", "https://b.pdf"))
        )
        assert fields.datasheet == "https://a.pdf"
