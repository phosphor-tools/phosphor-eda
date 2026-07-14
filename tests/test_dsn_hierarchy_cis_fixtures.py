from __future__ import annotations

import json
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import olefile
import pytest

from phosphor_eda.domain.variants import VariantField, VariantTargetKind
from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.dsn.binary_reader import STRUCT_PORT
from phosphor_eda.formats.dsn.hierarchy import (
    MAX_ENTRY_DEPTH,
    build_occurrence_to_instance,
    parse_hierarchy_stream,
)
from phosphor_eda.formats.dsn.package_netlist import (
    apply_packaged_no_connects,
    apply_packaged_pin_names,
    parse_pstxnet_no_connects,
)
from phosphor_eda.formats.dsn.packages import parse_package_stream
from phosphor_eda.formats.dsn.parser import DsnSchematicPage, parse_dsn
from phosphor_eda.formats.dsn.pins import resolve_symbol_pin
from phosphor_eda.formats.dsn.project import load_orcad_project, parse_opj_file
from phosphor_eda.formats.dsn.raw_models import (
    DsnBlockInstance,
    DsnHierarchy,
    DsnHierarchyEntry,
    DsnHierarchyNet,
    DsnHierarchyOccurrence,
    DsnSymbolPin,
    GraphicInst,
    PageNetEntry,
    ParsedDesign,
    PinConnection,
    PlacedInstance,
)
from phosphor_eda.formats.dsn.raw_models import SchematicPage as RawSchematicPage
from phosphor_eda.formats.dsn.sheet_tree import build_sheet_tree
from phosphor_eda.formats.dsn.to_schematic import dsn_to_design, dsn_to_source

if TYPE_CHECKING:
    from phosphor_eda.domain.schematic import Component, Net

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UPSTREAM_FIXTURES = FIXTURES.parent / "upstream"
OPENCELLULAR_BREAKOUT_ROOT = UPSTREAM_FIXTURES / "opencellular/electronics/breakout"
OPENCELLULAR_BREAKOUT_DSN = OPENCELLULAR_BREAKOUT_ROOT / "schematic/dsn/OC_CONNECT_1_BRKOUT_BRD.DSN"
OPENCELLULAR_SYNC_ROOT = UPSTREAM_FIXTURES / "opencellular/electronics/sync"
OPENCELLULAR_SYNC_DSN = OPENCELLULAR_SYNC_ROOT / "schematics/dsn/FB_CONNECT1_SYNC_LIFE-3_V1P1.DSN"
OPENCELLULAR_SYNC_OPJ = OPENCELLULAR_SYNC_ROOT / "schematics/dsn/FB_CONNECT1_SYNC_LIFE-3_V1P1.opj"
OPENCELLULAR_POWER_UNIT_ROOT = UPSTREAM_FIXTURES / "opencellular/electronics/power-unit"
OPENCELLULAR_POWER_UNIT_DSN = (
    OPENCELLULAR_POWER_UNIT_ROOT / "Rev-A/schematics/" / "POWER_SOURCE_BOARD_20180717.DSN"
)
CP_SMARTGARDEN_ROOT = UPSTREAM_FIXTURES / "cp-smartgarden"
CP_SMARTGARDEN_DSN = (
    CP_SMARTGARDEN_ROOT / "Document/Hardware/mcu/swrc319/Cadence/" / "LAUNCHXL-CC1310.DSN"
)
CP_SMARTGARDEN_OPJ = CP_SMARTGARDEN_DSN.parent / "launchxl-cc1310.opj"
CP_SMARTGARDEN_ALLEGRO = CP_SMARTGARDEN_DSN.parent / "Allegro"
ROHM_STEPPER_ROOT = UPSTREAM_FIXTURES / "rohm-stepper-driver"
ROHM_STEPPER_DSN = ROHM_STEPPER_ROOT / "Design Files for Rev 1.0/STEPPER.DSN"
ROHM_STEPPER_OPJ = ROHM_STEPPER_ROOT / "Design Files for Rev 1.0/STEPPER.opj"
MAXOME_ROOT = UPSTREAM_FIXTURES / "maxome-mpcie"
MAXOME_0P1_DSN = MAXOME_ROOT / "0p1/MAXOME_MPCIE_0P1.DSN"
MAXOME_0P1_OPJ = MAXOME_ROOT / "0p1/maxome_mpcie_0p1.opj"
MAXOME_1P1_DSN = MAXOME_ROOT / "1p1/MAXOME_MPCIE_1P1.DSN"
MAXOME_1P1_OPJ = MAXOME_ROOT / "1p1/maxome_mpcie_1p1.opj"
RFSOC_ROOT = UPSTREAM_FIXTURES / "rfsoc-frontend"
RFSOC_DSN = RFSOC_ROOT / "RFMC_Frontend/RFMC_FRONTEND_V1_00.DSN"
RFSOC_OPJ = RFSOC_ROOT / "RFMC_Frontend/RFMC_Frontend_v1_00.opj"
RPI_CMIO_DSN = FIXTURES / "dsn/raspberry-pi-cmio/RPI-CMIO-V3_0-PUBLIC.DSN"
RPI_PICO_DSN = FIXTURES / "dsn/raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN"
RPI_PICOW_DSN = FIXTURES / "dsn/raspberry-pi-pico-w/RPI-PICOW-R2.DSN"
OPENCELLULAR_BREAKOUT_OPJ = OPENCELLULAR_BREAKOUT_ROOT / "schematic/dsn/OC_CONNECT_1_BRKOUT_BRD.opj"
OPENCELLULAR_BREAKOUT_NETLIST = OPENCELLULAR_BREAKOUT_ROOT / "schematic/Netlist"

type CisStatus = Literal["absent", "placeholder", "non_placeholder"]


@dataclass(frozen=True)
class _FixtureInventory:
    stream_count: int
    view_names: tuple[str, ...]
    parsed_page_count: int
    page_stream_count: int
    package_count: int
    hierarchy_stream_count: int
    variant_store_leaf_stream_count: int
    has_variant_store_storage: bool
    cis_status: CisStatus
    erc_symbol_streams: tuple[str, ...]


@dataclass(frozen=True)
class _FixtureInventoryExpectation:
    name: str
    root: Path
    dsn: Path
    inventory: _FixtureInventory
    required_project_files: tuple[Path, ...]


@dataclass(frozen=True)
class _CisGroupRow:
    group: str
    state: str
    member_id: int


@dataclass(frozen=True)
class _OleInventory:
    stream_paths: tuple[str, ...]
    storage_paths: frozenset[str]
    stream_sizes: dict[str, int]
    cis_group_stream_data: dict[str, bytes]


FIXTURE_INVENTORY_EXPECTATIONS = (
    _FixtureInventoryExpectation(
        name="opencellular-breakout",
        root=OPENCELLULAR_BREAKOUT_ROOT,
        dsn=OPENCELLULAR_BREAKOUT_DSN,
        inventory=_FixtureInventory(
            stream_count=20,
            view_names=("TEST_BRD",),
            parsed_page_count=3,
            page_stream_count=3,
            package_count=0,
            hierarchy_stream_count=1,
            variant_store_leaf_stream_count=0,
            has_variant_store_storage=False,
            cis_status="absent",
            erc_symbol_streams=(),
        ),
        required_project_files=(
            Path("schematic/dsn/OC_CONNECT_1_BRKOUT_BRD.opj"),
            Path("schematic/Netlist/pstxnet.dat"),
            Path("schematic/Netlist/pstxprt.dat"),
            Path("schematic/Netlist/pstchip.dat"),
            Path("board/OC_CONNECT-1_BREAKOUT_LIFE-3.brd"),
            Path("gerbers/OC_CONNECT-1_BREAKOUT_LIFE-3.ipc"),
        ),
    ),
    _FixtureInventoryExpectation(
        name="opencellular-sync",
        root=OPENCELLULAR_SYNC_ROOT,
        dsn=OPENCELLULAR_SYNC_DSN,
        inventory=_FixtureInventory(
            stream_count=46,
            view_names=("opencellular_coonect1_sync",),
            parsed_page_count=14,
            page_stream_count=14,
            package_count=10,
            hierarchy_stream_count=1,
            variant_store_leaf_stream_count=3,
            has_variant_store_storage=True,
            cis_status="placeholder",
            erc_symbol_streams=("Symbols/ERC",),
        ),
        required_project_files=(
            Path("schematics/dsn/FB_CONNECT1_SYNC_LIFE-3_V1P1.opj"),
            Path("schematics/Netlist/pstxnet.dat"),
            Path("schematics/Netlist/pstxprt.dat"),
            Path("schematics/Netlist/pstchip.dat"),
            Path("board/Fb_Connect1_SYNC_Life-3.brd"),
            Path("gerbers/Fb_Connect1_SYNC_Life-3.ipc"),
        ),
    ),
    _FixtureInventoryExpectation(
        name="cp-smartgarden-launchxl-cc1310",
        root=CP_SMARTGARDEN_ROOT,
        dsn=CP_SMARTGARDEN_DSN,
        inventory=_FixtureInventory(
            stream_count=48,
            view_names=("CC1310_LaunchPad",),
            parsed_page_count=4,
            page_stream_count=4,
            package_count=14,
            hierarchy_stream_count=1,
            variant_store_leaf_stream_count=11,
            has_variant_store_storage=True,
            cis_status="non_placeholder",
            erc_symbol_streams=("Symbols/ERC",),
        ),
        required_project_files=(
            Path("README.md"),
            Path("Document/Hardware/mcu/swrc319/Cadence/launchxl-cc1310.opj"),
            Path("Document/Hardware/mcu/swrc319/Cadence/Allegro/pstxnet.dat"),
            Path("Document/Hardware/mcu/swrc319/Cadence/Allegro/pstxprt.dat"),
            Path("Document/Hardware/mcu/swrc319/Cadence/Allegro/pstchip.dat"),
            Path("Document/Hardware/mcu/swrc319/Cadence/Allegro/LAUNCHXL-CC1310.brd"),
            Path("Document/Hardware/mcu/swrc319/Cadence/Allegro/log/netrev.lst"),
        ),
    ),
    _FixtureInventoryExpectation(
        name="opencellular-power-unit",
        root=OPENCELLULAR_POWER_UNIT_ROOT,
        dsn=OPENCELLULAR_POWER_UNIT_DSN,
        inventory=_FixtureInventory(
            stream_count=118,
            view_names=("SCHEMATIC1",),
            parsed_page_count=27,
            page_stream_count=27,
            package_count=72,
            hierarchy_stream_count=1,
            variant_store_leaf_stream_count=0,
            has_variant_store_storage=False,
            cis_status="absent",
            erc_symbol_streams=("Symbols/ERC",),
        ),
        required_project_files=(
            Path("Rev-A/schematics/POWER_SOURCE_BOARD_20180717.DSN"),
            Path("Rev-A/schematics/POWER.OLB"),
            Path("Rev-A/bom/BoM_PSU_Assembly_08102018_ver1.1.xlsx"),
            Path("Rev-A/assembly/Power_Source_Proto1_BoM.xlsx"),
        ),
    ),
    _FixtureInventoryExpectation(
        name="rohm-stepper-driver-ctrl",
        root=ROHM_STEPPER_ROOT,
        dsn=ROHM_STEPPER_DSN,
        inventory=_FixtureInventory(
            stream_count=110,
            view_names=(
                "Clock-In Input",
                "Onboard Manual Controls",
                "Parallel Input",
                "Revision History",
                "Top Block Diagram",
            ),
            parsed_page_count=5,
            page_stream_count=5,
            package_count=81,
            hierarchy_stream_count=2,
            variant_store_leaf_stream_count=0,
            has_variant_store_storage=False,
            cis_status="absent",
            erc_symbol_streams=("Symbols/ERC", "Symbols/ERC_PHYSICAL"),
        ),
        required_project_files=(
            Path("README.md"),
            Path("Design Files for Rev 1.0/STEPPER.opj"),
            Path("Design Files for Rev 1.0/STEPPER EVAL BRD - SCHEMATIC File - Rev 1.0.opj"),
            Path("Design Files for Rev 1.0/STEPPER EVAL BRD - PCB Board File - Rev 1.0.brd"),
            Path("Design Files for Rev 1.0/Gerbers & Panel CAD/DRILL.DRL"),
            Path(
                "Design Files for Rev 1.0/Data Sheets/"
                "Stepper Driver - ROHM - BD6387xEFV - Data Sheet.pdf"
            ),
        ),
    ),
    _FixtureInventoryExpectation(
        name="maxome-mpcie-0p1",
        root=MAXOME_ROOT,
        dsn=MAXOME_0P1_DSN,
        inventory=_FixtureInventory(
            stream_count=36,
            view_names=("SCHEMATIC1",),
            parsed_page_count=3,
            page_stream_count=3,
            package_count=2,
            hierarchy_stream_count=1,
            variant_store_leaf_stream_count=8,
            has_variant_store_storage=True,
            cis_status="non_placeholder",
            erc_symbol_streams=(),
        ),
        required_project_files=(
            Path("0p1/readme.md"),
            Path("0p1/maxome_mpcie_0p1.opj"),
            Path("0p1/MAXOME_MPCIE_0P1_0.DBK"),
            Path("0p1/devices.dml"),
            Path("0p1/901-00107-01_DSB05PA30_BOM_0p1.xlsx"),
            Path("0p1/901-00107-01_DSB05PA30_Schematic_0p1.pdf"),
            Path("0p1/MAXOME_MPCIE_0P1.png"),
        ),
    ),
    _FixtureInventoryExpectation(
        name="maxome-mpcie-1p1",
        root=MAXOME_ROOT,
        dsn=MAXOME_1P1_DSN,
        inventory=_FixtureInventory(
            stream_count=36,
            view_names=("SCHEMATIC1",),
            parsed_page_count=3,
            page_stream_count=3,
            package_count=2,
            hierarchy_stream_count=1,
            variant_store_leaf_stream_count=8,
            has_variant_store_storage=True,
            cis_status="non_placeholder",
            erc_symbol_streams=(),
        ),
        required_project_files=(
            Path("1p1/readme.md"),
            Path("1p1/maxome_mpcie_1p1.opj"),
            Path("1p1/MAXOME_MPCIE_1P1_0.DBK"),
            Path("1p1/devices.dml"),
            Path("1p1/901-00107-01_DSB05PA30_BOM_1p1.xlsx"),
            Path("1p1/901-00107-01_DSB05PA30_Schematic_1p1.pdf"),
            Path("1p1/MAXOME_MPCIE_1P1.png"),
        ),
    ),
    _FixtureInventoryExpectation(
        name="rfsoc-frontend",
        root=RFSOC_ROOT,
        dsn=RFSOC_DSN,
        inventory=_FixtureInventory(
            stream_count=60,
            view_names=("DAC_ADC_CHANNEL", "IO_CHANNEL", "TOP"),
            parsed_page_count=9,
            page_stream_count=9,
            package_count=29,
            hierarchy_stream_count=1,
            variant_store_leaf_stream_count=0,
            has_variant_store_storage=False,
            cis_status="absent",
            erc_symbol_streams=("Symbols/ERC", "Symbols/ERC_PHYSICAL"),
        ),
        required_project_files=(
            Path("README.md"),
            Path("Components.xlsx"),
            Path("Tips.txt"),
            Path("RFMC_Frontend/RFMC_Frontend_v1_00.opj"),
            Path("RFMC_Frontend/RFMC_FRONTEND_V1_00_0.DBK"),
            Path("RFMC_Frontend/devices.dml"),
            Path("RFMC_Frontend/RFMC_FRONTEND_V1_00.png"),
            Path("RFMC_Frontend/allegro/rfmc_frontend_v1_00.brd"),
            Path("RFMC_Frontend/allegro/pstxnet.dat"),
            Path("RFMC_Frontend/allegro/pstxprt.dat"),
            Path("RFMC_Frontend/allegro/pstchip.dat"),
        ),
    ),
)


