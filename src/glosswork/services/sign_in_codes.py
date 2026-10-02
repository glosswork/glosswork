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

**A code is stored as a keyed digest, and the key is in no copy of the database** (DD-45).
A six-digit code has a million values, so no unkeyed hash, slow or fast, protects it from
someone holding the row: they try every value. That mattered little while the only way to
hold the row was to hold the machine. It matters once a backup can be taken by a
credential that is not an administrator's (DD-39), because a backup has to carry this
table. So the stored form is HMAC-SHA256 over a fixed label, the row id and the code,
under a key derived from the deployment's relay token (:func:`derive_code_key`). The
relay token is a setting: it is never written to the database, the data directory or a
log, so an artifact alone cannot be searched for a code.

There is **one stored form and one key, with no fallback**. Codes are on exactly when the
relay is configured, so a deployment that issues codes always has the token; the service
refuses to be built with a relay and no key; and nothing here computes an unkeyed digest
of a code. A row written before this form existed, or under a relay token that has since
changed, never matches and is refused like any wrong code: the person asks for another.
No older form is accepted for a while, because that would keep alive exactly the rows an
artifact can be used against.

Against guessing online, what protects a code is unchanged: its lifetime, its attempt cap
and the limits.
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


#: The two fixed labels of the stored form. Versioned, so a later construction can never
#: be confused with this one, and distinct from each other, so the key derivation and the
#: code digest are separate uses of their inputs.
_CODE_KEY_LABEL = b"glosswork.sign-in-code-key.v1:"
_CODE_LABEL = b"glosswork.sign-in-code.v1:"


def derive_code_key(relay_token: str) -> bytes:
    """The 32-byte key a deployment's codes are stored under: SHA-256 over a fixed label
    followed by the relay token.

    **Derived, and never the token itself**, for a measured reason. HMAC replaces a key
    longer than its 64-byte block with that key's SHA-256, and the hosting control plane
    stores exactly the SHA-256 of each relay token. With the token used directly as the
    key, a token of 65 characters or more would make the control plane's stored hash a
    working key. Under its own label the derived key equals no value anyone stores, at
    any token length, and this use of the token stays distinct from its use as the
    relay's bearer credential.

    The key is handed to :class:`SignInCodeService` and to nothing else. It is never
    logged, returned, stored or put in an exception message.
    """
    return hashlib.sha256(_CODE_KEY_LABEL + relay_token.encode("utf-8")).digest()


def code_hash(key: bytes, code_id: str, code: str) -> str:
    """The stored form of a code: HMAC-SHA256 under ``key`` over a fixed label and
    ``"<id>:<code>"``. The row id is in it so equal codes on two rows never share a
    stored form; the key is what makes a row useless to someone holding only the row."""
    return hmac.new(key, _CODE_LABEL + f"{code_id}:{code}".encode(), hashlib.sha256).hexdigest()


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
        code_key: bytes | None = None,
    ) -> None:
        # A relay and a key, or neither. A relay with no key would issue codes it has no
        # way to store safely, and a key with no relay is a secret held for nothing; both
        # are assembly mistakes and are refused here, not at the first sign-in.
        if code_key is not None and not code_key:
            raise ValueError("code_key must not be empty; pass None when there is no relay")
        if (relay is None) != (code_key is None):
            raise ValueError(
                "SignInCodeService takes a relay and a code key together, or neither: "
                "sign-in codes are on exactly when both exist"
            )
        self._code_key = code_key
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

    def _key(self) -> bytes:
        """The key, or the feature's refusal. There is no unkeyed path to fall back to:
        without a key no code is stored and none is checked."""
        if self._code_key is None:
            raise feature_disabled()
        return self._code_key

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
        key = self._key()
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
                    code_hash=code_hash(key, code_id, code),
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
        key = self._key()
        address = normalize_address(email)
        if not isinstance(code, str) or not _SIX_DIGITS.fullmatch(code):
            raise AuthenticationFailedError(VERIFY_FAILURE_MESSAGE)
        moment = now or utc_now()
        ts = format_datetime(moment)
        principal: PrincipalRow | None = None
        with self._db.write() as conn:
            live = self._codes.list_live_for_email(conn, address, ts)
            matches = [
                hmac.compare_digest(row.code_hash, code_hash(key, row.id, code)) for row in live
            ]
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
