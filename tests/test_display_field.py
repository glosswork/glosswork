"""DD-23: a record's label is chosen on the object type, with the position-derived
rule as its fallback (FR-S11).

Read alongside DD-23 in `docs/DESIGN_DECISIONS.md`. The fallback tests are labeled as
**scope fences**: they assert behavior that choosing a label deliberately preserved, so
they cannot fail against a tree without the choice and do not count as its coverage.
"""

from __future__ import annotations

from typing import Any

import pytest

from glosswork.actor import ActorContext
from glosswork.db import Database
from glosswork.errors import ForbiddenError, ValidationFailedError
from glosswork.fieldtypes import FIELD_TYPES, is_display_eligible
from glosswork.repositories.models import FieldDef
from glosswork.services import ServiceBundle
from glosswork.services.base import display_field
from tests.conftest import make_actor
from tests.mcp_support import memory_session, structured

# ------------------------------------------------------------------ helpers


def _field(key: str, field_type: str, position: int) -> FieldDef:
    """A bare ``FieldDef`` for the pure-helper tests; only key, type and position
    are read by ``display_field``."""
    return FieldDef(
        id=f"id-{key}",
        object_type_id="t",
        key=key,
        name=key.title(),
        description=f"The {key} field.",
        type=field_type,
        position=position,
        is_required=False,
        is_unique=False,
        is_indexed=False,
        embed=False,
        default_value=None,
        config={},
        is_deleted=False,
        created_at="2026-09-03T00:00:00Z",
        created_by="p",
        updated_at="2026-09-03T00:00:00Z",
        updated_by="p",
    )


def _spec(key: str, field_type: str = "short_text", **extra: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "key": key,
        "name": key.replace("_", " ").title(),
        "type": field_type,
        "description": f"The {key} field, described for the agents that read this schema.",
    }
    spec.update(extra)
    return spec


def _make_type(
    services: ServiceBundle,
    key: str,
    *,
    fields: list[dict[str, Any]] | None = None,
    display_field_key: str | None = None,
    prefix: str | None = None,
) -> Any:
    return services.schema.create_object_type(
        make_actor(),
        key=key,
        name=key.title(),
        name_plural=f"{key.title()}s",
        description=f"The {key} type, seeded by the display field tests.",
        key_prefix=(prefix or key[:4].upper()),
        fields=fields,
        display_field_key=display_field_key,
    )


# --------------------------------------------- the column round-trips


def test_a_created_type_round_trips_a_set_display_field_key(services: ServiceBundle) -> None:
    """The column is written on the CREATE and read back off the row."""
    _make_type(
        services,
        "widget",
        fields=[_spec("code"), _spec("label")],
        display_field_key="label",
    )
    object_type, _ = services.schema.get_object_type(make_actor(), "widget")
    assert object_type.display_field_key == "label"


def test_a_created_type_round_trips_a_null_display_field_key(services: ServiceBundle) -> None:
    """A type with no fields has nothing to label a record with yet."""
    _make_type(services, "widget", fields=[])
    object_type, _ = services.schema.get_object_type(make_actor(), "widget")
    assert object_type.display_field_key is None


# ------------------------------------------ the eligibility predicate


def test_is_display_eligible_pins_the_whole_field_type_vocabulary() -> None:
    """Pinned exhaustively over ``FIELD_TYPES`` so a fourteenth field type
    is a failure *here*, where the decision belongs, rather than a silent default
    somewhere downstream."""
    eligible = {t for t in FIELD_TYPES if is_display_eligible(t)}
    assert eligible == {
        "short_text",
        "long_text",
        "integer",
        "decimal",
        "boolean",
        "date",
        "datetime",
        "single_select",
        "multi_select",
        "url",
    }
    assert {t for t in FIELD_TYPES if not is_display_eligible(t)} == {
        "relation",
        "attachment",
        "user_ref",
    }
    assert len(FIELD_TYPES) == 13


# ------------------------------------ display_field honors and falls back


def test_a_chosen_key_wins_over_the_derived_rule() -> None:
    """The chosen field is not the first by position, so the two rules
    disagree and only the chosen one can produce this answer."""
    fields = {"code": _field("code", "short_text", 0), "label": _field("label", "short_text", 1)}
    assert display_field(fields, "label").key == "label"


