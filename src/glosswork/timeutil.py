"""Timestamp conventions: ISO-8601 UTC strings everywhere (docs/DATA_MODEL.md section 12).

System timestamps and ``datetime`` field values share one format, second precision with a
trailing ``Z``, so lexicographic comparison equals chronological comparison and resolved
date tokens compare correctly against stored values.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DATE_FORMAT = "%Y-%m-%d"


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
