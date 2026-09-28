"""Attachment upload and download (docs/DATA_MODEL.md section 8).

Every upload gets a fresh ``attachments`` row, even when the content hash matches an
existing row: uploads are per-use, blobs are content-addressed. Uploading identical
content twice therefore produces two rows and exactly one file on disk.

Every entry point takes the caller's ``ActorContext``, as every other service does.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import uuid
from collections.abc import Iterator
from datetime import datetime
from email.message import Message
from typing import TYPE_CHECKING, Any

from sqlalchemy import Connection

from glosswork.actor import ActorContext
from glosswork.db import Database
from glosswork.errors import NotFoundError, ValidationFailedError
from glosswork.logging import get_logger
from glosswork.repositories.interfaces import (
    AttachmentRepository,
    AuditRepository,
    BlobReferenceRepository,
    BlobRepository,
    RecordAttachmentRepository,
)
from glosswork.repositories.models import AttachmentRow
from glosswork.services.access import AccessService
from glosswork.services.base import make_event
from glosswork.timeutil import format_datetime, utc_now

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    from glosswork.services.tokens import AccessTokenService

logger = get_logger(__name__)

# How many blobs one sweep examines before stopping. The startup pass is bounded
# because it runs before the process serves traffic (FR-P4's readiness is what an
# operator watches) and a volume with a large attachment tree must not turn a restart
# into a directory walk of unbounded length. An operator who wants the whole tree
# swept presses the admin button, which passes a higher limit; either way the sweep
# reports what it examined so "clean" is distinguishable from "gave up".
DEFAULT_SWEEP_LIMIT = 1000

# What an unparseable or absent content type becomes.
DEFAULT_CONTENT_TYPE = "application/octet-stream"

# DD-29. The custom scheme an MCP client reads an attachment's bytes through.
# It lives here, in the service layer, because both the capability document (a service)
# and the MCP adapter publish it and neither may import the other.
ATTACHMENT_URI_SCHEME = "attachment"
ATTACHMENT_URI_TEMPLATE = f"{ATTACHMENT_URI_SCHEME}://{{attachment_id}}"

# The path half of a download URL. One definition, so ``download_url`` and the
# pattern published in ``describe_capabilities`` cannot describe different routes.
DOWNLOAD_PATH = "/api/v1/attachments/{attachment_id}/download"

# The upload route's path, for the same reason: ``upload_url``, the ``curl`` line an
# agent is handed, and the ``upload_route`` string ``describe_capabilities`` publishes
# all read this, so none of them can describe a route the service does not serve.
UPLOAD_PATH = "/api/v1/attachments"
UPLOAD_FIELD = "file"

# DD-16. The one capability value upload tickets carry. ``capability`` is a general
# mechanism; a second value is a new decision with its own blast-radius argument, and
# ``tests/test_upload_tickets.py`` pins the route table by equality so adding one is
# deliberate rather than incidental.
ATTACHMENT_UPLOAD_CAPABILITY = "attachment_upload"

# What a ticket's ``filename`` may not contain.
#
# **Stated rather than routed around:** ``AttachmentService.upload``
# validates a filename not at all. It takes whatever the multipart part carried, stores
# it, and relies on ``routes/attachments.py::_content_disposition`` to build a safe
# header out of it at download time. That is sound for the header and says
# nothing about the string, so the mint applies its own rule rather than inheriting an
# absent one -- and it is applied *only* at the mint, because retroactively refusing
# filenames the plain multipart route has always accepted is a separate behaviour
# change with its own migration question.
#
# The rule is deliberately narrow: a ticket's filename is stored and echoed into a
# ``curl`` line, so it must be one path segment, printable, and bounded. The ``curl``
# line shell-quotes it regardless -- two independent controls, because a tool result is
# a string a model may paste into a shell.
MAX_TICKET_FILENAME_CHARS = 255
_FORBIDDEN_FILENAME_CHARS = frozenset({"/", "\\", "\x00", "\n", "\r", "\t"})

# DD-29. What ``create_text_attachment`` may store: text an agent authored is the
# one payload allowed to ride inside a tool call, so the set is narrow and named rather
# than "anything that looks like text". Anything else goes to the multipart REST route.
# Every member satisfies :func:`is_text_content_type`, which is what keeps the write
# path and the ``resources/read`` path from disagreeing about what text is; a test
# asserts it rather than a comment.
TEXT_ATTACHMENT_TYPES: tuple[str, ...] = (
    "application/json",
    "application/xml",
    "text/csv",
    "text/html",
    "text/markdown",
    "text/plain",
)


def is_text_content_type(content_type: str) -> bool:
    """Whether a stored content type is served as text rather than as bytes.

    ``text/*``, plus ``application/json`` and ``application/xml`` and their structured
    suffixes (``application/ld+json``, ``application/atom+xml``), which are text that
    happens not to be typed ``text/``. Everything else is bytes, and a host decides
    what to do with the base64 the SDK frames for it.
    """
    main, _, sub = content_type.partition("/")
    if main == "text":
        return True
    if main != "application" or not sub:
        return False
    suffix = sub.rpartition("+")[2]
    return suffix in ("json", "xml")


def normalize_content_type(raw: str | None) -> str:
    """The uploaded content type reduced to a bare ``type/subtype``.

    Stored normalized rather than sanitized at serve time, because the stored value is
    what every reader sees: the download route, the metadata route, and any future
    consumer. Normalizing once at the write is the only way all three agree.

    Parameters are dropped -- ``text/html; charset=utf-8`` becomes ``text/html`` -- so a
    caller cannot smuggle anything through a parameter into a response header, and
    anything that does not parse as one type and one subtype becomes
    ``application/octet-stream``. This deliberately does **not** rewrite the type itself:
    ``Content-Disposition: attachment`` plus ``X-Content-Type-Options: nosniff`` is what
    stands between a stored ``text/html`` and stored XSS, and lying about the type would
    break every legitimate download without adding to that.

    ``email.message.Message`` does the parsing, so the grammar is the one the standard
    library implements rather than a regex written here.
    """
    if not raw:
        return DEFAULT_CONTENT_TYPE
    message = Message()
    message["Content-Type"] = raw
    parsed = message.get_content_type()
    # ``get_content_type`` falls back to ``text/plain`` for anything it cannot parse,
    # which would silently upgrade garbage into a renderable type; the failure is
    # detectable because the fallback is only returned when no type was found.
    if message.get_content_maintype() == "text" and message.get_content_subtype() == "plain":
        if "/" not in raw.split(";", 1)[0]:
            return DEFAULT_CONTENT_TYPE
    main, _, sub = parsed.partition("/")
    if not main or not sub:
        return DEFAULT_CONTENT_TYPE
    return parsed


class AttachmentService:
    def __init__(
        self,
        db: Database,
        attachment_repo: AttachmentRepository,
        blob_repo: BlobRepository,
        audit_repo: AuditRepository,
        access: AccessService,
        record_attachments: RecordAttachmentRepository,
        blob_refs: BlobReferenceRepository | None = None,
        max_attachment_bytes: int | None = None,
        base_url: str | None = None,
        tokens: AccessTokenService | None = None,
        upload_ticket_ttl_seconds: int = 300,
    ) -> None:
        self._db = db
        self._attachments = attachment_repo
        self._blobs = blob_repo
        self._audit = audit_repo
        # An attachment has no object type of its own -- blobs are shared by
        # content hash, so "the owning record" is legitimately zero, one, or many records
        # across several object types. ``record_attachments`` is the materialized reverse
        # index that gives the level check something to check against.
        self._access = access
        self._record_attachments = record_attachments
        self._blob_refs = blob_refs
        # ``GW_MAX_ATTACHMENT_BYTES`` is the per-file control. It arrives here as a
        # constructor argument, the way ``LoginRateLimiter``
        # receives its budgets, so the *service* enforces the cap and the route only
        # bounds how much it reads (DD-3: a route may read bounded bytes; it may not
        # decide). ``None`` means unbounded and exists for the direct-construction call
        # sites in the test suite; ``build_services`` always passes the setting.
        self._max_attachment_bytes = max_attachment_bytes
        # DD-29. ``GW_BASE_URL`` arrives the same way, and for the same reason:
        # the service answers "where are this attachment's bytes", so neither adapter
        # has to read ``Settings`` to say it. Optional outside OIDC mode, which is why
        # ``download_url`` falls back to a path rather than refusing.
        self._base_url = base_url
        # DD-16. An upload ticket is an ``access_tokens`` row, so the mint and the
        # spend both belong to that service; this one composes the document around them
        # and owns the two facts the token service has no business knowing -- where the
        # upload route is, and what the per-file ceiling is. ``None`` is the
        # direct-construction shape some tests use, and ``build_services`` always
        # passes it.
        self._tokens = tokens
        self._upload_ticket_ttl_seconds = upload_ticket_ttl_seconds

    @property
    def max_attachment_bytes(self) -> int | None:
        """The per-file cap this deployment enforces, published in
        ``describe_capabilities`` so an agent knows the ceiling before it reads a
        file."""
        return self._max_attachment_bytes

    def download_url(self, attachment_id: str) -> str:
        """Where an agent or a browser fetches this attachment's bytes.

        Absolute when ``GW_BASE_URL`` is configured, and the bare path when it is not:
        the setting is optional outside OIDC mode, and a path a caller can join to the
        host it already connected to is more useful than a refusal. The URL carries no
        credential; the caller presents the same bearer it presents to ``/mcp``, or the
        session cookie the SPA already holds (DD-29 rejects presigned URLs).
        """
        path = DOWNLOAD_PATH.format(attachment_id=attachment_id)
        if not self._base_url:
            return path
        return f"{self._base_url.rstrip('/')}{path}"

    @property
    def upload_ticket_ttl_seconds(self) -> int:
        """How long a minted ticket lives, published in ``describe_capabilities`` so an
        agent knows whether to mint before or after it reads the file."""
        return self._upload_ticket_ttl_seconds

    @property
    def upload_url(self) -> str:
        """Where bytes go up. Absolute when ``GW_BASE_URL`` is configured, and the bare
        path when it is not, exactly as :meth:`download_url` answers.

        Derived from that setting only, never from a request header:
        a poisoned ``Host`` telling an agent to POST a file to an attacker's origin is
        worse than a refusal, which is why :meth:`create_upload_ticket` refuses rather
        than guessing.
        """
        if not self._base_url:
            return UPLOAD_PATH
        return f"{self._base_url.rstrip('/')}{UPLOAD_PATH}"

    def create_upload_ticket(
        self,
        actor: ActorContext,
        filename: str,
        content_type: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Mint a single-use credential for one upload, and hand back a ready request
        (DD-16).

        This exists because of a measured gap, not a theoretical one. Bytes cannot ride
        in a tool call (DD-29), so an upload is an HTTP request -- and the
        recipe for that request names two things the model cannot obtain. The bearer belongs
        to the MCP *client*, and no part of the protocol hands the model its own
        connection credential; the origin is not visible to it either, because it never
        sees the endpoint URL. On a local deployment, which is exactly where
        ``GW_BASE_URL`` is most likely unset, both halves of the ``curl`` command were
        missing. So the endpoint supplies both.

        The credential is narrowed on every axis that can be narrowed: ``write`` scope,
        one capability, one route, one filename, minutes of life, single use, and dying
        with its minter's other credentials (inherited). It grants nothing -- there
        is no path by which it does more than the PAT that minted it.
        """
        if not self._base_url:
            raise ValidationFailedError(
                "This deployment has no GW_BASE_URL, so it cannot tell you an absolute "
                "URL to upload to, and a relative one is useless to you: you cannot see "
                "this endpoint's own origin either. Ask an administrator to set "
                "GW_BASE_URL, or upload through the browser UI's attachment widget.",
                setting="GW_BASE_URL",
            )
        assert self._tokens is not None, "create_upload_ticket needs an AccessTokenService"
        cleaned = self._validate_ticket_filename(filename)
        normalized = normalize_content_type(content_type)
        minted = self._tokens.mint_capability_token(
            actor,
            capability=ATTACHMENT_UPLOAD_CAPABILITY,
            capability_data={"filename": cleaned, "content_type": normalized},
            ttl_seconds=self._upload_ticket_ttl_seconds,
            now=now,
        )
        authorization = f"Bearer {minted.plaintext}"
        url = self.upload_url
        # Every interpolated value is shell-quoted. The local path is a
        # placeholder the agent replaces, and it is quoted too, so the shape of the line
        # it edits already shows where quoting belongs.
        curl = " ".join(
            [
                "curl",
                "-X",
                "POST",
                "-H",
                shlex.quote(f"Authorization: {authorization}"),
                "-F",
                shlex.quote(f"{UPLOAD_FIELD}=@/path/to/{cleaned}"),
                shlex.quote(url),
            ]
        )
        return {
            "upload_url": url,
            "method": "POST",
            "field": UPLOAD_FIELD,
            "authorization": authorization,
            "expires_at": minted.row.expires_at,
            "max_bytes": self._max_attachment_bytes,
            "filename": cleaned,
            "content_type": normalized,
            "single_use": True,
            "curl": curl,
        }

    @staticmethod
    def _validate_ticket_filename(filename: str) -> str:
        cleaned = (filename or "").strip()
        if not cleaned:
            raise ValidationFailedError(
                "filename is required: the ticket is bound to it, and the stored "
                "attachment takes its name from the ticket rather than from the "
                "multipart part.",
                parameter="filename",
            )
        if len(cleaned) > MAX_TICKET_FILENAME_CHARS:
            raise ValidationFailedError(
                f"filename is {len(cleaned)} characters; at most "
                f"{MAX_TICKET_FILENAME_CHARS} are stored.",
                parameter="filename",
                max_chars=MAX_TICKET_FILENAME_CHARS,
            )
        if any(char in _FORBIDDEN_FILENAME_CHARS for char in cleaned) or cleaned in (".", ".."):
            raise ValidationFailedError(
                "filename must be a single name with an extension, like 'report.pdf': "
                "no directory separators, no control characters. Point the upload at "
                "whatever local path holds the bytes; the stored name is this one.",
                parameter="filename",
                filename=filename,
            )
        return cleaned

    def upload(
        self,
        actor: ActorContext,
        filename: str,
        content_type: str,
        content: bytes,
        now: datetime | None = None,
        capability_token_id: str | None = None,
    ) -> AttachmentRow:
        """Store bytes as a new attachment row.

        ``capability_token_id`` (DD-16) is the ``access_tokens.id`` of the upload
        ticket this request presented, or ``None`` for an ordinary PAT or session, whose
        behaviour does not depend on it. When it is set,
        two things follow, both **inside the one transaction** that inserts the row
        (a nested ``db.write()`` would take a second connection and deadlock on this
        codebase's own writer lock):

        - the ticket is marked consumed with a conditional update whose rowcount must
          be 1, so two concurrent uses cannot both win and a rollback un-spends it; and
        - the ticket's bound ``filename`` and ``content_type`` **win over the multipart
          part's**, which is what makes a leaked ticket unrepurposable and removes the
          friction a mismatch refusal would add -- the agent names the file at mint and
          points ``curl`` at whatever local path holds the bytes.

        The size check stays where it is, before the transaction, so a refused upload
        does not burn the ticket.
        """
        if self._max_attachment_bytes is not None and len(content) > self._max_attachment_bytes:
            raise ValidationFailedError(
                f"Attachment is {len(content)} bytes; this deployment accepts at most "
                f"{self._max_attachment_bytes} (GW_MAX_ATTACHMENT_BYTES). Upload a smaller "
                "file, or ask an administrator to raise the limit.",
                byte_size=len(content),
                max_bytes=self._max_attachment_bytes,
                setting="GW_MAX_ATTACHMENT_BYTES",
            )
        # Normalized before it is stored, not on the way out (L6): the stored value is
        # what the metadata route returns too, so one normalization at the write is what
        # keeps the two answers the same.
        content_type = normalize_content_type(content_type)
        sha256 = hashlib.sha256(content).hexdigest()
        ts = format_datetime(now or utc_now())
        self._blobs.write(sha256, content)
        with self._db.write() as conn:
            if capability_token_id is not None:
                assert self._tokens is not None, "a ticketed upload needs an AccessTokenService"
                ticket = self._tokens.consume_capability_in_txn(conn, capability_token_id, now)
                bound = json.loads(ticket.capability_data or "{}")
                filename = bound.get("filename", filename)
                content_type = normalize_content_type(bound.get("content_type") or content_type)
            row = AttachmentRow(
                id=str(uuid.uuid4()),
                sha256=sha256,
                filename=filename,
                content_type=content_type,
                byte_size=len(content),
                uploaded_at=ts,
                uploaded_by=actor.principal_id,
            )
            self._attachments.insert_attachment(conn, row)
            self._audit.append(
                conn,
                [
                    make_event(
                        actor,
                        ts,
                        entity_type="attachment",
                        entity_id=row.id,
                        action="create",
                        new_value={
                            "filename": filename,
                            "content_type": content_type,
                            "sha256": sha256,
                            "byte_size": row.byte_size,
                        },
                    )
                ],
            )
        return row

    def upload_text(
        self,
        actor: ActorContext,
        filename: str,
        content_type: str,
        text: str,
        now: datetime | None = None,
    ) -> AttachmentRow:
        """Store a document an agent authored (DD-29).

        The one payload allowed to ride inside a tool call, because the model had to
        emit those characters anyway; base64 of a binary file is not, and goes to the
        multipart REST route instead. This is a thin front for :meth:`upload`, so the
        sha256, the audit event, the content-type normalization and the
        ``GW_MAX_ATTACHMENT_BYTES`` refusal are all inherited rather than restated.

        Only the allowlist is new, and it is narrow on purpose: an agent naming
        ``application/pdf`` here has misunderstood the surface, and a refusal that lists
        the allowed set and names the route for everything else is what corrects it.
        """
        normalized = normalize_content_type(content_type)
        if normalized not in TEXT_ATTACHMENT_TYPES:
            allowed = ", ".join(TEXT_ATTACHMENT_TYPES)
            raise ValidationFailedError(
                f"content_type {content_type!r} is not a text type this tool stores. "
                f"Allowed: {allowed}. For any other file, including binary, POST it to "
                "/api/v1/attachments as multipart/form-data with the field name 'file', "
                "using the same bearer token, and put the returned id on the record.",
                parameter="content_type",
                content_type=normalized,
                allowed=list(TEXT_ATTACHMENT_TYPES),
            )
        return self.upload(actor, filename, normalized, text.encode("utf-8"), now=now)

    # The four reads take an ``ActorContext``, first and positional, exactly
    # as ``upload`` does.

    def get_attachment(self, actor: ActorContext, attachment_id: str) -> AttachmentRow:
        with self._db.read() as conn:
            row = self._attachments.get_attachment(conn, attachment_id)
            if row is None:
                raise NotFoundError("attachment", attachment_id)
            self._require_readable(conn, actor, row)
            return row

    def get_many(self, actor: ActorContext, attachment_ids: list[str]) -> list[AttachmentRow]:
        """Resolve whatever ``attachment_ids`` reference real rows, silently
        omitting the rest. Used to render an attachment field's stored ids: those
        are validated for shape only (see ``RecordService``), not existence, so a
        caller resolving them for display must tolerate a dangling id rather than
        treat it as an error."""
        if not attachment_ids:
            return []
        with self._db.read() as conn:
            readable = self._access.accessible_type_ids(conn, actor, "read")
            by_attachment = self._record_attachments.object_type_ids_for_attachments(
                conn, attachment_ids
            )
            return [
                row
                for row in self._attachments.get_attachments(conn, attachment_ids)
                if self._access.attachment_readable(
                    actor, row, by_attachment.get(row.id, set()), readable
                )
            ]

    def download(self, actor: ActorContext, attachment_id: str) -> tuple[AttachmentRow, bytes]:
        row = self.get_attachment(actor, attachment_id)
        return row, self._blobs.read(row.sha256)

    def stream_download(
        self, actor: ActorContext, attachment_id: str
    ) -> tuple[AttachmentRow, Iterator[bytes]]:
        """Like :meth:`download`, but the caller gets an iterator of chunks
        instead of the full content, so the download route can stream the blob
        to the client without buffering it into memory (docs/DATA_MODEL.md
        section 8: "streamed by the app, never served directly from disk").
        The row lookup happens eagerly here — before any byte is read from disk —
        so an unknown id still raises ``NotFoundError`` before the route sends a
        response, rather than partway through a started stream. ``open_stream``
        (``repositories/blobs.py``) opens the blob eagerly for the same reason:
        a row whose bytes are missing on disk (the backup-restore negative
        case) must fail the same clean way, not mid-stream."""
        row = self.get_attachment(actor, attachment_id)
        return row, self._blobs.open_stream(row.sha256)

    def read_content(
        self, actor: ActorContext, attachment_id: str
    ) -> tuple[AttachmentRow, str | bytes]:
        """The attachment's bytes, already decided to be text or not (DD-29).

        What ``resources/read`` serves. The decision is here rather than in the adapter
        because it is a content decision, and it uses :func:`is_text_content_type`, the
        same predicate ``TEXT_ATTACHMENT_TYPES`` is checked against, so a document
        written through ``create_text_attachment`` always reads back as text.

        The read rule arrives with the row, because :meth:`get_attachment` applies it.

        A file typed as text whose bytes are not valid UTF-8 comes back as bytes. The
        alternative is a ``UnicodeDecodeError`` on a stored file the caller cannot fix,
        and a blob carrying its declared MIME type is a legal answer the host can still
        use; lying about the type would not be.
        """
        row = self.get_attachment(actor, attachment_id)
        content = self._blobs.read(row.sha256)
        if is_text_content_type(row.content_type):
            try:
                return row, content.decode("utf-8")
            except UnicodeDecodeError:
                return row, content
        return row, content

    # ------------------------------------------------------------- the read rule

    def _require_readable(self, conn: Connection, actor: ActorContext, row: AttachmentRow) -> None:
        """The attachment read rule, asked of the one module that answers authorization
        questions.

        The rule itself is :meth:`AccessService.attachment_readable`, because the record
        write funnel is its second enforcement site (DD-12), so this method is the lookup
        the rule needs and nothing else.
        """
        referencing = self._record_attachments.object_type_ids_for_attachment(conn, row.id)
        self._access.require_attachment_readable(conn, actor, row, referencing)

    # -------------------------------------------------------------- orphan sweep

    def sweep_orphan_blobs(
        self,
        actor: ActorContext,
        limit: int = DEFAULT_SWEEP_LIMIT,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Delete blobs on the volume that no ``attachments`` row references (FR-P2).

        It is the only caller of ``blobs.py``'s deletion path, so this is the one way an
        unreferenced blob leaves the volume.

        **The reference check is by content hash, not by row, and that is not an
        optimization.** Blobs are content-addressed, so uploading the same bytes twice
        produces two rows and one file (docs/DATA_MODEL.md section 8). Deleting the
        file when *a* referencing row disappears would silently break every other row
        sharing it. Only when the last one is gone is the file deletable, which is
        exactly what comparing against the set of all referenced hashes gives.

        Orphans are real rather than hypothetical: DD-36 notes that a blob written
        after the backup snapshot is copied into the artifact anyway and arrives on the
        restored volume unreferenced, and this sweep is the companion it names.

        Bounded by ``limit`` blobs examined, and the result says so, so a caller can
        tell a clean tree from a truncated pass.
        """
        assert self._blob_refs is not None, "sweep_orphan_blobs needs a BlobReferenceRepository"
        with self._db.read() as conn:
            referenced = self._blob_refs.referenced_hashes(conn)
        examined = 0
        deleted = 0
        for sha256 in self._blobs.iter_hashes():
            if examined >= limit:
                break
            examined += 1
            if sha256 in referenced:
                continue
            if self._blobs.delete(sha256):
                deleted += 1
        result = {
            "examined": examined,
            "deleted": deleted,
            "limit": limit,
            "truncated": int(examined >= limit),
        }
        if deleted:
            ts = format_datetime(now or utc_now())
            with self._db.write() as conn:
                self._audit.append(
                    conn,
                    [
                        make_event(
                            actor,
                            ts,
                            entity_type="attachment_blob",
                            entity_id="orphan_sweep",
                            action="blobs_swept",
                            new_value=dict(result),
                        )
                    ],
                )
        logger.info("orphan_blob_sweep", principal_id=actor.principal_id, **result)
        return result
