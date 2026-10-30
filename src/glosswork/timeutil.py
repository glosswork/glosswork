"""Timestamp conventions: ISO-8601 UTC strings everywhere (docs/DATA_MODEL.md section 12).

System timestamps and ``datetime`` field values share one format, second precision with a
trailing ``Z``, so lexicographic comparison equals chronological comparison and resolved
date tokens compare correctly against stored values.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DATE_FORMAT = "%Y-%m-%d"

# The canonical forms, character for character. ``[0-9]`` and not ``\d``: ``\d`` matches
# every Unicode decimal digit, and ``strptime`` accepts those too.
_CANONICAL_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_CANONICAL_DATETIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def utc_now() -> datetime:
    return datetime.now(UTC)


def format_datetime(value: datetime) -> str:
    """Render a datetime as the canonical stored form, e.g. ``2026-08-23T14:22:00Z``."""
    if value.tzinfo is None:
        raise ValueError("naive datetime; all internal datetimes must be UTC-aware")
    return value.astimezone(UTC).strftime(DATETIME_FORMAT)


def format_date(value: date | datetime) -> str:
    if isinstance(value, datetime):
        value = value.astimezone(UTC).date()
    return value.strftime(DATE_FORMAT)


def parse_datetime(value: str) -> datetime:
    """Parse the canonical stored form. Raises ValueError on anything else."""
    return datetime.strptime(value, DATETIME_FORMAT).replace(tzinfo=UTC)


def parse_date(value: str) -> date:
    return datetime.strptime(value, DATE_FORMAT).date()


def is_canonical_date(value: str) -> bool:
    """Whether ``value`` is exactly ``YYYY-MM-DD`` and names a real calendar day.

    :func:`parse_date` alone is not this check. ``strptime`` accepts ``2026-1-7`` and
    digits outside ASCII, and a value in either shape then compares as text against
    stored values and sorts wrongly without failing. The full match is what makes the
    form exact; the parse is what refuses 30 February.
    """
    if _CANONICAL_DATE.fullmatch(value) is None:
        return False
    try:
        parse_date(value)
    except ValueError:
        return False
    return True


def is_canonical_datetime(value: str) -> bool:
    """Whether ``value`` is exactly ``YYYY-MM-DDTHH:MM:SSZ`` and names a real instant.

    Refuses what :func:`parse_datetime` lets through (``2026-1-7T0:0:0Z``, a lower-case
    ``z``) as well as what it already refuses (a fractional second, an offset, hour 25).
    """
    if _CANONICAL_DATETIME.fullmatch(value) is None:
        return False
    try:
        parse_datetime(value)
    except ValueError:
        return False
    return True
