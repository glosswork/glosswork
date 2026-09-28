"""Environment-driven configuration (PRD FR-P3, docs/DATA_MODEL.md section 13)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(Exception):
    """Raised when environment configuration is invalid. Message names the offending variable."""


# The length floor on ``GW_BOOTSTRAP_SECRET`` (DD-18: every bound is a setting or a
# named constant). A floor, not an entropy check -- 32 identical characters pass it. A
# random secret of this length cannot be guessed online, which is also why the claim
# route carries no rate limiter: the login limiter's per-source window is shared with
# human sign-in, so failed claims would spend the budget of every person behind the
# same address.
BOOTSTRAP_SECRET_MIN_LENGTH = 32

# The length floor on ``GW_OPERATOR_TOKEN`` (DD-39), beside its sibling above and
# for the same reason: a floor, not an entropy check. It guards a read rather than a
# one-time claim, so the value is presented on every poll for the life of the
# deployment, which is the argument for a floor rather than against one.
OPERATOR_TOKEN_MIN_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GW_", extra="ignore")

    data_dir: Path = Field(default=Path("/data"))
    auth_mode: Literal["standalone", "oidc", "both"] = "standalone"
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_admin_groups: str | None = None
    # Groups mapped to the `creator` role. Shaped exactly like
    # `oidc_admin_groups` because it is the same idea one role down: without it the
    # provider can only ever say `admin` or `member`, so a `creator` set out of band is
    # overwritten at the principal's next login. Unset means nobody, which is why a
    # deployment that never sets it behaves exactly as if the variable did not exist.
    oidc_creator_groups: str | None = None
    # The ID-token claim carrying group membership (FR-I2). Configurable because
    # providers differ; Okta's default is "groups". Both group variables are matched
    # against this one claim.
    oidc_group_claim: str = "groups"
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    # The shared secret that enables ``POST /api/v1/bootstrap`` (DD-37), where a
    # program that started this container exchanges it for the first administrator's
    # credential, exactly once.
    #
    # Unset by default, and **blank counts as unset**: ``.env.example`` ships every
    # variable with an empty value, and an operator running the image with that file as
    # ``--env-file`` would otherwise have a deployment that refuses to start over a
    # feature they never asked for. A deployment that does not set this behaves exactly
    # as if the feature did not exist (hosted-only behaviour is configuration of the one
    # image, never a fork).
    bootstrap_secret: str | None = None
    # The credential that opens ``GET /api/v1/usage`` (DD-39, FR-P10), where the
    # operator running this image for somebody else reads how much of it is being used.
    #
    # It is **not** a tenant credential and cannot be made into one: it is not a row in
    # ``access_tokens``, so a workspace administrator cannot mint it, list it, revoke it
    # or restore it from a backup. That is the whole of what "an operator token separate
    # from tenant PATs" means, and it is why this is a setting rather than a fourth value
    # in that table's ``scope`` CHECK constraint -- which would be an edit to the access
    # model, and a hosting feature does not change the access model.
    #
    # Unset by default, and **blank counts as unset**, exactly as ``GW_BOOTSTRAP_SECRET``
    # and ``GW_SUBSCRIBE_URL`` are: ``.env.example`` ships every variable empty, and an
    # operator running the image with that file as ``--env-file`` would otherwise have a
    # deployment that refuses to start over a feature they never asked for. Unlike those
    # two, this one's blank-is-off is pinned by a test, in
    # ``tests/test_operator_usage.py``.
    #
    # Rotation is a restart. For a hosted machine that is seconds; for self-host it is a
    # ``docker run`` flag, and self-host sets it not at all.
    operator_token: str | None = None
    embedding_model: str = "bge-small-en-v1.5"
    embedding_enabled: bool = True
    # Where the image baked the embedding model (DD-32). The model is never
    # fetched at runtime: this names a directory that already exists in the image, and
    # startup fails fast citing this variable when the files are missing while
    # embedding is enabled. In a source checkout it points at <repo>/models, which
    # `uv run python scripts/fetch_model.py` populates.
    model_dir: Path = Field(default=Path("/app/models"))
    max_attachment_bytes: int = 26_214_400
    # Request-body ceilings (DD-18). Every input is bounded and a bad one is a 4xx.
    #
    # ``max_request_bytes`` is the edge cap on ``/api/`` bodies, and 4 MiB is chosen to
    # **agree with a cap that is already live** rather than to match a number in a
    # library's documentation: ``streamable_http_app`` takes the SDK's
    # ``DEFAULT_MAX_REQUEST_BODY_SIZE`` of 4 MiB, so the MCP surface is body-capped
    # whatever REST does. One setting governs both, which is what keeps the two from
    # drifting and what lets a 413 carry the same envelope on either.
    #
    # Two routes legitimately take more and enforce their own ceiling: the attachment
    # upload (``max_attachment_bytes``) and the CSV import (``max_csv_import_bytes``).
    # Neither in-app cap bounds the bytes that reach the container's temp directory
    # before the route's bounded read runs -- Starlette spools a multipart upload first --
    # so the proxy's ``client_max_body_size`` is the control there and
    # docs/DEPLOYMENT.md section 4 says so.
    max_request_bytes: int = 4_194_304
    # The attachment cap's number, for the attachment cap's reason: a CSV import is one
    # transaction against the single writer, and a file this size is already far past
    # anything the perf work imported.
    max_csv_import_bytes: int = 26_214_400
    # Above anything the perf work imported and below where the single writer is pinned
    # for minutes. Refused before any row is parsed, so an over-sized batch costs nothing.
    max_csv_import_rows: int = 20_000
    # Export streams, so this bounds *work* rather than memory. The default is the
    # number export once enforced as a silent truncation, so no export that used to
    # succeed is refused by it; over the ceiling is a refusal naming the cap, never a
    # quietly shortened file.
    max_csv_export_rows: int = 100_000
    base_url: str | None = None
    # The workspace's display name, shown at the top of the sidebar (DD-28). Unset
    # means the sidebar renders the mark alone, not a fallback to the product name --
    # docs/DESIGN.md 4.3 says in terms that the product name does not appear in the
    # shell, so inventing one to fill the space would contradict the point of the
    # setting.
    workspace_name: str | None = None
    # How long an upload ticket lives, in seconds (DD-16).
    #
    # A ticket is a credential that lands in a model's context and therefore in
    # transcripts and logs, which is inherent to any design where the agent runs the
    # HTTP request itself; the mitigation is blast radius, and this is the largest part
    # of it. Five minutes is long enough for a model to read a local file and issue one
    # POST, and short enough that a ticket recovered from a log later is already dead.
    # Lengthening it widens exactly that window and nothing else.
    upload_ticket_ttl_seconds: int = 300
    log_level: Literal["debug", "info", "warning", "error", "critical"] = "info"
    # Argon2id cost parameters (FR-I1). Exposed because the memory cost is a real
    # working-set consideration in a memory-capped container: each concurrent hash holds
    # ``argon2_memory_kib`` KiB. Library defaults (t=3, m=65536 KiB, p=4) are the floor
    # this deployment ships with, not a value anyone should lower without cause.
    argon2_time_cost: int = 3
    argon2_memory_kib: int = 65536
    argon2_parallelism: int = 4
    # Minimum local-account password length. A length floor only: no composition
    # rules, no regex. Short passwords are rejected with ``validation_failed``.
    password_min_length: int = 12
    # Session cookie attributes and lifetime (DD-9, DD-10).
    #
    # ``Secure`` is configuration, not an inference from the inbound request's scheme:
    # ``uvicorn.run`` sets ``proxy_headers=True`` but leaves ``forwarded_allow_ips`` at
    # its ``127.0.0.1`` default, so behind the TLS-terminating proxy FR-P7 assumes,
    # ``X-Forwarded-Proto`` is discarded from a non-loopback proxy and the scheme reads
    # ``http`` — a cookie inferred from that would ship without ``Secure`` in production
    # with nothing failing. Defaulting to ``true`` makes the safe value the one a
    # deployment gets by not thinking about it; a developer serving plain HTTP on
    # localhost sets this to ``false`` deliberately, the same fail-closed convention as
    # the ``GW_INSECURE_*`` variables it replaces.
    cookie_secure: bool = True
    # Absolute and idle session lifetimes, both enforced server-side at resolution; the
    # cookie itself carries no ``Max-Age``.
    session_lifetime_hours: int = 12
    session_idle_hours: int = 8
    # Login rate limiting (FR-I1; DD-9).
    #
    # Argon2id's ~33 ms per verification caps a single core near 30 guesses/second,
    # which is a speed bump and not a limit, and the bootstrap administrator is a
    # local password account and the highest-value credential in the deployment. The
    # window is fixed and there are two of them -- one keyed on the (lowercased email,
    # source IP) pair and one on the source alone; see ``services/rate_limit.py`` for
    # why the pair is not enough on its own and why the source window is a second
    # dictionary rather than a sentinel key.
    login_max_attempts: int = 10
    # A second, per-source budget over the same window (DD-14). Six times the
    # per-account one: generous enough for an office behind a correctly configured
    # proxy, and it caps a single address at 12 Argon2id verifications a minute instead
    # of unlimited. A deployment behind an *untrusted* proxy sees every attempt as
    # coming from the proxy and shares one budget, which is why the default is generous
    # and why `GW_TRUSTED_PROXY_IPS` matters (docs/DEPLOYMENT.md section 4).
    login_ip_max_attempts: int = 60
    login_window_seconds: int = 300
    # Extra ``Host`` header values the MCP transport accepts, beyond the one implied
    # by ``GW_BASE_URL`` (FR-P7). Comma-separated; ``host:*`` matches any port on
    # that host. See ``mcp_allowed_hosts`` below for why an empty allowlist leaves the
    # check off rather than rejecting everything.
    mcp_allowed_hosts: str = ""
    # Proxy addresses uvicorn trusts to set ``X-Forwarded-Proto``/``X-Forwarded-For``
    # (FR-P7). Passed to ``uvicorn.run`` as ``forwarded_allow_ips`` from
    # ``entrypoint.py``; the default trusts only loopback, matching uvicorn's own
    # default and requiring an operator to name their real proxy explicitly.
    trusted_proxy_ips: str = "127.0.0.1"
    # Read-only mode (DD-38). When true, every REST and MCP write is refused with 409
    # ``workspace_read_only`` except the five calls ``scopes.READ_ONLY_OPEN_ROUTES`` names,
    # and reads and full export keep working. A hosted workspace is frozen this way when
    # its trial ends; a self-hosted operator uses it for a migration window or a deployment
    # kept for reference. Read once at startup, so changing it is a restart.
    #
    # A boolean like every other boolean here, so a **blank value refuses startup** naming
    # the variable, and ``.env.example`` ships it as ``false`` rather than blank.
    read_only: bool = False
    # Where a refused write tells its caller to subscribe. Optional, and **blank counts as
    # unset**, as ``GW_BOOTSTRAP_SECRET`` does, because ``.env.example`` ships it blank.
    # Independent of ``GW_READ_ONLY``: self-host has no subscribe page, and a trial banner
    # needs the link while the trial is still running.
    subscribe_url: str | None = None

    @field_validator("subscribe_url")
    @classmethod
    def _absolute_http_subscribe_url(cls, value: str | None) -> str | None:
        """Blank is unset; anything else must be an absolute ``http`` or ``https`` URL.

        An agent is told to go to this address, so a relative path, another scheme, or a
        missing host is an address nobody can act on. Refused here rather than at the
        first refused write, because that is a diagnosis nobody at the write can act on.
        """
        if value is None or not value.strip():
            return None
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(
                "must be an absolute http or https URL, such as https://example.com/subscribe"
            )
        return value

    @field_validator("max_attachment_bytes")
    @classmethod
    def _positive_attachment_bytes(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("must be a positive integer")
        return value

    @field_validator(
        "max_request_bytes",
        "upload_ticket_ttl_seconds",
        "max_csv_import_bytes",
        "max_csv_import_rows",
        "max_csv_export_rows",
        "argon2_time_cost",
        "argon2_memory_kib",
        "argon2_parallelism",
        "password_min_length",
        "session_lifetime_hours",
        "session_idle_hours",
        "login_max_attempts",
        "login_ip_max_attempts",
        "login_window_seconds",
    )
    @classmethod
    def _positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("must be a positive integer")
        return value

    def mcp_allowed_host_values(self) -> list[str]:
        """The ``Host`` values the MCP transport accepts (FR-P7).

        Composed of the host implied by ``GW_BASE_URL`` and whatever
        ``GW_MCP_ALLOWED_HOSTS`` adds, deduplicated and order-preserving.

        **An empty result means the check stays off**, and that is a decision rather
        than an oversight. The SDK's validator is an exact-match allowlist, so
        enabling it with nothing in it rejects every MCP request; a deployment that
        never set ``GW_BASE_URL`` (which is optional outside OIDC mode) would lose its
        agent surface entirely at upgrade, for a control that is defense in depth
        here. DD-8 already puts MCP authorization on the bearer token, not the
        ``Host`` header. So: configure a base URL or an allowlist and the check turns
        itself on; configure neither and it stays off. ``app.py`` logs
        which of the two is in force at startup, so the state is observable rather
        than silent.
        """
        values: list[str] = []
        if self.base_url:
            netloc = urlparse(self.base_url).netloc
            if netloc:
                values.append(netloc)
        values.extend(part.strip() for part in self.mcp_allowed_hosts.split(",") if part.strip())
        return list(dict.fromkeys(values))

    def mcp_allowed_origin_values(self) -> list[str]:
        """The ``Origin`` values the MCP transport accepts (DD-15).

        Derived from the same two sources :meth:`mcp_allowed_host_values` composes, so
        enabling one allowlist cannot disable the other. Left unpopulated, the SDK
        defaults it to ``[]``, which -- once ``GW_BASE_URL`` is set and the check turns
        itself on -- refuses a request carrying the deployment's **own** ``Origin`` with
        403 "Invalid Origin header". A same-origin
        browser client was the one caller the hardening was certain to break.

        The two sources know different things, so the scheme is derived rather than
        assumed:

        - ``GW_BASE_URL`` carries a scheme, and it is used. Assuming ``https://`` here
          would refuse the deployment's own ``Origin`` on any internal host served over
          plain HTTP, which is the same bug under a new name.
        - a ``GW_MCP_ALLOWED_HOSTS`` entry carries none, so it yields **both** forms.
        - a ``:*`` port wildcard is preserved, because ``_validate_origin`` supports the
          same suffix as ``_validate_host``: ``dt.internal:*`` becomes
          ``https://dt.internal:*``.

        An absent ``Origin`` is valid to the SDK regardless (``_validate_origin``
        returns ``True`` on a falsy value), so this cannot break a non-browser client.
        An empty result leaves the check off for the same reason the host list does.
        """
        values: list[str] = []
        if self.base_url:
            parsed = urlparse(self.base_url)
            if parsed.netloc and parsed.scheme:
                values.append(f"{parsed.scheme}://{parsed.netloc}")
        for part in self.mcp_allowed_hosts.split(","):
            host = part.strip()
            if host:
                values.append(f"https://{host}")
                values.append(f"http://{host}")
        return list(dict.fromkeys(values))

    def admin_groups(self) -> frozenset[str]:
        """``GW_OIDC_ADMIN_GROUPS`` parsed into a set (FR-I2). Comma-separated, blank
        entries dropped, so an empty or unset value maps every identity to ``member``."""
        return _parse_groups(self.oidc_admin_groups)

    def creator_groups(self) -> frozenset[str]:
        """``GW_OIDC_CREATOR_GROUPS`` parsed into a set (FR-I2). Same shape and same
        fail-safe as :meth:`admin_groups`: unset maps nobody to ``creator``, so adding the
        variable is what turns the mapping on and removing it turns it off."""
        return _parse_groups(self.oidc_creator_groups)


def _parse_groups(raw: str | None) -> frozenset[str]:
    """One parser for both group variables, so they cannot drift in how they treat
    whitespace, blanks, or an unset value."""
    return frozenset(part.strip() for part in (raw or "").split(",") if part.strip())


def _format_pydantic_error(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        field = error["loc"][0] if error["loc"] else "unknown"
        env_var = f"GW_{str(field).upper()}"
        lines.append(f"  {env_var}: {error['msg']}")
    return "\n".join(lines)


def load_settings() -> Settings:
    """Load and validate settings from the environment, failing fast on error.

    Raises ConfigError naming the offending GW_* variable(s) rather than letting a raw
    pydantic ValidationError (which reports field names, not env var names) escape.
    """
    try:
        settings = Settings()
    except ValidationError as exc:
        raise ConfigError("Invalid configuration:\n" + _format_pydantic_error(exc)) from exc

    if settings.auth_mode in ("oidc", "both"):
        if not settings.oidc_issuer:
            raise ConfigError("GW_OIDC_ISSUER: required when GW_AUTH_MODE is 'oidc' or 'both'")
        if not settings.oidc_client_id:
            raise ConfigError("GW_OIDC_CLIENT_ID: required when GW_AUTH_MODE is 'oidc' or 'both'")
        if not settings.base_url:
            # The OIDC redirect_uri is built from this, never from the inbound request
            # (FR-P7): deriving it from the request would reintroduce the same
            # proxy-scheme trust problem GW_COOKIE_SECURE's default exists to avoid.
            raise ConfigError("GW_BASE_URL: required when GW_AUTH_MODE is 'oidc' or 'both'")

    if settings.bootstrap_secret:
        # Every refusal here names the variable to fix and none of them echoes the
        # secret. They are startup errors rather than a refusal at
        # claim time because the caller of a claim is a program that has already
        # provisioned a container: telling it "409" on the one call it makes, for a
        # reason set at boot, is a diagnosis nobody can act on from there.
        if not settings.base_url:
            raise ConfigError(
                "GW_BASE_URL: required when GW_BOOTSTRAP_SECRET is set. The bootstrap "
                "handoff returns absolute mcp_url and sign_in_url values, which cannot "
                "be composed without it."
            )
        if len(settings.bootstrap_secret) < BOOTSTRAP_SECRET_MIN_LENGTH:
            raise ConfigError(
                f"GW_BOOTSTRAP_SECRET: must be at least {BOOTSTRAP_SECRET_MIN_LENGTH} "
                "characters. Generate one with 'openssl rand -base64 32'."
            )
        if settings.auth_mode == "oidc":
            raise ConfigError(
                "GW_AUTH_MODE: cannot be 'oidc' when GW_BOOTSTRAP_SECRET is set. In "
                "'oidc' mode local password sign-in is refused, so the administrator "
                "the claim creates could never sign in at the sign_in_url it returns. "
                "Use 'standalone' or 'both'."
            )
        for name, value in (
            ("GW_BOOTSTRAP_ADMIN_EMAIL", settings.bootstrap_admin_email),
            ("GW_BOOTSTRAP_ADMIN_PASSWORD", settings.bootstrap_admin_password),
        ):
            if value:
                raise ConfigError(
                    f"{name}: cannot be set when GW_BOOTSTRAP_SECRET is set. The "
                    "environment bootstrap creates the administrator at startup, which "
                    "would leave every claim refused as already bootstrapped. Choose "
                    "one bootstrap path."
                )

    if settings.operator_token and len(settings.operator_token) < OPERATOR_TOKEN_MIN_LENGTH:
        # A startup refusal rather than a refusal at read time, for the same reason the
        # bootstrap checks above are: the caller of a usage read is a hosting operator
        # polling on a schedule, and telling it "401" for a reason set at boot is a
        # diagnosis nobody can act on from there. The message names the variable and
        # never echoes the value.
        raise ConfigError(
            f"GW_OPERATOR_TOKEN: must be at least {OPERATOR_TOKEN_MIN_LENGTH} characters. "
            "Generate one with 'openssl rand -base64 32'. Leave it unset, or blank, to "
            "keep the usage endpoint off."
        )

    return settings


def main() -> None:
    """Entry point used by the container to validate config before serving traffic."""
    try:
        load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
