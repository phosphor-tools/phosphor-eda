"""Tests for KiCad project assembly degradation on malformed sibling files.

``load_kicad_project`` must never let one corrupt sibling (.kicad_pro,
.kicad_pcb, .kicad_dru) abort the whole project load; each failure degrades
to an unparsed document while the rest of the project still assembles.
"""

from pathlib import Path

from phosphor_eda.domain.project import Project, ProjectDocument
from phosphor_eda.formats.kicad.project import load_kicad_project


def _document(project: Project, native_kind: str) -> ProjectDocument:
    return next(doc for doc in project.documents if doc.native_kind == native_kind)


def test_valid_pro_only_project_parses(tmp_path: Path) -> None:
    pro = tmp_path / "proj.kicad_pro"
    pro.write_text('{"net_settings": {}}', encoding="utf-8")

    project = load_kicad_project(pro)

    assert _document(project, ".kicad_pro").parsed


def test_absent_kicad_pro_document_is_not_parsed(tmp_path: Path) -> None:
    sch = tmp_path / "proj.kicad_sch"
    sch.write_text("(kicad_sch (version 20230121))", encoding="utf-8")

    project = load_kicad_project(tmp_path / "proj.kicad_pro")

    pro_doc = _document(project, ".kicad_pro")
    assert not pro_doc.exists
    assert not pro_doc.parsed


def test_corrupt_kicad_pro_degrades(tmp_path: Path) -> None:
    pro = tmp_path / "proj.kicad_pro"
    pro.write_text("{ this is not valid json", encoding="utf-8")

    project = load_kicad_project(pro)

    pro_doc = _document(project, ".kicad_pro")
    assert pro_doc.exists
    assert not pro_doc.parsed
    assert "invalid JSON" in pro_doc.metadata["parse_error"]
    assert project.net_classes == []
    assert project.variants == []
    assert project.parameters == {}


def test_corrupt_kicad_pcb_degrades(tmp_path: Path) -> None:
    pro = tmp_path / "proj.kicad_pro"
    pro.write_text("{}", encoding="utf-8")
    pcb = tmp_path / "proj.kicad_pcb"
    pcb.write_text('(kicad_pcb (layers (0 "F.Cu" signal)', encoding="utf-8")

    project = load_kicad_project(pro)

    pcb_doc = _document(project, ".kicad_pcb")
    assert pcb_doc.exists
    assert not pcb_doc.parsed
    assert "proj.kicad_pcb" in pcb_doc.metadata["parse_error"]
    assert project.boards == []
    # The healthy .kicad_pro still parsed alongside the broken board.
    assert _document(project, ".kicad_pro").parsed


def test_corrupt_kicad_dru_degrades(tmp_path: Path) -> None:
    pro = tmp_path / "proj.kicad_pro"
    pro.write_text("{}", encoding="utf-8")
    dru = tmp_path / "proj.kicad_dru"
    dru.write_text(
        '(rule "bad"\n  (constraint clearance (min ..mm))\n)\n',
        encoding="utf-8",
    )

    project = load_kicad_project(pro)

    dru_doc = _document(project, ".kicad_dru")
    assert dru_doc.exists
    assert not dru_doc.parsed
    assert project.design_rules == []
