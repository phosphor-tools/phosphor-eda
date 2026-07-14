import os
from pathlib import Path

import pytest
from fixture_paths import FIXTURES, UPSTREAM_FIXTURES

from phosphor_eda.formats.dsn.library import parse_library_inventory

CORPUS_ROOT = Path(os.environ.get("PHOSPHOR_EDA_CORPUS_ROOT", "__external_corpus_missing__"))
OPENORCADPARSER_OLB = FIXTURES / "orcad/openorcadparser-olb/0000.OLB"
CP_SMARTGARDEN_DSN = (
    UPSTREAM_FIXTURES
    / "cp-smartgarden/Document/Hardware/mcu/swrc319/Cadence"
    / "LAUNCHXL-CC1310.DSN"
)
LAPIS_DISCRETE_OLB = (
    CORPUS_ROOT
    / "designs/orcad/LapisDevBoard"
    / "DESIGN FILES/OrCAD Capture Schematics/library/discrete.olb"
)


def test_olb_inventory_parses_library_without_schematic_pages() -> None:
    inventory = parse_library_inventory(OPENORCADPARSER_OLB)

    assert inventory.path == str(OPENORCADPARSER_OLB)
    # H6/T6: lock the whole ordered field list, not just the last element.
    assert inventory.part_fields == [
        "1ST PART FIELD",
        "2ND PART FIELD",
        "3RD PART FIELD",
        "4TH PART FIELD",
        "5TH PART FIELD",
        "6TH PART FIELD",
        "7TH PART FIELD",
        "PCB Footprint",
    ]
    assert inventory.cache_part_names == []
    assert len(inventory.packages) == 1
    assert [package.name for package in inventory.package_inventory] == ["0000"]
    assert inventory.package_inventory[0].stream_path == "Packages/0000"
    assert inventory.package_inventory[0].source_package_names == ["0000", "0000.Normal"]
    assert inventory.package_inventory[0].source_library_references == []
    assert inventory.package_inventory[0].pin_count == 0


def test_design_cache_inventory_exposes_cache_names_and_package_pin_counts() -> None:
    inventory = parse_library_inventory(CP_SMARTGARDEN_DSN)

    assert "RESISTOR" in inventory.cache_part_names
    assert inventory.cache_pin_counts["RESISTOR"] == 2
    assert len(inventory.packages) == 14

    package_by_name = {package.name: package for package in inventory.package_inventory}
    assert package_by_name["IC_SWITCH_TS5A3159A_DSBGA6_0"].pin_count == 6
    assert package_by_name["IC_SWITCH_TS5A3159A_DSBGA6_0"].source_package_names == [
        "IC_SWITCH_TS5A3159A_DSBGA6_0",
        "IC_SWITCH_TS5A3159A_DSBGA6_0.Normal",
    ]


@pytest.mark.corpus
@pytest.mark.skipif(not LAPIS_DISCRETE_OLB.exists(), reason="Lapis OLB corpus fixture absent")
def test_lapis_discrete_olb_inventory_stress_fixture() -> None:
    inventory = parse_library_inventory(LAPIS_DISCRETE_OLB)

    assert len(inventory.packages) == 1200
    assert sum(package.pin_count for package in inventory.package_inventory) == 8064
    assert inventory.package_inventory[0].name == "10CTQ150/TO"
