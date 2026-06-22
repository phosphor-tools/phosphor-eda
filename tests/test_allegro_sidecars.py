from __future__ import annotations

from io import BytesIO
from pathlib import Path
from shutil import copytree
from typing import TYPE_CHECKING
from zipfile import ZIP_DEFLATED, ZipFile

from phosphor_eda.domain.project import DocumentKind
from phosphor_eda.formats.allegro import parse_allegro_pcb
from phosphor_eda.formats.allegro import sidecars as allegro_sidecars
from phosphor_eda.formats.allegro.project_loader import load_allegro_pcb_project
from phosphor_eda.formats.allegro.sidecars import (
    parse_allegro_package_symbol_sidecar,
    parse_allegro_padstack_sidecar,
)
from phosphor_eda.query.project_loader import load_project

if TYPE_CHECKING:
    from pytest import MonkeyPatch

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BREAKOUT_FIXTURE_ROOT = FIXTURES / "orcad" / "opencellular-breakout"
BREAKOUT_BOARD_RELATIVE = Path(
    "allegro/OpenCellular/electronics/breakout/board/OC_CONNECT-1_BREAKOUT_LIFE-3.brd"
)


def test_allegro_pad_sidecar_enriches_matching_padstack_without_changing_board_data(
    tmp_path: Path,
) -> None:
    """Proves local pad sidecars are evidence for exact padstack-name matches.

    The sidecar proves padstack provenance and matching geometry. It cannot
    prove unmatched package-symbol geometry, which must remain evidence only.
    """
    board_path = _copy_breakout_tree(tmp_path)
    _write_pad_sidecar(
        board_path.parent / "R20_67.pad",
        name="R20_67",
        units="Millimeters",
        width=0.1999996,
        height=0.675005,
        shape="Rectangle",
    )

    board = parse_allegro_pcb(board_path)

    assert board.metadata.properties["allegro_sidecar_count"] == "1"
    matching_pads = [
        pad for pad in board.pads if pad.metadata.properties["native_padstack_name"] == "R20_67"
    ]
    assert matching_pads
    assert all(
        pad.metadata.properties["sidecar_padstack_path"] == str(board_path.parent / "R20_67.pad")
        for pad in matching_pads
    )
    assert {pad.metadata.properties["sidecar_padstack_match"] for pad in matching_pads} == {
        "geometry_confirmed"
    }
    assert {(pad.width, pad.height) for pad in matching_pads} == {(0.19999959999999997, 0.675005)}


def test_allegro_unmatched_pad_sidecar_remains_project_evidence(tmp_path: Path) -> None:
    board_path = _copy_breakout_tree(tmp_path)
    _write_pad_sidecar(
        board_path.parent / "unmatched.pad",
        name="SIDE_ONLY",
        units="Millimeters",
        width=9.0,
        height=9.0,
        shape="Circle",
    )

    board = parse_allegro_pcb(board_path)

    assert board.metadata.properties["allegro_sidecar_count"] == "1"
    assert board.metadata.properties["allegro_unmatched_padstack_sidecars"] == "SIDE_ONLY"
    assert all("sidecar_padstack_path" not in pad.metadata.properties for pad in board.pads)


def test_allegro_matching_pad_sidecar_with_different_geometry_is_identity_only(
    tmp_path: Path,
) -> None:
    board_path = _copy_breakout_tree(tmp_path)
    _write_pad_sidecar(
        board_path.parent / "R20_67.pad",
        name="R20_67",
        units="Millimeters",
        width=9.0,
        height=9.0,
        shape="Circle",
    )

    board = parse_allegro_pcb(board_path)

    matching_pads = [
        pad for pad in board.pads if pad.metadata.properties["native_padstack_name"] == "R20_67"
    ]
    assert matching_pads
    assert {pad.metadata.properties["sidecar_padstack_match"] for pad in matching_pads} == {
        "identity_only"
    }
    assert {(pad.width, pad.height) for pad in matching_pads} == {(0.19999959999999997, 0.675005)}