def _stream_path(entry: list[str]) -> str:
    return "/".join(entry)


def _read_ole_inventory(dsn_path: Path) -> _OleInventory:
    with olefile.OleFileIO(str(dsn_path)) as ole:
        stream_paths = tuple(
            _stream_path(entry) for entry in ole.listdir(streams=True, storages=False)
        )
        cis_group_stream_data: dict[str, bytes] = {}
        for path in stream_paths:
            parts = path.split("/")
            if (
                len(parts) >= 5
                and parts[:3] == ["CIS", "VariantStore", "Groups"]
                and parts[3] != "GroupsDataStream"
            ):
                cis_group_stream_data[path] = ole.openstream(path).read()
        return _OleInventory(
            stream_paths=stream_paths,
            storage_paths=frozenset(
                _stream_path(entry) for entry in ole.listdir(streams=False, storages=True)
            ),
            stream_sizes={path: ole.get_size(path) for path in stream_paths},
            cis_group_stream_data=cis_group_stream_data,
        )


def _decode_cis_field(value: bytes) -> str:
    return value.strip(b"\x00").split(b"\x00", 1)[0].decode("latin1").strip()


def _strip_optional_size_prefix(data: bytes) -> bytes:
    if len(data) >= 4 and struct.unpack_from("<I", data, 0)[0] == len(data) - 4:
        return data[4:]
    return data


def _cis_group_rows_from_inventory(inventory: _OleInventory) -> list[_CisGroupRow]:
    rows: list[_CisGroupRow] = []
    for path, stream_data in inventory.cis_group_stream_data.items():
        group = path.split("/")[3]
        data = _strip_optional_size_prefix(stream_data)
        for row_bytes in data.split(b"~"):
            fields = [_decode_cis_field(field) for field in row_bytes.split(b"\xb0")]
            fields = [field for field in fields if field]
            if len(fields) >= 2 and fields[0].isdigit() and fields[1].isdigit():
                rows.append(
                    _CisGroupRow(
                        group=group,
                        state=fields[0],
                        member_id=int(fields[1]),
                    )
                )
    return rows


def _cis_group_rows(dsn_path: Path) -> list[_CisGroupRow]:
    return _cis_group_rows_from_inventory(_read_ole_inventory(dsn_path))


def _cis_bom_part_data_ids(dsn_path: Path, bom_name: str) -> list[int]:
    """Independently decode the ``BOMPartData`` id list from the raw OLE stream.

    Mirrors the in-test group decoder: strip the optional size prefix, split the
    ``\\xf9``-delimited string list, drop the leading declared-count field, and
    keep the numeric ids. Used to cross-check the parser's echoed BOM entry ids
    against a decode that shares no code with the parser (finding T5).
    """
    stream_path = f"CIS/VariantStore/BOM/{bom_name}/BOMPartData"
    with olefile.OleFileIO(str(dsn_path)) as ole:
        data = ole.openstream(stream_path).read()
    payload = _strip_optional_size_prefix(data)
    fields = [_decode_cis_field(field) for field in payload.split(b"\xf9")]
    fields = [field for field in fields if field]
    return [int(field) for field in fields[1:] if field.isdigit()]


def _cis_status(inventory: _OleInventory, variant_store_streams: tuple[str, ...]) -> CisStatus:
    variant_store_paths = sorted(
        path for path in variant_store_streams if path.startswith("CIS/VariantStore/")
    )
    if not variant_store_paths:
        return "absent"

    sizes = {path: inventory.stream_sizes[path] for path in variant_store_paths}
    # These byte-exact empty/header-only streams identify OpenCellular Sync's
    # placeholder VariantStore guard; non-empty stores belong to later CIS work.
    placeholder_streams = {
        "CIS/VariantStore/BOM/BOMDataStream": 5,
        "CIS/VariantStore/Groups/GroupsDataStream": 4,
        "CIS/VariantStore/VariantNames": 11,
    }
    if sizes == placeholder_streams and not _cis_group_rows_from_inventory(inventory):
        return "placeholder"
    return "non_placeholder"


def _instances_by_db_id(dsn_path: Path) -> dict[int, tuple[str, str]]:
    raw = parse_dsn(dsn_path)
    return {
        instance.db_id: (page.name, instance.reference)
        for page in raw.pages
        for instance in page.instances
    }


@pytest.mark.parametrize(
    "expected",
    FIXTURE_INVENTORY_EXPECTATIONS,
    ids=[expected.name for expected in FIXTURE_INVENTORY_EXPECTATIONS],
)
def test_committed_orcad_fixture_inventory_is_locked(
    expected: _FixtureInventoryExpectation,
) -> None:
    assert expected.dsn.exists()

    ole_inventory = _read_ole_inventory(expected.dsn)
    stream_paths = ole_inventory.stream_paths
    variant_store_streams = tuple(
        path for path in stream_paths if path.startswith("CIS/VariantStore/")
    )
    ctx = ParseContext()
    raw = parse_dsn(expected.dsn, ctx)

    actual_view_names = tuple(
        sorted({path.split("/")[1] for path in stream_paths if path.startswith("Views/")})
    )
    page_streams = [
        path for path in stream_paths if path.startswith("Views/") and "/Pages/" in path
    ]
    package_streams = [path for path in stream_paths if path.startswith("Packages/")]
    # OrCAD stores hierarchy payloads at paths like Views/<view>/Hierarchy/Hierarchy.
    hierarchy_streams = [path for path in stream_paths if "Hierarchy/Hierarchy" in path]
    erc_symbol_streams = tuple(
        sorted(path for path in stream_paths if path.startswith("Symbols/ERC"))
    )

    assert (
        _FixtureInventory(
            stream_count=len(stream_paths),
            view_names=actual_view_names,
            parsed_page_count=len(raw.pages),
            page_stream_count=len(page_streams),
            package_count=len(package_streams),
            hierarchy_stream_count=len(hierarchy_streams),
            variant_store_leaf_stream_count=len(variant_store_streams),
            has_variant_store_storage="CIS/VariantStore" in ole_inventory.storage_paths,
            cis_status=_cis_status(ole_inventory, variant_store_streams),
            erc_symbol_streams=erc_symbol_streams,
        )
        == expected.inventory
    )

    missing = [
        path for path in expected.required_project_files if not (expected.root / path).exists()
    ]
    assert missing == []
    assert [
        issue
        for issue in ctx.issues
        if issue.category in {"dsn_page_tail", "dsn_erc_object", "dsn_erc_symbol"}
    ] == []


@pytest.mark.parametrize(
    ("dsn_path", "expected_package_count"),
    [
        (OPENCELLULAR_BREAKOUT_DSN, 0),
        (OPENCELLULAR_SYNC_DSN, 10),
        (CP_SMARTGARDEN_DSN, 14),
        (ROHM_STEPPER_DSN, 81),
    ],
)
def test_committed_orcad_package_streams_parse_to_raw_inventory(
    dsn_path: Path, expected_package_count: int
) -> None:
    raw = parse_dsn(dsn_path)

    assert len(raw.packages) == expected_package_count


def test_orcad_package_stream_preserves_raw_package_fields_and_pin_order() -> None:
    raw = parse_dsn(ROHM_STEPPER_DSN)

    driver = raw.packages["Packages/BD63876EFV_0"]
    assert driver.name == "BD63876EFV_0"
    assert driver.refdes_prefix == "U"
    assert driver.pcb_footprint == "HTSSOP-B28"
    assert [(cell.ref, cell.normal_name) for cell in driver.part_cells] == [
        ("BD63876EFV_0", "BD63876EFV_0.Normal")
    ]
    assert [part.name for part in driver.library_parts] == ["BD63876EFV_0.Normal"]
    assert len(driver.devices) == 1
    assert driver.devices[0].refdes_suffix == "BD63876EFV_0"
    assert [pin.package_pin for pin in driver.devices[0].pins[:8]] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
    ]

    buffer = raw.packages["Packages/74LVC541A_0"]
    assert [pin.package_pin for pin in buffer.devices[0].pins[:4]] == [
        "1",
        "19",
        "2",
        "3",
    ]
    assert [pin.order for pin in buffer.devices[0].pins[:4]] == [0, 1, 2, 3]


def test_package_stream_eof_mismatch_records_path_and_offset() -> None:
    with olefile.OleFileIO(str(ROHM_STEPPER_DSN)) as ole:
        stream_path = "Packages/2-PIN Jumper_0"
        data = ole.openstream(stream_path).read()

    ctx = ParseContext()
    package = parse_package_stream(data + b"\x00", ctx, stream_path)

    assert package is None
    assert len(ctx.issues) == 1
    assert ctx.issues[0].category == "dsn_package_stream"
    assert stream_path in ctx.issues[0].message
    assert ctx.issues[0].message.count(f"{stream_path} at byte offset") == 1
    assert "trailing bytes" in ctx.issues[0].message


def test_cp_smartgarden_cis_group_ids_resolve_through_hierarchy_occurrences() -> None:
    raw = parse_dsn(CP_SMARTGARDEN_DSN)
    occurrence_to_instance = {
        occurrence.occurrence_id: occurrence.instance_db_id
        for occurrence in raw.hierarchy_occurrences
    }
    instances = _instances_by_db_id(CP_SMARTGARDEN_DSN)
    group_rows = _cis_group_rows(CP_SMARTGARDEN_DSN)

    assert len(raw.pages) == 4
    assert len(raw.hierarchy_occurrences) == 160
    assert len(group_rows) == 157
    assert Counter(row.group for row in group_rows) == {
        "DNM": 25,
        "DebuggerIF": 34,
        "Peripherals": 21,
        "RF": 32,
        "XDS": 45,
    }
    assert all(row.member_id in occurrence_to_instance for row in group_rows)
    assert all(occurrence_to_instance[row.member_id] in instances for row in group_rows)
    assert instances[occurrence_to_instance[20922]] == ("2_Peripherals", "R51")
    assert instances[occurrence_to_instance[20919]] == ("2_Peripherals", "R49")


def test_opencellular_sync_cis_placeholder_store_does_not_publish_variants() -> None:
    raw = parse_dsn(OPENCELLULAR_SYNC_DSN)
    project = load_orcad_project(OPENCELLULAR_SYNC_OPJ)

    assert raw.cis_variant_store.present is True
    assert raw.cis_variant_store.placeholder is True
    assert raw.cis_variant_store.variant_names == []
    assert raw.cis_variant_store.boms == []
    assert raw.cis_variant_store.groups == []
    assert project.variants == []


