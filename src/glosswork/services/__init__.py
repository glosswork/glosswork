"""Service layer: the single source of truth for business logic (DD-3).

REST and MCP are adapters over these services; neither contains logic of its own.
Every write method takes an :class:`~glosswork.actor.ActorContext` (DD-4) and
emits audit events inside the same transaction as its data changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx2

from glosswork.config import Settings
from glosswork.db import Database
from glosswork.repositories.blobs import FilesystemBlobRepository
from glosswork.repositories.sqlite import (
    SqliteAccessTokenRepository,
    SqliteAgentLabelRepository,
    SqliteAttachmentRepository,
    SqliteAuditRepository,
    SqliteBackupRepository,
    SqliteBlobReferenceRepository,
    SqliteCommentRepository,
    SqliteGrantRepository,
    SqliteInviteRepository,
    SqlitePrincipalRepository,
    SqliteRecordAttachmentRepository,
    SqliteRecordRepository,
    SqliteSavedViewRepository,
    SqliteSchemaRepository,
    SqliteSearchRepository,
    SqliteSessionRepository,
    SqliteSignInCodeRepository,
    SqliteUsageRepository,
)
from glosswork.services.access import AccessService
from glosswork.services.agent_labels import AgentLabelService
from glosswork.services.attachments import AttachmentService
from glosswork.services.audit import AuditService
from glosswork.services.authn import AuthService
from glosswork.services.backup import BackupService
from glosswork.services.bootstrap import BootstrapService
from glosswork.services.changes import ChangeFeedService
from glosswork.services.comments import CommentService
from glosswork.services.csv import CsvService
from glosswork.services.embedding import EmbeddingProvider, build_provider
from glosswork.services.export import ExportService
from glosswork.services.invites import InviteService
from glosswork.services.oidc import (
    JwksSource,
    OidcVerifier,
    PyJwkClientJwksSource,
)
from glosswork.services.oidc_flow import OidcFlowService
from glosswork.services.passwords import PasswordService
from glosswork.services.principals import PrincipalService
from glosswork.services.rate_limit import LoginRateLimiter
from glosswork.services.records import RecordService
from glosswork.services.relay import RelaySender
from glosswork.services.saved_views import SavedViewService
from glosswork.services.schema import SchemaService
from glosswork.services.search import SearchService
from glosswork.services.search_index import SearchIndexService
from glosswork.services.sessions import SessionService
from glosswork.services.sign_in_codes import SignInCodeService
from glosswork.services.tokens import AccessTokenService
from glosswork.services.usage import UsageService
from glosswork.services.workspace import WorkspaceService

ATTACHMENTS_DIRNAME = "attachments"


@dataclass(slots=True)
class ServiceBundle:
    access: AccessService
    schema: SchemaService
    records: RecordService
    comments: CommentService
    attachments: AttachmentService
    changes: ChangeFeedService
    csv: CsvService
    agent_labels: AgentLabelService
    saved_views: SavedViewService
    audit: AuditService
    principals: PrincipalService
    tokens: AccessTokenService
    authn: AuthService
    sessions: SessionService
    oidc_flow: OidcFlowService
    search_index: SearchIndexService
    search: SearchService
    backup: BackupService
    login_limiter: LoginRateLimiter
    export: ExportService
    workspace: WorkspaceService
    bootstrap: BootstrapService
    usage: UsageService
    invites: InviteService
    sign_in_codes: SignInCodeService
    # The email relay's client (change 9): ``None`` unless GW_RELAY_URL and
    # GW_RELAY_TOKEN are both set, which is what keeps a workspace without them exactly
    # as it was. It is the only sender in the product.
    relay: RelaySender | None
    embedding_provider: EmbeddingProvider | None


def build_services(
    db: Database,
    data_dir: Path,
    settings: Settings | None = None,
    jwks_source: JwksSource | None = None,
    oidc_http_client: httpx2.Client | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> ServiceBundle:
    """Assemble the service layer.

    ``settings`` carries the Argon2id cost parameters, the password length floor, and
    the OIDC configuration; it defaults to a plain ``Settings()`` so a test that cares
    about none of those can keep calling ``build_services(db, tmp_path)``.
    ``jwks_source`` is the DD-8-shaped seam that lets the OIDC suite verify a real
    token against a locally generated key set with no network call.
    ``oidc_http_client`` is the equivalent seam for the authorization-code
    exchange: an ``httpx2.Client`` built over ``httpx2.MockTransport`` in tests, so
    that path makes no network call either.

    ``embedding_provider`` is the FR-Q8 seam, shaped exactly like the two above: pass
    a deterministic fake so the queue, worker, and restart tests stay fast and
    model-free, or leave it unset and the real ONNX provider is constructed from
    ``GW_MODEL_DIR`` when ``GW_EMBEDDING_ENABLED`` is true. Constructing it here rather
    than inside the worker is what lets ``SearchIndexService`` report the live
    ``model_id`` on the status endpoint without loading a second copy of the model.
    """
    settings = settings or Settings(data_dir=data_dir)
    schema_repo = SqliteSchemaRepository()
    record_repo = SqliteRecordRepository()
    comment_repo = SqliteCommentRepository()
    audit_repo = SqliteAuditRepository()
    attachment_repo = SqliteAttachmentRepository()
    label_repo = SqliteAgentLabelRepository()
    saved_view_repo = SqliteSavedViewRepository()
    principal_repo = SqlitePrincipalRepository()
    token_repo = SqliteAccessTokenRepository()
    session_repo = SqliteSessionRepository()
    search_repo = SqliteSearchRepository()
    backup_repo = SqliteBackupRepository()
    blob_ref_repo = SqliteBlobReferenceRepository()
    grant_repo = SqliteGrantRepository()
    record_attachment_repo = SqliteRecordAttachmentRepository()
    blob_repo = FilesystemBlobRepository(data_dir / ATTACHMENTS_DIRNAME)

    if embedding_provider is None and settings.embedding_enabled:
        # DD-32: located by configuration, never downloaded. Fails fast with a
        # ConfigError naming GW_MODEL_DIR when the image or the checkout has no model.
        embedding_provider = build_provider(settings.model_dir, settings.embedding_model)
    search_index = SearchIndexService(
        db,
        search_repo,
        record_repo,
        embedding_enabled=settings.embedding_enabled,
        configured_model=settings.embedding_model,
        provider=embedding_provider,
        audit_repo=audit_repo,
    )
    # DD-11: the only place that answers an authorization question about an object
    # type. Built before every service that enforces with it, and passed to each.
    access = AccessService(db, grant_repo, schema_repo, principal_repo, audit_repo)

    search = SearchService(
        db,
        schema_repo,
        record_repo,
        comment_repo,
        principal_repo,
        search_repo,
        access,
        embedding_enabled=settings.embedding_enabled,
        provider=embedding_provider,
    )

    schema = SchemaService(
        db,
        schema_repo,
        record_repo,
        audit_repo,
        access,
        search_index,
        principal_repo,
        label_repo,
    )
    records = RecordService(
        db,
        schema_repo,
        record_repo,
        audit_repo,
        access,
        attachment_repo,
        search_index,
        record_attachment_repo,
        principal_repo,
        label_repo,
    )
    comments = CommentService(
        db, schema_repo, record_repo, comment_repo, audit_repo, access, search_index
    )
    # Constructed before ``attachments`` because that service mints the upload
    # ticket through it; it depends on nothing built after this point.
    tokens = AccessTokenService(db, token_repo, principal_repo, audit_repo)
    attachments = AttachmentService(
        db,
        attachment_repo,
        blob_repo,
        audit_repo,
        access,
        record_attachment_repo,
        blob_ref_repo,
        # The per-file cap arrives as a constructor argument, exactly as
        # ``LoginRateLimiter`` receives its budgets below, so the service enforces it.
        max_attachment_bytes=settings.max_attachment_bytes,
        # DD-29: the service answers ``download_url``, so neither adapter reads
        # ``Settings`` to say where an attachment's bytes are.
        base_url=settings.base_url,
        # DD-16: and it answers ``upload_url`` and mints the ticket that reaches
        # it, for the same reason. The token service does the minting; this one composes
        # the document, because it owns the route and the per-file ceiling.
        tokens=tokens,
        upload_ticket_ttl_seconds=settings.upload_ticket_ttl_seconds,
    )
    changes = ChangeFeedService(db, schema_repo, audit_repo, access)
    csv_service = CsvService(
        records,
        schema,
        max_import_bytes=settings.max_csv_import_bytes,
        max_import_rows=settings.max_csv_import_rows,
        max_export_rows=settings.max_csv_export_rows,
    )
    agent_labels = AgentLabelService(db, label_repo, access)
    saved_views = SavedViewService(db, schema_repo, saved_view_repo, audit_repo, access)
    audit = AuditService(db, audit_repo, schema_repo, record_repo, access)
    passwords = PasswordService(settings)
    principals = PrincipalService(
        db, principal_repo, token_repo, audit_repo, passwords, session_repo
    )
    oidc = OidcVerifier(settings, jwks_source or PyJwkClientJwksSource(settings.oidc_issuer or ""))
    authn = AuthService(settings, principals, oidc)
    sessions = SessionService(db, session_repo, principal_repo, audit_repo, settings)
    oidc_flow = OidcFlowService(settings, oidc_http_client)
    backup = BackupService(db, data_dir, backup_repo, blob_repo, audit_repo)
    # One limiter per application, which is what an in-process fixed window means
    # (FR-I1): it is built here so it shares the bundle's lifetime rather than
    # being re-created per request, which would count every attempt as the first.
    login_limiter = LoginRateLimiter(
        settings.login_max_attempts,
        settings.login_ip_max_attempts,
        settings.login_window_seconds,
    )
    export = ExportService(
        db,
        schema_repo,
        record_repo,
        comment_repo,
        saved_view_repo,
        label_repo,
        audit_repo,
    )
    # The sidebar's one bounded read. Built from the same principal and
    # agent-label repositories every other service shares, plus ``settings`` for the
    # operator-configured name -- no new repository, no migration.
    workspace = WorkspaceService(db, principal_repo, label_repo, settings)
    # DD-37: the first administrator, handed back over HTTP exactly once. Built
    # last because it composes three services above it -- it creates a principal, mints
    # that principal's token, and answers with the workspace's two addresses -- and owns
    # no repository of its own beyond the principal count its refusal reads.
    bootstrap = BootstrapService(db, principal_repo, principals, tokens, workspace, settings)
    # DD-39: the operator's view of volume. Built beside ``bootstrap`` and for the
    # same reason -- both are configured-secret paths that resolve no caller -- and it
    # takes ``workspace`` because that service already owns the definition of two of the
    # numbers it reports and both are already shown to the tenant in the sidebar. Its
    # flushing thread is started by the application lifespan, not here: ``build_services``
    # is called by tests that never start one, and a service that starts a thread on
    # construction would leak one per bundle.
    usage = UsageService(db, SqliteUsageRepository(), principal_repo, workspace, settings)
    # Change 9. Built only when both relay settings are set; every code and invite route
    # answers ``feature_disabled`` without it.
    relay = RelaySender(settings) if settings.email_codes_enabled else None
    invites = InviteService(
        db, SqliteInviteRepository(), principal_repo, principals, audit_repo, relay
    )
    sign_in_codes = SignInCodeService(
        db, SqliteSignInCodeRepository(), principal_repo, invites, relay
    )
    return ServiceBundle(
        access=access,
        schema=schema,
        records=records,
        comments=comments,
        attachments=attachments,
        changes=changes,
        csv=csv_service,
        agent_labels=agent_labels,
        saved_views=saved_views,
        audit=audit,
        principals=principals,
        tokens=tokens,
        authn=authn,
        sessions=sessions,
        oidc_flow=oidc_flow,
        search_index=search_index,
        search=search,
        backup=backup,
        login_limiter=login_limiter,
        export=export,
        workspace=workspace,
        bootstrap=bootstrap,
        usage=usage,
        invites=invites,
        sign_in_codes=sign_in_codes,
        relay=relay,
        embedding_provider=embedding_provider,
    )
