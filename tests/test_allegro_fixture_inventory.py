from __future__ import annotations

import json
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fixture_paths import FIXTURES, UPSTREAM_FIXTURES

if TYPE_CHECKING:
    from collections.abc import Callable

CORPUS_ROOT = Path(os.environ.get("PHOSPHOR_EDA_CORPUS_ROOT", "__external_corpus_missing__"))
EXTERNAL_ALLEGRO_CORPUS = CORPUS_ROOT / "designs/allegro"
EXTERNAL_KICAD_ALLEGRO_FIXTURES = CORPUS_ROOT / "kicad/qa/data/pcbnew/plugins/allegro"

VERSION_STRING_OFFSET = 0xF8
V18_VERSION_STRING_OFFSET = 0x124
VERSION_STRING_BYTES = 60

MAGIC_TO_VERSION_FAMILY = {
    0x00130000: "V_160",
    0x00130400: "V_162",
    0x00130C00: "V_164",
    0x00131000: "V_165",
    0x00131500: "V_166",
    0x00140400: "V_172",
    0x00140500: "V_172",
    0x00140600: "V_172",
    0x00140700: "V_172",
    0x00140900: "V_174",
    0x00140E00: "V_174",
    0x00141500: "V_175",
    0x00150000: "V_180",
}


@dataclass(frozen=True)
class _AllegroHeader:
    magic: int
    version_family: str
    writer_string: str


@dataclass(frozen=True)
class _AllegroFixtureExpectation:
    name: str
    root: Path
    board_file: str
    file_size: int
    magic: int
    version_family: str
    writer_tokens: tuple[str, ...]
    provenance_files: tuple[str, ...]
    project_sidecars: tuple[str, ...]
    packaged_netlists: tuple[str, ...]
    ipc356_files: tuple[str, ...]
    manufacturing_files: tuple[str, ...]
    report_files: tuple[str, ...]


@dataclass(frozen=True)
class _AllegroHeaderFixtureExpectation:
    name: str
    root: Path
    header_file: str
    file_size: int
    magic: int
    version_family: str
    writer_tokens: tuple[str, ...]
    provenance_files: tuple[str, ...]


@dataclass(frozen=True)
class _CorpusBoardHeader:
    path: str
    header: _AllegroHeader


