"""Opaque keyset cursors and page-limit validation for paginated service reads.

Record queries carry their own sort-aware cursor in ``compiler.py``; these helpers
serve the simpler single-order pages where the boundary is a small JSON document.
Cursors are opaque to callers: pass ``next_cursor`` back unmodified.

Two guards every paginated read shares (DD-18). Both are here rather
than in each service because a rule three services state three times is a rule that
drifts:

- :func:`validate_page_limit`, so a page ceiling reads the same on query, history and
  comments as ``MAX_SEARCH_LIMIT`` already did on search;
- element-type validation on a decoded cursor, plus :func:`cursor_int`. A cursor is
  base64 JSON and is **not** signed -- both decoders re-derive scope and filter from the
  request, so a tampered cursor can only ever move the boundary within what the caller
  may already read (signing stays out of scope). What it could still do without the
  guard is carry a value of the wrong *type*: a dict would reach sqlite3, which refuses to
  bind one, and a non-integer id would reach ``int(...)``. Both would be 500s for input a
  caller can correct, and the first of them would log the full SQL text with its bound
  parameters.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from glosswork.errors import ValidationFailedError

# What a cursor payload may carry. JSON has no other scalar, so this is "every leaf a
# legitimate cursor can hold": anything else is a list or an object, which is to say a
# tampered cursor rather than one this application encoded.
_SCALAR_TYPES = (str, int, float, bool, type(None))

_TAMPERED = (
    "Invalid cursor. Pass the next_cursor value from a previous response, unmodified, "
    "or omit cursor to start from the beginning."
)


def is_cursor_scalar(value: Any) -> bool:
    """True for a value a cursor may legitimately carry."""
    return isinstance(value, _SCALAR_TYPES)


def cursor_int(value: Any) -> int:
    """A cursor's integer boundary, or ``validation_failed``.

    ``int(...)`` on a decoded cursor was an unhandled ``ValueError`` at two call sites
    (``records.get_record_history_page``, ``audit.search``) and therefore a 500. Both go
    through here now, so the two cannot answer differently.
    """
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValidationFailedError(_TAMPERED)
    try:
        return int(value)
    except ValueError:
        raise ValidationFailedError(_TAMPERED) from None


def validate_page_limit(limit: Any, max_limit: int, limit_name: str) -> None:
    """One page ceiling, stated once (DD-18).

    Enforced in the service so **both adapters inherit it**: REST and MCP are adapters
    over one service layer (DD-3), so a bound declared on an MCP parameter schema would
    hold on one surface and not the other. No ``le=`` is added to the MCP side even
    where it would be easy -- a Pydantic bound refuses inside the SDK's own
    coercion, which ``adapter.py`` deliberately leaves as the SDK's message rather than
    the project envelope, so ``limit=1001`` would be ``validation_failed`` on REST and an
    SDK schema error on MCP. The bound goes in each parameter's description text instead,
    and this raises.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= max_limit):
        raise ValidationFailedError(
            f"limit must be an integer between 1 and {max_limit}; got {limit!r}.",
            "limit",
            max_limit=max_limit,
            limit_name=limit_name,
        )


def encode_cursor(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode()


def decode_cursor(cursor: str, expected_keys: set[str]) -> dict[str, Any]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationFailedError(
            "Invalid cursor. Pass the next_cursor value from a previous response, "
            "unmodified, or omit cursor to start from the beginning."
        ) from exc
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise ValidationFailedError(
            "Invalid cursor for this listing. Pass the next_cursor value from a previous "
            "response of the same tool, unmodified."
        )
    # Element types, not just the key set. The key set was already
    # checked; what reached the query was whatever the caller put under those keys.
    if not all(is_cursor_scalar(value) for value in payload.values()):
        raise ValidationFailedError(_TAMPERED)
    return payload
