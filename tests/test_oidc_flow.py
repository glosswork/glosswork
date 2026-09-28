"""The OIDC authorization-code + PKCE flow (FR-I1, DD-10).

Distinct from `test_oidc.py`, which covers `OidcVerifier` (validating an ID token
already in hand); this covers `OidcFlowService`, which gets one: building the
authorization redirect and exchanging a code for it. The one network call — the
exchange — is made through an injected `httpx2.Client` over `httpx2.MockTransport`, so
nothing here listens on a socket, matching `test_oidc.py`'s own
network-free discipline for the verifier.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx2
import pytest

from glosswork.config import Settings
from glosswork.services.oidc import OidcNotConfiguredError
from glosswork.services.oidc_flow import OidcExchangeFailedError, OidcFlowService

ISSUER = "https://example.okta.com/oauth2/default"
CLIENT_ID = "0oa1glosswork"
REDIRECT_URI = "https://glosswork.example.com/api/v1/auth/oidc/callback"


def flow_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "auth_mode": "oidc",
        "oidc_issuer": ISSUER,
        "oidc_client_id": CLIENT_ID,
        "base_url": "https://glosswork.example.com",
        # These settings build real apps; none of these tests is about search,
        # so none should acquire a model dependency.
        "embedding_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)


def mock_client(handler: Any) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(handler))


# ------------------------------------------------------------------------------ start


def test_start_builds_a_pkce_authorization_url() -> None:
    service = OidcFlowService(flow_settings())
    transaction = service.start(REDIRECT_URI)

    parsed = urlparse(transaction.authorization_url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "example.okta.com"
    assert parsed.path == "/oauth2/default/v1/authorize"
    params = parse_qs(parsed.query)
    assert params["response_type"] == ["code"]
    assert params["client_id"] == [CLIENT_ID]
    assert params["redirect_uri"] == [REDIRECT_URI]
    assert params["state"] == [transaction.state]
    assert params["code_challenge_method"] == ["S256"]
    assert "code_challenge" in params
    # PKCE: the challenge is derived from the verifier, not equal to it.
    assert params["code_challenge"][0] != transaction.code_verifier


def test_start_generates_fresh_state_and_verifier_each_call() -> None:
    service = OidcFlowService(flow_settings())
    first = service.start(REDIRECT_URI)
    second = service.start(REDIRECT_URI)
    assert first.state != second.state
    assert first.code_verifier != second.code_verifier


def test_start_refuses_in_standalone_mode() -> None:
    service = OidcFlowService(flow_settings(auth_mode="standalone"))
    with pytest.raises(OidcNotConfiguredError):
        service.start(REDIRECT_URI)


# --------------------------------------------------------------------- exchange_code


def test_exchange_code_posts_to_the_token_endpoint_and_returns_the_id_token() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["url"] = str(request.url)
        captured["body"] = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))
        return httpx2.Response(200, json={"id_token": "the-id-token", "token_type": "Bearer"})

    service = OidcFlowService(flow_settings(), mock_client(handler))
    id_token = service.exchange_code("the-code", "the-verifier", REDIRECT_URI)

    assert id_token == "the-id-token"
    assert captured["url"] == f"{ISSUER}/v1/token"
    assert captured["body"]["grant_type"] == "authorization_code"
    assert captured["body"]["code"] == "the-code"
    assert captured["body"]["code_verifier"] == "the-verifier"
    assert captured["body"]["client_id"] == CLIENT_ID


def test_exchange_code_includes_the_client_secret_when_configured() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))
        assert body["client_secret"] == "shh"
        return httpx2.Response(200, json={"id_token": "tok"})

    service = OidcFlowService(flow_settings(oidc_client_secret="shh"), mock_client(handler))
    service.exchange_code("code", "verifier", REDIRECT_URI)


def test_exchange_code_raises_when_the_provider_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json={"error": "invalid_grant"})

    service = OidcFlowService(flow_settings(), mock_client(handler))
    with pytest.raises(OidcExchangeFailedError):
        service.exchange_code("bad-code", "verifier", REDIRECT_URI)


def test_exchange_code_raises_when_no_id_token_is_returned() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"access_token": "at", "token_type": "Bearer"})

    service = OidcFlowService(flow_settings(), mock_client(handler))
    with pytest.raises(OidcExchangeFailedError):
        service.exchange_code("code", "verifier", REDIRECT_URI)


def test_exchange_code_refuses_in_standalone_mode() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:  # pragma: no cover - must not run
        raise AssertionError("no network call should be attempted")

    service = OidcFlowService(flow_settings(auth_mode="standalone"), mock_client(handler))
    with pytest.raises(OidcNotConfiguredError):
        service.exchange_code("code", "verifier", REDIRECT_URI)
