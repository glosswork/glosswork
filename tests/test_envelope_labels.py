"""Audit and comment envelopes carry human labels (DD-25).

One defect in two places: an envelope hands the frontend a foreign id where a human label
belongs. ``audit_event_doc`` would render a principal UUID, and an audit row cannot say which
*record* it is about because it carries only ``record_id``.

DD-25 resolves both at the repository layer, in the same query that reads the row, so the
serializers stay pure row-to-dict functions and one page of events costs one query rather than N.
The joins are ``LEFT``: an event whose principal or record has since been deleted still returns,
with a null label the frontend renders as the raw id it shows today. That case is asserted here
rather than assumed.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from glosswork.actor import ActorContext
from glosswork.serializers import audit_event_doc, comment_doc
from glosswork.services import ServiceBundle
from tests.conftest import auth, make_actor

ARTIFACT_FIELDS: list[dict[str, Any]] = [
    {
        "key": "title",
        "name": "Title",
        "type": "short_text",
        "description": "Short human-readable name for the artifact.",
    },
]


@pytest.fixture
def seeded(services: ServiceBundle, actor: ActorContext) -> tuple[ServiceBundle, str, str]:
    """One artifact type, one record, one comment, written by a real named principal."""
    services.schema.create_object_type(
        actor,
        key="artifact",
        name="Artifact",
        name_plural="Artifacts",
        description="A tracked work artifact used by the test suite.",
        key_prefix="ART",
        fields=ARTIFACT_FIELDS,
    )
    author = services.principals.create_user(
        actor,
        email="dana@example.com",
        display_name="Dana Okonkwo",
        role="member",
    )
    writer = ActorContext(
        principal_id=author.id,
        principal_type="user",
        agent_label_id=None,
        auth_method="pat",
        surface="api",
        request_id="req-seed",
        scope="write",
    )
    # Object types are closed by default, so the named principal this suite writes
    # as needs a grant. The subject here is envelope labels, not authorization; the
    # grant is fixture setup for the new model, not the assertion being relaxed.
    services.access.grant(actor, "artifact", author.id, "write")
    record = services.records.create_record(writer, "artifact", {"title": "First artifact."})
    comment = services.comments.add_comment(writer, record.key, "A note about this artifact.")
    return services, record.key, comment.id


def test_audit_events_carry_the_principals_display_name(
    seeded: tuple[ServiceBundle, str, str],
) -> None:
    """The audit envelope resolves ``principal_id`` to a name (DD-25)."""
    services, record_key, _ = seeded
    events = services.records.get_record_history(make_actor(), record_key)
    assert events, "the record's creation should have produced audit events"
    assert {e.principal_display_name for e in events} == {"Dana Okonkwo"}


def test_audit_events_carry_the_record_key(seeded: tuple[ServiceBundle, str, str]) -> None:
    """The audit envelope resolves ``record_id`` to the record's key (DD-25)."""
    services, record_key, _ = seeded
    events = services.records.get_record_history(make_actor(), record_key)
    assert {e.record_key for e in events} == {record_key}


def test_audit_search_carries_both_labels(seeded: tuple[ServiceBundle, str, str]) -> None:
    """The audit browser reads through ``search``, not ``for_record``; it needs both joins too."""
    services, record_key, _ = seeded
    result = services.audit.search(make_actor(), record=record_key)
    assert result.events
    for event in result.events:
        assert event.principal_display_name == "Dana Okonkwo"
        assert event.record_key == record_key


def test_comments_carry_the_authors_display_name(seeded: tuple[ServiceBundle, str, str]) -> None:
    """The other half: without the label, a comment's author line is a UUID."""
    services, record_key, _ = seeded
    comments = services.comments.list_comments(make_actor(), record_key)
    assert [c.principal_display_name for c in comments] == ["Dana Okonkwo"]


def test_a_freshly_added_comment_already_carries_its_authors_name(
    seeded: tuple[ServiceBundle, str, str],
) -> None:
    """The add path returns the row it just wrote, so it must be read back through the join —
    otherwise the thread renders a raw id until the next refetch."""
    services, record_key, _ = seeded
    principal = next(
        p for p in services.principals.list_principals() if p.display_name == "Dana Okonkwo"
    )
    actor = ActorContext(
        principal_id=principal.id,
        principal_type="user",
        agent_label_id=None,
        auth_method="pat",
        surface="api",
        request_id="req-add",
        scope="write",
    )
    created = services.comments.add_comment(actor, record_key, "A second note.")
    assert created.principal_display_name == principal.display_name


