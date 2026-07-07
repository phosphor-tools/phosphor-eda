import pytest

from phosphor_eda.render.tokens import (
    ResolvedStyle,
    VisualRole,
    eda_default_copper_color,
    resolve_layer_style,
)


def test_native_layer_override_wins_over_semantic_token() -> None:
    style = resolve_layer_style(
        {
            "eda.layer[F.Cu].fill": "#ff0000",
            "eda.copper.front.fill": "#d17a22",
        },
        VisualRole(
            namespace="eda",
            function="copper",
            side="front",
            source_layer_name="F.Cu",
        ),
    )

    assert style.fill == "#ff0000"


def test_semantic_side_token_is_used_without_native_override() -> None:
    style = resolve_layer_style(
        {"eda.copper.front.fill": "#d17a22"},
        VisualRole(namespace="eda", function="copper", side="front"),
    )

    assert style.fill == "#d17a22"


def test_indexed_inner_token_is_used_before_default_inner_token() -> None:
    style = resolve_layer_style(
        {
            "eda.copper.inner.2.fill": "#4fcbcb",
            "eda.copper.inner.default.fill": "#7fc87f",
        },
        VisualRole(namespace="eda", function="copper", side="inner", inner_index=2),
    )

    assert style.fill == "#4fcbcb"


def test_default_inner_token_is_used_without_indexed_inner_token() -> None:
    style = resolve_layer_style(
        {"eda.copper.inner.default.fill": "#7fc87f"},
        VisualRole(namespace="eda", function="copper", side="inner", inner_index=2),
    )

    assert style.fill == "#7fc87f"


def test_missing_normal_fill_token_raises_with_role_and_property() -> None:
    role = VisualRole(namespace="cad", function="copper", side="front")

    with pytest.raises(ValueError, match=r"cad\.copper\.front.*fill"):
        _ = resolve_layer_style({}, role)


def test_realistic_exposed_substrate_fill_defaults_to_substrate_fill() -> None:
    style = resolve_layer_style(
        {"realistic.substrate.fill": "#244426"},
        VisualRole(namespace="realistic", function="exposed_substrate"),
    )

    assert style.fill == "#244426"


def test_eda_copper_defaults_to_opaque_opacity() -> None:
    style = resolve_layer_style(
        {},
        VisualRole(namespace="eda", function="copper", side="front"),
    )

    assert style.opacity == 1.0


def test_eda_generated_inner_palette_supports_160_unique_copper_layers() -> None:
    colors = {eda_default_copper_color(f"In{index}.Cu", index) for index in range(1, 161)}

    assert len(colors) == 160


def test_eda_generated_inner_palette_is_deterministic() -> None:
    first_pass = [eda_default_copper_color(f"In{index}.Cu", index) for index in range(1, 161)]
    second_pass = [eda_default_copper_color(f"In{index}.Cu", index) for index in range(1, 161)]

    assert second_pass == first_pass


def test_explicit_eda_layer_token_wins_over_generated_default() -> None:
    style = resolve_layer_style(
        {"eda.layer[In42.Cu].fill": "#123456"},
        VisualRole(
            namespace="eda",
            function="copper",
            side="inner",
            source_layer_name="In42.Cu",
        ),
        eda_layer_order=42,
    )

    assert style == ResolvedStyle(
        fill="#123456",
        stroke="none",
        opacity=1.0,
    )


def test_eda_edge_defaults_to_outline_style() -> None:
    style = resolve_layer_style(
        {},
        VisualRole(namespace="eda", function="edge", source_layer_name="Edge.Cuts"),
    )

    assert style == ResolvedStyle(
        fill="none",
        stroke="#202020",
        stroke_width_mm=0.08,
    )


def test_eda_drill_defaults_to_outline_style() -> None:
    style = resolve_layer_style(
        {},
        VisualRole(namespace="eda", function="drill", source_layer_name="drills"),
    )

    assert style == ResolvedStyle(
        fill="none",
        stroke="#202020",
        stroke_width_mm=0.06,
    )


