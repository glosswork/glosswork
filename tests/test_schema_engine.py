"""Schema engine acceptance tests (FR-S1 through FR-S10)."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID, ActorContext
from glosswork.app import DATABASE_FILENAME, create_app
from glosswork.compiler import SortKey, _sort_expr, field_expr
from glosswork.config import Settings
from glosswork.db import Database
from glosswork.errors import (
    ImpactChangedError,
    InsufficientScopeError,
    ValidationFailedError,
)
from glosswork.fieldtypes import (
    FIELD_TYPES,
    PSEUDO_FIELD_DESCRIPTIONS,
    PSEUDO_FIELDS,
    coerce_value,
)
from glosswork.filters import FilterContext
from glosswork.migrations import run_migrations
from glosswork.repositories.models import FieldDef, ObjectType
from glosswork.repositories.sqlite import SqliteAuditRepository, SqliteSchemaRepository
from glosswork.services import ServiceBundle, build_services
from glosswork.services.capabilities import capabilities_document
from glosswork.services.schema import SchemaService
from tests.conftest import make_actor

# ---------------------------------------------------------------------------
# All thirteen field types
# ---------------------------------------------------------------------------


class TestThirteenFieldTypes:
    def test_object_type_uses_every_field_type(
        self,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        _, fields = sink_type
        used_types = {f.type for f in fields.values()}
        assert used_types == FIELD_TYPES, f"missing field types: {sorted(FIELD_TYPES - used_types)}"
        assert len(FIELD_TYPES) == 13

    def test_attachment_field_is_a_validation_target(
        self,
        services: ServiceBundle,
        actor: ActorContext,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        """Attachment is exercised here as a definition and validation target only."""
        record = services.records.create_record(
            make_actor(), "artifact", {"title": "With files", "files": ["att-1", "att-2"]}
        )
        assert record.data["files"] == ["att-1", "att-2"]
        with pytest.raises(ValidationFailedError):
            services.records.create_record(
                make_actor(), "artifact", {"title": "Bad files", "files": "not-a-list"}
            )
        with pytest.raises(ValidationFailedError):
            services.records.create_record(
                make_actor(), "artifact", {"title": "Bad files", "files": [1, 2]}
            )

    def test_every_field_type_validates_and_rejects(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID

        good = {
            "title": "ok",
            "summary": "long text",
            "points": 3,
            "score": 1.25,
            "active": True,
            "due": "2026-09-01",
            "seen_at": "2026-09-01T08:00:00Z",
            "status": "todo",
            "tags": ["red"],
            "owner": BOOTSTRAP_PRINCIPAL_ID,
            "homepage": "https://example.com/",
            "files": [],
        }
        record = services.records.create_record(make_actor(), "artifact", good)
        assert record.data["points"] == 3

        bad_values = {
            "title": 42,
            "summary": ["not", "text"],
            "points": "five",
            "score": "1.5",
            "active": "yes",
            "due": "September 1st",
            "seen_at": "2026-09-01 08:00",
            "status": "not_an_option",
            "tags": ["mauve"],
            "owner": "not-a-principal",
            "homepage": "example.com",
            "files": "x",
        }
        for field_key, bad in bad_values.items():
            with pytest.raises(ValidationFailedError):
                services.records.create_record(
                    make_actor(), "artifact", {"title": "probe", field_key: bad}
                )

    def test_relation_values_are_redirected_to_links(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        with pytest.raises(ValidationFailedError, match="link_records"):
            services.records.create_record(
                make_actor(), "artifact", {"title": "x", "parent": "ART-001"}
            )


# ---------------------------------------------------------------------------
# Descriptions are required and agent-facing
# ---------------------------------------------------------------------------


class TestDescriptionsRequired:
    @pytest.mark.parametrize("bad_description", ["", "   ", "\n\t"])
    def test_object_type_description_required(
        self, services: ServiceBundle, actor: ActorContext, bad_description: str
    ) -> None:
        with pytest.raises(ValidationFailedError) as excinfo:
            services.schema.create_object_type(
                actor,
                key="thing",
                name="Thing",
                name_plural="Things",
                description=bad_description,
                key_prefix="THG",
            )
        assert "agents interpret the schema" in str(excinfo.value)

    @pytest.mark.parametrize("bad_description", ["", "   "])
    def test_field_description_required(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
        bad_description: str,
    ) -> None:
        with pytest.raises(ValidationFailedError) as excinfo:
            services.schema.add_field(
                make_actor(),
                "artifact",
                {
                    "key": "extra",
                    "name": "Extra",
                    "type": "short_text",
                    "description": bad_description,
                },
            )
        assert "agents interpret the schema" in str(excinfo.value)

    def test_enum_option_description_required(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        with pytest.raises(ValidationFailedError) as excinfo:
            services.schema.add_field(
                make_actor(),
                "artifact",
                {
                    "key": "priority",
                    "name": "Priority",
                    "type": "single_select",
                    "description": "How urgent this artifact is.",
                    "config": {"options": [{"value": "p1", "label": "P1", "description": " "}]},
                },
            )
        assert "agents interpret the schema" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Additive changes apply immediately (FR-S5)
# ---------------------------------------------------------------------------


class TestAdditiveChanges:
    def test_add_field_and_enum_option_apply_immediately(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        field = services.schema.add_field(
            make_actor(),
            "artifact",
            {
                "key": "risk",
                "name": "Risk",
                "type": "single_select",
                "description": "Delivery risk; 'high' means the milestone is threatened.",
                "config": {
                    "options": [
                        {"value": "low", "label": "Low", "description": "On track."},
                        {"value": "high", "label": "High", "description": "Threatened."},
                    ]
                },
            },
        )
        assert field.key == "risk"
        # Adding an enum option is additive and applies on the call.
        result = services.schema.update_field(
            make_actor(),
            "artifact",
            "risk",
            {
                "config": {
                    "options": [
                        {"value": "low", "label": "Low", "description": "On track."},
                        {"value": "high", "label": "High", "description": "Threatened."},
                        {"value": "critical", "label": "Critical", "description": "Stopped."},
                    ]
                }
            },
        )
        assert result.applied is True
        assert result.proposal is None
        assert "additive" in result.message
        _, fields = services.schema.get_object_type(make_actor(), "artifact")
        options = {o["value"] for f in fields if f.key == "risk" for o in f.config["options"]}
        assert options == {"low", "high", "critical"}

    def test_rename_edit_description_relax_and_toggles(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        # Rename the object type and edit its description.
        renamed = services.schema.update_object_type(
            make_actor(),
            "artifact",
            {"name": "Work Artifact", "description": "Renamed; still the test type."},
        )
        assert renamed.name == "Work Artifact"

        # Rename a field, edit its description, toggle embed and indexed.
        result = services.schema.update_field(
            make_actor(),
            "artifact",
            "summary",
            {
                "name": "Narrative",
                "description": "Narrative body; edited by the additive test.",
                "embed": False,
                "indexed": True,
            },
        )
        assert result.applied is True
        _, fields = services.schema.get_object_type(make_actor(), "artifact")
        by_key = {f.key: f for f in fields}
        assert by_key["summary"].name == "Narrative"
        assert by_key["summary"].embed is False
        assert by_key["summary"].is_indexed is True

        # Relax required: title is required with no violating data either way.
        result = services.schema.update_field(
            make_actor(), "artifact", "title", {"required": False}
        )
        assert result.applied is True
        _, fields = services.schema.get_object_type(make_actor(), "artifact")
        assert {f.key: f for f in fields}["title"].is_required is False

    def test_key_and_prefix_immutable(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        with pytest.raises(ValidationFailedError, match="immutable"):
            services.schema.update_object_type(make_actor(), "artifact", {"key": "other"})
        with pytest.raises(ValidationFailedError, match="immutable"):
            services.schema.update_field(make_actor(), "artifact", "title", {"key": "name2"})


# ---------------------------------------------------------------------------
# Destructive changes return a proposal with nothing applied (FR-S6)
# ---------------------------------------------------------------------------


def _seed_records(services: ServiceBundle) -> None:
    for i, status in enumerate(["todo", "doing", "done"], start=1):
        services.records.create_record(
            make_actor(),
            "artifact",
            {"title": f"Rec {i}", "status": status, "points": i, "summary": f"body {i}"},
        )


class TestDestructiveChanges:
    def test_delete_field_returns_proposal_and_applies_nothing(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        _seed_records(services)
        proposal = services.schema.propose_schema_change(
            make_actor(), "delete_field", "artifact", "points", reason="unused"
        )
        assert proposal.status == "pending"
        assert proposal.change_type == "delete_field"
        # Nothing applied: the field still exists and data is intact.
        _, fields = services.schema.get_object_type(make_actor(), "artifact")
        assert "points" in {f.key for f in fields}
        assert services.records.get_record(make_actor(), "ART-001").data["points"] == 1

    def test_delete_object_type_returns_proposal(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        _seed_records(services)
        proposal = services.schema.propose_schema_change(
            make_actor(), "delete_object_type", "artifact"
        )
        assert proposal.status == "pending"
        assert proposal.impact["affected_records"] == 3
        assert services.schema.get_object_type(make_actor(), "artifact")[0].is_deleted is False

    def test_destructive_changes_route_to_proposal_even_for_an_admin_caller(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        """FR-S6: an admin-scoped caller gets a proposal, never a direct apply.

        **Why this is not parametrized over every scope.** It once was, over
        `["read", "write", "admin"]`, to assert the routing decision was identical for
        every scope value. `update_field` and `propose_schema_change` consult
        `AccessService` at the service layer, so a `read`- or `write`-scoped credential
        never reaches the routing decision at all. So this test covers the caller that
        *can* reach it, and
        `test_a_narrower_credential_never_reaches_the_routing_decision` below covers the
        two that cannot. FR-S6's routing is the same for every caller that reaches it.
        """
        _seed_records(services)
        result = services.schema.update_field(
            make_actor(), "artifact", "points", {"type": "short_text"}
        )
        assert result.applied is False
        assert result.proposal is not None and result.proposal.status == "pending"

        proposal = services.schema.propose_schema_change(
            make_actor(), "delete_field", "artifact", "score"
        )
        assert proposal.status == "pending"
        # Nothing applied.
        _, fields = services.schema.get_object_type(make_actor(), "artifact")
        by_key = {f.key: f for f in fields}
        assert by_key["points"].type == "integer"
        assert "score" in by_key

    @pytest.mark.parametrize("scope", ["read", "write"])
    def test_a_narrower_credential_never_reaches_the_routing_decision(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
        scope: str,
    ) -> None:
        """`update_field` requires `admin` on the type and `propose_schema_change`
        requires `write`, and the credential is a ceiling over both. The refusal names
        the *credential* here rather than the grant, because the bootstrap principal is
        a system administrator and its grant is not what fell short."""
        _seed_records(services)
        scoped_actor = dataclasses.replace(make_actor(), scope=scope)  # type: ignore[arg-type]
        with pytest.raises(InsufficientScopeError):
            services.schema.update_field(scoped_actor, "artifact", "points", {"type": "short_text"})
        if scope == "read":
            with pytest.raises(InsufficientScopeError):
                services.schema.propose_schema_change(
                    scoped_actor, "delete_field", "artifact", "score"
                )
        else:
            # `write` is exactly what proposing needs, and proposing is not deciding.
            assert (
                services.schema.propose_schema_change(
                    scoped_actor, "delete_field", "artifact", "score"
                ).status
                == "pending"
            )

    def test_schema_service_never_consults_scope_for_routing(self) -> None:
        """Structural half of 'regardless of caller scope': the schema service has
        no scope-conditional path a privileged caller could take around FR-S6."""
        import glosswork.services.schema as schema_module

        source = Path(schema_module.__file__).read_text()
        assert "actor.scope" not in source
        assert ".scope" not in source

    def test_update_field_auto_routes_and_says_which(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        _seed_records(services)
        additive = services.schema.update_field(
            make_actor(), "artifact", "points", {"description": "Story points; renamed."}
        )
        assert additive.applied is True and "additive" in additive.message

        destructive = services.schema.update_field(
            make_actor(), "artifact", "points", {"type": "short_text"}
        )
        assert destructive.applied is False
        assert destructive.proposal is not None
        assert destructive.proposal.change_type == "change_field_type"
        assert "requires human approval" in destructive.message
        # Nothing applied.
        _, fields = services.schema.get_object_type(make_actor(), "artifact")
        assert {f.key: f for f in fields}["points"].type == "integer"

    def test_remove_in_use_enum_option_routes_to_proposal(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        _seed_records(services)  # uses status values todo/doing/done
        trimmed_options = [
            {"value": "todo", "label": "Todo", "description": "The todo state."},
            {"value": "done", "label": "Done", "description": "The done state."},
        ]
        result = services.schema.update_field(
            make_actor(), "artifact", "status", {"config": {"options": trimmed_options}}
        )
        assert result.applied is False
        assert result.proposal is not None
        assert result.proposal.change_type == "remove_enum_option"
        assert result.proposal.impact["in_use"]["values"] == ["doing"]
        assert result.proposal.impact["in_use"]["count"] == 1
        # Unused option removal is additive: no seeded record uses 'blue' on tags.
        result2 = services.schema.update_field(
            make_actor(),
            "artifact",
            "tags",
            {
                "config": {
                    "options": [
                        {"value": "red", "label": "Red", "description": "The red state."},
                        {"value": "green", "label": "Green", "description": "The green state."},
                    ]
                }
            },
        )
        assert result2.applied is True

    def test_tighten_violated_constraints_route_to_proposal(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        _seed_records(services)
        # 'summary' is present on all three seeds; clear one so required is violated.
        services.records.update_record(make_actor(), "ART-003", {"summary": None})
        result = services.schema.update_field(
            make_actor(), "artifact", "summary", {"required": True}
        )
        assert result.applied is False
        assert result.proposal is not None
        assert result.proposal.change_type == "tighten_constraint"
        assert result.proposal.impact["violations"]["constraint"] == "required"
        assert result.proposal.impact["violations"]["sample_record_keys"] == ["ART-003"]

        # unique with duplicates present routes to a proposal too.
        services.records.update_record(make_actor(), "ART-001", {"points": 7})
        services.records.update_record(make_actor(), "ART-002", {"points": 7})
        result2 = services.schema.update_field(make_actor(), "artifact", "points", {"unique": True})
        assert result2.applied is False
        assert result2.proposal is not None
        assert result2.proposal.impact["violations"]["constraint"] == "unique"

        # Tightening with no violations applies immediately (nothing is violated).
        result3 = services.schema.update_field(make_actor(), "artifact", "title", {"unique": True})
        assert result3.applied is True

    def test_change_to_relation_or_attachment_rejected(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        for target in ("relation", "attachment"):
            with pytest.raises(ValidationFailedError, match="not supported"):
                services.schema.update_field(make_actor(), "artifact", "points", {"type": target})


# ---------------------------------------------------------------------------
# Impact document (FR-S8)
# ---------------------------------------------------------------------------


class TestImpactDocument:
    def test_impact_carries_count_samples_and_dry_run(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        services.records.create_record(make_actor(), "artifact", {"title": "n1", "summary": "12"})
        services.records.create_record(
            make_actor(), "artifact", {"title": "n2", "summary": "not a number"}
        )
        services.records.create_record(make_actor(), "artifact", {"title": "n3"})
        result = services.schema.update_field(
            make_actor(), "artifact", "summary", {"type": "integer"}
        )
        assert result.proposal is not None
        impact = result.proposal.impact
        assert impact["affected_records"] == 2  # records with a value
        assert impact["non_empty_values"] == 2
        assert "12" in impact["sample_values"] and "not a number" in impact["sample_values"]
        failures = impact["coercion_failures"]
        assert len(failures) == 1
        assert failures[0]["record_key"] == "ART-002"
        assert failures[0]["value"] == "not a number"
        assert "does not parse" in failures[0]["reason"]


# ---------------------------------------------------------------------------
# Approval: snapshot, recomputation, elections (FR-S7, FR-S9)
# ---------------------------------------------------------------------------


class TestApproval:
    def test_approval_applies_and_snapshots(
        self,
        db: Database,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        _seed_records(services)
        proposal = services.schema.propose_schema_change(
            make_actor(), "delete_field", "artifact", "points"
        )
        approved = services.schema.approve_proposal(make_actor(), proposal.id)
        assert approved.status == "approved"
        assert approved.snapshot_ref is not None

        # The snapshot is an audit row linked via snapshot_ref (FR-S7).
        audit = SqliteAuditRepository()
        with db.read() as conn:
            event = audit.get_event(conn, int(approved.snapshot_ref))
        assert event is not None
        assert event.action == "snapshot"
        assert event.entity_type == "schema_proposal"
        assert event.entity_id == proposal.id
        assert event.old_value["records"] == {"ART-001": 1, "ART-002": 2, "ART-003": 3}
        assert event.old_value["field_def"]["type"] == "integer"

        # The change actually applied: field gone, data stripped.
        _, fields = services.schema.get_object_type(make_actor(), "artifact")
        assert "points" not in {f.key for f in fields}
        assert "points" not in services.records.get_record(make_actor(), "ART-001").data

    def test_approval_recomputes_impact_and_refuses_material_change(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        _seed_records(services)
        proposal = services.schema.propose_schema_change(
            make_actor(), "delete_field", "artifact", "points"
        )
        assert proposal.impact["affected_records"] == 3
        # Data changes between proposal and approval.
        services.records.create_record(make_actor(), "artifact", {"title": "late", "points": 99})
        with pytest.raises(ImpactChangedError) as excinfo:
            services.schema.approve_proposal(make_actor(), proposal.id)
        new_impact = excinfo.value.new_impact
        assert new_impact["affected_records"] == 4
        # Proposal still pending; nothing applied.
        assert services.schema.get_proposal(make_actor(), proposal.id).status == "pending"
        _, fields = services.schema.get_object_type(make_actor(), "artifact")
        assert "points" in {f.key for f in fields}
        # Re-confirming with the recomputed impact proceeds.
        approved = services.schema.approve_proposal(
            make_actor(), proposal.id, confirm_impact=new_impact
        )
        assert approved.status == "approved"
        assert approved.impact["affected_records"] == 4

    def test_null_non_coercible_election(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        services.records.create_record(make_actor(), "artifact", {"title": "a", "summary": "41"})
        services.records.create_record(
            make_actor(), "artifact", {"title": "b", "summary": "not numeric"}
        )
        result = services.schema.update_field(
            make_actor(), "artifact", "summary", {"type": "integer"}
        )
        assert result.proposal is not None
        # Without the election, approval refuses (FR-S9).
        with pytest.raises(ValidationFailedError, match="null_non_coercible"):
            services.schema.approve_proposal(make_actor(), result.proposal.id)
        approved = services.schema.approve_proposal(
            make_actor(), result.proposal.id, null_non_coercible=True
        )
        assert approved.status == "approved"
        assert services.records.get_record(make_actor(), "ART-001").data["summary"] == 41
        assert "summary" not in services.records.get_record(make_actor(), "ART-002").data
        _, fields = services.schema.get_object_type(make_actor(), "artifact")
        assert {f.key: f for f in fields}["summary"].type == "integer"

    def test_delete_object_type_approval_soft_deletes_records(
        self,
        db: Database,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        _seed_records(services)
        proposal = services.schema.propose_schema_change(
            make_actor(), "delete_object_type", "artifact"
        )
        approved = services.schema.approve_proposal(make_actor(), proposal.id)
        assert approved.status == "approved"
        with db.read() as conn:
            live = conn.execute(
                text("SELECT COUNT(*) FROM records WHERE deleted_at IS NULL")
            ).scalar()
            types = conn.execute(
                text("SELECT COUNT(*) FROM object_types WHERE is_deleted = 0")
            ).scalar()
        assert live == 0
        assert types == 0

    def test_rejection_leaves_everything_untouched(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        _seed_records(services)
        proposal = services.schema.propose_schema_change(
            make_actor(), "delete_field", "artifact", "points"
        )
        rejected = services.schema.reject_proposal(make_actor(), proposal.id, "keep it")
        assert rejected.status == "rejected"
        _, fields = services.schema.get_object_type(make_actor(), "artifact")
        assert "points" in {f.key for f in fields}


# ---------------------------------------------------------------------------
# The coercion matrix, both directions (docs/DATA_MODEL.md section 4)
# ---------------------------------------------------------------------------

SAFE_PATHS: list[tuple[str, str, object, object, dict[str, object]]] = [
    ("short_text", "long_text", "hello", "hello", {}),
    ("long_text", "short_text", "brief", "brief", {"max_length": 10}),
    ("short_text", "integer", "42", 42, {}),
    ("long_text", "integer", "-7", -7, {}),
    ("short_text", "decimal", "3.25", 3.25, {}),
    ("short_text", "date", "2026-08-23", "2026-08-23", {}),
    ("short_text", "datetime", "2026-08-23T10:00:00Z", "2026-08-23T10:00:00Z", {}),
    (
        "short_text",
        "single_select",
        "todo",
        "todo",
        {"options": [{"value": "todo", "label": "Todo", "description": "d"}]},
    ),
    ("single_select", "multi_select", "todo", ["todo"], {}),
    (
        "multi_select",
        "single_select",
        ["only"],
        "only",
        {"options": [{"value": "only", "label": "Only", "description": "d"}]},
    ),
]

FAILING_PATHS: list[tuple[str, str, object, dict[str, object]]] = [
    ("long_text", "short_text", "far too long for the limit", {"max_length": 5}),
    ("short_text", "integer", "not a number", {}),
    ("long_text", "integer", "12.5", {}),
    ("short_text", "decimal", "many", {}),
    ("short_text", "date", "23/08/2026", {}),
    ("short_text", "datetime", "2026-08-23 10:00", {}),
    (
        "short_text",
        "single_select",
        "unknown",
        {"options": [{"value": "todo", "label": "Todo", "description": "d"}]},
    ),
    ("multi_select", "single_select", ["a", "b"], {}),
    ("integer", "relation", 1, {}),
    ("integer", "attachment", 1, {}),
]


class TestCoercionMatrix:
    @pytest.mark.parametrize(("from_type", "to_type", "value", "expected", "config"), SAFE_PATHS)
    def test_documented_safe_paths_convert(
        self, from_type: str, to_type: str, value: object, expected: object, config: dict
    ) -> None:
        ok, converted, reason = coerce_value(from_type, to_type, value, config)
        assert ok, f"{from_type} -> {to_type} should be safe, got: {reason}"
        assert converted == expected

    @pytest.mark.parametrize(("from_type", "to_type", "value", "config"), FAILING_PATHS)
    def test_documented_failing_paths_fail_with_reason(
        self, from_type: str, to_type: str, value: object, config: dict
    ) -> None:
        ok, _, reason = coerce_value(from_type, to_type, value, config)
        assert not ok
        assert reason

    def test_failing_paths_surface_in_dry_run(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        """End to end: long_text -> short_text with max_length flags the long row."""
        services.records.create_record(make_actor(), "artifact", {"title": "s", "summary": "ok"})
        services.records.create_record(
            make_actor(),
            "artifact",
            {"title": "l", "summary": "definitely exceeds the configured limit"},
        )
        result = services.schema.update_field(
            make_actor(),
            "artifact",
            "summary",
            {"type": "short_text", "config": {"max_length": 5}},
        )
        assert result.proposal is not None
        failures = result.proposal.impact["coercion_failures"]
        assert [f["record_key"] for f in failures] == ["ART-002"]
        assert "max_length" in failures[0]["reason"]

    def test_text_to_select_with_create_options_election(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        services.records.create_record(make_actor(), "artifact", {"title": "a", "summary": "alpha"})
        services.records.create_record(make_actor(), "artifact", {"title": "b", "summary": "beta"})
        result = services.schema.update_field(
            make_actor(), "artifact", "summary", {"type": "single_select"}
        )
        assert result.proposal is not None
        assert len(result.proposal.impact["coercion_failures"]) == 2
        approved = services.schema.approve_proposal(
            make_actor(), result.proposal.id, create_missing_options=True
        )
        assert approved.status == "approved"
        _, fields = services.schema.get_object_type(make_actor(), "artifact")
        summary = {f.key: f for f in fields}["summary"]
        assert summary.type == "single_select"
        assert {o["value"] for o in summary.config["options"]} == {"alpha", "beta"}
        assert services.records.get_record(make_actor(), "ART-001").data["summary"] == "alpha"


# ---------------------------------------------------------------------------
# DD-20: the pseudo-field keys are reserved for field keys
# ---------------------------------------------------------------------------


class TestReservedFieldKeys:
    """A field key may not shadow a system pseudo-field.

    The shadow was real: `FilterContext.lookup` finds `fields_by_key` first, so a type
    with a `created_by` field compiled `created_by eq @me` to
    `json_extract(data, '$.created_by')` -- an attacker-writable value -- while the
    envelope carried the real column at top level beside it.
    """

    def _type_with_field(self, services: ServiceBundle, key: str) -> None:
        services.schema.create_object_type(
            make_actor(),
            key="shadow",
            name="Shadow",
            name_plural="Shadows",
            description="A type whose field would shadow a system column.",
            key_prefix="SHD",
            fields=[
                {
                    "key": key,
                    "name": "Shadowing field",
                    "type": "short_text",
                    "description": "Would shadow the system pseudo-field of the same name.",
                }
            ],
        )

    @pytest.mark.parametrize("reserved", sorted(PSEUDO_FIELDS))
    def test_a_reserved_key_is_refused_on_create_object_type(
        self, services: ServiceBundle, reserved: str
    ) -> None:
        with pytest.raises(ValidationFailedError) as exc:
            self._type_with_field(services, reserved)
        assert reserved in exc.value.message
        assert "reserved" in exc.value.message

    @pytest.mark.parametrize("reserved", sorted(PSEUDO_FIELDS))
    def test_a_reserved_key_is_refused_on_add_field(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
        reserved: str,
    ) -> None:
        with pytest.raises(ValidationFailedError) as exc:
            services.schema.add_field(
                make_actor(),
                "artifact",
                {
                    "key": reserved,
                    "name": "Shadowing field",
                    "type": "short_text",
                    "description": "Would shadow the system pseudo-field of the same name.",
                },
            )
        assert reserved in exc.value.message
        assert "reserved" in exc.value.message

    def test_the_refusal_names_the_whole_reserved_set(self, services: ServiceBundle) -> None:
        """Non-negotiable 6: the message is agent-facing. An agent that
        takes this refusal must be able to pick a new key without a second round trip,
        which means being told every name it cannot use and why."""
        with pytest.raises(ValidationFailedError) as exc:
            self._type_with_field(services, "created_by")
        message = exc.value.message
        for name in PSEUDO_FIELDS:
            assert name in message, f"the refusal does not name {name!r}"
        assert "FR-R7" in message
        assert "created_by_name" in message  # it says what to do instead

    def test_a_reserved_inverse_field_key_is_refused_naming_the_input(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        """`validate_config` runs before `_create_inverse_field` ever would, so the
        message names `inverse_field_key` -- the input the caller actually wrote --
        rather than the field it would have created. That ordering is also what keeps
        `_add_field_in_txn`'s third caller unreachable with a reserved key."""
        with pytest.raises(ValidationFailedError) as exc:
            services.schema.add_field(
                make_actor(),
                "artifact",
                {
                    "key": "sibling",
                    "name": "Sibling",
                    "type": "relation",
                    "description": "Self-referential relation whose inverse is reserved.",
                    "config": {
                        "target_type_key": "artifact",
                        "cardinality": "many",
                        "inverse_field_key": "created_by",
                    },
                },
            )
        assert "inverse_field_key" in exc.value.message
        assert "reserved" in exc.value.message
        # And nothing was created on either side.
        _, fields = services.schema.get_object_type(make_actor(), "artifact")
        assert "sibling" not in {f.key for f in fields}
        assert "created_by" not in {f.key for f in fields}

    def test_a_superstring_of_a_reserved_key_is_accepted(
        self,
        services: ServiceBundle,
        sink_type: tuple[ObjectType, dict[str, FieldDef]],
    ) -> None:
        """Fence: the rule is equality against the set, never a prefix or substring
        test. `created_by_name` is the key the refusal itself suggests."""
        field = services.schema.add_field(
            make_actor(),
            "artifact",
            {
                "key": "created_by_name",
                "name": "Created by name",
                "type": "short_text",
                "description": "Denormalized creator name, distinct from the system column.",
            },
        )
        assert field.key == "created_by_name"

    def test_an_object_type_keyed_key_is_accepted(self, services: ServiceBundle) -> None:
        """Fence: the rule is on field keys only. An object type named `key` shares
        no namespace with a filter's field names, so widening it buys nothing."""
        object_type = services.schema.create_object_type(
            make_actor(),
            key="key",
            name="Key",
            name_plural="Keys",
            description="An object type whose own key matches a pseudo-field name.",
            key_prefix="KEY",
            fields=[
                {
                    "key": "label",
                    "name": "Label",
                    "type": "short_text",
                    "description": "What this key is called.",
                }
            ],
        )
        assert object_type.key == "key"