ALLEGRO_FIXTURE_EXPECTATIONS = (
    _AllegroFixtureExpectation(
        name="opencellular-breakout",
        root=UPSTREAM_FIXTURES / "opencellular/electronics/breakout",
        board_file=("board/OC_CONNECT-1_BREAKOUT_LIFE-3.brd"),
        file_size=4_702_680,
        magic=0x00131504,
        version_family="V_166",
        writer_tokens=("allv16-62/8/2", "allv16-610/12"),
        provenance_files=(),
        project_sidecars=(
            "schematic/dsn/OC_CONNECT_1_BRKOUT_BRD.DSN",
            "schematic/dsn/OC_CONNECT_1_BRKOUT_BRD.opj",
        ),
        packaged_netlists=(
            "schematic/Netlist/pstchip.dat",
            "schematic/Netlist/pstxnet.dat",
            "schematic/Netlist/pstxprt.dat",
        ),
        ipc356_files=("gerbers/OC_CONNECT-1_BREAKOUT_LIFE-3.ipc",),
        manufacturing_files=(),
        report_files=(),
    ),
    _AllegroFixtureExpectation(
        name="opencellular-sync",
        root=UPSTREAM_FIXTURES / "opencellular/electronics/sync",
        board_file=("board/Fb_Connect1_SYNC_Life-3.brd"),
        file_size=5_327_796,
        magic=0x00131504,
        version_family="V_166",
        writer_tokens=("allv16-62/8/2", "batv16-62/8/2"),
        provenance_files=(),
        project_sidecars=(
            "schematics/dsn/FB_CONNECT1_SYNC_LIFE-3_V1P1.DSN",
            "schematics/dsn/FB_CONNECT1_SYNC_LIFE-3_V1P1.opj",
        ),
        packaged_netlists=(
            "schematics/Netlist/pstchip.dat",
            "schematics/Netlist/pstxnet.dat",
            "schematics/Netlist/pstxprt.dat",
        ),
        ipc356_files=("gerbers/Fb_Connect1_SYNC_Life-3.ipc",),
        manufacturing_files=(),
        report_files=(),
    ),
    _AllegroFixtureExpectation(
        name="cp-smartgarden-launchxl-cc1310",
        root=UPSTREAM_FIXTURES / "cp-smartgarden",
        board_file="Document/Hardware/mcu/swrc319/Cadence/Allegro/LAUNCHXL-CC1310.brd",
        file_size=3_591_692,
        magic=0x00131503,
        version_family="V_166",
        writer_tokens=("allv16-611/4/", "batv16-610/28"),
        provenance_files=("README.md",),
        project_sidecars=(
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/art/art_param.txt",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/art/art_param.txt,1",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/art/nc_param.txt",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/art/nc_param.txt,1",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/devices.dml",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/eco.txt",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/eco.txt,1",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/master.tag",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/nc_param.txt",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/pdf_out_config.txt",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/pdf_out_config.txt,1",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/pxlBA.txt",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/signoise.run/cases.cfg",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/signoise.run/cases.cfg,1",
            "Document/Hardware/mcu/swrc319/Cadence/LAUNCHXL-CC1310.DSN",
            "Document/Hardware/mcu/swrc319/Cadence/LAUNCHXL-CC1310_0.DBK",
            "Document/Hardware/mcu/swrc319/Cadence/devices.dml",
            "Document/Hardware/mcu/swrc319/Cadence/launchxl-cc1310.opj",
            "Document/Hardware/mcu/swrc319/Cadence/pxlBA.txt",
            "Document/Hardware/mcu/swrc319/Cadence/signoise.run/cases.cfg",
            "Document/Hardware/mcu/swrc319/Cadence/signoise.run/cases.cfg,1",
        ),
        packaged_netlists=(
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/pstchip.dat",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/pstchip.dat,1",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/pstchip.dat,2",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/pstchip.dat,3",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/pstxnet.dat",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/pstxnet.dat,1",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/pstxnet.dat,2",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/pstxnet.dat,3",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/pstxprt.dat",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/pstxprt.dat,1",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/pstxprt.dat,2",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/pstxprt.dat,3",
        ),
        ipc356_files=(),
        manufacturing_files=(),
        report_files=(
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/LAUNCHXL-CC1310.SAV",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/allegro.jrl",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/allegro.jrl,1",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/allegro.jrl",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/allegro.jrl,1",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/aperture.log",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/batch_drc.log",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/batch_drc.log,1",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/cmpshape.log",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/dbdoctor.log",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/dbdoctor.log,1",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/extract.log",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/extract.log,1",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/ncdrill.log,1",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/nclegend.log",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/nclegend.log,1",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/netrev.lst",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/pdf_out.log",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/pdf_out.log,1",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/plctxt.log",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/plctxt.log,1",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/refresh.log",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/signoise.log",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/log/signoise.log,1",
            "Document/Hardware/mcu/swrc319/Cadence/Allegro/netlist.log",
            "Document/Hardware/mcu/swrc319/Cadence/LAUNCHXL-CC1310.DRC",
            "Document/Hardware/mcu/swrc319/Cadence/LAUNCHXL-CC1310.PRP",
            "Document/Hardware/mcu/swrc319/Cadence/netlist.log",
        ),
    ),
    _AllegroFixtureExpectation(
        name="rohm-stepper-driver-ctrl",
        root=UPSTREAM_FIXTURES / "rohm-stepper-driver",
        board_file="Design Files for Rev 1.0/STEPPER EVAL BRD - PCB Board File - Rev 1.0.brd",
        file_size=1_209_464,
        magic=0x00131003,
        version_family="V_165",
        writer_tokens=("allv16-54/29/", "batv16-54/23"),
        provenance_files=("README.md",),
        project_sidecars=(
            "Design Files for Rev 1.0/STEPPER EVAL BRD - DESIGN File - Rev 1.0.DSN",
            "Design Files for Rev 1.0/STEPPER EVAL BRD - SCHEMATIC File - Rev 1.0.opj",
            "Design Files for Rev 1.0/STEPPER.DSN",
            "Design Files for Rev 1.0/STEPPER.opj",
        ),
        packaged_netlists=(),
        ipc356_files=(),
        manufacturing_files=(
            "Design Files for Rev 1.0/Gerbers & Panel CAD/DRILL.DRL",
            "Design Files for Rev 1.0/Gerbers & Panel CAD/L1.GBR",
            "Design Files for Rev 1.0/Gerbers & Panel CAD/L2.GBR",
            "Design Files for Rev 1.0/Gerbers & Panel CAD/L3.GBR",
            "Design Files for Rev 1.0/Gerbers & Panel CAD/L31.GBR",
            "Design Files for Rev 1.0/Gerbers & Panel CAD/L32.GBR",
            "Design Files for Rev 1.0/Gerbers & Panel CAD/L4.GBR",
            "Design Files for Rev 1.0/Gerbers & Panel CAD/L41.GBR",
            "Design Files for Rev 1.0/Gerbers & Panel CAD/L60.GBR",
        ),
        report_files=("Design Files for Rev 1.0/Gerbers & Panel CAD/REPORT.DOC",),
    ),
)

