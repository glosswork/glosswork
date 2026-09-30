"""Sign-in by a six-digit email code (change 9, FR-I18).

Every test here configures the workspace as a hosted one is, with ``GW_RELAY_URL``
pointing at a live fake relay (``tests/fake_relay.py``) on a loopback port, and reads the
code the person would read: the one the relay received.

**Every "same answer" or "nothing happened" test pins the expected answer and carries a
positive control in the same test** (plan F3, P16). On a tree without the feature every
one of these routes answers the same ``401``, so an assertion that only compared two
answers with each other, or only checked that nothing was sent, would pass there.
"""

from __future__ import annotations

import re
import socket
import threading
import time
from pathlib import Path
from typing import Any

import httpx2
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from glosswork import admin
from glosswork.app import create_app
from tests.conftest import make_actor
from tests.fake_relay import LiveRelay, Scripted
from tests.relay_support import (
    KNOWN_EMAIL,
    REQUEST_MESSAGE,
    VERIFY_FAILURE_MESSAGE,
    code_app,  # noqa: F401 - fixture
    code_client,  # noqa: F401 - fixture
    code_hash,
    codes_sent_to,
    execute,
    live_relay,  # noqa: F401 - fixture
    relay_app,
    relay_settings,
    request_code,
    rows,
    seed_local_user,
    services_of,
    source,
    verify_code,
    wrong_code,
)


def _assert_request_answer(response: Any) -> None:
    assert response.status_code == 202, response.text
    assert response.json() == {"message": REQUEST_MESSAGE}


def _assert_verify_failure(response: Any) -> None:
    assert response.status_code == 401, response.text
    error = response.json()["error"]
    assert error["code"] == "invalid_credentials", response.text
    assert error["message"] == VERIFY_FAILURE_MESSAGE, response.text


def _signed_in_as(response: Any, email: str) -> None:
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["email"] == email, body
    assert body["auth_method"] == "session", body
    assert "gw_session" in response.headers.get("set-cookie", ""), response.headers


def _one_code(relay_live: LiveRelay, client: TestClient, email: str) -> str:
    before = len(codes_sent_to(relay_live.relay, email))
    _assert_request_answer(request_code(client, email))
    codes = codes_sent_to(relay_live.relay, email)
    assert len(codes) == before + 1, relay_live.relay.messages()
    return codes[-1]


# ------------------------------------------------------------------------ sign in


