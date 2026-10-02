"""Sign-in by a six-digit email code (change 9, FR-I18).

Every test here configures the workspace as a hosted one is, with ``GW_RELAY_URL``
pointing at a live fake relay (``tests/fake_relay.py``) on a loopback port, and reads the
code the person would read: the one the relay received.

**Every "same answer" or "nothing happened" test pins the expected answer and carries a
positive control in the same test** (plan F3, P16). On a tree without the feature every
one of these routes answers the same ``401``, so an assertion that only compared two
answers with each other, or only checked that nothing was sent, would pass there.

**The stored form of a code (change 20).** The last section pins that a code is stored as
a keyed digest whose key is derived from the relay token, so a copy of the database alone
yields no usable code. Each test there restates the derivation rather than importing it:
a test that called the product's own function would agree with whatever that function
did. Expected passed counts, per selection (``uv run pytest -q tests/test_sign_in_codes.py
-k <selection>``):

==================================  ======
``-k``                              passed
==================================  ======
``stored_form_is_keyed``            2
``artifact_holds_no_usable_code``   2
``pre_upgrade_code_is_refused``     1
``relay_token_rotation``            1
``code_service_needs_a_key``        2
==================================  ======

The no-relay case of ``code_service_needs_a_key`` is a **fence**: true before change 20.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import re
import secrets
import socket
import sqlite3
import tarfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from glosswork import admin
from glosswork.app import create_app
from tests.conftest import auth, make_actor
from tests.fake_relay import FakeRelay, LiveRelay, Scripted, new_token, serve
from tests.relay_support import (
    ADMIN_EMAIL,
    KNOWN_EMAIL,
    REQUEST_MESSAGE,
    VERIFY_FAILURE_MESSAGE,
    admin_token,
    code_app,  # noqa: F401 - fixture
    code_client,  # noqa: F401 - fixture
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
    wrong = next(f"{n:06d}" for n in range(1_000_000) if f"{n:06d}" not in (first, second))
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
    assert "Deleted 5 sign-in code rows for ada@example.com" in out, out

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
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None))
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
        (row,) = rows(
            app, "SELECT code_hash FROM sign_in_codes WHERE email = :e", {"e": KNOWN_EMAIL}
        )
        _signed_in_as(verify_code(client, KNOWN_EMAIL, code), KNOWN_EMAIL)
    out, err = capfd.readouterr()
    logged = out + err
    assert "relay_send" in logged, logged[-2000:]
    assert code not in logged
    # The stored form, read from the row. Recomputing it here would check for a value the
    # product may no longer produce, and such an assertion can never fail.
    assert re.fullmatch(r"[0-9a-f]{64}", row["code_hash"]), row
    assert row["code_hash"] not in logged
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


# ------------------------------------------------- the stored form of a code (change 20)

#: The two labels of the stored form, restated from the design rather than imported.
CODE_LABEL = b"glosswork.sign-in-code.v1:"
KEY_LABEL = b"glosswork.sign-in-code-key.v1:"

#: An operator credential beside the relay, as a hosted workspace has. A fixture string,
#: for the reason ``tests/test_operator_usage.py`` gives.
OPERATOR_TOKEN = "operator-token-for-tests-0123456"

#: The relay token at the two lengths that matter: what the hosted control plane makes,
#: and what ``tests/fake_relay.new_token`` makes. HMAC replaces a key longer than 64 bytes
#: with its SHA-256, so a construction can be sound at one length and not at the other.
TOKEN_MAKERS: dict[int, Callable[[], str]] = {
    43: lambda: secrets.token_urlsafe(32),
    70: new_token,
}


@pytest.fixture(params=sorted(TOKEN_MAKERS), ids=lambda n: f"{n}-character relay token")
def sized_relay(request: pytest.FixtureRequest) -> Iterator[LiveRelay]:
    relay = FakeRelay(TOKEN_MAKERS[request.param]())
    assert len(relay.token) == request.param
    with serve(relay) as live:
        yield live


def _unkeyed(row_id: str, code: str) -> str:
    """The stored form before change 20."""
    return hashlib.sha256(f"{row_id}:{code}".encode()).hexdigest()


def _keyed(key: bytes, row_id: str, code: str) -> str:
    return hmac.new(key, CODE_LABEL + f"{row_id}:{code}".encode(), hashlib.sha256).hexdigest()


def _derived_key(relay_token: str) -> bytes:
    return hashlib.sha256(KEY_LABEL + relay_token.encode("utf-8")).digest()


def test_the_stored_form_is_keyed_with_a_key_derived_from_the_relay_token(
    tmp_path: Path, sized_relay: LiveRelay
) -> None:
    app = relay_app(tmp_path / "data", sized_relay)
    with TestClient(app) as client:
        seed_local_user(app)
        code = _one_code(sized_relay, client, KNOWN_EMAIL)
        (row,) = rows(
            app, "SELECT id, code_hash FROM sign_in_codes WHERE email = :e", {"e": KNOWN_EMAIL}
        )
    token = sized_relay.relay.token
    stored, row_id = row["code_hash"], row["id"]
    assert stored != _unkeyed(row_id, code), "the code is stored as its unkeyed digest"
    assert stored != _keyed(token.encode(), row_id, code), "the key is the relay token itself"
    assert stored != _keyed(hashlib.sha256(token.encode()).digest(), row_id, code), (
        "the key is the relay token's bare SHA-256, which the hosting control plane stores"
    )
    assert stored == _keyed(_derived_key(token), row_id, code)


def _snapshot_from(tar_bytes: bytes, into: Path) -> Path:
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r|") as tar:
        for member in tar:
            if member.name == "glosswork.sqlite3":
                tar.extract(member, path=into, filter="data")
                return into / member.name
    raise AssertionError("the artifact holds no database snapshot")


def test_an_artifact_holds_no_usable_code(tmp_path: Path, sized_relay: LiveRelay) -> None:
    """A copy of the database, and nothing else, does not give up a live code.

    The artifact comes through the administrator's backup route, which exists with or
    without change 20, so the deciding assertion is reached on either tree. Every
    six-digit value is tried against the stored form with what the holder of an artifact
    and the operator credential has: no key at all, an empty key, the operator credential
    as the key, and the relay token's bare SHA-256, which is the only form of that token
    the hosting control plane stores."""
    app = relay_app(tmp_path / "data", sized_relay, operator_token=OPERATOR_TOKEN)
    with TestClient(app) as client:
        pat = admin_token(app)
        code = _one_code(sized_relay, client, ADMIN_EMAIL)
        taken = TestClient(app).post("/api/v1/admin/backup", headers=auth(pat))
        assert taken.status_code == 200, taken.text
        snapshot = _snapshot_from(taken.content, tmp_path / "out")
        conn = sqlite3.connect(snapshot)
        try:
            ((row_id, stored_hex),) = conn.execute(
                "SELECT id, code_hash FROM sign_in_codes WHERE email = ? AND consumed_at IS NULL",
                (ADMIN_EMAIL,),
            ).fetchall()
        finally:
            conn.close()
        stored = bytes.fromhex(stored_hex)

        token_sha256 = hashlib.sha256(sized_relay.relay.token.encode()).digest()
        keys = {
            "an empty key": b"",
            "the operator credential as key": OPERATOR_TOKEN.encode(),
            "the relay token's bare SHA-256 as key": token_sha256,
        }
        prefix = f"{row_id}:".encode()
        found: list[tuple[str, str]] = []
        for n in range(1_000_000):
            candidate = b"%06d" % n
            if hashlib.sha256(prefix + candidate).digest() == stored:
                found.append(("no key", candidate.decode()))
            message = CODE_LABEL + prefix + candidate
            for name, key in keys.items():
                if hmac.digest(key, message, "sha256") == stored:
                    found.append((name, candidate.decode()))
        assert found == [], f"a code was recovered from the artifact alone: {found}"

        # The positive control: the row searched is a live one, and its code works.
        _signed_in_as(verify_code(client, ADMIN_EMAIL, code), ADMIN_EMAIL)


def test_a_pre_upgrade_code_is_refused(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    """A row an older image wrote, in the unkeyed form, never verifies. Accepting it for
    a while would keep alive exactly the rows an artifact can be used against."""
    seed_local_user(code_app)
    row_id, code = str(uuid.uuid4()), "424242"
    execute(
        code_app,
        "INSERT INTO sign_in_codes (id, email, code_hash, created_at, expires_at, attempts, "
        "consumed_at, sent) VALUES (:id, :email, :hash, "
        "strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), "
        "strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '+10 minutes'), 0, NULL, 1)",
        {"id": row_id, "email": KNOWN_EMAIL, "hash": _unkeyed(row_id, code)},
    )
    refused = verify_code(code_client, KNOWN_EMAIL, code)
    _assert_verify_failure(refused)
    assert "gw_session" not in refused.headers.get("set-cookie", "")
    assert code_client.get("/api/v1/me").status_code == 401
    (row,) = rows(code_app, "SELECT attempts FROM sign_in_codes WHERE id = :id", {"id": row_id})
    assert row["attempts"] == 1, row

    # The positive control: a code this image issues signs the same person in.
    fresh = _one_code(live_relay, code_client, KNOWN_EMAIL)
    _signed_in_as(verify_code(code_client, KNOWN_EMAIL, fresh), KNOWN_EMAIL)


def test_a_code_does_not_survive_a_relay_token_rotation(
    tmp_path: Path,
    live_relay: LiveRelay,  # noqa: F811
) -> None:
    data_dir = tmp_path / "data"
    first = relay_app(data_dir, live_relay)
    with TestClient(first) as client:
        seed_local_user(first)
        issued_before = _one_code(live_relay, client, KNOWN_EMAIL)

    with serve(FakeRelay(new_token())) as rotated:
        assert rotated.relay.token != live_relay.relay.token
        second = relay_app(data_dir, rotated)
        with TestClient(second) as client:
            _assert_verify_failure(verify_code(client, KNOWN_EMAIL, issued_before))
            issued_after = _one_code(rotated, client, KNOWN_EMAIL)
            _signed_in_as(verify_code(client, KNOWN_EMAIL, issued_after), KNOWN_EMAIL)


def test_the_code_service_needs_a_key_exactly_when_it_has_a_relay(
    code_app: FastAPI,  # noqa: F811
    code_client: TestClient,  # noqa: F811
) -> None:
    from glosswork.repositories.sqlite import (
        SqlitePrincipalRepository,
        SqliteSignInCodeRepository,
    )
    from glosswork.services.sign_in_codes import SignInCodeService

    services = services_of(code_app)
    relay = services.relay
    assert relay is not None

    def build(sender: Any, key: bytes | None) -> SignInCodeService:
        return SignInCodeService(
            code_app.state.db,
            SqliteSignInCodeRepository(),
            SqlitePrincipalRepository(),
            services.invites,
            sender,
            code_key=key,
        )

    for sender, key in ((relay, None), (relay, b""), (None, b"k" * 32), (None, b"")):
        with pytest.raises(ValueError):
            build(sender, key)
    assert build(relay, b"k" * 32).enabled is True
    assert build(None, None).enabled is False


def test_code_service_needs_a_key_fence_without_a_relay_no_code_is_ever_stored(
    tmp_path: Path,
) -> None:
    """**Fence.** A deployment with no relay settings has no key and needs none: both
    code routes answer ``feature_disabled`` and no row is written."""
    from glosswork.config import Settings

    app = create_app(Settings(data_dir=tmp_path / "data", embedding_enabled=False))
    with TestClient(app) as client:
        for response in (
            request_code(client, KNOWN_EMAIL),
            verify_code(client, KNOWN_EMAIL, "123456"),
        ):
            assert response.status_code == 409, response.text
            assert response.json()["error"]["code"] == "feature_disabled", response.text
        assert rows(app, "SELECT COUNT(*) AS n FROM sign_in_codes") == [{"n": 0}]
