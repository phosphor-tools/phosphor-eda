from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from phosphor_eda.cli import main
from phosphor_eda.domain.project import DocumentKind
from phosphor_eda.query.convert import load_project
from phosphor_eda.query.sql import load_database

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DSN_FILE = FIXTURES / "dsn/raspberry-pi-pico/RPI-PICO-R3-PUBLIC.DSN"
JETSON_ORIN_PRO = FIXTURES / "kicad-jetson-orin" / "jetson-orin-baseboard.kicad_pro"
JETSON_ORIN_PCB = FIXTURES / "kicad-jetson-orin" / "jetson-orin-baseboard.kicad_pcb"
PIMX8_PRJPCB = FIXTURES / "altium/pi-mx8/PiMX8MP_r0.3_release.PrjPcb"


def _write_opj(path: Path, dsn_path: Path = DSN_FILE) -> Path:
    relative_dsn = (
        dsn_path.relative_to(path.parent).as_posix()
        if dsn_path.is_relative_to(path.parent)
        else str(dsn_path)
    )
    path.write_text(
        f"""(ExpressProject "Pico Project"
  (ProjectVersion "19981106")
  (ProjectType "PCB")
  (Folder "Design Resources"
    (Folder "Library"
      (File ".\\library\\discrete.olb"
        (Type "Schematic Library")))
    (File "{relative_dsn}"
      (Type "Schematic Design"))
    (DRC_Check_Unconnected_Nets "TRUE")
    (DRC_Check_Floating_Pins "FALSE")
    ("Allegro Netlist Output Board File" "allegro\\pico.brd")
    (BOM_Report_File ".\\pico.bom"))
  (Folder "Outputs"
    (File "..\\netlist\\pstxnet.dat"
      (Type "Report")
      (DisplayName "pstxnet.dat"))))
""",
        encoding="utf-8",
    )
    return path


def test_load_project_rejects_direct_document_entrypoints() -> None:
    for path in (DSN_FILE, JETSON_ORIN_PCB):
        with pytest.raises(ValueError, match="project file required"):
            load_project(path)


def test_load_project_accepts_project_entrypoints(tmp_path: Path) -> None:
    opj = _write_opj(tmp_path / "pico.opj")

    assert load_project(JETSON_ORIN_PRO).metadata.format == "kicad"
    assert load_project(PIMX8_PRJPCB).metadata.format == "altium"
    assert load_project(opj).metadata.format == "orcad"


def test_opj_project_manifest_and_schematic_loading(tmp_path: Path) -> None:
    opj = _write_opj(tmp_path / "pico.opj")

    project = load_project(opj)

    assert project.name == "Pico Project"
    assert project.metadata.format == "orcad"
    assert project.metadata.format_version == "19981106"
    assert project.parameters["ProjectType"] == "PCB"
    assert project.parameters["DRC_Check_Unconnected_Nets"] == "TRUE"
    assert project.parameters["Allegro Netlist Output Board File"] == "allegro\\pico.brd"
    assert project.schematic is not None
    assert any(doc.kind is DocumentKind.SCHEMATIC and doc.parsed for doc in project.documents)
    assert any(
        doc.kind is DocumentKind.LIBRARY and doc.path == ".\\library\\discrete.olb"
        for doc in project.documents
    )
    assert any(
        doc.kind is DocumentKind.PCB and doc.path == "allegro\\pico.brd" and not doc.parsed
        for doc in project.documents
    )
    assert any(
        doc.kind is DocumentKind.REPORT
        and doc.path == "..\\netlist\\pstxnet.dat"
        and doc.description == "pstxnet.dat"
        for doc in project.documents
    )


def test_opj_project_preserves_manifest_when_dsn_parse_fails(tmp_path: Path) -> None:
    dsn = tmp_path / "broken.dsn"
    dsn.write_text("not a valid dsn", encoding="utf-8")
    opj = _write_opj(tmp_path / "broken.opj", dsn_path=dsn)

    project = load_project(opj)

    assert project.schematic is None
    schematic_docs = [doc for doc in project.documents if doc.kind is DocumentKind.SCHEMATIC]
    assert len(schematic_docs) == 1
    assert schematic_docs[0].exists
    assert not schematic_docs[0].parsed
    assert schematic_docs[0].metadata["parse_error"]


def test_sql_exposes_project_manifest_and_parameters(tmp_path: Path) -> None:
    opj = _write_opj(tmp_path / "pico.opj")
    con = load_database(load_project(opj))

    docs = con.execute(
        """
        SELECT path, kind, native_kind, description, parsed
        FROM project_documents
        ORDER BY ord
        """
    ).fetchall()
    params = dict(con.execute("SELECT key, value FROM project_parameters").fetchall())

    assert (str(DSN_FILE), "schematic", "Schematic Design", "", True) in docs
    assert ("allegro\\pico.brd", "pcb", "Allegro Netlist Output Board File", "", False) in docs
    assert params["ProjectType"] == "PCB"
    assert params["DRC_Check_Floating_Pins"] == "FALSE"


def test_cli_requires_project_for_project_backed_commands() -> None:
    runner = CliRunner()

    for args in (
        ["list", "components"],
        ["show", "component", "U1"],
        ["trace", "U1", "U2"],
        ["sql", "SELECT 1"],
        ["pcb", "render"],
    ):
        result = runner.invoke(main, args)
        assert result.exit_code != 0, args
        assert "-P/--project" in result.output


def test_cli_project_option_drives_schematic_commands(tmp_path: Path) -> None:
    opj = _write_opj(tmp_path / "pico.opj")
    runner = CliRunner()

    result = runner.invoke(main, ["-P", str(opj), "list", "components"])

    assert result.exit_code == 0, result.output
    assert "REF" in result.output
    assert "PART" in result.output


def test_cli_convert_command_is_removed() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["convert", str(DSN_FILE), "out.txt"])

    assert result.exit_code != 0
    assert "No such command" in result.output


def test_cli_force_single_sheet_option_is_removed() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["--force-single-sheet", "list", "components"])

    assert result.exit_code != 0
    assert "No such option" in result.output
