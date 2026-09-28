"""A workspace frozen to read-only, over REST (DD-38).

With ``GW_READ_ONLY`` on, every REST write is refused with 409 ``workspace_read_only``
naming ``GW_SUBSCRIBE_URL``, except the five routes ``READ_ONLY_OPEN_ROUTES`` names,
and reads and full export keep working. The MCP half, and the parity walk
between the two surfaces, is ``tests/test_mcp_read_only.py``.

An assertion marked **Fence** would pass on a tree with no freeze at all, by
construction, so it is not counted as coverage of the freeze; each is shown able to fail
by a mutation of the rule instead.

Why "nothing changed" is proven with **valid** calls and not with the placeholder sweep:
the sweep's placeholder arguments answer 422 and 404, which change no row
even with no freeze at all, so a snapshot around them cannot fail. The sweep proves the
rule's reach; the valid calls prove the absence of effect.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from glosswork.app import create_app
from glosswork.config import Settings
from glosswork.db import Database
from glosswork.scopes import RouteScope, route_scopes
from glosswork.services import ServiceBundle
from tests.conftest import auth, make_actor, mint_scope_tokens, seed_second_principal
from tests.mcp_support import seed_task_type
from tests.test_rest_scope_enforcement import _request

SUBSCRIBE_URL = "https://glosswork.example.com/subscribe?workspace=acme"
BASE_URL = "https://tracker.example.com"
LOGIN_EMAIL = "reader@example.com"
LOGIN_PASSWORD = "a-long-enough-password"
CREATOR_EMAIL = "creator@example.com"
READ_ONLY_CODE = "workspace_read_only"

# Written out here rather than imported, so that widening the set in ``scopes.py`` is a
# visible edit to this file too.
EXPECTED_OPEN_ROUTES = frozenset(
    {
        ("POST", "/api/v1/admin/backup"),
        ("POST", "/api/v1/access-tokens"),
        ("DELETE", "/api/v1/access-tokens/{token_id}"),
        ("POST", "/api/v1/me/password"),
        ("DELETE", "/api/v1/principals/{principal_id}"),
    }
)

# The non-GET routes that declare ``read``. The derived rule leaves these open, so a new
# one that writes would slip through silently; pinning them is the guard.
EXPECTED_NON_GET_READ_ROUTES = frozenset(
    {
        ("POST", "/api/v1/object-types/{object_type_key}/query"),
        ("POST", "/api/v1/search"),
        ("DELETE", "/api/v1/auth/session"),
    }
)


# ------------------------------------------------------------------ the workspace


@dataclass
class Seeded:
    """The workspace every test here freezes. Shaped like ``tests/test_mcp_agent_label.py``'s
    ``Seeded`` so that module's ``WRITE_CALLS`` argument builders apply to it unchanged."""

    plain_a: str  # no links either way
    target_b: str  # linked to from child_c.parent
    child_c: str
    deleted_d: str
    comment_id: str
    second_principal: str
    attachment_id: str
    creator_id: str
    token_id: str


@dataclass
class Workspace:
    data_dir: Path
    seeded: Seeded
    tokens: dict[str, str]
    creator_admin_token: str
    ticket_authorization: str


def settings_for(data_dir: Path, **overrides: Any) -> Settings:
    """Embedding off, as in ``tests/conftest.py``. ``GW_BASE_URL`` is set because the
    upload ticket needs it, and ``testserver`` is admitted to ``/mcp`` because the
    base URL turns the transport's host check on."""
    return Settings(
        data_dir=data_dir,
        embedding_enabled=False,
        base_url=BASE_URL,
        mcp_allowed_hosts="testserver",
        **overrides,
    )


