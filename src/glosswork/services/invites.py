"""Invites (change 9, FR-I19): an administrator invites a person by email and role.

An invite is its own list (DQ2), not a person waiting to be activated: no principal exists
until the invited address proves itself with an emailed code, so a mistyped address never
appears in the people directory, a ``user_ref`` picker or the count of people. The first
code sign-in for the address creates the person, or reactivates a removed one, with the
invite's role (``SignInCodeService.verify_code`` calls :meth:`InviteService.accept_in_txn`).

**Live** is narrower than open. An invite is live only while it is neither accepted nor
revoked, is younger than :data:`INVITE_LIFETIME`, and the administrator who sent it is
still an active principal whose role is ``admin`` (plan F8). All of it is checked inside
the verifying transaction, so an administrator removed or demoted leaves no invite that
still makes people, administrators included.

The invite email goes through the relay synchronously, and the invite stands whatever
the relay answers: the person can always go to the sign-in page and ask for a code.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import Connection

from glosswork.actor import ActorContext
from glosswork.db import Database
from glosswork.errors import (
    ConflictError,
    FeatureDisabledError,
    NotFoundError,
    ValidationFailedError,
)
from glosswork.repositories.interfaces import (
    AuditRepository,
    InviteRepository,
    PrincipalRepository,
)
from glosswork.repositories.models import InviteRow, PrincipalRow
from glosswork.services.base import make_event
from glosswork.services.principals import VALID_ROLES, PrincipalService
from glosswork.services.relay import (
    RelayMessage,
    RelayResult,
    RelaySender,
    is_deliverable_address,
    normalize_address,
)
from glosswork.timeutil import format_datetime, parse_datetime, utc_now

INVITE_LIFETIME = timedelta(days=14)

#: The note on the audit rows an acceptance writes, and on the principal it creates.
ACCEPTED_SOURCE = "invite accepted"

_SAVED = "The invite is saved, but"
_STILL = "The person can still sign in by asking for a code on the sign-in page."
#: What an administrator reads after inviting, by relay outcome (docs/DEPLOYMENT.md 5a).
OUTCOME_MESSAGES: dict[str, str] = {
    "accepted": "Invite sent.",
    "refused_credential": f"{_SAVED} the email could not be sent. {_STILL}",
    "refused_fields": f"{_SAVED} the email could not be sent. {_STILL}",
    "rate_limited": f"{_SAVED} the email service is busy. Try again later. {_STILL}",
    "unavailable": f"{_SAVED} the email could not be sent. {_STILL}",
}
DISPLAY_NAME_HINT = (
    "Your display name may be the reason: the email service refuses a name that reads "
    "like a web address, such as one with a dot in it."
)


def invite_expires_at(invite: InviteRow) -> str:
    return format_datetime(parse_datetime(invite.created_at) + INVITE_LIFETIME)


def outcome_message(result: RelayResult) -> str:
    message = OUTCOME_MESSAGES[result.outcome]
    if result.outcome == "refused_fields" and "inviter_name" in result.refused_fields:
        message = f"{message} {DISPLAY_NAME_HINT}"
    return message


class InviteService:
    def __init__(
        self,
        db: Database,
        invite_repo: InviteRepository,
        principal_repo: PrincipalRepository,
        principals: PrincipalService,
        audit_repo: AuditRepository,
        relay: RelaySender | None,
    ) -> None:
        self._db = db
        self._invites = invite_repo
        self._principals = principal_repo
        self._principal_service = principals
        self._audit = audit_repo
        self._relay = relay

    def require_enabled(self) -> None:
        if self._relay is None:
            raise FeatureDisabledError(
                "Invites by email need sign-in by emailed code, which is not turned on for "
                "this workspace. Add a person from People & agents instead.",
                feature="email_code_sign_in",
                setting="GW_RELAY_URL",
            )

    # -------------------------------------------------------------------- reads

    def list_live(self, now: datetime | None = None) -> list[InviteRow]:
        """The live invites, newest first."""
        self.require_enabled()
        moment = now or utc_now()
        with self._db.read() as conn:
            return [
                row for row in self._invites.list_open(conn) if self._is_live(conn, row, moment)
            ]

    def live_invite(self, conn: Connection, address: str, now: datetime) -> InviteRow | None:
        """The live invite for a normalized address, if any, on the caller's connection."""
        row = self._invites.get_open_by_email(conn, address)
        if row is None or not self._is_live(conn, row, now):
            return None
        return row

    # ------------------------------------------------------------------- writes

    def create_invite(
        self,
        actor: ActorContext,
        *,
        email: str,
        display_name: str,
        role: str,
        now: datetime | None = None,
    ) -> tuple[InviteRow, RelayResult]:
        """Write an invite and send its email. Refuses an address that is not one
        deliverable address, one that already has an active account here, and one with a
        live invite. A removed person's address may be invited: that is how they come
        back. An open invite that is no longer live is revoked first, so it cannot block
        the address."""
        self.require_enabled()
        address = normalize_address(email or "")
        if not is_deliverable_address(address):
            raise ValidationFailedError(
                "email must be one email address, such as lin@example.com: at most 254 "
                'characters, one @ with text on both sides, and no spaces or any of , ; < > ".',
                field_key="email",
            )
        if role not in VALID_ROLES:
            raise ValidationFailedError(
                f"Unknown role {role!r}. Valid roles: {', '.join(sorted(VALID_ROLES))}.",
                field_key="role",
            )
        name = (display_name or "").strip()
        if not name:
            raise ValidationFailedError(
                "display_name must be a non-empty string.", field_key="display_name"
            )
        moment = now or utc_now()
        ts = format_datetime(moment)
        with self._db.write() as conn:
            existing = self._principals.get_by_email(conn, address)
            if existing is not None and existing.is_active:
                raise ConflictError(f"{address} already has an account here.", email=address)
            if existing is not None and (
                existing.type != "user" or existing.auth_provider != "local"
            ):
                # A removed person from an identity provider comes back through that
                # provider, not by code: acceptance would refuse them, so an invite here
                # would send an email and codes that can never work.
                raise ConflictError(
                    f"{address} belongs to an account that signs in through an identity "
                    "provider, so it cannot be invited by email.",
                    email=address,
                )
            open_invite = self._invites.get_open_by_email(conn, address)
            if open_invite is not None:
                if self._is_live(conn, open_invite, moment):
                    raise ConflictError(
                        f"{address} already has a pending invite. Revoke it to send a new one.",
                        email=address,
                        invite_id=open_invite.id,
                    )
                self._revoke_in_txn(conn, actor, open_invite, ts, note="superseded")
            inviter = self._principals.get(conn, actor.principal_id)
            if inviter is None:  # pragma: no cover - the edge resolved this principal
                raise NotFoundError("principal", actor.principal_id)
            row = InviteRow(
                id=str(uuid.uuid4()),
                email=address,
                display_name=name,
                role=role,
                invited_by=actor.principal_id,
                created_at=ts,
                accepted_at=None,
                principal_id=None,
                revoked_at=None,
                revoked_by=None,
            )
            self._invites.insert(conn, row)
            self._audit.append(
                conn,
                [
                    make_event(
                        actor,
                        ts,
                        entity_type="invite",
                        entity_id=row.id,
                        action="create",
                        note=f"role={role}",
                    )
                ],
            )
        assert self._relay is not None  # require_enabled above
        result = self._relay.send(RelayMessage.invite(address, inviter.display_name))
        return row, result

    def revoke_invite(
        self, actor: ActorContext, invite_id: str, now: datetime | None = None
    ) -> InviteRow:
        self.require_enabled()
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            row = self._invites.get(conn, invite_id)
            if row is None:
                raise NotFoundError("invite", invite_id)
            if row.accepted_at is not None or row.revoked_at is not None:
                state = "accepted" if row.accepted_at is not None else "revoked"
                raise ConflictError(
                    f"This invite was already {state}, so there is nothing to revoke.",
                    invite_id=invite_id,
                )
            self._revoke_in_txn(conn, actor, row, ts, note=None)
            refreshed = self._invites.get(conn, invite_id)
        assert refreshed is not None
        return refreshed

    def accept_in_txn(
        self,
        conn: Connection,
        actor: ActorContext,
        address: str,
        existing: PrincipalRow | None,
        now: datetime,
    ) -> PrincipalRow | None:
        """Accept the live invite for ``address`` inside the verifying transaction, or
        return ``None`` when there is none to accept.

        No principal with the address: create one, ``local`` with no password, the
        invite's display name and role, made by the inviting administrator (plan F20). A
        removed local person with the address: reactivate that same principal with the
        invite's role. Anyone else with the address (an active or removed person from an
        identity provider) is not signed in by code, and the invite stays open."""
        invite = self.live_invite(conn, address, now)
        if invite is None:
            return None
        ts = format_datetime(now)
        principal: PrincipalRow
        if existing is None:
            prepared = self._principal_service.prepare_user(
                actor,
                email=address,
                display_name=invite.display_name,
                role=invite.role,
                auth_provider="local",
                now=now,
            )
            prepared.created_by = invite.invited_by
            principal = self._principal_service.create_user_in_txn(
                conn, actor, prepared, source=ACCEPTED_SOURCE
            )
        elif (
            not existing.is_active and existing.type == "user" and existing.auth_provider == "local"
        ):
            principal = self._principal_service.reactivate_in_txn(
                conn, actor, existing, role=invite.role, ts=ts, source=ACCEPTED_SOURCE
            )
        else:
            return None
        self._invites.update_row(conn, invite.id, {"accepted_at": ts, "principal_id": principal.id})
        self._audit.append(
            conn,
            [
                make_event(
                    actor,
                    ts,
                    entity_type="invite",
                    entity_id=invite.id,
                    action="update",
                    field_key="accepted_at",
                    old_value=None,
                    new_value=ts,
                    note=ACCEPTED_SOURCE,
                )
            ],
        )
        return principal

    # ------------------------------------------------------------------ helpers

    def _is_live(self, conn: Connection, row: InviteRow, now: datetime) -> bool:
        if row.accepted_at is not None or row.revoked_at is not None:
            return False
        if parse_datetime(row.created_at) + INVITE_LIFETIME <= now:
            return False
        inviter = self._principals.get(conn, row.invited_by)
        return inviter is not None and inviter.is_active and inviter.role == "admin"

    def _revoke_in_txn(
        self,
        conn: Connection,
        actor: ActorContext,
        row: InviteRow,
        ts: str,
        note: str | None,
    ) -> None:
        self._invites.update_row(conn, row.id, {"revoked_at": ts, "revoked_by": actor.principal_id})
        self._audit.append(
            conn,
            [
                make_event(
                    actor,
                    ts,
                    entity_type="invite",
                    entity_id=row.id,
                    action="update",
                    field_key="revoked_at",
                    old_value=None,
                    new_value=ts,
                    note=note,
                )
            ],
        )
