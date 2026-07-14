"""OrCAD DSN component enrichment: parameters, links, placement, title blocks."""

import os
from pathlib import Path

import pytest
from fixture_paths import FIXTURES, UPSTREAM_FIXTURES

from phosphor_eda.domain.schematic import (
    Component,
    FootprintModel,
    LibraryLink,
    Parameter,
    Schematic,
    ScopeId,
)
from phosphor_eda.formats.dsn.parser import DsnSchematicPage, RawTitleBlock, parse_dsn
from phosphor_eda.formats.dsn.raw_models import ParsedDesign
from phosphor_eda.formats.dsn.resolver import resolve_dsn_source
from phosphor_eda.formats.dsn.source import DsnPageSource, DsnPinOccurrence, DsnSourceDesign
from phosphor_eda.formats.dsn.to_schematic import dsn_to_design

PICO_DSN = FIXTURES / "dsn/raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN"
PICOW_DSN = FIXTURES / "dsn/raspberry-pi-pico-w/RPI-PICOW-R2.DSN"
CMIO_DSN = FIXTURES / "dsn/raspberry-pi-cmio/RPI-CMIO-V3_0-PUBLIC.DSN"
CP_SMARTGARDEN_DSN = (
    UPSTREAM_FIXTURES
    / "cp-smartgarden/Document/Hardware/mcu/swrc319/Cadence"
    / "LAUNCHXL-CC1310.DSN"
)
NXP_MR_NAVQ95_T1S_DSN = os.environ.get("PHOSPHOR_ORCAD_NXP_MR_NAVQ95_T1S_DSN")


@pytest.fixture(scope="module")
def pico() -> Schematic:
    return dsn_to_design(parse_dsn(PICO_DSN), name="Pico")


@pytest.fixture(scope="module")
def picow() -> Schematic:
    return dsn_to_design(parse_dsn(PICOW_DSN), name="PicoW")


@pytest.fixture(scope="module")
def cmio() -> Schematic:
    return dsn_to_design(parse_dsn(CMIO_DSN), name="CMIO")


@pytest.fixture(scope="module")
def cp_smartgarden() -> Schematic:
    return dsn_to_design(parse_dsn(CP_SMARTGARDEN_DSN), name="CP SmartGarden")


def _component(design: Schematic, reference: str) -> Component:
    component = next((c for c in design.components if c.reference == reference), None)
    assert component is not None, f"component {reference} not found"
    return component


class TestParameters:
    def test_instance_props_become_parameters(self, pico: Schematic) -> None:
        c18 = _component(pico, "C18")
        by_name = {p.name: p for p in c18.parameters}
        assert by_name["PCB Footprint"] == Parameter(name="PCB Footprint", value="capc1005")
        assert by_name["manufacturer"].value == "Murata"
        assert by_name["manufacturer_pn"].value == "GRM155R60J225ME01D"
        assert by_name["part_number"].value == "RP-019A1P-0B1170"

    def test_parameters_keep_parse_order(self, pico: Schematic) -> None:
        c18 = _component(pico, "C18")
        names = [p.name for p in c18.parameters]
        # Insertion order as parsed from the prefix chain pairs.
        assert names[:3] == ["part_number", "manufacturer", "manufacturer_pn"]

    def test_duplicate_props_become_duplicate_parameters(self) -> None:
        scope = ScopeId(path=("root",))
        page = DsnPageSource(
            id="page:root",
            name="Root",
            scope_id=scope,
            nets=[],
            wires=[],
            pin_occurrences=[
                DsnPinOccurrence(
                    id="pin:1",
                    scope_id=scope,
                    local_net_id=None,
                    source_net_id=0,
                    component_source_id="u1",
                    component_reference="U1",
                    component_part="PART",
                    pin_designator="1",
                    pin_name="A",
                    location=(0, 0),
                    component_props={"Vendor": "second"},
                    component_props_list=(("Vendor", "first"), ("Vendor", "second")),
                )
            ],
            ports=[],
            globals=[],
            off_page_connectors=[],
        )

        design = resolve_dsn_source(
            DsnSourceDesign(name="duplicates", pages=[page], hierarchy_mappings=[])
        )

        u1 = _component(design, "U1")
        assert u1.parameters == [
            Parameter(name="Vendor", value="first"),
            Parameter(name="Vendor", value="second"),
        ]

    def test_props_reach_metadata(self, pico: Schematic) -> None:
        c18 = _component(pico, "C18")
        assert c18.metadata["PCB Footprint"] == "capc1005"
        assert c18.metadata["manufacturer_pn"] == "GRM155R60J225ME01D"
        assert c18.metadata["dsn_component_source_ids"]

    def test_empty_prop_values_skipped_in_metadata(self, picow: Schematic) -> None:
        # PicoW R5 carries empty part_number/manufacturer_pn values; the
        # faithful parameter list keeps them, the convenience dict drops them.
        r5 = _component(picow, "R5")
        by_name = {p.name: p for p in r5.parameters}
        assert by_name["part_number"].value == ""
        assert "part_number" not in r5.metadata


