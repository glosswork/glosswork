"""Error types with machine-readable codes (docs/MCP_TOOLS.md section 6, FR-A4, FR-M6).

Every error carries a stable ``code``, a message written to tell the caller what to do
next, and structured ``details``. REST and MCP adapters map these onto their envelopes.
"""

from __future__ import annotations

from typing import Any


class GlossworkError(Exception):
    """Base for all domain errors."""

    code = "internal_error"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}


def error_envelope(exc: GlossworkError) -> dict[str, Any]:
    """The one ``{code, message, details}`` envelope shared by every adapter (FR-A4,
    FR-M6): one mapping, two surfaces. The REST exception handler
    and the MCP tool error path both call this, so the two surfaces cannot drift."""
    return {"code": exc.code, "message": exc.message, "details": exc.details}


class UnknownObjectTypeError(GlossworkError):
    code = "unknown_object_type"

    def __init__(self, key: str, valid_keys: list[str]) -> None:
        super().__init__(
            f"No object type with key {key!r}. Valid object type keys: "
            f"{', '.join(sorted(valid_keys)) or '(none defined yet)'}. "
            "Call list_object_types to see what each type is for.",
            {"key": key, "valid_keys": sorted(valid_keys)},
        )


class UnknownFieldError(GlossworkError):
    code = "unknown_field"

    def __init__(
        self, field_key: str, object_type_key: str, valid_keys: list[str], near_misses: list[str]
    ) -> None:
        suggestion = f" Did you mean {near_misses[0]!r}?" if near_misses else ""
        super().__init__(
            f"Object type {object_type_key!r} has no field {field_key!r}.{suggestion} "
            f"Valid field keys: {', '.join(sorted(valid_keys))}. "
            "Call describe_object_type for each field's type and description.",
            {
                "field_key": field_key,
                "object_type_key": object_type_key,
                "valid_keys": sorted(valid_keys),
                "near_misses": near_misses,
            },
        )


class InvalidOperatorError(GlossworkError):
    code = "invalid_operator"

    def __init__(self, op: str, field_key: str, field_type: str, valid_ops: list[str]) -> None:
        super().__init__(
            f"Operator {op!r} is not valid for field {field_key!r} of type {field_type!r}. "
            f"Use one of: {', '.join(valid_ops)}.",
            {"op": op, "field_key": field_key, "field_type": field_type, "valid_ops": valid_ops},
        )


class ValidationFailedError(GlossworkError):
    code = "validation_failed"

    def __init__(self, message: str, field_key: str | None = None, **extra: Any) -> None:
        details: dict[str, Any] = dict(extra)
        if field_key is not None:
            details["field_key"] = field_key
        super().__init__(message, details)
        self.field_key = field_key


class VersionConflictError(GlossworkError):
    """Stale write (FR-R4). Carries everything the caller needs to merge."""

    code = "version_conflict"

    def __init__(
        self,
        record_key: str,
        current_version: int,
        supplied_version: int,
        conflicting_fields: dict[str, dict[str, Any]],
        changed_since_your_version: list[str],
    ) -> None:
        super().__init__(
            f"Record {record_key} is at version {current_version}; you supplied "
            f"{supplied_version}. Re-read the record and retry, or pass force=true "
            "to overwrite.",
            {
                "record_key": record_key,
                "current_version": current_version,
                "supplied_version": supplied_version,
                "conflicting_fields": conflicting_fields,
                "changed_since_your_version": changed_since_your_version,
            },
        )
        self.current_version = current_version
        self.conflicting_fields = conflicting_fields
        self.changed_since_your_version = changed_since_your_version


class RelationBlockedError(GlossworkError):
    """Delete blocked by inbound links (FR-L4)."""

    code = "relation_blocked"

    def __init__(self, record_key: str, blocking_record_keys: list[str]) -> None:
        super().__init__(
            f"Record {record_key} is the target of links from: "
            f"{', '.join(blocking_record_keys)}. Unlink those records first, or pass "
            "force=true to remove the links and proceed.",
            {"record_key": record_key, "blocking_record_keys": blocking_record_keys},
        )
        self.blocking_record_keys = blocking_record_keys


