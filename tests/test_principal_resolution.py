"""The one principal resolver, and the three callers that reach it.

The ordering (``@me``, id, email, unique display name) is the load-bearing part and is
pinned case by case, including the one case that proves it is an *ordering* rather than a
set of independent lookups: a principal whose display name happens to be another
principal's id resolves as the id.

The read/write asymmetry is the second: a filter accepts a deactivated principal because
finding a departed colleague's open work is the handover query, and a write does not
because a fresh assignment to someone who has left is a mistake. The stored-value exception
(``TestTheStoredValueException``) is the seam between them, and it is why re-submitting an
unchanged value still saves. ``TestServiceAccountsRefused`` covers a field that refuses
service accounts.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID
from glosswork.db import Database
from glosswork.errors import ValidationFailedError
from glosswork.repositories.models import PrincipalRow
from glosswork.repositories.sqlite import SqlitePrincipalRepository
from glosswork.services import ServiceBundle
from glosswork.services.principals import resolve_principal_ref
from tests.conftest import make_actor

PASSWORD = "correct-horse-battery-staple"

OWNER_FIELD: dict[str, Any] = {
    "key": "owner",
    "name": "Owner",
    "type": "user_ref",
    "description": "Who is accountable for this initiative day to day.",
}


# --------------------------------------------------------------------------- fixtures


def user(
    services: ServiceBundle, email: str, display_name: str, active: bool = True
) -> PrincipalRow:
    row = services.principals.create_user(
        make_actor(), email=email, display_name=display_name, role="member", password=PASSWORD
    )
    if not active:
        services.principals.deactivate_principal(make_actor(), row.id)
    return row


def service_account(services: ServiceBundle, display_name: str) -> PrincipalRow:
    return services.principals.create_service_account(
        make_actor(),
        display_name=display_name,
        description="Seeded for the resolver suite; holds tokens, never logs in.",
    )


def seed_type(services: ServiceBundle, *extra_config: dict[str, Any]) -> None:
    owner = dict(OWNER_FIELD)
    for config in extra_config:
        owner["config"] = config
    services.schema.create_object_type(
        make_actor(),
        key="initiative",
        name="Initiative",
        name_plural="Initiatives",
        description="A programme of work, seeded for the resolver suite.",
        key_prefix="INI",
        fields=[
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "What this initiative is called, shown wherever it is listed.",
            },
            owner,
        ],
    )


@pytest.fixture
def typed(services: ServiceBundle) -> ServiceBundle:
    seed_type(services)
    return services


def resolve(db: Database, value: str, **kwargs: Any) -> str:
    """The resolver, called the way its three callers call it."""
    kwargs.setdefault("me", BOOTSTRAP_PRINCIPAL_ID)
    with db.read() as conn:
        return resolve_principal_ref(conn, SqlitePrincipalRepository(), value, **kwargs)


# -------------------------------------------------------- the resolver, branch by branch


class TestResolverOrdering:
    def test_at_me_resolves_to_the_calling_principal(
        self, db: Database, services: ServiceBundle
    ) -> None:
        assert resolve(db, "@me") == BOOTSTRAP_PRINCIPAL_ID

    def test_an_id_resolves_to_itself(self, db: Database, services: ServiceBundle) -> None:
        sarah = user(services, "sarah@example.com", "Sarah Okonjo")
        assert resolve(db, sarah.id) == sarah.id

    def test_an_email_resolves_case_insensitively(
        self, db: Database, services: ServiceBundle
    ) -> None:
        sarah = user(services, "sarah@example.com", "Sarah Okonjo")
        assert resolve(db, "SARAH@EXAMPLE.COM") == sarah.id

    def test_a_unique_display_name_resolves_case_insensitively(
        self, db: Database, services: ServiceBundle
    ) -> None:
        sarah = user(services, "sarah@example.com", "Sarah Okonjo")
        assert resolve(db, "sarah okonjo") == sarah.id
        assert resolve(db, "  Sarah Okonjo  ") == sarah.id

    def test_an_ambiguous_display_name_names_every_candidate_with_its_email(
        self, db: Database, services: ServiceBundle
    ) -> None:
        """Ambiguity is never resolved by picking. The caller is handed
        what distinguishes the candidates rather than a guess and a shrug."""
        one = user(services, "j.smith@example.com", "Jamie Smith")
        two = user(services, "jamie.s@example.com", "Jamie Smith")
        with pytest.raises(ValidationFailedError) as excinfo:
            resolve(db, "Jamie Smith")
        message = excinfo.value.message
        assert "j.smith@example.com" in message
        assert "jamie.s@example.com" in message
        assert {c["id"] for c in excinfo.value.details["candidates"]} == {one.id, two.id}

    def test_an_unknown_string_is_rejected_and_points_at_the_directory(
        self, db: Database, services: ServiceBundle
    ) -> None:
        with pytest.raises(ValidationFailedError) as excinfo:
            resolve(db, "Nobody At All")
        assert "find_principals" in excinfo.value.message

    def test_a_display_name_that_is_another_principals_id_resolves_as_an_id(
        self, db: Database, services: ServiceBundle
    ) -> None:
        """**The ordering, pinned.** If the branches were independent lookups rather than
        an order, this would be ambiguous or would resolve to the impostor."""
        target = user(services, "target@example.com", "Target Person")
        impostor = user(services, "impostor@example.com", target.id)
        assert resolve(db, target.id) == target.id
        assert resolve(db, target.id) != impostor.id

    def test_at_me_can_never_be_shadowed_by_a_display_name(
        self, db: Database, services: ServiceBundle
    ) -> None:
        user(services, "cheeky@example.com", "@me")
        assert resolve(db, "@me") == BOOTSTRAP_PRINCIPAL_ID

    def test_an_empty_reference_is_rejected(self, db: Database, services: ServiceBundle) -> None:
        with pytest.raises(ValidationFailedError):
            resolve(db, "")


class TestResolverModes:
    def test_an_inactive_principal_is_refused_by_default_and_allowed_for_a_read(
        self, db: Database, services: ServiceBundle
    ) -> None:
        gone = user(services, "gone@example.com", "Devon Null", active=False)
        with pytest.raises(ValidationFailedError) as excinfo:
            resolve(db, gone.id)
        assert "deactivated" in excinfo.value.message
        assert resolve(db, gone.id, allow_inactive=True) == gone.id

    def test_name_resolution_narrows_before_it_counts(
        self, db: Database, services: ServiceBundle
    ) -> None:
        """A name shared by one active and one deactivated principal is unambiguous on a
        write and ambiguous in a filter, because "the candidate set the mode considers"
        is not the same set."""
        here = user(services, "here@example.com", "Robin Ash")
        user(services, "left@example.com", "Robin Ash", active=False)
        assert resolve(db, "Robin Ash") == here.id
        with pytest.raises(ValidationFailedError):
            resolve(db, "Robin Ash", allow_inactive=True)

    def test_stored_value_lets_an_inactive_id_through_and_nothing_else(
        self, db: Database, services: ServiceBundle
    ) -> None:
        """The stored-value exception at the resolver. The exception is for *this* id,
        not for inactivity."""
        gone = user(services, "gone@example.com", "Devon Null", active=False)
        other = user(services, "other@example.com", "Other Gone", active=False)
        assert resolve(db, gone.id, stored_value=gone.id) == gone.id
        with pytest.raises(ValidationFailedError):
            resolve(db, other.id, stored_value=gone.id)


# ------------------------------------------------------------ the write path resolves


class TestWritePath:
    def test_create_by_email_and_by_name_stores_a_bare_id(self, typed: ServiceBundle) -> None:
        sarah = user(typed, "sarah@example.com", "Sarah Okonjo")
        by_email = typed.records.create_record(
            make_actor(), "initiative", {"title": "A", "owner": "sarah@example.com"}
        )
        by_name = typed.records.create_record(
            make_actor(), "initiative", {"title": "B", "owner": "Sarah Okonjo"}
        )
        assert by_email.data["owner"] == sarah.id
        assert by_name.data["owner"] == sarah.id

    def test_the_stored_value_is_a_bare_string_id(self, typed: ServiceBundle) -> None:
        """*(Fence on the shape.)* No migration, no coercion, no new stored shape: the
        column holds a bare principal id, as it always has."""
        sarah = user(typed, "sarah@example.com", "Sarah Okonjo")
        record = typed.records.create_record(
            make_actor(), "initiative", {"title": "A", "owner": "Sarah Okonjo"}
        )
        stored = record.data["owner"]
        assert isinstance(stored, str)
        assert stored == sarah.id

    def test_update_by_email_resolves(self, typed: ServiceBundle) -> None:
        sarah = user(typed, "sarah@example.com", "Sarah Okonjo")
        record = typed.records.create_record(make_actor(), "initiative", {"title": "A"})
        updated = typed.records.update_record(
            make_actor(), record.key, {"owner": "sarah@example.com"}
        )
        assert updated.data["owner"] == sarah.id

    def test_at_me_now_resolves_on_a_write_too(self, typed: ServiceBundle) -> None:
        record = typed.records.create_record(
            make_actor(), "initiative", {"title": "A", "owner": "@me"}
        )
        assert record.data["owner"] == BOOTSTRAP_PRINCIPAL_ID

    def test_the_audit_event_carries_the_resolved_id_not_the_name(
        self, typed: ServiceBundle
    ) -> None:
        """DD-4: the audit trail records what was stored."""
        sarah = user(typed, "sarah@example.com", "Sarah Okonjo")
        record = typed.records.create_record(make_actor(), "initiative", {"title": "A"})
        typed.records.update_record(make_actor(), record.key, {"owner": "Sarah Okonjo"})
        events = typed.records.get_record_history(make_actor(), record.key)
        owner_events = [e for e in events if e.field_key == "owner"]
        assert [e.new_value for e in owner_events] == [sarah.id]

    def test_bulk_update_resolves_too(self, typed: ServiceBundle) -> None:
        sarah = user(typed, "sarah@example.com", "Sarah Okonjo")
        typed.records.create_record(make_actor(), "initiative", {"title": "A"})
        typed.records.create_record(make_actor(), "initiative", {"title": "B"})
        result = typed.records.bulk_update(
            make_actor(), "initiative", {"owner": "sarah@example.com"}, filter={}
        )
        assert result.affected_count == 2
        page = typed.records.query_records(make_actor(), "initiative", fields="*")
        assert {r["data"]["owner"] for r in page.records} == {sarah.id}

    def test_an_ambiguous_name_on_a_write_names_the_field_and_the_candidates(
        self, typed: ServiceBundle
    ) -> None:
        user(typed, "j.smith@example.com", "Jamie Smith")
        user(typed, "jamie.s@example.com", "Jamie Smith")
        with pytest.raises(ValidationFailedError) as excinfo:
            typed.records.create_record(
                make_actor(), "initiative", {"title": "A", "owner": "Jamie Smith"}
            )
        assert excinfo.value.field_key == "owner"
        assert len(excinfo.value.details["candidates"]) == 2


# --------------------------------------------------------- the filter compiler resolves


class TestFilters:
    @pytest.fixture
    def seeded(self, typed: ServiceBundle) -> dict[str, Any]:
        sarah = user(typed, "sarah@example.com", "Sarah Okonjo")
        gone = user(typed, "gone@example.com", "Devon Null")
        hers = typed.records.create_record(
            make_actor(), "initiative", {"title": "Hers", "owner": sarah.id}
        )
        theirs = typed.records.create_record(
            make_actor(), "initiative", {"title": "Theirs", "owner": gone.id}
        )
        typed.principals.deactivate_principal(make_actor(), gone.id)
        return {"sarah": sarah, "gone": gone, "hers": hers, "theirs": theirs}

    def keys(self, services: ServiceBundle, value: Any, op: str = "eq") -> set[str]:
        result = services.records.query_records(
            make_actor(), "initiative", filter={"field": "owner", "op": op, "value": value}
        )
        return {r["key"] for r in result.records}

    def test_filter_by_email(self, typed: ServiceBundle, seeded: dict[str, Any]) -> None:
        assert self.keys(typed, "sarah@example.com") == {seeded["hers"].key}

    def test_filter_by_display_name(self, typed: ServiceBundle, seeded: dict[str, Any]) -> None:
        assert self.keys(typed, "Sarah Okonjo") == {seeded["hers"].key}

    def test_filter_created_by_pseudo_field_by_name(
        self, typed: ServiceBundle, seeded: dict[str, Any]
    ) -> None:
        """``created_by`` is a ``user_ref`` pseudo-field, so resolution reaches it
        even though it compiles to a column and carries no field config at all."""
        result = typed.records.query_records(
            make_actor(),
            "initiative",
            filter={"field": "created_by", "op": "eq", "value": "@me"},
        )
        assert {r["key"] for r in result.records} == {
            seeded["hers"].key,
            seeded["theirs"].key,
        }
        bootstrap = typed.principals.get_principal(BOOTSTRAP_PRINCIPAL_ID)
        by_name = typed.records.query_records(
            make_actor(),
            "initiative",
            filter={"field": "created_by", "op": "eq", "value": bootstrap.display_name},
        )
        assert {r["key"] for r in by_name.records} == {
            seeded["hers"].key,
            seeded["theirs"].key,
        }

    def test_at_me_is_unchanged(self, typed: ServiceBundle, seeded: dict[str, Any]) -> None:
        """*(Fence.)* FR-R9's behaviour is not what name resolution alters."""
        assert self.keys(typed, "@me") == set()

    def test_every_member_of_an_in_list_resolves(
        self, typed: ServiceBundle, seeded: dict[str, Any]
    ) -> None:
        assert self.keys(typed, ["sarah@example.com", "Devon Null"], op="in") == {
            seeded["hers"].key,
            seeded["theirs"].key,
        }

    def test_an_ambiguous_name_in_a_filter_is_validation_failed(
        self, typed: ServiceBundle, seeded: dict[str, Any]
    ) -> None:
        user(typed, "j.smith@example.com", "Jamie Smith")
        user(typed, "jamie.s@example.com", "Jamie Smith")
        with pytest.raises(ValidationFailedError) as excinfo:
            self.keys(typed, "Jamie Smith")
        assert len(excinfo.value.details["candidates"]) == 2

    def test_an_unknown_name_is_validation_failed_not_an_empty_result(
        self, typed: ServiceBundle, seeded: dict[str, Any]
    ) -> None:
        """The point of the whole branch: a filter that silently returns zero rows is the
        worst possible answer to a typo."""
        with pytest.raises(ValidationFailedError):
            self.keys(typed, "Nobody At All")

    def test_a_deactivated_principals_name_still_resolves_in_a_filter(
        self, typed: ServiceBundle, seeded: dict[str, Any]
    ) -> None:
        """The handover query. The same name on a write is refused, next."""
        assert self.keys(typed, "Devon Null") == {seeded["theirs"].key}

    def test_the_same_deactivated_name_is_refused_on_a_write(
        self, typed: ServiceBundle, seeded: dict[str, Any]
    ) -> None:
        with pytest.raises(ValidationFailedError):
            typed.records.create_record(
                make_actor(), "initiative", {"title": "New", "owner": "Devon Null"}
            )


