"""KiCad component enrichment: parameters, links, dnp, title blocks."""

from pathlib import Path

import pytest

from phosphor_eda.domain.schematic import (
    DnpSource,
    FootprintModel,
    LibraryLink,
    Parameter,
    PartNumber,
    Schematic,
)
from phosphor_eda.formats.kicad import kicad_to_design
from phosphor_eda.query.format import format_component_detail

FIXTURES = Path(__file__).parent / "fixtures"
ETHERNET_SCH = FIXTURES / "kicad-jetson-orin" / "ethernet.kicad_sch"
CSI_SCH = FIXTURES / "kicad-jetson-orin" / "csi.kicad_sch"
ORANGECRAB_SCH = FIXTURES / "kicad-orangecrab" / "OrangeCrab.kicad_sch"


@pytest.fixture(scope="module")
def ethernet() -> Schematic:
    return kicad_to_design(ETHERNET_SCH)


@pytest.fixture(scope="module")
def csi() -> Schematic:
    return kicad_to_design(CSI_SCH)


@pytest.fixture(scope="module")
def orangecrab() -> Schematic:
    return kicad_to_design(ORANGECRAB_SCH)


def _component(design: Schematic, reference: str):
    component = next((c for c in design.components if c.reference == reference), None)
    assert component is not None, f"component {reference} not found"
    return component


class TestParameters:
    def test_all_properties_extracted_in_order(self, ethernet: Schematic) -> None:
        c52 = _component(ethernet, "C52")
        names = [p.name for p in c52.parameters]
        assert names[:4] == ["Reference", "Value", "Footprint", "Datasheet"]
        assert "MPN" in names
        assert "Manufacturer" in names

    def test_hidden_property_visibility(self, ethernet: Schematic) -> None:
        c52 = _component(ethernet, "C52")
        by_name = {p.name: p for p in c52.parameters}
        assert by_name["MPN"] == Parameter(name="MPN", value="GRM155R61H104KE14D", visible=False)
        assert by_name["Reference"].visible

    def test_properties_reach_metadata(self, ethernet: Schematic) -> None:
        c52 = _component(ethernet, "C52")
        assert c52.metadata["MPN"] == "GRM155R61H104KE14D"
        assert c52.metadata["Manufacturer"] == "Murata"
        assert "Reference" not in c52.metadata


class TestResolvedFields:
    def test_part_number_resolved(self, ethernet: Schematic) -> None:
        c52 = _component(ethernet, "C52")
        assert c52.part_numbers == [PartNumber(manufacturer="Murata", number="GRM155R61H104KE14D")]

    def test_datasheet_resolved(self, ethernet: Schematic) -> None:
        c52 = _component(ethernet, "C52")
        assert c52.datasheet.startswith("https://www.murata.com/")

    def test_library_link(self, ethernet: Schematic) -> None:
        c52 = _component(ethernet, "C52")
        assert c52.lib == LibraryLink(symbol="C_100n_0402", library="antmicroCapacitors0402")

    def test_footprint_model(self, ethernet: Schematic) -> None:
        c52 = _component(ethernet, "C52")
        assert c52.footprints == [
            FootprintModel(name="C_0402_1005Metric", library="antmicro-footprints", is_current=True)
        ]
        assert c52.footprint is not None
        assert c52.footprint.name == "C_0402_1005Metric"


class TestDnp:
    def test_explicit_dnp_yes(self, csi: Schematic) -> None:
        r171 = _component(csi, "R171")
        assert r171.dnp is True
        assert r171.dnp_source is DnpSource.EXPLICIT

    def test_explicit_dnp_no(self, csi: Schematic) -> None:
        fitted = next(c for c in csi.components if not c.dnp)
        assert fitted.dnp_source is None


class TestComponentDetailView:
    def test_detail_shows_enrichment(self, ethernet: Schematic) -> None:
        detail = format_component_detail(ethernet, "C52")
        assert "footprint: antmicro-footprints:C_0402_1005Metric" in detail
        assert "part_number: Murata GRM155R61H104KE14D" in detail
        assert "datasheet: https://www.murata.com/" in detail
        assert "parameters:" in detail
        assert "MPN: GRM155R61H104KE14D (hidden)" in detail

    def test_detail_shows_dnp(self, csi: Schematic) -> None:
        detail = format_component_detail(csi, "R171")
        assert "dnp: yes (explicit)" in detail


class TestTitleBlock:
    def test_root_page_title_block(self, orangecrab: Schematic) -> None:
        root = orangecrab.pages[0]
        assert root.title_block is not None
        assert root.title_block.title == "Orange Crab"
        assert root.title_block.revision == "r0.2.1"
        assert root.title_block.date == "2020-11-01"
        assert root.title_block.company == "Good Stuff Department"
        assert root.title_block.comments["4"] == "Designed by: Greg Davill"
