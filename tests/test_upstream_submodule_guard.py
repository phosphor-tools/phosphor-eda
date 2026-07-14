"""Tests for the content-based upstream-submodule initialization guard."""

from __future__ import annotations

from typing import TYPE_CHECKING

from conftest import _uninitialized_upstream_submodules

if TYPE_CHECKING:
    from pathlib import Path

_GITMODULES = """\
[submodule "tests/upstream/alpha"]
\tpath = tests/upstream/alpha
\turl = https://example.invalid/alpha.git
[submodule "tests/upstream/beta"]
\tpath = tests/upstream/beta
\turl = https://example.invalid/beta.git
"""


def _make_root(tmp_path: Path) -> Path:
    (tmp_path / ".gitmodules").write_text(_GITMODULES)
    return tmp_path


def test_populated_submodule_is_not_reported(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    alpha = root / "tests" / "upstream" / "alpha"
    alpha.mkdir(parents=True)
    (alpha / "README.md").write_text("content")
    beta = root / "tests" / "upstream" / "beta"
    beta.mkdir(parents=True)
    (beta / "board.kicad_pro").write_text("content")

    assert _uninitialized_upstream_submodules(root) == ()


def test_gitlink_pointer_alone_counts_as_uninitialized(tmp_path: Path) -> None:
    """A bare ``.git`` pointer must not be mistaken for checked-out content."""
    root = _make_root(tmp_path)
    alpha = root / "tests" / "upstream" / "alpha"
    alpha.mkdir(parents=True)
    (alpha / ".git").write_text("gitdir: /elsewhere")
    beta = root / "tests" / "upstream" / "beta"
    beta.mkdir(parents=True)
    (beta / "board.kicad_pro").write_text("content")

    assert _uninitialized_upstream_submodules(root) == ("tests/upstream/alpha",)


def test_missing_directory_counts_as_uninitialized(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    beta = root / "tests" / "upstream" / "beta"
    beta.mkdir(parents=True)
    (beta / "board.kicad_pro").write_text("content")

    assert _uninitialized_upstream_submodules(root) == ("tests/upstream/alpha",)
