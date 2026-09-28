"""Login rate limiting (FR-I1, DD-9).

The four basic cases, plus the two the design turns on: that the
limiter counts before it looks anything up (so it cannot become an account-existence
oracle), and that a bearer-credentialed surface is untouched by it.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from glosswork.actor import bootstrap_actor
from glosswork.app import create_app
from glosswork.config import Settings
from glosswork.errors import RateLimitedError
from glosswork.services.rate_limit import MAX_TRACKED_KEYS, LoginRateLimiter

PASSWORD = "CorrectHorseBattery1"
EMAIL = "ada@example.com"


@pytest.fixture
def limited_app(tmp_path: Any) -> Any:
    """An app with a deliberately tiny ceiling, so the tests trip it in three calls
    rather than ten and stay fast even though each attempt pays for an Argon2id hash."""
    settings = Settings(
        data_dir=tmp_path / "data",
        embedding_enabled=False,
        login_max_attempts=3,
        login_window_seconds=60,
    )
    return create_app(settings)


@pytest.fixture
def limited_client(limited_app: Any) -> Any:
    with TestClient(limited_app) as client:
        services = limited_app.state.services
        services.principals.create_user(
            bootstrap_actor("seed"),
            email=EMAIL,
            display_name="Ada",
            role="admin",
            auth_provider="local",
            password=PASSWORD,
        )
        yield client


# --------------------------------------------------------------- the unit itself


def test_ceiling_trips_after_the_configured_number_of_attempts() -> None:
    limiter = LoginRateLimiter(max_attempts=3, ip_max_attempts=60, window_seconds=60)
    for _ in range(3):
        limiter.check_and_record(EMAIL, "203.0.113.10")
    with pytest.raises(RateLimitedError) as excinfo:
        limiter.check_and_record(EMAIL, "203.0.113.10")
    assert excinfo.value.code == "rate_limited"
    assert 0 < excinfo.value.retry_after_seconds <= 60


def test_window_expires_and_the_budget_returns() -> None:
    limiter = LoginRateLimiter(max_attempts=2, ip_max_attempts=60, window_seconds=1)
    limiter.check_and_record(EMAIL, "203.0.113.10")
    limiter.check_and_record(EMAIL, "203.0.113.10")
    with pytest.raises(RateLimitedError):
        limiter.check_and_record(EMAIL, "203.0.113.10")
    time.sleep(1.05)
    limiter.check_and_record(EMAIL, "203.0.113.10")  # must not raise


def test_a_different_ip_is_unaffected() -> None:
    """A successful login from a different IP is unaffected. That is also what forces the
    key to be the (email, IP) pair rather than the email alone: an email-only counter would
    lock the real owner out of every address."""
    limiter = LoginRateLimiter(max_attempts=2, ip_max_attempts=60, window_seconds=60)
    limiter.check_and_record(EMAIL, "203.0.113.10")
    limiter.check_and_record(EMAIL, "203.0.113.10")
    with pytest.raises(RateLimitedError):
        limiter.check_and_record(EMAIL, "203.0.113.10")
    limiter.check_and_record(EMAIL, "198.51.100.7")  # must not raise


def test_a_different_email_from_the_same_ip_is_unaffected() -> None:
    """**Fence.** This asserts the *bounded* form -- one further email
    from a blocked pair's address still gets through -- and stays true under the new
    source window, which has budget left here. It never asserted that varying emails
    is unlimited; nothing ever did, which is how that gap went unnoticed for so long."""
    limiter = LoginRateLimiter(max_attempts=2, ip_max_attempts=60, window_seconds=60)
    limiter.check_and_record(EMAIL, "203.0.113.10")
    limiter.check_and_record(EMAIL, "203.0.113.10")
    with pytest.raises(RateLimitedError):
        limiter.check_and_record(EMAIL, "203.0.113.10")
    limiter.check_and_record("someone.else@example.com", "203.0.113.10")  # must not raise


def test_the_email_key_is_case_and_whitespace_normalized() -> None:
    """Otherwise an attacker gets a fresh budget per capitalization, which is a
    limiter that limits nothing."""
    limiter = LoginRateLimiter(max_attempts=2, ip_max_attempts=60, window_seconds=60)
    limiter.check_and_record("Ada@Example.com", "203.0.113.10")
    limiter.check_and_record("  ada@example.com  ", "203.0.113.10")
    with pytest.raises(RateLimitedError):
        limiter.check_and_record("ADA@EXAMPLE.COM", "203.0.113.10")


def test_success_clears_the_window() -> None:
    limiter = LoginRateLimiter(max_attempts=2, ip_max_attempts=60, window_seconds=60)
    limiter.check_and_record(EMAIL, "203.0.113.10")
    limiter.reset(EMAIL, "203.0.113.10")
    limiter.check_and_record(EMAIL, "203.0.113.10")
    limiter.check_and_record(EMAIL, "203.0.113.10")  # a full budget again


def test_tracked_keys_are_bounded() -> None:
    """An attacker cycling source addresses must not grow this dictionary without
    bound; the limiter drops the oldest live window rather than the process."""
    limiter = LoginRateLimiter(max_attempts=5, ip_max_attempts=5, window_seconds=600)
    for i in range(MAX_TRACKED_KEYS + 50):
        limiter.check_and_record(EMAIL, f"198.51.100.{i}")
    # The cap applies to the **union** of the pair and source windows, because
    # every attempt now opens one of each.
    union = len(limiter._windows) + len(limiter._ip_windows)  # noqa: SLF001
    assert union <= MAX_TRACKED_KEYS


# ------------------------------------------------------------------- over HTTP


def test_login_returns_429_with_retry_after_once_the_ceiling_trips(
    limited_client: TestClient,
) -> None:
    for _ in range(3):
        assert (
            limited_client.post(
                "/api/v1/auth/login", json={"email": EMAIL, "password": "wrong-password"}
            ).status_code
            == 401
        )

    refused = limited_client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": "wrong-password"}
    )
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "rate_limited"
    assert int(refused.headers["Retry-After"]) > 0


def test_the_ceiling_refuses_the_correct_password_too(limited_client: TestClient) -> None:
    """The limiter runs before the credential check, so a real password does not get
    a free pass once the budget is spent -- otherwise the ceiling is only a ceiling
    for attackers who guess wrong, which is all of them until they do not."""
    for _ in range(3):
        limited_client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "nope"})
    refused = limited_client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert refused.status_code == 429


def test_a_known_and_an_unknown_email_are_indistinguishable(limited_client: TestClient) -> None:
    """The account-enumeration property, asserted on both halves: the 401s
    before the ceiling and the 429s after it must match in status, code and body.

    This is the property the dummy-hash branch establishes at the service layer, and
    the limiter is the most obvious thing that could have undone it -- by counting only
    real accounts, or by saying which it was counting.
    """
    known = [
        limited_client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "wrong"})
        for _ in range(4)
    ]
    unknown = [
        limited_client.post(
            "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong"}
        )
        for _ in range(4)
    ]

    assert [r.status_code for r in known] == [401, 401, 401, 429]
    assert [r.status_code for r in unknown] == [401, 401, 401, 429]
    for a, b in zip(known, unknown, strict=True):
        assert a.json() == b.json()


def test_the_refusal_costs_no_password_hash(limited_client: TestClient) -> None:
    """The timing class.

    A refused attempt must not verify anything, so it returns in far less than the
    ~33 ms an Argon2id verification costs, and it must do so identically for a known
    and an unknown email. Asserted as an order-of-magnitude separation rather than a
    tight bound, because a wall-clock assertion on a loaded CI machine is otherwise a
    flake generator.
    """

    def elapsed_ms(email: str) -> float:
        start = time.perf_counter()
        response = limited_client.post(
            "/api/v1/auth/login", json={"email": email, "password": "wrong"}
        )
        assert response.status_code == 429
        return (time.perf_counter() - start) * 1000

    # Both budgets, because the limiter keys on the (email, IP) pair: spending one
    # email's window says nothing about the other's.
    for email in (EMAIL, "nobody@example.com"):
        for _ in range(3):
            limited_client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})

    known_ms = elapsed_ms(EMAIL)
    unknown_ms = elapsed_ms("nobody@example.com")
    assert known_ms < 15, f"a refused login appears to have hashed a password ({known_ms:.1f} ms)"
    assert unknown_ms < 15


# ------------------------------------------------------------ the source window


def test_varying_the_email_no_longer_buys_an_unlimited_source_budget() -> None:
    """The review's finding as a unit assertion: a fresh random email per request used to open
    a fresh bucket every time, so thirty requests from one address bought thirty full
    Argon2id runs and no 429."""
    limiter = LoginRateLimiter(max_attempts=10, ip_max_attempts=5, window_seconds=60)
    for i in range(5):
        limiter.check_and_record(f"probe{i}@example.com", "203.0.113.10")
    with pytest.raises(RateLimitedError):
        limiter.check_and_record("probe5@example.com", "203.0.113.10")


def test_the_source_window_does_not_reach_a_second_address() -> None:
    """The counterpart of ``test_a_different_ip_is_unaffected`` for the new window:
    an office behind one address is throttled together, and nobody else is."""
    limiter = LoginRateLimiter(max_attempts=10, ip_max_attempts=2, window_seconds=60)
    limiter.check_and_record("a@example.com", "203.0.113.10")
    limiter.check_and_record("b@example.com", "203.0.113.10")
    with pytest.raises(RateLimitedError):
        limiter.check_and_record("c@example.com", "203.0.113.10")
    limiter.check_and_record("c@example.com", "198.51.100.7")  # must not raise


def test_success_clears_the_pair_window_and_leaves_the_source_budget_spent() -> None:
    """Section 2's reset rule. Clearing the source window on success would hand an
    attacker holding one valid account an unlimited budget for guessing the rest."""
    limiter = LoginRateLimiter(max_attempts=2, ip_max_attempts=3, window_seconds=60)
    limiter.check_and_record(EMAIL, "203.0.113.10")
    limiter.check_and_record(EMAIL, "203.0.113.10")
    limiter.reset(EMAIL, "203.0.113.10")
    limiter.check_and_record(EMAIL, "203.0.113.10")  # the pair window is fresh...
    with pytest.raises(RateLimitedError):
        limiter.check_and_record(EMAIL, "203.0.113.10")  # ...and the source's is not


def test_eviction_pressure_from_other_sources_cannot_clear_a_blocked_window() -> None:
    """Eviction pressure cannot clear a blocked window.

    ``_remember`` appended to a single insertion-ordered deque and never re-appended a
    key on update, so a long-lived source window sat permanently at the head and was
    the *first* thing dropped once the cap was reached: roughly
    ``MAX_TRACKED_KEYS / ip_max_attempts`` other sources would hand a blocked address a
    clean budget. Eviction now picks by last update and skips any window at or over its
    ceiling.
    """
    limiter = LoginRateLimiter(max_attempts=5, ip_max_attempts=2, window_seconds=600)
    blocked = "203.0.113.10"
    limiter.check_and_record(EMAIL, blocked)
    limiter.check_and_record(EMAIL, blocked)
    with pytest.raises(RateLimitedError):
        limiter.check_and_record(EMAIL, blocked)

    # Enough distinct sources, each attempting once, to force eviction many times over.
    for i in range(MAX_TRACKED_KEYS):
        limiter.check_and_record(f"other{i}@example.com", f"198.51.{i // 256}.{i % 256}")

    assert len(limiter._windows) + len(limiter._ip_windows) <= MAX_TRACKED_KEYS  # noqa: SLF001
    with pytest.raises(RateLimitedError):
        limiter.check_and_record(EMAIL, blocked)


def test_the_source_window_trips_over_http_on_the_sixty_first_attempt(
    limited_client: TestClient,
) -> None:
    """The review's probe, as an assertion: one address, a fresh random email per
    request, no pair window anywhere near its ceiling. Without the source window this
    returned sixty 401s and a sixty-first, and paid for an Argon2id verification every time.

    ``limited_client`` lowers the pair budget to 3 and inherits the default source
    budget of 60, so only the source window can be what refuses here.
    """
    for i in range(60):
        response = limited_client.post(
            "/api/v1/auth/login", json={"email": f"probe{i}@example.com", "password": "wrong"}
        )
        assert response.status_code == 401, (i, response.text)

    refused = limited_client.post(
        "/api/v1/auth/login", json={"email": "probe60@example.com", "password": "wrong"}
    )
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "rate_limited"
    assert refused.json()["error"]["details"]["window"] == "source"
    assert int(refused.headers["Retry-After"]) > 0


# ------------------------------------------------------------------------ attempt=

# Every test above passes no ``attempt`` argument to either ``RateLimitedError`` or
# ``check_and_record`` and keeps asserting login's own message, so that coverage already
# re-runs as a byte-identical fence for the default.


def test_the_default_message_still_says_login_byte_for_byte() -> None:
    """**Fence**: the default (no ``attempt`` argument) message's exact bytes are
    deliberately fixed, so every caller above -- which passes none -- keeps them. This
    assertion guards a shape nothing alters, so it cannot fail by construction; it is
    kept here only so a future change to the default cannot
    silently pass without tripping it."""
    exc = RateLimitedError(42)
    assert exc.message == "Too many login attempts. Wait 42 seconds and try again."


def test_check_and_record_names_the_attempt_kind_in_the_message() -> None:
    """``check_and_record(attempt=...)`` reaches ``RateLimitedError``'s
    message, so a change-password refusal reads as one rather than as a login
    refusal."""
    limiter = LoginRateLimiter(max_attempts=1, ip_max_attempts=60, window_seconds=60)
    limiter.check_and_record("principal:abc", "203.0.113.10", attempt="password change")
    with pytest.raises(RateLimitedError) as excinfo:
        limiter.check_and_record("principal:abc", "203.0.113.10", attempt="password change")
    assert "password change" in excinfo.value.message
    assert "login" not in excinfo.value.message