class NotFoundError(GlossworkError):
    code = "not_found"

    def __init__(self, entity: str, ref: str) -> None:
        super().__init__(
            f"No {entity} found for {ref!r}. Verify the key or id.",
            {"entity": entity, "ref": ref},
        )


class ImpactChangedError(GlossworkError):
    """Approval-time impact differs materially from proposal-time impact (FR-S8, section 3)."""

    code = "impact_changed"

    def __init__(self, proposal_id: str, new_impact: dict[str, Any]) -> None:
        super().__init__(
            f"The data affected by proposal {proposal_id} has changed since it was "
            "proposed. Review the recomputed impact and re-approve passing it as "
            "confirm_impact to proceed.",
            {"proposal_id": proposal_id, "new_impact": new_impact},
        )
        self.new_impact = new_impact


class ProposalStateError(GlossworkError):
    code = "proposal_state"

    def __init__(self, proposal_id: str, status: str) -> None:
        super().__init__(
            f"Proposal {proposal_id} is {status!r} and can no longer be decided.",
            {"proposal_id": proposal_id, "status": status},
        )


class InsufficientScopeError(GlossworkError):
    """The token's scope is below what the operation requires (FR-M4, DD-8)."""

    code = "insufficient_scope"

    def __init__(self, tool_name: str, required_scope: str, actual_scope: str) -> None:
        super().__init__(
            f"Tool {tool_name!r} requires the {required_scope!r} scope; your token has the "
            f"{actual_scope!r} scope. Request a token with the {required_scope!r} scope from "
            "an administrator, then reconnect with it.",
            {
                "tool_name": tool_name,
                "required_scope": required_scope,
                "actual_scope": actual_scope,
            },
        )
        self.required_scope = required_scope
        self.actual_scope = actual_scope

    @classmethod
    def for_route(
        cls, method: str, path: str, required_scope: str, actual_scope: str
    ) -> InsufficientScopeError:
        """The same error for the REST surface. One error class, one code, one
        envelope across both surfaces: only the identifying details differ, because a
        route is named by method and path where a tool is named by its tool name."""
        exc = cls.__new__(cls)
        GlossworkError.__init__(
            exc,
            f"{method} {path} requires the {required_scope!r} scope; your token has the "
            f"{actual_scope!r} scope. Request a token with the {required_scope!r} scope from "
            "an administrator, then retry with it.",
            {
                "method": method,
                "path": path,
                "required_scope": required_scope,
                "actual_scope": actual_scope,
            },
        )
        exc.required_scope = required_scope
        exc.actual_scope = actual_scope
        return exc

    @classmethod
    def for_capability(cls, where: str, capability: str) -> InsufficientScopeError:
        """A **capability credential** used somewhere it does not belong (DD-16).

        Same class, same code, same envelope as the two above: from a caller's point of
        view this is still "your credential does not reach this", and the remedy is
        still a different credential. The details differ because the fix does -- an
        upload ticket is not upgraded or requested from an administrator, it is simply
        the wrong credential for this call, and the caller already holds a real one.

        ``actual_scope`` reports ``capability`` rather than the ticket's stored
        ``write``: reporting ``write`` would tell a caller its scope was insufficient
        for a route ``write`` reaches, which is the opposite of true.
        """
        exc = cls.__new__(cls)
        GlossworkError.__init__(
            exc,
            f"{where} does not accept an upload ticket. The credential you presented is "
            f"a single-use ticket for {capability!r} and is accepted only on the one "
            "route that operation names. Use your own personal access token, or the "
            "session you already hold, for anything else.",
            {
                "where": where,
                "capability": capability,
                "required_scope": "write",
                "actual_scope": "capability",
            },
        )
        exc.required_scope = "write"
        exc.actual_scope = "capability"
        return exc

    @classmethod
    def for_operation(
        cls, operation: str, required_scope: str, actual_scope: str
    ) -> InsufficientScopeError:
        """The same error raised from inside the service layer, named by *what was
        attempted* rather than by a route or a tool.

        A service must not name a REST method and path: the same method is reached
        from REST, from MCP, and from the operator CLI, and reporting a route the
        caller never called is worse than reporting nothing (DD-3 — the service layer
        does not know its adapters).
        """
        exc = cls.__new__(cls)
        GlossworkError.__init__(
            exc,
            f"{operation} requires the {required_scope!r} scope; your credential has the "
            f"{actual_scope!r} scope. Use a credential with the {required_scope!r} scope, "
            "or ask an administrator for one.",
            {
                "operation": operation,
                "required_scope": required_scope,
                "actual_scope": actual_scope,
            },
        )
        exc.required_scope = required_scope
        exc.actual_scope = actual_scope
        return exc

    @classmethod
    def for_session_only(
        cls, operation: str, actual_auth_method: str, scope: str
    ) -> InsufficientScopeError:
        """A **credential kind** refusal rather than a scope one: the
        credential's scope was never the problem, and a personal access token must
        never be able to change the password of the human it was handed to.

        Modelled on :meth:`for_operation` for the same reason: raised from inside the
        service layer and named by *what was attempted*, never by a route's method and
        path (DD-3). ``required_scope`` and ``actual_scope`` are both set to ``scope``
        -- the actor's own scope, since scope is not what refused this call -- only
        ``actual_auth_method`` in the details distinguishes the refusal from a plain
        scope shortfall.
        """
        exc = cls.__new__(cls)
        GlossworkError.__init__(
            exc,
            f"{operation} can only be done from a signed-in browser session; a "
            "personal access token cannot change a password.",
            {
                "operation": operation,
                "required_auth_method": "session",
                "actual_auth_method": actual_auth_method,
            },
        )
        exc.required_scope = scope
        exc.actual_scope = scope
        return exc


