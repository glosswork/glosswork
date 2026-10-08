"""FastAPI application factory and entry point."""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route
from starlette.staticfiles import StaticFiles

from glosswork.actor import bootstrap_actor
from glosswork.auth import PatTokenResolver, TokenResolver
from glosswork.config import ConfigError, Settings, load_settings
from glosswork.db import Database, check_search_extensions
from glosswork.errors import (
    GlossworkError,
    InternalError,
    RateLimitedError,
    ValidationFailedError,
    error_envelope,
    http_status_for,
)
from glosswork.logging import configure_logging, get_logger
from glosswork.mcp_server import create_mcp_server
from glosswork.middleware import (
    MCP_PATH,
    McpPayloadEnvelope,
    RequestContextMiddleware,
    is_api_path,
    is_mcp_path,
)
from glosswork.migrations import pending_migrations, run_migrations
from glosswork.repositories.sqlite import (
    SqliteCommentRepository,
    SqliteRecordRepository,
    SqliteSchemaRepository,
    SqliteSearchRepository,
)
from glosswork.services import ServiceBundle, build_services
from glosswork.services.embedding import EmbeddingProvider
from glosswork.services.embedding_worker import EmbeddingWorker
from glosswork.services.oidc import JwksSource
from glosswork.timeutil import format_datetime

DATABASE_FILENAME = "glosswork.sqlite3"


def _resolve_frontend_dist_dir() -> Path:
    """Locate web/dist (FR-P1: single container, single process).

    Two layouts both name it relative to something stable rather than an
    environment variable no one has asked for yet:

    - Source checkout (local dev, tests, `uv run uvicorn ... --reload`): this
      module lives at ``<repo>/src/glosswork/app.py``, so two parents up is
      the repo root, matching the ``Path(__file__).resolve().parents[...]``
      convention the test suite already uses to find the repo root.
    - The built image: the runtime stage installs the package non-editable
      (`uv sync --no-editable`), so `__file__` resolves inside the venv's
      site-packages, nowhere near `/app/web/dist`. But the container's
      `ENTRYPOINT` always runs with `WORKDIR /app`, the exact directory the
      Dockerfile `COPY --from=frontend-builder /app/web/dist /app/web/dist`
      target, so `web/dist` relative to the current working directory finds it
      there instead.

    Whichever candidate actually exists on disk wins; if neither does (e.g. the
    Python test suite, which never builds `web/`), the caller skips registering
    static serving entirely.
    """
    from_source_checkout = Path(__file__).resolve().parents[2] / "web" / "dist"
    if from_source_checkout.is_dir():
        return from_source_checkout
    return Path.cwd() / "web" / "dist"


FRONTEND_DIST_DIR = _resolve_frontend_dist_dir()


def _ensure_bootstrap_admin(services: ServiceBundle, settings: Settings, logger: Any) -> None:
    """Create the first administrator on first run (FR-I1).

    Three outcomes, all of them non-fatal:

    - both ``GW_BOOTSTRAP_ADMIN_EMAIL`` and ``GW_BOOTSTRAP_ADMIN_PASSWORD`` set and no
      active admin user exists: create one and log that it happened. The password is
      never logged.
    - an admin user already exists: a no-op, so a second start does nothing. This is
      what makes the variables safe to leave set in a compose file.
    - the variables are unset and no admin exists: log how to create one with the
      operator CLI, and start anyway. Refusing to start would strand a deployment
      whose administrator is about to be created out of band, which is the ordinary
      path (``python -m glosswork.admin create-admin``).
    """
    if services.principals.active_admin_user_count() > 0:
        return
    email = settings.bootstrap_admin_email
    password = settings.bootstrap_admin_password
    if not email or not password:
        logger.info(
            "bootstrap_admin_absent",
            hint=(
                "No administrator exists yet. Create one with "
                "'python -m glosswork.admin create-admin --email <you> --password <pw>', "
                "or set GW_BOOTSTRAP_ADMIN_EMAIL and GW_BOOTSTRAP_ADMIN_PASSWORD and restart."
            ),
        )
        return
    actor = bootstrap_actor(str(uuid.uuid4()))
    principal = services.principals.create_user(
        actor,
        email=email,
        display_name=email.split("@", 1)[0],
        role="admin",
        auth_provider="local",
        password=password,
    )
    # The email and the principal id, never the password.
    logger.info("bootstrap_admin_created", principal_id=principal.id, email=principal.email)