def test_an_event_with_no_record_still_returns_with_a_null_record_key(
    seeded: tuple[ServiceBundle, str, str],
) -> None:
    """The record join must be LEFT, and this is the case that proves it.

    LEFT was first justified by "a referent that has since been deleted". Read against the schema
    rather than inferred, that case is not the reachable one: ``audit_events.principal_id`` is
    ``NOT NULL REFERENCES principals(id)``, so a dangling principal cannot exist, and records
    are soft-deleted, so their row stays. What *is* reachable — and common — is
    ``record_id IS NULL``: every schema-level event has one. An INNER JOIN would drop all of
    them from every audit-browser page silently.
    """
    services, record_key, _ = seeded
    result = services.audit.search(make_actor(), limit=100)
    schema_events = [e for e in result.events if e.record_id is None]
    assert schema_events, "creating the object type should have produced record-less events"
    assert {e.record_key for e in schema_events} == {None}
    # And they still carry a principal label, so the null is only in the half that is absent.
    assert all(e.principal_display_name is not None for e in schema_events)


def test_a_soft_deleted_record_still_resolves_its_key(
    seeded: tuple[ServiceBundle, str, str],
) -> None:
    """Deleting a record is the product's own path; its audit trail must stay readable."""
    services, record_key, _ = seeded
    actor = make_actor()
    services.records.delete_record(actor, record_key)
    result = services.audit.search(actor, record=record_key, limit=100)
    assert result.events
    assert {e.record_key for e in result.events} == {record_key}


def test_a_deactivated_principal_still_resolves_its_display_name(
    seeded: tuple[ServiceBundle, str, str],
) -> None:
    """Deactivation, not deletion, is how a principal goes away here (FR-I3), and the row
    stays — so the label survives and the audit trail keeps naming who did what."""
    services, record_key, _ = seeded
    author = next(
        p for p in services.principals.list_principals() if p.display_name == "Dana Okonkwo"
    )
    services.principals.deactivate_principal(make_actor(), author.id)
    result = services.audit.search(make_actor(), principal_id=author.id, limit=100)
    assert result.events
    assert {e.principal_display_name for e in result.events} == {"Dana Okonkwo"}


def test_the_envelopes_expose_both_new_fields(seeded: tuple[ServiceBundle, str, str]) -> None:
    """`audit_event_doc` and `comment_doc` add the fields; nothing is removed, so every
    existing consumer keeps working."""
    services, record_key, _ = seeded
    event = services.records.get_record_history(make_actor(), record_key)[0]
    doc = audit_event_doc(event)
    assert doc["principal_display_name"] == "Dana Okonkwo"
    assert doc["record_key"] == record_key

    comment = services.comments.list_comments(make_actor(), record_key)[0]
    assert comment_doc(comment)["principal_display_name"] == "Dana Okonkwo"


def test_the_rest_surface_carries_the_labels(
    client: TestClient, api_tokens: dict[str, str], app_services: ServiceBundle
) -> None:
    """Parity check over HTTP: the same fields reach the browser (FR-U8, FR-C1)."""
    actor = make_actor()
    app_services.schema.create_object_type(
        actor,
        key="artifact",
        name="Artifact",
        name_plural="Artifacts",
        description="A tracked work artifact used by the test suite.",
        key_prefix="ART",
        fields=ARTIFACT_FIELDS,
    )
    created = client.post(
        "/api/v1/object-types/artifact/records",
        json={"title": "Over HTTP."},
        headers=auth(api_tokens["write"]),
    )
    assert created.status_code in (200, 201), created.text
    key = created.json()["key"]

    posted = client.post(
        f"/api/v1/records/{key}/comments",
        json={"body": "A note over HTTP."},
        headers=auth(api_tokens["write"]),
    )
    assert posted.status_code in (200, 201), posted.text
    assert posted.json()["principal_display_name"] is not None

    history = client.get(f"/api/v1/records/{key}/history", headers=auth(api_tokens["read"]))
    assert history.status_code == 200, history.text
    events = history.json()["events"]
    assert events
    assert all(e["record_key"] == key for e in events)
    assert all(e["principal_display_name"] is not None for e in events)

    comments = client.get(f"/api/v1/records/{key}/comments", headers=auth(api_tokens["read"]))
    assert comments.status_code == 200, comments.text
    assert all(c["principal_display_name"] is not None for c in comments.json()["comments"])


