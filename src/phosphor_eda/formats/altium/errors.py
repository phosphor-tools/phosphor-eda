"""Altium-specific parse exceptions.

Shared parse diagnostics (ParseContext/ParseIssue/ParseSeverity) live in
``phosphor_eda.formats.common.diagnostics``. This module holds only the exception types that
let Altium callers distinguish a malformed input file from a parser bug.
"""

from __future__ import annotations

from pathlib import Path

import olefile


class AltiumPcbParseError(ValueError):
    """Raised when an Altium .PcbDoc is malformed beyond recovery.

    Subclasses ValueError so existing ``except ValueError`` callers still
    catch it, while a dedicated type lets the CLI report a bad file rather
    than surfacing it as an internal bug.
    """


class AltiumFormatError(ValueError):
    """Raised when a file is not an Altium binary (OLE) document.

    Covers Altium's "Save As ASCII" export format, which stores the same
    records as pipe-delimited text and is not supported.
    """


def require_ole_file(path: str | Path) -> None:
    """Raise :class:`AltiumFormatError` when *path* is not an OLE container.

    Gives ASCII-format Altium exports a clean, named-file error instead of
    leaking an ``olefile`` traceback.
    """
    resolved = Path(path)
    if olefile.isOleFile(str(resolved)):
        return
    try:
        with resolved.open("rb") as fh:
            header = fh.read(1)
    except OSError:
        header = b""
    if header == b"|":
        msg = f"{resolved.name}: ASCII-format Altium files are not supported"
    else:
        msg = f"{resolved.name}: not an Altium binary (OLE) document"
    raise AltiumFormatError(msg)