def test_a_known_address_receives_a_six_digit_code_and_signs_in_with_it(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    seed_local_user(code_app)
    code = _one_code(live_relay, code_client, KNOWN_EMAIL)
    assert re.fullmatch(r"[0-9]{6}", code), code

    signed_in = verify_code(code_client, KNOWN_EMAIL, code)
    _signed_in_as(signed_in, KNOWN_EMAIL)
    me = code_client.get("/api/v1/me")
    assert me.status_code == 200, me.text
    assert me.json()["email"] == KNOWN_EMAIL


def test_modes_reports_codes_on_and_password_sign_in_off(
    code_client: TestClient,  # noqa: F811
) -> None:
    modes = code_client.get("/api/v1/auth/modes")
    assert modes.status_code == 200, modes.text
    assert modes.json() == {"standalone": False, "oidc": False, "email_code": True}


def test_with_codes_on_password_sign_in_is_refused(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
) -> None:
    """DQ1 (a). The positive control is the bootstrap claim's own premise: this person has
    a password, and it is still refused, as ``feature_disabled`` and not as a wrong
    password."""
    services_of(code_app).principals.create_user(
        make_actor(),
        email="pat@example.com",
        display_name="Pat",
        password="correct-horse-battery-staple",
    )
    refused = code_client.post(
        "/api/v1/auth/login",
        json={"email": "pat@example.com", "password": "correct-horse-battery-staple"},
    )
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "feature_disabled", refused.text
    assert error["details"]["feature"] == "password_sign_in", refused.text


def test_the_request_answer_is_the_same_whether_or_not_the_address_can_sign_in(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    services = services_of(code_app)
    seed_local_user(code_app)
    gone = seed_local_user(code_app, "gone@example.com", name="Gone")
    services.principals.deactivate_principal(make_actor(), gone)
    services.principals.create_user(
        make_actor(),
        email="okta@example.com",
        display_name="Okta Person",
        auth_provider="oidc",
        external_id="00u-okta-person",
    )

    answers = {
        email: request_code(code_client, email)
        for email in (KNOWN_EMAIL, "nobody@example.com", "gone@example.com", "okta@example.com")
    }
    for response in answers.values():
        _assert_request_answer(response)
    bodies = {email: (r.status_code, r.content) for email, r in answers.items()}
    assert len(set(bodies.values())) == 1, bodies

    sent = live_relay.relay.messages()
    assert [(m["template"], m["to"]) for m in sent] == [("sign_in_code", KNOWN_EMAIL)], sent


def test_verify_failures_are_one_answer_beside_a_success(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    seed_local_user(code_app)
    code = _one_code(live_relay, code_client, KNOWN_EMAIL)

    _assert_verify_failure(verify_code(code_client, "nobody@example.com", code))
    _assert_verify_failure(verify_code(code_client, KNOWN_EMAIL, wrong_code(code)))
    _assert_verify_failure(verify_code(code_client, KNOWN_EMAIL, "12345"))
    _assert_verify_failure(verify_code(code_client, KNOWN_EMAIL, "abcdef"))

    expired = _one_code(live_relay, code_client, KNOWN_EMAIL)
    execute(
        code_app,
        "UPDATE sign_in_codes SET expires_at = '2000-01-01T00:00:00Z' "
        "WHERE email = :e AND consumed_at IS NULL",
        {"e": KNOWN_EMAIL},
    )
    _assert_verify_failure(verify_code(code_client, KNOWN_EMAIL, expired))

    fresh = _one_code(live_relay, code_client, KNOWN_EMAIL)
    _signed_in_as(verify_code(code_client, KNOWN_EMAIL, fresh), KNOWN_EMAIL)


def test_a_code_is_single_use(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    seed_local_user(code_app)
    code = _one_code(live_relay, code_client, KNOWN_EMAIL)
    _signed_in_as(verify_code(code_client, KNOWN_EMAIL, code), KNOWN_EMAIL)
    _assert_verify_failure(verify_code(code_client, KNOWN_EMAIL, code))


def test_a_code_expires_ten_minutes_after_it_was_made(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    seed_local_user(code_app)
    code = _one_code(live_relay, code_client, KNOWN_EMAIL)
    (row,) = rows(code_app, "SELECT * FROM sign_in_codes WHERE email = :e", {"e": KNOWN_EMAIL})
    from glosswork.timeutil import parse_datetime

    lifetime = parse_datetime(row["expires_at"]) - parse_datetime(row["created_at"])
    assert lifetime.total_seconds() == 600, row
    (message,) = live_relay.relay.messages("sign_in_code")
    assert message["fields"]["code_expires_at"] == row["expires_at"], (message, row)

    execute(
        code_app,
        "UPDATE sign_in_codes SET created_at = '2000-01-01T00:00:00Z', "
        "expires_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-1 seconds') WHERE id = :id",
        {"id": row["id"]},
    )
    _assert_verify_failure(verify_code(code_client, KNOWN_EMAIL, code))


def test_five_wrong_attempts_kill_the_live_codes_and_the_right_code_then_fails(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    seed_local_user(code_app)
    first = _one_code(live_relay, code_client, KNOWN_EMAIL)
    second = _one_code(live_relay, code_client, KNOWN_EMAIL)
    wrong = next(
        f"{n:06d}" for n in range(1_000_000) if f"{n:06d}" not in (first, second)
    )
    for _ in range(5):
        _assert_verify_failure(verify_code(code_client, KNOWN_EMAIL, wrong))
    _assert_verify_failure(verify_code(code_client, KNOWN_EMAIL, first))
    _assert_verify_failure(verify_code(code_client, KNOWN_EMAIL, second))

    # The positive control: four wrong guesses are not enough to kill a code.
    third = _one_code(live_relay, code_client, KNOWN_EMAIL)
    fourth_wrong = next(f"{n:06d}" for n in range(1_000_000) if f"{n:06d}" != third)
    for _ in range(4):
        _assert_verify_failure(verify_code(code_client, KNOWN_EMAIL, fourth_wrong))
    _signed_in_as(verify_code(code_client, KNOWN_EMAIL, third), KNOWN_EMAIL)


def test_a_second_request_does_not_kill_the_first_code(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    """F2: someone else asking for a code for this address must not kill the code its
    owner is typing. Either code signs in."""
    seed_local_user(code_app)
    first = _one_code(live_relay, code_client, KNOWN_EMAIL)
    second = _one_code(live_relay, code_client, KNOWN_EMAIL)
    _signed_in_as(verify_code(code_client, KNOWN_EMAIL, first), KNOWN_EMAIL)
    _signed_in_as(verify_code(code_client, KNOWN_EMAIL, second), KNOWN_EMAIL)


# ------------------------------------------------------------------------- limits


def test_the_hourly_cap_holds_across_source_addresses(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    seed_local_user(code_app)
    for n in range(6):
        client = TestClient(code_app, client=source(f"198.51.100.{n + 1}"))
        _assert_request_answer(request_code(client, KNOWN_EMAIL))
    assert len(codes_sent_to(live_relay.relay, KNOWN_EMAIL)) == 5, live_relay.relay.messages()


def test_the_daily_cap_holds_across_source_addresses(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    seed_local_user(code_app)
    ip = iter(range(1, 200))
    for _round in range(4):
        for _ in range(5):
            client = TestClient(code_app, client=source(f"203.0.113.{next(ip)}"))
            _assert_request_answer(request_code(client, KNOWN_EMAIL))
        execute(
            code_app,
            "UPDATE sign_in_codes SET created_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now', "
            "'-2 hours') WHERE email = :e",
            {"e": KNOWN_EMAIL},
        )
    assert len(codes_sent_to(live_relay.relay, KNOWN_EMAIL)) == 20
    client = TestClient(code_app, client=source(f"203.0.113.{next(ip)}"))
    _assert_request_answer(request_code(client, KNOWN_EMAIL))
    assert len(codes_sent_to(live_relay.relay, KNOWN_EMAIL)) == 20, live_relay.relay.messages()


def test_the_per_address_cap_survives_a_restart(
    tmp_path: Path,
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    data_dir = tmp_path / "data"
    first = relay_app(data_dir, live_relay)
    with TestClient(first) as client:
        seed_local_user(first)
        for _ in range(5):
            _assert_request_answer(request_code(client, KNOWN_EMAIL))
    assert len(codes_sent_to(live_relay.relay, KNOWN_EMAIL)) == 5

    restarted = relay_app(data_dir, live_relay)
    with TestClient(restarted) as client:
        _assert_request_answer(request_code(client, KNOWN_EMAIL))
    assert len(codes_sent_to(live_relay.relay, KNOWN_EMAIL)) == 5, live_relay.relay.messages()


def test_requests_and_verifications_are_limited_per_source_address(
    tmp_path: Path,
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    app = relay_app(tmp_path / "data", live_relay, login_ip_max_attempts=3)
    with TestClient(app, client=source("192.0.2.10")) as client:
        for n in range(3):
            _assert_request_answer(request_code(client, f"p{n}@example.com"))
        refused = request_code(client, "p9@example.com")
        assert refused.status_code == 429, refused.text
        assert refused.json()["error"]["code"] == "rate_limited"
        assert refused.json()["error"]["details"]["window"] == "source"
        # A second source, with the app's lifespan still running: a second ``with``
        # would start it again, which the MCP session manager refuses.
        other = TestClient(app, client=source("192.0.2.11"))
        for n in range(3):
            _assert_verify_failure(verify_code(other, f"p{n}@example.com", "000000"))
        refused = verify_code(other, "p0@example.com", "000000")
        assert refused.status_code == 429, refused.text
        assert refused.json()["error"]["code"] == "rate_limited"


def test_clear_sign_in_codes_resets_an_address_count(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
    live_relay: LiveRelay,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_local_user(code_app)
    for _ in range(5):
        _assert_request_answer(request_code(code_client, KNOWN_EMAIL))
    _assert_request_answer(request_code(code_client, KNOWN_EMAIL))
    assert len(codes_sent_to(live_relay.relay, KNOWN_EMAIL)) == 5

    monkeypatch.setenv("GW_DATA_DIR", str(code_app.state.settings.data_dir))
    monkeypatch.setenv("GW_EMBEDDING_ENABLED", "false")
    capsys.readouterr()
    assert admin.main(["clear-sign-in-codes", "--email", KNOWN_EMAIL.upper()]) == 0
    out = capsys.readouterr().out
    assert "5" in out, out

    code = _one_code(live_relay, code_client, KNOWN_EMAIL)
    _signed_in_as(verify_code(code_client, KNOWN_EMAIL, code), KNOWN_EMAIL)


# ----------------------------------------------------------------- state changes


def test_sign_in_by_code_works_while_the_workspace_is_read_only(
    tmp_path: Path,
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    app = relay_app(tmp_path / "data", live_relay, read_only=True)
    with TestClient(app) as client:
        seed_local_user(app)
        code = _one_code(live_relay, client, KNOWN_EMAIL)
        _signed_in_as(verify_code(client, KNOWN_EMAIL, code), KNOWN_EMAIL)


def test_a_person_removed_after_a_code_was_sent_cannot_use_it(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    person = seed_local_user(code_app)
    code = _one_code(live_relay, code_client, KNOWN_EMAIL)
    services_of(code_app).principals.deactivate_principal(make_actor(), person)
    _assert_verify_failure(verify_code(code_client, KNOWN_EMAIL, code))


# --------------------------------------------------------------- the relay call


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_the_request_answers_before_the_relay_does(
    tmp_path: Path,
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    """Under uvicorn, not ``TestClient``, which runs background tasks before it returns
    (plan P9). The relay takes two seconds; the answer must not."""
    live_relay.relay.answer_always(Scripted(delay=2.0))
    app = create_app(relay_settings(tmp_path / "data", live_relay))
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        seed_local_user(app)
        started = time.monotonic()
        response = httpx2.post(
            f"http://127.0.0.1:{port}/api/v1/auth/code/request",
            json={"email": KNOWN_EMAIL},
            timeout=10,
        )
        elapsed = time.monotonic() - started
        _assert_request_answer(response)
        assert elapsed < 1.5, elapsed
        sent = live_relay.relay.wait_for(1, timeout=8)
        assert [m["to"] for m in sent] == [KNOWN_EMAIL], sent
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_no_log_line_carries_the_code_its_hash_or_the_relay_token(
    tmp_path: Path,
    live_relay: LiveRelay,  # noqa: F811
    capfd: pytest.CaptureFixture[str],
) -> None:
    """``capfd`` rather than ``caplog``: structlog renders straight to stdout, so nothing
    reaches the handler ``caplog`` installs (``tests/test_own_password.py``)."""
    app = relay_app(tmp_path / "data", live_relay, log_level="debug")
    with TestClient(app) as client:
        seed_local_user(app)
        code = _one_code(live_relay, client, KNOWN_EMAIL)
        (row,) = rows(app, "SELECT id FROM sign_in_codes WHERE email = :e", {"e": KNOWN_EMAIL})
        _signed_in_as(verify_code(client, KNOWN_EMAIL, code), KNOWN_EMAIL)
    out, err = capfd.readouterr()
    logged = out + err
    assert "relay_send" in logged, logged[-2000:]
    assert code not in logged
    assert code_hash(row["id"], code) not in logged
    assert live_relay.relay.token not in logged


def test_a_failed_code_insert_leaks_no_code_hash_or_token(
    tmp_path: Path,
    live_relay: LiveRelay,  # noqa: F811
    capfd: pytest.CaptureFixture[str],
) -> None:
    """F4. A trigger makes the background insert fail with a database error, whose text
    carries its bound parameters. The task logs the class and the request id only."""
    app = relay_app(tmp_path / "data", live_relay, log_level="debug")
    with TestClient(app) as client:
        modes = client.get("/api/v1/auth/modes").json()
        assert modes.get("email_code") is True, modes
        seed_local_user(app)
        execute(
            app,
            "CREATE TRIGGER refuse_codes BEFORE INSERT ON sign_in_codes "
            "BEGIN SELECT RAISE(ABORT, 'forced by the test'); END",
        )
        _assert_request_answer(request_code(client, KNOWN_EMAIL))
    out, err = capfd.readouterr()
    logged = out + err
    assert "sign_in_code_request_failed" in logged, logged[-2000:]
    assert "IntegrityError" in logged, logged[-2000:]
    assert "parameters" not in logged
    assert "INSERT INTO sign_in_codes" not in logged
    assert not re.search(r"\b[0-9a-f]{64}\b", logged), logged
    assert live_relay.relay.token not in logged
    assert live_relay.relay.messages() == []