def seed_services(services: ServiceBundle, db: Database) -> Seeded:
    """The ``seeded`` fixture of ``tests/test_mcp_agent_label.py``, plus an attachment, a
    signed-in-able reader, a ``creator``, and a token to revoke."""
    seed_task_type(services)
    a = services.records.create_record(make_actor(), "task", {"title": "A"})
    b = services.records.create_record(make_actor(), "task", {"title": "B"})
    c = services.records.create_record(make_actor(), "task", {"title": "C"})
    d = services.records.create_record(make_actor(), "task", {"title": "D"})
    services.records.link_records(make_actor(), c.key, "parent", [b.key])
    services.records.delete_record(make_actor(), d.key)
    comment = services.comments.add_comment(make_actor(), a.key, "seed comment")
    second_principal = seed_second_principal(db)
    services.access.grant(make_actor(), "task", second_principal, "read")
    attachment = services.attachments.upload(
        make_actor(), filename="seed.txt", content_type="text/plain", content=b"seeded bytes"
    )
    services.principals.create_user(
        make_actor(), email=LOGIN_EMAIL, display_name="Reader", password=LOGIN_PASSWORD
    )
    creator = services.principals.create_user(
        make_actor(), email=CREATOR_EMAIL, display_name="Creator", role="creator"
    )
    token = services.tokens.mint(make_actor(), name="to-revoke", scope="read")
    return Seeded(
        plain_a=a.key,
        target_b=b.key,
        child_c=c.key,
        deleted_d=d.key,
        comment_id=comment.id,
        second_principal=second_principal,
        attachment_id=attachment.id,
        creator_id=creator.id,
        token_id=token.row.id,
    )


def seed_workspace(data_dir: Path) -> Workspace:
    """Seed a workspace **with the flag off**, through an app whose lifespan is entered
    and exited before the app under test starts, over the same data directory. Everything
    minted here, including the upload ticket, exists before the freeze."""
    app = create_app(settings_for(data_dir))
    with TestClient(app):
        services: ServiceBundle = app.state.services
        tokens = mint_scope_tokens(services)
        seeded = seed_services(services, app.state.db)
        creator_admin_token = services.tokens.mint(
            make_actor(), name="creator-admin", scope="admin", principal_id=seeded.creator_id
        ).plaintext
        ticket = services.attachments.create_upload_ticket(make_actor(), "late.bin")
    return Workspace(
        data_dir=data_dir,
        seeded=seeded,
        tokens=tokens,
        creator_admin_token=creator_admin_token,
        ticket_authorization=str(ticket["authorization"]),
    )


@contextmanager
def running(
    workspace: Workspace, *, read_only: bool, subscribe_url: str | None = SUBSCRIBE_URL
) -> Iterator[TestClient]:
    """The app under test over the seeded data directory, presenting the ``admin`` PAT
    by default."""
    app = create_app(
        settings_for(workspace.data_dir, read_only=read_only, subscribe_url=subscribe_url)
    )
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {workspace.tokens['admin']}"
        yield client


def snapshot(data_dir: Path) -> dict[str, list[tuple[Any, ...]]]:
    """Every row of every **tenant** table, minus the two columns credential resolution
    writes on every call, refused or not. Virtual tables are skipped because their
    shadow tables are ordinary tables and are read here directly.

    ``usage_counters`` is excluded for the same reason those two columns are, and the
    exclusion is a statement rather than a convenience (DD-39). A refused write is
    still a tool call, and the operator's counter records that it happened, by name and
    by error code. What this function is asserting is that **nothing the tenant can read
    changed**, and an operator's counter is not something the tenant can read: it is not
    exported, not backed up into their view, and not reachable from any tenant
    credential. Including it here would make the assertion say "the deployment wrote no
    byte anywhere", which was never true -- ``last_used_at`` and ``last_seen_at`` are
    already excluded on exactly that ground.
    """
    database = Database.connect(data_dir / "glosswork.sqlite3")
    try:
        with database.read() as conn:
            tables = [
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type = 'table' "
                        "AND name NOT LIKE 'sqlite_%' "
                        "AND upper(sql) NOT LIKE 'CREATE VIRTUAL TABLE%' ORDER BY name"
                    )
                )
            ]
            excluded = {"access_tokens": {"last_used_at"}, "sessions": {"last_seen_at"}}
            tables = [t for t in tables if t != "usage_counters"]
            rows: dict[str, list[tuple[Any, ...]]] = {}
            for table in tables:
                columns = [
                    info[1]
                    for info in conn.execute(text(f'PRAGMA table_info("{table}")'))
                    if info[1] not in excluded.get(table, set())
                ]
                selected = ", ".join(f'"{column}"' for column in columns)
                rows[table] = sorted(
                    (tuple(row) for row in conn.execute(text(f'SELECT {selected} FROM "{table}"'))),
                    key=repr,
                )
    finally:
        database.close()
    return rows


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return seed_workspace(tmp_path / "data")


