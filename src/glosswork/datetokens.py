"""Relative date token resolution (FR-R8).

Tokens are resolved server-side so agents never compute dates:
``@today``, ``@now``, ``@start_of_week``, ``@start_of_month``, ``@start_of_quarter``,
``@start_of_year``, each with an optional signed offset in units ``d w M y h m``
(days, weeks, months, years, hours, minutes), e.g. ``@today-7d``, ``@start_of_month-1M``.
"""

from __future__ import annotations

import calendar
import re
from datetime import UTC, datetime, timedelta

from glosswork.errors import ValidationFailedError
from glosswork.timeutil import format_date, format_datetime

DATE_TOKEN_BASES: tuple[str, ...] = (
    "today",
    "now",
    "start_of_week",
    "start_of_month",
    "start_of_quarter",
    "start_of_year",
)
DATE_TOKEN_UNITS: dict[str, str] = {
    "d": "days",
    "w": "weeks",
    "M": "months",
    "y": "years",
    "h": "hours",
    "m": "minutes",
}

TOKEN_PATTERN = re.compile(
    rf"^@(?P<base>{'|'.join(DATE_TOKEN_BASES)})"
    rf"(?:(?P<sign>[+-])(?P<n>\d+)(?P<unit>[{''.join(DATE_TOKEN_UNITS)}]))?$"
)


def is_date_token(value: object) -> bool:
    return isinstance(value, str) and value.startswith("@")


def _base_value(base: str, now: datetime) -> datetime:
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if base == "now":
        return now.replace(microsecond=0)
    if base == "today":
        return midnight
    if base == "start_of_week":
        return midnight - timedelta(days=midnight.weekday())
    if base == "start_of_month":
        return midnight.replace(day=1)
    if base == "start_of_quarter":
        quarter_month = 3 * ((midnight.month - 1) // 3) + 1
        return midnight.replace(month=quarter_month, day=1)
    if base == "start_of_year":
        return midnight.replace(month=1, day=1)
    raise AssertionError(f"unreachable base {base!r}")


def _shift_months(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + (value.month - 1) + months
    year, month = divmod(month_index, 12)
    month += 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _apply_offset(value: datetime, sign: str, n: int, unit: str) -> datetime:
    amount = n if sign == "+" else -n
    if unit == "d":
        return value + timedelta(days=amount)
    if unit == "w":
        return value + timedelta(weeks=amount)
    if unit == "h":
        return value + timedelta(hours=amount)
    if unit == "m":
        return value + timedelta(minutes=amount)
    if unit == "M":
        return _shift_months(value, amount)
    if unit == "y":
        return _shift_months(value, amount * 12)
    raise AssertionError(f"unreachable unit {unit!r}")


def resolve_date_token(token: str, field_type: str, now: datetime | None = None) -> str:
    """Resolve one ``@`` token to a stored-format literal for the given field type.

    ``field_type`` is ``'date'`` (yields ``YYYY-MM-DD``) or ``'datetime'`` (yields
    ``YYYY-MM-DDTHH:MM:SSZ``). Unknown tokens raise a validation error listing the
    supported grammar, since a typoed token silently treated as a literal would fail
    date parsing with a less helpful message.
    """
    match = TOKEN_PATTERN.match(token)
    if not match:
        raise ValidationFailedError(
            f"Unknown date token {token!r}. Supported: @today, @now, @start_of_week, "
            "@start_of_month, @start_of_quarter, @start_of_year, with an optional "
            "offset like -7d or +30d (units: d, w, M, y, h, m)."
        )
    now = (now or datetime.now(UTC)).astimezone(UTC)
    value = _base_value(match["base"], now)
    if match["sign"]:
        value = _apply_offset(value, match["sign"], int(match["n"]), match["unit"])
    if field_type == "date":
        return format_date(value)
    return format_datetime(value)
