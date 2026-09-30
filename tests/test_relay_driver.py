"""The relay driver and its configuration (change 9).

Three parts:

1. **Settings.** ``GW_RELAY_URL`` and ``GW_RELAY_TOKEN`` turn email codes on together;
   startup refuses every half-configured or unsafe combination, naming the variable and
   never echoing a value.
2. **The fake relay enforces the definition.** A request outside ``docs/DEPLOYMENT.md``
   section 5a is refused by ``tests/fake_relay.py``. These are guards on the enforcer
   itself: they would pass on a tree without the driver, and are not counted as coverage
   of it.
3. **The driver.** ``build_relay_request`` produces exactly the documented request for
   each template, which the fake relay accepts, and ``RelaySender`` maps each answer to
   one outcome.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest

from glosswork.config import ConfigError, load_settings
from tests.fake_relay import FakeRelay, LiveRelay, Scripted, new_token, serve

GOOD_URL = "https://api.glosswork.dev/v1/relay/send"


def _token() -> str:
    return new_token()


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> pytest.MonkeyPatch:
    for name in ("GW_RELAY_URL", "GW_RELAY_TOKEN", "GW_AUTH_MODE", "GW_BOOTSTRAP_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path))
    return monkeypatch


# -------------------------------------------------------------------- settings


def test_both_settings_turn_codes_on(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GW_RELAY_URL", GOOD_URL)
    clean_env.setenv("GW_RELAY_TOKEN", _token())
    settings = load_settings()
    assert settings.email_codes_enabled is True


def test_blank_counts_as_unset(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GW_RELAY_URL", "")
    clean_env.setenv("GW_RELAY_TOKEN", "  ")
    settings = load_settings()
    assert settings.email_codes_enabled is False


@pytest.mark.parametrize(
    ("url", "token", "names"),
    [
        (GOOD_URL, None, "GW_RELAY_TOKEN"),
        (None, "t" * 40, "GW_RELAY_URL"),
        ("http://relay.example.com/send", "t" * 40, "GW_RELAY_URL"),
        ("/v1/relay/send", "t" * 40, "GW_RELAY_URL"),
        ("ftp://relay.example.com/send", "t" * 40, "GW_RELAY_URL"),
        ("https://user:pw@relay.example.com/send", "t" * 40, "GW_RELAY_URL"),
        ("https://relay.example.com/send?x=1", "t" * 40, "GW_RELAY_URL"),
        ("https://relay.example.com/send#frag", "t" * 40, "GW_RELAY_URL"),
        (GOOD_URL, "t" * 31, "GW_RELAY_TOKEN"),
    ],
)
def test_startup_refuses_a_half_or_unsafe_relay_configuration(
    clean_env: pytest.MonkeyPatch, url: str | None, token: str | None, names: str
) -> None:
    if url is not None:
        clean_env.setenv("GW_RELAY_URL", url)
    if token is not None:
        clean_env.setenv("GW_RELAY_TOKEN", token)
    with pytest.raises(ConfigError) as refused:
        load_settings()
    message = str(refused.value)
    assert names in message, message
    if token is not None:
        assert token not in message
    if url is not None and "pw@" in url:
        assert "pw" not in message.replace("pw@", "")  # never echoes the URL's secret part
        assert url not in message


def test_startup_refuses_codes_in_oidc_only_mode(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GW_RELAY_URL", GOOD_URL)
    clean_env.setenv("GW_RELAY_TOKEN", _token())
    clean_env.setenv("GW_AUTH_MODE", "oidc")
    clean_env.setenv("GW_OIDC_ISSUER", "https://example.okta.com")
    clean_env.setenv("GW_OIDC_CLIENT_ID", "client")
    clean_env.setenv("GW_BASE_URL", "https://tracker.example.com")
    with pytest.raises(ConfigError) as refused:
        load_settings()
    assert "GW_AUTH_MODE" in str(refused.value)


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8791/v1/relay/send", "http://localhost:8791/send", "http://[::1]:9/send"],
)
def test_a_loopback_relay_may_be_plain_http(clean_env: pytest.MonkeyPatch, url: str) -> None:
    clean_env.setenv("GW_RELAY_URL", url)
    clean_env.setenv("GW_RELAY_TOKEN", _token())
    assert load_settings().email_codes_enabled is True


# ------------------------------------------------- the fake relay's enforcement


def _valid_body(**overrides: Any) -> dict[str, Any]:
    expires = (datetime.now(UTC) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    body: dict[str, Any] = {
        "message_id": str(uuid.uuid4()),
        "template": "sign_in_code",
        "to": "ada@example.com",
        "fields": {"code": "012345", "code_expires_at": expires},
    }
    body.update(overrides)
    return body


def _post(live: LiveRelay, body: Any, token: str | None = None, **headers: str) -> Any:
    sent_headers = {
        "Authorization": f"Bearer {token if token is not None else live.relay.token}",
        "Content-Type": "application/json",
    }
    sent_headers.update(headers)
    return httpx2.post(live.url, content=json.dumps(body).encode(), headers=sent_headers)


@pytest.fixture
def live() -> Any:
    relay = FakeRelay(new_token())
    with serve(relay) as served:
        yield served


def test_the_fake_relay_accepts_a_request_inside_the_definition(live: LiveRelay) -> None:
    """The positive control for every refusal below."""
    assert _post(live, _valid_body()).status_code == 202
    assert len(live.relay.messages()) == 1


def _in(minutes: float) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.parametrize(
    "make_body",
    [
        lambda: _valid_body(extra="x"),
        lambda: _valid_body(fields={"code": "01234", "code_expires_at": _in(10)}),
        lambda: _valid_body(fields={"code": "0123456", "code_expires_at": _in(10)}),
        lambda: _valid_body(fields={"code": 123456, "code_expires_at": _in(10)}),
        lambda: _valid_body(fields={"code": "012345", "code_expires_at": _in(31)}),
        lambda: _valid_body(fields={"code": "012345", "code_expires_at": _in(2)}),
        lambda: _valid_body(fields={"code": "012345", "code_expires_at": "2026-09-29T15:25:00.5Z"}),
        lambda: _valid_body(fields={"code": "012345", "code_expires_at": _in(10), "link": "x"}),
        lambda: _valid_body(to=["ada@example.com", "eve@example.com"]),
        lambda: _valid_body(to="ada@example.com, eve@example.com"),
        lambda: _valid_body(to="Ada@Example.com"),
        lambda: _valid_body(template="password_reset"),
        lambda: _valid_body(template="invite"),
        lambda: _valid_body(message_id="not-a-uuid"),
        lambda: _valid_body(message_id=str(uuid.uuid4()).upper()),
        lambda: {"template": "sign_in_code"},
    ],
)
def test_the_fake_relay_refuses_a_request_outside_the_definition(
    live: LiveRelay, make_body: Any
) -> None:
    """Each body is built when its case runs, so a relative expiry is measured against the
    relay's clock at that moment rather than at import."""
    assert _post(live, make_body()).status_code == 400
    assert live.relay.messages() == []