def test_cp_smartgarden_cis_dnm_group_publishes_not_fitted_variant() -> None:
    project = load_orcad_project(CP_SMARTGARDEN_OPJ)

    assert len(project.variants) == 1
    variant = project.variants[0]
    assert variant.name == "DNM"
    assert variant.order == 1
    assert variant.source_id == "CIS/VariantStore/Groups/DNM"
    assert variant.metadata == {
        "source_format": "orcad_cis",
        "dsn_cis_group_name": "DNM",
        "dsn_cis_group_stream_path": "CIS/VariantStore/Groups/GroupsDataStream",
        "dsn_cis_group_raw_fields": "DNM\x1f0",
        "not_fitted_evidence": "group_name,component_properties,bom_child_stream",
    }
    assert len(variant.overrides) == 25
    assert {override.field for override in variant.overrides} == {VariantField.DNP}
    assert {override.value for override in variant.overrides} == {True}
    assert {override.native_kind for override in variant.overrides} == {
        "orcad_cis_not_fitted_group_member"
    }
    assert {override.target.kind for override in variant.overrides} == {VariantTargetKind.COMPONENT}

    by_ref = {override.target.reference: override for override in variant.overrides}
    r51 = by_ref["R51"]
    assert r51.target.object_id == "dsn:component:page:2_Peripherals:component:1261346"
    assert r51.target.source_id == "page:2_Peripherals:component:1261346"
    assert r51.target.occurrence_id == "20922"
    assert r51.source_id == "CIS/VariantStore/Groups/DNM/DNM:0"
    assert r51.metadata == {
        "source_format": "orcad_cis",
        "dsn_cis_group_name": "DNM",
        "dsn_cis_group_stream_path": "CIS/VariantStore/Groups/GroupsDataStream",
        "dsn_cis_group_raw_fields": "DNM\x1f0",
        "dsn_cis_member_stream_path": "CIS/VariantStore/Groups/DNM/DNM",
        "dsn_cis_member_row_order": "0",
        "dsn_cis_member_state": "0",
        "dsn_cis_occurrence_id": "20922",
        "dsn_cis_resolved_instance_db_id": "1261346",
        "dsn_cis_resolution_kind": "hierarchy_occurrence",
        "not_fitted_evidence": "group_name,component_properties,bom_child_stream",
    }


def test_maxome_cis_dni_group_publishes_only_not_fitted_variant() -> None:
    project = load_orcad_project(MAXOME_1P1_OPJ)

    assert len(project.variants) == 1
    variant = project.variants[0]
    assert variant.name == "DNI"
    # 41 DNP overrides from group members + 2 update-storage snapshot carriers
    # (R22, J2) whose CIS-database values equal base — so no change-claiming
    # PART_NUMBERS/FOOTPRINTS/PARAMETER/ALTERNATE_PART override is emitted (G3/G4).
    assert len(variant.overrides) == 43
    dnp = [o for o in variant.overrides if o.field == VariantField.DNP]
    update_storage = [
        o for o in variant.overrides if o.native_kind == "orcad_cis_update_storage_row"
    ]
    assert len(dnp) == 41
    assert {override.value for override in dnp} == {True}
    assert {override.native_kind for override in dnp} == {"orcad_cis_not_fitted_group_member"}
    assert len(update_storage) == 2
    assert {o.field for o in update_storage} == {VariantField.OTHER}
    assert {o.value for o in update_storage} == {None}
    assert {o.target.reference for o in update_storage} == {"R22", "J2"}
    # G4: OrCAD never emits ALTERNATE_PART, even with update-storage rows present.
    assert not any(o.field == VariantField.ALTERNATE_PART for o in variant.overrides)
    # Update-storage targets coexist with their own DNP overrides (V4).
    update_refs = {o.target.reference for o in update_storage}
    assert update_refs <= {o.target.reference for o in dnp}
    # Full raw row is preserved as snapshot metadata (nothing discarded).
    r22 = next(o for o in update_storage if o.target.reference == "R22")
    assert r22.metadata["dsn_cis_update_storage"] == "snapshot"
    assert r22.metadata["dsn_cis_update_col:Value"] == "10kOhms"
    assert r22.metadata["dsn_cis_update_col:PCB Footprint"] == "RES_0402"
    assert variant.metadata == {
        "source_format": "orcad_cis",
        "dsn_cis_group_name": "DNI",
        "dsn_cis_group_stream_path": "CIS/VariantStore/Groups/GroupsDataStream",
        "dsn_cis_group_raw_fields": "DNI\x1f0",
        "not_fitted_evidence": "group_name,bom_child_stream",
    }
    assert all(
        override.metadata["dsn_cis_member_stream_path"] == "CIS/VariantStore/Groups/DNI/DNI"
        for override in dnp
    )


def test_cp_smartgarden_cis_variant_names_preserve_stream_order_and_duplicates() -> None:
    raw = parse_dsn(CP_SMARTGARDEN_DSN)

    assert raw.cis_variant_store.present is True
    assert raw.cis_variant_store.placeholder is False
    # 14 declared names; the 15th stream string is the last-selected Part
    # Manager group, parsed into ``last_selected_group`` rather than misreported
    # as an extra name plus a count-mismatch warning (finding V4 / G1).
    assert [name.name for name in raw.cis_variant_store.variant_names] == [
        "Common",
        "DNM",
        "bom-Standard",
        "bom-Standard-Common",
        "Common",
        "DNM",
        "XDS",
        "DebuggerIF",
        "Peripherals",
        "RF",
        "DebuggerIF",
        "Peripherals",
        "RF",
        "XDS",
    ]
    assert raw.cis_variant_store.last_selected_group == "RF"
    assert [name.order for name in raw.cis_variant_store.variant_names] == list(range(14))
    assert [name.duplicate_index for name in raw.cis_variant_store.variant_names] == [
        0,
        0,
        0,
        0,
        1,
        1,
        0,
        0,
        0,
        0,
        1,
        1,
        1,
        1,
    ]


def test_cp_smartgarden_cis_bom_preserves_raw_ids_and_resolution_metadata() -> None:
    raw = parse_dsn(CP_SMARTGARDEN_DSN)

    assert len(raw.cis_variant_store.boms) == 1
    bom = raw.cis_variant_store.boms[0]
    assert bom.name == "Standard"
    assert bom.stream_path == "CIS/VariantStore/BOM/BOMDataStream"
    assert raw.cis_variant_store.bom_raw_fields == ["1", "Standard"]
    assert [(child.stream_path, child.values) for child in bom.child_string_lists] == [
        (
            "CIS/VariantStore/BOM/Standard/Standard",
            ["6", "Common", "DNM", "DebuggerIF", "Peripherals", "RF", "XDS"],
        )
    ]
    assert len(bom.entries) == 140
    assert sum(entry.resolved_instance_db_id is not None for entry in bom.entries) == 139
    assert [entry.raw_id for entry in bom.entries if entry.resolved_instance_db_id is None] == [
        19405
    ]
    assert all(entry.resolution_kind for entry in bom.entries)


def test_cp_smartgarden_cis_bom_entries_match_independent_stream_decode() -> None:
    """H5/T5: the BOM entry ids and resolution counts are echoes of the raw
    stream, so verify them against an independent OLE/CIS decode that shares no
    code with the parser (extends the neighbouring group-decode pattern)."""
    raw = parse_dsn(CP_SMARTGARDEN_DSN)
    bom = raw.cis_variant_store.boms[0]
    parser_ids = [entry.raw_id for entry in bom.entries]

    independent_ids = _cis_bom_part_data_ids(CP_SMARTGARDEN_DSN, bom.name)
    assert parser_ids == independent_ids
    assert len(independent_ids) == 140

    # Independently resolve each id through the hierarchy-occurrence map and
    # confirm the parser's 139/140 split (only 19405 is a dead snapshot id).
    occurrence_to_instance = {
        occurrence.occurrence_id: occurrence.instance_db_id
        for occurrence in raw.hierarchy_occurrences
    }
    resolved = [oid for oid in independent_ids if oid in occurrence_to_instance]
    unresolved = [oid for oid in independent_ids if oid not in occurrence_to_instance]
    assert len(resolved) == 139
    assert unresolved == [19405]
    assert [
        entry.raw_id for entry in bom.entries if entry.resolved_instance_db_id is not None
    ] == resolved


def test_cp_smartgarden_cis_groups_preserve_members_and_resolve_all_rows() -> None:
    raw = parse_dsn(CP_SMARTGARDEN_DSN)

    groups_by_name = {group.name: group for group in raw.cis_variant_store.groups}
    assert list(groups_by_name) == ["DNM", "XDS", "DebuggerIF", "Peripherals", "RF"]
    assert {name: group.raw_fields for name, group in groups_by_name.items()} == {
        "DNM": ["DNM", "0"],
        "XDS": ["XDS", "0"],
        "DebuggerIF": ["DebuggerIF", "0"],
        "Peripherals": ["Peripherals", "0"],
        "RF": ["RF", "0"],
    }
    assert {name: len(group.members) for name, group in groups_by_name.items()} == {
        "DNM": 25,
        "XDS": 45,
        "DebuggerIF": 34,
        "Peripherals": 21,
        "RF": 32,
    }
    assert sum(len(group.members) for group in groups_by_name.values()) == 157
    assert (
        sum(
            member.resolved_instance_db_id is not None
            for group in groups_by_name.values()
            for member in group.members
        )
        == 157
    )
    assert Counter(member.state for member in groups_by_name["DebuggerIF"].members) == {
        "0": 1,
        "1": 33,
    }
    assert all(
        member.stream_path == f"CIS/VariantStore/Groups/{group.name}/{group.name}"
        for group in groups_by_name.values()
        for member in group.members
    )


def test_maxome_cis_update_storage_rows_parse_and_resolve() -> None:
    raw = parse_dsn(MAXOME_1P1_DSN)

    groups_by_name = {group.name: group for group in raw.cis_variant_store.groups}
    dni = groups_by_name["DNI"]
    assert len(dni.members) == 41
    assert sum(member.resolved_instance_db_id is not None for member in dni.members) == 41
    assert len(dni.update_storage_rows) == 2
    assert sum(row.resolved_instance_db_id is not None for row in dni.update_storage_rows) == 2
    assert dni.update_storage_rows[0].stream_path == (
        "CIS/VariantStore/Groups/DNI/UpdateStorageGroupDataStream"
    )
    assert "Part Number" in dni.update_storage_rows[0].columns
    assert "Value" in dni.update_storage_rows[0].columns
    assert len(dni.update_storage_rows[0].columns) == len(dni.update_storage_rows[0].values)


def test_maxome_0p1_cis_update_storage_maps_four_snapshot_carriers() -> None:
    # The official 0p1 BOM xlsx marks D13, R22, D11 and J2 as Do-Not-Install,
    # and each carries a CIS update-storage snapshot row. All four map to
    # snapshot carrier overrides on the DNI variant with no change-claiming
    # PART_NUMBERS/FOOTPRINTS/PARAMETER override (values equal base — finding V1).
    project = load_orcad_project(MAXOME_0P1_OPJ)
    variant = next(v for v in project.variants if v.name == "DNI")

    update_storage = [
        o for o in variant.overrides if o.native_kind == "orcad_cis_update_storage_row"
    ]
    assert {o.target.reference for o in update_storage} == {"D13", "R22", "D11", "J2"}
    assert {o.field for o in update_storage} == {VariantField.OTHER}
    # Every mapped column is snapshot-equal to base, so no typed change override.
    assert not any(
        o.field in {VariantField.PART_NUMBERS, VariantField.FOOTPRINTS, VariantField.PARAMETER}
        for o in variant.overrides
    )
    d13 = next(o for o in update_storage if o.target.reference == "D13")
    assert d13.metadata["dsn_cis_update_col:Part Number"] == "MMB-DIO-00000027"
    assert d13.metadata["dsn_cis_update_col:PCB Footprint"] == "SOT23-6"
    assert d13.metadata["dsn_cis_update_col:Name"] == "INS6210462"
    assert d13.metadata["dsn_cis_update_storage"] == "snapshot"


def test_maxome_cis_update_storage_emits_no_alternate_part() -> None:
    # G4: OrCAD update-storage rows are snapshots, never substitutions, so no
    # ALTERNATE_PART override is ever emitted even though rows are present.
    for opj in (MAXOME_0P1_OPJ, MAXOME_1P1_OPJ):
        project = load_orcad_project(opj)
        overrides = [o for v in project.variants for o in v.overrides]
        assert any(o.native_kind == "orcad_cis_update_storage_row" for o in overrides)
        assert not any(o.field == VariantField.ALTERNATE_PART for o in overrides)
        assert not any(o.native_kind == "orcad_alternate_part" for o in overrides)