# ------------------------------------------------------------------- CSV import inherits


class TestCsvImport:
    """No code of its own: ``user_ref`` stays in ``_AS_IS_TYPES`` and the resolution
    arrives through ``create_record`` / ``update_record``."""

    def test_import_resolves_an_owner_column_of_emails(self, typed: ServiceBundle) -> None:
        sarah = user(typed, "sarah@example.com", "Sarah Okonjo")
        csv_text = "title,owner\nFrom CSV,sarah@example.com\n"
        result = typed.csv.import_csv(make_actor(), "initiative", csv_text, mode="create")
        assert result.errors == []
        page = typed.records.query_records(make_actor(), "initiative", fields="*")
        assert {r["data"]["owner"] for r in page.records} == {sarah.id}

    def test_import_resolves_an_owner_column_of_names(self, typed: ServiceBundle) -> None:
        sarah = user(typed, "sarah@example.com", "Sarah Okonjo")
        csv_text = "title,owner\nFrom CSV,Sarah Okonjo\n"
        result = typed.csv.import_csv(make_actor(), "initiative", csv_text, mode="create")
        assert result.errors == []
        page = typed.records.query_records(make_actor(), "initiative", fields="*")
        assert {r["data"]["owner"] for r in page.records} == {sarah.id}


# -------------------------------------------------------------- the stored-value exception


