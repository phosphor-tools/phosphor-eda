"""Recursive KiCad schematic sheet loading."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import phosphor_eda.formats.kicad.sexp as sexp
from phosphor_eda.domain.schematic import ScopeId
from phosphor_eda.formats.common.diagnostics import warn_optional
from phosphor_eda.formats.kicad.errors import KiCadParseError, load_kicad_sexp
from phosphor_eda.formats.kicad.lib_symbols import LibPins, LibPowerKinds, parse_lib_symbols
from phosphor_eda.formats.kicad.source import KiCadSheetInstance
from phosphor_eda.formats.kicad.title_block import parse_kicad_title_block

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.formats.common.diagnostics import ParseContext
    from phosphor_eda.formats.kicad.sexp import SExpNode


class SheetWarningReporter(Protocol):
    def warn(self, message: str) -> None: ...


class NullSheetWarningReporter:
    """Drops sheet-loading warnings. Used when no ParseContext is supplied."""

    def warn(self, message: str) -> None:
        return


class ParseContextSheetWarningReporter:
    """Routes sheet-loading warnings onto a shared ParseContext."""

    def __init__(self, ctx: ParseContext) -> None:
        self._ctx = ctx

    def warn(self, message: str) -> None:
        self._ctx.warn("missing_sheet", message)


@dataclass(slots=True)
class LoadedSheet:
    instance: KiCadSheetInstance
    source_path: Path
    data: SExpNode


@dataclass(slots=True)
class LoadedSheetTree:
    root_scope_id: ScopeId
    sheets: list[LoadedSheet]
    sheet_instances: list[KiCadSheetInstance]
    lib_pins: LibPins
    lib_descs: dict[str, str]
    lib_power_kinds: LibPowerKinds


def load_sheet_tree(
    path: Path,
    name: str = "",
    warning_reporter: SheetWarningReporter | None = None,
) -> LoadedSheetTree:
    """Load a root KiCad sheet and all reachable child sheets.

    Missing or cyclic child-sheet references are reported through
    *warning_reporter*. When none is supplied warnings are dropped rather than
    printed, keeping library code free of stdout/stderr noise; callers that
    want them surfaced pass a ``ParseContextSheetWarningReporter``.
    """
    loaded_sheets: list[LoadedSheet] = []
    sheet_instances: list[KiCadSheetInstance] = []
    lib_pins: LibPins = {}
    lib_descs: dict[str, str] = {}
    lib_power_kinds: LibPowerKinds = {}
    root_scope = ScopeId(path=())
    reporter = warning_reporter or NullSheetWarningReporter()

    _load_sheet_tree(
        path=path,
        sheet_name=name or path.stem,
        scope_id=root_scope,
        parent_scope_id=None,
        sheet_symbol_id="",
        loaded_sheets=loaded_sheets,
        sheet_instances=sheet_instances,
        lib_pins=lib_pins,
        lib_descs=lib_descs,
        lib_power_kinds=lib_power_kinds,
        ancestor_files=(path.resolve(),),
        warning_reporter=reporter,
    )

    return LoadedSheetTree(
        root_scope_id=root_scope,
        sheets=loaded_sheets,
        sheet_instances=sheet_instances,
        lib_pins=lib_pins,
        lib_descs=lib_descs,
        lib_power_kinds=lib_power_kinds,
    )


def parse_sheet_info(sheet_node: SExpNode, ctx: ParseContext | None = None) -> tuple[str, str]:
    """Extract name and filename from a sheet S-expression node.

    KiCad 6 wrote the properties as ``"Sheet name"`` / ``"Sheet file"``
    (with a space); KiCad 7+ writes ``"Sheetname"`` / ``"Sheetfile"``. A
    property node missing its name atom is malformed; it is skipped and a
    diagnostic recorded on *ctx* rather than raising a bare IndexError.
    """
    sheet_name = ""
    sheet_file = ""
    for sub in sheet_node[1:]:
        if sexp.tag(sub) == "property" and isinstance(sub, list):
            if len(sub) < 2:
                warn_optional(
                    ctx,
                    "kicad_malformed_node",
                    "Skipped malformed sheet property node missing its name",
                )
                continue
            prop_name = str(sub[1])
            prop_val = str(sub[2]) if len(sub) > 2 else ""
            if prop_name in ("Sheetname", "Sheet name"):
                sheet_name = prop_val
            elif prop_name in ("Sheetfile", "Sheet file"):
                sheet_file = prop_val
    return sheet_name, sheet_file


def _load_sheet_tree(
    *,
    path: Path,
    sheet_name: str,
    scope_id: ScopeId,
    parent_scope_id: ScopeId | None,
    sheet_symbol_id: str,
    loaded_sheets: list[LoadedSheet],
    sheet_instances: list[KiCadSheetInstance],
    lib_pins: LibPins,
    lib_descs: dict[str, str],
    lib_power_kinds: LibPowerKinds,
    ancestor_files: tuple[Path, ...],
    warning_reporter: SheetWarningReporter,
) -> None:
    data = _load_kicad_file(path)
    instance_id = _source_id(scope_id, "sheet_instance", "root" if not scope_id.path else "self")
    instance = KiCadSheetInstance(
        id=instance_id,
        scope_id=scope_id,
        sheet_name=sheet_name,
        source_file=str(path),
        parent_scope_id=parent_scope_id,
        sheet_symbol_id=sheet_symbol_id,
        title_block=parse_kicad_title_block(data[1:]),
    )
    sheet_instances.append(instance)
    loaded_sheets.append(LoadedSheet(instance=instance, source_path=path, data=data))

    lib_syms_node = sexp.find(data[1:], "lib_symbols")
    if lib_syms_node is not None:
        sheet_lib_pins, sheet_lib_descs, sheet_lib_power_kinds = parse_lib_symbols(lib_syms_node)
        for key, value in sheet_lib_pins.items():
            if key not in lib_pins:
                lib_pins[key] = value
        for key, value in sheet_lib_descs.items():
            if key not in lib_descs:
                lib_descs[key] = value
        for key, value in sheet_lib_power_kinds.items():
            if key not in lib_power_kinds:
                lib_power_kinds[key] = value

    for sheet_index, sheet_node in enumerate(sexp.find_all(data[1:], "sheet")):
        sheet_uuid = _node_value(sheet_node[1:], "uuid") or f"sheet-{sheet_index}"
        child_name, child_file = parse_sheet_info(sheet_node)
        if not child_file:
            warning_reporter.warn(
                f"Warning: sheet symbol {child_name or sheet_uuid!r} in {path.name} "
                "has no sheet-file property; child sheet not loaded",
            )
            continue
        child_scope = ScopeId(path=(*scope_id.path, sheet_uuid))
        child_path = path.parent / child_file.replace("\\", "/")
        symbol_id = _source_id(scope_id, "sheet_symbol", sheet_uuid)
        if not child_path.exists():
            warning_reporter.warn(
                f"Warning: child sheet not found: {child_file} (resolved to {child_path})",
            )
            continue
        child_resolved_path = child_path.resolve()
        if child_resolved_path in ancestor_files:
            warning_reporter.warn(
                f"Warning: child sheet cycle skipped: {child_file} (resolved to {child_path})",
            )
            continue
        try:
            _load_sheet_tree(
                path=child_path,
                sheet_name=child_name or child_path.stem,
                scope_id=child_scope,
                parent_scope_id=scope_id,
                sheet_symbol_id=symbol_id,
                loaded_sheets=loaded_sheets,
                sheet_instances=sheet_instances,
                lib_pins=lib_pins,
                lib_descs=lib_descs,
                lib_power_kinds=lib_power_kinds,
                ancestor_files=(*ancestor_files, child_resolved_path),
                warning_reporter=warning_reporter,
            )
        except KiCadParseError as exc:
            # A malformed child sheet degrades like a missing one: skip it and
            # keep loading the rest of the tree rather than aborting the parse.
            warning_reporter.warn(f"Warning: child sheet not parsed: {child_file} ({exc})")


def _load_kicad_file(path: Path) -> SExpNode:
    return load_kicad_sexp(path)


def _source_id(scope_id: ScopeId, kind: str, source_key: str) -> str:
    scope_key = "root" if not scope_id.path else "/".join(scope_id.path)
    return f"{scope_key}:{kind}:{source_key}"


def _node_value(items: SExpNode, tag_name: str) -> str:
    node = sexp.find(items, tag_name)
    return sexp.val(node) if node is not None else ""