# ------------------------------------------------------------------ agent labels (DD-25)
#
# The same defect, one layer over: an envelope hands the frontend an agent-label UUID where a
# human label belongs. DD-25's first fix covered only the principal half, because before
# DD-17 a label only ever reached an audit row over ``/mcp``. It reaches every
# surface now, so the id is rendered at three sites in the SPA and resolves at none.
#
# Resolved the same way and for the same reasons: a LEFT JOIN in the repository's own read, so
# the serializers stay pure and one page of events stays one query. LEFT because an absent label
# is the *common* case here, not the exceptional one -- every write by a person at a keyboard
# has none.


@pytest.fixture
def labeled(services: ServiceBundle, actor: ActorContext) -> tuple[ServiceBundle, str, str]:
    """One artifact type and one record written under an agent label, plus a labeled comment.

    The label is registered through the ordinary resolver rather than inserted, so the row
    under test is the one the product actually writes (DD-17).
    """
    services.schema.create_object_type(
        actor,
        key="artifact",
        name="Artifact",
        name_plural="Artifacts",
        description="A tracked work artifact used by the test suite.",
        key_prefix="ART",
        fields=ARTIFACT_FIELDS,
    )
    author = services.principals.create_user(
        actor,
        email="dana@example.com",
        display_name="Dana Okonkwo",
        role="member",
    )
    services.access.grant(actor, "artifact", author.id, "write")
    label = services.agent_labels.register_use(author.id, "sales-agent")
    writer = ActorContext(
        principal_id=author.id,
        principal_type="user",
        agent_label_id=label.id,
        auth_method="pat",
        surface="api",
        request_id="req-agent",
        scope="write",
    )
    record = services.records.create_record(writer, "artifact", {"title": "Written by an agent."})
    services.comments.add_comment(writer, record.key, "An agent's note.")
    return services, record.key, author.id


def test_audit_events_carry_the_agent_labels_text(
    labeled: tuple[ServiceBundle, str, str],
) -> None:
    """The audit envelope resolves ``agent_label_id`` to the label a person can read."""
    services, record_key, _ = labeled
    events = services.records.get_record_history(make_actor(), record_key)
    assert events
    assert {e.agent_label for e in events} == {"sales-agent"}


def test_comments_carry_the_agent_labels_text(labeled: tuple[ServiceBundle, str, str]) -> None:
    """The comment header renders ``(agent: <uuid>)`` today (CommentItem.tsx:69)."""
    services, record_key, _ = labeled
    comments = services.comments.list_comments(make_actor(), record_key)
    assert [c.agent_label for c in comments] == ["sales-agent"]


def test_an_unlabeled_write_resolves_to_no_label_rather_than_a_blank(
    seeded: tuple[ServiceBundle, str, str],
) -> None:
    """The join is LEFT and the absent case is the common one: a person at a keyboard.

    ``None`` rather than ``""`` because DESIGN.md 6.5 turns on the distinction -- the primitive
    renders no agent at all, and never fabricates one.
    """
    services, record_key, _ = seeded
    events = services.records.get_record_history(make_actor(), record_key)
    assert events
    assert {e.agent_label for e in events} == {None}
    comments = services.comments.list_comments(make_actor(), record_key)
    assert [c.agent_label for c in comments] == [None]


def test_the_envelopes_expose_the_agent_label(labeled: tuple[ServiceBundle, str, str]) -> None:
    """Nothing is removed from either envelope, so every existing consumer keeps working."""
    services, record_key, _ = labeled
    event = services.records.get_record_history(make_actor(), record_key)[0]
    doc = audit_event_doc(event)
    assert doc["agent_label"] == "sales-agent"
    assert doc["agent_label_id"] is not None

    comment = services.comments.list_comments(make_actor(), record_key)[0]
    assert comment_doc(comment)["agent_label"] == "sales-agent"


def test_audit_search_carries_the_agent_label(labeled: tuple[ServiceBundle, str, str]) -> None:
    """The audit browser reads through ``search``; it needs the third join too."""
    services, record_key, _ = labeled
    result = services.audit.search(make_actor(), record=record_key)
    assert result.events
    assert {e.agent_label for e in result.events} == {"sales-agent"}