class TestReservedKeyCollisionsAlreadyOnDisk:
    """A deployment created before field keys were reserved may already hold a shadowing
    field, made in good faith. It is reported, never rewritten (DD-20).

    The fixture seeds the collision through ``SqliteSchemaRepository.insert_field``
    inside a ``db.write()``, because no service entry point can create one.
    That repository call is the only way to build the state the report exists for.
    """

    RESERVED = "created_by"

    def _seed_collision(self, db: Database, services: ServiceBundle) -> ObjectType:
        object_type = services.schema.create_object_type(
            make_actor(),
            key="legacy",
            name="Legacy",
            name_plural="Legacies",
            description="A type created before the reserved-key rule existed.",
            key_prefix="LEG",
            fields=[
                {
                    "key": "title",
                    "name": "Title",
                    "type": "short_text",
                    "description": "Short human-readable name.",
                }
            ],
        )
        now = "2026-09-04T00:00:00Z"
        with db.write() as conn:
            SqliteSchemaRepository().insert_field(
                conn,
                FieldDef(
                    id="00000000-0000-4000-8000-0000000c0111",
                    object_type_id=object_type.id,
                    key=self.RESERVED,
                    name="Created by",
                    description="A pre-017 field shadowing the system column.",
                    type="short_text",
                    position=2,
                    is_required=False,
                    is_unique=False,
                    is_indexed=False,
                    embed=False,
                    default_value=None,
                    config={},
                    is_deleted=False,
                    created_at=now,
                    created_by=BOOTSTRAP_PRINCIPAL_ID,
                    updated_at=now,
                    updated_by=BOOTSTRAP_PRINCIPAL_ID,
                ),
            )
        return object_type

    def test_the_report_names_the_type_and_the_field(
        self, db: Database, services: ServiceBundle
    ) -> None:
        assert services.schema.reserved_key_collisions() == []
        self._seed_collision(db, services)
        assert services.schema.reserved_key_collisions() == [("legacy", self.RESERVED)]

    def test_a_deleted_field_is_not_reported(self, db: Database, services: ServiceBundle) -> None:
        """A field already retired through the deprecation window is resolved, not
        outstanding, so reporting it every start would train the operator to ignore the
        warning."""
        object_type = self._seed_collision(db, services)
        with db.write() as conn:
            conn.execute(
                text("UPDATE fields SET is_deleted = 1 WHERE object_type_id = :t AND key = :k"),
                {"t": object_type.id, "k": self.RESERVED},
            )
        assert services.schema.reserved_key_collisions() == []

    def test_a_seeded_database_starts_warns_once_and_is_ready(
        self, tmp_path: Path, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """The startup half. Captured with ``capfd`` rather than ``caplog``: this
        deployment's structlog configuration renders JSON to stdout, so nothing reaches
        the stdlib handler ``caplog`` installs.

        ``/readyz`` stays 200: a running deployment with a collision is degraded, not
        down.
        """
        settings = Settings(data_dir=tmp_path / "data", embedding_enabled=False)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        db = Database.connect(settings.data_dir / DATABASE_FILENAME)
        run_migrations(db)
        services = build_services(db, settings.data_dir, settings)
        self._seed_collision(db, services)
        db.close()

        capfd.readouterr()  # discard the seeding chatter
        with TestClient(create_app(settings)) as client:
            # The body, not the status. The SPA catch-all answers 200 for a
            # route that does not exist, so a status-only assertion is vacuous.
            assert client.get("/readyz").json() == {"status": "ok"}
        out, _err = capfd.readouterr()

        logged = [
            json.loads(line)
            for line in out.splitlines()
            if line.startswith("{") and '"reserved_key_collision"' in line
        ]
        assert len(logged) == 1, out
        entry = logged[0]
        assert entry["level"] == "warning"
        assert entry["object_type"] == "legacy"
        assert entry["field"] == self.RESERVED
        # The sentence names the resolution and what the operator will see when they
        # go looking.
        assert "delete_field" in entry["hint"]
        assert "system_fields" in entry["hint"]

    def test_a_report_that_raises_does_not_block_startup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The lifespan logs what the report returns and never blocks
        startup on it, exactly as its two neighbours do."""

        def _explode(self: SchemaService) -> list[tuple[str, str]]:
            raise RuntimeError("collision scan exploded")

        monkeypatch.setattr(SchemaService, "reserved_key_collisions", _explode, raising=True)
        settings = Settings(data_dir=tmp_path / "data", embedding_enabled=False)
        with TestClient(create_app(settings)) as client:
            assert client.get("/readyz").json() == {"status": "ok"}  # the body

    # --------------------------------------------------------------- the four sites
    #
    # A **fence** that cannot fail by construction and is not counted. A new collision
    # is refused, and the four sites that read an existing one are deliberately not
    # reconciled -- two of which already disagree with the other two, which is the
    # strongest argument for a loud report rather than a silent fix. Recorded here so
    # the disagreement is pinned rather than only described.

    def test_fence_the_filter_and_sort_paths_compile_the_shadow_to_json_extract(
        self, db: Database, services: ServiceBundle
    ) -> None:
        """Sites one and two (``filters.py``'s ``FilterContext.lookup``, and
        ``compiler.py``'s ``field_expr``/``_sort_expr``): ``fields_by_key`` is consulted
        first, so a filter or sort on ``created_by`` reads the user field."""
        self._seed_collision(db, services)
        _, fields = services.schema.get_object_type(make_actor(), "legacy")
        fields_by_key = {f.key: f for f in fields}
        assert self.RESERVED in fields_by_key

        context = FilterContext(
            "legacy",
            fields_by_key,
            datetime(2026, 9, 4, tzinfo=UTC),
            lambda ref: ref,
            lambda ref: ref,
        )
        field_type, field = context.lookup(self.RESERVED)
        assert field is not None and field.type == "short_text"
        assert field_expr(field, self.RESERVED) == "json_extract(data, '$.created_by')"
        assert (
            _sort_expr(SortKey(self.RESERVED, "asc"), fields_by_key)
            == "json_extract(data, '$.created_by')"
        )
        # And against a type with no such field, the same key is the column.
        assert field_expr(None, self.RESERVED) == "created_by"

    def test_fence_projection_and_multi_type_search_read_the_column(
        self, db: Database, services: ServiceBundle
    ) -> None:
        """Sites three and four (``records.py``'s projection and ``search.py``'s
        one-type rule): both treat the key as a pseudo-field, so a
        ``fields: ["created_by"]`` projection omits the shadow entirely while
        ``fields: "*"`` shows both, and a multi-type search accepts the key that a
        single-type query would have compiled to ``json_extract``."""
        self._seed_collision(db, services)
        record = services.records.create_record(
            make_actor(), "legacy", {"title": "Shadowed", "created_by": "not-a-uuid"}
        )

        projected = services.records.query_records(
            make_actor(), "legacy", fields=[self.RESERVED]
        ).records[0]
        assert projected["data"] == {}  # the shadow is dropped as a pseudo-field
        assert projected[self.RESERVED] == record.created_by != "not-a-uuid"

        everything = services.records.query_records(make_actor(), "legacy", fields="*").records[0]
        assert everything["data"][self.RESERVED] == "not-a-uuid"  # and here it is
        assert everything[self.RESERVED] == record.created_by  # beside the real column

        # Site four: a multi-type search treats the key as a pseudo-field and accepts
        # it, which is how it reaches the *column* on a type whose own query path would
        # have compiled the same filter to json_extract. The paired refusal below is
        # what proves the acceptance took the pseudo-field branch rather than being
        # permissive in general: `title` is a user field on the very same type and is
        # refused by the one-type rule.
        services.search.search(
            make_actor(),
            query="Shadowed",
            filter={"field": self.RESERVED, "op": "eq", "value": "@me"},
        )
        with pytest.raises(ValidationFailedError) as exc:
            services.search.search(
                make_actor(),
                query="Shadowed",
                filter={"field": "title", "op": "eq", "value": "Shadowed"},
            )
        assert "title" in exc.value.message


# ``capabilities_document`` takes four deployment-dependent parameters, two for DD-29
# and two for DD-16, all supplied by ``describe_capabilities`` from
# ``AttachmentService``. These tests are about ``key_rules`` and care about none of
# them, so they pass fixed values rather than threading a service through; the block
# those values land in is asserted, against the service's own answers, in
# ``tests/test_attachment_surface.py`` and ``tests/test_endpoint_teaches_itself.py``.
CAPABILITIES_ARGS = {
    "semantic_enabled": False,
    "download_url_pattern": "/api/v1/attachments/{attachment_id}/download",
    "attachment_max_bytes": 26_214_400,
    "upload_url": "/api/v1/attachments",
    "upload_ticket_ttl_seconds": 300,
}


class TestReservedFieldKeysArePublished:
    """Non-negotiable 6: the rule is agent-facing, so it is published where an agent
    already looks. The precedent is that every bound an agent can hit appears
    in ``describe_capabilities``; a reserved key is the same kind of obligation, and an
    agent that reads only ``key_rules.object_type_and_field_keys`` would otherwise name
    a field ``created_by`` and take a refusal it could have avoided.
    """

    def test_capabilities_publishes_reserved_field_keys(self) -> None:
        document = capabilities_document(**CAPABILITIES_ARGS)
        assert document["key_rules"]["reserved_field_keys"] == sorted(PSEUDO_FIELDS)

    def test_reserved_field_keys_note_says_what_to_do_instead(self) -> None:
        note = capabilities_document(**CAPABILITIES_ARGS)["key_rules"]["reserved_field_keys_note"]
        assert "object type key may" in note
        assert "created_by_name" in note

    def test_the_two_pseudo_field_dicts_carry_the_same_keys(self) -> None:
        """There is no second list of reserved keys, but there is a parallel dict
        keyed by the same eight names. A pseudo-field added to one and not the other is
        a ``KeyError`` in ``describe_object_type``, and ``grep -rn PSEUDO_FIELDS`` does
        not match ``PSEUDO_FIELD_DESCRIPTIONS``' literal keys, so the drift this pins is
        one no text search could have caught.
        """
        assert set(PSEUDO_FIELD_DESCRIPTIONS) == set(PSEUDO_FIELDS)
        assert all(PSEUDO_FIELD_DESCRIPTIONS[key].strip() for key in PSEUDO_FIELDS)