class ForbiddenError(GlossworkError):
    """The credential's scope was sufficient and the *principal's grant* was not
    (DD-11).

    Sits beside :class:`InsufficientScopeError`, which keeps its exact prior meaning:
    the *credential's scope* is too low. The two are separate codes because the remedy
    differs and an agent has to be able to tell them apart without parsing prose --
    ``insufficient_scope`` is fixed by presenting a stronger credential, ``forbidden``
    only by someone granting this principal access to this object type. A ``read`` PAT
    held by the administrator of a type therefore still reports ``insufficient_scope``
    on a write, because the ceiling, not the grant, is what refused it.

    The message names four things, which is what an agent needs in order to stop and do
    something useful instead of retrying: the type, the level held, the level required,
    and who can fix it.
    """

    code = "forbidden"

    def __init__(self, object_type_key: str, held: str, required: str) -> None:
        if held == "none":
            opening = (
                f"You have no grant on object type {object_type_key!r}. You hold 'none'; "
                f"this call needs at least {required!r}."
            )
            remedy = "to grant it."
        else:
            opening = (
                f"Your access to object type {object_type_key!r} is {held!r}; this call "
                f"needs at least {required!r}."
            )
            remedy = "to raise it."
        super().__init__(
            f"{opening} Ask an administrator of {object_type_key!r}, or a system "
            f"administrator, {remedy}",
            {"object_type": object_type_key, "held": held, "required": required},
        )
        self.object_type_key = object_type_key
        self.held = held
        self.required = required

    @classmethod
    def for_role(
        cls, method: str, path: str, required_role: str, actual_role: str
    ) -> ForbiddenError:
        """The same code for the *role* axis, built exactly the way
        ``InsufficientScopeError.for_route`` builds its REST variant.

        One code, because the caller's situation is the same in both: the credential was
        strong enough and the principal was not, so the remedy is somebody granting this
        principal something rather than the caller presenting a stronger credential. A
        ``creator`` holds ``admin`` scope precisely so it can reach the schema routes, so
        ``insufficient_scope`` would be an outright lie on these twelve.
        """
        exc = cls.__new__(cls)
        GlossworkError.__init__(
            exc,
            f"{method} {path} is restricted to principals with the {required_role!r} role; "
            f"this principal is {actual_role!r}. Ask a system administrator to run it, or "
            "to change your role.",
            {
                "method": method,
                "path": path,
                "required_role": required_role,
                "actual_role": actual_role,
            },
        )
        exc.object_type_key = ""
        exc.held = actual_role
        exc.required = required_role
        return exc