def _reconcile_indexes_at_startup(services: ServiceBundle, logger: Any) -> None:
    """Bring every per-type index on ``records`` in line with the schema (FR-R6).

    Two things at once, in one write transaction, because they cannot safely be
    separated. It retires any index carrying an old-style name -- SQLite cannot rename
    one, so an upgrade has to drop and recreate -- and then reconciles both the
    per-field indexes and the sort composites for every live object type. A drop that
    committed without its matching create would leave a serving deployment with no
    per-type indexes at all, which is why this hook's non-fatal contract makes atomicity
    a requirement rather than a nicety.

    The reconcilers are what let a filtered, sorted page return without SQLite sorting
    the whole matching set (``sqlexpr.sort_index_ddl`` carries the measurement) and what
    make the unique partial expression index the hard enforcement docs/DATA_MODEL.md
    section 5 says it is. ``SchemaService`` maintains both on every schema change, so on
    a deployment already running this version this call finds nothing to do and costs a
    few ``sqlite_master`` reads per object type; on the first start after the upgrade it
    rebuilds the type's indexes once, which docs/DEPLOYMENT.md bounds.

    Doing it here rather than in a numbered migration is deliberate: the desired set is
    derived from the schema, not from a fixed DDL script, and the migration runner takes
    static SQL. Reconciling at startup also repairs drift, which a one-shot migration
    could not.

    Non-fatal, like the sweep below: a deployment that cannot build an index should
    still serve, more slowly, rather than refuse to start.
    """
    try:
        dropped, created = services.schema.retire_legacy_index_names()
    except Exception as exc:  # pragma: no cover - defensive: never block startup
        logger.warning("index_reconcile_failed", error=f"{type(exc).__name__}: {exc}")
        return
    if dropped or created:
        logger.info(
            "indexes_reconciled",
            dropped=len(dropped),
            created=len(created),
            dropped_names=dropped,
            created_names=created,
        )


def _report_missing_base_url_at_startup(settings: Settings, logger: Any) -> None:
    """Warn once when ``GW_BASE_URL`` is unset (DD-16).

    Shaped exactly like :func:`_report_reserved_key_collisions_at_startup` beside it, and
    for the same reason: the deployment is degraded rather than down, so this blocks
    nothing and fails no probe. A browser-only deployment is entirely unaffected -- the
    SPA joins relative paths to the origin it loaded from.

    What is degraded is the agent surface. Without the setting, every agent-facing URL
    this endpoint publishes is a bare path, and a path is useless to a model that cannot
    see the endpoint's own origin either; ``create_attachment_upload`` therefore refuses
    outright rather than hand back half a request. The origin is derived from this
    setting and never from a request's ``Host``, because a poisoned header telling an
    agent where to POST a file is worse than a refusal, which is why
    the fix is configuration rather than inference.
    """
    if settings.base_url:
        return
    logger.warning(
        "base_url_unset",
        setting="GW_BASE_URL",
        hint=(
            "GW_BASE_URL is not set, so agent-facing URLs cannot be absolute: "
            "describe_capabilities publishes bare paths for download_url and "
            "upload_url, and create_attachment_upload refuses to mint an upload "
            "ticket. Set GW_BASE_URL to this deployment's external origin (e.g. "
            "https://tracker.example.com). The browser UI is unaffected."
        ),
    )


def _report_read_only_at_startup(settings: Settings, logger: Any) -> None:
    """Say once, at startup, that this deployment refuses writes (DD-38).

    Otherwise nothing but a refused write tells an operator the flag is on. Logged at
    ``warning`` beside ``base_url_unset``, for the same reason: the deployment is degraded
    by configuration rather than down, so it blocks nothing and fails no probe. Only
    whether a subscribe URL is set is logged, not the URL itself, which is the hosting
    operator's to own.
    """
    if not settings.read_only:
        return
    logger.warning(
        "read_only_mode",
        setting="GW_READ_ONLY",
        subscribe_url_set=settings.subscribe_url is not None,
    )


