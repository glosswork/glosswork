"""A fresh container hands back its first admin credential once (DD-37, FR-I1).

``POST /api/v1/bootstrap`` is credential-exempt and guarded by ``GW_BOOTSTRAP_SECRET``:
a program that started this container can exchange the secret it configured for an
``admin`` personal access token and the two addresses a harness needs, exactly once.

Two things about the shape of every test here, both of them scars:

- **Every app sets ``base_url`` to the same origin its ``TestClient`` uses**.
  ``GW_BASE_URL`` turns on the MCP transport's ``Host`` allowlist, so an app configured
  for one origin and driven from ``testserver`` answers ``/mcp`` with **421 Invalid Host
  header** before any credential is read. The first claim's ``tools/list`` would have
  failed on that rather than on anything the handoff is about.
- **A status code alone cannot assert that a route exists.** ``web/dist`` exists in this
  checkout, so the SPA fallback answers any unmatched ``/api/v1/`` path with
  ``200 text/html`` (AGENTS.md, Traps). Every assertion below reads the content type or
  the body, which is also what makes these tests fail correctly on a tree without the
  route.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from glosswork.app import DATABASE_FILENAME, create_app
from glosswork.config import ConfigError, Settings, load_settings

# The origin every app and every client in this module agrees on.
ORIGIN = "https://acme.example"
SECRET = "n6QHcLmR2vTkX8pZwFdJbY4sA7eG3uNq"
assert len(SECRET) >= 32
PASSWORD = "correct-horse-battery-staple"
EMAIL = "admin@acme.example"

JSON_RPC_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _app(tmp_path: Path, *, name: str = "data", **overrides: Any) -> FastAPI:
    """An app on its own data directory, configured for :data:`ORIGIN`."""
    settings = Settings(
        data_dir=tmp_path / name,
        embedding_enabled=False,
        base_url=ORIGIN,
        **overrides,
    )
    return create_app(settings)


def _claim(client: TestClient, **body: Any) -> Any:
    return client.post("/api/v1/bootstrap", json=body)


def _good_claim(client: TestClient, email: str = EMAIL) -> Any:
    return _claim(client, secret=SECRET, email=email, password=PASSWORD)


def _counts(tmp_path: Path, name: str = "data") -> tuple[int, int]:
    """``(user principals, access tokens)`` read straight from the database file, so
    the count this suite trusts is not the one the code under test computes."""
    connection = sqlite3.connect(tmp_path / name / DATABASE_FILENAME)
    try:
        users = connection.execute(
            "SELECT COUNT(*) FROM principals WHERE type = 'user'"
        ).fetchone()[0]
        tokens = connection.execute("SELECT COUNT(*) FROM access_tokens").fetchone()[0]
    finally:
        connection.close()
    return int(users), int(tokens)


def _audit_values(tmp_path: Path, name: str = "data") -> str:
    connection = sqlite3.connect(tmp_path / name / DATABASE_FILENAME)
    try:
        rows = connection.execute("SELECT * FROM audit_events").fetchall()
    finally:
        connection.close()
    return json.dumps(rows, default=str)


def _json(response: Any) -> dict[str, Any]:
    """The response body, asserted to *be* JSON first.

    Not a formality: an unmatched ``/api/v1/`` path is answered either by the edge's
    401 or, where the credential check does not reach, by the SPA fallback's
    ``200 text/html`` (AGENTS.md, Traps). Reading the content type is what makes
    "this route does not exist" fail as an assertion rather than as a decode error.
    """
    assert response.headers["content-type"].startswith("application/json"), response.text
    body: dict[str, Any] = response.json()
    return body


def _error(response: Any) -> dict[str, Any]:
    """The ``{code, message, details}`` envelope, which every failure on this surface
    nests under ``error`` (FR-A4)."""
    envelope: dict[str, Any] = _json(response)["error"]
    return envelope


# -------------------------------------------------------------- the first claim


def test_first_claim_returns_the_handoff(tmp_path: Path) -> None:
    """The whole point of the route: one HTTP call after ``/readyz`` yields a working
    ``admin`` credential and the two addresses a harness needs."""
    app = _app(tmp_path, bootstrap_secret=SECRET)
    with TestClient(app, base_url=ORIGIN) as client:
        # The body, not the status. A status-only assertion here passed
        # against a route that did not exist, the SPA catch-all answering 200 in
        # its place whenever web/dist was built.
        assert client.get("/readyz").json() == {"status": "ok"}

        response = _good_claim(client)
        assert response.status_code == 201, response.text
        body = _json(response)

        assert body["token"].startswith("gw_pat_")
        assert body["token_prefix"] == body["token"][:8]
        assert len(body["token_prefix"]) == 8
        assert body["scope"] == "admin"
        assert body["principal_id"]
        assert body["mcp_url"] == f"{ORIGIN}/mcp"
        assert body["sign_in_url"] == f"{ORIGIN}/login"
        assert response.headers["cache-control"] == "no-store"

        token = body["token"]
        workspace = client.get("/api/v1/workspace", headers={"Authorization": f"Bearer {token}"})
        assert workspace.status_code == 200, workspace.text
        assert workspace.headers["content-type"].startswith("application/json")

        tools = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={**JSON_RPC_HEADERS, "Authorization": f"Bearer {token}"},
        )
        assert tools.status_code == 200, tools.text
        listed = tools.json()
        assert "result" in listed, listed
        assert "create_object_type" in {tool["name"] for tool in listed["result"]["tools"]}


# ----------------------------------------------------------------- exactly once


def test_second_claim_is_refused(tmp_path: Path) -> None:
    """Exactly once. The second claim is refused and writes nothing."""
    app = _app(tmp_path, bootstrap_secret=SECRET)
    with TestClient(app, base_url=ORIGIN) as client:
        assert _good_claim(client).status_code == 201
        before = _counts(tmp_path)

        response = _good_claim(client, email="second@acme.example")
        assert response.status_code == 409, response.text
        assert _error(response)["code"] == "bootstrap_claimed"
        assert _counts(tmp_path) == before


# -------------------------------------------------------------- after a restart


def test_claim_after_restart_is_refused(tmp_path: Path) -> None:
    """The refusal is a fact about the volume, not about one process's memory."""
    first = _app(tmp_path, bootstrap_secret=SECRET)
    with TestClient(first, base_url=ORIGIN) as client:
        assert _good_claim(client).status_code == 201
    before = _counts(tmp_path)

    restarted = _app(tmp_path, bootstrap_secret=SECRET)
    with TestClient(restarted, base_url=ORIGIN) as client:
        response = _good_claim(client, email="second@acme.example")
        assert response.status_code == 409, response.text
        assert _error(response)["code"] == "bootstrap_claimed"
    assert _counts(tmp_path) == before


