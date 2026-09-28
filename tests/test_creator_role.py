"""The `creator` role, and deriving the role gates.

Three safety mechanisms once compared role *strings*, each of which a third role value
would have broken silently. All three read from one declaration -- ``role_scope`` --
which is the same "one ordering, not two" discipline ``SCOPE_ORDER`` applies to scopes.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from glosswork.db import Database
from glosswork.errors import ValidationFailedError
from glosswork.services import ServiceBundle
from glosswork.services.principals import VALID_ROLES, role_scope
from tests.conftest import make_actor

PASSWORD = "correct-horse-battery-staple"


def _user(services: ServiceBundle, email: str, role: str) -> str:
    return services.principals.create_user(
        make_actor(), email=email, display_name=email, role=role, password=PASSWORD
    ).id


# ------------------------------------------------------------- the role vocabulary


def test_creator_is_a_role_ordered_between_member_and_admin() -> None:
    assert VALID_ROLES == {"admin", "creator", "member"}


def test_role_scope_gives_a_creator_admin_scope() -> None:
    """A creator needs `admin` credential scope to reach the schema routes at all; what
    keeps it out of /admin/export is `require_role`, not its scope."""
    assert role_scope("admin") == "admin"
    assert role_scope("creator") == "admin"
    assert role_scope("member") == "write"


# ------------------------------------------- 5a: the last-admin guard fires on any exit


def test_demoting_the_only_admin_to_creator_is_refused(services: ServiceBundle) -> None:
    """The prior guard compared against the literal "member", so this transition would
    have walked the last administrator out of the deployment without firing."""
    admin_id = _user(services, "root@example.com", "admin")
    with pytest.raises(ValidationFailedError) as excinfo:
        services.principals.update_principal(make_actor(), admin_id, role="creator")
    assert "only active administrator" in excinfo.value.message


def test_demoting_the_only_admin_to_member_is_still_refused(services: ServiceBundle) -> None:
    """**Scope fence**: the pre-007 behaviour, unchanged. Cannot fail against the
    unfixed tree by construction, so it is not counted as a measured assertion."""
    admin_id = _user(services, "root@example.com", "admin")
    with pytest.raises(ValidationFailedError):
        services.principals.update_principal(make_actor(), admin_id, role="member")


def test_demoting_an_admin_to_creator_is_allowed_when_another_admin_exists(
    services: ServiceBundle,
) -> None:
    first = _user(services, "root@example.com", "admin")
    _user(services, "second@example.com", "admin")
    assert (
        services.principals.update_principal(make_actor(), first, role="creator").role == "creator"
    )


# ------------------------------------- 5a: the demotion sweep reads role_scope, not a role


def test_promoting_member_to_creator_revokes_no_token(services: ServiceBundle) -> None:
    """The prior sweep revoked every `admin`-scoped PAT whenever the new role was not
    `admin`, which would have stripped a fresh creator of the credential it needs."""
    _user(services, "root@example.com", "admin")
    member_id = _user(services, "m@example.com", "member")
    write_token = services.tokens.mint(
        make_actor(), name="laptop", scope="write", principal_id=member_id
    )
    services.principals.update_principal(make_actor(), member_id, role="creator")
    assert services.tokens.get_token(write_token.row.id).revoked_at is None


def test_demoting_creator_to_member_revokes_its_admin_token(services: ServiceBundle) -> None:
    _user(services, "root@example.com", "admin")
    creator_id = _user(services, "c@example.com", "creator")
    admin_token = services.tokens.mint(
        make_actor(), name="schema work", scope="admin", principal_id=creator_id
    )
    write_token = services.tokens.mint(
        make_actor(), name="notes", scope="write", principal_id=creator_id
    )
    services.principals.update_principal(make_actor(), creator_id, role="member")
    assert services.tokens.get_token(admin_token.row.id).revoked_at is not None
    assert services.tokens.get_token(write_token.row.id).revoked_at is None


# ---------------------------------------------- 5a: the mint ceiling reads role_scope


def test_a_creator_may_be_minted_an_admin_token(services: ServiceBundle) -> None:
    """`tokens.py` read `owner.role != "admin"` directly, so this was impossible."""
    creator_id = _user(services, "c@example.com", "creator")
    minted = services.tokens.mint(
        make_actor(), name="schema work", scope="admin", principal_id=creator_id
    )
    assert minted.row.scope == "admin"


def test_a_member_still_cannot_be_minted_an_admin_token(services: ServiceBundle) -> None:
    """**Scope fence**: FR-I4's ceiling, unchanged for members."""
    member_id = _user(services, "m@example.com", "member")
    with pytest.raises(ValidationFailedError) as excinfo:
        services.tokens.mint(make_actor(), name="x", scope="admin", principal_id=member_id)
    assert "'member'" in excinfo.value.message
    assert "'write'" in excinfo.value.message


# ----------------------------------------------------- 6: creating an object type


def _create_as(services: ServiceBundle, principal_id: str, key: str, prefix: str):
    from tests.test_access_service import actor_for

    return services.schema.create_object_type(
        actor_for(principal_id, "admin"),
        key=key,
        name=key.title(),
        name_plural=key.title() + "s",
        description=f"A {key}, created to test the creator role's own grant.",
        key_prefix=prefix,
    )


def test_a_member_cannot_create_an_object_type(services: ServiceBundle) -> None:
    member_id = _user(services, "m@example.com", "member")
    with pytest.raises(ValidationFailedError) as excinfo:
        _create_as(services, member_id, "widget", "WID")
    assert "'creator'" in excinfo.value.message
    assert "set-role" in excinfo.value.message


def test_a_creator_creates_a_type_and_gets_its_own_admin_grant(
    services: ServiceBundle, db: Database
) -> None:
    creator_id = _user(services, "c@example.com", "creator")
    created = _create_as(services, creator_id, "widget", "WID")
    with db.read() as conn:
        rows = conn.execute(
            text("SELECT principal_id, level FROM object_type_grants WHERE object_type_id = :t"),
            {"t": created.id},
        ).all()
    assert [(r[0], r[1]) for r in rows] == [(creator_id, "admin")]


def test_a_system_admin_also_gets_the_redundant_owner_row(
    services: ServiceBundle, db: Database
) -> None:
    """Deliberately redundant: it makes "who owns this type" a legible query against one
    table rather than a rule the reader has to know."""
    admin_id = _user(services, "root@example.com", "admin")
    created = _create_as(services, admin_id, "gadget", "GAD")
    with db.read() as conn:
        level = conn.execute(
            text(
                "SELECT level FROM object_type_grants WHERE object_type_id = :t "
                "AND principal_id = :p"
            ),
            {"t": created.id, "p": admin_id},
        ).scalar()
    assert level == "admin"
