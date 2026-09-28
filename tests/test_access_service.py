"""``AccessService`` and the composition it implements (DD-11).

granted(principal, T) =
    'admin'                       if principal.role == 'admin'
    grant_level(principal, T)     if a grant row exists   -- may be 'none'
    T.default_level               otherwise

effective(credential, T) = min(credential.scope, granted(principal, T))
"""

from __future__ import annotations

import itertools
import uuid

import pytest

from glosswork.actor import ActorContext, Level, Scope
from glosswork.db import Database
from glosswork.errors import ForbiddenError
from glosswork.repositories.models import ObjectType
from glosswork.services import ServiceBundle
from tests.conftest import make_actor

SCOPES: tuple[Scope, ...] = ("read", "write", "admin")
LEVELS: tuple[Level, ...] = ("none", "read", "write", "admin")


def actor_for(principal_id: str, scope: Scope) -> ActorContext:
    return ActorContext(
        principal_id=principal_id,
        principal_type="user",
        agent_label_id=None,
        auth_method="pat",
        surface="api",
        request_id=str(uuid.uuid4()),
        scope=scope,
    )


@pytest.fixture
def widget(services: ServiceBundle) -> ObjectType:
    return services.schema.create_object_type(
        make_actor(),
        key="widget",
        name="Widget",
        name_plural="Widgets",
        description="A thing under test, for the access composition table.",
        key_prefix="WID",
    )


def _member(services: ServiceBundle, email: str = "member@example.com") -> str:
    return services.principals.create_user(
        make_actor(), email=email, display_name="Member", role="member"
    ).id


def _admin(services: ServiceBundle, email: str = "root@example.com") -> str:
    return services.principals.create_user(
        make_actor(), email=email, display_name="Root", role="admin"
    ).id


def _type_row(services: ServiceBundle, key: str = "widget") -> ObjectType:
    with services.schema._db.read() as conn:  # noqa: SLF001 - test reaches the row directly
        row = services.schema._schema.get_object_type_by_key(conn, key)  # noqa: SLF001
    assert row is not None
    return row


def _granted(services: ServiceBundle, actor: ActorContext, object_type: ObjectType) -> Level:
    with services.schema._db.read() as conn:  # noqa: SLF001
        return services.access.granted_level(conn, actor, object_type)


def _effective(services: ServiceBundle, actor: ActorContext, object_type: ObjectType) -> Level:
    with services.schema._db.read() as conn:  # noqa: SLF001
        return services.access.effective_level(conn, actor, object_type)


# ------------------------------------------------------------ granted(principal, T)


def test_a_system_admin_is_implicitly_admin_on_every_type(
    services: ServiceBundle, widget: ObjectType
) -> None:
    """Which is what makes "a system administrator can always repair it" true, and
    therefore what lets grants have no last-admin invariant at all."""
    admin = actor_for(_admin(services), "admin")
    assert _granted(services, admin, _type_row(services)) == "admin"


def test_with_no_grant_row_the_type_default_applies(
    services: ServiceBundle, widget: ObjectType
) -> None:
    member = actor_for(_member(services), "write")
    # Every type is uniformly closed at creation (no backfill).
    assert _type_row(services).default_level == "none"
    assert _granted(services, member, _type_row(services)) == "none"

    services.schema.update_object_type(make_actor(), "widget", {"default_level": "read"})
    assert _granted(services, member, _type_row(services)) == "read"


def test_an_explicit_none_grant_overrides_a_permissive_default(
    services: ServiceBundle, widget: ObjectType
) -> None:
    """The second most common thing an administrator wants after "let one person in"."""
    principal_id = _member(services)
    member = actor_for(principal_id, "write")
    services.schema.update_object_type(make_actor(), "widget", {"default_level": "write"})
    assert _granted(services, member, _type_row(services)) == "write"

    services.access.grant(make_actor(), "widget", principal_id, "none")
    assert _granted(services, member, _type_row(services)) == "none"


def test_a_grant_row_beats_the_default_in_both_directions(
    services: ServiceBundle, widget: ObjectType
) -> None:
    principal_id = _member(services)
    member = actor_for(principal_id, "write")
    services.schema.update_object_type(make_actor(), "widget", {"default_level": "read"})
    services.access.grant(make_actor(), "widget", principal_id, "write")
    assert _granted(services, member, _type_row(services)) == "write"
    services.access.grant(make_actor(), "widget", principal_id, "read")
    assert _granted(services, member, _type_row(services)) == "read"


def test_revoking_returns_the_principal_to_the_default_unlike_granting_none(
    services: ServiceBundle, widget: ObjectType
) -> None:
    principal_id = _member(services)
    member = actor_for(principal_id, "write")
    services.schema.update_object_type(make_actor(), "widget", {"default_level": "read"})
    services.access.grant(make_actor(), "widget", principal_id, "none")
    assert _granted(services, member, _type_row(services)) == "none"
    services.access.revoke(make_actor(), "widget", principal_id)
    assert _granted(services, member, _type_row(services)) == "read"


