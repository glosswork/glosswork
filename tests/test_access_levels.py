"""The level ordering, and the proof that it is *one* ordering.

``SCOPE_ORDER`` is derived from ``LEVEL_ORDER`` rather than declared beside it, so the
guarantee under test is not "the two agree today" but "there is nothing to disagree".
"""

from __future__ import annotations

import itertools

import pytest

from glosswork.actor import Scope
from glosswork.auth import LEVEL_ORDER, SCOPE_ORDER, level_allows, min_level, scope_allows

SCOPES: tuple[Scope, ...] = ("read", "write", "admin")
LEVELS = ("none", "read", "write", "admin")

# What ``scope_allows`` returned before access levels existed, transcribed rather than
# computed, so a change to the ordering shows up here as a diff instead of as agreement
# with itself.
_SCOPE_ALLOWS_BEFORE_ACCESS_LEVELS = {
    ("read", "read"): True,
    ("read", "write"): False,
    ("read", "admin"): False,
    ("write", "read"): True,
    ("write", "write"): True,
    ("write", "admin"): False,
    ("admin", "read"): True,
    ("admin", "write"): True,
    ("admin", "admin"): True,
}


@pytest.mark.parametrize(("actual", "required"), sorted(_SCOPE_ALLOWS_BEFORE_ACCESS_LEVELS))
def test_scope_allows_is_byte_for_byte_unchanged(actual: Scope, required: Scope) -> None:
    """All nine pairs. Deriving ``SCOPE_ORDER`` must change nothing about it."""
    assert scope_allows(actual, required) is _SCOPE_ALLOWS_BEFORE_ACCESS_LEVELS[(actual, required)]


def test_scope_order_is_the_level_order_without_the_deny_row() -> None:
    assert SCOPE_ORDER == {k: v for k, v in LEVEL_ORDER.items() if k != "none"}
    assert set(SCOPE_ORDER) == set(SCOPES)
    assert LEVEL_ORDER["none"] < LEVEL_ORDER["read"]


def test_level_allows_reads_the_same_table_as_scope_allows() -> None:
    for actual, required in itertools.product(SCOPES, SCOPES):
        assert level_allows(actual, required) == scope_allows(actual, required)


def test_none_satisfies_no_requirement() -> None:
    for required in SCOPES:
        assert level_allows("none", required) is False


def test_min_level_is_the_credential_ceiling() -> None:
    """A credential can only ever narrow (DD-11)."""
    # A read PAT held by the administrator of a type still only reads it.
    assert min_level("read", "admin") == "read"
    # And no credential lifts an explicit deny.
    for scope in SCOPES:
        assert min_level(scope, "none") == "none"
    # Where the grant is the lower of the two, the grant wins.
    assert min_level("admin", "write") == "write"
    assert min_level("write", "write") == "write"


def test_min_level_is_the_lower_of_the_two_for_every_combination() -> None:
    for scope in SCOPES:
        for granted in LEVELS:
            result = min_level(scope, granted)  # type: ignore[arg-type]
            assert LEVEL_ORDER[result] == min(LEVEL_ORDER[scope], LEVEL_ORDER[granted])