def test_cp_smartgarden_bom_part_data_is_stale_occurrence_namespace() -> None:
    # G5: CP's BOMPartData ids share the hierarchy-occurrence namespace but are a
    # frozen snapshot (139/140 join; never derive membership from it).
    raw = parse_dsn(CP_SMARTGARDEN_DSN)
    bom = raw.cis_variant_store.boms[0]

    resolved = sum(e.resolved_instance_db_id is not None for e in bom.entries)
    assert bom.part_data_stale_snapshot is True
    assert bom.part_data_namespace == "hierarchy_occurrence"
    assert (resolved, len(bom.entries)) == (139, 140)


def test_maxome_bom_part_data_is_unknown_namespace() -> None:
    # G5: Maxome's BOMPartData ids join no hierarchy occurrences at all.
    raw = parse_dsn(MAXOME_1P1_DSN)
    bom = raw.cis_variant_store.boms[0]

    resolved = sum(e.resolved_instance_db_id is not None for e in bom.entries)
    assert bom.part_data_stale_snapshot is True
    assert bom.part_data_namespace == "unknown_namespace"
    assert resolved == 0
    # The 26 frozen ids are a Maxome-family namespace that joins nothing.
    assert len(bom.entries) == 26


def test_launchxl_cache_pins_align_after_convert_view_fix() -> None:
    # A6: the two ``<base>.Convert`` symbols (CONN_BANAN_2PIN, LDB212G4020C_6PIN)
    # no longer misalign, so no cache misalignment warning fires.
    ctx = ParseContext()
    _ = parse_dsn(CP_SMARTGARDEN_DSN, ctx)

    categories = Counter(issue.category for issue in ctx.issues)
    assert categories["dsn_cache_pin_misalignment"] == 0


def test_maxome_cis_update_storage_materializes_to_sql() -> None:
    from phosphor_eda.query.sql.loader import load_database

    project = load_orcad_project(MAXOME_1P1_OPJ)
    con = load_database(project)
    try:
        rows = con.execute(
            """
            SELECT target_reference, field, applied
            FROM variant_overrides
            WHERE native_kind = 'orcad_cis_update_storage_row'
            ORDER BY target_reference
            """
        ).fetchall()
        assert rows == [
            ("J2", "other", False),
            ("R22", "other", False),
        ]
        metadata = con.execute(
            """
            SELECT metadata
            FROM variant_overrides
            WHERE native_kind = 'orcad_cis_update_storage_row'
              AND target_reference = 'R22'
            """
        ).fetchone()
        assert metadata is not None
        parsed = json.loads(metadata[0])
        assert parsed["dsn_cis_update_storage"] == "snapshot"
        assert parsed["dsn_cis_update_col:Value"] == "10kOhms"
    finally:
        con.close()


def test_cis_groups_data_stream_pairs_records_without_cross_record_shift() -> None:
    # B9: GroupsDataStream records are framed by a ``\xb0\xb0`` separator with a
    # single ``\xb0`` between name and value. Splitting on records before fields
    # keeps each name/value pair aligned; the old "drop empties, then pair by
    # twos" pass mis-paired every group after any empty field. Distinct values
    # (0, 9, 0) make a one-slot shift detectable.
    from phosphor_eda.formats.dsn.cis import parse_cis_variant_store

    def record(name: str, value: str) -> bytes:
        return name.encode() + b"\xb0" + value.encode()

    separator = b"\xb0\xb0"
    payload = (
        separator.join([record("A", "0"), record("B", "9"), record("VB Buf", "0")]) + separator
    )
    streams = {"CIS/VariantStore/Groups/GroupsDataStream": payload}

    store = parse_cis_variant_store(streams, {"CIS/VariantStore"}, {}, ParseContext())

    assert [(group.name, group.raw_fields) for group in store.groups] == [
        ("A", ["A", "0"]),
        ("B", ["B", "9"]),
        ("VB Buf", ["VB Buf", "0"]),
    ]


def test_cis_groups_data_stream_warns_on_short_row() -> None:
    # B9: a record with fewer than two fields is skipped with a diagnostic
    # instead of a bare continue.
    from phosphor_eda.formats.dsn.cis import parse_cis_variant_store

    payload = b"LONELY\xb0\xb0GOOD\xb00\xb0\xb0"
    streams = {"CIS/VariantStore/Groups/GroupsDataStream": payload}
    ctx = ParseContext()

    store = parse_cis_variant_store(streams, {"CIS/VariantStore"}, {}, ctx)

    assert [group.name for group in store.groups] == ["GOOD"]
    assert any(
        "has 1 field(s)" in issue.message for issue in ctx.issues if issue.category == "dsn_cis"
    )


def test_cis_group_members_keep_positional_fields_with_empty_state() -> None:
    # Group member rows are ``state\xb0occurrence_id`` framed by ``~``. Dropping
    # empty fields before the positional read shifted the id out of position
    # (the same field-shift bug GroupsDataStream already fixed), so a member
    # with an empty state was silently lost. Keep empty fields in place.
    from phosphor_eda.formats.dsn.cis import parse_cis_variant_store

    groups_payload = b"G\xb00\xb0\xb0"
    member_payload = b"1\xb010~\xb020"
    streams = {
        "CIS/VariantStore/Groups/GroupsDataStream": groups_payload,
        "CIS/VariantStore/Groups/G/G": member_payload,
    }
    occurrence_to_instance = {10: 100, 20: 200}

    store = parse_cis_variant_store(
        streams, {"CIS/VariantStore"}, occurrence_to_instance, ParseContext()
    )

    (group,) = store.groups
    assert [(m.state, m.occurrence_id, m.resolved_instance_db_id) for m in group.members] == [
        ("1", 10, 100),
        ("", 20, 200),
    ]


def test_resolve_symbol_pin_structured_only_symbol_resolves() -> None:
    # A symbol with structured pins but no legacy cache names must not fail
    # the alignment check; there is nothing to disagree with.
    symbol_pins = {"NEWSYM": [DsnSymbolPin(name="A"), DsnSymbolPin(name="B")]}
    resolved = resolve_symbol_pin("NEWSYM", "2", symbol_pins, symbol_pin_names={})
    assert resolved is not None
    assert resolved.name == "B"


def test_resolve_symbol_pin_normalizes_expected_name() -> None:
    # The expected name may carry overline markup; both sides normalize.
    symbol_pins = {"SYM": [DsnSymbolPin(name="C\\S\\")]}
    resolved = resolve_symbol_pin("SYM", "1", symbol_pins, expected_pin_name="C\\S\\")
    assert resolved is not None
    assert resolved.name == "C\\S\\"


def test_cp_smartgarden_cache_symbol_pins_are_structured_without_pin_name_regression() -> None:
    raw = parse_dsn(CP_SMARTGARDEN_DSN)

    # The ``RESISTOR.Convert`` de Morgan view name is no longer appended as a
    # phantom pin; legacy names now match the structured layout, so the two pin
    # sources align and structured metadata resolves (A6).
    assert raw.symbol_pin_names["CONN_BANAN_2PIN"] == ["1", "2"]
    assert [pin.name for pin in raw.symbol_pins["CONN_BANAN_2PIN"]] == ["1", "2"]
    resolved = resolve_symbol_pin(
        "CONN_BANAN_2PIN",
        "1",
        raw.symbol_pins,
        expected_pin_name="1",
        symbol_pin_names=raw.symbol_pin_names,
    )
    assert resolved is not None
    assert resolved.name == "1"

    processor_pins = raw.symbol_pins["IC_PROC_TM4C1294NCPDT_TQFP128B"]
    assert [(pin.name, pin.port_type, pin.port_type_name) for pin in processor_pins[:4]] == [
        ("nRST", 0, "input"),
        ("OSC0", 0, "input"),
        ("OSC1", 2, "output"),
        ("XOSC0", 0, "input"),
    ]
    assert processor_pins[0].start == (0, 10)
    assert processor_pins[0].hotpt == (-10, 10)
    assert processor_pins[0].pin_shape == 32


def test_cp_smartgarden_cache_pin_port_type_reaches_public_pin_metadata() -> None:
    design = dsn_to_design(parse_dsn(CP_SMARTGARDEN_DSN), name="CP SmartGarden")
    processor = next(
        component
        for component in design.components
        if component.reference == "U5" and component.part == "IC_PROC_TM4C1294NCPDT_TQFP128B"
    )
    nrst = next(pin for pin in processor.pins if pin.name == "nRST")
    osc1 = next(pin for pin in processor.pins if pin.name == "OSC1")

    assert nrst.metadata["dsn_symbol_pin_port_type"] == "input"
    assert nrst.metadata["electrical"] == "input"
    assert nrst.metadata["dsn_symbol_pin_start"] == "0,10"
    assert nrst.metadata["dsn_symbol_pin_hotpt"] == "-10,10"
    assert osc1.metadata["dsn_symbol_pin_port_type"] == "output"
    assert osc1.metadata["electrical"] == "output"


def test_rohm_stepper_hierarchy_occurrences_cover_all_placed_instances() -> None:
    raw = parse_dsn(ROHM_STEPPER_DSN)
    instance_ids = {
        instance.db_id for page in raw.pages for instance in page.instances if instance.db_id
    }
    occurrence_instance_ids = {
        occurrence.instance_db_id for occurrence in raw.hierarchy_occurrences
    }
    net_mapping_db_ids = [mapping.db_id for mapping in raw.net_id_mappings]

    assert len(raw.pages) == 5
    assert len(raw.net_id_mappings) == 39
    assert len(net_mapping_db_ids) == len(set(net_mapping_db_ids))
    assert [(mapping.db_id, mapping.name) for mapping in raw.net_id_mappings[:3]] == [
        (283, "CLK_E"),
        (371, "VCC_P"),
        (412, "I01"),
    ]
    # 173, not 174: the phantom 0x0d instance the old parser fabricated from a
    # misparsed 0x0c block record (its garbage db_id matched a hierarchy
    # occurrence) is gone now that block placements are type-gated (A2).
    assert len(raw.hierarchy_occurrences) == 173
    assert occurrence_instance_ids == instance_ids


def test_committed_orcad_erc_symbol_catalog_is_preserved() -> None:
    assert [
        (
            symbol.stream_path,
            symbol.name,
            symbol.marker_category,
            symbol.source_library,
            symbol.primitive_count,
        )
        for symbol in parse_dsn(OPENCELLULAR_SYNC_DSN).erc_symbols
    ] == [("Symbols/ERC", "ERC", "erc", "", 1)]
    assert [
        (
            symbol.stream_path,
            symbol.name,
            symbol.marker_category,
            symbol.source_library,
            symbol.primitive_count,
        )
        for symbol in parse_dsn(CP_SMARTGARDEN_DSN).erc_symbols
    ] == [("Symbols/ERC", "ERC", "erc", "", 1)]
    assert [
        (
            symbol.stream_path,
            symbol.name,
            symbol.marker_category,
            symbol.source_library,
            symbol.primitive_count,
        )
        for symbol in parse_dsn(ROHM_STEPPER_DSN).erc_symbols
    ] == [
        ("Symbols/ERC", "ERC", "erc", "", 1),
        ("Symbols/ERC_PHYSICAL", "ERC_PHYSICAL", "erc_physical", "", 1),
    ]


def test_opencellular_power_unit_erc_objects_are_preserved_as_raw_diagnostics() -> None:
    raw = parse_dsn(OPENCELLULAR_POWER_UNIT_DSN)
    erc_objects = [erc_object for page in raw.pages for erc_object in page.erc_objects]

    assert len(erc_objects) == 53
    assert erc_objects[0].page_name == "03_Power_Input_Connectors"
    assert erc_objects[0].symbol_name == "ERC"
    assert erc_objects[0].loc_x == 1195
    assert erc_objects[0].loc_y == 305
    assert erc_objects[0].message == (
        "ERROR(ORCAP-1620): Port has a type which is inconsistent with other ports on the net"
    )
    assert erc_objects[0].subject == "DC_Input_Fault "
    assert erc_objects[0].detail == ("SCHEMATIC1, 03_Power_Input_Connectors  (304.80, 78.74) ")
    # ERC bbox anchor decodes to the mm coordinate embedded in the detail string.
    assert erc_objects[0].bbox_x1 == 1200
    assert erc_objects[0].bbox_y1 == 310
    assert (round(erc_objects[0].bbox_x1 * 0.254, 2), round(erc_objects[0].bbox_y1 * 0.254, 2)) == (
        304.80,
        78.74,
    )


def test_opencellular_power_unit_drc_violations_surface_on_schematic() -> None:
    """D3: placed ERC objects reach the public schematic as raw DRC evidence."""
    design = dsn_to_design(parse_dsn(OPENCELLULAR_POWER_UNIT_DSN))

    assert design.metadata["dsn_drc_violation_count"] == "53"
    violations = json.loads(design.metadata["dsn_drc_violations"])
    assert len(violations) == 53
    assert violations[0] == {
        "page": "03_Power_Input_Connectors",
        "message": (
            "ERROR(ORCAP-1620): Port has a type which is inconsistent with other ports on the net"
        ),
        "subject": "DC_Input_Fault",
        "x_mm": 304.80,
        "y_mm": 78.74,
    }


