"""KiCad-specific parse exceptions.

Shared parse diagnostics (ParseContext/ParseIssue/ParseSeverity) live in
``phosphor_eda.formats.common.diagnostics``. This module holds only the
exception types that let KiCad callers distinguish a malformed input file
from a parser bug, plus the S-expression read helper that attaches the
offending file path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sexpdata

if TYPE_CHECKING:
    from pathlib import Path

    from phosphor_eda.formats.kicad.sexp import SExpNode

# sexpdata raises these bare, path-less errors on truncated or unbalanced input.
_SEXP_ERRORS = (
    sexpdata.ExpectClosingBracket,
    sexpdata.ExpectNothing,
    sexpdata.ExpectSExp,
)

# Diagnostic category for a per-item PCB degradation (skipped malformed node),
# shared by the board- and footprint-level parsers.
MALFORMED_PCB_ITEM = "kicad_malformed_pcb_item"


class KiCadParseError(ValueError):
    """Raised when a KiCad file is malformed beyond recovery.

    Subclasses ValueError so existing ``except ValueError`` callers still
    catch it, while a dedicated type lets the CLI report a bad file rather
    than surfacing it as an internal bug. The message always names the file.
    """


def load_kicad_sexp(path: Path) -> SExpNode:
    """Read and parse a KiCad S-expression file.

    Wraps ``sexpdata``'s path-less parse errors in :class:`KiCadParseError`
    with the file name so a truncated vendor file degrades to a named,
    catchable error instead of leaking a third-party traceback.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        msg = f"{path.name}: not a UTF-8 KiCad file: {exc}"
        raise KiCadParseError(msg) from exc
    try:
        return sexpdata.loads(text)
    except _SEXP_ERRORS as exc:
        msg = f"{path.name}: malformed KiCad S-expression: {exc}"
        raise KiCadParseError(msg) from exc
