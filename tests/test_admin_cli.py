"""The operator CLI (FR-P3, `python -m glosswork.admin`).

The bootstrap chicken-and-egg is solved here rather than by a temporary auth path:
the deployment accepts only real PATs, minting one over HTTP needs a session, and a
session needs a credential -- so the first credential is created out of band. Tested
by calling the CLI entry point, because "an operator can actually get in" is the
property, not "the functions exist".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID, bootstrap_actor
from glosswork.admin import main
from glosswork.auth import PatTokenResolver
from glosswork.config import Settings
from glosswork.db import Database
from glosswork.migrations import run_migrations
from glosswork.services import ServiceBundle, build_services
from glosswork.services.tokens import TOKEN_PREFIX, hash_token

PASSWORD = "correct-horse-battery-staple"
DATABASE_FILENAME = "glosswork.sqlite3"


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "data"
    directory.mkdir()
    monkeypatch.setenv("GW_DATA_DIR", str(directory))
    for leftover in ("GW_BOOTSTRAP_ADMIN_EMAIL", "GW_BOOTSTRAP_ADMIN_PASSWORD"):
        monkeypatch.delenv(leftover, raising=False)
    return directory


def open_services(data_dir: Path) -> tuple[Database, ServiceBundle]:
    db = Database.connect(data_dir / DATABASE_FILENAME)
    run_migrations(db)
    return db, build_services(db, data_dir, Settings(data_dir=data_dir, embedding_enabled=False))


# ---------------------------------------------------------------- create-admin


def test_create_admin_creates_a_local_admin_account(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["create-admin", "--email", "ops@example.com", "--password", PASSWORD]) == 0
    out = capsys.readouterr().out
    assert "ops@example.com" in out
    assert PASSWORD not in out  # never printed, at any verbosity

    db, services = open_services(data_dir)
    try:
        principal = services.principals.find_by_email("ops@example.com")
        assert principal is not None
        assert principal.role == "admin"
        assert principal.type == "user"
        assert principal.auth_provider == "local"
        assert principal.password_hash is not None
        assert principal.password_hash.startswith("$argon2id$")
        # The credential actually works, which is the whole point of the command.
        assert services.principals.verify_password("ops@example.com", PASSWORD)
    finally:
        db.close()


def test_create_admin_is_idempotent(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Re-running is a no-op, not an error. That is what makes the bootstrap
    environment variables safe to leave set in a compose file."""
    assert main(["create-admin", "--email", "ops@example.com", "--password", PASSWORD]) == 0
    capsys.readouterr()
    assert main(["create-admin", "--email", "ops@example.com", "--password", PASSWORD]) == 0
    assert "already exists" in capsys.readouterr().out


