import pytest

from phosphor_eda.pcb_render_tokens import (
    ResolvedStyle,
    VisualRole,
    resolve_layer_style,
)


def test_native_layer_override_wins_over_semantic_token() -> None:
    style = resolve_layer_style(
        {
            "cad.layer[F.Cu].fill": "#ff0000",
            "cad.copper.front.fill": "#d17a22",
        },
        VisualRole(
            namespace="cad",
            function="copper",
            side="front",
            source_layer_name="F.Cu",
        ),
        dimmed=False,
        warn=lambda _message: None,
    )

    assert style.fill == "#ff0000"


def test_semantic_side_token_is_used_without_native_override() -> None:
    style = resolve_layer_style(
        {"cad.copper.front.fill": "#d17a22"},
        VisualRole(namespace="cad", function="copper", side="front"),
        dimmed=False,
        warn=lambda _message: None,
    )

    assert style.fill == "#d17a22"


def test_indexed_inner_token_is_used_before_default_inner_token() -> None:
    style = resolve_layer_style(
        {
            "cad.copper.inner.2.fill": "#4fcbcb",
            "cad.copper.inner.default.fill": "#7fc87f",
        },
        VisualRole(namespace="cad", function="copper", side="inner", inner_index=2),
        dimmed=False,
        warn=lambda _message: None,
    )

    assert style.fill == "#4fcbcb"


def test_default_inner_token_is_used_without_indexed_inner_token() -> None:
    style = resolve_layer_style(
        {"cad.copper.inner.default.fill": "#7fc87f"},
        VisualRole(namespace="cad", function="copper", side="inner", inner_index=2),
        dimmed=False,
        warn=lambda _message: None,
    )

    assert style.fill == "#7fc87f"


def test_missing_normal_fill_token_raises_with_role_and_property() -> None:
    role = VisualRole(namespace="cad", function="copper", side="front")

    with pytest.raises(ValueError, match=r"cad\.copper\.front.*fill"):
        _ = resolve_layer_style({}, role, dimmed=False, warn=lambda _message: None)


def test_missing_dimmed_token_warns_and_falls_back_to_normal_token() -> None:
    warnings: list[str] = []
    role = VisualRole(namespace="cad", function="copper", side="front")

    style = resolve_layer_style(
        {"cad.copper.front.fill": "#d17a22"},
        role,
        dimmed=True,
        warn=warnings.append,
    )

    assert style.fill == "#d17a22"
    assert warnings == [
        "Missing dimmed style token cad.dimmed.copper.front.fill; using cad.copper.front.fill"
    ]


def test_missing_dimmed_token_warns_once_when_tracker_is_reused() -> None:
    warnings: list[str] = []
    warned_tokens: set[str] = set()
    role = VisualRole(namespace="cad", function="copper", side="front")

    for _ in range(2):
        style = resolve_layer_style(
            {"cad.copper.front.fill": "#d17a22"},
            role,
            dimmed=True,
            warn=warnings.append,
            warned_missing_dimmed_tokens=warned_tokens,
        )
        assert style.fill == "#d17a22"

    assert warnings == [
        "Missing dimmed style token cad.dimmed.copper.front.fill; using cad.copper.front.fill"
    ]


def test_explicit_highlight_color_overrides_fill_but_retains_other_tokens() -> None:
    style = resolve_layer_style(
        {
            "highlight.copper.front.fill": "#ff8a00",
            "highlight.copper.front.opacity": 0.85,
            "highlight.copper.front.stroke": "none",
            "highlight.copper.front.strokeWidthMm": 0,
        },
        VisualRole(namespace="highlight", function="copper", side="front"),
        dimmed=False,
        warn=lambda _message: None,
        highlight_color="#ff3b30",
    )

    assert style == ResolvedStyle(
        fill="#ff3b30",
        stroke="none",
        opacity=0.85,
        stroke_width_mm=0.0,
    )


def test_explicit_highlight_color_does_not_require_layer_fill_token() -> None:
    style = resolve_layer_style(
        {
            "highlight.copper.front.opacity": 0.85,
            "highlight.copper.front.stroke": "none",
            "highlight.copper.front.strokeWidthMm": 0,
        },
        VisualRole(namespace="highlight", function="copper", side="front"),
        dimmed=False,
        warn=lambda _message: None,
        highlight_color="#ff3b30",
    )

    assert style == ResolvedStyle(
        fill="#ff3b30",
        stroke="none",
        opacity=0.85,
        stroke_width_mm=0.0,
    )
