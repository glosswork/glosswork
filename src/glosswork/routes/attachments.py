"""Attachment upload and download routes (docs/DATA_MODEL.md section 8).

Thin adapters over ``AttachmentService`` (DD-3): routes parse the request, call one
service method, and shape the response. ``GlossworkError`` subclasses (e.g.
``NotFoundError`` for an unknown attachment id) are never caught here; the single
exception handler in ``app.py`` maps them onto the error envelope (FR-A4).

Download requires an authenticated actor context, like every other route here.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import StreamingResponse

from glosswork.actor import ActorContext
from glosswork.api_deps import get_actor, get_services
from glosswork.envelopes import attachment_doc
from glosswork.scopes import CAPABILITY_CREDENTIAL_STATE, require_capability, require_scope
from glosswork.services import ServiceBundle
from glosswork.services.attachments import ATTACHMENT_UPLOAD_CAPABILITY

# Characters an ASCII ``filename=`` parameter may carry. Everything else -- the quote
# and backslash that end the parameter early, CR and LF that inject a header, and every
# non-ASCII byte -- becomes an underscore. The real name still reaches a modern client
# through ``filename*``.
_SAFE_FILENAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._-()[]{}+,@#$%^&=~"
)
_FALLBACK_FILENAME = "download"


def _content_disposition(filename: str) -> str:
    """``Content-Disposition`` built by construction rather than by interpolation.

    An f-string around the stored filename would let a hand-rolled multipart body carrying a
    raw ``"`` land that quote in the header and truncate the parameter. Two parameters are
    emitted, which is what RFC 6266 prescribes and what every current browser reads: a
    sanitized ASCII ``filename=`` that older clients understand, and an RFC 5987
    ``filename*=UTF-8''...`` carrying the real name percent-encoded. ``attachment`` is not
    negotiable -- it is the disposition that keeps a stored ``text/html`` from rendering in
    the SPA's origin.
    """
    ascii_name = "".join(c if c in _SAFE_FILENAME_CHARS else "_" for c in filename).strip()
    if not ascii_name:
        ascii_name = _FALLBACK_FILENAME
    encoded = quote(filename or _FALLBACK_FILENAME, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


router = APIRouter(prefix="/api/v1", tags=["attachments"])

Actor = Annotated[ActorContext, Depends(get_actor)]
Services = Annotated[ServiceBundle, Depends(get_services)]


@router.post(
    "/attachments",
    # DD-16. ``require_capability`` in place of ``require_scope("write")``, not
    # beside it: it declares the same scope, so route introspection is unchanged, and it
    # is the **only** opt-in to a capability credential in the deployment. Every other
    # route -- including every route added later -- is closed to an upload ticket by
    # ``enforce_scope``, with nobody having to remember to close it. A plain PAT takes
    # exactly the pre-021 path through it.
    dependencies=[require_capability(ATTACHMENT_UPLOAD_CAPABILITY, "write")],
)
def upload_attachment(
    file: UploadFile,
    actor: Actor,
    services: Services,
    request: Request,
) -> dict[str, object]:
    # Reads at most ``cap + 1`` bytes. The extra byte is the whole size check at
    # the route: a read that returns more than ``cap`` proves the upload is over without
    # reading, or holding, the rest. The *decision* is still the service's --
    # ``AttachmentService.upload`` raises naming ``GW_MAX_ATTACHMENT_BYTES`` (DD-3) -- so
    # this route bounds memory and nothing else. It is exempt from the edge body cap
    # precisely because it legitimately takes more than 4 MiB.
    #
    # A comment rather than a docstring: a route's docstring becomes its OpenAPI
    # ``description`` and rides into ``web/src/api/schema.ts`` and every published client.
    cap = request.app.state.settings.max_attachment_bytes
    content = file.file.read(cap + 1)
    # The ticket's own row id, left here by ``require_capability``, or None for a plain
    # PAT or session. Read off request state rather than off the actor: a capability is
    # read for *authorization* in exactly one predicate (grep-pinned),
    # and "which row do I mark consumed" is a different question -- answered by the
    # dependency that already decided the call was allowed.
    ticket_id = getattr(request.state, CAPABILITY_CREDENTIAL_STATE, None)
    row = services.attachments.upload(
        actor,
        file.filename or "",
        file.content_type or "",
        content,
        capability_token_id=ticket_id,
    )
    return attachment_doc(row, services.attachments.download_url(row.id))


@router.get("/attachments/{attachment_id}", dependencies=[require_scope("read")])
def get_attachment(
    attachment_id: str,
    actor: Actor,
    services: Services,
) -> dict[str, object]:
    row = services.attachments.get_attachment(actor, attachment_id)
    return attachment_doc(row, services.attachments.download_url(row.id))


@router.get("/attachments/{attachment_id}/download", dependencies=[require_scope("read")])
def download_attachment(
    attachment_id: str,
    actor: Actor,
    services: Services,
) -> StreamingResponse:
    # The download is inert. An attachment is served from the SPA's
    # own origin, so a stored ``text/html`` is a stored-XSS candidate and
    # ``Content-Disposition: attachment`` is the one thing between them. ``nosniff`` is
    # the second: without it a browser may ignore the declared type and sniff the bytes,
    # turning a file uploaded as ``application/octet-stream`` back into whatever it looks
    # like. The content type itself was normalized to a bare ``type/subtype`` at upload,
    # so nothing a caller put in a parameter reaches this header.
    row, chunks = services.attachments.stream_download(actor, attachment_id)
    return StreamingResponse(
        chunks,
        media_type=row.content_type,
        headers={
            "Content-Disposition": _content_disposition(row.filename),
            "X-Content-Type-Options": "nosniff",
        },
    )
