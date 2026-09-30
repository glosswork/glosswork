"""Invites (change 9, FR-I19): an administrator invites a person by email and role, and
that person's first code sign-in creates them.

An invite is its own list (DQ2): no person exists until the invited address proves
itself with a code. Every test drives the routes over HTTP against a workspace whose
relay is a live fake (``tests/fake_relay.py``).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import auth, make_actor
from tests.fake_relay import LiveRelay, Scripted
from tests.relay_support import (
    ADMIN_EMAIL,
    ADMIN_NAME,
    VERIFY_FAILURE_MESSAGE,
    admin_token,
    code_app,  # noqa: F401 - fixture
    code_client,  # noqa: F401 - fixture
    codes_sent_to,
    execute,
    live_relay,  # noqa: F401 - fixture
    relay_app,
    request_code,
    seed_local_user,
    services_of,
    verify_code,
)

INVITEE = "lin@example.com"


@pytest.fixture
def admin_headers(code_app: FastAPI, code_client: TestClient) -> dict[str, str]:  # noqa: F811
    return auth(admin_token(code_app))


def _invite(
    client: TestClient,
    headers: dict[str, str],
    email: str = INVITEE,
    role: str = "creator",
    display_name: str = "Lin Chen",
) -> Any:
    return client.post(
        "/api/v1/invites",
        json={"email": email, "display_name": display_name, "role": role},
        headers=headers,
    )


def _created(response: Any) -> dict[str, Any]:
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _live_invites(client: TestClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    listed = client.get("/api/v1/invites", headers=headers)
    assert listed.status_code == 200, listed.text
    invites: list[dict[str, Any]] = listed.json()["invites"]
    return invites


def _code_for(relay: LiveRelay, client: TestClient, email: str) -> str:
    before = len(codes_sent_to(relay.relay, email))
    answered = request_code(client, email)
    assert answered.status_code == 202, answered.text
    codes = codes_sent_to(relay.relay, email)
    assert len(codes) == before + 1, relay.relay.messages()
    return codes[-1]


def _assert_verify_failure(response: Any) -> None:
    assert response.status_code == 401, response.text
    error = response.json()["error"]
    assert error["code"] == "invalid_credentials", response.text
    assert error["message"] == VERIFY_FAILURE_MESSAGE


def _refused(response: Any, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code, response.text


# ---------------------------------------------------------------------- inviting


def test_an_administrator_invites_by_email_and_role_and_the_relay_receives_it(
    code_client: TestClient,  # noqa: F811
    admin_headers: dict[str, str],
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    body = _created(_invite(code_client, admin_headers, email="  Lin@Example.com "))
    invite = body["invite"]
    assert invite["email"] == INVITEE
    assert invite["role"] == "creator"
    assert invite["display_name"] == "Lin Chen"
    assert invite["accepted_at"] is None and invite["revoked_at"] is None
    assert body["email"] == {"outcome": "accepted", "message": "Invite sent."}

    (message,) = live_relay.relay.messages("invite")
    assert message["to"] == INVITEE
    assert message["fields"] == {"inviter_name": ADMIN_NAME}
    assert [i["id"] for i in _live_invites(code_client, admin_headers)] == [invite["id"]]


def test_the_invited_address_first_code_sign_in_creates_the_person(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
    admin_headers: dict[str, str],
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    services = services_of(code_app)
    assert services.principals.find_by_email(INVITEE) is None
    invite = _created(_invite(code_client, admin_headers))["invite"]
    assert services.principals.find_by_email(INVITEE) is None

    person = TestClient(code_app)
    code = _code_for(live_relay, person, INVITEE)
    signed_in = verify_code(person, INVITEE, code)
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["email"] == INVITEE
    assert signed_in.json()["role"] == "creator"

    created = services.principals.find_by_email(INVITEE)
    assert created is not None
    assert created.display_name == "Lin Chen"
    assert created.role == "creator"
    assert created.auth_provider == "local"
    assert created.password_hash is None
    inviter = services.principals.find_by_email(ADMIN_EMAIL)
    assert inviter is not None and created.created_by == inviter.id
    assert _live_invites(code_client, admin_headers) == []
    assert invite["id"]

    # The person's next sign-in is an ordinary one.
    again = _code_for(live_relay, person, INVITEE)
    assert verify_code(person, INVITEE, again).status_code == 200


def test_a_revoked_invite_lets_no_one_in(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
    admin_headers: dict[str, str],
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    invite = _created(_invite(code_client, admin_headers))["invite"]
    code = _code_for(live_relay, code_client, INVITEE)
    revoked = code_client.delete(f"/api/v1/invites/{invite['id']}", headers=admin_headers)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked_at"] is not None
    _assert_verify_failure(verify_code(TestClient(code_app), INVITEE, code))
    assert services_of(code_app).principals.find_by_email(INVITEE) is None

    again = code_client.delete(f"/api/v1/invites/{invite['id']}", headers=admin_headers)
    _refused(again, 409, "conflict")


def test_inviting_an_address_with_an_account_or_a_live_invite_is_refused(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
    admin_headers: dict[str, str],
) -> None:
    seed_local_user(code_app, "taken@example.com")
    _refused(_invite(code_client, admin_headers, email="taken@example.com"), 409, "conflict")
    _created(_invite(code_client, admin_headers))
    _refused(_invite(code_client, admin_headers), 409, "conflict")


@pytest.mark.parametrize(
    "address",
    [
        "no-at-sign.example.com",
        "two@@example.com",
        "a@b@example.com",
        "@example.com",
        "lin@",
        "lin chen@example.com",
        "lin@example.com, eve@example.com",
        "lin;eve@example.com",
        '"lin"@example.com',
        "<lin@example.com>",
        "x" * 250 + "@example.com",
    ],
)
def test_a_malformed_address_is_refused(
    code_client: TestClient,  # noqa: F811
    admin_headers: dict[str, str],
    live_relay: LiveRelay,  # noqa: F811
    address: str,
) -> None:
    _refused(_invite(code_client, admin_headers, email=address), 422, "validation_failed")
    assert live_relay.relay.messages() == []


def test_a_non_administrator_is_refused(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
    admin_headers: dict[str, str],
) -> None:
    services = services_of(code_app)
    creator = seed_local_user(code_app, "cora@example.com", role="creator", name="Cora")
    creator_token = services.tokens.mint(
        make_actor(), name="creator", scope="admin", principal_id=creator
    ).plaintext
    member = seed_local_user(code_app, "mo@example.com", role="member", name="Mo")
    member_token = services.tokens.mint(
        make_actor(), name="member", scope="write", principal_id=member
    ).plaintext

    _refused(_invite(code_client, auth(creator_token)), 403, "forbidden")
    _refused(_invite(code_client, auth(member_token)), 403, "insufficient_scope")
    _refused(code_client.get("/api/v1/invites", headers=auth(creator_token)), 403, "forbidden")
    # The positive control: the administrator is not refused.
    _created(_invite(code_client, admin_headers))


# -------------------------------------------------------- invites that are not live


def test_inviting_a_removed_person_reactivates_the_same_principal(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
    admin_headers: dict[str, str],
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    services = services_of(code_app)
    person = seed_local_user(code_app, INVITEE, role="member", name="Lin")
    services.principals.deactivate_principal(make_actor(), person)

    _created(_invite(code_client, admin_headers, role="creator"))
    code = _code_for(live_relay, code_client, INVITEE)
    signed_in = verify_code(TestClient(code_app), INVITEE, code)
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["id"] == person
    back = services.principals.get_principal(person)
    assert back.is_active is True
    assert back.role == "creator"


@pytest.mark.parametrize("change", ["removed", "demoted"])
def test_an_invite_from_an_administrator_since_removed_or_demoted_lets_no_one_in(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
    admin_headers: dict[str, str],
    live_relay: LiveRelay,  # noqa: F811
    change: str,
) -> None:
    services = services_of(code_app)
    second_admin_headers = auth(admin_token(code_app, "linus@example.com", "Linus"))
    _created(_invite(code_client, second_admin_headers))
    code = _code_for(live_relay, code_client, INVITEE)
    linus = services.principals.find_by_email("linus@example.com")
    assert linus is not None
    if change == "removed":
        services.principals.deactivate_principal(make_actor(), linus.id)
    else:
        services.principals.update_principal(make_actor(), linus.id, role="member")
    _assert_verify_failure(verify_code(TestClient(code_app), INVITEE, code))
    assert services.principals.find_by_email(INVITEE) is None
    assert admin_headers  # the first administrator stays, so the second can be removed


def test_an_invite_older_than_fourteen_days_lets_no_one_in(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
    admin_headers: dict[str, str],
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    _created(_invite(code_client, admin_headers))
    code = _code_for(live_relay, code_client, INVITEE)
    execute(
        code_app,
        "UPDATE invites SET created_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-15 days') "
        "WHERE email = :e",
        {"e": INVITEE},
    )
    assert _live_invites(code_client, admin_headers) == []
    _assert_verify_failure(verify_code(TestClient(code_app), INVITEE, code))
    # An expired invite does not block a new one for the same address.
    _created(_invite(code_client, admin_headers))


def test_an_invite_can_be_revoked_while_the_workspace_is_read_only(
    tmp_path: Path,
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    data_dir = tmp_path / "data"
    writable = relay_app(data_dir, live_relay)
    with TestClient(writable) as client:
        headers = auth(admin_token(writable))
        invite = _created(_invite(client, headers))["invite"]
    frozen = relay_app(data_dir, live_relay, read_only=True)
    with TestClient(frozen) as client:
        refused = _invite(client, headers, email="second@example.com")
        _refused(refused, 409, "workspace_read_only")
        revoked = client.delete(f"/api/v1/invites/{invite['id']}", headers=headers)
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["revoked_at"] is not None


# ------------------------------------------------------------ the email's outcome


@pytest.mark.parametrize(
    ("answer", "outcome", "phrase"),
    [
        (Scripted(status=401), "refused_credential", "could not be sent"),
        (Scripted(status=403), "refused_credential", "could not be sent"),
        (
            Scripted(status=422, refused=["inviter_name"]),
            "refused_fields",
            "display name may be the reason",
        ),
        (Scripted(status=429), "rate_limited", "busy"),
        (Scripted(status=500), "unavailable", "could not be sent"),
        (Scripted(status=422, body={"detail": "not our shape"}), "refused_fields", "not be sent"),
    ],
)
def test_an_invite_the_relay_refuses_is_kept_and_reports_the_outcome(
    code_client: TestClient,  # noqa: F811
    admin_headers: dict[str, str],
    live_relay: LiveRelay,  # noqa: F811
    answer: Scripted,
    outcome: str,
    phrase: str,
) -> None:
    live_relay.relay.script(answer)
    body = _created(_invite(code_client, admin_headers))
    assert body["email"]["outcome"] == outcome, body
    assert phrase in body["email"]["message"], body
    assert "saved" in body["email"]["message"], body
    if outcome != "refused_fields" or answer.body is not None:
        assert "display name" not in body["email"]["message"], body
    assert [i["email"] for i in _live_invites(code_client, admin_headers)] == [INVITEE]


@pytest.fixture
def plain_client(tmp_path: Path) -> Iterator[TestClient]:
    from glosswork.app import create_app
    from glosswork.config import Settings

    app = create_app(Settings(data_dir=tmp_path / "plain", embedding_enabled=False))
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {admin_token(app)}"
        yield client


def test_all_three_invite_routes_answer_feature_disabled_with_codes_off(
    plain_client: TestClient,
) -> None:
    for response in (
        plain_client.get("/api/v1/invites"),
        _invite(plain_client, {}),
        plain_client.delete("/api/v1/invites/00000000-0000-4000-8000-000000000001"),
    ):
        _refused(response, 409, "feature_disabled")
        assert response.json()["error"]["details"]["setting"] == "GW_RELAY_URL"
