"""Shared pytest fixtures and test-session configuration."""

from __future__ import annotations

import os

import pytest

from phosphor_eda.domain.pcb import (
    Board,
    LayerRole,
    PadStack,
    PcbArtwork,
    PcbArtworkKind,
    PcbArtworkPurpose,
    PcbBoardProfile,
    PcbBoardProfileElement,
    PcbConductor,
    PcbConductorKind,
    PcbDrill,
    PcbDrillPlating,
    PcbFootprint,
    PcbLayer,
    PcbLine,
    PcbMaskAperture,
    PcbNet,
    PcbPad,
    PcbPadType,
    PcbText,
    PcbVia,
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-behavior-locks",
        action="store_true",
        default=False,
        help="Run slow full-project behavior-lock tests.",
    )
    parser.addoption(
        "--run-corpus",
        action="store_true",
        default=False,
        help="Run optional local-corpus tests.",
    )
    parser.addoption(
        "--run-allegro-corpus",
        action="store_true",
        default=False,
        help="Run optional local Allegro corpus tests.",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register custom test markers."""
    config.addinivalue_line(
        "markers",
        "behavior_lock: slow full-project output lock tests; run with "
        "--run-behavior-locks or PHOSPHOR_RUN_BEHAVIOR_LOCKS=1",
    )
    config.addinivalue_line(
        "markers",
        "corpus: optional local-corpus tests; run with --run-corpus or PHOSPHOR_RUN_CORPUS=1",
    )
    config.addinivalue_line(
        "markers",
        "allegro_corpus: optional local Allegro corpus tests; run with --run-allegro-corpus, "
        "--run-corpus, PHOSPHOR_RUN_ALLEGRO_CORPUS=1, or PHOSPHOR_RUN_CORPUS=1",
    )
    config.addinivalue_line(
        "markers",
        "allegro_external_oracle: optional Allegro external-tool oracle tests; run with "
        "PHOSPHOR_RUN_ALLEGRO_EXTERNAL_ORACLES=1",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_behavior_locks = (
        config.getoption("--run-behavior-locks")
        or os.environ.get("PHOSPHOR_RUN_BEHAVIOR_LOCKS") == "1"
    )
    run_corpus = config.getoption("--run-corpus") or os.environ.get("PHOSPHOR_RUN_CORPUS") == "1"
    run_allegro_corpus = (
        run_corpus
        or config.getoption("--run-allegro-corpus")
        or os.environ.get("PHOSPHOR_RUN_ALLEGRO_CORPUS") == "1"
    )
    run_allegro_external_oracles = os.environ.get("PHOSPHOR_RUN_ALLEGRO_EXTERNAL_ORACLES") == "1"
    skip_behavior_lock = pytest.mark.skip(
        reason=(
            "behavior-lock tests are slow; run with --run-behavior-locks "
            "or PHOSPHOR_RUN_BEHAVIOR_LOCKS=1"
        )
    )
    skip_corpus = pytest.mark.skip(
        reason="corpus tests are optional; run with --run-corpus or PHOSPHOR_RUN_CORPUS=1"
    )
    skip_allegro_corpus = pytest.mark.skip(
        reason=(
            "Allegro corpus tests are optional; run with --run-allegro-corpus "
            "or --run-corpus, PHOSPHOR_RUN_ALLEGRO_CORPUS=1, or PHOSPHOR_RUN_CORPUS=1"
        )
    )
    skip_allegro_external_oracle = pytest.mark.skip(
        reason=(
            "Allegro external-tool oracle tests are optional; run with "
            "PHOSPHOR_RUN_ALLEGRO_EXTERNAL_ORACLES=1"
        )
    )
    for item in items:
        if "behavior_lock" in item.keywords and not run_behavior_locks:
            item.add_marker(skip_behavior_lock)
        if "corpus" in item.keywords and not run_corpus:
            item.add_marker(skip_corpus)
        if "allegro_corpus" in item.keywords and not run_allegro_corpus:
            item.add_marker(skip_allegro_corpus)
        if "allegro_external_oracle" in item.keywords and not run_allegro_external_oracles:
            item.add_marker(skip_allegro_external_oracle)


def build_render_test_board() -> Board:
    """Build the synthetic board shared by the PCB render test modules.

    Returns a fresh instance each call so tests that mutate the board stay
    isolated.
    """
    front_cu = PcbLayer("F.Cu", (LayerRole.COPPER, LayerRole.FRONT, LayerRole.OUTER), number=0)
    back_cu = PcbLayer("B.Cu", (LayerRole.COPPER, LayerRole.BACK, LayerRole.OUTER), number=31)
    front_mask = PcbLayer("F.Mask", (LayerRole.SOLDER_MASK, LayerRole.FRONT), number=37)
    front_silk = PcbLayer("F.SilkS", (LayerRole.SILKSCREEN, LayerRole.FRONT), number=33)
    edge = PcbLayer("Edge.Cuts", (LayerRole.EDGE,), number=44)
    net = PcbNet(1, "VCC")
    footprint = PcbFootprint("U1", "Package", 5.0, 5.0, 0.0, front_cu, value="MCU")
    pad_drill = PcbDrill(
        "drill:pad:U1:1",
        5.0,
        5.0,
        0.4,
        plating=PcbDrillPlating.PLATED,
        layers=(front_cu, back_cu),
    )
    via_drill = PcbDrill(
        "drill:via:1",
        8.0,
        5.0,
        0.35,
        plating=PcbDrillPlating.PLATED,
        layers=(front_cu, back_cu),
    )
    pad = PcbPad(
        id="pad:U1:1",
        number="1",
        x=5.0,
        y=5.0,
        stack=PadStack.simple("circle", 1.4, 1.4),
        pad_type=PcbPadType.THROUGH_HOLE,
        layers=(front_cu, front_mask),
        net=net,
        footprint=footprint,
        drill=pad_drill,
        mask_aperture=PcbMaskAperture(mask_expansion=0.05),
    )
    via = PcbVia(
        id="via:1",
        x=8.0,
        y=5.0,
        stack=PadStack.simple("circle", 0.8, 0.8),
        layers=(front_cu, back_cu),
        drill=via_drill,
        net=net,
    )
    return Board(
        name="render-test",
        layers=[front_cu, back_cu, front_mask, front_silk, edge],
        nets={1: net},
        footprints=[footprint],
        pads=[pad],
        vias=[via],
        drills=[pad_drill, via_drill],
        conductors=[
            PcbConductor(
                id="trace:1",
                kind=PcbConductorKind.TRACE,
                layer=front_cu,
                data=PcbLine(5.0, 5.0, 8.0, 5.0, 0.25),
                net=net,
            )
        ],
        artwork=[
            PcbArtwork(
                id="silk:1",
                kind=PcbArtworkKind.LINE,
                purpose=PcbArtworkPurpose.SILKSCREEN,
                layer=front_silk,
                data=PcbLine(4.0, 7.0, 6.0, 7.0, 0.12),
                footprint=footprint,
            ),
            PcbArtwork(
                id="text:U1:ref",
                kind=PcbArtworkKind.TEXT,
                purpose=PcbArtworkPurpose.DESIGNATOR,
                layer=front_silk,
                data=PcbText("U1", 5.0, 3.5, 0.0, 1.0),
                footprint=footprint,
            ),
        ],
        pours=[],
        keepouts=[],
        board_profile=PcbBoardProfile(
            elements=tuple(
                PcbBoardProfileElement(
                    id=f"edge:{index}",
                    kind=PcbArtworkKind.LINE,
                    layer=edge,
                    data=PcbLine(x1, y1, x2, y2, 0.1),
                )
                for index, ((x1, y1), (x2, y2)) in enumerate(
                    zip(
                        [(0.0, 0.0), (12.0, 0.0), (12.0, 10.0), (0.0, 10.0)],
                        [(12.0, 0.0), (12.0, 10.0), (0.0, 10.0), (0.0, 0.0)],
                        strict=False,
                    )
                )
            )
        ),
    )