def test_allegro_package_symbol_sidecar_enriches_matching_footprint(tmp_path: Path) -> None:
    board_path = _copy_breakout_tree(tmp_path)
    symbol_path = board_path.parent / "R0603.psm"
    symbol_path.write_bytes(bytes.fromhex("0205140003000000"))

    board = parse_allegro_pcb(board_path)

    matching_footprints = [
        footprint for footprint in board.footprints if footprint.footprint_lib == "R0603"
    ]
    assert matching_footprints
    assert all(
        footprint.metadata.properties["sidecar_package_symbol_path"] == str(symbol_path)
        for footprint in matching_footprints
    )
    assert {
        footprint.metadata.properties["sidecar_package_symbol_kind"]
        for footprint in matching_footprints
    } == {"psm"}


def test_allegro_unmatched_package_symbol_sidecar_remains_project_evidence(
    tmp_path: Path,
) -> None:
    board_path = _copy_breakout_tree(tmp_path)
    (board_path.parent / "SIDE_ONLY.dra").write_bytes(bytes.fromhex("0205140003000000"))

    board = parse_allegro_pcb(board_path)

    assert board.metadata.properties["allegro_sidecar_count"] == "1"
    assert board.metadata.properties["allegro_unmatched_package_symbol_sidecars"] == "SIDE_ONLY"
    assert all(
        "sidecar_package_symbol_path" not in footprint.metadata.properties
        for footprint in board.footprints
    )


def test_allegro_corrupt_pad_sidecar_preserves_parse_diagnostic(tmp_path: Path) -> None:
    board_path = _copy_breakout_tree(tmp_path)
    (board_path.parent / "R20_67.pad").write_bytes(b"not an allegro padstack sidecar")

    board = parse_allegro_pcb(board_path)

    assert board.metadata.properties["allegro_sidecar_count"] == "0"
    assert board.metadata.properties["allegro_sidecar_diagnostic_count"] == "1"
    assert (
        board.metadata.properties["allegro_sidecar_diagnostic_codes"]
        == "padstack-sidecar-missing-zip"
    )
    assert all("sidecar_padstack_path" not in pad.metadata.properties for pad in board.pads)


def test_allegro_malformed_pad_json_takes_diagnostic_path(tmp_path: Path) -> None:
    scalar_path = tmp_path / "scalar.pad"
    array_path = tmp_path / "array.pad"
    _write_raw_pad_sidecar(scalar_path, '"not an object"')
    _write_raw_pad_sidecar(array_path, '{"text": [], "padDesign": []}')

    parsed_scalar, scalar_diagnostics = parse_allegro_padstack_sidecar(scalar_path)
    parsed_array, array_diagnostics = parse_allegro_padstack_sidecar(array_path)

    assert parsed_scalar is None
    assert parsed_array is None
    assert [diagnostic.code for diagnostic in scalar_diagnostics] == [
        "padstack-sidecar-parse-failed"
    ]
    assert [diagnostic.code for diagnostic in array_diagnostics] == [
        "padstack-sidecar-parse-failed"
    ]


def test_allegro_boolean_pad_geometry_takes_diagnostic_path(tmp_path: Path) -> None:
    pad_path = tmp_path / "boolean-geometry.pad"
    _write_pad_sidecar_payload(
        pad_path,
        name="BOOLEAN_GEOMETRY",
        width="true",
        height="true",
    )

    parsed_pad, diagnostics = parse_allegro_padstack_sidecar(pad_path)

    assert parsed_pad is None
    assert [diagnostic.code for diagnostic in diagnostics] == ["padstack-sidecar-parse-failed"]