def test_create_admin_reads_the_bootstrap_environment_variables(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GW_BOOTSTRAP_ADMIN_EMAIL", "env@example.com")
    monkeypatch.setenv("GW_BOOTSTRAP_ADMIN_PASSWORD", PASSWORD)
    assert main(["create-admin"]) == 0
    db, services = open_services(data_dir)
    try:
        assert services.principals.find_by_email("env@example.com") is not None
    finally:
        db.close()


def test_create_admin_names_both_the_option_and_the_variable_when_incomplete(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An operator running this is, by definition, locked out. A message that names
    only one of the two ways to supply the value sends them looking."""
    assert main(["create-admin", "--email", "ops@example.com"]) == 1
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "--password" in combined
    assert "GW_BOOTSTRAP_ADMIN_PASSWORD" in combined


def test_create_admin_rejects_a_password_below_the_length_floor(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI is a shell over the service layer, so the password policy applies here
    too rather than being a rule the web surface enforces and the CLI bypasses."""
    assert main(["create-admin", "--email", "ops@example.com", "--password", "short"]) == 1
    captured = capsys.readouterr()
    assert "at least" in (captured.out + captured.err)


# ------------------------------------------------------------------ mint-token


def test_mint_token_quiet_prints_exactly_one_token_and_nothing_else(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--quiet` is not a convenience: `web/playwright.config.ts` captures this with
    `$(...)`, so any extra line on stdout breaks the e2e suite's credential."""
    assert main(["create-admin", "--email", "ops@example.com", "--password", PASSWORD]) == 0
    capsys.readouterr()
    assert main(["mint-token", "--name", "ci", "--scope", "admin", "--quiet"]) == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 1, out
    token = lines[0].strip()
    assert token.startswith(TOKEN_PREFIX)
    assert len(token) == len(TOKEN_PREFIX) + 32

    db, services = open_services(data_dir)
    try:
        stored = services.tokens.list_tokens(
            bootstrap_actor("cli-token-listing"),
            services.principals.find_by_email("ops@example.com").id,
        )
        assert [row.token_hash for row in stored] == [hash_token(token)]
        assert token not in str(stored)
    finally:
        db.close()


def test_mint_token_stores_the_agent_label_and_adds_no_line_of_output(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--agent-label` adds an argument, never a line. `--quiet` is captured
    with `$(...)` by `web/playwright.config.ts`, so stdout stays exactly one line."""
    assert main(["create-admin", "--email", "ops@example.com", "--password", PASSWORD]) == 0
    capsys.readouterr()
    exit_code = main(
        [
            "mint-token",
            "--name",
            "ci",
            "--scope",
            "admin",
            "--agent-label",
            "claude-code",
            "--quiet",
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 1, out
    assert lines[0].strip().startswith(TOKEN_PREFIX)

    db, services = open_services(data_dir)
    try:
        principal = services.principals.find_by_email("ops@example.com")
        assert principal is not None
        stored = services.tokens.list_tokens(bootstrap_actor("cli-agent-label"), principal.id)
        assert [row.agent_label for row in stored] == ["claude-code"]
    finally:
        db.close()


def test_mint_token_verbose_says_the_plaintext_is_shown_once(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["create-admin", "--email", "ops@example.com", "--password", PASSWORD]) == 0
    capsys.readouterr()
    assert main(["mint-token", "--name", "my laptop", "--scope", "read"]) == 0
    out = capsys.readouterr().out
    assert TOKEN_PREFIX in out
    assert "once" in out.lower()


def test_mint_token_can_target_the_seeded_bootstrap_principal(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The path `web/playwright.config.ts` takes: no admin *user* need exist, because
    migration 1's bootstrap principal is already an active `admin` service account, and
    minting for it keeps the e2e run's attributed principal the bootstrap principal."""
    assert (
        main(
            [
                "mint-token",
                "--name",
                "playwright-e2e",
                "--scope",
                "admin",
                "--principal-id",
                BOOTSTRAP_PRINCIPAL_ID,
                "--quiet",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.strip().startswith(TOKEN_PREFIX)


def test_mint_token_with_no_admin_tells_the_operator_to_create_one(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["mint-token", "--name", "orphan", "--scope", "admin"]) == 1
    captured = capsys.readouterr()
    assert "create-admin" in (captured.out + captured.err)


def test_a_minted_token_resolves_through_the_production_resolver(data_dir: Path) -> None:
    """End to end, and the property that actually matters: what the CLI prints is what
    `PatTokenResolver` accepts. A token that mints but does not resolve would be a
    bootstrap path that does not bootstrap."""
    assert main(["create-admin", "--email", "ops@example.com", "--password", PASSWORD]) == 0
    db, services = open_services(data_dir)
    try:
        minted = services.tokens.mint(bootstrap_actor("req"), name="cli", scope="write")
        identity = PatTokenResolver(lambda: services).resolve(f"Bearer {minted.plaintext}")
        assert identity.scope == "write"
    finally:
        db.close()


# ---------------------------------------------------- set-password and listing


def test_set_password_changes_the_stored_hash(data_dir: Path) -> None:
    assert main(["create-admin", "--email", "ops@example.com", "--password", PASSWORD]) == 0
    db, services = open_services(data_dir)
    try:
        before = services.principals.find_by_email("ops@example.com").password_hash
    finally:
        db.close()

    assert (
        main(["set-password", "--email", "ops@example.com", "--password", "a-new-password!"]) == 0
    )

    db, services = open_services(data_dir)
    try:
        after = services.principals.find_by_email("ops@example.com")
        assert after is not None and after.password_hash != before
        assert services.principals.verify_password("ops@example.com", "a-new-password!")
    finally:
        db.close()


def test_set_password_for_an_unknown_email_fails_cleanly(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["set-password", "--email", "ghost@example.com", "--password", PASSWORD]) == 1
    captured = capsys.readouterr()
    assert "ghost@example.com" in (captured.out + captured.err)


def test_list_principals_prints_no_secrets(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["create-admin", "--email", "ops@example.com", "--password", PASSWORD]) == 0
    capsys.readouterr()
    assert main(["mint-token", "--name", "t", "--scope", "read", "--quiet"]) == 0
    minted = capsys.readouterr().out.strip()
    assert main(["list-principals"]) == 0
    out = capsys.readouterr().out
    assert "ops@example.com" in out
    assert "$argon2id$" not in out
    assert PASSWORD not in out
    assert minted not in out


# ------------------------------------------------------------------- discipline


def test_no_traceback_ever_reaches_the_operator(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A domain error is a message and an exit code, not a stack trace. Operators run
    this while locked out of a running system; a traceback is the worst possible
    response."""
    assert main(["mint-token", "--name", "x", "--scope", "admin"]) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in (captured.out + captured.err)


def test_main_returns_a_code_rather_than_exiting(data_dir: Path) -> None:
    """`main` is the testable entry point: it never calls `sys.exit` itself, so the
    suite can call it directly instead of driving a subprocess."""
    assert isinstance(main(["list-principals"]), int)


# ------------------------------------------- set-role, grant, revoke, list-grants


def _seed_type_and_member(data_dir: Path) -> str:
    """One object type and one `member` principal, through the service layer."""
    db, services = open_services(data_dir)
    try:
        services.schema.create_object_type(
            bootstrap_actor("cli-seed"),
            key="initiative",
            name="Initiative",
            name_plural="Initiatives",
            description="A funded workstream, seeded so the grant CLI has a type to name.",
            key_prefix="INI",
        )
        return services.principals.create_user(
            bootstrap_actor("cli-seed"),
            email="them@example.com",
            display_name="Them",
            role="member",
            password=PASSWORD,
        ).id
    finally:
        db.close()


def test_set_role_promotes_and_says_what_is_still_needed(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Delegating a type is two steps, and the CLI says so where it bites
    rather than leaving it to be discovered."""
    assert main(["create-admin", "--email", "ops@example.com", "--password", PASSWORD]) == 0
    _seed_type_and_member(data_dir)

    assert main(["set-role", "--principal", "them@example.com", "--role", "creator"]) == 0
    out = capsys.readouterr().out
    assert "creator" in out
    assert "grant --type" in out

    db, services = open_services(data_dir)
    try:
        assert services.principals.find_by_email("them@example.com").role == "creator"
    finally:
        db.close()


def test_grant_revoke_and_list_grants_round_trip(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["create-admin", "--email", "ops@example.com", "--password", PASSWORD]) == 0
    principal_id = _seed_type_and_member(data_dir)
    capsys.readouterr()

    # Both an email and an id are accepted wherever the CLI names a principal.
    assert (
        main(
            ["grant", "--type", "initiative", "--principal", "them@example.com", "--level", "read"]
        )
        == 0
    )
    assert main(["list-grants", "--type", "initiative"]) == 0
    listed = capsys.readouterr().out
    assert "default_level = none" in listed
    assert "read" in listed and "them@example.com" in listed

    assert (
        main(["grant", "--type", "initiative", "--principal", principal_id, "--level", "write"])
        == 0
    )
    capsys.readouterr()
    assert main(["revoke", "--type", "initiative", "--principal", "them@example.com"]) == 0
    assert "default_level now applies" in capsys.readouterr().out

    assert main(["list-grants", "--type", "initiative"]) == 0
    assert "them@example.com" not in capsys.readouterr().out


def test_granting_admin_to_a_member_warns_that_it_cannot_be_exercised(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same two steps: a `member` granted `admin` resolves to `write` through every credential
    it can hold, so the CLI says so rather than letting an operator believe otherwise."""
    assert main(["create-admin", "--email", "ops@example.com", "--password", PASSWORD]) == 0
    _seed_type_and_member(data_dir)
    capsys.readouterr()

    assert (
        main(
            ["grant", "--type", "initiative", "--principal", "them@example.com", "--level", "admin"]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert "set-role" in captured.err
    assert "creator" in captured.err


def test_an_unknown_principal_is_an_error_not_a_silent_no_op(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["create-admin", "--email", "ops@example.com", "--password", PASSWORD]) == 0
    _seed_type_and_member(data_dir)
    capsys.readouterr()
    assert (
        main(
            [
                "grant",
                "--type",
                "initiative",
                "--principal",
                "nobody@example.com",
                "--level",
                "read",
            ]
        )
        == 1
    )
    assert "no principal found" in capsys.readouterr().err.lower()


# ------------------------------- the set-role warning for an OIDC principal


def test_set_role_warns_that_an_oidc_principals_role_will_be_re_derived(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`GW_OIDC_CREATOR_GROUPS` makes a durable
    `creator` possible; it cannot stop an operator setting a role the provider will not
    assert. The warning removes the *silence*, which was the worst property of the
    defect -- the role still reverts, but nobody is surprised by it later."""
    assert main(["create-admin", "--email", "ops@example.com", "--password", PASSWORD]) == 0
    db, services = open_services(data_dir)
    try:
        services.principals.create_user(
            bootstrap_actor("cli-seed"),
            email="sso@example.com",
            display_name="SSO User",
            role="member",
            auth_provider="oidc",
            external_id="00u1abcdef",
        )
    finally:
        db.close()
    capsys.readouterr()

    assert main(["set-role", "--principal", "sso@example.com", "--role", "creator"]) == 0
    captured = capsys.readouterr()
    assert "creator" in captured.out  # the role change itself still succeeds
    assert "identity provider" in captured.err
    assert "GW_OIDC_CREATOR_GROUPS" in captured.err


def test_set_role_does_not_warn_for_a_local_principal(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**Scope fence.** A local account's role is durable, so warning about it would be
    noise that teaches an operator to ignore the warning that matters."""
    assert main(["create-admin", "--email", "ops@example.com", "--password", PASSWORD]) == 0
    db, services = open_services(data_dir)
    try:
        services.principals.create_user(
            bootstrap_actor("cli-seed"),
            email="local@example.com",
            display_name="Local User",
            role="member",
            password=PASSWORD,
        )
    finally:
        db.close()
    capsys.readouterr()

    assert main(["set-role", "--principal", "local@example.com", "--role", "creator"]) == 0
    captured = capsys.readouterr()
    assert "creator" in captured.out
    assert "identity provider" not in captured.err
