"""Typed parse errors for native Allegro source decoding."""

from __future__ import annotations

from typing import Literal

AllegroErrorCode = Literal[
    "header-layout-mismatch",
    "invalid-seek",
    "invalid-unit-divisor",
    "linked-list-cycle",
    "negative-read",
    "record-count-mismatch",
    "record-length-invalid",
    "record-value-out-of-range",
    "string-count-out-of-range",
    "truncated-read",
    "unknown-record-tag",
    "unresolved-reference",
    "unsupported-board-units",
    "unsupported-version",
    "unterminated-string",
]


class AllegroParseError(ValueError):
    """Raised when an Allegro binary file cannot be parsed as source data."""

    def __init__(
        self,
        message: str,
        *,
        code: AllegroErrorCode,
        offset: int | None = None,
        source_name: str | None = None,
    ) -> None:
        self.code = code
        self.offset = offset
        self.source_name = source_name

        parts: list[str] = []
        if source_name is not None:
            parts.append(source_name)
        if offset is not None:
            parts.append(f"0x{offset:X}")
        prefix = ":".join(parts)
        super().__init__(f"{prefix}: {message}" if prefix else message)


class AllegroUnsupportedVersionError(AllegroParseError):
    """Raised when a file's Allegro version magic is not supported."""
