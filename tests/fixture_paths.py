"""Shared filesystem locations for test fixtures.

Every test module lives directly under ``tests/``, so these constants resolve
to the same directories that the per-module ``FIXTURES``/``UPSTREAM_FIXTURES``
definitions used to compute individually.
"""

from __future__ import annotations

from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent
FIXTURES = TESTS_ROOT / "fixtures"
UPSTREAM_FIXTURES = TESTS_ROOT / "upstream"
