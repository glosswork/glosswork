"""``GET /api/v1/workspace``: what this deployment is called and who is in it (DD-28).

``docs/DESIGN.md`` 4.3 and 8.1 put a workspace name and an "N people · M agents" line at the top
of the sidebar. Building it surfaced that **neither value existed**: there is no workspace name
anywhere in the product, and there is no count of anything -- ``GET /principals/directory``
returns rows bounded at 50, and every principal's agent labels sit behind ``require_scope
("admin")``. This is the one bounded read that answers both, and these are its tests.

**What the two numbers mean**, in the one place the definition lives outside the service:

- ``people`` counts active principals of type ``user``.
- ``agents`` counts registered agent labels. FR-I6 auto-registers a label on an agent's first
  call, so a label exists exactly when an agent has acted here, which is what "3 agents" should
  mean to somebody reading the sidebar.
- A **service account is not a person**. ``docs/DESIGN.md`` 6.5 says a service account is an
  agent, and it reaches the agent count through the label it presents rather than on its own.
- A service account acting with **no** label is in neither number. DD-17 made a label present on
  writes from every surface, so this is now the exception rather than the norm, but it is a real
  case and ``test_a_service_account_with_no_label_is_in_neither_count`` pins it so it is a stated
  limitation rather than a surprise.

**Why ``read`` scope**, argued rather than assumed: two integers with no names in them are
strictly less disclosure than ``GET /api/v1/principals/directory``, which already serves every
authenticated caller each principal's id, display name, email, type and active flag at ``read``
scope. A count cannot be the thing that needs protecting when the list it counts is published.
``test_a_read_scope_credential_is_accepted`` is the assertion that keeps that true, and it is
the whole reason this is a new route rather than a widened admin one.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from glosswork.config import Settings
from glosswork.db import Database
from glosswork.repositories.models import PrincipalRow
from glosswork.repositories.sqlite import SqlitePrincipalRepository
from glosswork.services import ServiceBundle
from tests.conftest import auth, mint_scope_tokens, seed_second_principal

WORKSPACE_PATH = "/api/v1/workspace"

#: The bootstrap principal every fresh database carries (``actor.BOOTSTRAP_PRINCIPAL_ID``).
BOOTSTRAP = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
def app_db(app: FastAPI, client: TestClient) -> Database:
    """The running app's own ``Database``, for seeding rows the API is then asked about.

    Depends on ``client`` for the reason ``app_services`` does: the lifespan constructs
    ``app.state.db``, so it does not exist until the app has started.
    """
    database: Database = app.state.db
    return database


def _body(client: TestClient, headers: dict[str, str] | None = None) -> dict[str, Any]:
    response = client.get(WORKSPACE_PATH, headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


class TestShape:
    def test_the_document_has_exactly_five_keys_and_the_fifth_is_trial(
        self, client: TestClient
    ) -> None:
        """Pinned by equality, not by ``in``: this document is read by one sidebar block, one
        first-run screen and one banner, and a key added here without a reader is a disclosure
        nobody asked for. The counterpart to DD-25's two sidecars naming what they withhold.

        ``mcp_url`` is the fourth key, and it has a reader: the first-run screen's prompt block
        (``docs/DESIGN.md`` 8.5) has to print the URL an agent should connect to, and the browser
        cannot compose it. See ``TestMcpUrl``. ``trial`` is the fifth, read by the trial banner.
        See ``TestTrial``.
        """
        assert set(_body(client)) == {"name", "people", "agents", "mcp_url", "trial"}

    def test_the_counts_are_integers_and_never_null(self, client: TestClient) -> None:
        body = _body(client)
        assert isinstance(body["people"], int), body
        assert isinstance(body["agents"], int), body


class TestName:
    def test_name_is_null_when_the_setting_is_unset(self, client: TestClient) -> None:
        """The ``client`` fixture builds an app with no ``GW_WORKSPACE_NAME``.

        Null, and **not** the product name: ``docs/DESIGN.md`` 4.3 says in terms that the
        product name does not appear in the shell and the workspace's does, so falling back to
        "Glosswork" would contradict that. An unnamed deployment renders
        the tile alone.
        """
        assert _body(client)["name"] is None

    def test_name_is_the_setting_when_it_is_set(self, tmp_path: Any) -> None:
        """A second app, built with the setting present.

        Built here rather than through the ``app`` fixture because ``Settings`` is read once at
        construction: overriding it after the app exists would test a value the running
        application never saw.
        """
        from glosswork.app import create_app

        settings = Settings(data_dir=tmp_path, embedding_enabled=False, workspace_name="Northwind")
        named_app: FastAPI = create_app(settings)
        with TestClient(named_app) as named:
            tokens = mint_scope_tokens(named_app.state.services)
            named.headers["Authorization"] = f"Bearer {tokens['admin']}"
            assert _body(named)["name"] == "Northwind"


class TestMcpUrl:
    """``mcp_url`` (DD-28): the URL an agent connects to, composed here
    rather than in the browser.

    **Why the server composes it.** The first-run screen prints this URL for a person to paste
    into an agent, and the obvious client-side answer -- the browser's own origin plus ``/mcp``
    -- is wrong in the configuration the transport's hardening exists for. ``create_app`` builds
    the ``/mcp`` Host and Origin allowlists from ``GW_BASE_URL`` (DD-15), so an operator browsing
    an origin outside that allowlist would be handed a URL this deployment refuses. The URL's
    consumer is also the agent rather than the person at the keyboard, and an agent elsewhere
    cannot reach the ``http://localhost:8000`` a laptop trial is browsed at.

    **Null rather than a guess** when ``GW_BASE_URL`` is unset, which is legal outside OIDC mode.
    An unset base URL is exactly the case where the allowlist is empty and the check disables
    itself, so the browser's origin always works there and the client falls back to it. Composing
    a fake absolute URL here would be the server asserting something it was never told.
    """

    def test_mcp_url_is_null_when_the_base_url_is_unset(self, client: TestClient) -> None:
        """The ``client`` fixture builds an app with no ``GW_BASE_URL``."""
        assert _body(client)["mcp_url"] is None

    def test_mcp_url_is_the_base_url_plus_the_mcp_path_when_it_is_set(self, tmp_path: Any) -> None:
        """Built as a second app for the reason ``test_name_is_the_setting_when_it_is_set``
        gives: ``Settings`` is read at construction, so overriding it afterwards would test a
        value the running application never saw."""
        from glosswork.app import create_app

        settings = Settings(
            data_dir=tmp_path,
            embedding_enabled=False,
            base_url="https://northwind.glosswork.app",
        )
        based_app: FastAPI = create_app(settings)
        with TestClient(based_app) as based:
            tokens = mint_scope_tokens(based_app.state.services)
            based.headers["Authorization"] = f"Bearer {tokens['admin']}"
            assert _body(based)["mcp_url"] == "https://northwind.glosswork.app/mcp"

    def test_a_trailing_slash_on_the_base_url_does_not_double_the_separator(
        self, tmp_path: Any
    ) -> None:
        """``GW_BASE_URL=https://host/`` is a setting a person will write, and
        ``https://host//mcp`` is a different path to the SDK's route matcher, which is mounted
        on the exact ``/mcp`` (``app.py``). The same ``rstrip('/')`` the attachment service
        already applies to this setting.
        """
        from glosswork.app import create_app

        settings = Settings(
            data_dir=tmp_path,
            embedding_enabled=False,
            base_url="https://northwind.glosswork.app/",
        )
        based_app: FastAPI = create_app(settings)
        with TestClient(based_app) as based:
            tokens = mint_scope_tokens(based_app.state.services)
            based.headers["Authorization"] = f"Bearer {tokens['admin']}"
            assert _body(based)["mcp_url"] == "https://northwind.glosswork.app/mcp"


class TestTrial:
    """``trial`` (change 30): when a hosted trial ends and where to subscribe.

    ``null`` unless ``GW_TRIAL_ENDS_AT`` is set, which is every self-hosted workspace, and
    otherwise the end time with the subscribe address. The workspace reads no clock for it and
    enforces nothing: the browser counts down, and the freeze is ``GW_READ_ONLY``'s.

    Each configured case builds a second app, for the reason
    ``test_name_is_the_setting_when_it_is_set`` gives.
    """

    def test_trial_is_null_when_no_trial_end_is_set(self, client: TestClient) -> None:
        """The ``client`` fixture builds an app with no ``GW_TRIAL_ENDS_AT``."""
        body = _body(client)
        assert "trial" in body, body
        assert body["trial"] is None

    def test_trial_carries_the_end_time_and_the_subscribe_address(self, tmp_path: Any) -> None:
        """The end time goes out in the one form every timestamp here has, whole seconds and
        a ``Z``, never the operator's input echoed: this one was written with an offset."""
        with _trial_client(
            tmp_path,
            trial_ends_at="2026-10-09T11:00:00-04:00",
            subscribe_url="https://example.com/subscribe",
        ) as trial:
            assert _body(trial)["trial"] == {
                "ends_at": "2026-10-09T15:00:00Z",
                "subscribe_url": "https://example.com/subscribe",
            }

    def test_trial_subscribe_url_is_null_when_no_subscribe_address_is_set(
        self, tmp_path: Any
    ) -> None:
        """A trial end with no subscribe address is legal: the banner counts down with no
        link, and the key is present and ``null`` so the browser reads one shape."""
        with _trial_client(tmp_path, trial_ends_at="2026-10-09T15:00:00Z") as trial:
            assert _body(trial)["trial"] == {
                "ends_at": "2026-10-09T15:00:00Z",
                "subscribe_url": None,
            }

    def test_trial_is_null_and_no_address_is_sent_with_only_a_subscribe_address(
        self, tmp_path: Any
    ) -> None:
        """The subscribe address rides only inside a non-null ``trial``, so a workspace with
        a subscribe address and no trial sends nothing new, anywhere in the document."""
        with _trial_client(tmp_path, subscribe_url="https://example.com/subscribe") as plain:
            response = plain.get(WORKSPACE_PATH)
            assert response.json()["trial"] is None
            assert "example.com/subscribe" not in response.text

    def test_trial_is_readable_at_read_scope(self, tmp_path: Any) -> None:
        """Every credential on a workspace on trial may read the subscribe address, under the
        written rule that the address never holds a secret. Asserted because it is a
        disclosure this change adds on purpose: a ``read`` token is never handed the address
        by a refused write."""
        with _trial_client(
            tmp_path,
            trial_ends_at="2026-10-09T15:00:00Z",
            subscribe_url="https://example.com/subscribe",
        ) as trial:
            tokens: dict[str, str] = trial.scope_tokens  # type: ignore[attr-defined]
            body = _body(trial, auth(tokens["read"]))
            assert body["trial"]["subscribe_url"] == "https://example.com/subscribe"

    def test_trial_end_is_logged_once_at_startup(
        self, tmp_path: Any, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """As ``read_only_mode`` is for the freeze: the end time, and whether a subscribe
        address is set, never the address itself."""
        with _trial_client(
            tmp_path,
            trial_ends_at="2026-10-09T15:00:00Z",
            subscribe_url="https://example.com/subscribe",
        ):
            pass
        lines = [line for line in _log_lines(capfd) if line.get("event") == "trial_end_set"]
        assert len(lines) == 1, lines
        assert lines[0]["level"] == "info"
        assert lines[0]["setting"] == "GW_TRIAL_ENDS_AT"
        assert lines[0]["trial_ends_at"] == "2026-10-09T15:00:00Z"
        assert lines[0]["subscribe_url_set"] is True
        assert "example.com" not in json.dumps(lines)

    def test_trial_end_is_not_logged_when_it_is_unset(
        self, tmp_path: Any, capfd: pytest.CaptureFixture[str]
    ) -> None:
        with _trial_client(tmp_path):
            pass
        assert not [line for line in _log_lines(capfd) if line.get("event") == "trial_end_set"]


def _log_lines(capfd: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    """The application's structured log lines, one JSON object each (FR-P5)."""
    return [
        json.loads(line) for line in capfd.readouterr().out.splitlines() if line.startswith("{")
    ]


@contextmanager
def _trial_client(tmp_path: Any, **overrides: Any) -> Iterator[TestClient]:
    """A second app with the given settings, signed in as an administrator."""
    from glosswork.app import create_app

    configured: FastAPI = create_app(
        Settings(data_dir=tmp_path, embedding_enabled=False, **overrides)
    )
    with TestClient(configured) as test_client:
        tokens = mint_scope_tokens(configured.state.services)
        test_client.scope_tokens = tokens  # type: ignore[attr-defined]
        test_client.headers["Authorization"] = f"Bearer {tokens['admin']}"
        yield test_client


class TestCounts:
    def test_people_counts_active_users_seeded_in_this_run(
        self, client: TestClient, app_db: Database
    ) -> None:
        """The expected value comes from the run, never from a constant.

        ``docs/changes/README.md``: "a count in an assertion comes from the run, not from a
        constant, so the criterion still holds when the fixture changes". The baseline is read
        first, two users are seeded, and the delta is asserted -- so this passes whatever the
        bootstrap fixture happens to contain today.
        """
        before = _body(client)["people"]

        seed_second_principal(
            app_db,
            principal_id="11111111-1111-4111-8111-111111111111",
            display_name="Ada Lovelace",
            email="ada@example.com",
        )
        seed_second_principal(
            app_db,
            principal_id="22222222-2222-4222-8222-222222222222",
            display_name="Grace Hopper",
            email="grace@example.com",
        )

        assert _body(client)["people"] == before + 2

    def test_an_inactive_user_is_not_counted(self, client: TestClient, app_db: Database) -> None:
        """``is_active`` is the difference between a person who is here and one who was.

        Measured separately from the count above because a naive ``COUNT(*) FROM principals``
        passes that one and fails this one, which is exactly the defect worth catching.
        """
        before = _body(client)["people"]

        seed_second_principal(
            app_db,
            principal_id="33333333-3333-4333-8333-333333333333",
            display_name="Departed Person",
            email="gone@example.com",
            is_active=False,
        )

        assert _body(client)["people"] == before

    def test_agents_counts_registered_labels_across_principals(
        self, client: TestClient, app_services: ServiceBundle, app_db: Database
    ) -> None:
        """Across principals, which is the half a caller cannot get any other way.

        ``GET /api/v1/agent-labels`` returns the caller's own labels only, and
        ``GET /api/v1/admin/agent-labels`` is refused below the ``admin`` scope. Two labels are
        registered against two different principals, so a count scoped to the caller reports 1
        and fails here.
        """
        before = _body(client)["agents"]

        other = seed_second_principal(
            app_db,
            principal_id="44444444-4444-4444-8444-444444444444",
            display_name="Label Owner",
            email="owner@example.com",
        )
        app_services.agent_labels.register_use(BOOTSTRAP, "sales-agent")
        app_services.agent_labels.register_use(other, "claude-code")

        assert _body(client)["agents"] == before + 2

    def test_a_second_use_of_one_label_does_not_count_twice(
        self, client: TestClient, app_services: ServiceBundle
    ) -> None:
        """A label is an agent, not a call. ``register_use`` increments ``call_count`` on the
        existing row, and a count that summed ``call_count`` would report 2 here."""
        app_services.agent_labels.register_use(BOOTSTRAP, "sales-agent")
        before = _body(client)["agents"]

        app_services.agent_labels.register_use(BOOTSTRAP, "sales-agent")

        assert _body(client)["agents"] == before

    def test_a_service_account_with_no_label_is_in_neither_count(
        self, client: TestClient, app_db: Database
    ) -> None:
        """A stated limitation, pinned so it is a decision rather than a surprise.

        A service account is not a person (``docs/DESIGN.md`` 6.5 says it is an agent), and it
        reaches the agent count only through a label it presents. One that has never presented a
        label is therefore in neither number. DD-17 made a label present on writes from every
        surface, so this is the exception; it is asserted here because a future reader who finds
        it will otherwise file it as a bug.
        """
        before = _body(client)

        # Seeded through the repository rather than a service call, for the same reason
        # ``seed_second_principal`` exists: ``create_*`` mints its own id, and this test needs to
        # name the row it is asserting about.
        with app_db.write() as conn:
            SqlitePrincipalRepository().insert(
                conn,
                PrincipalRow(
                    id="55555555-5555-4555-8555-555555555555",
                    type="service_account",
                    display_name="silent-runner",
                    email=None,
                    role="member",
                    auth_provider="local",
                    external_id=None,
                    password_hash=None,
                    is_active=True,
                    description="A service account that has never presented an agent label.",
                    created_at="2026-01-01T00:00:00Z",
                    created_by=None,
                ),
            )

        after = _body(client)
        assert after["people"] == before["people"], "a service account is not a person"
        assert after["agents"] == before["agents"], "an unlabelled service account is not an agent"


class TestAccess:
    """**Both assertions here check the document, never the status code alone.**

    Measured on a tree without the route and it mattered: with ``web/dist`` present,
    ``app.py``'s static-file fallback answers *any* unmatched path -- ``/api/v1/workspace``
    included -- with ``200 text/html`` and the SPA's ``index.html``. Two assertions of
    ``status_code == 200`` and ``== 401`` **both passed against a tree with no such route**
    (AGENTS.md, Traps). Asserting the JSON body is what makes them capable of failing.
    """

    def test_a_read_scope_credential_is_accepted(
        self, client: TestClient, api_tokens: dict[str, str]
    ) -> None:
        """The whole reason this is a new route.

        Measured on a tree without it: ``GET /api/v1/admin/agent-labels`` returns
        ``403 insufficient_scope`` under a ``write`` credential, so no existing route could serve
        this line to a member. A ``read`` PAT is the weakest credential there is, and it reaches
        the real document here -- asserted by its keys, not by its status.
        """
        response = client.get(WORKSPACE_PATH, headers=auth(api_tokens["read"]))
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/json"), response.text
        assert set(response.json()) == {
            "name",
            "people",
            "agents",
            "mcp_url",
            "trial",
        }, response.text

    def test_an_anonymous_request_is_refused(self, anon_client: TestClient) -> None:
        """The request edge fails closed (DD-15).

        A **fence**: every route inherits this and the workspace route does not alter it, so it is
        labelled one and not counted as coverage. It still asserts the error envelope rather than
        the bare status, because ``401`` here has to mean "the credential gate refused a real route"
        and not "the SPA fallback happened to be reached before it".
        """
        response = anon_client.get(WORKSPACE_PATH)
        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "invalid_token", response.text
