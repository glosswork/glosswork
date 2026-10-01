"""Operator CLI for out-of-band identity administration (FR-P3).

The deployment accepts only real personal access tokens (DD-8's ``PatTokenResolver``).
Minting a PAT over HTTP requires a session, and a session needs an account, so there must be
a way to create the very first credential without one: the bootstrap chicken-and-egg. This
module is that way out: ``python -m glosswork.admin create-admin`` makes the first local
account, and ``python -m glosswork.admin mint-token`` mints the first PAT against it, both
with no browser, no session, and no existing credential.

This is not scaffolding made redundant by the login screen. It is the operator story
FR-P3 needs regardless (an administrator locked out of a running deployment, a service
account that needs a token minted for it, an operator resetting a forgotten password)
and the story a deployment handoff needs. It also keeps the identity layer
independently runnable and verifiable: every identity check can be driven from this
file with no browser in the loop.

Every sub-command is a thin argument-parsing shell over the same service methods the
REST routes call (DD-3, AGENTS.md non-negotiable 3) — it parses, calls, and prints,
nothing else. Business rules (password hashing, scope ceilings, uniqueness) live in
``services.principals`` and ``services.tokens``, not here.

Usage::

    python -m glosswork.admin create-admin --email you@example.com --password ...
    python -m glosswork.admin mint-token --name "my laptop" --scope admin
    python -m glosswork.admin set-password --email you@example.com --password ...
    python -m glosswork.admin list-principals
    python -m glosswork.admin set-role --principal you@example.com --role creator
    python -m glosswork.admin grant --type task --principal you@example.com --level admin
    python -m glosswork.admin revoke --type task --principal you@example.com
    python -m glosswork.admin list-grants --type task
    python -m glosswork.admin clear-sign-in-codes --email person@example.com

Delegating an object type to a colleague is **two** steps, not one::

    set-role --principal <email> --role creator
    grant --type <key> --principal <email> --level admin

The grant alone is not enough: exercising ``admin`` on a type needs a credential
carrying ``admin`` scope, which ``role_scope`` and the mint ceiling together restrict to
``creator`` and ``admin`` principals. A ``member`` granted ``admin`` resolves to ``write``
through every credential it is capable of holding.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Callable
from typing import Any

from glosswork.actor import ActorContext, bootstrap_actor
from glosswork.config import ConfigError, Settings, load_settings
from glosswork.db import Database, check_search_extensions
from glosswork.errors import GlossworkError
from glosswork.migrations import run_migrations
from glosswork.services import ServiceBundle, build_services
from glosswork.services.access import VALID_LEVELS
from glosswork.services.principals import VALID_ROLES

# Deliberately not imported from glosswork.app: that module builds a module-level
# `app = create_app(_load_settings_or_exit())` at import time, straight from live
# process environment variables, and can call sys.exit(1) or construct the whole
# FastAPI app (MCP server, routes, frontend static mount) as a side effect of the
# import. The CLI needs to load and validate its own settings and handle its own
# errors, so the database filename is duplicated here rather than pulled in through
# an import with those side effects.
DATABASE_FILENAME = "glosswork.sqlite3"


def _open_services(settings: Settings) -> tuple[Database, ServiceBundle]:
    """Connect the database, run pending migrations, and build the service bundle.

    Mirrors what ``create_app``'s lifespan does on startup (``app.py``), so the CLI
    sees exactly the same schema and service wiring a running server would. The
    caller is responsible for closing the returned ``Database``.
    """
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database.connect(settings.data_dir / DATABASE_FILENAME)
    check_search_extensions(db)
    run_migrations(db)
    # Built with embedding forced off. Every command this CLI has -- create an
    # administrator, mint a token, set a password, list principals -- writes only to
    # ``principals`` and ``access_tokens``; none of them touches indexed content, so
    # none needs a model. Loading a 133 MB ONNX graph to mint a token would be waste
    # in the container and an outright failure in a source checkout that has not run
    # ``scripts/fetch_model.py`` -- and this CLI is the documented bootstrap path for
    # a credential-only deployment (FR-P3), so it must not acquire a model dependency
    # it cannot use. A command that ever writes a record or a comment must revisit
    # this, because with embedding off such a write would enqueue nothing.
    services = build_services(
        db, settings.data_dir, settings.model_copy(update={"embedding_enabled": False})
    )
    return db, services


def _cli_actor() -> ActorContext:
    """The actor attributed to every write this CLI makes.

    The CLI has no logged-in principal — it runs out of band, before any session or
    PAT exists to attribute a request to — so the seeded bootstrap principal (DD-4)
    is the correct attribution for an out-of-band operator action, exactly as it is
    for every other pre-authentication write path in this codebase.
    """
    return bootstrap_actor(str(uuid.uuid4()), surface="api")


# --------------------------------------------------------------------- sub-commands


def _cmd_create_admin(args: argparse.Namespace, settings: Settings) -> int:
    email = args.email or settings.bootstrap_admin_email
    password = args.password or settings.bootstrap_admin_password
    if not email:
        print(
            "Error: --email is required (or set GW_BOOTSTRAP_ADMIN_EMAIL).",
            file=sys.stderr,
        )
        return 1
    if not password:
        print(
            "Error: --password is required (or set GW_BOOTSTRAP_ADMIN_PASSWORD).",
            file=sys.stderr,
        )
        return 1
    display_name = args.display_name or email.split("@", 1)[0]

    db, services = _open_services(settings)
    try:
        existing = services.principals.find_by_email(email)
        if existing is not None:
            print(f"Admin principal already exists: {existing.id} <{existing.email}>")
            return 0
        principal = services.principals.create_user(
            _cli_actor(),
            email=email,
            display_name=display_name,
            role="admin",
            auth_provider="local",
            password=password,
        )
        print(f"Created admin principal: {principal.id} <{principal.email}>")
        return 0
    finally:
        db.close()


def _cmd_mint_token(args: argparse.Namespace, settings: Settings) -> int:
    db, services = _open_services(settings)
    try:
        principal_id = args.principal_id
        if principal_id is None:
            admins = [
                p
                for p in services.principals.list_principals("user", include_inactive=False)
                if p.role == "admin"
            ]
            if not admins:
                print(
                    "Error: no active admin user exists. Run 'create-admin' first.",
                    file=sys.stderr,
                )
                return 1
            principal_id = admins[0].id

        minted = services.tokens.mint(
            _cli_actor(),
            name=args.name,
            scope=args.scope,
            principal_id=principal_id,
            expires_at=args.expires_at,
            agent_label=args.agent_label,
        )
        if args.quiet:
            # A shell captures this with $(...) (web/playwright.config.ts): stdout
            # must carry the plaintext and nothing else.
            print(minted.plaintext)
            return 0
        print(f"Token id: {minted.row.id}")
        print(f"Prefix:   {minted.row.token_prefix}")
        print(f"Scope:    {minted.row.scope}")
        print()
        print("This plaintext token is shown exactly once and cannot be recovered later.")
        print("Store it now:")
        print(minted.plaintext)
        return 0
    finally:
        db.close()


def _cmd_set_password(args: argparse.Namespace, settings: Settings) -> int:
    db, services = _open_services(settings)
    try:
        principal = services.principals.find_by_email(args.email)
        if principal is None:
            print(f"Error: no principal found for email {args.email!r}.", file=sys.stderr)
            return 1
        services.principals.set_password(_cli_actor(), principal.id, args.password)
        print(f"Password updated for {principal.id} <{principal.email}>")
        return 0
    finally:
        db.close()


def _cmd_list_principals(args: argparse.Namespace, settings: Settings) -> int:
    del args  # no options; kept for a uniform dispatch signature
    db, services = _open_services(settings)
    try:
        for principal in services.principals.list_principals():
            identity = principal.email or principal.display_name
            active = "active" if principal.is_active else "inactive"
            print(
                f"{principal.id}  {principal.type:15}  {principal.role:6}  {active:8}  {identity}"
            )
        return 0
    finally:
        db.close()


def _cmd_set_role(args: argparse.Namespace, settings: Settings) -> int:
    db, services = _open_services(settings)
    try:
        principal = _resolve_principal(services, args.principal)
        if principal is None:
            print(f"Error: no principal found for {args.principal!r}.", file=sys.stderr)
            return 1
        updated = services.principals.update_principal(_cli_actor(), principal.id, role=args.role)
        print(f"Role for {updated.id} <{updated.email or updated.display_name}>: {updated.role}")
        if updated.role == "creator":
            print(
                "A creator may define its own object types. To hand it one that already "
                "exists, follow this with:\n"
                f"  grant --type <key> --principal {args.principal} --level admin"
            )
        if updated.auth_provider == "oidc":
            # A role set here is overwritten at the
            # principal's next sign-in by whatever GW_OIDC_ADMIN_GROUPS and
            # GW_OIDC_CREATOR_GROUPS assert about them, because the provider is
            # authoritative on every login (FR-I2) -- that is the property that makes
            # removing someone from a group actually demote them. So
            # the durable way to set a role on an OIDC principal is a group, and this
            # says so at the moment the operator would otherwise be surprised later.
            print(
                f"Warning: {updated.email or updated.display_name} signs in through your "
                "identity provider, which re-derives their role from group membership on "
                "every login. This change lasts only until their next sign-in unless "
                f"their groups already map to {updated.role!r}. To make it durable, add "
                "them to a group named in GW_OIDC_ADMIN_GROUPS or GW_OIDC_CREATOR_GROUPS.",
                file=sys.stderr,
            )
        return 0
    finally:
        db.close()


def _cmd_grant(args: argparse.Namespace, settings: Settings) -> int:
    db, services = _open_services(settings)
    try:
        principal = _resolve_principal(services, args.principal)
        if principal is None:
            print(f"Error: no principal found for {args.principal!r}.", file=sys.stderr)
            return 1
        row = services.access.grant(_cli_actor(), args.type, principal.id, args.level)
        print(f"{args.type}: {principal.email or principal.display_name} -> {row.level}")
        if args.level == "admin" and principal.role not in ("admin", "creator"):
            # Stated where it bites rather than left to be discovered: the ceiling
            # means a `member` cannot exercise an `admin` grant through any credential.
            print(
                f"Warning: {principal.role!r} principals cannot exercise an 'admin' grant. "
                f"Run 'set-role --principal {args.principal} --role creator' as well.",
                file=sys.stderr,
            )
        return 0
    finally:
        db.close()


def _cmd_revoke(args: argparse.Namespace, settings: Settings) -> int:
    db, services = _open_services(settings)
    try:
        principal = _resolve_principal(services, args.principal)
        if principal is None:
            print(f"Error: no principal found for {args.principal!r}.", file=sys.stderr)
            return 1
        services.access.revoke(_cli_actor(), args.type, principal.id)
        print(
            f"{args.type}: revoked {principal.email or principal.display_name}; "
            "the type's default_level now applies."
        )
        return 0
    finally:
        db.close()


def _cmd_list_grants(args: argparse.Namespace, settings: Settings) -> int:
    db, services = _open_services(settings)
    try:
        object_type, _ = services.schema.get_object_type(_cli_actor(), args.type)
        print(f"{args.type}: default_level = {object_type.default_level}")
        for grant in services.access.list_grants(_cli_actor(), args.type):
            principal = services.principals.get_principal_or_none(grant.principal_id)
            who = principal.email or principal.display_name if principal else grant.principal_id
            print(f"  {grant.level:6}  {who}")
        return 0
    finally:
        db.close()


def _resolve_principal(services: ServiceBundle, ref: str) -> Any:
    """A principal by email or by id. Both are accepted everywhere the CLI names one,
    because an operator reading a grant listing has an email and an operator reading an
    audit row has an id."""
    by_email = services.principals.find_by_email(ref) if "@" in ref else None
    return by_email if by_email is not None else services.principals.get_principal_or_none(ref)


_Command = Callable[[argparse.Namespace, Settings], int]


def _cmd_clear_sign_in_codes(args: argparse.Namespace, settings: Settings) -> int:
    """The recovery for a person locked out of sign-in by emailed code (change 9, DQ4):
    someone spent their address's hourly or daily allowance. Deleting the address's code
    rows resets its count; nothing else is touched."""
    db, services = _open_services(settings)
    try:
        deleted = services.sign_in_codes.clear_codes(args.email)
        print(f"Deleted {deleted} sign-in code rows for {args.email.strip().lower()}")
        return 0
    finally:
        db.close()


_COMMANDS: dict[str, _Command] = {
    "create-admin": _cmd_create_admin,
    "mint-token": _cmd_mint_token,
    "set-password": _cmd_set_password,
    "list-principals": _cmd_list_principals,
    "set-role": _cmd_set_role,
    "grant": _cmd_grant,
    "revoke": _cmd_revoke,
    "list-grants": _cmd_list_grants,
    "clear-sign-in-codes": _cmd_clear_sign_in_codes,
}


# ---------------------------------------------------------------------------- parser


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m glosswork.admin",
        description="Operator CLI for out-of-band identity administration (FR-P3).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_admin = subparsers.add_parser(
        "create-admin",
        help="Create the local-account bootstrap administrator (idempotent).",
    )
    create_admin.add_argument("--email", default=None, help="Defaults to GW_BOOTSTRAP_ADMIN_EMAIL.")
    create_admin.add_argument(
        "--password", default=None, help="Defaults to GW_BOOTSTRAP_ADMIN_PASSWORD."
    )
    create_admin.add_argument(
        "--display-name", default=None, help="Defaults to the local part of the email."
    )

    mint_token = subparsers.add_parser("mint-token", help="Mint a personal access token.")
    mint_token.add_argument("--name", required=True, help="Names where the token will be used.")
    mint_token.add_argument("--scope", default="admin", choices=["read", "write", "admin"])
    mint_token.add_argument(
        "--principal-id",
        default=None,
        help="Defaults to the first active admin user.",
    )
    mint_token.add_argument(
        "--expires-at",
        default=None,
        help="ISO-8601 UTC, e.g. 2027-01-31T00:00:00Z. Defaults to never.",
    )
    mint_token.add_argument(
        "--agent-label",
        default=None,
        help=(
            "The agent label this token is minted for, so calls it makes are attributed "
            "without an X-Agent-Label header. Descriptive only; it grants nothing."
        ),
    )
    mint_token.add_argument(
        "--quiet",
        action="store_true",
        help="Print only the plaintext token, for capture with $(...).",
    )

    set_password = subparsers.add_parser(
        "set-password",
        help=(
            "Set a local account's password. Revokes every session and personal access "
            "token that account holds."
        ),
    )
    set_password.add_argument("--email", required=True)
    set_password.add_argument("--password", required=True)

    subparsers.add_parser("list-principals", help="List every principal (no secrets).")

    set_role = subparsers.add_parser(
        "set-role",
        help="Set a principal's system role (member | creator | admin).",
    )
    set_role.add_argument("--principal", required=True, help="Email or principal id.")
    set_role.add_argument("--role", required=True, choices=sorted(VALID_ROLES))

    grant = subparsers.add_parser(
        "grant",
        help="Grant a principal access to one object type.",
        description=(
            "Grant a principal access to one object type. Delegating a type is two "
            "steps: 'set-role --role creator' first, then 'grant --level admin'. A "
            "'member' granted 'admin' cannot exercise it, because exercising 'admin' on "
            "a type needs a credential carrying 'admin' scope and only creator and admin "
            "principals may hold one."
        ),
    )
    grant.add_argument("--type", required=True, help="Object type key.")
    grant.add_argument("--principal", required=True, help="Email or principal id.")
    grant.add_argument(
        "--level",
        required=True,
        choices=list(VALID_LEVELS),
        help="'none' is an explicit deny overriding the type's default_level.",
    )

    revoke = subparsers.add_parser(
        "revoke",
        help="Remove a principal's grant, returning it to the type's default_level.",
    )
    revoke.add_argument("--type", required=True, help="Object type key.")
    revoke.add_argument("--principal", required=True, help="Email or principal id.")

    list_grants = subparsers.add_parser(
        "list-grants", help="List every grant on one object type, and its default."
    )
    list_grants.add_argument("--type", required=True, help="Object type key.")

    clear_codes = subparsers.add_parser(
        "clear-sign-in-codes",
        help=(
            "Delete an address's emailed sign-in codes, which resets how many it may be "
            "sent. The recovery for a person locked out by someone asking for codes."
        ),
    )
    clear_codes.add_argument("--email", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    """The testable entry point. Never calls ``sys.exit``; returns a process exit
    code instead (0 on success, 1 for a handled configuration or domain error)."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    command = _COMMANDS[args.command]
    try:
        return command(args, settings)
    except GlossworkError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