class TestTheStoredValueException:
    def test_an_unchanged_inactive_value_re_submits_successfully(
        self, typed: ServiceBundle
    ) -> None:
        """A PATCH that *names* the field must not fail once the referent is deactivated,
        because that is what the detail card's per-field Edit does on every save."""
        gone = user(typed, "gone@example.com", "Devon Null")
        record = typed.records.create_record(
            make_actor(), "initiative", {"title": "A", "owner": gone.id}
        )
        typed.principals.deactivate_principal(make_actor(), gone.id)
        updated = typed.records.update_record(
            make_actor(), record.key, {"title": "A2", "owner": gone.id}
        )
        assert updated.data["owner"] == gone.id
        assert updated.data["title"] == "A2"

    def test_setting_an_inactive_principal_afresh_is_refused_on_create(
        self, typed: ServiceBundle
    ) -> None:
        gone = user(typed, "gone@example.com", "Devon Null", active=False)
        with pytest.raises(ValidationFailedError):
            typed.records.create_record(
                make_actor(), "initiative", {"title": "A", "owner": gone.id}
            )

    def test_setting_an_inactive_principal_afresh_is_refused_on_bulk_update(
        self, typed: ServiceBundle
    ) -> None:
        gone = user(typed, "gone@example.com", "Devon Null", active=False)
        typed.records.create_record(make_actor(), "initiative", {"title": "A"})
        with pytest.raises(ValidationFailedError):
            typed.records.bulk_update(make_actor(), "initiative", {"owner": gone.id}, filter={})

    def test_name_and_email_resolution_on_a_write_never_returns_an_inactive_principal(
        self, typed: ServiceBundle
    ) -> None:
        gone = user(typed, "gone@example.com", "Devon Null", active=False)
        for reference in ("gone@example.com", "Devon Null"):
            with pytest.raises(ValidationFailedError):
                typed.records.create_record(
                    make_actor(), "initiative", {"title": "A", "owner": reference}
                )
        assert gone.id  # the principal exists; it is the *assignment* that is refused

    def test_export_and_re_import_round_trips_after_someone_leaves(
        self, typed: ServiceBundle
    ) -> None:
        """What the stored-value exception is actually for. Export writes the id, and the
        re-import re-submits it onto the same record, so it resolves through the stored
        value even though the principal has since been deactivated."""
        gone = user(typed, "gone@example.com", "Devon Null")
        record = typed.records.create_record(
            make_actor(), "initiative", {"title": "A", "owner": gone.id}
        )
        exported = typed.csv.export_csv(make_actor(), "initiative")
        assert gone.id in exported
        typed.principals.deactivate_principal(make_actor(), gone.id)

        result = typed.csv.import_csv(
            make_actor(), "initiative", exported, mode="upsert", upsert_key="key"
        )
        assert result.errors == []
        refreshed = typed.records.get_record(make_actor(), record.key)
        assert refreshed.data["owner"] == gone.id