def test_opencellular_power_unit_native_no_connect_markers() -> None:
    """D1: native no-connect markers become public NC pins (sidecar-free fixture)."""
    design = dsn_to_design(parse_dsn(OPENCELLULAR_POWER_UNIT_DSN))
    no_connect_pins = [
        pin for component in design.components for pin in component.pins if pin.no_connect
    ]

    assert len(no_connect_pins) == 97
    assert all(pin.metadata["dsn_no_connect_source"] == "dsn_marker" for pin in no_connect_pins)


@pytest.mark.parametrize(
    ("dsn_path", "expected"),
    [
        (ROHM_STEPPER_DSN, 31),
        (RPI_CMIO_DSN, 33),
        (RPI_PICO_DSN, 2),
        (RPI_PICOW_DSN, 17),
        (OPENCELLULAR_SYNC_DSN, 13),
        (OPENCELLULAR_BREAKOUT_DSN, 24),
    ],
)
def test_native_no_connect_counts_on_committed_fixtures(dsn_path: Path, expected: int) -> None:
    """D1: native markers alone (no sidecar) yield the expected NC pin count."""
    design = dsn_to_design(parse_dsn(dsn_path))
    no_connect_pins = [
        pin for component in design.components for pin in component.pins if pin.no_connect
    ]
    assert len(no_connect_pins) == expected
    assert all(pin.metadata["dsn_no_connect_source"] == "dsn_marker" for pin in no_connect_pins)


def test_rohm_u5_native_no_connect_pins() -> None:
    """D5: rohm U5 negative-order pins resolve to the cache NC1-NC6 + Pad names."""
    design = dsn_to_design(parse_dsn(ROHM_STEPPER_DSN))
    u5 = next(component for component in design.components if component.reference == "U5")
    nc_names = sorted(pin.name for pin in u5.pins if pin.no_connect)
    assert nc_names == ["NC1", "NC2", "NC3", "NC4", "NC5", "NC6", "Pad"]


@pytest.mark.parametrize(
    ("dsn_path", "wired_markers", "power_markers"),
    [
        (OPENCELLULAR_POWER_UNIT_DSN, 100, 5),
        (RPI_PICOW_DSN, 6, 0),
        (OPENCELLULAR_BREAKOUT_DSN, 12, 0),
    ],
)
def test_native_no_connect_marker_ambiguity_diagnostics(
    dsn_path: Path, wired_markers: int, power_markers: int
) -> None:
    """D2: markers on wired/power-anchored pins diagnose without setting no_connect."""
    ctx = ParseContext()
    _ = dsn_to_design(parse_dsn(dsn_path), ctx=ctx)
    categories = Counter(issue.category for issue in ctx.issues)
    assert categories["dsn_marker_on_wired_pin"] == wired_markers
    assert categories["dsn_marker_on_power_pin"] == power_markers


def test_native_marker_suppresses_netless_diagnostic() -> None:
    """D2: an intentional NC never fires the netless-pin parse diagnostic."""
    ctx = ParseContext()
    _ = dsn_to_design(parse_dsn(ROHM_STEPPER_DSN), ctx=ctx)
    # rohm's 31 netless pins are all marked NC, so none are reported as netless.
    assert not any(issue.category == "dsn_netless_pin" for issue in ctx.issues)


def test_cp_smartgarden_native_and_sidecar_no_connect_three_way_split() -> None:
    """D1/N2: LAUNCHXL's markers and sidecar NCs coexist as 77/6/17 provenance."""
    project = load_orcad_project(CP_SMARTGARDEN_OPJ)
    assert project.schematic is not None
    design = project.schematic

    sources = Counter(
        pin.metadata["dsn_no_connect_source"]
        for component in design.components
        for pin in component.pins
        if pin.no_connect
    )
    # 77 pins carry both a native marker and a sidecar NC record; 17 are
    # sidecar-only (mounting holes/fiducials with no designer marker).
    assert sources == {"dsn_marker,pstxnet.dat": 77, "pstxnet.dat": 17}

    ctx = ParseContext()
    raw = parse_dsn(CP_SMARTGARDEN_DSN)
    apply_packaged_pin_names(raw, CP_SMARTGARDEN_ALLEGRO, ctx)
    apply_packaged_no_connects(raw, CP_SMARTGARDEN_ALLEGRO, ctx)
    _ = dsn_to_design(raw, ctx=ctx)
    # The remaining 6 markers land on wired pins (stale markers).
    assert Counter(issue.category for issue in ctx.issues)["dsn_marker_on_wired_pin"] == 6


def test_opencellular_breakout_native_marker_beats_stale_sidecar() -> None:
    """N2: breakout markers cover NC pins its own stale sidecar omits."""
    project = load_orcad_project(OPENCELLULAR_BREAKOUT_OPJ)
    assert project.schematic is not None
    design = project.schematic

    no_connect_pins = [
        pin for component in design.components for pin in component.pins if pin.no_connect
    ]
    sources = Counter(pin.metadata["dsn_no_connect_source"] for pin in no_connect_pins)
    # 24 total NC pins: 20 confirmed by both marker and sidecar, 4 the stale
    # sidecar omits but the native marker still catches.
    assert len(no_connect_pins) == 24
    assert sources == {"dsn_marker,pstxnet.dat": 20, "dsn_marker": 4}


def test_cp_smartgarden_sidecar_package_pin_metadata() -> None:
    """B4: sidecar pstchip physical pin numbers surface as pin metadata."""
    project = load_orcad_project(CP_SMARTGARDEN_OPJ)
    assert project.schematic is not None
    design = project.schematic

    p7_pin = next(
        pin
        for component in design.components
        if component.reference == "P7"
        for pin in component.pins
        if pin.name == "7"
    )
    assert p7_pin.metadata["dsn_sidecar_package_pin"] == "7"


def test_cp_smartgarden_pstxnet_exposes_no_connect_members() -> None:
    no_connects = parse_pstxnet_no_connects(CP_SMARTGARDEN_ALLEGRO / "pstxnet.dat")

    assert len(no_connects) == 94
    assert no_connects[0].source_path.endswith("Allegro/pstxnet.dat")
    assert no_connects[0].raw_net_name == "NC"
    assert no_connects[0].refdes == "P7"
    assert no_connects[0].pin_token == "7"
    assert no_connects[0].pin_name == "7"


def test_cp_smartgarden_sidecar_no_connects_resolve_to_public_pin_ids() -> None:
    raw = parse_dsn(CP_SMARTGARDEN_DSN)
    apply_packaged_pin_names(raw, CP_SMARTGARDEN_ALLEGRO)

    # apply_packaged_no_connects is idempotent: re-running it on the same design
    # must not duplicate or re-append no-connect records. Apply once, capture the
    # result, apply again, and assert the record list is unchanged.
    apply_packaged_no_connects(raw, CP_SMARTGARDEN_ALLEGRO, ParseContext())
    after_first = [(nc.refdes, nc.pin_token, nc.matched_pin_id) for nc in raw.no_connect_pins]
    apply_packaged_no_connects(raw, CP_SMARTGARDEN_ALLEGRO, ParseContext())
    after_second = [(nc.refdes, nc.pin_token, nc.matched_pin_id) for nc in raw.no_connect_pins]
    assert after_second == after_first

    design = dsn_to_design(raw)
    public_pin_ids = {pin.id for component in design.components for pin in component.pins}

    assert len(raw.no_connect_pins) == 94
    assert all(no_connect.matched_pin_id for no_connect in raw.no_connect_pins)
    assert {no_connect.matched_pin_id for no_connect in raw.no_connect_pins} <= public_pin_ids
    assert {
        no_connect.matched_pin_id
        for no_connect in raw.no_connect_pins
        if no_connect.refdes == "P7" and no_connect.pin_token == "7"
    } == {"dsn:component:page:3_Debugger Interface:component:1240098:pin:8"}


def test_cp_smartgarden_project_marks_pstxnet_no_connect_pins() -> None:
    project = load_orcad_project(CP_SMARTGARDEN_OPJ)
    assert project.schematic is not None

    design = project.schematic
    no_connect_pins = [
        pin for component in design.components for pin in component.pins if pin.no_connect
    ]

    assert len(no_connect_pins) == 94
    assert all(net.name != "NC" for net in design.nets)

    p7_pin = next(
        pin
        for component in design.components
        if component.reference == "P7"
        for pin in component.pins
        if pin.name == "7"
    )
    assert p7_pin.no_connect is True
    assert p7_pin.metadata["dsn_no_connect_raw_net_name"] == "NC"
    assert p7_pin.metadata["dsn_no_connect_pin_token"] == "7"
    assert p7_pin.metadata["dsn_no_connect_pin_name"] == "7"

    u5_pin = next(
        pin
        for component in design.components
        if component.reference == "U5"
        for pin in component.pins
        if pin.name == "XOSC1"
    )
    assert u5_pin.no_connect is True
    assert u5_pin.metadata["dsn_no_connect_pin_token"] == "67"


def test_pstxnet_no_connect_ambiguous_pin_name_produces_diagnostic(tmp_path: Path) -> None:
    (tmp_path / "pstxnet.dat").write_text(
        "\n".join(
            (
                "NET_NAME",
                "'NC'",
                "NODE_NAME\tU1 10",
                " '@SCHEMATIC:INS1@DUP.DUP.NORMAL(CHIPS)':",
                " 'A':;",
            )
        )
    )
    raw = ParsedDesign(
        symbol_pin_names={"DUP": ["A", "A"]},
        pages=[
            RawSchematicPage(
                name="P1",
                instances=[
                    PlacedInstance(
                        package_name="DUP.Normal",
                        reference="U1",
                        pin_connections=[
                            PinConnection(pin_number="1"),
                            PinConnection(pin_number="2"),
                        ],
                    )
                ],
            )
        ],
    )
    ctx = ParseContext()

    apply_packaged_no_connects(raw, tmp_path, ctx)

    assert len(raw.no_connect_pins) == 1
    assert raw.no_connect_pins[0].matched_pin_id == ""
    assert not any(
        pin.no_connect
        for page in raw.pages
        for instance in page.instances
        for pin in instance.pin_connections
    )
    assert [issue.category for issue in ctx.issues] == ["dsn_sidecar_no_connect_ambiguous"]


def test_pstxnet_no_connect_package_pin_number_disambiguates_name_match(
    tmp_path: Path,
) -> None:
    (tmp_path / "pstxnet.dat").write_text(
        "\n".join(
            (
                "NET_NAME",
                "'NC'",
                "NODE_NAME\tU1 42",
                " '@SCHEMATIC:INS1@DUP.DUP.NORMAL(CHIPS)':",
                " 'A':;",
            )
        )
    )
    raw = ParsedDesign(
        symbol_pin_names={"DUP": ["A", "A"]},
        pages=[
            RawSchematicPage(
                name="P1",
                instances=[
                    PlacedInstance(
                        package_name="DUP.Normal",
                        reference="U1",
                        pin_connections=[
                            PinConnection(pin_number="1", package_pin_number="41"),
                            PinConnection(pin_number="2", package_pin_number="42"),
                        ],
                    )
                ],
            )
        ],
    )
    ctx = ParseContext()

    apply_packaged_no_connects(raw, tmp_path, ctx)

    assert raw.no_connect_pins[0].matched_pin_id == "dsn:component:page:P1:component:0:pin:2"
    assert raw.pages[0].instances[0].pin_connections[1].no_connect is True
    assert ctx.issues == []


def test_pstxnet_no_connect_empty_pin_name_does_not_match_empty_candidate_name(
    tmp_path: Path,
) -> None:
    (tmp_path / "pstxnet.dat").write_text(
        "\n".join(
            (
                "NET_NAME",
                "'NC'",
                "NODE_NAME\tU1 99",
            )
        )
    )
    raw = ParsedDesign(
        pages=[
            RawSchematicPage(
                name="P1",
                instances=[
                    PlacedInstance(
                        package_name="DUP.Normal",
                        reference="U1",
                        pin_connections=[PinConnection(pin_number="1")],
                    )
                ],
            )
        ],
    )
    ctx = ParseContext()

    apply_packaged_no_connects(raw, tmp_path, ctx)

    assert raw.no_connect_pins[0].pin_name == ""
    assert raw.no_connect_pins[0].matched_pin_id == ""
    assert raw.pages[0].instances[0].pin_connections[0].no_connect is False
    assert [issue.category for issue in ctx.issues] == ["dsn_sidecar_no_connect_unresolved"]


def test_rohm_stepper_preserves_view_page_ownership() -> None:
    raw = parse_dsn(ROHM_STEPPER_DSN)

    views_by_name = {view.name: view for view in raw.views}
    assert set(views_by_name) == {
        "Clock-In Input",
        "Onboard Manual Controls",
        "Parallel Input",
        "Revision History",
        "Top Block Diagram",
    }
    assert {name: tuple(view.page_names) for name, view in views_by_name.items()} == {
        "Clock-In Input": ("Clock-In",),
        "Onboard Manual Controls": ("Controls",),
        "Parallel Input": ("Parallel",),
        "Revision History": ("History",),
        "Top Block Diagram": ("Top",),
    }
    assert {name: tuple(view.hierarchy_stream_paths) for name, view in views_by_name.items()} == {
        "Clock-In Input": (),
        "Onboard Manual Controls": (),
        "Parallel Input": (),
        "Revision History": ("Views/Revision History/Hierarchy/Hierarchy",),
        "Top Block Diagram": ("Views/Top Block Diagram/Hierarchy/Hierarchy",),
    }
    assert {page.name: page.view_name for page in raw.pages} == {
        "Clock-In": "Clock-In Input",
        "Controls": "Onboard Manual Controls",
        "Parallel": "Parallel Input",
        "History": "Revision History",
        "Top": "Top Block Diagram",
    }
    assert all(page.stream_path.startswith(f"Views/{page.view_name}/Pages/") for page in raw.pages)