ALLEGRO_HEADER_FIXTURE_EXPECTATIONS = (
    _AllegroHeaderFixtureExpectation(
        name="cutiepi-v18-header",
        root=FIXTURES / "orcad/cutiepi-v18-header",
        header_file="kicad-allegro/boards/CutiePi_V2_3_dbd18/header.bin",
        file_size=1_280,
        magic=0x00150000,
        version_family="V_180",
        writer_tokens=("dbd414729/29", "allv16-65/21"),
        provenance_files=(
            "LICENSE.3-CLAUSE-BSD",
            "README.md",
            "UPSTREAM-KICAD-REGISTRY.json",
            "UPSTREAM-README.md",
        ),
    ),
)


def _read_allegro_header(path: Path) -> _AllegroHeader:
    data = path.read_bytes()
    minimum_magic_size = struct.calcsize("<I")
    assert len(data) >= minimum_magic_size, f"{path} is too small for an Allegro header"

    magic = struct.unpack_from("<I", data)[0]
    masked_magic = magic & 0xFFFFFF00
    version_family = MAGIC_TO_VERSION_FAMILY.get(masked_magic)
    if version_family is None and masked_magic <= 0x00120000:
        version_family = "PRE_V16"
    if version_family is None:
        version_family = f"UNKNOWN_0x{masked_magic:08X}"

    writer_offset = (
        V18_VERSION_STRING_OFFSET if version_family == "V_180" else VERSION_STRING_OFFSET
    )
    minimum_writer_size = writer_offset + VERSION_STRING_BYTES
    assert len(data) >= minimum_writer_size, f"{path} is too small for an Allegro header"

    writer_string = data[writer_offset : writer_offset + VERSION_STRING_BYTES].split(b"\0", 1)[0]
    return _AllegroHeader(
        magic=magic,
        version_family=version_family,
        writer_string=writer_string.decode("latin1"),
    )


def _relative_files(root: Path, predicate: Callable[[Path], bool]) -> tuple[str, ...]:
    return tuple(
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*"))
        if path.is_file() and predicate(path)
    )


def _is_project_sidecar(path: Path) -> bool:
    return path.suffix.lower() in {
        ".opj",
        ".dsn",
        ".dbk",
        ".dml",
        ".txt",
        ".tag",
        ".cfg",
    } or path.name.lower().endswith((".txt,1", ".cfg,1"))


def _is_packaged_netlist(path: Path) -> bool:
    return path.name.startswith(("pstxnet.dat", "pstxprt.dat", "pstchip.dat"))


def _is_ipc356(path: Path) -> bool:
    return path.suffix.lower() == ".ipc"


def _suffix_without_allegro_revision(path: Path) -> str:
    name, separator, revision = path.name.rpartition(",")
    if separator and revision.isdigit():
        return Path(name).suffix
    return path.suffix


def _is_manufacturing_output(path: Path) -> bool:
    return _suffix_without_allegro_revision(path).upper() in {".GBR", ".DRL"}


def _is_report(path: Path) -> bool:
    return _suffix_without_allegro_revision(path).lower() in {
        ".log",
        ".lst",
        ".jrl",
        ".drc",
        ".prp",
        ".sav",
        ".doc",
    }


def _corpus_board_headers(root: Path) -> tuple[_CorpusBoardHeader, ...]:
    return tuple(
        _CorpusBoardHeader(
            path=path.relative_to(root).as_posix(), header=_read_allegro_header(path)
        )
        for path in sorted(root.rglob("*.brd"))
    )


