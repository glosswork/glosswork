"""Local password accounts in `standalone` mode (FR-I1).

Argon2id via `argon2-cffi`, never hand-rolled. Three properties are worth more than
the hashing itself and each has its own test: the stored hash never leaves the
service layer, the unknown-email and wrong-password branches are indistinguishable in
both response and work done, and a cost-parameter change upgrades a stored hash
transparently on the owner's next successful login.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from glosswork.config import Settings
from glosswork.db import Database
from glosswork.errors import AuthenticationFailedError, ValidationFailedError
from glosswork.services import ServiceBundle, build_services
from glosswork.services.passwords import PasswordService
from tests.conftest import make_actor

GOOD_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def local_user(services: ServiceBundle) -> str:
    services.principals.create_user(
        make_actor(),
        email="Ada@Example.COM",
        display_name="Ada Lovelace",
        role="member",
        password=GOOD_PASSWORD,
    )
    return "ada@example.com"


# ------------------------------------------------------------------- hashing


def test_the_stored_hash_is_argon2id_with_the_configured_parameters(
    services: ServiceBundle, local_user: str
) -> None:
    principal = services.principals.find_by_email(local_user)
    assert principal is not None and principal.password_hash is not None
    assert principal.password_hash.startswith("$argon2id$v=19$")
    assert "m=65536,t=3,p=4" in principal.password_hash
    assert GOOD_PASSWORD not in principal.password_hash


def test_non_default_cost_parameters_reach_the_hasher() -> None:
    """The three cost parameters are exposed through `Settings` because the memory
    cost is a real working-set consideration in a memory-capped container: each
    concurrent hash holds `GW_ARGON2_MEMORY_KIB` KiB. Exposed and ignored would be
    worse than not exposed at all."""
    service = PasswordService(
        Settings(argon2_time_cost=1, argon2_memory_kib=8192, argon2_parallelism=1)
    )
    assert service.parameters == (1, 8192, 1)
    assert "m=8192,t=1,p=1" in service.hash(GOOD_PASSWORD)


def test_verification_accepts_the_right_password_and_rejects_the_wrong_one(
    services: ServiceBundle, local_user: str
) -> None:
    principal = services.principals.verify_password(local_user, GOOD_PASSWORD)
    assert principal.email == local_user
    with pytest.raises(AuthenticationFailedError):
        services.principals.verify_password(local_user, GOOD_PASSWORD + "!")


def test_email_matching_is_case_insensitive(services: ServiceBundle, local_user: str) -> None:
    assert services.principals.verify_password("ADA@EXAMPLE.com", GOOD_PASSWORD) is not None


# -------------------------------------------------------- no enumeration oracle


def test_unknown_email_and_wrong_password_are_the_same_error(
    services: ServiceBundle, local_user: str
) -> None:
    """Byte-identical, deliberately. Two different messages here would
    turn the login form into a "does this person have an account" lookup for anyone who
    can reach it."""
    with pytest.raises(AuthenticationFailedError) as unknown:
        services.principals.verify_password("nobody@example.com", GOOD_PASSWORD)
    with pytest.raises(AuthenticationFailedError) as wrong:
        services.principals.verify_password(local_user, "not-the-password")
    assert unknown.value.message == wrong.value.message
    assert unknown.value.code == wrong.value.code == "invalid_credentials"
    assert unknown.value.details == wrong.value.details


def test_the_unknown_email_branch_still_performs_a_hash_verification(
    services: ServiceBundle, local_user: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Identical responses are not enough on their own: without a dummy verification
    the unknown-email branch would return in microseconds while a wrong password takes
    ~30 ms, and that difference is the same oracle by another route. Asserted by
    counting the work rather than by timing it, which would be flaky."""
    calls: list[str] = []
    real_verify = services.principals.passwords.verify

    def counting_verify(password_hash: str, password: str) -> bool:
        calls.append(password_hash)
        return real_verify(password_hash, password)

    monkeypatch.setattr(services.principals.passwords, "verify", counting_verify)

    with pytest.raises(AuthenticationFailedError):
        services.principals.verify_password("nobody@example.com", GOOD_PASSWORD)
    unknown_email_verifications = len(calls)

    calls.clear()
    with pytest.raises(AuthenticationFailedError):
        services.principals.verify_password(local_user, "not-the-password")
    wrong_password_verifications = len(calls)

    assert unknown_email_verifications == wrong_password_verifications == 1


def test_an_oidc_principal_cannot_be_logged_into_with_a_password(
    services: ServiceBundle,
) -> None:
    """A principal provisioned from the identity provider has no local password, and
    an attempt to log in as one must not be distinguishable from any other failure."""
    services.principals.create_user(
        make_actor(),
        email="okta-user@example.com",
        display_name="Okta User",
        role="member",
        auth_provider="oidc",
        external_id="sub-1",
    )
    with pytest.raises(AuthenticationFailedError) as excinfo:
        services.principals.verify_password("okta-user@example.com", GOOD_PASSWORD)
    assert "incorrect" in excinfo.value.message


