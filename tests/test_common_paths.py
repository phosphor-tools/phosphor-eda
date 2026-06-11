"""Tests for the shared document-reference resolution helper."""

from phosphor_eda.formats.common.diagnostics import ParseContext
from phosphor_eda.formats.common.paths import resolve_document_reference

_KNOWN = ["SCH/Main.SchDoc", "SCH/Power.SchDoc", "SCH/MCU.SchDoc"]


def test_exact_match_after_separator_normalization():
    assert (
        resolve_document_reference(
            "SCH\\Main.SchDoc",
            referencing_dir=None,
            known_documents=_KNOWN,
        )
        == "SCH/Main.SchDoc"
    )


def test_resolves_relative_to_referencing_directory():
    # Child reference is bare but lives in the referencing document's directory.
    assert (
        resolve_document_reference(
            "Power.SchDoc",
            referencing_dir="SCH",
            known_documents=_KNOWN,
        )
        == "SCH/Power.SchDoc"
    )


def test_basename_fallback_when_directory_prefix_differs():
    # Sheet symbol references a bare filename; project lists it under SCH/.
    assert (
        resolve_document_reference(
            "MCU.SchDoc",
            referencing_dir=None,
            known_documents=_KNOWN,
        )
        == "SCH/MCU.SchDoc"
    )


def test_backslash_known_document_matches_exactly_and_keeps_original_key():
    # The known-documents index may spell paths with backslashes; the reference
    # must still exact-match, and the returned key must be the original spelling
    # so callers can index their known-documents mapping with it.
    known = ["SCH\\Main.SchDoc", "other/Main.SchDoc"]
    ctx = ParseContext()
    result = resolve_document_reference(
        "SCH/Main.SchDoc",
        referencing_dir=None,
        known_documents=known,
        ctx=ctx,
    )
    assert result == "SCH\\Main.SchDoc"
    assert ctx.issues == []


def test_backslash_known_document_matches_relative_to_referencing_directory():
    known = ["SCH\\Power.SchDoc", "other/Power.SchDoc"]
    ctx = ParseContext()
    result = resolve_document_reference(
        "Power.SchDoc",
        referencing_dir="SCH",
        known_documents=known,
        ctx=ctx,
    )
    assert result == "SCH\\Power.SchDoc"
    assert ctx.issues == []


def test_returns_none_when_no_candidate_matches():
    assert (
        resolve_document_reference(
            "Missing.SchDoc",
            referencing_dir=None,
            known_documents=_KNOWN,
        )
        is None
    )


def test_ambiguous_basename_warns_and_picks_first():
    known = ["a/Shared.SchDoc", "b/Shared.SchDoc"]
    ctx = ParseContext()
    result = resolve_document_reference(
        "Shared.SchDoc",
        referencing_dir=None,
        known_documents=known,
        ctx=ctx,
    )
    assert result == "a/Shared.SchDoc"
    assert len(ctx.issues) == 1
    issue = ctx.issues[0]
    assert issue.category == "ambiguous_document_reference"
    assert "a/Shared.SchDoc" in issue.message
    assert "b/Shared.SchDoc" in issue.message


def test_exact_match_preferred_over_ambiguous_basename():
    known = ["a/Shared.SchDoc", "b/Shared.SchDoc"]
    ctx = ParseContext()
    result = resolve_document_reference(
        "b/Shared.SchDoc",
        referencing_dir=None,
        known_documents=known,
        ctx=ctx,
    )
    assert result == "b/Shared.SchDoc"
    assert ctx.issues == []
