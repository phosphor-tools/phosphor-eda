"""Shared document-reference resolution for hierarchical schematics.

A sheet (Altium sheet symbol, KiCad sub-sheet) references a child document by a
relative path. How the reference is spelled varies: a bare filename, a
subdirectory-prefixed path, Windows or POSIX separators. The project's
known-documents index, meanwhile, may spell the same document differently (e.g.
Altium lists ``SCH/Foo.SchDoc`` while the sheet symbol references ``Foo.SchDoc``).

``resolve_document_reference`` reconciles a reference against the known-document
keys using a fixed precedence so hierarchy detection matches regardless of how
either side spells the path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from phosphor_eda.formats.common.diagnostics import ParseContext


def normalize_path_key(reference: str) -> str:
    """Normalize separators on a document reference (``\\`` -> ``/``)."""
    return reference.replace("\\", "/")


def basename_key(reference: str) -> str:
    """Return the bare filename of a normalized document reference."""
    return normalize_path_key(reference).rsplit("/", 1)[-1]


def resolve_document_reference(
    reference: str,
    *,
    referencing_dir: str | None,
    known_documents: Iterable[str],
    ctx: ParseContext | None = None,
) -> str | None:
    """Resolve a child-document reference to a canonical known-document key.

    Resolution order:

    1. normalize separators (``\\`` -> ``/``);
    2. exact match against the known-documents index;
    3. resolution relative to the referencing document's directory;
    4. basename fallback. If the basename matches more than one known document,
       warn on *ctx* (naming the candidates) and pick the deterministic first.

    Returns the matched known-document key, or ``None`` when nothing matches.
    """
    known = list(known_documents)
    known_set = set(known)
    normalized = normalize_path_key(reference)

    if normalized in known_set:
        return normalized

    if referencing_dir:
        relative = normalize_path_key(f"{normalize_path_key(referencing_dir)}/{normalized}")
        if relative in known_set:
            return relative

    target_basename = basename_key(normalized)
    basename_matches = [doc for doc in known if basename_key(doc) == target_basename]
    if not basename_matches:
        return None
    if len(basename_matches) > 1 and ctx is not None:
        candidates = ", ".join(basename_matches)
        message = (
            f"Document reference {reference!r} matches multiple documents by"
            f" basename ({candidates}); using {basename_matches[0]!r}."
        )
        ctx.warn("ambiguous_document_reference", message)
    return basename_matches[0]