class TestResolvedFields:
    def test_footprint_from_pcb_footprint_prop(self, pico: Schematic) -> None:
        c18 = _component(pico, "C18")
        assert c18.footprints == [FootprintModel(name="capc1005", is_current=True)]
        assert c18.footprint is not None
        assert c18.footprint.name == "capc1005"

    def test_library_link_with_cis_part_number(self, pico: Schematic) -> None:
        c18 = _component(pico, "C18")
        assert c18.lib == LibraryLink(symbol="CAP_MLCC", design_item_id="RP-019A1P-0B1170")

    def test_library_link_without_part_number(self, cmio: Schematic) -> None:
        c3 = _component(cmio, "C3")
        assert c3.lib == LibraryLink(symbol="CAP_3216_6.3V_47u")

    def test_datasheet_resolved_from_props(self, pico: Schematic) -> None:
        c18 = _component(pico, "C18")
        assert c18.datasheet.endswith("Murata_GRM155R60J225ME01.pdf")

    def test_no_false_dnp(self, pico: Schematic) -> None:
        # No fixture carries No_Mount/_DNP props; the convention ladder must
        # not fire on ordinary CIS values (matcher itself is unit-tested).
        c18 = _component(pico, "C18")
        assert c18.dnp is False
        assert c18.dnp_source is None


class TestPlacement:
    def test_occurrence_coordinates_populated(self, pico: Schematic) -> None:
        c18 = _component(pico, "C18")
        assert len(c18.occurrences) == 1
        occurrence = c18.occurrences[0]
        # Raw DSN units, plumbing-only per the coverage matrix.
        assert occurrence.x == 70.0
        assert occurrence.y == 670.0

    def test_all_placed_instances_have_coordinates(self, picow: Schematic) -> None:
        for component in picow.components:
            for occurrence in component.occurrences:
                assert occurrence.x is not None, component.reference
                assert occurrence.y is not None, component.reference