def test_single_view_fixture_keeps_flat_page_shape_while_recording_owner() -> None:
    raw = parse_dsn(CP_SMARTGARDEN_DSN)

    assert len(raw.views) == 1
    assert raw.views[0].name == "CC1310_LaunchPad"
    assert tuple(raw.views[0].page_names) == (
        "1_CC1310RF",
        "2_Peripherals",
        "4_XDS110",
        "3_Debugger Interface",
    )
    assert [page.name for page in raw.pages] == [
        "1_CC1310RF",
        "2_Peripherals",
        "3_Debugger Interface",
        "4_XDS110",
    ]
    assert {page.view_name for page in raw.pages} == {"CC1310_LaunchPad"}


def test_rohm_stepper_opj_records_hierarchy_view_on_loaded_schematic_document() -> None:
    project = parse_opj_file(ROHM_STEPPER_ROOT / "Design Files for Rev 1.0/STEPPER.opj")
    schematic_docs = [doc for doc in project.documents if doc.path == r".\stepper.dsn"]

    assert len(schematic_docs) == 1
    assert schematic_docs[0].metadata["hierarchy_view_pages"] == "Top Block Diagram/Top"
    assert schematic_docs[0].metadata["hierarchy_view_paths"] == (
        r"E:\DESIGN FILES FOR REV 1.0\STEPPER.DSN"
    )


def test_rohm_end_to_end_project_locks_documented_facts() -> None:
    """H1/T1: the full ``load_orcad_project`` pipeline reproduces the facts the
    fixture's own documentation (README narrative, title block, datasheet tree)
    can be checked against. This end-to-end path is what would have caught the
    R2 phantom-instance bug (the count and refdes map both depended on it).

    Refdes -> part cross-check against the ``Data Sheets/`` tree and README:
      * U1 = SN74LVC541A_5  -- "Octal Buffer - Texas Inst - SN74LVC541A.pdf"
      * U2 = BD3570HFP_2    -- README "BD3570HFP LDO (U2)"; "LDO ... BD3570HFP.pdf"
      * U3 = ML610Q102      -- README "Lapis ML610Q102 (U3)"; "ML610Q101 & Q102 ...pdf"
      * U4 = BD8377FV-M_2   -- README "BD8377 12-ch LED Driver (U4)"; "BD8377 ...pdf"
      * U5 = BD63877EFV_5   -- clock-in driver; datasheet "BD6387xEFV" family.
      * U6 = BD63876EFV_2   -- README "BD63876 Parallel input (U6)"; "BD6387xEFV" family.

    The README's marketing narrative calls U5 a "BD63720", but the committed
    Rev 1.0 DSN actually places BD63877EFV, and the title block ("Rohm BD6387x
    Eval Board") plus the "BD6387xEFV" datasheet corroborate the BD6387x family
    over the marketing part number. The parse (the design file) is authoritative,
    so U5=BD63877EFV_5 is what we lock.
    """
    project = load_orcad_project(ROHM_STEPPER_OPJ)
    design = project.schematic
    assert design is not None

    # Component count (Wave 2/A2: 173, not the pre-fix 174 phantom instance).
    assert len(design.components) == 173

    refdes_to_part = {component.reference: component.part for component in design.components}
    assert {ref: refdes_to_part[ref] for ref in ("U1", "U2", "U3", "U4", "U5", "U6")} == {
        "U1": "SN74LVC541A_5",
        "U2": "BD3570HFP_2",
        "U3": "ML610Q102",
        "U4": "BD8377FV-M_2",
        "U5": "BD63877EFV_5",
        "U6": "BD63876EFV_2",
    }

    # Title block value (a documentation-verifiable fact on every page).
    top = next(page for page in design.pages if page.name == "Top")
    assert top.title_block is not None
    assert top.title_block.title == "Rohm BD6387x Eval Board - Top Schematic"
    assert top.title_block.revision == "7"
    assert (top.title_block.sheet_number, top.title_block.sheet_total) == ("2", "5")

    # Page count with the Wave 4 hierarchy scope paths.
    assert len(design.pages) == 5
    scope_by_page = {page.name: page.scope_id.path for page in design.pages}
    assert scope_by_page == {
        "Top": ("Top",),
        "History": ("History",),
        "Controls": ("Top", "CONTROLS", "Controls"),
        "Clock-In": ("Top", "CLOCK-IN", "Clock-In"),
        "Parallel": ("Top", "PARALLEL", "Parallel"),
    }

    # Wave 3 native no-connects: 31 marker pins on the sidecar-free design.
    no_connect_pins = [
        pin for component in design.components for pin in component.pins if pin.no_connect
    ]
    assert len(no_connect_pins) == 31
    assert Counter(
        component.reference
        for component in design.components
        for pin in component.pins
        if pin.no_connect
    ) == {"U5": 7, "U6": 7, "E1": 5, "J8": 5, "U1": 3, "U2": 2, "J3": 1, "U4": 1}


# Diagnostic counts for the newly promoted fixtures. Maxome's former single
# ``dsn_cis`` warning was the VariantNames trailing-string mismatch; Wave 6 (G1)
# parses that trailing string into ``last_selected_group`` instead, so the count
# drops to zero. The BOMPartData stale-snapshot verdict (G5) is recorded as
# typed metadata, not a ``ctx`` warning, because a stale snapshot is the normal
# shape of that stream. RFSoC's single ``dsn_repeated_sheet`` warning is the
# Wave 4 (E4) block-multiplicity signal: DAC_ADC_CHANNEL x8 and IO_CHANNEL x6
# are repeated sheets pending occurrence identity. RFSoC's single
# ``dsn_unknown_stream`` is the Wave 5 (B3) inventory flag for its unparsed
# ``Symbols/BxoxoxkxMxarxkx`` BookMarkSymbol catalog entry.
NEW_FIXTURE_DIAGNOSTIC_COUNTS: tuple[tuple[str, Path, dict[str, int]], ...] = (
    ("maxome-mpcie-0p1", MAXOME_0P1_DSN, {}),
    ("maxome-mpcie-1p1", MAXOME_1P1_DSN, {}),
    ("rfsoc-frontend", RFSOC_DSN, {"dsn_repeated_sheet": 1, "dsn_unknown_stream": 1}),
)


@pytest.mark.parametrize(
    ("name", "dsn", "expected_counts"),
    NEW_FIXTURE_DIAGNOSTIC_COUNTS,
    ids=[row[0] for row in NEW_FIXTURE_DIAGNOSTIC_COUNTS],
)
def test_new_orcad_fixture_diagnostic_counts_are_locked(
    name: str, dsn: Path, expected_counts: dict[str, int]
) -> None:
    ctx = ParseContext()
    _ = parse_dsn(dsn, ctx)

    assert dict(Counter(issue.category for issue in ctx.issues)) == expected_counts, name


@pytest.mark.parametrize(
    ("dsn", "opj"),
    [
        (MAXOME_0P1_DSN, MAXOME_0P1_OPJ),
        (MAXOME_1P1_DSN, MAXOME_1P1_OPJ),
        (RFSOC_DSN, RFSOC_OPJ),
    ],
    ids=["maxome-mpcie-0p1", "maxome-mpcie-1p1", "rfsoc-frontend"],
)
def test_new_orcad_fixtures_parse_dsn_and_opj_without_crashing(dsn: Path, opj: Path) -> None:
    raw = parse_dsn(dsn)
    project = parse_opj_file(opj)

    assert raw.pages
    assert project.documents


def test_rohm_stepper_no_connect_markers_decode_clean_pin_order_and_names() -> None:
    raw = parse_dsn(ROHM_STEPPER_DSN)

    u5 = next(inst for page in raw.pages for inst in page.instances if inst.reference == "U5")
    marked = [pin for pin in u5.pin_connections if pin.has_no_connect_marker]

    # The int16 pin-order sign bit marks BD63877EFV's NC1..NC6 + Pad pins; the
    # public designator is the clean 1-based order, never the raw u16 (65530).
    assert [pin.pin_order for pin in marked] == [6, 8, 11, 21, 23, 28, 29]
    assert [pin.pin_number for pin in marked] == ["6", "8", "11", "21", "23", "28", "29"]
    assert all("65" not in pin.pin_number for pin in u5.pin_connections)
    names = raw.symbol_pin_names["BD63877EFV_5"]
    assert [names[pin.pin_order - 1] for pin in marked] == [
        "NC1",
        "NC2",
        "NC3",
        "NC4",
        "NC5",
        "NC6",
        "Pad",
    ]


def test_rohm_stepper_marked_pins_map_to_native_package_pins_without_bogus_diagnostic() -> None:
    ctx = ParseContext()
    design = dsn_to_design(parse_dsn(ROHM_STEPPER_DSN), name="rohm", ctx=ctx)

    u5 = next(component for component in design.components if component.reference == "U5")
    nc1 = next(pin for pin in u5.pins if pin.designator == "6")

    # order 6 → device pin index 5 → package pin "6" (the R1 mapping the old
    # unsigned read broke with a misleading "outside native package" warning).
    assert nc1.metadata["dsn_package_pin"] == "6"
    assert not any(
        issue.category == "dsn_package_evidence"
        and "outside native package device" in issue.message
        for issue in ctx.issues
    )


def test_launchxl_no_connect_marker_count_is_locked() -> None:
    raw = parse_dsn(CP_SMARTGARDEN_DSN)

    marked = sum(
        pin.has_no_connect_marker
        for page in raw.pages
        for inst in page.instances
        for pin in inst.pin_connections
    )

    assert marked == 83


def test_rohm_stepper_top_block_instances_carry_sheet_pins_and_bindings() -> None:
    raw = parse_dsn(ROHM_STEPPER_DSN)

    top = next(page for page in raw.pages if page.name == "Top")
    assert [block.reference for block in top.block_instances] == [
        "CONTROLS",
        "CLOCK-IN",
        "PARALLEL",
    ]

    controls = top.block_instances[0]
    # db_id joins the Hierarchy stream's child-schematic edge (Onboard Manual
    # Controls); the sheet pins are the block's ports with directions.
    assert controls.db_id == 8707
    assert [pin.name for pin in controls.sheet_pins] == [
        "CLK",
        "CW_CCW",
        "MODE_0",
        "MODE_1",
        "ENABLE",
        "GND_SIGNAL",
        "+3.3V_C",
        "BUFFER_OE",
    ]
    assert [pin.port_type_name for pin in controls.sheet_pins] == [
        "output",
        "output",
        "output",
        "output",
        "output",
        "power",
        "power",
        "output",
    ]
    # One T0x10 binding per sheet pin, each carrying a parent-page net id.
    assert len(controls.net_bindings) == len(controls.sheet_pins)
    assert [binding.pin_order for binding in controls.net_bindings] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert all(binding.net_id > 0 for binding in controls.net_bindings)


def test_rfsoc_block_instance_count_is_locked() -> None:
    raw = parse_dsn(RFSOC_DSN)

    assert sum(len(page.block_instances) for page in raw.pages) == 14


def test_stepper_ports_resolve_real_net_names_without_drops() -> None:
    ctx = ParseContext()
    raw = parse_dsn(ROHM_STEPPER_DSN, ctx)

    assert not any(issue.category == "dsn_port" for issue in ctx.issues)
    clock_in = next(page for page in raw.pages if page.name == "Clock-In")
    ports_by_net = {port.props.get("_net_name") for port in clock_in.ports}
    # Real net names, not the kilobyte binary junk the old has_name_indices=False
    # path produced; the symbol name is kept separately.
    assert {"CLK", "CW_CCW", "MODE_0", "MODE_1", "ENABLE", "GND_SIGNAL"} <= ports_by_net
    assert {port.name for port in clock_in.ports} == {"PORTLEFT-L", "PORTRIGHT-R"}


def test_stepper_and_power_ports_have_no_drop_diagnostics() -> None:
    for dsn in (ROHM_STEPPER_DSN, OPENCELLULAR_POWER_UNIT_DSN):
        ctx = ParseContext()
        parse_dsn(dsn, ctx)
        assert not any(issue.category == "dsn_port" for issue in ctx.issues), dsn