# ------------------------------------------------- the credential ceiling, all nine


@pytest.mark.parametrize(("scope", "granted"), list(itertools.product(SCOPES, LEVELS)))
def test_the_credential_is_a_ceiling_in_every_combination(
    services: ServiceBundle, widget: ObjectType, scope: Scope, granted: Level
) -> None:
    """``effective = min(scope, granted)``, for all twelve pairs. A credential can only
    ever narrow: it never lifts ``none``, and a ``read`` PAT held by the administrator
    of a type still only reads it."""
    from glosswork.auth import LEVEL_ORDER

    principal_id = _member(services, f"m-{scope}-{granted}@example.com")
    services.access.grant(make_actor(), "widget", principal_id, granted)
    effective = _effective(services, actor_for(principal_id, scope), _type_row(services))
    assert LEVEL_ORDER[effective] == min(LEVEL_ORDER[scope], LEVEL_ORDER[granted])


def test_a_read_pat_on_a_type_you_administer_reports_read(
    services: ServiceBundle, widget: ObjectType
) -> None:
    principal_id = _member(services)
    services.access.grant(make_actor(), "widget", principal_id, "admin")
    assert _effective(services, actor_for(principal_id, "read"), _type_row(services)) == "read"


def test_the_ceiling_applies_to_a_system_admin_too(
    services: ServiceBundle, widget: ObjectType
) -> None:
    admin = actor_for(_admin(services), "read")
    assert _granted(services, admin, _type_row(services)) == "admin"
    assert _effective(services, admin, _type_row(services)) == "read"


# ------------------------------------------------------------------ require_level


def test_require_level_raises_forbidden_naming_type_held_and_required(
    services: ServiceBundle, widget: ObjectType
) -> None:
    member = actor_for(_member(services), "write")
    with services.schema._db.read() as conn:  # noqa: SLF001
        with pytest.raises(ForbiddenError) as excinfo:
            services.access.require_level(conn, member, _type_row(services), "read")
    error = excinfo.value
    assert error.code == "forbidden"
    assert error.details == {"object_type": "widget", "held": "none", "required": "read"}
    assert "widget" in error.message
    assert "'none'" in error.message
    assert "'read'" in error.message
    assert "administrator" in error.message


def test_require_level_passes_at_or_above_the_requirement(
    services: ServiceBundle, widget: ObjectType
) -> None:
    principal_id = _member(services)
    services.access.grant(make_actor(), "widget", principal_id, "write")
    member = actor_for(principal_id, "write")
    with services.schema._db.read() as conn:  # noqa: SLF001
        services.access.require_level(conn, member, _type_row(services), "read")
        services.access.require_level(conn, member, _type_row(services), "write")


def test_which_half_of_the_min_fell_short_decides_the_code(
    services: ServiceBundle, widget: ObjectType
) -> None:
    """Section 10's distinction, made real. Reporting `forbidden` when the credential
    was the narrow half would send an agent to ask for a grant it already holds."""
    from glosswork.errors import InsufficientScopeError

    principal_id = _member(services)
    services.access.grant(make_actor(), "widget", principal_id, "admin")
    with services.schema._db.read() as conn:  # noqa: SLF001
        # Grant is admin, credential is write: the *credential* is what refused.
        with pytest.raises(InsufficientScopeError) as scope_exc:
            services.access.require_level(
                conn, actor_for(principal_id, "write"), _type_row(services), "admin"
            )
        assert scope_exc.value.code == "insufficient_scope"

    services.access.grant(make_actor(), "widget", principal_id, "read")
    with services.schema._db.read() as conn:  # noqa: SLF001
        # Credential is write and sufficient; the *grant* is what refused.
        with pytest.raises(ForbiddenError) as grant_exc:
            services.access.require_level(
                conn, actor_for(principal_id, "write"), _type_row(services), "write"
            )
        assert grant_exc.value.code == "forbidden"


def test_the_forbidden_message_differs_when_some_access_is_held(
    services: ServiceBundle, widget: ObjectType
) -> None:
    principal_id = _member(services)
    services.access.grant(make_actor(), "widget", principal_id, "read")
    member = actor_for(principal_id, "write")
    with services.schema._db.read() as conn, pytest.raises(ForbiddenError) as excinfo:  # noqa: SLF001
        services.access.require_level(conn, member, _type_row(services), "write")
    assert "is 'read'" in excinfo.value.message
    assert "raise it" in excinfo.value.message


# -------------------------------------------------------------- accessible_type_ids