@pytest.mark.parametrize(
    "expected",
    ALLEGRO_FIXTURE_EXPECTATIONS,
    ids=[expected.name for expected in ALLEGRO_FIXTURE_EXPECTATIONS],
)
def test_committed_allegro_board_fixture_inventory_is_locked(
    expected: _AllegroFixtureExpectation,
) -> None:
    board_path = expected.root / expected.board_file

    assert board_path.exists()
    assert board_path.stat().st_size == expected.file_size

    header = _read_allegro_header(board_path)
    assert header.magic == expected.magic
    assert header.version_family == expected.version_family
    assert all(token in header.writer_string for token in expected.writer_tokens)

    assert expected.board_file in _relative_files(
        expected.root, lambda path: path.suffix.lower() == ".brd"
    )
    assert set(expected.provenance_files) <= set(
        _relative_files(
            expected.root,
            lambda path: path.relative_to(expected.root).as_posix() in expected.provenance_files,
        )
    )
    assert set(expected.project_sidecars) <= set(
        _relative_files(expected.root, _is_project_sidecar)
    )
    assert set(expected.packaged_netlists) <= set(
        _relative_files(expected.root, _is_packaged_netlist)
    )
    assert set(expected.ipc356_files) <= set(_relative_files(expected.root, _is_ipc356))
    assert set(expected.manufacturing_files) <= set(
        _relative_files(expected.root, _is_manufacturing_output)
    )
    assert set(expected.report_files) <= set(_relative_files(expected.root, _is_report))


@pytest.mark.parametrize(
    "relative_path",
    (
        "adafruit-rgb-lcd-shield/license.txt",
        "debugotron/LICENSE",
        "jetson-orin/LICENSE",
        "opencellular/LICENSE-HARDWARE",
        "pi-mx8/LICENSE",
        "qfsae-pcb/LICENSE",
        "rp2040-minimal/.metadata/LICENSE",
        "sparkfun-bme280/LICENSE.md",
    ),
)
def test_licensed_upstream_fixture_preserves_license_file(relative_path: str) -> None:
    assert (UPSTREAM_FIXTURES / relative_path).is_file()


@pytest.mark.parametrize(
    "expected",
    ALLEGRO_HEADER_FIXTURE_EXPECTATIONS,
    ids=[expected.name for expected in ALLEGRO_HEADER_FIXTURE_EXPECTATIONS],
)
def test_committed_allegro_header_fixture_inventory_is_locked(
    expected: _AllegroHeaderFixtureExpectation,
) -> None:
    header_path = expected.root / expected.header_file

    assert header_path.exists()
    assert header_path.stat().st_size == expected.file_size

    header = _read_allegro_header(header_path)
    assert header.magic == expected.magic
    assert header.version_family == expected.version_family
    assert all(token in header.writer_string for token in expected.writer_tokens)

    assert _relative_files(expected.root, lambda path: path.suffix.lower() == ".brd") == ()
    assert (
        _relative_files(
            expected.root,
            lambda path: path.relative_to(expected.root).as_posix() in expected.provenance_files,
        )
        == expected.provenance_files
    )


def test_promoted_v18_header_fixture_preserves_kicad_registry_evidence() -> None:
    root = FIXTURES / "orcad/cutiepi-v18-header"
    header = root / "kicad-allegro/boards/CutiePi_V2_3_dbd18/header.bin"
    registry = root / "UPSTREAM-KICAD-REGISTRY.json"

    assert _read_allegro_header(header).version_family == "V_180"
    registry_data = json.loads(registry.read_text(encoding="utf-8"))
    registry_entry = registry_data["CutiePi_V2_3_dbd18"]
    assert registry_entry["formatVersion"] == "18.0"
    assert registry_entry["license"] == "BSD-3-Clause"
    assert registry_entry["url"] == "https://github.com/cutiepi-io/cutiepi-board"
    assert registry_entry["header"]["skip"] is True


def test_promoted_header_fixtures_do_not_contain_workstation_paths() -> None:
    suspicious_tokens = (
        b"/Users/",
        b"/home/",
        b"C:/Users/",
        b"C:" + b"\\" + b"Users" + b"\\",
        b"\\" + b"Users" + b"\\",
        b"AppData",
    )

    for expected in ALLEGRO_HEADER_FIXTURE_EXPECTATIONS:
        data = (expected.root / expected.header_file).read_bytes()
        assert not any(token in data for token in suspicious_tokens), expected.name