class TestTitleBlock:
    def test_orcad_title_block_aliases_map_to_typed_fields(self) -> None:
        design = dsn_to_design(
            ParsedDesign(
                pages=[
                    DsnSchematicPage(
                        name="Root",
                        title_blocks=[
                            RawTitleBlock(
                                name="TitleBlockSymbol",
                                props={
                                    "Title": "Controller",
                                    "RevCode": "A",
                                    "OrgName": "Acme Hardware",
                                    "OrgAddr2": "Floor 2",
                                    "OrgAddr1": "12 Main St",
                                    "OrgAddr4": "~",
                                    "Doc": "DOC-42",
                                    "Page Number": "3",
                                    "Page Count": "9",
                                    "Author": "*",
                                    "DrawnBy": "Drafter",
                                    "Designer": "Designer",
                                    "Approver": "Approver",
                                    "Check name": "Checker",
                                    "PageTitle": "Power",
                                    "Owner": "Hardware Team",
                                    "Classification": "Company Internal",
                                    "Cage Code": "CAGE",
                                },
                            )
                        ],
                    )
                ]
            )
        )
        [page] = design.pages
        block = page.title_block

        assert block is not None
        assert block.title == "Controller"
        assert block.revision == "A"
        assert block.organization == "Acme Hardware"
        assert block.org_address == "12 Main St\nFloor 2"
        assert block.document_number == "DOC-42"
        assert block.sheet_number == "3"
        assert block.sheet_total == "9"
        assert block.author == ""
        assert block.drawn_by == "Drafter"
        assert block.checked_by == "Checker"
        assert block.approved_by == "Approver"
        assert block.metadata["Designer"] == "Designer"
        assert block.metadata["PageTitle"] == "Power"
        assert block.metadata["Owner"] == "Hardware Team"
        assert block.metadata["Classification"] == "Company Internal"
        assert block.cage_code == "CAGE"
        assert block.metadata["OrgAddr4"] == "~"
        assert block.metadata["dsn_title_block_symbol"] == "TitleBlockSymbol"

    def test_cp_smartgarden_title_block_designer_and_check_aliases(
        self, cp_smartgarden: Schematic
    ) -> None:
        page = next(p for p in cp_smartgarden.pages if p.name == "1_CC1310RF")
        block = page.title_block

        assert block is not None
        assert block.drawn_by == "KHT"
        assert block.checked_by == "TER, KHT"
        assert block.metadata["Designer name"] == "KHT"
        assert block.metadata["Check name"] == "TER, KHT"

    def test_cp_smartgarden_library_header_metadata(self, cp_smartgarden: Schematic) -> None:
        assert cp_smartgarden.metadata["dsn_library_intro"] == "OrCAD Windows Design"
        assert cp_smartgarden.metadata["dsn_library_version"] == "3.2"
        assert cp_smartgarden.metadata["dsn_library_created_timestamp"] == "1314865985"
        assert cp_smartgarden.metadata["dsn_library_modified_timestamp"] == "1453363925"

    @pytest.mark.corpus
    @pytest.mark.skipif(
        NXP_MR_NAVQ95_T1S_DSN is None or not Path(NXP_MR_NAVQ95_T1S_DSN).exists(),
        reason=(
            "NXP MR-NAVQ95 local OrCAD corpus fixture not present; set "
            "PHOSPHOR_ORCAD_NXP_MR_NAVQ95_T1S_DSN"
        ),
    )
    def test_nxp_title_block_aliases_are_typed_or_queryable_metadata(self) -> None:
        assert NXP_MR_NAVQ95_T1S_DSN is not None
        design = dsn_to_design(parse_dsn(Path(NXP_MR_NAVQ95_T1S_DSN)), name="NXP MR-NAVQ95 T1S")
        page = next(p for p in design.pages if p.name == "P01-Cover")
        block = page.title_block

        assert block is not None
        assert block.title == "X-MR-NAVQ95E-T1S"
        assert block.drawn_by == "Youri Tils"
        assert block.approved_by == "Jari van Ewijk"
        assert block.metadata["Designer"] == "Youri Tils"
        assert block.metadata["PageTitle"] == "Cover"
        assert block.metadata["Classification"] == "Company Internal/Proprietary"

    def test_picow_title_block(self, picow: Schematic) -> None:
        page = next(p for p in picow.pages if p.name == "RP2040")
        block = page.title_block
        assert block is not None
        assert block.title == "Raspberry Pi PicoW"
        assert block.revision == "3"
        assert block.organization == ""
        assert block.metadata["Doc"] == "RPI-PicoW"
        assert block.metadata["Author"] == "Dominic Plunkett"
        assert block.metadata["Copyright_Year"] == "2022"

    def test_every_picow_page_has_title_block(self, picow: Schematic) -> None:
        assert all(page.title_block is not None for page in picow.pages)

    def test_cmio_title_block(self, cmio: Schematic) -> None:
        page = next(p for p in cmio.pages if p.name == "PAGE1 - CONTENTS")
        block = page.title_block
        assert block is not None
        assert block.title == "Raspberry Pi Compute Module IO Board"
        assert block.revision == "3.0"
        assert block.organization == "Raspberry Pi"
        assert block.metadata["Doc"] == "RPI-CMIO"
        assert block.metadata["Page Number"] == "1"