def test_the_fake_relay_refuses_a_missing_or_wrong_bearer(live: LiveRelay) -> None:
    assert _post(live, _valid_body(), token="wrong").status_code == 401
    no_auth = httpx2.post(
        live.url,
        content=json.dumps(_valid_body()).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert no_auth.status_code == 401
    assert live.relay.messages() == []


# ---------------------------------------------------------------------- driver


FIXED_NOW = datetime(2026, 9, 29, 15, 15, 0, tzinfo=UTC)


def _settings(url: str, token: str) -> Any:
    from glosswork.config import Settings

    return Settings(relay_url=url, relay_token=token, embedding_enabled=False)


def test_the_driver_sends_exactly_the_documented_request_for_each_template(
    live: LiveRelay,
) -> None:
    from glosswork.services.relay import RelayMessage, RelaySender, build_relay_request

    settings = _settings(live.url, live.relay.token)
    expires = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=10)
    code_message = RelayMessage.sign_in_code("ada@example.com", "004217", expires)
    invite_message = RelayMessage.invite("lin@example.com", "Grace Hopper")

    request = build_relay_request(code_message, settings)
    assert request.method == "POST"
    assert request.url == live.url
    assert request.headers == {
        "Authorization": f"Bearer {live.relay.token}",
        "Content-Type": "application/json",
    }
    assert list(json.loads(request.body)) == ["message_id", "template", "to", "fields"]
    assert json.loads(request.body)["fields"] == {
        "code": "004217",
        "code_expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    with httpx2.Client() as client:
        sender = RelaySender(settings, client)
        assert sender.send(code_message).outcome == "accepted"
        assert sender.send(invite_message).outcome == "accepted"
    sent = live.relay.messages()
    assert [m["template"] for m in sent] == ["sign_in_code", "invite"]
    assert sent[1] == {
        "message_id": invite_message.message_id,
        "template": "invite",
        "to": "lin@example.com",
        "fields": {"inviter_name": "Grace Hopper"},
    }
    assert live.relay.refused == []


def _mock_sender(handler: Any, timeout_seconds: float | None = None) -> Any:
    from glosswork.services.relay import RelaySender

    settings = _settings("https://relay.example.com/send", "t" * 40)
    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    if timeout_seconds is None:
        return RelaySender(settings, client)
    return RelaySender(settings, client, timeout_seconds=timeout_seconds)


def _invite() -> Any:
    from glosswork.services.relay import RelayMessage

    return RelayMessage.invite("lin@example.com", "christopher.scheidel")


@pytest.mark.parametrize(
    ("status", "body", "outcome", "fields"),
    [
        (200, {}, "accepted", []),
        (202, {}, "accepted", []),
        (204, None, "accepted", []),
        (401, {}, "refused_credential", []),
        (403, {}, "refused_credential", []),
        (422, {"error": {"code": "refused_fields", "fields": ["inviter_name"]}}, "refused_fields",
         ["inviter_name"]),
        (422, {"error": {"code": "other", "fields": ["inviter_name"]}}, "refused_fields", []),
        (422, {"error": {"code": "refused_fields", "fields": "inviter_name"}}, "refused_fields",
         []),
        (422, "not json", "refused_fields", []),
        (429, {}, "rate_limited", []),
        (400, {}, "unavailable", []),
        (404, {}, "unavailable", []),
        (500, {}, "unavailable", []),
        (503, {}, "unavailable", []),
        (302, {}, "unavailable", []),
    ],
)
def test_each_answer_maps_to_one_outcome(
    status: int, body: Any, outcome: str, fields: list[str]
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if body is None:
            return httpx2.Response(status)
        if isinstance(body, str):
            return httpx2.Response(status, content=body.encode())
        return httpx2.Response(status, json=body)

    result = _mock_sender(handler).send(_invite())
    assert result.outcome == outcome
    assert result.refused_fields == fields


def test_a_connection_error_is_unavailable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    assert _mock_sender(handler).send(_invite()).outcome == "unavailable"


def test_no_complete_answer_within_the_deadline_is_unavailable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        time.sleep(0.6)
        return httpx2.Response(202)

    started = time.monotonic()
    result = _mock_sender(handler, timeout_seconds=0.2).send(_invite())
    assert result.outcome == "unavailable"
    assert time.monotonic() - started < 0.5


def test_the_relay_timeout_is_five_seconds_in_total() -> None:
    from glosswork.services import relay

    assert relay.RELAY_TIMEOUT_SECONDS == 5


def test_the_driver_never_retries() -> None:
    calls: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return httpx2.Response(503)

    assert _mock_sender(handler).send(_invite()).outcome == "unavailable"
    assert calls == [1]


def test_a_refusal_body_is_read_for_field_names_only(capfd: pytest.CaptureFixture[str]) -> None:
    """The workspace reads ``fields`` from exactly the defined shape and logs the names,
    never a value (the relay may echo nothing, but a misbehaving one might)."""
    from glosswork.logging import configure_logging

    configure_logging("debug")

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            422,
            json={
                "error": {"code": "refused_fields", "fields": ["inviter_name"]},
                "echo": "christopher.scheidel",
            },
        )

    result = _mock_sender(handler).send(_invite())
    assert result.refused_fields == ["inviter_name"]
    out, _ = capfd.readouterr()
    assert "inviter_name" in out
    assert "christopher.scheidel" not in out


def test_a_scripted_refusal_from_the_live_fake_round_trips(live: LiveRelay) -> None:
    from glosswork.services.relay import RelaySender

    live.relay.script(Scripted(status=422, refused=["inviter_name"]))
    with httpx2.Client() as client:
        result = RelaySender(_settings(live.url, live.relay.token), client).send(_invite())
    assert result.outcome == "refused_fields"
    assert result.refused_fields == ["inviter_name"]