def _report_trial_end_at_startup(settings: Settings, logger: Any) -> None:
    """Say once, at startup, that this workspace is on a trial and when it ends (change 30).

    At ``info`` rather than ``warning``: a trial is how a hosted workspace is meant to run,
    not a degraded one. Only whether a subscribe URL is set is logged, as
    ``read_only_mode`` does, so an operator can see from the log that a banner will count
    down with no link.
    """
    if settings.trial_ends_at is None:
        return
    logger.info(
        "trial_end_set",
        setting="GW_TRIAL_ENDS_AT",
        trial_ends_at=format_datetime(settings.trial_ends_at),
        subscribe_url_set=settings.subscribe_url is not None,
    )


def _report_reserved_key_collisions_at_startup(services: ServiceBundle, logger: Any) -> None:
    """Warn about any field whose key shadows a system pseudo-field (DD-20).

    A new one is refused; a deployment upgrading into the rule may already hold one,
    created in good faith before the rule existed. Nothing rewrites it -- renaming a field key
    would rewrite ``records.data`` over user data with no undo, and flipping precedence
    so the column wins would silently change every filter and saved view that already
    reads it (DD-20). So the operator is told, once per start, and decides.

    Non-fatal like its two neighbours, and for the same reason: a deployment with a
    collision is degraded, not down, so ``/readyz`` does not fail on it either.
    """
    try:
        collisions = services.schema.reserved_key_collisions()
    except Exception as exc:
        # Not `pragma: no cover` like its neighbours: "never blocks startup" is a claim, so
        # a test drives this branch and asserts `/readyz` is still 200
        # (`test_a_report_that_raises_does_not_block_startup`).
        logger.warning("reserved_key_collision_scan_failed", error=f"{type(exc).__name__}: {exc}")
        return
    for object_type_key, field_key in collisions:
        logger.warning(
            "reserved_key_collision",
            object_type=object_type_key,
            field=field_key,
            hint=(
                f"Field {object_type_key}.{field_key} shadows the system pseudo-field "
                f"{field_key!r}: filters and sorts on this type read the field, while "
                "search across several types and a fields: [...] projection read the "
                f"system column, and describe_object_type returns {field_key!r} twice, "
                "once under 'fields' and once under 'system_fields', with two different "
                "types. To resolve it, propose a delete_field for this field and re-add "
                "it under another key; the deprecation window that proposal already has "
                "is what preserves the data while readers move over."
            ),
        )


def _sweep_orphan_blobs_at_startup(services: ServiceBundle, logger: Any) -> None:
    """The bounded startup half of the orphan blob sweep (FR-P2).

    The concrete case this exists for is a restore: DD-36 copies the blob tree
    *after* the database snapshot, so a blob written during the backup arrives on the
    restored volume with no ``attachments`` row pointing at it. Sweeping once at
    startup is where that gets cleaned up without an operator having to know it
    happened.

    Bounded (``DEFAULT_SWEEP_LIMIT``) and non-fatal, both deliberately. Startup must
    not become a directory walk of unbounded length, and a deployment whose
    attachment tree is unreadable should still come up and serve — reclaiming disk is
    housekeeping, not a precondition for correctness. An operator who wants the whole
    tree swept has ``POST /api/v1/admin/blobs/sweep``, which takes a higher limit.
    """
    actor = bootstrap_actor(str(uuid.uuid4()))
    try:
        result = services.attachments.sweep_orphan_blobs(actor)
    except Exception as exc:  # pragma: no cover - defensive: never block startup
        logger.warning("orphan_blob_sweep_failed", error=f"{type(exc).__name__}: {exc}")
        return
    if result["deleted"]:
        logger.info("orphan_blob_sweep_startup", **result)


def _clear_backup_staging_at_startup(services: ServiceBundle, logger: Any) -> None:
    """Remove a staged backup snapshot a killed process left behind (DD-36).

    A backup stages a full copy of the database under the data directory and removes it
    when its response ends. A process that is killed mid-backup removes nothing, and
    nothing else reads that directory, so this is the only thing that ever would. Here
    and nowhere else, because before the lifespan has started no backup can be in
    flight: the image runs one process.

    Non-fatal, like the orphan blob sweep beside it: reclaiming disk is housekeeping,
    not a precondition for serving.
    """
    try:
        removed = services.backup.clear_staging()
    except Exception as exc:  # pragma: no cover - defensive: never block startup
        logger.warning("backup_staging_clear_failed", error=f"{type(exc).__name__}: {exc}")
        return
    if removed:
        logger.info("backup_staging_cleared", removed=removed)