class TokenRefusedError(GlossworkError):
    """The presented credential cannot be verified by this deployment: unknown,
    revoked, expired, or belonging to a deactivated principal (DD-8).

    Raised by the ``TokenResolver`` seam, so it is refused *before* any dispatch on
    either surface. REST answers 401 with the shared ``{code, message, details}``
    envelope; MCP raises JSON-RPC ``INVALID_REQUEST`` carrying the same message. The
    ``reason`` detail distinguishes expired from unknown, because the fix differs.
    """

    code = "invalid_token"

    def __init__(self, message: str, reason: str = "unknown") -> None:
        super().__init__(message, {"reason": reason})
        self.reason = reason


class CsrfFailedError(GlossworkError):
    """A cookie-authenticated, non-safe request carried no CSRF token, or one that
    does not match the session's stored hash (DD-10).

    Raised only for requests authenticated by the session cookie: a bearer-authenticated
    request is structurally immune to CSRF (a cross-site page cannot attach a custom
    header without a CORS preflight this application never grants) and is exempt.
    Distinct from ``insufficient_scope`` because the remedy is entirely different — the
    caller has the right credential and the right scope, just not the right token.
    """

    code = "csrf_failed"

    def __init__(self, message: str) -> None:
        super().__init__(message)


class FeatureDisabledError(GlossworkError):
    """A correct request against a deployment that has the capability turned off
    (DD-19).

    Deliberately **not** ``validation_failed``: that code's documented remedy
    (docs/MCP_TOOLS.md section 6) is "fix the named field", which here would send an
    agent to repair an argument that is valid. This sits in the 409 family with
    ``impact_changed`` and ``proposal_state``, the other "correct request, wrong
    state" codes. ``details.feature`` names the capability, ``details.setting`` the
    configuration that governs it, and ``details.use_instead``, when present, the
    alternative the caller can use on its own (``{"mode": "keyword"}`` for a
    semantic search on a deployment with embedding disabled).
    """

    code = "feature_disabled"

    def __init__(
        self,
        message: str,
        *,
        feature: str,
        setting: str,
        use_instead: dict[str, Any] | None = None,
    ) -> None:
        details: dict[str, Any] = {"feature": feature, "setting": setting}
        if use_instead is not None:
            details["use_instead"] = use_instead
        super().__init__(message, details)
        self.feature = feature
        self.setting = setting
        self.use_instead = use_instead


class BootstrapClaimedError(GlossworkError):
    """A second claim against a deployment that already has a user (DD-37).

    In the 409 "correct request, wrong state" family with ``feature_disabled`` and
    ``proposal_state``, not a 401: the caller's secret was right, and telling it so is
    the whole point -- a hosting operator that gets this knows its tenant is bootstrapped
    and that nobody is going to hand it a second credential, which is a different
    recovery from "your secret is wrong".

    The state it reports is permanent. Principals are never hard-deleted, so once this
    deployment has a ``user`` principal -- from a claim, from ``create-admin``, from
    ``GW_BOOTSTRAP_ADMIN_*``, or from a first OIDC sign-in -- the endpoint stays closed
    for the life of the volume.
    """

    code = "bootstrap_claimed"

    def __init__(self, message: str) -> None:
        super().__init__(message)


class WorkspaceReadOnlyError(GlossworkError):
    """A write against a deployment running with ``GW_READ_ONLY`` on (DD-38).

    In the 409 "correct request, wrong state" family with ``feature_disabled`` and
    ``bootstrap_claimed``: the call was well formed and the caller's credential
    reached it, and what refuses it is the deployment's state. Not 403, which says the
    caller lacks authority, and not 402, which is hosted vocabulary in a self-host image.

    The message never says *why* the workspace is read-only (a trial ended, a payment
    failed), because the product does not know; the hosting operator does. What it does say
    is what the caller can still do and where to go, which is the subscribe URL when the
    deployment has one and the setting an administrator turned on when it does not.

    ``attempted`` is what was refused in the caller's own vocabulary: the method and route
    template on REST, the tool name on MCP, as ``refuse_capability_credential`` names them.
    """

    code = "workspace_read_only"

    def __init__(self, attempted: str, subscribe_url: str | None) -> None:
        opening = (
            f"This workspace is read-only, so {attempted} changed nothing. Reading, searching "
            "and exporting still work."
        )
        if subscribe_url is not None:
            remedy = f"To make changes again, subscribe at {subscribe_url}."
        else:
            remedy = "An administrator of this deployment turned writes off with GW_READ_ONLY."
        super().__init__(
            f"{opening} {remedy}",
            {"subscribe_url": subscribe_url, "setting": "GW_READ_ONLY", "attempted": attempted},
        )
        self.attempted = attempted
        self.subscribe_url = subscribe_url