def outcome(response: Any) -> tuple[int, str | None]:
    """A response's status and envelope code, compared as one value so that a failing
    assertion reports both."""
    try:
        code = response.json()["error"]["code"]
    except Exception:
        code = None
    return response.status_code, code


def assert_read_only_refusal(response: Any, subscribe_url: str | None = SUBSCRIBE_URL) -> None:
    assert outcome(response) == (409, READ_ONLY_CODE), response.text
    error = response.json()["error"]
    assert error["code"] == READ_ONLY_CODE, error
    assert error["details"]["subscribe_url"] == subscribe_url, error
    assert error["details"]["setting"] == "GW_READ_ONLY", error
    if subscribe_url is not None:
        assert subscribe_url in error["message"], error


def is_read_only_refusal(response: Any) -> bool:
    if response.status_code != 409:
        return False
    try:
        return bool(response.json()["error"]["code"] == READ_ONLY_CODE)
    except Exception:
        return False


# --------------------------------------------------------------- the valid writes

RestCall = Callable[[TestClient, Seeded], Any]

# One valid request per REST write: every REST twin of a ``WRITE_CALLS`` tool in
# ``tests/test_mcp_agent_label.py`` with the same arguments, plus creating a principal, a
# saved view, and a CSV import. ``test_every_valid_write_succeeds_with_the_flag_off`` is
# what shows each one is valid, so a refusal of it is a refusal of a real write.
VALID_WRITES: dict[str, RestCall] = {
    "create_record": lambda c, s: c.post(
        "/api/v1/object-types/task/records", json={"title": "new"}
    ),
    "update_record": lambda c, s: c.patch(
        f"/api/v1/records/{s.plain_a}", json={"values": {"title": "changed"}}
    ),
    "delete_record": lambda c, s: c.delete(f"/api/v1/records/{s.plain_a}"),
    "restore_record": lambda c, s: c.post(f"/api/v1/records/{s.deleted_d}/restore"),
    "bulk_update_records": lambda c, s: c.post(
        "/api/v1/object-types/task/bulk-update",
        json={
            "filter": {"field": "title", "op": "eq", "value": "A"},
            "values": {"status": "doing"},
        },
    ),
    "link_records": lambda c, s: c.post(
        f"/api/v1/records/{s.plain_a}/links/parent", json={"to_records": [s.target_b]}
    ),
    "unlink_records": lambda c, s: c.request(
        "DELETE", f"/api/v1/records/{s.child_c}/links/parent", json={"to_records": [s.target_b]}
    ),
    "add_comment": lambda c, s: c.post(
        f"/api/v1/records/{s.plain_a}/comments", json={"body": "attributed"}
    ),
    "update_comment": lambda c, s: c.patch(
        f"/api/v1/comments/{s.comment_id}", json={"body": "edited"}
    ),
    "delete_comment": lambda c, s: c.delete(f"/api/v1/comments/{s.comment_id}"),
    "create_object_type": lambda c, s: c.post(
        "/api/v1/object-types",
        json={
            "key": "note",
            "name": "Note",
            "name_plural": "Notes",
            "description": "A free-form note attached to nothing in particular.",
            "key_prefix": "NOTE",
        },
    ),
    "update_object_type": lambda c, s: c.patch("/api/v1/object-types/task", json={"name": "Job"}),
    "add_field": lambda c, s: c.post(
        "/api/v1/object-types/task/fields",
        json={
            "key": "effort",
            "name": "Effort",
            "type": "integer",
            "description": "Estimated effort in ideal days.",
        },
    ),
    "update_field": lambda c, s: c.patch(
        "/api/v1/object-types/task/fields/notes",
        json={"changes": {"description": "Working notes, rewritten."}},
    ),
    "propose_schema_change": lambda c, s: c.post(
        "/api/v1/schema-proposals",
        json={
            "change_type": "delete_field",
            "object_type": "task",
            "field_key": "due",
            "reason": "Nobody fills it in.",
        },
    ),
    "create_text_attachment": lambda c, s: c.post(
        "/api/v1/attachments", files={"file": ("note.txt", b"hello", "text/plain")}
    ),
    "set_object_type_grant": lambda c, s: c.put(
        f"/api/v1/object-types/task/grants/{s.second_principal}", json={"level": "write"}
    ),
    "revoke_object_type_grant": lambda c, s: c.delete(
        f"/api/v1/object-types/task/grants/{s.second_principal}"
    ),
    "create_principal": lambda c, s: c.post(
        "/api/v1/principals",
        json={"type": "user", "email": "pat@example.com", "display_name": "Pat"},
    ),
    "create_saved_view": lambda c, s: c.post(
        "/api/v1/object-types/task/saved-views",
        json={"name": "My view", "config": {"columns": ["title"]}, "mode": "table"},
    ),
    "import_csv": lambda c, s: c.post(
        "/api/v1/object-types/task/import",
        files={"file": ("data.csv", b"title\r\nimported\r\n", "text/csv")},
        data={"mode": "create"},
    ),
}