def test_accessible_type_ids_resolves_the_whole_map_at_the_required_level(
    services: ServiceBundle, widget: ObjectType
) -> None:
    other = services.schema.create_object_type(
        make_actor(),
        key="gadget",
        name="Gadget",
        name_plural="Gadgets",
        description="A second type, so the filter has something to leave out.",
        key_prefix="GAD",
    )
    third = services.schema.create_object_type(
        make_actor(),
        key="doodad",
        name="Doodad",
        name_plural="Doodads",
        description="A third type, open by default, to exercise the default branch.",
        key_prefix="DOO",
    )
    services.schema.update_object_type(make_actor(), "doodad", {"default_level": "read"})

    principal_id = _member(services)
    services.access.grant(make_actor(), "widget", principal_id, "write")
    member = actor_for(principal_id, "write")

    with services.schema._db.read() as conn:  # noqa: SLF001
        readable = services.access.accessible_type_ids(conn, member, "read")
        writable = services.access.accessible_type_ids(conn, member, "write")
    assert readable == {widget.id, third.id}
    assert writable == {widget.id}
    assert other.id not in readable


def test_accessible_type_ids_gives_a_system_admin_everything(
    services: ServiceBundle, widget: ObjectType
) -> None:
    admin = actor_for(_admin(services), "admin")
    with services.schema._db.read() as conn:  # noqa: SLF001
        assert services.access.accessible_type_ids(conn, admin, "admin") == {widget.id}
        # ... and the ceiling still applies: a read PAT admin writes nothing.
        reader = actor_for(admin.principal_id, "read")
        assert services.access.accessible_type_ids(conn, reader, "write") == set()


def test_effective_levels_agrees_with_effective_level_per_type(
    services: ServiceBundle, widget: ObjectType
) -> None:
    """The bulk form exists so ``list_object_types`` is two reads rather than N. It has
    to give the same answer as the single form or it is a second implementation."""
    services.schema.create_object_type(
        make_actor(),
        key="gadget",
        name="Gadget",
        name_plural="Gadgets",
        description="A second type, so the bulk form has more than one row to resolve.",
        key_prefix="GAD",
    )
    principal_id = _member(services)
    services.access.grant(make_actor(), "widget", principal_id, "read")
    member = actor_for(principal_id, "write")
    with services.schema._db.read() as conn:  # noqa: SLF001
        types = services.schema._schema.list_object_types(conn)  # noqa: SLF001
        bulk = services.access.effective_levels(conn, member, types)
        singly = {t.id: services.access.effective_level(conn, member, t) for t in types}
    assert bulk == singly
    assert set(bulk.values()) == {"read", "none"}


# ------------------------------------------------------------- grant management


def test_granting_requires_admin_on_the_type(services: ServiceBundle, widget: ObjectType) -> None:
    outsider_id = _member(services, "outsider@example.com")
    target_id = _member(services, "target@example.com")
    with pytest.raises(ForbiddenError):
        services.access.grant(actor_for(outsider_id, "admin"), "widget", target_id, "read")


def test_a_grant_change_emits_an_object_type_grant_audit_event(
    services: ServiceBundle, widget: ObjectType, db: Database
) -> None:
    from sqlalchemy import text

    principal_id = _member(services)
    services.access.grant(make_actor(), "widget", principal_id, "read")
    services.access.grant(make_actor(), "widget", principal_id, "write")
    services.access.revoke(make_actor(), "widget", principal_id)
    with db.read() as conn:
        rows = conn.execute(
            text(
                "SELECT action, old_value, new_value, object_type_id FROM audit_events "
                "WHERE entity_type = 'object_type_grant' ORDER BY id"
            )
        ).all()
    assert [r[0] for r in rows] == ["create", "update", "delete"]
    assert all(r[3] == widget.id for r in rows)


def test_re_granting_preserves_who_first_admitted_the_principal(
    services: ServiceBundle, widget: ObjectType
) -> None:
    principal_id = _member(services)
    first = services.access.grant(make_actor(), "widget", principal_id, "read")
    second = services.access.grant(make_actor(), "widget", principal_id, "write")
    assert second.id == first.id
    assert second.created_at == first.created_at
    assert second.created_by == first.created_by
    assert second.level == "write"


def test_an_unknown_level_is_refused_with_the_valid_set(
    services: ServiceBundle, widget: ObjectType
) -> None:
    from glosswork.errors import ValidationFailedError

    with pytest.raises(ValidationFailedError) as excinfo:
        services.access.grant(make_actor(), "widget", _member(services), "owner")
    assert "none, read, write, admin" in excinfo.value.message


def test_list_grants_returns_the_types_rows(services: ServiceBundle, widget: ObjectType) -> None:
    a = _member(services, "a@example.com")
    b = _member(services, "b@example.com")
    services.access.grant(make_actor(), "widget", a, "read")
    services.access.grant(make_actor(), "widget", b, "none")
    grants = services.access.list_grants(make_actor(), "widget")
    assert {(g.principal_id, g.level) for g in grants} >= {(a, "read"), (b, "none")}