class AuthenticationFailedError(GlossworkError):
    """A login attempt failed (FR-I1).

    Deliberately one error for every failure mode of a password login — unknown
    email, wrong password, deactivated principal — so the response cannot be used to
    enumerate users. The service layer walks the same code path (including a dummy
    hash verification) for each, so the two do not differ in timing either.
    """

    code = "invalid_credentials"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, details)


class OperatorTokenRefusedError(GlossworkError):
    """The one answer ``GET /api/v1/usage`` gives anybody who is not the operator
    (DD-39, FR-P10).

    **One error for four different situations**, and that is the whole point: the
    variable is unset, or it is blank, or the header is absent, or the value is wrong.
    All four get this, byte for byte, so an unauthenticated prober cannot tell a metered
    workspace from an unmetered one in one request. Naming ``feature_disabled`` for the
    unconfigured case would have been exactly that oracle, because ``feature_disabled``
    is **409** and a credential refusal is **401**.

    A **new code rather than a reused one**, deliberately, and not ``invalid_operator``:
    that code already exists and already means "this filter operator is not valid for
    this field type", which is tenant-facing and about query syntax. Reusing it would
    make one string mean two unrelated things in the same table. It is also not
    ``invalid_token`` or ``invalid_credentials``, which both describe a *tenant*
    credential the edge resolved; nothing was resolved here, because the path is
    credential-exempt and this refusal is the route's own.

    The message names no setting and no deployment fact. There is nothing actionable to
    say to a caller who does not hold the credential, and saying anything would be the
    disclosure this error exists to prevent.
    """

    code = "operator_token_refused"

    def __init__(self) -> None:
        super().__init__(
            "This request did not carry the operator credential this endpoint requires."
        )


# Every GlossworkError code maps to exactly one HTTP status. Declared here rather
# than in ``app.py`` because two consumers need it and must not drift: the single
# ``@app.exception_handler(GlossworkError)`` for anything raised inside the
# application, and ``RequestContextMiddleware``, which resolves the credential
# outside the router (and therefore outside that handler's reach) and shapes its
# refusal through this same table and ``error_envelope``.
class RateLimitedError(GlossworkError):
    """Too many attempts against one credential in one window (FR-I1).

    Carries ``retry_after_seconds`` so the route can set a ``Retry-After`` header and
    a caller can back off deliberately rather than by trial.

    **The message and details must not depend on whether the account exists.** The
    login builds indistinguishability into ``login_with_password`` with its dummy-hash
    branch, and a limiter that said "too many attempts for a known account" would
    hand back the account-enumeration oracle that branch exists to close. The limiter
    counts attempts before anything looks the email up, so an unknown email and a
    known one trip it identically -- asserted in ``tests/test_login_rate_limit.py``.

    ``window`` says which of the limiter's two budgets refused -- ``"account"``
    for the (email, source) pair, ``"source"`` for the address alone. It rides in the
    **details**, not the message, because the message is what a person reads and "too
    many login attempts" is the right sentence for both. It says nothing about whether
    the account exists.

    ``attempt`` names what kind of attempt tripped the limiter -- ``"login"`` by
    default, so every existing caller keeps its exact bytes, and ``"password change"``
    when the same limiter guards ``POST /api/v1/me/password``: a person on
    that form reading "too many login attempts" would read it as being about the wrong
    action.
    """

    code = "rate_limited"

    def __init__(
        self, retry_after_seconds: int, window: str = "account", attempt: str = "login"
    ) -> None:
        super().__init__(
            f"Too many {attempt} attempts. Wait {retry_after_seconds} seconds and try again.",
            {"retry_after_seconds": retry_after_seconds, "window": window},
        )
        self.retry_after_seconds = retry_after_seconds
        self.window = window