@pytest.mark.parametrize("name", sorted(VALID_WRITES))
def test_every_valid_write_is_refused_and_changes_nothing(workspace: Workspace, name: str) -> None:
    """Without the flag each call answers 2xx and the snapshot differs. With the flag,
    each is refused 409 ``workspace_read_only`` naming the subscribe URL, and no row in
    any table changes."""
    before = snapshot(workspace.data_dir)
    with running(workspace, read_only=True) as client:
        response = VALID_WRITES[name](client, workspace.seeded)
    after = snapshot(workspace.data_dir)
    # One comparison, so that a failure reports the status, the code and whether rows
    # changed together, and neither half runs only after the other.
    assert (*outcome(response), after == before) == (409, READ_ONLY_CODE, True), name
    assert_read_only_refusal(response)


@pytest.mark.parametrize("name", sorted(VALID_WRITES))
def test_every_valid_write_succeeds_with_the_flag_off(workspace: Workspace, name: str) -> None:
    """**Fence.** The same call succeeds and changes rows with the flag off, which is what
    makes the refusal above a refusal of a real write."""
    before = snapshot(workspace.data_dir)
    with running(workspace, read_only=False) as client:
        response = VALID_WRITES[name](client, workspace.seeded)
    after = snapshot(workspace.data_dir)
    assert 200 <= response.status_code < 300, (name, response.status_code, response.text)
    assert after != before, f"{name} is not a write: it changed no row"


# ------------------------------------------------------------------ the rule's reach


def _selected(app: FastAPI) -> list[RouteScope]:
    """The rule: non-GET and declaring above ``read``."""
    return [
        entry
        for entry in route_scopes(app)
        if entry.method != "GET" and entry.scope in ("write", "admin")
    ]


def test_every_write_route_outside_the_open_set_is_refused(workspace: Workspace) -> None:
    """Every route the rule selects, minus the open set, driven the placeholder way
    ``tests/test_rest_scope_enforcement.py`` drives its sweep, answers 409
    ``workspace_read_only``. Without the flag these answer 422, 404, 409
    ``feature_disabled`` or 200."""
    with running(workspace, read_only=True) as client:
        app: FastAPI = client.app  # type: ignore[assignment]
        refused_wrongly = []
        for entry in _selected(app):
            if (entry.method, entry.path) in EXPECTED_OPEN_ROUTES:
                continue
            response = _request(client, entry)
            if not is_read_only_refusal(response):
                refused_wrongly.append((entry.key, response.status_code))
    assert refused_wrongly == [], refused_wrongly


