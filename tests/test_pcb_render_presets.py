"""Guard tests for the bundled render preset registry.

Every bundled preset must be registered in ``BUNDLED_PRESETS``, load
cleanly, and be documented in skill.md. A new preset file that skips any
of those steps fails here.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import pytest

from phosphor_eda.render.settings import (
    BUNDLED_PRESETS,
    LayerSelectionRule,
    load_bundled_render_settings,
)

SKILL_MD = Path(__file__).resolve().parents[1] / "src" / "phosphor_eda" / "skill.md"


def _bundled_preset_files() -> set[str]:
    package = files("phosphor_eda.render.profiles")
    return {
        resource.name.removesuffix(".json")
        for resource in package.iterdir()
        if resource.name.endswith(".json")
    }


def test_registry_matches_bundled_preset_files() -> None:
    assert _bundled_preset_files() == set(BUNDLED_PRESETS)


def test_registry_names_the_expected_presets() -> None:
    assert BUNDLED_PRESETS == ("realistic", "design", "print", "documentation")


@pytest.mark.parametrize("name", BUNDLED_PRESETS)
def test_every_preset_loads_and_parses(name: str) -> None:
    settings = load_bundled_render_settings(name)
    assert settings.render_mode in ("eda", "realistic")
    assert settings.source.layers, f"preset {name} selects no source layers"


@pytest.mark.parametrize("name", BUNDLED_PRESETS)
def test_every_preset_is_documented_in_skill_md(name: str) -> None:
    assert f"`phosphor:{name}`" in SKILL_MD.read_text()


def test_unknown_preset_error_lists_available_names() -> None:
    with pytest.raises(ValueError, match="realistic, design, print, documentation"):
        _ = load_bundled_render_settings("review")


def test_print_highlights_contrast_with_base_copper() -> None:
    """Highlighted copper must be distinguishable from base copper in print."""
    settings = load_bundled_render_settings("print")
    base_fill = settings.tokens["eda.copper.front.fill"]
    highlight_fill = settings.tokens["highlight.layer.default.fill"]
    assert base_fill != highlight_fill


def _documentation_copper_rules() -> list[LayerSelectionRule]:
    settings = load_bundled_render_settings("documentation")
    rules = [rule for rule in settings.source.layers if rule.match.role == "copper"]
    assert rules, "documentation preset selects no copper layers"
    return rules


def test_documentation_preset_omits_conductors_and_vias() -> None:
    """The documentation preset shows component pads, not routing or stitching."""
    for rule in _documentation_copper_rules():
        assert "conductor" not in rule.item_kinds
        assert "via" not in rule.item_kinds


def test_documentation_preset_scopes_copper_to_active_side() -> None:
    """Back-side SMD pads must not bleed into a front-view callout figure."""
    for rule in _documentation_copper_rules():
        assert rule.match.side == "active"


def test_documentation_preset_excludes_passives() -> None:
    """Passives are noise in callout figures; explicit highlights still render them."""
    settings = load_bundled_render_settings("documentation")
    assert set(settings.source.exclude_components) >= {"R*", "C*", "L*", "FB*"}


def test_documentation_preset_disables_marker_rings() -> None:
    """Rings overlap on fine-pitch headers; off until sizing is density-aware."""
    settings = load_bundled_render_settings("documentation")
    assert settings.tokens.get("highlight.marker.enabled") is not True
