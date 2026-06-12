"""OrCAD DSN-specific parse exceptions.

Shared parse diagnostics (ParseContext/ParseIssue) live in
``phosphor_eda.formats.common.diagnostics``. This module holds the exception
types that let DSN callers distinguish a malformed input file from a parser
bug.
"""

from __future__ import annotations


class DsnFormatError(ValueError):
    """Raised when a DSN stream is structurally invalid at a known offset.

    Subclasses ValueError so the CLI error boundary reports a bad file
    instead of a traceback. ``offset`` and ``type_id`` locate the structure
    whose declared layout contradicts the stream.
    """

    def __init__(self, message: str, *, offset: int, type_id: int) -> None:
        super().__init__(message)
        self.offset = offset
        self.type_id = type_id