def test_every_other_route_is_not_refused(workspace: Workspace) -> None:
    """**Fence.** Every route the rule does not select, and every open route, answers
    something other than ``workspace_read_only`` for the same placeholder request. Made to
    fail by dropping the rule's method condition, or by emptying the open set."""
    with running(workspace, read_only=True) as client:
        app: FastAPI = client.app  # type: ignore[assignment]
        selected = {(entry.method, entry.path) for entry in _selected(app)}
        refused = []
        for entry in route_scopes(app):
            key = (entry.method, entry.path)
            if key in selected and key not in EXPECTED_OPEN_ROUTES:
                continue
            response = _request(client, entry)
            if is_read_only_refusal(response):
                refused.append(entry.key)
    assert refused == [], refused


def test_the_open_set_is_pinned_by_equality() -> None:
    """Pinned by equality, so widening it is a visible edit to this test."""
    from glosswork.scopes import READ_ONLY_OPEN_ROUTES

    assert READ_ONLY_OPEN_ROUTES == EXPECTED_OPEN_ROUTES


def test_every_open_route_is_a_route_the_rule_selects(app: FastAPI) -> None:
    """**Fence.** An open route the rule would not refuse anyway is dead weight that reads
    like a decision."""
    selected = {(entry.method, entry.path) for entry in _selected(app)}
    assert EXPECTED_OPEN_ROUTES <= selected


def test_the_non_get_read_routes_are_exactly_the_three_named(app: FastAPI) -> None:
    """**A guard on the derived rule.** The rule leaves a non-GET route that declares
    ``read`` open. Today those are a query, a search and a sign-out; a fourth fails here
    and forces somebody to decide whether it writes.

    A **fence** with respect to the freeze: what it guards is a future route, not the
    freeze. It was shown able to fail by registering a fourth such route in a scratch
    copy."""
    found = {
        (entry.method, entry.path)
        for entry in route_scopes(app)
        if entry.method != "GET" and entry.scope == "read"
    }
    assert found == EXPECTED_NON_GET_READ_ROUTES


# --------------------------------------------------------------------- the ticket


def test_an_upload_ticket_minted_before_the_freeze_is_refused(workspace: Workspace) -> None:
    """``require_capability``'s ticket branch never runs
    ``enforce_scope``, so a check placed only after it would let a ticket minted before
    a freeze restart upload after it. Without the check this answers 200 and adds a row."""
    before = snapshot(workspace.data_dir)
    with running(workspace, read_only=True) as client:
        response = client.post(
            "/api/v1/attachments",
            files={"file": ("late.bin", b"\x00\x01late", "application/octet-stream")},
            headers={"Authorization": workspace.ticket_authorization},
        )
    after = snapshot(workspace.data_dir)
    assert (*outcome(response), after["attachments"] == before["attachments"]) == (
        409,
        READ_ONLY_CODE,
        True,
    )
    assert_read_only_refusal(response)
    assert after == before


# -------------------------------------------------------------------------- copy


def test_without_a_subscribe_url_the_refusal_names_the_setting(workspace: Workspace) -> None:
    """Self-host has no subscribe page: the message names
    ``GW_READ_ONLY`` instead, and ``details.subscribe_url`` is ``null``."""
    with running(workspace, read_only=True, subscribe_url=None) as client:
        response = VALID_WRITES["add_comment"](client, workspace.seeded)
    assert_read_only_refusal(response, subscribe_url=None)
    error = response.json()["error"]
    assert error["message"] == (
        "This workspace is read-only, so POST /api/v1/records/{ref}/comments changed "
        "nothing. Reading, searching and exporting still work. An administrator of this "
        "deployment turned writes off with GW_READ_ONLY."
    )
    assert error["details"]["attempted"] == "POST /api/v1/records/{ref}/comments"


def test_with_a_subscribe_url_the_message_is_the_approved_copy(workspace: Workspace) -> None:
    """The approved message, word for word."""
    with running(workspace, read_only=True) as client:
        response = VALID_WRITES["update_record"](client, workspace.seeded)
    assert_read_only_refusal(response)
    assert response.json()["error"]["message"] == (
        "This workspace is read-only, so PATCH /api/v1/records/{ref} changed nothing. "
        "Reading, searching and exporting still work. To make changes again, subscribe at "
        f"{SUBSCRIBE_URL}."
    )


