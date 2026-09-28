"""The principal directory: the read, its route, its MCP tool, and the fence that keeps
every other ``/principals*`` route where it is.

The whole point of this surface is a **narrower projection**, not a wider audience: the
route drops ``require_role("admin")`` precisely because it stops carrying ``role``,
``auth_provider`` and ``external_id``. So the projection assertions here are by set
equality and then by name, and they are the reason the gate change is safe rather than a
detail beside it (DD-25).
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.routing import BaseRoute

from glosswork.app import create_app
from glosswork.config import Settings
from glosswork.db import Database
from glosswork.errors import ValidationFailedError
from glosswork.repositories.sqlite import SqlitePrincipalRepository
from glosswork.routes import identity
from glosswork.scopes import declared_role, declared_scope, flatten_routes
from glosswork.services import ServiceBundle
from glosswork.services.principals import DIRECTORY_MAX_LIMIT
from tests.conftest import make_actor

PASSWORD = "correct-horse-battery-staple"

# The five keys of ``envelopes.principal_directory_doc``, written out rather than
# imported, so a change to that function is a diff here too.
DIRECTORY_KEYS = {"id", "display_name", "email", "type", "is_active"}

# Withheld by name. ``password_hash`` is on no envelope; the other four are on
# ``principal_doc`` and are what the permissions panel needs, which is why that panel keeps
# its admin gate (DD-42).
WITHHELD_KEYS = ("role", "auth_provider", "external_id", "password_hash", "description")


# --------------------------------------------------------------------------- fixtures


def seed_people(services: ServiceBundle) -> None:
    """Four principals with deliberately overlapping name and email substrings."""
    services.principals.create_user(
        make_actor(),
        email="sarah.okonjo@example.com",
        display_name="Sarah Okonjo",
        role="member",
        password=PASSWORD,
    )
    services.principals.create_user(
        make_actor(),
        email="sam.okafor@example.com",
        display_name="Sam Okafor",
        role="creator",
        password=PASSWORD,
    )
    departed = services.principals.create_user(
        make_actor(),
        email="dev.null@example.com",
        display_name="Devon Null",
        role="member",
        password=PASSWORD,
    )
    services.principals.deactivate_principal(make_actor(), departed.id)
    services.principals.create_service_account(
        make_actor(),
        display_name="Nightly Importer",
        description="Loads the vendor feed every night at 02:00 UTC.",
    )


@pytest.fixture
def people(services: ServiceBundle) -> ServiceBundle:
    seed_people(services)
    return services


# --------------------------------------------------------- the repository read (DD-2)


class TestRepository:
    def test_substring_matches_display_name_case_insensitively(
        self, db: Database, people: ServiceBundle
    ) -> None:
        repo = SqlitePrincipalRepository()
        with db.read() as conn:
            found = repo.search_principals(
                conn, q="OKONJO", principal_type=None, include_inactive=False, limit=50
            )
        assert [p.display_name for p in found] == ["Sarah Okonjo"]

    def test_substring_matches_email_case_insensitively(
        self, db: Database, people: ServiceBundle
    ) -> None:
        repo = SqlitePrincipalRepository()
        with db.read() as conn:
            found = repo.search_principals(
                conn, q="SAM.OKAFOR@", principal_type=None, include_inactive=False, limit=50
            )
        assert [p.display_name for p in found] == ["Sam Okafor"]

    def test_one_substring_can_match_both_columns_across_rows(
        self, db: Database, people: ServiceBundle
    ) -> None:
        """``ok`` hits ``Okonjo``/``Okafor`` by name and both by email: one OR, not two
        queries the caller has to union."""
        repo = SqlitePrincipalRepository()
        with db.read() as conn:
            found = repo.search_principals(
                conn, q="ok", principal_type=None, include_inactive=False, limit=50
            )
        assert {p.display_name for p in found} == {"Sarah Okonjo", "Sam Okafor"}

    def test_inactive_is_excluded_by_default_and_included_on_request(
        self, db: Database, people: ServiceBundle
    ) -> None:
        repo = SqlitePrincipalRepository()
        with db.read() as conn:
            active = repo.search_principals(
                conn, q="Devon", principal_type=None, include_inactive=False, limit=50
            )
            everyone = repo.search_principals(
                conn, q="Devon", principal_type=None, include_inactive=True, limit=50
            )
        assert active == []
        assert [p.display_name for p in everyone] == ["Devon Null"]

    def test_type_filter_separates_users_from_service_accounts(
        self, db: Database, people: ServiceBundle
    ) -> None:
        repo = SqlitePrincipalRepository()
        with db.read() as conn:
            accounts = repo.search_principals(
                conn, q=None, principal_type="service_account", include_inactive=False, limit=50
            )
        # The deployment's own bootstrap principal is a service account too, so this
        # asserts the filter kept only service accounts rather than a literal list.
        assert {p.type for p in accounts} == {"service_account"}
        assert "Nightly Importer" in {p.display_name for p in accounts}
        assert "Sarah Okonjo" not in {p.display_name for p in accounts}

    def test_limit_is_honored(self, db: Database, people: ServiceBundle) -> None:
        repo = SqlitePrincipalRepository()
        with db.read() as conn:
            assert (
                len(
                    repo.search_principals(
                        conn, q=None, principal_type=None, include_inactive=False, limit=2
                    )
                )
                == 2
            )

    def test_principals_by_ids_returns_the_named_rows_and_ignores_unknown_ids(
        self, db: Database, people: ServiceBundle
    ) -> None:
        repo = SqlitePrincipalRepository()
        with db.read() as conn:
            everyone = repo.search_principals(
                conn, q=None, principal_type=None, include_inactive=True, limit=50
            )
            wanted = [everyone[0].id, "not-a-principal"]
            found = repo.principals_by_ids(conn, wanted)
        assert [p.id for p in found] == [everyone[0].id]

    def test_principals_by_ids_of_nothing_is_no_query(
        self, db: Database, people: ServiceBundle
    ) -> None:
        repo = SqlitePrincipalRepository()
        with db.read() as conn:
            assert repo.principals_by_ids(conn, []) == []


# ----------------------------------------------------------- the service contract


class TestService:
    def test_service_caps_the_limit_at_the_documented_maximum(self, people: ServiceBundle) -> None:
        """The clamp is the service's, not the repository's: policy lives in the layer
        that owns it, and asking for more than the maximum is answered rather than
        refused."""
        assert people.principals.search_principals(
            limit=10_000
        ) == people.principals.search_principals(limit=DIRECTORY_MAX_LIMIT)

    def test_service_defaults_exclude_inactive(self, people: ServiceBundle) -> None:
        assert "Devon Null" not in {p.display_name for p in people.principals.search_principals()}

    def test_service_rejects_an_unknown_type(self, people: ServiceBundle) -> None:
        with pytest.raises(ValidationFailedError):
            people.principals.search_principals(principal_type="robot")

    def test_service_rejects_a_limit_below_one(self, people: ServiceBundle) -> None:
        with pytest.raises(ValidationFailedError):
            people.principals.search_principals(limit=0)


# ------------------------------------------------------------------- the REST route


def directory(client: TestClient, **params: Any) -> Any:
    response = client.get("/api/v1/principals/directory", params=params)
    assert response.status_code == 200, response.text
    return response.json()["principals"]


@pytest.fixture
def seeded_client(app: FastAPI, client: TestClient) -> TestClient:
    seed_people(app.state.services)
    return client


class TestRoute:
    def test_the_body_carries_exactly_the_five_documented_keys(
        self, seeded_client: TestClient
    ) -> None:
        entries = directory(seeded_client)
        assert entries, "the fixture seeded four principals"
        for entry in entries:
            assert set(entry) == DIRECTORY_KEYS

    @pytest.mark.parametrize("withheld", WITHHELD_KEYS)
    def test_each_withheld_key_is_absent_by_name(
        self, seeded_client: TestClient, withheld: str
    ) -> None:
        for entry in directory(seeded_client):
            assert withheld not in entry

    def test_a_read_scoped_pat_gets_200(
        self, seeded_client: TestClient, api_tokens: dict[str, str]
    ) -> None:
        """The scope half. ``GET /principals`` requires ``admin`` scope; this one does
        not, which is what lets a read-only agent look somebody up."""
        response = seeded_client.get(
            "/api/v1/principals/directory",
            headers={"Authorization": f"Bearer {api_tokens['read']}"},
        )
        assert response.status_code == 200

    def test_the_path_is_not_captured_by_the_principal_id_route(
        self, seeded_client: TestClient
    ) -> None:
        """The declaration-order requirement, pinned. If ``/principals/{principal_id}``
        were declared first it would match ``directory`` as an id and answer 404 (or,
        worse, apply its ``admin`` gate), so a passing 200 here is the ordering."""
        response = seeded_client.get("/api/v1/principals/directory")
        assert response.status_code == 200
        assert isinstance(response.json()["principals"], list)

    def test_query_filters_by_name_and_by_email(self, seeded_client: TestClient) -> None:
        assert [e["display_name"] for e in directory(seeded_client, q="okonjo")] == ["Sarah Okonjo"]
        assert [e["display_name"] for e in directory(seeded_client, q="dev.null@")] == []
        assert [
            e["display_name"]
            for e in directory(seeded_client, q="dev.null@", include_inactive=True)
        ] == ["Devon Null"]

    def test_type_narrows_to_service_accounts(self, seeded_client: TestClient) -> None:
        entries = directory(seeded_client, type="service_account")
        assert {e["type"] for e in entries} == {"service_account"}
        assert "Nightly Importer" in {e["display_name"] for e in entries}


class TestRouteIsReadableWithoutTheAdminRole:
    """The header claim of section 1, over a real session rather than a constructed
    actor: a ``member`` who can reach nothing else in this namespace can reach this."""

    @pytest.fixture
    def member_session(self, tmp_path: Path) -> TestClient:
        settings = Settings(
            data_dir=tmp_path / "data", cookie_secure=False, embedding_enabled=False
        )
        app = create_app(settings)
        with TestClient(app) as client:
            services: ServiceBundle = app.state.services
            seed_people(services)
            services.principals.create_user(
                make_actor(),
                email="dana@example.com",
                display_name="Dana",
                role="member",
                password=PASSWORD,
            )
            response = client.post(
                "/api/v1/auth/login", json={"email": "dana@example.com", "password": PASSWORD}
            )
            assert response.status_code == 200, response.text
            yield client

    def test_a_member_role_session_gets_200(self, member_session: TestClient) -> None:
        response = member_session.get("/api/v1/principals/directory")
        assert response.status_code == 200
        assert {e["display_name"] for e in response.json()["principals"]} >= {
            "Sarah Okonjo",
            "Sam Okafor",
        }

    def test_the_same_member_still_cannot_read_the_management_list(
        self, member_session: TestClient
    ) -> None:
        """The fence's live half: widening was one route, not the namespace."""
        assert member_session.get("/api/v1/principals").status_code == 403