def test_a_null_key_falls_back_to_the_first_non_relation_field() -> None:
    """**Scope fence**: today's behavior, unchanged. Cannot fail against a tree
    without the choice and is not counted."""
    fields = {"code": _field("code", "short_text", 0), "label": _field("label", "short_text", 1)}
    assert display_field(fields).key == "code"
    assert display_field(fields, None).key == "code"


def test_a_type_with_only_relation_fields_has_no_display_field() -> None:
    """**Scope fence**: today's behavior, unchanged."""
    assert display_field({"parent": _field("parent", "relation", 0)}) is None


def test_a_dangling_key_falls_back_rather_than_raising() -> None:
    """The reference is a field key with no FK, so it can name a field that is gone;
    a record must always be labelable."""
    fields = {"code": _field("code", "short_text", 0)}
    assert display_field(fields, "deleted_field").key == "code"


def test_a_key_naming_a_live_but_ineligible_field_falls_back() -> None:
    """Live, present, and unusable: an ``attachment`` cannot label a record,
    so the derived rule answers instead. The attachment sits at position 1 so the
    fallback's answer differs from the rejected key -- at position 0 the fallback
    would return the attachment itself and the assertion could not fail."""
    fields = {
        "label": _field("label", "short_text", 0),
        "blob": _field("blob", "attachment", 1),
    }
    assert display_field(fields, "blob").key == "label"


def test_the_fallback_is_first_non_relation_not_first_eligible() -> None:
    """A distinction not to collapse. The creation *default* skips ineligible fields;
    the *fallback* is the position rule untouched, so an existing type whose first
    field is an ``attachment`` labels its records exactly as it did."""
    fields = {
        "blob": _field("blob", "attachment", 0),
        "label": _field("label", "short_text", 1),
    }
    assert display_field(fields).key == "blob"


# ------------------------------------------------- set at creation


def test_display_field_override(services: ServiceBundle) -> None:
    """The chosen key is *not* the type's first field, so a derived answer and a
    chosen one differ observably."""
    _make_type(
        services,
        "widget",
        fields=[_spec("code"), _spec("label")],
        display_field_key="label",
    )
    object_type, fields = services.schema.get_object_type(make_actor(), "widget")
    resolved = display_field({f.key: f for f in fields}, object_type.display_field_key)
    assert resolved is not None and resolved.key == "label"


def test_an_omitted_key_defaults_to_the_first_field(services: ServiceBundle) -> None:
    """An omitted key defaults to the first field."""
    _make_type(services, "widget", fields=[_spec("code"), _spec("label")])
    object_type, _ = services.schema.get_object_type(make_actor(), "widget")
    assert object_type.display_field_key == "code"


def test_an_omitted_key_skips_a_leading_relation_field(services: ServiceBundle) -> None:
    """Picks the "first eligible" field rather than the "first", so a type whose first
    declared field is a relation gets a usable default instead of an immediately-dangling
    one."""
    _make_type(services, "target", fields=[_spec("name")], prefix="TGT")
    _make_type(
        services,
        "widget",
        fields=[
            _spec(
                "parent",
                "relation",
                config={
                    "target_type_key": "target",
                    "cardinality": "one",
                    "inverse_field_key": "widgets",
                },
            ),
            _spec("label"),
        ],
    )
    object_type, _ = services.schema.get_object_type(make_actor(), "widget")
    assert object_type.display_field_key == "label"


def test_an_omitted_key_with_no_fields_stores_null(services: ServiceBundle) -> None:
    """An omitted key on a type with no fields stores null."""
    _make_type(services, "widget", fields=None)
    object_type, _ = services.schema.get_object_type(make_actor(), "widget")
    assert object_type.display_field_key is None


def test_an_unknown_key_at_creation_is_validation_failed_listing_eligible_keys(
    services: ServiceBundle,
) -> None:
    """The error names what *would* have worked, not merely what did not."""
    with pytest.raises(ValidationFailedError) as excinfo:
        _make_type(
            services,
            "widget",
            fields=[_spec("code"), _spec("label")],
            display_field_key="nope",
        )
    assert "code" in excinfo.value.message and "label" in excinfo.value.message