def test_dsn_port_source_carries_net_name_and_symbol_separately() -> None:
    # No committed fixture wires a port to a net (all ports float — S2), so
    # exercise the port at a wire location synthetically to lock that
    # DsnPort.name (resolver net-name evidence) is the net name while the
    # graphic symbol name is preserved in DsnPort.symbol.
    page = DsnSchematicPage(name="P")
    page.nets = [PageNetEntry(name="CLK", net_id=5)]
    page.wire_net_map = {(10, 20): {5}}
    page.ports = [
        GraphicInst(
            type_id=STRUCT_PORT,
            name="PORTLEFT-L",
            loc_x=10,
            loc_y=20,
            props={"_net_name": "CLK"},
        )
    ]
    source = dsn_to_source(ParsedDesign(pages=[page]), name="synthetic")

    port = source.pages[0].ports[0]
    assert port.name == "CLK"
    assert port.name_key == "clk"
    assert port.symbol == "PORTLEFT-L"


def test_breakout_component_values_decode_cp1252_micro_plusminus_degree() -> None:
    raw = parse_dsn(OPENCELLULAR_BREAKOUT_DSN)

    strings = set(raw.string_list)
    assert "0.1µF" in strings
    assert "±5%" in strings
    assert "-55°C ~ 125°C" in strings
    assert not any("�" in value for value in strings)


def _root_hierarchy(raw: ParsedDesign) -> DsnHierarchy:
    """Return the design's largest (root) structured hierarchy."""
    return max(raw.hierarchies, key=lambda hierarchy: len(hierarchy.entries))


# --- E1: structured Hierarchy-stream parser ---------------------------------

_HIERARCHY_FIXTURES = (
    ("stepper", ROHM_STEPPER_DSN, "Top Block Diagram", 176),
    ("rfsoc", RFSOC_DSN, "TOP", 489),
    ("launchxl", CP_SMARTGARDEN_DSN, "CC1310_LaunchPad", 160),
    ("maxome", MAXOME_1P1_DSN, "SCHEMATIC1", 111),
)


@pytest.mark.parametrize(
    ("dsn", "schematic_name", "entry_count"),
    [(row[1], row[2], row[3]) for row in _HIERARCHY_FIXTURES],
    ids=[row[0] for row in _HIERARCHY_FIXTURES],
)
def test_committed_hierarchy_streams_parse_structured_without_fallback(
    dsn: Path, schematic_name: str, entry_count: int
) -> None:
    ctx = ParseContext()
    raw = parse_dsn(dsn, ctx)

    root = _root_hierarchy(raw)
    assert root.schematic_name == schematic_name
    assert len(root.entries) == entry_count
    assert not any(hierarchy.fallback_used for hierarchy in raw.hierarchies)
    assert not any(issue.category == "dsn_hierarchy_fallback" for issue in ctx.issues)


@pytest.mark.parametrize(
    ("dsn"),
    [row[1] for row in _HIERARCHY_FIXTURES],
    ids=[row[0] for row in _HIERARCHY_FIXTURES],
)
def test_structured_hierarchy_occurrences_match_byte_scan(dsn: Path) -> None:
    """The structured tree yields the same placed-instance occurrence links as
    the legacy byte scan, so it can drive downstream resolution unchanged."""
    raw = parse_dsn(dsn)
    placed = {inst.db_id for page in raw.pages for inst in page.instances if inst.db_id}

    byte_scan = {(occ.occurrence_id, occ.instance_db_id) for occ in raw.hierarchy_occurrences}
    structured = {
        (entry.occurrence_id, entry.instance_db_id)
        for hierarchy in raw.hierarchies
        for entry in hierarchy.entries
        if entry.occurrence_id > 0
        and entry.occurrence_id != entry.instance_db_id
        and entry.instance_db_id in placed
    }
    assert structured == byte_scan


def test_stepper_structured_hierarchy_child_edges_and_no_refdes() -> None:
    raw = parse_dsn(ROHM_STEPPER_DSN)
    root = _root_hierarchy(raw)

    block_edges = {
        (entry.instance_db_id, entry.child_schematic)
        for entry in root.entries
        if entry.child_schematic
    }
    assert block_edges == {
        (8707, "Onboard Manual Controls"),
        (9372, "Clock-In Input"),
        (11604, "Parallel Input"),
    }
    # STEPPER instantiates every child once and stores no per-occurrence refdes.
    assert all(entry.refdes == "" for entry in root.entries)
    # Block entries carry occurrence-scoped net tables, pin occurrences, and
    # named port/global connections.
    controls = next(
        entry for entry in root.entries if entry.child_schematic == "Onboard Manual Controls"
    )
    assert len(controls.occurrence_nets) == 41
    assert controls.occurrence_nets[0] == DsnHierarchyNet(net_id=1175, name="N13369")
    assert len(controls.pin_occurrences) == 8
    assert ("LED_CLK") in {conn.name for conn in controls.named_connections}
    # Design-global net occurrences (top 0x44 list with negative ids).
    assert {net.name for net in root.global_nets} >= {"D-", "D+", "VCC_P"}
    assert all(net.net_id < 0 for net in root.global_nets)


def test_rfsoc_structured_hierarchy_per_occurrence_refdes_and_nets() -> None:
    raw = parse_dsn(RFSOC_DSN)
    root = _root_hierarchy(raw)

    # Instance 17838 is one resistor instantiated once per DAC_ADC_CHANNEL
    # occurrence; each occurrence carries its own refdes.
    refdes_for_17838 = {entry.refdes for entry in root.entries if entry.instance_db_id == 17838}
    assert refdes_for_17838 == {"R107", "R59", "R71", "R23", "R83", "R35", "R95", "R47"}
    assert sum(entry.instance_db_id == 17838 for entry in root.entries) == 8

    # Block occurrences carry occurrence-scoped net tables.
    blocks = [entry for entry in root.entries if entry.child_schematic]
    assert len(blocks) == 14
    assert all(entry.occurrence_nets for entry in blocks)


def test_rfsoc_repeated_sheet_block_multiplicities_are_locked() -> None:
    ctx = ParseContext()
    raw = parse_dsn(RFSOC_DSN, ctx)
    root = _root_hierarchy(raw)

    child_counts = Counter(entry.child_schematic for entry in root.entries if entry.child_schematic)
    assert child_counts == {"DAC_ADC_CHANNEL": 8, "IO_CHANNEL": 6}

    repeated = [issue for issue in ctx.issues if issue.category == "dsn_repeated_sheet"]
    assert len(repeated) == 1
    assert "DAC_ADC_CHANNEL x8" in repeated[0].message
    assert "IO_CHANNEL x6" in repeated[0].message


@pytest.mark.parametrize(
    "dsn",
    [ROHM_STEPPER_DSN, CP_SMARTGARDEN_DSN, MAXOME_1P1_DSN, OPENCELLULAR_POWER_UNIT_DSN],
    ids=["stepper", "launchxl", "maxome", "power"],
)
def test_singly_instantiated_and_flat_designs_do_not_warn_repeated_sheet(dsn: Path) -> None:
    ctx = ParseContext()
    parse_dsn(dsn, ctx)
    assert not any(issue.category == "dsn_repeated_sheet" for issue in ctx.issues)


def test_rfsoc_block_occurrence_id_ranges_contain_child_occurrences() -> None:
    """Lock the parent-assignment heuristic: every top-level occurrence at or
    above the first block's id falls inside exactly one block's contiguous
    ``[block_occ, next_block_occ)`` occurrence-id range (e.g. R23 occ 9685 in
    DAC_ADC_CHANNEL block occ 9606)."""
    raw = parse_dsn(RFSOC_DSN)
    root = _root_hierarchy(raw)

    block_occ_ids = sorted(
        entry.occurrence_id for entry in root.entries if entry.child_schematic and entry.depth == 0
    )
    assert len(block_occ_ids) == 14
    ranges = [
        (block_occ_ids[i], block_occ_ids[i + 1] if i + 1 < len(block_occ_ids) else None)
        for i in range(len(block_occ_ids))
    ]

    def owning_block(occurrence_id: int) -> int | None:
        for start, end in ranges:
            if start <= occurrence_id and (end is None or occurrence_id < end):
                return start
        return None

    first_block = block_occ_ids[0]
    members = [
        entry
        for entry in root.entries
        if not entry.child_schematic and entry.depth == 0 and entry.occurrence_id >= first_block
    ]
    assert members  # the fixture has occurrence members above the first block
    assert all(owning_block(entry.occurrence_id) is not None for entry in members)

    r23 = next(entry for entry in root.entries if entry.refdes == "R23")
    assert r23.occurrence_id == 9685
    assert owning_block(r23.occurrence_id) == 9606


def test_corrupt_hierarchy_stream_engages_byte_scan_fallback() -> None:
    # 9-byte prefix, then an oversized string length that overshoots EOF so the
    # structured parse aborts; the (occ=100, inst=200) + 0x42 triple is still
    # recoverable by the byte scan.
    data = (
        b"\xff" * 9
        + struct.pack("<H", 0xFFFF)
        + struct.pack("<I", 100)
        + struct.pack("<I", 200)
        + b"\x42"
        + b"\x00" * 16
    )
    ctx = ParseContext()

    hierarchy = parse_hierarchy_stream(
        data, {200}, stream_path="Views/T/Hierarchy/Hierarchy", ctx=ctx
    )

    assert hierarchy.fallback_used is True
    assert [(entry.occurrence_id, entry.instance_db_id) for entry in hierarchy.entries] == [
        (100, 200)
    ]
    fallback = [issue for issue in ctx.issues if issue.category == "dsn_hierarchy_fallback"]
    assert len(fallback) == 1
    assert "byte-scan fallback" in fallback[0].message


def test_hierarchy_entry_with_rewinding_end_offset_engages_fallback() -> None:
    # A corrupt long-form entry prefix whose end offset points BEFORE the entry
    # would rewind the entry loops and stall without raising; it must fail the
    # structured parse and land in the byte-scan fallback instead.
    entry = (
        b"\x42"
        + struct.pack("<I", 4)  # long prefix: end offset back inside the header
        + b"\x00" * 4
        + b"\x42"
        + struct.pack("<h", 0)  # short prefix, no name-value pairs
        + struct.pack("<II", 0x1111, 0x2222)
        + b"\x42"
        + b"\x00" * 16
    )
    data = (
        b"\x00" * 9
        + _hierarchy_string("ROOT")
        + b"\x00" * 7
        + struct.pack("<H", 0)  # global nets
        + struct.pack("<H", 0)  # net db-id mappings
        + struct.pack("<H", 0)  # 0x52 trailer
        + struct.pack("<I", 0)  # 0x5b trailer
        + struct.pack("<H", 1)  # declared entry count
        + entry
    )
    ctx = ParseContext()

    hierarchy = parse_hierarchy_stream(
        data, {0x2222}, stream_path="Views/T/Hierarchy/Hierarchy", ctx=ctx
    )

    assert hierarchy.fallback_used is True
    assert [(entry.occurrence_id, entry.instance_db_id) for entry in hierarchy.entries] == [
        (0x1111, 0x2222)
    ]
    assert any(issue.category == "dsn_hierarchy_fallback" for issue in ctx.issues)


def _hierarchy_string(text: str) -> bytes:
    raw = text.encode("ascii")
    return struct.pack("<H", len(raw)) + raw + b"\x00"


def _nested_hierarchy_entry(occurrence_id: int, instance_db_id: int, child: bytes) -> bytes:
    """One synthetic 0x42 entry; a non-empty *child* nests inside its region."""
    child_schematic = "CHILD_VIEW" if child else ""
    body = (
        _hierarchy_string(child_schematic)
        + _hierarchy_string(f"R{occurrence_id & 0xFF}")
        + b"\x00" * 4  # unknown_2
        + struct.pack("<H", 0)  # sub-entry count
    )
    if child:
        body += struct.pack("<H", 0)  # occurrence-net count
        body += struct.pack("<H", 0)  # trailer-id count
        body += child
    return (
        b"\x42"
        + struct.pack("<h", 0)  # short-form prefix, no name-value pairs
        + struct.pack("<II", occurrence_id, instance_db_id)
        + b"\x42"
        + struct.pack("<I", len(body))  # region byte offset
        + b"\x00" * 4  # region padding
        + body
        + struct.pack("<H", 0)  # entry trailer
    )


def _nested_hierarchy_stream(depth: int) -> bytes:
    """A synthetic Hierarchy stream whose single root entry nests *depth* deep.

    Occurrence ids keep a non-zero second byte so the multi-prefix attempts of
    ``read_prefix_chain`` fail their bounds check instead of mis-parsing.
    """
    entry = b""
    for level in reversed(range(depth)):
        entry = _nested_hierarchy_entry(0x100 + level, 0x200 + level, entry)
    return (
        b"\x00" * 9
        + _hierarchy_string("ROOT")
        + b"\x00" * 7
        + struct.pack("<H", 0)  # global nets
        + struct.pack("<H", 0)  # net db-id mappings
        + struct.pack("<H", 0)  # 0x52 trailer
        + struct.pack("<I", 0)  # 0x5b trailer
        + struct.pack("<H", 1)  # declared entry count
        + entry
    )


def test_synthetic_nested_hierarchy_parses_structured() -> None:
    ctx = ParseContext()

    hierarchy = parse_hierarchy_stream(
        _nested_hierarchy_stream(3), set(), stream_path="Views/T/Hierarchy/Hierarchy", ctx=ctx
    )

    assert hierarchy.fallback_used is False
    assert not ctx.issues
    assert [(entry.depth, entry.parent_index) for entry in hierarchy.entries] == [
        (0, None),
        (1, 0),
        (2, 1),
    ]
    assert [entry.occurrence_id for entry in hierarchy.entries] == [0x100, 0x101, 0x102]