def test_allegro_unknown_pad_sidecar_units_take_diagnostic_path(tmp_path: Path) -> None:
    pad_path = tmp_path / "unknown-units.pad"
    _write_pad_sidecar_payload(
        pad_path,
        name="UNKNOWN_UNITS",
        width="1.0",
        height="1.0",
        units="Furlongs",
    )

    parsed_pad, diagnostics = parse_allegro_padstack_sidecar(pad_path)

    assert parsed_pad is None
    assert [diagnostic.code for diagnostic in diagnostics] == ["padstack-sidecar-parse-failed"]
    assert "unsupported Allegro padstack sidecar unit" in diagnostics[0].message


def test_allegro_oversized_pad_zip_entry_takes_diagnostic_path(tmp_path: Path) -> None:
    pad_path = tmp_path / "oversized.pad"
    _write_raw_pad_sidecar(pad_path, "x" * 2_000_000)

    parsed_pad, diagnostics = parse_allegro_padstack_sidecar(pad_path)

    assert parsed_pad is None
    assert [diagnostic.code for diagnostic in diagnostics] == ["padstack-sidecar-parse-failed"]
    assert "exceeds maximum" in diagnostics[0].message


def test_allegro_oversized_pad_sidecar_file_takes_diagnostic_path(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    pad_path = tmp_path / "oversized-file.pad"
    pad_path.write_bytes(b"not a padstack")
    monkeypatch.setattr(allegro_sidecars, "_MAX_PADSTACK_SIDECAR_BYTES", 10)

    parsed_pad, diagnostics = parse_allegro_padstack_sidecar(pad_path)

    assert parsed_pad is None
    assert [diagnostic.code for diagnostic in diagnostics] == ["padstack-sidecar-parse-failed"]
    assert "exceeds maximum" in diagnostics[0].message


def test_allegro_missing_sidecar_files_become_parse_diagnostics(tmp_path: Path) -> None:
    missing_pad = tmp_path / "missing.pad"
    missing_psm = tmp_path / "missing.psm"

    parsed_pad, pad_diagnostics = parse_allegro_padstack_sidecar(missing_pad)
    parsed_symbol, symbol_diagnostics = parse_allegro_package_symbol_sidecar(missing_psm)

    assert parsed_pad is None
    assert parsed_symbol is None
    assert [diagnostic.code for diagnostic in pad_diagnostics] == ["padstack-sidecar-parse-failed"]
    assert [diagnostic.code for diagnostic in symbol_diagnostics] == [
        "package-symbol-sidecar-read-failed"
    ]


def test_allegro_duplicate_sidecar_names_are_preserved_as_diagnostics(
    tmp_path: Path,
) -> None:
    board_path = _copy_breakout_tree(tmp_path)
    _write_pad_sidecar(
        board_path.parent / "R20_67.pad",
        name="R20_67",
        units="Millimeters",
        width=0.1999996,
        height=0.675005,
        shape="Rectangle",
    )
    library_dir = board_path.parent / "library"
    library_dir.mkdir()
    _write_pad_sidecar(
        library_dir / "R20_67.pad",
        name="R20_67",
        units="Millimeters",
        width=0.1999996,
        height=0.675005,
        shape="Rectangle",
    )

    board = parse_allegro_pcb(board_path)

    assert board.metadata.properties["allegro_padstack_sidecar_count"] == "2"
    assert (
        "padstack-sidecar-duplicate-name"
        in board.metadata.properties["allegro_sidecar_diagnostic_codes"]
    )
    assert all("sidecar_padstack_path" not in pad.metadata.properties for pad in board.pads)


def test_allegro_duplicate_package_symbol_names_are_preserved_as_diagnostics(
    tmp_path: Path,
) -> None:
    board_path = _copy_breakout_tree(tmp_path)
    (board_path.parent / "R0603.psm").write_bytes(bytes.fromhex("0205140003000000"))
    library_dir = board_path.parent / "library"
    library_dir.mkdir()
    (library_dir / "R0603.dra").write_bytes(bytes.fromhex("0205140003000000"))

    board = parse_allegro_pcb(board_path)

    assert board.metadata.properties["allegro_package_symbol_sidecar_count"] == "2"
    assert (
        "package-symbol-sidecar-duplicate-name"
        in board.metadata.properties["allegro_sidecar_diagnostic_codes"]
    )
    assert all(
        "sidecar_package_symbol_path" not in footprint.metadata.properties
        for footprint in board.footprints
    )


def test_load_allegro_project_preserves_sidecar_source_paths(tmp_path: Path) -> None:
    board_path = _copy_breakout_tree(tmp_path)
    pad_path = board_path.parent / "R20_67.pad"
    symbol_path = board_path.parent / "R0603.psm"
    corrupt_path = board_path.parent / "corrupt.pad"
    _write_pad_sidecar(
        pad_path,
        name="R20_67",
        units="Millimeters",
        width=0.1999996,
        height=0.675005,
        shape="Rectangle",
    )
    symbol_path.write_bytes(bytes.fromhex("0205140003000000"))
    corrupt_path.write_bytes(b"not an allegro padstack sidecar")

    project = load_allegro_pcb_project(board_path)

    assert str(board_path) in project.metadata.source_paths
    assert str(pad_path) in project.metadata.source_paths
    assert str(symbol_path) in project.metadata.source_paths
    assert str(corrupt_path) in project.metadata.source_paths


def test_orcad_manifest_classifies_allegro_symbol_sidecars_as_libraries(
    tmp_path: Path,
) -> None:
    opj = tmp_path / "symbols.opj"
    opj.write_text(
        """(ExpressProject "Symbols"
  (ProjectVersion "19981106")
  (ProjectType "PCB")
  (Folder "Library"
    (File "r0603.psm" (Type "Package Symbol"))
    (File "r0603.dra" (Type "Drawing"))
    (File "r20_67.pad" (Type "Padstack"))))
""",
        encoding="utf-8",
    )

    project = load_project(opj)

    assert {
        doc.path: doc.kind
        for doc in project.documents
        if doc.path in {"r0603.psm", "r0603.dra", "r20_67.pad"}
    } == {
        "r0603.psm": DocumentKind.LIBRARY,
        "r0603.dra": DocumentKind.LIBRARY,
        "r20_67.pad": DocumentKind.LIBRARY,
    }


def _write_pad_sidecar(
    path: Path,
    *,
    name: str,
    units: str,
    width: float,
    height: float,
    shape: str,
) -> None:
    _write_pad_sidecar_payload(
        path,
        name=name,
        width=str(width),
        height=str(height),
        shape=shape,
        units=units,
    )


def _write_pad_sidecar_payload(
    path: Path,
    *,
    name: str,
    width: str,
    height: str,
    shape: str = "Rectangle",
    units: str = "Millimeters",
) -> None:
    payload = f"""{{
"text" : {{
    "comps" : [
    {{
        "name" : "{name}",
        "type" : "Padstack",
        "units" : "{units}",
        "accuracy" : 6,
    }}
    ]
}},
"padDesign" : {{
    "pad" : [
    {{
        "width" : {width},
        "height" : {height},
        "shape" : "{shape}",
        "class_name" : "ETCH",
        "subclass_name" : "BEGIN LAYER",
    }}
    ]
}}
}}"""
    _write_raw_pad_sidecar(path, payload)


def _write_raw_pad_sidecar(path: Path, payload: str) -> None:
    buffer = BytesIO()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("Users/test/AppData/Local/Temp/#Taaaaaa00001.tmp", payload)
    path.write_bytes(b"\x00" * 32 + buffer.getvalue())


def _copy_breakout_tree(tmp_path: Path) -> Path:
    copied_root = tmp_path / "opencellular-breakout"
    copytree(BREAKOUT_FIXTURE_ROOT, copied_root)
    return copied_root / BREAKOUT_BOARD_RELATIVE