class PayloadTooLargeError(GlossworkError):
    """The request body is larger than this deployment accepts (DD-18).

    Its own code rather than ``validation_failed``, because the remedy differs and an
    agent has to be able to tell them apart without parsing prose: ``validation_failed``
    means "fix the named field" (docs/MCP_TOOLS.md section 6), and there is no field to
    fix here -- the caller sends less, or an operator raises the cap. 413 is the status
    HTTP already reserves for exactly this, which is why the code sits in the table
    rather than borrowing 422's.

    Raised at three places, all of them edges: ``RequestContextMiddleware``'s REST body
    cap, the MCP wrapper that replaces the SDK's plain-text 413, and -- with
    ``limit_name`` naming the route's own variable -- the attachment upload and CSV
    import, the two routes exempt from the edge cap because they legitimately take more.

    ``limit_name`` is the ``GW_*`` variable an operator would change, and it is in the
    message as well as the details: a 413 that does not say which of three caps refused
    it sends the reader to the wrong one.
    """

    code = "payload_too_large"

    def __init__(
        self, limit_bytes: int, limit_name: str, received_bytes: int | None = None
    ) -> None:
        seen = f" This request carried at least {received_bytes} bytes." if received_bytes else ""
        details: dict[str, Any] = {"limit_bytes": limit_bytes, "limit_name": limit_name}
        if received_bytes is not None:
            details["received_bytes"] = received_bytes
        super().__init__(
            f"Request body is larger than this deployment accepts: the limit is "
            f"{limit_bytes} bytes ({limit_name}).{seen} Send less data, or ask an "
            f"administrator to raise {limit_name}.",
            details,
        )
        self.limit_bytes = limit_bytes
        self.limit_name = limit_name


class InternalError(GlossworkError):
    """Something this system did not classify (DD-19).

    The one error whose message deliberately says nothing about what went wrong.
    Every other ``GlossworkError`` exists because some code decided the caller
    could act on it; this one is raised by nobody and constructed only at the two
    seams where an *unclassified* exception would otherwise escape -- the app-level
    REST handler and ``McpAdapter``'s two ``except Exception`` blocks. Without the MCP
    seam, the SDK's catch-all returns ``str(exc)`` in the tool result: a
    ``sqlite3.OperationalError`` hands over its table names, and a ``KeyError`` its
    internal key, to any caller with enough scope to reach the tool. REST does the
    opposite, and the two agree.

    Carries only the request id, which is the one thing that is both safe to disclose
    and actually useful: it is what an operator greps the application log for, where
    the full traceback was written at ``error``.

    ``GlossworkError.code`` already defaults to ``"internal_error"`` and
    ``STATUS_BY_CODE`` already maps it to 500; nothing raised the base class, so this
    subclass is the only addition and ``http_status_for`` is unchanged.
    """

    code = "internal_error"

    def __init__(self, request_id: str) -> None:
        super().__init__(
            "This request failed for a reason the system could not classify. Nothing "
            "was changed by the failure itself. If it repeats, quote request id "
            f"{request_id} to an administrator, who can find the details in the "
            "application log.",
            {"request_id": request_id},
        )
        self.request_id = request_id


STATUS_BY_CODE: dict[str, int] = {
    "unknown_object_type": 404,
    "unknown_field": 400,
    "invalid_operator": 400,
    "validation_failed": 422,
    "version_conflict": 409,
    "relation_blocked": 409,
    "not_found": 404,
    "impact_changed": 409,
    "proposal_state": 409,
    "feature_disabled": 409,
    "bootstrap_claimed": 409,
    "workspace_read_only": 409,
    "insufficient_scope": 403,
    "forbidden": 403,
    "invalid_token": 401,
    "invalid_credentials": 401,
    "operator_token_refused": 401,
    "csrf_failed": 403,
    "rate_limited": 429,
    "payload_too_large": 413,
    "internal_error": 500,
}
DEFAULT_ERROR_STATUS = 400


def http_status_for(exc: GlossworkError) -> int:
    return STATUS_BY_CODE.get(exc.code, DEFAULT_ERROR_STATUS)
