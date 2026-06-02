"""Tests for the resolver union-find helper."""

from __future__ import annotations

import pytest

from phosphor_eda.net_union import NetUnion


def test_find_returns_each_id_as_its_initial_representative() -> None:
    union = NetUnion(["net-a", "net-b"])

    assert union.find("net-a") == "net-a"
    assert union.find("net-b") == "net-b"


def test_union_merges_groups_and_reports_whether_state_changed() -> None:
    union = NetUnion(["net-a", "net-b", "net-c"])

    assert union.union("net-a", "net-b") is True
    assert union.find("net-a") == union.find("net-b")
    assert union.find("net-c") == "net-c"

    assert union.union("net-b", "net-a") is False


def test_groups_returns_members_by_representative() -> None:
    union = NetUnion(["net-a", "net-b", "net-c", "net-d"])

    assert union.union("net-a", "net-b") is True
    assert union.union("net-c", "net-d") is True

    assert union.groups() == {
        "net-a": ["net-a", "net-b"],
        "net-c": ["net-c", "net-d"],
    }


def test_find_compresses_transitive_parent_chain_through_public_methods() -> None:
    union = NetUnion(["net-a", "net-b", "net-c", "net-d"])

    assert union.union("net-b", "net-a") is True
    assert union.union("net-c", "net-b") is True
    assert union.union("net-d", "net-c") is True

    assert union.find("net-a") == "net-d"
    assert union.union("net-a", "net-d") is False
    assert union.groups() == {"net-d": ["net-a", "net-b", "net-c", "net-d"]}


def test_find_rejects_unknown_id() -> None:
    union = NetUnion(["net-a"])

    with pytest.raises(KeyError, match="Unknown net id: missing"):
        _ = union.find("missing")


def test_union_rejects_unknown_left_id() -> None:
    union = NetUnion(["net-a", "net-b"])

    with pytest.raises(KeyError, match="Unknown net id: missing"):
        _ = union.union("missing", "net-b")


def test_union_rejects_unknown_right_id() -> None:
    union = NetUnion(["net-a", "net-b"])

    with pytest.raises(KeyError, match="Unknown net id: missing"):
        _ = union.union("net-a", "missing")