# ---------------------------------------------------------------- service accounts refused


class TestServiceAccountsRefused:
    @pytest.fixture
    def strict(self, services: ServiceBundle) -> ServiceBundle:
        seed_type(services, {"allow_service_accounts": False})
        return services

    def test_a_service_account_is_refused_by_id(self, strict: ServiceBundle) -> None:
        robot = service_account(strict, "Nightly Importer")
        with pytest.raises(ValidationFailedError) as excinfo:
            strict.records.create_record(
                make_actor(), "initiative", {"title": "A", "owner": robot.id}
            )
        assert "service account" in excinfo.value.message

    def test_a_service_account_is_refused_by_name(self, strict: ServiceBundle) -> None:
        service_account(strict, "Nightly Importer")
        with pytest.raises(ValidationFailedError):
            strict.records.create_record(
                make_actor(), "initiative", {"title": "A", "owner": "Nightly Importer"}
            )

    def test_a_human_is_still_accepted_on_a_strict_field(self, strict: ServiceBundle) -> None:
        sarah = user(strict, "sarah@example.com", "Sarah Okonjo")
        record = strict.records.create_record(
            make_actor(), "initiative", {"title": "A", "owner": "Sarah Okonjo"}
        )
        assert record.data["owner"] == sarah.id

    def test_fence_the_default_still_accepts_a_service_account(self, typed: ServiceBundle) -> None:
        """*(Fence.)* A field with no ``allow_service_accounts`` key, which every field
        created before that key existed is, accepts a service account."""
        robot = service_account(typed, "Nightly Importer")
        record = typed.records.create_record(
            make_actor(), "initiative", {"title": "A", "owner": robot.id}
        )
        assert record.data["owner"] == robot.id

    def test_an_explicit_true_accepts_a_service_account(self, services: ServiceBundle) -> None:
        seed_type(services, {"allow_service_accounts": True})
        robot = service_account(services, "Nightly Importer")
        record = services.records.create_record(
            make_actor(), "initiative", {"title": "A", "owner": robot.id}
        )
        assert record.data["owner"] == robot.id

    def test_a_created_by_filter_never_consults_it(self, services: ServiceBundle) -> None:
        """A pseudo-field has no config, so a strict ``owner`` field on the same type
        cannot make ``created_by`` reject the service account that wrote the record."""
        seed_type(services, {"allow_service_accounts": False})
        robot = service_account(services, "Nightly Importer")
        services.access.grant(make_actor(), "initiative", robot.id, "write")
        robot_actor = replace(make_actor(), principal_id=robot.id)
        record = services.records.create_record(robot_actor, "initiative", {"title": "A"})
        found = services.records.query_records(
            make_actor(),
            "initiative",
            filter={"field": "created_by", "op": "eq", "value": "Nightly Importer"},
        )
        assert {r["key"] for r in found.records} == {record.key}