# -------------------------------------------------------------------------- the fence
#
# *(Fence.)* This guards behaviour the directory deliberately does not change, so it cannot
# fail by construction and is not counted as a measured assertion.


PRE_EXISTING_PRINCIPAL_ROUTES = [
    ("/api/v1/principals", "GET"),
    ("/api/v1/principals", "POST"),
    ("/api/v1/principals/{principal_id}", "GET"),
    ("/api/v1/principals/{principal_id}", "PATCH"),
    ("/api/v1/principals/{principal_id}", "DELETE"),
    ("/api/v1/principals/{principal_id}/password", "POST"),
]


def _route(app: FastAPI, path: str, method: str) -> BaseRoute:
    for route in flatten_routes(app):
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"no route {method} {path}")


@pytest.mark.parametrize(("path", "method"), PRE_EXISTING_PRINCIPAL_ROUTES)
def test_fence_every_pre_existing_principal_route_keeps_both_dependencies(
    app: FastAPI, path: str, method: str
) -> None:
    """Asserted by inspecting the route's declared scope and role rather than by calling
    it: a 403 could come from anywhere, but a dropped declaration is the actual defect.
    Both axes, not one -- scope and role are independent (DD-11), and losing either
    widens the route."""
    route = _route(app, path, method)
    assert declared_scope(route) == "admin"
    assert declared_role(route) == "admin"


def test_fence_the_directory_route_declares_no_role_dependency(app: FastAPI) -> None:
    route = _route(app, "/api/v1/principals/directory", "GET")
    assert declared_scope(route) == "read"
    assert declared_role(route) is None


def test_the_route_module_never_names_role(app: FastAPI) -> None:
    """Accept clause 7, as a grep: ``role`` appears in this module only on the
    management routes' dependencies and bodies, never on a directory response."""
    source = inspect.getsource(identity.principal_directory)
    assert '"role"' not in source
    assert "principal_directory_doc" in source
