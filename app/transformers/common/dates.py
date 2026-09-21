"""Date/time helpers shared by all provider transformers."""

from __future__ import annotations

from datetime import datetime

from app.transformers.common.converters import as_text

_HOME_DT_FORMAT = "%Y-%m-%dT%H:%M:%S"


def parse_home_date(value: str) -> datetime:
    """Parse a canonical home ISO date (``2026-10-01``)."""
    return datetime.strptime(value, "%Y-%m-%d")


def parse_home_datetime(value: str) -> datetime:
    """Parse a canonical home ISO datetime (``2026-10-01T08:30:00``)."""
    return datetime.strptime(value, _HOME_DT_FORMAT)


def format_home_datetime(value: datetime) -> str:
    """Format a datetime back to the canonical home format."""
    return value.strftime(_HOME_DT_FORMAT)


def parse_iso_datetime(value: object) -> datetime | None:
    """Parse an ISO local date-time without raising (``None`` when unusable)."""
    text = as_text(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def split_home_datetime(value: object) -> tuple[str | None, str | None]:
    """Split an ISO date-time into ``(date, time)``.

    A provider that states only a date yields ``(date, None)``; a missing or
    non-ISO value yields ``(None, None)``. The home contract keeps the date and
    the time in separate fields, and no provider states them separately.
    """
    text = as_text(value)
    if text is None:
        return None, None

    separator = text.find("T")
    if separator < 0:
        return text, None

    return text[:separator] or None, text[separator + 1 :] or None


def duration_between(start: object, end: object) -> str | None:
    """Travel time between two ISO date-times, e.g. ``"7h 40m"``.

    Derived, not read: no provider states a total duration on the journey, and
    the home contract needs one.
    """
    start_at = parse_iso_datetime(start)
    end_at = parse_iso_datetime(end)

    if start_at is None or end_at is None:
        return None

    return format_minutes((end_at - start_at).total_seconds() // 60)


def format_minutes(minutes: object) -> str | None:
    """Format a whole number of minutes as ``"7h 40m"`` / ``"45m"``."""
    if minutes is None:
        return None

    try:
        total = int(minutes)
    except (TypeError, ValueError):
        return None

    if total < 0:
        return None

    hours, rest = divmod(total, 60)

    return f"{hours}h {rest}m" if hours else f"{rest}m"