def create_app(
    settings: Settings,
    migrate_on_startup: bool = True,
    token_resolver: TokenResolver | None = None,
    oidc_http_client: httpx2.Client | None = None,
    jwks_source: JwksSource | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> FastAPI:
    configure_logging(settings.log_level)
    logger = get_logger(__name__)

    # The MCP server is built once per process and mounted before the lifespan runs;
    # it reaches the services lazily through app.state (DD-3, DD-8).
    def _services() -> ServiceBundle:
        services: ServiceBundle = app.state.services
        return services

    # Built once and shared by both surfaces (DD-8): the MCP adapter and
    # RequestContextMiddleware resolve every call's identity through this same TokenResolver
    # instance. Which resolver is built here is the only thing either surface depends on,
    # which is the payoff DD-8 was designed for. The resolver reaches the services lazily
    # through the same closure the MCP factory uses, because the bundle is not built until
    # the lifespan runs.
    resolver = token_resolver or PatTokenResolver(_services)
    mcp = create_mcp_server(_services, resolver)
    # DD-5 mount shape: JSON responses (every tool is a short synchronous call),
    # stateless (the server never initiates requests). The SDK app is attached as a
    # route on the exact ``/mcp`` path rather than a ``Mount("/mcp")`` so that the
    # documented URL (docs/MCP_TOOLS.md section 1) is served directly instead of
    # answering with a 307 to ``/mcp/``.
    #
    # DNS-rebinding protection is the SDK's configurable ``Host`` allowlist, turned on
    # and nothing more. The allowlist comes from ``GW_BASE_URL`` plus ``GW_MCP_ALLOWED_HOSTS``
    # and the check enables itself only when that list is non-empty -- see
    # ``Settings.mcp_allowed_host_values`` for why an empty list must not mean "reject
    # everything". The state is logged at startup either way.
    #
    # ``allowed_origins`` is derived from the same two sources (DD-15). Left unpopulated,
    # the SDK defaults it to ``[]`` -- so turning the check on by setting ``GW_BASE_URL``
    # would refuse a request carrying the deployment's own ``Origin``. One setting, both
    # lists, so enabling one cannot disable the other.
    allowed_hosts = settings.mcp_allowed_host_values()
    allowed_origins = settings.mcp_allowed_origin_values()
    #
    # ``max_request_body_size`` is passed explicitly (DD-18), and the value is the same
    # ``settings.max_request_bytes`` the REST edge caps on. Not passed, the surface takes
    # the SDK's 4 MiB default, body-capped by accident whatever REST does. One setting
    # governs both, which is what a test asserts so the two cannot drift.
    mcp_app = mcp.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        stateless_http=True,
        max_request_body_size=settings.max_request_bytes,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=bool(allowed_hosts),
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        ),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        db = Database.connect(settings.data_dir / DATABASE_FILENAME)
        # Before the migration runner, not after: migration 6 creates the
        # ``vec_embeddings`` virtual table and would otherwise fail with a bare
        # "no such module: vec0". Deliberately **not** gated on
        # GW_EMBEDDING_ENABLED -- migration 6 runs unconditionally, so sqlite-vec is
        # a startup dependency of every deployment, disabled or not
        # (docs/DATA_MODEL.md section 13).
        check_search_extensions(db)
        if migrate_on_startup:
            applied = run_migrations(db)  # automatic and idempotent (FR-P6)
            if applied:
                logger.info("migrations_applied", numbers=applied)
        app.state.db = db
        app.state.services = build_services(
            db,
            settings.data_dir,
            settings,
            jwks_source=jwks_source,
            oidc_http_client=oidc_http_client,
            # The FR-Q8 seam, threaded exactly like the two above: a test can
            # run the real app with embedding genuinely enabled over a deterministic
            # provider, instead of paying for inference or proving the queue's
            # behavior one layer below the surface that exposes it.
            embedding_provider=embedding_provider,
        )
        if not pending_migrations(db):
            # Only against a fully migrated database. `/readyz` exists precisely so an
            # operator can start the process against an unmigrated one and be told what
            # is missing (FR-P4), and the bootstrap check reads `principals` and
            # `access_tokens`, which may not exist yet on that path.
            _ensure_bootstrap_admin(app.state.services, settings, logger)
            _reconcile_indexes_at_startup(app.state.services, logger)
            _report_reserved_key_collisions_at_startup(app.state.services, logger)
            _sweep_orphan_blobs_at_startup(app.state.services, logger)
        # Outside the migration guard too: it reads a directory and no table.
        _clear_backup_staging_at_startup(app.state.services, logger)
        # Outside the migration guard: this reads configuration and no table, so it is
        # worth saying even to an operator starting against an unmigrated database.
        _report_missing_base_url_at_startup(settings, logger)
        _report_read_only_at_startup(settings, logger)
        _report_trial_end_at_startup(settings, logger)
        logger.info(
            "startup",
            data_dir=str(settings.data_dir),
            auth_mode=settings.auth_mode,
            mcp_host_allowlist=allowed_hosts or None,
        )
        # The embedding worker is one daemon thread, started only when embedding is
        # enabled (DD-34: with it off, no worker starts and writes enqueue nothing, so
        # the queue cannot grow unbounded) and stopped gracefully below after
        # finishing its current batch.
        worker: EmbeddingWorker | None = None
        services = app.state.services
        if settings.embedding_enabled and services.embedding_provider is not None:
            worker = EmbeddingWorker(
                db,
                SqliteSearchRepository(),
                SqliteSchemaRepository(),
                SqliteRecordRepository(),
                SqliteCommentRepository(),
                services.embedding_provider,
            )
            worker.start()
        app.state.embedding_worker = worker
        # DD-39. The usage counter's flushing thread. Started here rather than in
        # ``build_services`` because tests build bundles without ever running a lifespan,
        # and a service that started a thread on construction would leak one per bundle.
        services.usage.start()
        # A mounted sub-app's lifespan never runs, so the host enters the MCP
        # session manager itself (DD-5).
        async with mcp.session_manager.run():
            yield
        # The usage flush goes **first**, and with a budget of its own. A stop has a
        # platform grace to fit inside -- as little as 5 s on some platforms -- and
        # ``EmbeddingWorker.stop`` below already claims up to 5 s of it for one worst-case
        # source. The usage flush is one transaction of a few dozen single-row upserts, so
        # it is sub-millisecond unless it is queued behind another writer, and going first
        # means it is not queued behind the worker's last write. Its own timeout is 1.5 s
        # and a timeout is not a failure: what is lost is the counts since the last
        # interval, which is strictly better than hanging the drain past its grace period.
        services.usage.stop()
        if worker is not None:
            worker.stop()
        db.close()
        logger.info("shutdown")

    # ``redoc_url`` and the Swagger OAuth2 redirect are disabled so the set of routes
    # that legitimately carry no scope declaration is exactly the list named in
    # ``scopes.SCOPE_EXEMPT_PATHS`` (the allowlist is asserted to be exactly those
    # entries, so a new unprotected route cannot be waved through by widening it).
    app = FastAPI(
        title="Glosswork",
        lifespan=lifespan,
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=None,
    )
    # The auth routes read cookie attributes (GW_COOKIE_SECURE) and the OIDC
    # redirect_uri (GW_BASE_URL) from this (DD-9, DD-10); routes are thin
    # adapters (DD-3), so it is exposed the same way the service bundle is rather than
    # threaded through a second closure.
    app.state.settings = settings
    app.add_middleware(
        RequestContextMiddleware,
        token_resolver=resolver,
        get_services=_services,
        max_request_bytes=settings.max_request_bytes,
    )

    @app.exception_handler(GlossworkError)
    def _glosswork_error_handler(request: Request, exc: GlossworkError) -> JSONResponse:
        """The one exception handler mapping GlossworkError subclasses onto the
        consistent error envelope (FR-A4): machine-readable code, human message,
        structured details. No route below this may catch GlossworkError itself.
        The MCP adapter builds its error results from the same ``error_envelope``, and
        ``RequestContextMiddleware`` — which resolves the credential outside the
        router, and so outside this handler's reach — shapes its 401 through the same
        ``error_envelope`` and ``http_status_for`` table (failure is uniform
        across surfaces).

        The one error carrying a header is ``RateLimitedError`` (FR-I1): a 429
        without ``Retry-After`` tells a caller to back off without saying how long,
        which in practice means retrying immediately. Adding it here rather than in
        the login route keeps the "no route shapes its own error" rule intact."""
        headers: dict[str, str] | None = None
        if isinstance(exc, RateLimitedError):
            headers = {"Retry-After": str(exc.retry_after_seconds)}
        return JSONResponse(
            {"error": error_envelope(exc)}, status_code=http_status_for(exc), headers=headers
        )

    @app.exception_handler(RequestValidationError)
    def _request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        """FastAPI's request validation, answered in the project envelope.

        FastAPI's default handler returns its own ``{"detail": [...]}`` shape and echoes
        the offending input under ``detail[].input``. Three defects in one response, and
        one handler closes all three:

        1. **The shape.** Every other failure on this surface is
           ``{"error": {code, message, details}}`` (FR-A4). This one was not, so a client
           parsing errors needed a second branch for the one case it was most likely to
           hit.
        2. **The echo.** ``input`` is the request body as parsed, so a malformed login
           body came back carrying whatever was in the password position, verbatim, in a
           response and in any log that captured it.
        3. **The recursion.** A deeply nested body made FastAPI's serializer recurse
           while encoding that same ``input``, inside FastAPI's own validation handler --
           which is why no application handler caught it first and the caller got a 500.
           It is closed here because this handler **never serializes the input**, not
           because it catches ``RecursionError``.

        ``loc`` and ``msg`` are kept, so a caller still learns exactly what to fix; both
        are stringified, so nothing from the body rides along in a type this response
        would then have to encode.
        """
        details = [
            {"loc": [str(part) for part in error.get("loc", ())], "msg": str(error.get("msg", ""))}
            for error in exc.errors()
        ]
        failed = ValidationFailedError(
            "This request's body or parameters did not validate. See details.errors for "
            "the location and reason of each failure.",
            errors=details,
        )
        return JSONResponse({"error": error_envelope(failed)}, status_code=http_status_for(failed))

    @app.exception_handler(Exception)
    def _unclassified_error_handler(request: Request, exc: Exception) -> JSONResponse:
        """Everything the handler above did not classify (DD-19).

        Without it, an unclassified exception reaches Starlette's
        ``ServerErrorMiddleware``, whose 21-byte plain-text body would be the REST answer
        while MCP's equivalent path returned ``str(exc)``. Both surfaces return the same
        ``internal_error`` envelope carrying only the request id, so "failure is uniform
        across surfaces" holds for the unclassified case too.

        Three consequences of ``ServerErrorMiddleware`` **always re-raising** after a
        handler runs (``starlette/middleware/errors.py``: "We always continue to raise
        the exception"):

        1. The exception still propagates afterward, so uvicorn logs the traceback a
           second time. Accepted: the structured application log is the one an operator
           greps, and dropping ``exc_info`` here to avoid a duplicate in uvicorn's
           plain-text log is the wrong trade.
        2. ``RequestContextMiddleware`` is added *inside* ``ServerErrorMiddleware``, so
           this response bypasses its ``send_wrapper`` and carries **no**
           ``x-request-id`` header, unlike every other error. The id therefore rides in
           the body, which it can: ``scope["state"]`` is populated before dispatch and
           the same dict is visible here.
        3. A test of this path must build
           ``TestClient(app, raise_server_exceptions=False)``, or the re-raise surfaces
           in the test instead of the envelope.

        The access log line is unaffected, and by construction rather than by luck:
        ``RequestContextMiddleware`` wraps dispatch in ``try``/``finally`` with
        ``status_code`` initialized to 500.
        """
        request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
        get_logger("glosswork.app").error(
            "unclassified_exception",
            request_id=request_id,
            path=request.url.path,
            method=request.method,
            exc_info=exc,
        )
        internal = InternalError(request_id)
        return JSONResponse(
            {"error": error_envelope(internal)}, status_code=http_status_for(internal)
        )

    from glosswork.routes import register_routes

    register_routes(app)
    # No ``GET`` (DD-15): that verb is the SSE stream, and this deployment runs
    # ``stateless_http=True`` with ``json_response=True`` and never sends a
    # server-initiated message, so the handler serves nothing. Worse, it could not be
    # gated -- ``McpAdapter.middleware`` sees JSON-RPC methods, not HTTP verbs -- so an
    # unauthenticated GET held a connection open indefinitely, there being no idle
    # timeout in stateless mode. Refusing at the router with 405 is where a handler that
    # serves nothing belongs; the SDK client never issues it, because
    # ``handle_get_stream`` short-circuits while ``session_id`` is None. ``DELETE``
    # stays, since removing it changes nothing any client does.
    app.router.routes.append(
        Route(
            MCP_PATH,
            # Wrapped so the SDK's 21-byte plain-text 413 is replaced by the project
            # envelope: without it REST would answer with a ``payload_too_large``
            # envelope while MCP kept the plain text, which is the
            # asymmetry DD-19 removed for the unclassified case.
            endpoint=McpPayloadEnvelope(mcp_app, settings.max_request_bytes),
            methods=["POST", "DELETE"],
            include_in_schema=False,
        )
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Liveness: the process is up and able to respond."""
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(request: Request) -> JSONResponse:
        """Readiness accounts for pending migrations (FR-P4)."""
        db: Database | None = getattr(request.app.state, "db", None)
        if db is None:
            return JSONResponse(
                {"status": "not_ready", "reason": "database not initialized"}, status_code=503
            )
        pending = [m.number for m in pending_migrations(db)]
        if pending:
            return JSONResponse(
                {"status": "not_ready", "pending_migrations": pending}, status_code=503
            )
        return JSONResponse({"status": "ok"})

    # Serve the built frontend (FR-P1: single container, single process). Skipped
    # entirely when web/dist hasn't been built (e.g. the Python test suite, which
    # never runs `npm --prefix web run build`), so `uv run pytest` is unaffected.
    # Registered last and after every other route: Starlette/FastAPI routing is
    # registration-order-sensitive, and the catch-all below must not shadow
    # `/api/v1/*`, `/mcp`, `/healthz`, `/readyz`, `/openapi.json`, `/docs`, or
    # `/redoc`.
    if FRONTEND_DIST_DIR.is_dir():
        index_html = FRONTEND_DIST_DIR / "index.html"
        assets_dir = FRONTEND_DIST_DIR / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

        @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
        def _serve_frontend(full_path: str) -> FileResponse | JSONResponse:
            """SPA fallback: any GET that matched no earlier route serves
            index.html so client-side routing (react-router) works on a hard
            refresh of a nested route (e.g. /initiative/INIT-014). Paths under
            the MCP surface are excluded so an unmatched ``/mcp/...`` request
            still 404s instead of returning HTML (docs/MCP_TOOLS.md section 1;
            matches the pre-existing MCP transport test).

            Paths under ``/api/`` are excluded for the same reason. A typo'd or
            removed API route would answer ``200 text/html`` here, which is worse
            than the MCP case: an agent reading a status code sees a healthy
            deployment, and so did thirteen assertions in this repository, which
            passed against routes that did not exist. The
            fallback exists only when ``web/dist`` sits beside the source, so
            this was true in the image and after any local frontend build and
            false in a frontend-free job, which is why no test saw it. It also
            needed a credential to see: uncredentialed, ``/api/`` answers 401
            before the router ever reaches this handler.

            The predicate is exactly ``is_api_path``, the same one
            ``enforce_scope`` uses, so the two cannot drift apart. It is a plain
            ``startswith("/api/")``, which means ``//api/...``, ``/API/...``, a
            percent-encoded slash and the bare ``/api`` all still fall through to
            index.html. None of those is a route and none is written by a test;
            ``tests/test_spa_fallback.py`` pins them so the edges read as chosen.
            """
            path = "/" + full_path
            if is_mcp_path(path) or is_api_path(path):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            return FileResponse(index_html)

    return app


def _load_settings_or_exit() -> Settings:
    try:
        return load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)


app = create_app(_load_settings_or_exit())