@pytest.mark.parametrize("function", ["fabrication", "assembly", "courtyard"])
def test_eda_fabrication_functions_default_to_outline_style(function: str) -> None:
    style = resolve_layer_style(
        {},
        VisualRole(namespace="eda", function=function, side="front"),
    )

    assert style == ResolvedStyle(
        fill="none",
        stroke="#666666",
        stroke_width_mm=0.08,
        opacity=0.8,
    )


@pytest.mark.parametrize("function", ["designator", "value", "user_text"])
def test_eda_part_text_functions_default_to_neutral_fill(function: str) -> None:
    style = resolve_layer_style(
        {},
        VisualRole(namespace="eda", function=function),
    )

    assert style == ResolvedStyle(fill="#777777", stroke="none", opacity=0.8)


@pytest.mark.parametrize(
    ("side", "fill"),
    [("front", "#ff8a00"), ("back", "#5aa7ff"), ("inner", "#ffe066")],
)
def test_highlight_copper_defaults_by_side(side: str, fill: str) -> None:
    style = resolve_layer_style(
        {},
        VisualRole(namespace="highlight", function="copper", side=side),
    )

    assert style == ResolvedStyle(fill=fill, stroke="none", opacity=0.85)


def test_highlight_non_copper_defaults_to_side_fill() -> None:
    style = resolve_layer_style(
        {},
        VisualRole(namespace="highlight", function="silkscreen", side="front"),
    )

    assert style == ResolvedStyle(fill="#ff8a00", stroke="none", opacity=0.85)


def test_highlight_sideless_layer_defaults_to_front_fill() -> None:
    style = resolve_layer_style(
        {},
        VisualRole(namespace="highlight", function="mechanical"),
    )

    assert style == ResolvedStyle(fill="#ff8a00", stroke="none", opacity=0.85)


def test_highlight_token_overrides_code_default() -> None:
    style = resolve_layer_style(
        {"highlight.copper.front.fill": "#cc0000"},
        VisualRole(namespace="highlight", function="copper", side="front"),
    )

    assert style.fill == "#cc0000"


@pytest.mark.parametrize(
    ("function", "expected"),
    [
        ("substrate", ResolvedStyle(fill="#b58b55")),
        ("solder_mask", ResolvedStyle(fill="#1f7a3a")),
        ("covered_copper", ResolvedStyle(fill="#145222", opacity=0.6)),
        ("exposed_substrate", ResolvedStyle(fill="#b58b55")),
        ("exposed_copper", ResolvedStyle(fill="#b87333", opacity=0.9)),
        ("silkscreen", ResolvedStyle(fill="#ffffff", stroke_width_mm=0.08)),
    ],
)
def test_realistic_functions_have_code_defaults(function: str, expected: ResolvedStyle) -> None:
    style = resolve_layer_style(
        {},
        VisualRole(namespace="realistic", function=function),
    )

    assert style == expected


def test_realistic_substrate_token_overrides_code_default() -> None:
    style = resolve_layer_style(
        {"realistic.substrate.fill": "#244426"},
        VisualRole(namespace="realistic", function="substrate"),
    )

    assert style.fill == "#244426"


def test_explicit_highlight_color_overrides_fill_but_retains_other_tokens() -> None:
    style = resolve_layer_style(
        {
            "highlight.copper.front.fill": "#ff8a00",
            "highlight.copper.front.opacity": 0.85,
            "highlight.copper.front.stroke": "none",
            "highlight.copper.front.strokeWidthMm": 0,
        },
        VisualRole(namespace="highlight", function="copper", side="front"),
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
        highlight_color="#ff3b30",
    )

    assert style == ResolvedStyle(
        fill="#ff3b30",
        stroke="none",
        opacity=0.85,
        stroke_width_mm=0.0,
    )