def test_hierarchy_nesting_beyond_depth_guard_engages_fallback() -> None:
    """A malformed stream nesting past the depth guard must fall back to the
    byte scan instead of raising (or exhausting the Python stack)."""
    depth = MAX_ENTRY_DEPTH + 16
    placed = {0x200 + level for level in range(depth)}
    ctx = ParseContext()

    hierarchy = parse_hierarchy_stream(
        _nested_hierarchy_stream(depth),
        placed,
        stream_path="Views/T/Hierarchy/Hierarchy",
        ctx=ctx,
    )

    assert hierarchy.fallback_used is True
    # The byte scan still recovers every (occurrence, instance) link.
    assert len(hierarchy.entries) == depth
    fallback = [issue for issue in ctx.issues if issue.category == "dsn_hierarchy_fallback"]
    assert len(fallback) == 1
    assert "nesting" in fallback[0].message


def test_occurrence_conflict_is_detected_and_treated_unresolved() -> None:
    occurrences = [
        DsnHierarchyOccurrence(occurrence_id=1, instance_db_id=10),
        DsnHierarchyOccurrence(occurrence_id=2, instance_db_id=20),
        DsnHierarchyOccurrence(occurrence_id=1, instance_db_id=99),
    ]
    ctx = ParseContext()

    mapping = build_occurrence_to_instance(occurrences, ctx)

    # Occurrence 1 links to two instances, so it is dropped (unresolved).
    assert mapping == {2: 20}
    conflicts = [issue for issue in ctx.issues if issue.category == "dsn_occurrence_conflict"]
    assert len(conflicts) == 1
    assert "occurrence 1" in conflicts[0].message


def test_occurrence_map_keeps_non_conflicting_links() -> None:
    occurrences = [
        DsnHierarchyOccurrence(occurrence_id=1, instance_db_id=10),
        DsnHierarchyOccurrence(occurrence_id=1, instance_db_id=10),
        DsnHierarchyOccurrence(occurrence_id=2, instance_db_id=20),
    ]
    ctx = ParseContext()

    mapping = build_occurrence_to_instance(occurrences, ctx)

    assert mapping == {1: 10, 2: 20}
    assert not any(issue.category == "dsn_occurrence_conflict" for issue in ctx.issues)


# --- E2/E3/E5: public sheet-tree scoping, cross-sheet merge, occurrence identity ---

_FLAT_SCOPE_FIXTURES = (
    ("launchxl", CP_SMARTGARDEN_DSN, 4, 146, 113),
    ("maxome", MAXOME_1P1_DSN, 3, 111, 73),
    ("breakout", OPENCELLULAR_BREAKOUT_DSN, 3, 80, 75),
    ("power", OPENCELLULAR_POWER_UNIT_DSN, 27, 1099, 681),
    ("sync", OPENCELLULAR_SYNC_DSN, 14, 271, 174),
)


@pytest.mark.parametrize(
    ("dsn", "pages", "components", "nets"),
    [(row[1], row[2], row[3], row[4]) for row in _FLAT_SCOPE_FIXTURES],
    ids=[row[0] for row in _FLAT_SCOPE_FIXTURES],
)
def test_flat_designs_keep_single_segment_scope_and_stable_counts(
    dsn: Path, pages: int, components: int, nets: int
) -> None:
    """A flat design (single view, no blocks) keeps its pre-hierarchy behavior
    exactly: one-segment page scopes and unchanged component/net counts."""
    design = dsn_to_design(parse_dsn(dsn))

    assert len(design.pages) == pages
    assert len(design.components) == components
    assert len(design.nets) == nets
    for page in design.pages:
        assert page.scope_id.path == (page.name or "unnamed",)


def test_stepper_child_pages_scope_under_owning_block() -> None:
    """E2: singly-instantiated child pages get scope paths rooted at the block
    instance that owns them; root pages stay flat."""
    design = dsn_to_design(parse_dsn(ROHM_STEPPER_DSN))

    scope_by_page = {page.name: page.scope_id.path for page in design.pages}
    assert scope_by_page["Top"] == ("Top",)
    assert scope_by_page["History"] == ("History",)
    assert scope_by_page["Controls"] == ("Top", "CONTROLS", "Controls")
    assert scope_by_page["Clock-In"] == ("Top", "CLOCK-IN", "Clock-In")
    assert scope_by_page["Parallel"] == ("Top", "PARALLEL", "Parallel")


def test_stepper_connectivity_and_ids_unchanged_by_scoping() -> None:
    """E3/E5: sheet-pin merging reproduces the connectivity the pre-hierarchy
    global name-merge produced (net count/membership locked), and
    singly-instantiated components keep stable unscoped ids."""
    design = dsn_to_design(parse_dsn(ROHM_STEPPER_DSN))

    assert len(design.components) == 173
    assert len(design.nets) == 100

    nets_by_name: dict[str, list[Net]] = {}
    for net in design.nets:
        nets_by_name.setdefault(net.name, []).append(net)

    def pin_count(name: str) -> int:
        (net,) = nets_by_name[name]
        return len(net.pins)

    assert pin_count("CLK") == 4
    assert pin_count("GND_SIGNAL") == 106
    assert pin_count("+3.3V_C") == 32

    # Child-page components keep their stable, unscoped page id.
    assert any(
        component.id.startswith("dsn:component:page:Controls:") for component in design.components
    )


def test_rfsoc_repeated_channel_resistor_has_distinct_scoped_identity() -> None:
    """E5: one child-page resistor (db 17838) instantiated 8x becomes eight
    public components with distinct per-occurrence refdes, distinct scopes, and
    distinct scoped nets."""
    design = dsn_to_design(parse_dsn(RFSOC_DSN))

    channel_resistors = [
        component
        for component in design.components
        if any(":component:17838" in occ.source_id for occ in component.occurrences)
    ]
    assert len(channel_resistors) == 8
    assert {component.reference for component in channel_resistors} == {
        "R23",
        "R35",
        "R47",
        "R59",
        "R71",
        "R83",
        "R95",
        "R107",
    }
    for component in channel_resistors:
        (occurrence,) = component.occurrences
        assert occurrence.scope_id.path[0] == "DAC_ADC_TOP"
        assert occurrence.scope_id.path[-1] == "DAC_ADC"
        # Repeated-sheet ids are scope-qualified to stay distinct across channels.
        assert "DAC_ADC_TOP/" in component.id

    dac_p_net_ids = {
        pin.net.id
        for component in channel_resistors
        for pin in component.pins
        if pin.net is not None and pin.net.name.startswith("DAC_0") and pin.net.name.endswith("_P")
    }
    assert len(dac_p_net_ids) == 8


def test_rfsoc_channel_net_bridges_child_and_parent_scopes() -> None:
    """E3: each channel's sheet-pin net resolves through the T0x10 parent-wire
    binding to a distinct parent net spanning both scopes."""
    design = dsn_to_design(parse_dsn(RFSOC_DSN))

    def channel_resistor(reference: str, channel: str) -> Component:
        (component,) = [
            component
            for component in design.components
            if component.reference == reference
            and any(f"{channel}/DAC_ADC" in str(occ.scope_id) for occ in component.occurrences)
        ]
        return component

    ch0 = channel_resistor("R23", "CH0")
    ch0_net = next(
        pin.net for pin in ch0.pins if pin.net is not None and pin.net.name == "DAC_00_P"
    )
    ch0_scopes = {str(occ.scope_id) for occ in ch0_net.occurrences}
    assert "/DAC_ADC_TOP/CH0/DAC_ADC" in ch0_scopes
    assert "/DAC_ADC_TOP" in ch0_scopes

    ch7 = channel_resistor("R107", "CH7")
    ch7_net = next(
        pin.net for pin in ch7.pins if pin.net is not None and pin.net.name == "DAC_07_P"
    )
    assert ch0_net.id != ch7_net.id


def test_rfsoc_component_count_reflects_channel_multiplication() -> None:
    """E5: the total component count reflects the multiplied channels
    (DAC_ADC_CHANNEL x8 + IO_CHANNEL x6), not one collapsed instance each."""
    design = dsn_to_design(parse_dsn(RFSOC_DSN))

    assert len(design.components) == 470
    assert len(design.pages) == 21


def test_duplicate_root_page_names_collide_with_page_wording() -> None:
    """Two root-level pages sharing a name collide on the one-segment scope;
    the diagnostic names the page collision, not a block reference."""
    raw = ParsedDesign(
        pages=[
            RawSchematicPage(name="MAIN", view_name="ViewA"),
            RawSchematicPage(name="MAIN", view_name="ViewB"),
        ]
    )
    ctx = ParseContext()

    tree = build_sheet_tree(raw, ctx)

    assert [scope.scope_id.path for scope in tree.scopes] == [("MAIN",)]
    (issue,) = [i for i in ctx.issues if i.category == "dsn_scope_collision"]
    assert "duplicate top-level page name" in issue.message
    assert "block reference" not in issue.message


def test_duplicate_block_references_collide_with_block_wording() -> None:
    """Two blocks with the same reference on one page produce colliding child
    scopes; the diagnostic blames the non-unique block reference."""
    raw = ParsedDesign(
        pages=[
            RawSchematicPage(
                name="TOP",
                view_name="Root",
                block_instances=[
                    DsnBlockInstance(db_id=1, reference="CH1"),
                    DsnBlockInstance(db_id=2, reference="CH1"),
                ],
            ),
            RawSchematicPage(name="Child", view_name="CHILD_VIEW"),
        ],
        hierarchies=[
            DsnHierarchy(
                entries=[
                    DsnHierarchyEntry(
                        occurrence_id=10, instance_db_id=1, child_schematic="CHILD_VIEW"
                    ),
                    DsnHierarchyEntry(
                        occurrence_id=20, instance_db_id=2, child_schematic="CHILD_VIEW"
                    ),
                ]
            )
        ],
    )
    ctx = ParseContext()

    tree = build_sheet_tree(raw, ctx)

    assert [scope.scope_id.path for scope in tree.scopes] == [
        ("TOP",),
        ("TOP", "CH1", "Child"),
    ]
    (issue,) = [i for i in ctx.issues if i.category == "dsn_scope_collision"]
    assert "block reference is not unique on its page" in issue.message


def test_duplicate_block_reference_records_only_accepted_child_scope() -> None:
    """A colliding second block occurrence must not leave a dangling
    child_scope_id: only scopes actually added to the tree are recorded on the
    block link, so the resolver never dereferences a scope with no ScopePlan."""
    raw = ParsedDesign(
        pages=[
            RawSchematicPage(
                name="TOP",
                view_name="Root",
                block_instances=[
                    DsnBlockInstance(db_id=1, reference="CH1"),
                    DsnBlockInstance(db_id=2, reference="CH1"),
                ],
            ),
            RawSchematicPage(name="Child", view_name="CHILD_VIEW"),
        ],
        hierarchies=[
            DsnHierarchy(
                entries=[
                    DsnHierarchyEntry(
                        occurrence_id=10, instance_db_id=1, child_schematic="CHILD_VIEW"
                    ),
                    DsnHierarchyEntry(
                        occurrence_id=20, instance_db_id=2, child_schematic="CHILD_VIEW"
                    ),
                ]
            )
        ],
    )

    tree = build_sheet_tree(raw, ParseContext())

    accepted = {scope.scope_id for scope in tree.scopes}
    for link in tree.block_links:
        for child_scope in link.child_scope_ids:
            assert child_scope in accepted
    # The first CH1 link keeps its child; the colliding second one records none.
    assert sorted(len(link.child_scope_ids) for link in tree.block_links) == [0, 1]


def test_self_referencing_child_block_is_skipped_without_extra_scope() -> None:
    """A block that re-instantiates its own view is a direct cycle. It must be
    skipped at the point of reference — the current page's view is in scope for
    the cycle check — so no extra self-occurrence scope is emitted."""
    raw = ParsedDesign(
        pages=[
            RawSchematicPage(
                name="ROOT",
                view_name="Root",
                block_instances=[DsnBlockInstance(db_id=1, reference="B1")],
            ),
            RawSchematicPage(
                name="CHILD",
                view_name="Child",
                block_instances=[DsnBlockInstance(db_id=2, reference="B2")],
            ),
        ],
        hierarchies=[
            DsnHierarchy(
                entries=[
                    DsnHierarchyEntry(occurrence_id=10, instance_db_id=1, child_schematic="Child"),
                    DsnHierarchyEntry(occurrence_id=20, instance_db_id=2, child_schematic="Child"),
                ]
            )
        ],
    )
    ctx = ParseContext()

    tree = build_sheet_tree(raw, ctx)

    assert [scope.scope_id.path for scope in tree.scopes] == [
        ("ROOT",),
        ("ROOT", "B1", "CHILD"),
    ]
    assert any(issue.category == "dsn_block_cycle" for issue in ctx.issues)