# ------------------------------------------------------------------- the secret


def test_wrong_or_absent_secret_is_refused(tmp_path: Path) -> None:
    """The secret is compared before anything else is validated or read.

    The third case is the one that matters: a wrong secret carrying a password the
    policy would refuse answers **401, not 422**. A 422 there would tell an
    unauthenticated caller this deployment's password policy.
    """
    app = _app(tmp_path, bootstrap_secret=SECRET)
    with TestClient(app, base_url=ORIGIN) as client:
        before = _counts(tmp_path)

        wrong = _claim(client, secret="x" * 32, email=EMAIL, password=PASSWORD)
        assert wrong.status_code == 401, wrong.text
        assert _error(wrong)["code"] == "invalid_credentials"
        assert _counts(tmp_path) == before

        absent = _claim(client, email=EMAIL, password=PASSWORD)
        assert absent.status_code == 401, absent.text
        assert _error(absent)["code"] == "invalid_credentials"
        assert _counts(tmp_path) == before

        short = _claim(client, secret="x" * 32, email=EMAIL, password="short")
        assert short.status_code == 401, short.text
        assert _error(short)["code"] == "invalid_credentials"
        assert _counts(tmp_path) == before


# ------------------------------------------------------------- feature disabled


def test_claim_without_the_setting_is_feature_disabled(tmp_path: Path) -> None:
    """A deployment that never set the secret behaves as it always did."""
    app = _app(tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        before = _counts(tmp_path)
        response = _claim(client, secret=SECRET, email=EMAIL, password=PASSWORD)
        assert response.status_code == 409, response.text
        body = _error(response)
        assert body["code"] == "feature_disabled"
        assert body["details"]["feature"] == "bootstrap"
        assert body["details"]["setting"] == "GW_BOOTSTRAP_SECRET"
        assert _counts(tmp_path) == before


# ----------------------------------------------- after an environment bootstrap


def test_claim_after_environment_bootstrap_is_refused(tmp_path: Path) -> None:
    """An admin created by ``GW_BOOTSTRAP_ADMIN_*`` on an earlier start closes the
    endpoint. The two are refused *together* at startup (see the settings refusals), so this
    is the only shape the combination can reach: the environment admin on one start, the
    secret alone on the next.
    """
    seeded = _app(
        tmp_path,
        bootstrap_admin_email="env@acme.example",
        bootstrap_admin_password=PASSWORD,
    )
    with TestClient(seeded, base_url=ORIGIN):
        pass
    before = _counts(tmp_path)
    assert before[0] == 1

    restarted = _app(tmp_path, bootstrap_secret=SECRET)
    with TestClient(restarted, base_url=ORIGIN) as client:
        response = _good_claim(client)
        assert response.status_code == 409, response.text
        assert _error(response)["code"] == "bootstrap_claimed"
    assert _counts(tmp_path) == before


# ------------------------------------------------------------ concurrent claims


def test_concurrent_claims_yield_one_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two claims released together yield exactly one 201 (DD-22).

    The barrier sits at the password hash, which is deliberately *outside* the
    transaction, so both threads are guaranteed to be inside the claim when the race is
    run. A passing run is not evidence on its own -- this shape was measured losing the
    race in every trial with the count outside the transaction -- which is why the test
    was first run with the check moved out, and failed.
    """
    from glosswork.services.passwords import PasswordService

    barrier = threading.Barrier(2, timeout=30)
    original_hash = PasswordService.hash

    def barriered_hash(self: PasswordService, password: str) -> str:
        barrier.wait()
        return original_hash(self, password)

    monkeypatch.setattr(PasswordService, "hash", barriered_hash)

    app = _app(tmp_path, bootstrap_secret=SECRET)
    with TestClient(app, base_url=ORIGIN) as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_good_claim, client, f"racer{n}@acme.example") for n in range(2)]
            statuses = sorted(future.result().status_code for future in futures)

    assert statuses == [201, 409], statuses
    assert _counts(tmp_path) == (1, 1)


# ---------------------------------------------------------- no secret is logged


def test_no_secret_reaches_logs_or_audit(tmp_path: Path, capfd: Any) -> None:
    """The plaintext token, the password and the secret appear in no log line and no
    audit row. The token prefix and the email are what a later
    administrator needs, and they are all the line carries."""
    app = _app(tmp_path, bootstrap_secret=SECRET)
    with TestClient(app, base_url=ORIGIN) as client:
        response = _good_claim(client)
        assert response.status_code == 201, response.text
        token = response.json()["token"]

    out, err = capfd.readouterr()
    logs = out + err
    assert "bootstrap_claimed" in logs, logs
    for plaintext in (token, PASSWORD, SECRET):
        assert plaintext not in logs

    audit = _audit_values(tmp_path)
    for plaintext in (token, PASSWORD, SECRET):
        assert plaintext not in audit


# -------------------------------------------------- settings refused at startup


def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in (
        "GW_BOOTSTRAP_SECRET",
        "GW_BOOTSTRAP_ADMIN_EMAIL",
        "GW_BOOTSTRAP_ADMIN_PASSWORD",
        "GW_BASE_URL",
        "GW_AUTH_MODE",
        "GW_OIDC_ISSUER",
        "GW_OIDC_CLIENT_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_EMBEDDING_ENABLED", "false")


def test_settings_refusals(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Configuration that contradicts the secret is refused at startup, not discovered
    when a claim fails. Each case names the variable an operator must fix, and the
    short-secret message never echoes the value."""
    # The handoff cannot be composed without an absolute base.
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("GW_BOOTSTRAP_SECRET", SECRET)
    with pytest.raises(ConfigError) as missing_base:
        load_settings()
    assert "GW_BASE_URL" in str(missing_base.value)

    # A length floor, named, with a generator, and without the value.
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("GW_BASE_URL", ORIGIN)
    monkeypatch.setenv("GW_BOOTSTRAP_SECRET", "tooshort")
    with pytest.raises(ConfigError) as too_short:
        load_settings()
    assert "GW_BOOTSTRAP_SECRET" in str(too_short.value)
    assert "tooshort" not in str(too_short.value)

    # In oidc mode local password sign-in is refused, so the handoff's
    # sign_in_url would lead nowhere for the account the claim just created.
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("GW_BASE_URL", ORIGIN)
    monkeypatch.setenv("GW_BOOTSTRAP_SECRET", SECRET)
    monkeypatch.setenv("GW_AUTH_MODE", "oidc")
    monkeypatch.setenv("GW_OIDC_ISSUER", "https://example.okta.com/oauth2/default")
    monkeypatch.setenv("GW_OIDC_CLIENT_ID", "0oa1acme")
    with pytest.raises(ConfigError) as oidc_mode:
        load_settings()
    assert "GW_AUTH_MODE" in str(oidc_mode.value)

    # With the environment admin set, the lifespan creates it before any claim,
    # so every claim would be a 409 the caller only discovers by making it.
    for variable in ("GW_BOOTSTRAP_ADMIN_EMAIL", "GW_BOOTSTRAP_ADMIN_PASSWORD"):
        _isolate_env(monkeypatch, tmp_path)
        monkeypatch.setenv("GW_BASE_URL", ORIGIN)
        monkeypatch.setenv("GW_BOOTSTRAP_SECRET", SECRET)
        monkeypatch.setenv(variable, "env@acme.example" if "EMAIL" in variable else PASSWORD)
        with pytest.raises(ConfigError) as conflict:
            load_settings()
        assert variable in str(conflict.value)

    # And the setting on its own, with a base URL, starts.
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("GW_BASE_URL", ORIGIN)
    monkeypatch.setenv("GW_BOOTSTRAP_SECRET", SECRET)
    assert load_settings().bootstrap_secret == SECRET


# ------------------------------------------------------------------- a bad body


def test_malformed_or_weak_body_is_refused_without_echo(tmp_path: Path) -> None:
    """A 422 never echoes the body (DD-19), which matters here because the body holds the
    secret. The weak-password case is the other order from the wrong-secret test's third
    case: with the *right* secret, the policy's own refusal is what the caller gets."""
    app = _app(tmp_path, bootstrap_secret=SECRET)
    with TestClient(app, base_url=ORIGIN) as client:
        before = _counts(tmp_path)

        malformed = _claim(client, secret=SECRET)
        assert malformed.status_code == 422, malformed.text
        assert SECRET not in malformed.text
        assert _counts(tmp_path) == before

        weak = _claim(client, secret=SECRET, email=EMAIL, password="short")
        assert weak.status_code == 422, weak.text
        assert _error(weak)["code"] == "validation_failed"
        assert SECRET not in weak.text
        assert _counts(tmp_path) == before


# --------------------------------------------------------------- an agent label


def test_claim_with_an_agent_label_echoes_it_and_stores_it(tmp_path: Path) -> None:
    """The handoff carries a **seventh** name beside DD-37's six, so a hosting operator
    can tell a stored label from a dropped one rather than assuming -- the same failure
    a mint path that silently drops a label causes."""
    app = _app(tmp_path, bootstrap_secret=SECRET)
    with TestClient(app, base_url=ORIGIN) as client:
        response = _claim(
            client, secret=SECRET, email=EMAIL, password=PASSWORD, agent_label="hosting-operator"
        )
        assert response.status_code == 201, response.text
        body = _json(response)
        assert body["agent_label"] == "hosting-operator"

        token = body["token"]
        listed = client.get("/api/v1/access-tokens", headers={"Authorization": f"Bearer {token}"})
        assert listed.status_code == 200, listed.text
        assert [row["agent_label"] for row in listed.json()["access_tokens"]] == [
            "hosting-operator"
        ]


def test_claim_without_an_agent_label_echoes_null(tmp_path: Path) -> None:
    """A deployment claimed without one behaves exactly as it does today."""
    app = _app(tmp_path, bootstrap_secret=SECRET)
    with TestClient(app, base_url=ORIGIN) as client:
        response = _good_claim(client)
        assert response.status_code == 201, response.text
        assert _json(response)["agent_label"] is None