@pytest.mark.parametrize(
    ("field_type", "extra"),
    [
        (
            "relation",
            {
                "config": {
                    "target_type_key": "target",
                    "cardinality": "one",
                    "inverse_field_key": "widgets",
                }
            },
        ),
        ("attachment", {}),
        ("user_ref", {}),
    ],
)
def test_an_ineligible_key_at_creation_is_validation_failed(
    services: ServiceBundle, field_type: str, extra: dict[str, Any]
) -> None:
    """All three exclusions, each for its own reason."""
    _make_type(services, "target", fields=[_spec("name")], prefix="TGT")
    with pytest.raises(ValidationFailedError) as excinfo:
        _make_type(
            services,
            "widget",
            fields=[_spec("bad", field_type, **extra), _spec("label")],
            display_field_key="bad",
        )
    assert "label" in excinfo.value.message


# --------------------------------------------- changed afterwards


def test_update_object_type_sets_changes_and_clears_the_display_field_key(
    services: ServiceBundle,
) -> None:
    """Set, change, and clear, in one narrative: an explicit ``null``
    returns the type to the derived rule."""
    _make_type(services, "widget", fields=[_spec("code"), _spec("label"), _spec("alt")])
    actor = make_actor()

    updated = services.schema.update_object_type(actor, "widget", {"display_field_key": "label"})
    assert updated.display_field_key == "label"

    updated = services.schema.update_object_type(actor, "widget", {"display_field_key": "alt"})
    assert updated.display_field_key == "alt"

    updated = services.schema.update_object_type(actor, "widget", {"display_field_key": None})
    assert updated.display_field_key is None
    _, fields = services.schema.get_object_type(actor, "widget")
    assert display_field({f.key: f for f in fields}, None).key == "code"


def test_update_object_type_rejects_an_unknown_display_field_key(
    services: ServiceBundle,
) -> None:
    """Validation resolves against the type's **live** fields."""
    _make_type(services, "widget", fields=[_spec("code")])
    with pytest.raises(ValidationFailedError):
        services.schema.update_object_type(make_actor(), "widget", {"display_field_key": "ghost"})


def test_update_object_type_rejects_an_ineligible_display_field_key(
    services: ServiceBundle,
) -> None:
    """An ineligible field is refused on update as it is at creation."""
    _make_type(services, "widget", fields=[_spec("code"), _spec("blob", "attachment")])
    with pytest.raises(ValidationFailedError) as excinfo:
        services.schema.update_object_type(make_actor(), "widget", {"display_field_key": "blob"})
    assert "code" in excinfo.value.message


def test_setting_the_display_field_key_is_audited_with_old_and_new(
    services: ServiceBundle, db: Database
) -> None:
    """DD-4. The change appears in ``audit_events`` with the
    prior value beside the new one, through the dicts ``update_object_type`` already
    builds."""
    _make_type(services, "widget", fields=[_spec("code"), _spec("label")])
    services.schema.update_object_type(make_actor(), "widget", {"display_field_key": "label"})
    events = services.audit.search(make_actor(), object_type="widget").events
    updates = [e for e in events if e.entity_type == "object_type" and e.action == "update"]
    assert updates, "the display field change must be audited"
    assert updates[0].old_value == {"display_field_key": "code"}
    assert updates[0].new_value == {"display_field_key": "label"}


# ------------------------------- released when its field goes


def test_approving_a_delete_field_proposal_nulls_a_matching_display_field_key(
    services: ServiceBundle,
) -> None:
    """The column holds a key rather than an id, which buys the ordering in
    ``create_object_type`` and costs exactly this null-out. The type falls back to
    the derived rule -- the behavior of a type that never chose a field."""
    _make_type(services, "widget", fields=[_spec("code"), _spec("label")])
    actor = make_actor()
    services.schema.update_object_type(actor, "widget", {"display_field_key": "label"})

    proposal = services.schema.propose_schema_change(
        actor, change_type="delete_field", object_type_key="widget", field_key="label"
    )
    services.schema.approve_proposal(make_actor(), proposal.id)

    object_type, fields = services.schema.get_object_type(make_actor(), "widget")
    assert object_type.display_field_key is None
    resolved = display_field({f.key: f for f in fields}, object_type.display_field_key)
    assert resolved is not None and resolved.key == "code"


# ---------------------------- the value reaches both relation paths


