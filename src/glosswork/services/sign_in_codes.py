"""Sign-in by a six-digit emailed code (change 9, FR-I18).

A hosted workspace's people have no password anyone knows: the bootstrap claim makes one
up and the control plane revokes the claim's token in the same run. So a person signs in
by asking for a code at the workspace's sign-in page and typing the code the workspace
had the hosting control plane's relay email them. This service is the whole of that
path; the routes in ``routes/auth.py`` only parse and shape (DD-3).

**Enumeration-safe by construction.** The request's answer never depends on the address:
the route answers ``202`` with one sentence before this service looks anything up, and the
work runs after the answer as a background task. A row is written for every address,
known or not, so what verification does next cannot tell the two apart either; an
unknown address's code is simply never sent.

**Limits live where a restart cannot clear them.** A hosted machine sleeps when idle and
restarts on every configuration change, so the per-address limits are counted from the
``sign_in_codes`` table itself. The per-source limit is the login limiter's, in memory,
as DD-14 has it.

**No request supersedes a live code** (plan F2). Someone who knows a person's address
must not be able to kill the code that person is typing by asking for another; a code
they trigger lands in the owner's inbox, where it still works. Verification compares
against every live code for the address, and a wrong guess counts against all of them.

**What is accepted** (DQ4). Anyone can spend an address's hourly and daily allowance from
many sources and keep that person out until it rolls off, up to a day; the recovery is
the operator command ``clear-sign-in-codes``. The guessing odds at these numbers are
about 1 in 10,000 per targeted address per day.

A code hash is sha256, not a slow hash: a six-digit code has a million values, so no hash
protects it from someone holding the database for ten minutes, and that person already
holds every session hash. What protects a code is its lifetime, its attempt cap and the
limits.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import Connection

from glosswork.actor import anonymous_actor
from glosswork.db import Database
from glosswork.errors import AuthenticationFailedError, FeatureDisabledError
from glosswork.logging import get_logger
from glosswork.repositories.interfaces import PrincipalRepository, SignInCodeRepository
from glosswork.repositories.models import PrincipalRow, SignInCodeRow
from glosswork.services.invites import InviteService
from glosswork.services.relay import (
    RelayMessage,
    RelaySender,
    is_deliverable_address,
    normalize_address,
)
from glosswork.timeutil import format_datetime, utc_now

# Named constants (DD-18), published by nothing because no agent uses these routes.
CODE_LIFETIME = timedelta(minutes=10)
CODE_MAX_ATTEMPTS = 5
CODES_PER_ADDRESS_PER_HOUR = 5
CODES_PER_ADDRESS_PER_DAY = 20
CODE_RETENTION = timedelta(hours=24)
#: A ceiling on the rows an address that cannot sign in may leave per day (plan F15). Past
#: it an unknown address's request writes nothing, which the requester cannot see because
#: it happens after the answer. An address that can sign in is never refused by it.
UNSENT_CODE_ROWS_PER_DAY = 10_000

#: The request's one answer, whatever the address.
REQUEST_MESSAGE = "If this address can sign in here, a code is on its way. It works for 10 minutes."
#: Every verification failure's one message: unknown address, wrong code, expired code,
#: a used code, a removed person, a revoked or expired invite.
VERIFY_FAILURE_MESSAGE = "That code is not right, or it has expired. Ask for a new code."

FEATURE = "email_code_sign_in"
SETTING = "GW_RELAY_URL"

_SIX_DIGITS = re.compile(r"[0-9]{6}", re.ASCII)


def code_hash(code_id: str, code: str) -> str:
    """The stored form of a code: sha256 of ``"<id>:<code>"``, so equal codes on two rows
    never share a hash."""
    return hashlib.sha256(f"{code_id}:{code}".encode()).hexdigest()


def feature_disabled() -> FeatureDisabledError:
    return FeatureDisabledError(
        "Sign-in by emailed code is not turned on for this workspace. Sign in with a "
        "password, or through your identity provider.",
        feature=FEATURE,
        setting=SETTING,
    )


class SignInCodeService:
    def __init__(
        self,
        db: Database,
        code_repo: SignInCodeRepository,
        principal_repo: PrincipalRepository,
        invites: InviteService,
        relay: RelaySender | None,
    ) -> None:
        self._db = db
        self._codes = code_repo
        self._principals = principal_repo
        self._invites = invites
        self._relay = relay
        self._logger = get_logger(__name__)

    @property
    def enabled(self) -> bool:
        return self._relay is not None

    def require_enabled(self) -> None:
        if self._relay is None:
            raise feature_disabled()

    # ------------------------------------------------------------------ request

    def request_code(self, email: str, request_id: str) -> None:
        """The background half of ``POST /api/v1/auth/code/request``: record a code for
        the address and, if the address can sign in, send it.

        Never raises. It runs after the answer was sent, where an exception would be
        logged by the server with its traceback, and a database error's text carries its
        bound parameters, a code hash among them (plan F4). So every failure is logged as
        its class and the request id, and nothing else."""
        try:
            message = self._record_code(email)
            if message is not None and self._relay is not None:
                self._relay.send(message)
        except Exception as exc:
            self._logger.error(
                "sign_in_code_request_failed",
                request_id=request_id,
                error_class=type(exc).__name__,
            )

    def _record_code(self, email: str, now: datetime | None = None) -> RelayMessage | None:
        address = normalize_address(email)
        moment = now or utc_now()
        ts = format_datetime(moment)
        expires = moment + CODE_LIFETIME
        with self._db.write() as conn:
            self._codes.delete_created_before(conn, format_datetime(moment - CODE_RETENTION))
            hour_ago = format_datetime(moment - timedelta(hours=1))
            day_ago = format_datetime(moment - timedelta(days=1))
            if self._codes.count_for_email_since(conn, address, hour_ago) >= (
                CODES_PER_ADDRESS_PER_HOUR
            ):
                return None
            if self._codes.count_for_email_since(conn, address, day_ago) >= (
                CODES_PER_ADDRESS_PER_DAY
            ):
                return None
            sendable = is_deliverable_address(address) and self._can_sign_in(conn, address, moment)
            if not sendable and self._codes.count_unsent_since(conn, day_ago) >= (
                UNSENT_CODE_ROWS_PER_DAY
            ):
                return None
            code = f"{secrets.randbelow(10**6):06d}"
            code_id = str(uuid.uuid4())
            self._codes.insert(
                conn,
                SignInCodeRow(
                    id=code_id,
                    email=address,
                    code_hash=code_hash(code_id, code),
                    created_at=ts,
                    expires_at=format_datetime(expires),
                    attempts=0,
                    consumed_at=None,
                    sent=sendable,
                ),
            )
        if not sendable:
            return None
        return RelayMessage.sign_in_code(address, code, expires)

    def _can_sign_in(self, conn: Connection, address: str, now: datetime) -> bool:
        """An active local person, or an address with a live invite."""
        if _signs_in_by_code(self._principals.get_by_email(conn, address)):
            return True
        return self._invites.live_invite(conn, address, now) is not None

    # ------------------------------------------------------------------- verify

    def verify_code(
        self, email: str, code: str, request_id: str, now: datetime | None = None
    ) -> PrincipalRow:
        """Check a code and return the person it signs in, or raise the one failure.

        In one transaction: compare with every live code for the address; no match adds
        an attempt to each and spends any at the cap; a match spends that code and then
        resolves the person **by address, whatever their state** (plan F5): an active
        local person signs in, and otherwise a live invite creates or reactivates them.
        The failure is raised after the transaction commits, so the attempts it counted
        are kept."""
        address = normalize_address(email)
        if not isinstance(code, str) or not _SIX_DIGITS.fullmatch(code):
            raise AuthenticationFailedError(VERIFY_FAILURE_MESSAGE)
        moment = now or utc_now()
        ts = format_datetime(moment)
        principal: PrincipalRow | None = None
        with self._db.write() as conn:
            live = self._codes.list_live_for_email(conn, address, ts)
            matches = [hmac.compare_digest(row.code_hash, code_hash(row.id, code)) for row in live]
            if not any(matches):
                for row in live:
                    attempts = row.attempts + 1
                    changes: dict[str, object] = {"attempts": attempts}
                    if attempts >= CODE_MAX_ATTEMPTS:
                        changes["consumed_at"] = ts
                    self._codes.update_row(conn, row.id, changes)
            else:
                matched = live[matches.index(True)]
                self._codes.update_row(conn, matched.id, {"consumed_at": ts})
                existing = self._principals.get_by_email(conn, address)
                if _signs_in_by_code(existing):
                    principal = existing
                else:
                    principal = self._invites.accept_in_txn(
                        conn, anonymous_actor(request_id), address, existing, moment
                    )
        if principal is None:
            raise AuthenticationFailedError(VERIFY_FAILURE_MESSAGE)
        return principal

    # ----------------------------------------------------------------- recovery

    def clear_codes(self, email: str) -> int:
        """Delete an address's code rows, which resets its per-address count (DQ4). The
        operator's recovery for a person locked out by someone spending their allowance;
        run from ``python -m glosswork.admin clear-sign-in-codes``. Touches nothing else."""
        with self._db.write() as conn:
            return self._codes.delete_for_email(conn, normalize_address(email))


def _signs_in_by_code(principal: PrincipalRow | None) -> bool:
    return (
        principal is not None
        and principal.is_active
        and principal.type == "user"
        and principal.auth_provider == "local"
    )
