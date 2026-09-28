"""The first administrator, handed back over HTTP exactly once (DD-37, FR-I1).

A program that starts a fresh container cannot get a working credential out of it
without running a command *inside* it. ``BootstrapService.claim`` closes that: the
caller presents the secret it configured as ``GW_BOOTSTRAP_SECRET`` and receives the
first administrator's ``admin`` personal access token together with the two addresses
a harness needs -- the MCP endpoint and the human sign-in page.

**The rule lives here and only here** (DD-3). The route parses a body, calls
:meth:`BootstrapService.claim`, and shapes a response; every refusal below is this
service's, so the MCP surface and a future adapter inherit the same answers.

The order of the four checks is load-bearing, not stylistic:

1. the feature is enabled;
2. the secret matches, compared with :func:`hmac.compare_digest`;
3. the body is valid and the password is hashed, **outside** any transaction;
4. the deployment is unclaimed, the administrator is created, and its token is minted,
   **inside one** transaction.

Two and three are in that order so that a caller without the secret learns nothing
about the password policy or about whether this deployment is already bootstrapped.
Three and four are in that order because SQLite has a single writer:
holding its lock across a ~30 ms Argon2 hash would serialize every other write in the
deployment behind an unauthenticated request. Four is one transaction
because two claims arriving together must yield exactly one token (DD-22), and a check
outside ``BEGIN IMMEDIATE`` loses that race in practice, not just in theory.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from glosswork.actor import ActorContext, anonymous_actor
from glosswork.config import Settings
from glosswork.db import Database
from glosswork.errors import (
    AuthenticationFailedError,
    BootstrapClaimedError,
    FeatureDisabledError,
)
from glosswork.logging import get_logger
from glosswork.repositories.interfaces import PrincipalRepository
from glosswork.services.principals import PrincipalService, role_scope
from glosswork.services.tokens import AccessTokenService
from glosswork.services.workspace import WorkspaceService

logger = get_logger(__name__)

#: What the minted token is called in the token list, so an administrator reading
#: ``GET /api/v1/access-tokens`` later can tell where it came from without guessing.
BOOTSTRAP_TOKEN_NAME = "bootstrap"


@dataclass(frozen=True, slots=True)
class BootstrapHandoff:
    """Everything a program needs to connect an agent to a fresh deployment.

    These names are a contract with the connection kit and a hosting operator, so they
    are approved copy rather than implementation detail. The seventh, ``agent_label``,
    is a deliberate part of DD-37's contract rather than a quiet widening: without it a
    caller cannot tell a stored label from a dropped one.

    ``token`` is the plaintext, which exists here and in the one response that carries
    it and nowhere else -- not in a table, not in a log line, not in an audit row.

    ``mcp_url`` and ``sign_in_url`` are ``None`` only when ``GW_BASE_URL`` is unset,
    which :func:`~glosswork.config.load_settings` refuses beside
    ``GW_BOOTSTRAP_SECRET``. A real deployment therefore always carries both; the
    optionality is the type being honest about a ``Settings`` object built by hand.
    """

    token: str
    token_prefix: str
    scope: str
    principal_id: str
    mcp_url: str | None
    sign_in_url: str | None
    agent_label: str | None


class BootstrapService:
    def __init__(
        self,
        db: Database,
        principal_repo: PrincipalRepository,
        principals: PrincipalService,
        tokens: AccessTokenService,
        workspace: WorkspaceService,
        settings: Settings,
    ) -> None:
        self._db = db
        self._principal_repo = principal_repo
        self._principals = principals
        self._tokens = tokens
        self._workspace = workspace
        self._settings = settings

    def claim(
        self,
        request_id: str,
        *,
        secret: str | None,
        email: str,
        password: str,
        display_name: str | None = None,
        agent_label: str | None = None,
    ) -> BootstrapHandoff:
        """Exchange the bootstrap secret for the first administrator's credential.

        Raises :class:`~glosswork.errors.FeatureDisabledError` when the deployment set
        no secret, :class:`~glosswork.errors.AuthenticationFailedError` when the
        presented one is absent or wrong,
        :class:`~glosswork.errors.ValidationFailedError` when the email, display name
        or password is refused, and
        :class:`~glosswork.errors.BootstrapClaimedError` when this deployment already
        has a user.
        """
        configured = self._settings.bootstrap_secret
        if not configured:
            raise FeatureDisabledError(
                "Bootstrap over HTTP is not enabled on this deployment. Create the "
                "first administrator with 'python -m glosswork.admin create-admin' "
                "instead.",
                feature="bootstrap",
                setting="GW_BOOTSTRAP_SECRET",
            )
        if not self._secret_matches(secret, configured):
            raise AuthenticationFailedError(
                "The bootstrap secret is missing or does not match this deployment's "
                "GW_BOOTSTRAP_SECRET."
            )

        # The creating actor is the seeded principal at ``read`` scope, exactly as OIDC
        # provisioning attributes the principal it creates for an identity nobody has
        # signed in as yet (DD-15). The admin-scoped constructor beside it in
        # ``actor.py`` is deliberately not used here: it carries ``admin``, and nothing
        # reachable from a request may. A grep over this file and ``routes/`` is what
        # enforces that, which is why this comment does not spell its name.
        actor = anonymous_actor(request_id)
        row = self._principals.prepare_user(
            actor,
            email=email,
            display_name=display_name or email.split("@", 1)[0],
            role="admin",
            auth_provider="local",
            password=password,
        )

        with self._db.write() as conn:
            if self._principal_repo.count_users(conn) > 0:
                raise BootstrapClaimedError(
                    "This deployment is already bootstrapped: it has a user account. "
                    "Sign in, or ask an administrator for a token."
                )
            self._principals.create_user_in_txn(conn, actor, row)
            # The mint is attributed to the administrator that was just created, acting
            # for itself, the way a login attributes its session to the principal it
            # signed in. It has to be: the anonymous actor can create a principal but is
            # refused ``insufficient_scope`` when minting *for* one, so this second
            # actor is required rather than stylistic.
            minted = self._tokens.mint_in_txn(
                conn,
                self._minting_actor(request_id, row.id),
                name=BOOTSTRAP_TOKEN_NAME,
                scope="admin",
                agent_label=agent_label,
            )

        # The principal id, the email and the token prefix: what an administrator needs
        # to recognise this credential later. Never the plaintext, the password, or the
        # secret.
        logger.info(
            "bootstrap_claimed",
            principal_id=row.id,
            email=row.email,
            token_prefix=minted.row.token_prefix,
        )
        return BootstrapHandoff(
            token=minted.plaintext,
            token_prefix=minted.row.token_prefix,
            scope=minted.row.scope,
            principal_id=row.id,
            mcp_url=self._workspace.mcp_url(),
            sign_in_url=self._workspace.sign_in_url(),
            # Echoed from the stored row, not from the request, so the caller learns what
            # was actually kept rather than what it asked for.
            agent_label=minted.row.agent_label,
        )

    @staticmethod
    def _secret_matches(presented: str | None, configured: str) -> bool:
        """Constant-time comparison of the two secrets' digests.

        Digests rather than the raw strings so the comparison is over a fixed length and
        cannot leak the configured secret's length through timing, and
        ``compare_digest`` rather than ``==`` so it cannot leak the matching prefix
        either. An absent secret is a mismatch, not a separate answer.
        """
        if presented is None:
            return False
        return hmac.compare_digest(
            hashlib.sha256(presented.encode("utf-8")).digest(),
            hashlib.sha256(configured.encode("utf-8")).digest(),
        )

    @staticmethod
    def _minting_actor(request_id: str, principal_id: str) -> ActorContext:
        return ActorContext(
            principal_id=principal_id,
            principal_type="user",
            agent_label_id=None,
            auth_method="pat",
            surface="api",
            request_id=request_id,
            scope=role_scope("admin"),
        )