@pytest.fixture
def linked_pair(services: ServiceBundle) -> Any:
    """A ``ticket`` linked to one ``owner``, where the owner type's display field is
    deliberately **not** its first: `code` comes first by position, `label` is chosen.
    A derived answer and a chosen one therefore differ, which is what makes the
    assertions below able to fail."""
    _make_type(
        services,
        "owner",
        fields=[_spec("code"), _spec("label")],
        display_field_key="label",
        prefix="OWN",
    )
    _make_type(
        services,
        "ticket",
        fields=[
            _spec("title"),
            _spec(
                "owned_by",
                "relation",
                config={"target_type_key": "owner", "cardinality": "many"},
            ),
        ],
        prefix="TIC",
    )
    owner = services.records.create_record(
        make_actor(), "owner", {"code": "OWN-A", "label": "Atlas platform team"}
    )
    ticket = services.records.create_record(make_actor(), "ticket", {"title": "Migrate"})
    services.records.link_records(make_actor(), ticket.key, "owned_by", [owner.key])
    return ticket, owner


def test_list_link_summaries_carries_the_targets_display_value(
    services: ServiceBundle, linked_pair: Any
) -> None:
    """The value the backend computes on the *other* relation path crosses to this
    one too, resolved on the **target** type's chosen key."""
    ticket, owner = linked_pair
    summaries = services.records.list_link_summaries(make_actor(), ticket.key)
    assert summaries["owned_by"] == [
        {"key": owner.key, "id": owner.id, "display": "Atlas platform team"}
    ]


def test_both_relation_paths_agree_on_the_display_value(
    services: ServiceBundle, linked_pair: Any
) -> None:
    """`include=links` and `expand_relations` compute the same concept, so both paths
    publish it, and publish the same value."""
    ticket, _ = linked_pair
    summary = services.records.list_link_summaries(make_actor(), ticket.key)["owned_by"][0]
    expanded = services.records.get_record_expansions(make_actor(), ticket.key, ["owned_by"])
    assert summary["display"] == expanded["owned_by"][0]["display"] == "Atlas platform team"


def test_a_redacted_link_summary_gains_no_display_key_at_all(
    services: ServiceBundle, linked_pair: Any
) -> None:
    """An unreadable target's entry carries no key, no id, no title, no object type and no
    field values. ``display`` is a title. The key is **absent**, not null -- a null one
    would still say a display field exists and is empty."""
    ticket, _ = linked_pair
    principal_id = services.principals.create_user(
        make_actor(),
        email="reader@example.com",
        display_name="Reader",
        role="member",
        password="correct-horse-battery-staple",
    ).id
    services.access.grant(make_actor(), "ticket", principal_id, "read")
    who = ActorContext(
        principal_id=principal_id,
        principal_type="user",
        agent_label_id=None,
        auth_method="pat",
        surface="api",
        request_id="req-redacted",
        scope="read",
    )

    entries = services.records.list_link_summaries(who, ticket.key)["owned_by"]
    assert entries == [{"redacted": True}]
    assert "display" not in entries[0]


# ---------------------------- the compact projection is told the key


def test_the_compact_projection_returns_the_chosen_field_not_the_first(
    services: ServiceBundle,
) -> None:
    """``_validate_projection`` and ``_compact_projection`` take the ``ObjectType``
    rather than a bare key string, which is the whole reason the chosen value can
    reach them at all."""
    _make_type(
        services,
        "widget",
        fields=[_spec("code"), _spec("label")],
        display_field_key="label",
    )
    services.records.create_record(make_actor(), "widget", {"code": "W-1", "label": "Atlas"})
    result = services.records.query_records(make_actor(), "widget")
    assert set(result.records[0]["data"]) == {"label"}


def test_the_compact_projection_still_falls_back_without_a_choice(
    services: ServiceBundle,
) -> None:
    """**Scope fence**: a type that has chosen nothing projects the first field by
    position, as it always has."""
    _make_type(services, "widget", fields=[_spec("code"), _spec("label")])
    services.schema.update_object_type(make_actor(), "widget", {"display_field_key": None})
    services.records.create_record(make_actor(), "widget", {"code": "W-1", "label": "Atlas"})
    result = services.records.query_records(make_actor(), "widget")
    assert set(result.records[0]["data"]) == {"code"}


