"""CSV import and export routes (DD-3: thin adapters — parse the request, call
``CsvService``, shape the response; no CSV parsing, mapping, or validation logic
lives here).

PRD.md section 6.11 (FR-E1..FR-E5), section 6.7 (FR-A1..FR-A4);
docs/MCP_TOOLS.md section 8.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse

from glosswork.actor import ActorContext
from glosswork.api_deps import get_actor, get_services
from glosswork.errors import ValidationFailedError
from glosswork.scopes import require_scope
from glosswork.services import ServiceBundle

router = APIRouter(prefix="/api/v1", tags=["csv"])


def _parse_json_param(value: str | None, name: str) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        raise ValidationFailedError(f"{name} must be valid JSON.") from None


@router.post("/object-types/{object_type_key}/import", dependencies=[require_scope("write")])
def import_csv(
    object_type_key: str,
    request: Request,
    file: UploadFile = File(...),  # noqa: B008
    mode: Literal["create", "upsert"] = Form(...),
    upsert_key: str | None = Form(default=None),
    dry_run: bool = Form(default=False),
    create_missing_options: bool = Form(default=False),
    actor: ActorContext = Depends(get_actor),  # noqa: B008
    services: ServiceBundle = Depends(get_services),  # noqa: B008
) -> dict[str, Any]:
    # Reads at most ``cap + 1`` bytes and passes the **bytes** through. Both halves keep a
    # decision out of the route (DD-3): the read is bounded, so an unbounded upload is not
    # materialized just to answer that it is too large; and the route does no
    # ``.decode("utf-8")``, because decoding is part of parsing and lives in ``CsvService``
    # with the rest of it -- a Latin-1 file is now ``validation_failed`` naming the byte
    # offset rather than an unhandled ``UnicodeDecodeError`` and a 500.
    #
    # Exempt from the edge body cap, a CSV import legitimately carrying
    # more than 4 MiB; ``GW_MAX_CSV_IMPORT_BYTES`` is its ceiling.
    cap = request.app.state.settings.max_csv_import_bytes
    result = services.csv.import_csv(
        actor,
        object_type_key,
        file.file.read(cap + 1),
        mode,
        upsert_key=upsert_key,
        dry_run=dry_run,
        create_missing_options=create_missing_options,
    )
    return {
        "dry_run": result.dry_run,
        "created": result.created,
        "updated": result.updated,
        "errors": [{"row": e.row, "field": e.field, "reason": e.reason} for e in result.errors],
    }


@router.get("/object-types/{object_type_key}/export", dependencies=[require_scope("read")])
def export_csv(
    object_type_key: str,
    filter: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    columns: str | None = Query(default=None),
    actor: ActorContext = Depends(get_actor),  # noqa: B008
    services: ServiceBundle = Depends(get_services),  # noqa: B008
) -> Response:
    # Streams. ``export_csv_stream`` resolves the schema and the row
    # ceiling **before** returning its generator, so an export over
    # ``GW_MAX_CSV_EXPORT_ROWS`` is refused with a real 422 rather than arriving as a
    # truncated 200 -- once a ``StreamingResponse`` has sent its headers the status can no
    # longer change. Same shape as ``ExportService.stream`` (FR-E5).
    parsed_filter = _parse_json_param(filter, "filter")
    parsed_sort = _parse_json_param(sort, "sort")
    column_list = [c.strip() for c in columns.split(",") if c.strip()] if columns else None
    rows = services.csv.export_csv_stream(
        actor,
        object_type_key,
        filter=parsed_filter,
        sort=parsed_sort,
        columns=column_list,
    )
    return StreamingResponse(
        rows,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{object_type_key}.csv"'},
    )
