"""Shared helpers for the email-code sign-in and invite suites (change 9).

Every app built here is configured exactly as a hosted workspace is: ``GW_RELAY_URL``
names a live fake relay on a loopback port and ``GW_RELAY_TOKEN`` its token. Nothing
reaches into ``create_app`` through a seam, which is also what lets these suites run
against a tree without the feature and fail on behaviour rather than on an import.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from glosswork.app import create_app
from glosswork.config import Settings
from glosswork.services import ServiceBundle
from tests.conftest import make_actor
from tests.fake_relay import FakeRelay, LiveRelay, new_token, serve

#: The request's one answer, whatever the address (the plan's "Sign-in by code", step 2).
REQUEST_MESSAGE = "If this address can sign in here, a code is on its way. It works for 10 minutes."
#: Every verify failure's one message (step 4).
VERIFY_FAILURE_MESSAGE = "That code is not right, or it has expired. Ask for a new code."

KNOWN_EMAIL = "ada@example.com"
ADMIN_EMAIL = "grace@example.com"
ADMIN_NAME = "Grace Hopper"


@pytest.fixture
def live_relay() -> Iterator[LiveRelay]:
    relay = FakeRelay(new_token())
    with serve(relay) as live:
        yield live


def relay_settings(data_dir: Path, live: LiveRelay, **overrides: Any) -> Settings:
    """A hosted workspace's settings: the relay configured, embedding off, plain-HTTP
    cookies because ``TestClient`` talks to ``http://testserver``."""
    values: dict[str, Any] = {
        "data_dir": data_dir,
        "embedding_enabled": False,
        "cookie_secure": False,
        "relay_url": live.url,
        "relay_token": live.relay.token,
    }
    values.update(overrides)
    return Settings(**values)


def relay_app(data_dir: Path, live: LiveRelay, **overrides: Any) -> FastAPI:
    return create_app(relay_settings(data_dir, live, **overrides))


@pytest.fixture
def code_app(tmp_path: Path, live_relay: LiveRelay) -> FastAPI:
    """Login limits raised well clear of what one test spends, so a test about a code is
    never answered by the limiter instead; the limiter has its own test."""
    return relay_app(
        tmp_path / "data", live_relay, login_max_attempts=100, login_ip_max_attempts=1000
    )


@pytest.fixture
def code_client(code_app: FastAPI) -> Iterator[TestClient]:
    """No credential attached: these are the routes a signed-out browser calls."""
    with TestClient(code_app) as client:
        yield client


def services_of(app: FastAPI) -> ServiceBundle:
    services: ServiceBundle = app.state.services
    return services


def seed_local_user(
    app: FastAPI, email: str = KNOWN_EMAIL, role: str = "member", name: str = "Ada Lovelace"
) -> str:
    """An active local person with no password, as a hosted workspace's people are."""
    principal = services_of(app).principals.create_user(
        make_actor(), email=email, display_name=name, role=role
    )
    return principal.id


def admin_token(app: FastAPI, email: str = ADMIN_EMAIL, name: str = ADMIN_NAME) -> str:
    """An administrator person and an ``admin`` personal access token for them."""
    services = services_of(app)
    existing = services.principals.find_by_email(email)
    principal_id = existing.id if existing else seed_local_user(app, email, "admin", name)
    minted = services.tokens.mint(
        make_actor(), name="invite tests", scope="admin", principal_id=principal_id
    )
    return minted.plaintext


def request_code(client: TestClient, email: str) -> Any:
    return client.post("/api/v1/auth/code/request", json={"email": email})


def verify_code(client: TestClient, email: str, code: str) -> Any:
    return client.post("/api/v1/auth/code/verify", json={"email": email, "code": code})


def codes_sent_to(relay: FakeRelay, email: str) -> list[str]:
    return [m["fields"]["code"] for m in relay.messages("sign_in_code") if m["to"] == email]


def wrong_code(right: str) -> str:
    return f"{(int(right) + 1) % 1_000_000:06d}"


def execute(app: FastAPI, sql: str, params: dict[str, Any] | None = None) -> None:
    """Move time in the test's own database (F16): backdate rows rather than add a clock
    seam to ``create_app``."""
    with app.state.db.write() as conn:
        conn.execute(text(sql), params or {})


def rows(app: FastAPI, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with app.state.db.read() as conn:
        return [dict(r) for r in conn.execute(text(sql), params or {}).mappings()]


def source(ip: str) -> tuple[str, int]:
    return (ip, 50000)


def fresh_email() -> str:
    return f"person-{uuid.uuid4().hex[:12]}@example.com"