# ---------------------------- describe_object_type names it


def test_describe_carries_the_stored_key_and_an_equal_effective_one(
    client: Any, app_services: ServiceBundle
) -> None:
    """A chosen key returns as ``display_field_key`` with
    ``effective_display_field_key`` equal to it."""
    _make_type(
        app_services,
        "widget",
        fields=[_spec("code"), _spec("label")],
        display_field_key="label",
    )
    doc = client.get("/api/v1/object-types/widget").json()
    assert doc["display_field_key"] == "label"
    assert doc["effective_display_field_key"] == "label"


def test_describe_reports_a_null_column_beside_the_derived_effective_key(
    client: Any, app_services: ServiceBundle
) -> None:
    """The two keys are what let the schema editor show "(first field)"
    selected while an agent still reads the real answer. Merging them would make
    ``null`` unobservable on the client."""
    _make_type(app_services, "widget", fields=[_spec("code"), _spec("label")])
    app_services.schema.update_object_type(make_actor(), "widget", {"display_field_key": None})
    doc = client.get("/api/v1/object-types/widget").json()
    assert doc["display_field_key"] is None
    assert doc["effective_display_field_key"] == "code"


def test_describe_reports_null_for_both_when_no_field_is_eligible(
    client: Any, app_services: ServiceBundle
) -> None:
    """``effective_display_field_key`` is null only when the type has no
    eligible field at all."""
    _make_type(app_services, "widget", fields=[])
    doc = client.get("/api/v1/object-types/widget").json()
    assert doc["display_field_key"] is None
    assert doc["effective_display_field_key"] is None


def test_field_doc_carries_display_eligible_for_every_field(
    client: Any, app_services: ServiceBundle
) -> None:
    """The flag on the wire is what lets a TypeScript module that cannot import
    ``fieldtypes.py`` enforce eligibility without re-deriving it."""
    _make_type(app_services, "target", fields=[_spec("name")], prefix="TGT")
    _make_type(
        app_services,
        "widget",
        fields=[
            _spec("label"),
            _spec("count", "integer"),
            _spec("due", "date"),
            _spec("blob", "attachment"),
            _spec("who", "user_ref"),
            _spec(
                "parent",
                "relation",
                config={"target_type_key": "target", "cardinality": "one"},
            ),
        ],
    )
    doc = client.get("/api/v1/object-types/widget").json()
    eligible = {f["key"]: f["display_eligible"] for f in doc["fields"]}
    assert eligible == {
        "label": True,
        "count": True,
        "due": True,
        "blob": False,
        "who": False,
        "parent": False,
    }


# ---------------------------- the REST adapter forwards it


