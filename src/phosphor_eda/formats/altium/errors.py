"""Altium-specific parse exceptions.

Shared parse diagnostics (ParseContext/ParseIssue/ParseSeverity) live in
``phosphor_eda.formats.common.diagnostics``. This module holds only the exception types that
let Altium callers distinguish a malformed input file from a parser bug.
"""

from __future__ import annotations


class AltiumPcbParseError(ValueError):
    """Raised when an Altium .PcbDoc is malformed beyond recovery.

    Subclasses ValueError so existing ``except ValueError`` callers still
    catch it, while a dedicated type lets the CLI report a bad file rather
    than surfacing it as an internal bug.
    """
