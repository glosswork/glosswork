"""With no relay configured, a workspace behaves exactly as it did before change 9.

Email codes are hosted-only (Q72): the relay is the only sender, and a workspace without
``GW_RELAY_URL`` and ``GW_RELAY_TOKEN`` keeps standalone passwords and administrator-made
people. Password sign-in with no relay is a **fence** here: the existing
``tests/test_auth_routes.py`` and ``tests/test_local_accounts.py`` are its coverage.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from glosswork.app import create_app
from glosswork.config import Settings


@pytest.fixture
def plain_app(tmp_path: Path) -> FastAPI:
    return create_app(Settings(data_dir=tmp_path / "data", embedding_enabled=False))


@pytest.fixture
def plain_client(plain_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(plain_app) as client:
        yield client


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/auth/code/request", {"email": "ada@example.com"}),
        ("/api/v1/auth/code/verify", {"email": "ada@example.com", "code": "123456"}),
    ],
)
def test_the_code_routes_answer_feature_disabled(
    plain_client: TestClient, path: str, body: dict[str, str]
) -> None:
    response = plain_client.post(path, json=body)
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "feature_disabled", response.text
    assert error["details"] == {"feature": "email_code_sign_in", "setting": "GW_RELAY_URL"}


def test_modes_reports_email_code_off_and_standalone_as_today(plain_client: TestClient) -> None:
    modes = plain_client.get("/api/v1/auth/modes")
    assert modes.status_code == 200, modes.text
    assert modes.json() == {"standalone": True, "oidc": False, "email_code": False}


def test_no_relay_client_is_constructed(plain_client: TestClient, plain_app: FastAPI) -> None:
    """**Fence**: no relay client exists before the change either."""
    services = plain_app.state.services
    assert getattr(services, "relay", None) is None