def test_rest_create_round_trips_the_display_field_key(client: Any) -> None:
    """Against the **route**, not the service. ``CreateObjectTypeBody`` is an
    explicit Pydantic model with no ``model_config``, so Pydantic v2's default
    ``extra="ignore"`` would have discarded an undeclared ``display_field_key``
    without raising: a creation path that looks like it works and stores nothing."""
    created = client.post(
        "/api/v1/object-types",
        json={
            "key": "widget",
            "name": "Widget",
            "name_plural": "Widgets",
            "description": "A widget, created over REST by the display field adapter test.",
            "key_prefix": "WID",
            "fields": [_spec("code"), _spec("label")],
            "display_field_key": "label",
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["display_field_key"] == "label"
    described = client.get("/api/v1/object-types/widget").json()
    assert described["display_field_key"] == "label"


def test_rest_patch_round_trips_the_display_field_key(client: Any) -> None:
    """``update_object_type`` takes ``changes`` as a bare dict, so the PATCH
    needed no body-model change -- but it needed the allow-list widened, and this is
    what proves the widening reached the route."""
    client.post(
        "/api/v1/object-types",
        json={
            "key": "widget",
            "name": "Widget",
            "name_plural": "Widgets",
            "description": "A widget, created over REST by the display field adapter test.",
            "key_prefix": "WID",
            "fields": [_spec("code"), _spec("label")],
        },
    )
    patched = client.patch("/api/v1/object-types/widget", json={"display_field_key": "label"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["display_field_key"] == "label"
    assert client.get("/api/v1/object-types/widget").json()["display_field_key"] == "label"


def test_rest_rejects_an_ineligible_display_field_key(client: Any) -> None:
    """The adapter forwards and does not branch; ``SchemaService`` decides
    eligibility (DD-3), and the caller sees ``validation_failed``."""
    response = client.post(
        "/api/v1/object-types",
        json={
            "key": "widget",
            "name": "Widget",
            "name_plural": "Widgets",
            "description": "A widget, created over REST by the display field adapter test.",
            "key_prefix": "WID",
            "fields": [_spec("label"), _spec("blob", "attachment")],
            "display_field_key": "blob",
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"


def test_a_write_level_caller_cannot_change_the_display_field_key(
    services: ServiceBundle,
) -> None:
    """It requires ``admin`` on the type like every other key in
    ``update_object_type``'s allow-list (``services/schema.py``'s
    ``require_level(..., "admin")``)."""
    _make_type(services, "widget", fields=[_spec("code"), _spec("label")])
    principal_id = services.principals.create_user(
        make_actor(),
        email="writer@example.com",
        display_name="Writer",
        role="member",
        password="correct-horse-battery-staple",
    ).id
    services.access.grant(make_actor(), "widget", principal_id, "write")
    who = ActorContext(
        principal_id=principal_id,
        principal_type="user",
        agent_label_id=None,
        auth_method="pat",
        surface="api",
        request_id="req-write",
        # An `admin`-**scoped** credential, deliberately: the credential is a ceiling
        # (DD-11), so a `write` scope would be refused before the grant is consulted and
        # this test would prove the scope check rather than the level one.
        scope="admin",
    )
    with pytest.raises(ForbiddenError):
        services.schema.update_object_type(who, "widget", {"display_field_key": "label"})


# ---------------------------- a search hit's title


def test_a_search_hit_titles_on_the_chosen_field_not_the_first(
    services: ServiceBundle,
) -> None:
    """``search.py``'s ``_title`` never saw the object type; its call site
    always did. The chosen field is not the first by position, so a derived title and
    a chosen one differ, and only the threading can produce this answer."""
    _make_type(
        services,
        "widget",
        fields=[_spec("code", embed=True), _spec("label", embed=True)],
        display_field_key="label",
    )
    services.records.create_record(
        make_actor(), "widget", {"code": "zzz-serial", "label": "Atlas platform team"}
    )
    result = services.search.search(make_actor(), "Atlas", mode="keyword")
    assert [hit.title for hit in result.results] == ["Atlas platform team"]


def test_a_search_hit_still_titles_on_the_derived_field_without_a_choice(
    services: ServiceBundle,
) -> None:
    """**Scope fence**: a type that has chosen nothing titles on the first field by
    position, as it always has."""
    _make_type(services, "widget", fields=[_spec("code", embed=True), _spec("label", embed=True)])
    services.schema.update_object_type(make_actor(), "widget", {"display_field_key": None})
    services.records.create_record(
        make_actor(), "widget", {"code": "zzz-serial", "label": "Atlas platform team"}
    )
    result = services.search.search(make_actor(), "Atlas", mode="keyword")
    assert [hit.title for hit in result.results] == ["zzz-serial"]


# ---------------------------- the MCP adapter forwards it


@pytest.mark.anyio
async def test_the_mcp_tool_sets_the_display_field_key_at_creation(
    mcp_server: Any, services: ServiceBundle, pat: dict[str, str]
) -> None:
    """``tools_admin.create_object_type`` has an explicit keyword signature and
    forwards no extras, so the parameter had to be declared there; the agent-facing
    ``Field(description=...)`` is what makes it usable rather than merely present
    (non-negotiable 6)."""
    async with memory_session(mcp_server, token=pat["admin"]) as c:
        await c.call_tool(
            "create_object_type",
            {
                "key": "widget",
                "name": "Widget",
                "name_plural": "Widgets",
                "description": "A widget, created over MCP by the display field adapter test.",
                "key_prefix": "WID",
                "fields": [_spec("code"), _spec("label")],
                "display_field_key": "label",
            },
        )
        described = structured(await c.call_tool("describe_object_type", {"object_type": "widget"}))

    assert described["display_field_key"] == "label"
    assert described["effective_display_field_key"] == "label"
