"""Shared parse diagnostics for all phosphor-eda parsers.

Provides ParseContext to accumulate non-fatal issues during parsing,
and require_enum for safe enum conversion with diagnostics.

This module is format-agnostic: Altium, KiCad, Eagle, and DSN parsers all
thread a single ParseContext so every degradation is observable. Format-specific
exception types live in each format package (e.g. ``altium/errors.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TypeVar, overload


class ParseSeverity(IntEnum):
    """Severity level for a parse issue."""

    WARNING = 0
    ERROR = 1


@dataclass
class ParseIssue:
    """A single parse issue (warning or error)."""

    severity: ParseSeverity
    category: str
    message: str
    record_index: int | None = None


E = TypeVar("E", bound=IntEnum)


def warn_optional(ctx: ParseContext | None, category: str, message: str) -> None:
    """Record a warning on *ctx* when one is present; a no-op otherwise.

    Lets parsers thread an optional ParseContext without repeating the
    ``if ctx is not None`` guard at every call site.
    """
    if ctx is not None:
        ctx.warn(category, message)


class ParseContext:
    """Accumulates warnings and errors during parsing.

    Allows parsers to continue past non-fatal issues while preserving
    a structured log of everything that went wrong.
    """

    def __init__(self) -> None:
        self.issues: list[ParseIssue] = []

    def warn(
        self,
        category: str,
        message: str,
        record_index: int | None = None,
    ) -> None:
        """Record a warning-level issue."""
        self.issues.append(
            ParseIssue(
                severity=ParseSeverity.WARNING,
                category=category,
                message=message,
                record_index=record_index,
            )
        )

    def error(
        self,
        category: str,
        message: str,
        record_index: int | None = None,
    ) -> None:
        """Record an error-level issue."""
        self.issues.append(
            ParseIssue(
                severity=ParseSeverity.ERROR,
                category=category,
                message=message,
                record_index=record_index,
            )
        )

    @overload
    def require_enum(
        self,
        value: int,
        enum_cls: type[E],
        field_name: str,
        record_index: int | None = None,
        *,
        default: E,
    ) -> E: ...

    @overload
    def require_enum(
        self,
        value: int,
        enum_cls: type[E],
        field_name: str,
        record_index: int | None = None,
        default: None = None,
    ) -> E | None: ...

    def require_enum(
        self,
        value: int,
        enum_cls: type[E],
        field_name: str,
        record_index: int | None = None,
        default: E | None = None,
    ) -> E | None:
        """Try to convert an integer to an enum member.

        Returns the enum member on success. If the value is not a valid
        member, records a warning and returns *default*.
        """
        try:
            return enum_cls(value)
        except ValueError:
            self.warn(
                category="unknown_enum",
                message=f"Unknown {field_name} value {value} for {enum_cls.__name__}",
                record_index=record_index,
            )
            return default