def test_a_deactivated_principal_cannot_log_in(services: ServiceBundle, local_user: str) -> None:
    principal = services.principals.find_by_email(local_user)
    assert principal is not None
    services.principals.deactivate_principal(make_actor(), principal.id)
    with pytest.raises(AuthenticationFailedError):
        services.principals.verify_password(local_user, GOOD_PASSWORD)


# ------------------------------------------------------------- transparent rehash


def test_check_needs_rehash_upgrades_the_stored_hash_on_next_login(
    db: Database, tmp_path: object
) -> None:
    """Lower the cost, hash, raise the cost, verify: the stored hash changes and the
    old password still works. This is the whole reason the PHC string carries its own
    parameters — a cost change needs no migration and no forced reset."""
    from pathlib import Path

    data_dir = Path(str(tmp_path))
    cheap = Settings(
        data_dir=data_dir,
        argon2_time_cost=1,
        argon2_memory_kib=8192,
        argon2_parallelism=1,
        embedding_enabled=False,
    )
    weak_services = build_services(db, data_dir, cheap)
    weak_services.principals.create_user(
        make_actor(),
        email="upgrade@example.com",
        display_name="Upgrade Me",
        role="member",
        password=GOOD_PASSWORD,
    )
    before = weak_services.principals.find_by_email("upgrade@example.com")
    assert before is not None and before.password_hash is not None
    assert "m=8192,t=1,p=1" in before.password_hash

    dear = Settings(
        data_dir=data_dir,
        argon2_time_cost=2,
        argon2_memory_kib=16384,
        argon2_parallelism=2,
        embedding_enabled=False,
    )
    strong_services = build_services(db, data_dir, dear)
    assert strong_services.principals.passwords.needs_rehash(before.password_hash)

    principal = strong_services.principals.verify_password("upgrade@example.com", GOOD_PASSWORD)
    assert principal is not None

    after = strong_services.principals.find_by_email("upgrade@example.com")
    assert after is not None and after.password_hash is not None
    assert after.password_hash != before.password_hash
    assert "m=16384,t=2,p=2" in after.password_hash
    # The old password still works, which is the point: this is an upgrade, not a reset.
    assert strong_services.principals.verify_password("upgrade@example.com", GOOD_PASSWORD)


# ---------------------------------------------------------------- password rules


def test_a_short_password_is_rejected_with_validation_failed(
    services: ServiceBundle,
) -> None:
    """Minimal and stated: a length floor only, no composition rules. FR-S10's
    "min/max and regex validation are out of scope" is about *field* constraints, so
    this is its own small decision rather than implementer taste."""
    with pytest.raises(ValidationFailedError) as excinfo:
        services.principals.create_user(
            make_actor(),
            email="short@example.com",
            display_name="Short",
            role="member",
            password="hunter2",
        )
    assert "at least" in excinfo.value.message
    assert excinfo.value.details["min_length"] == 12


def test_the_length_floor_is_configurable_and_nothing_else_is_checked(
    db: Database, tmp_path: object
) -> None:
    from pathlib import Path

    data_dir = Path(str(tmp_path))
    lax = build_services(
        db, data_dir, Settings(data_dir=data_dir, password_min_length=4, embedding_enabled=False)
    )
    # No composition rules: an all-lowercase, no-digit, no-symbol password is fine.
    assert lax.principals.passwords.hash("abcd").startswith("$argon2id$")
    with pytest.raises(ValidationFailedError):
        lax.principals.passwords.hash("abc")


# ------------------------------------------------------------ no hash ever leaks


def test_no_read_path_exposes_password_hash(
    client: TestClient, app_services: ServiceBundle
) -> None:
    """`password_hash` must not appear in any serializer, envelope, or OpenAPI
    response schema. Checked against the live surface rather than by
    reading `envelopes.py`, so a future route that shapes a principal by hand is
    caught too."""
    created = client.post(
        "/api/v1/principals",
        json={
            "type": "user",
            "email": "leaky@example.com",
            "display_name": "Leaky",
            "password": GOOD_PASSWORD,
        },
    )
    assert created.status_code == 201, created.text
    assert "password_hash" not in created.text
    assert GOOD_PASSWORD not in created.text

    listing = client.get("/api/v1/principals")
    assert listing.status_code == 200
    assert "password_hash" not in listing.text

    one = client.get(f"/api/v1/principals/{created.json()['id']}")
    assert "password_hash" not in one.text

    me = client.get("/api/v1/me")
    assert me.status_code == 200
    assert "password_hash" not in me.text

    spec = client.get("/openapi.json").text
    assert "password_hash" not in spec


def test_password_hash_appears_in_no_audit_row(
    client: TestClient, app_services: ServiceBundle
) -> None:
    created = client.post(
        "/api/v1/principals",
        json={
            "type": "user",
            "email": "audited@example.com",
            "display_name": "Audited",
            "password": GOOD_PASSWORD,
        },
    )
    assert created.status_code == 201
    changed = client.post(
        f"/api/v1/principals/{created.json()['id']}/password",
        json={"password": "another-perfectly-fine-password"},
    )
    assert changed.status_code == 200
    events = client.get("/api/v1/audit-events", params={"limit": 200}).json()
    body = str(events)
    assert "$argon2id$" not in body
    assert GOOD_PASSWORD not in body
    assert "another-perfectly-fine-password" not in body