# ------------------------------------------------------------ what stays open


def test_both_exports_still_carry_a_record_seeded_before_the_freeze(
    workspace: Workspace,
) -> None:
    """**Fence.** The full export is a GET that declares ``admin``, so dropping the rule's
    method condition refuses it, which makes this fail."""
    with running(workspace, read_only=True) as client:
        full = client.get("/api/v1/admin/export")
        csv = client.get("/api/v1/object-types/task/export")
        download = client.get(f"/api/v1/attachments/{workspace.seeded.attachment_id}/download")
    assert full.status_code == 200, full.text
    assert workspace.seeded.target_b in full.text
    assert csv.status_code == 200, csv.text
    assert workspace.seeded.target_b in csv.text
    assert download.status_code == 200
    assert download.content == b"seeded bytes"


def test_the_backup_still_runs(workspace: Workspace) -> None:
    """**Fence.** The backup is a POST that declares ``admin``, so only the open set keeps
    it running through the frozen days, and emptying the open set makes this fail."""
    with running(workspace, read_only=True) as client:
        response = client.post("/api/v1/admin/backup")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-tar")
    assert len(response.content) > 0


def test_the_credential_calls_in_the_open_set_still_work(workspace: Workspace) -> None:
    """**Fence.** Made to fail by emptying the open set. Minting a token (a person back to
    export needs an agent connected), revoking one, and deactivating a principal."""
    with running(workspace, read_only=True) as client:
        minted = client.post("/api/v1/access-tokens", json={"name": "exporter", "scope": "read"})
        revoked = client.delete(f"/api/v1/access-tokens/{workspace.seeded.token_id}")
        deactivated = client.delete(f"/api/v1/principals/{workspace.seeded.second_principal}")
    # The status alone: a successful mint's body carries the token's plaintext.
    assert 200 <= minted.status_code < 300, minted.status_code
    assert 200 <= revoked.status_code < 300, revoked.text
    assert 200 <= deactivated.status_code < 300, deactivated.text


def test_sign_in_still_issues_a_session(workspace: Workspace) -> None:
    """**Fence.** Sign-in is credential-exempt, so it never reaches the scope
    dependency."""
    with running(workspace, read_only=True) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": LOGIN_EMAIL, "password": LOGIN_PASSWORD},
            headers={"Authorization": ""},
        )
    assert response.status_code == 200, response.text


def test_a_read_pat_on_a_write_route_is_still_told_its_scope(workspace: Workspace) -> None:
    """**Fence.** Scope answers before read-only: a ``read`` token cannot write on
    any workspace, and telling its holder to subscribe would send them to fix the wrong
    thing."""
    with running(workspace, read_only=True) as client:
        response = VALID_WRITES["add_comment"](
            TestClient(client.app, headers=auth(workspace.tokens["read"])), workspace.seeded
        )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "insufficient_scope"


def test_a_creator_admin_pat_is_told_read_only_before_its_role(workspace: Workspace) -> None:
    """Read-only answers before the role refusal, because the scope dependency, which
    carries the check, is listed before ``require_role`` on every role route. Without the
    flag this answers 403 ``forbidden``."""
    with running(workspace, read_only=True) as client:
        response = client.post(
            "/api/v1/admin/blobs/sweep", headers=auth(workspace.creator_admin_token)
        )
    assert_read_only_refusal(response)


# ------------------------------------------------------------- the flag unset


def test_with_the_flag_unset_the_sweep_finds_no_refusal(workspace: Workspace) -> None:
    """**Fence.** With ``GW_READ_ONLY`` off, no route answers ``workspace_read_only``."""
    with running(workspace, read_only=False) as client:
        app: FastAPI = client.app  # type: ignore[assignment]
        refused = [
            entry.key
            for entry in route_scopes(app)
            if is_read_only_refusal(_request(client, entry))
        ]
    assert refused == [], refused
