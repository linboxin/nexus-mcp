"""Timezone-aware helpers.

Nexus stores every timestamp as a Unix epoch. We present them in the student's
academic timezone (the Moodle account timezone, falling back to Union's site
timezone America/New_York), never in UTC.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def resolve_timezone(candidates: list[tuple[str | None, str]]) -> tuple[ZoneInfo, str, str]:
    """Return (zoneinfo, name, source) for the first usable candidate.

    ``"99"`` is Moodle's sentinel for "use the server timezone" and is skipped.
    """
    for name, source in candidates:
        if not name or name == "99":
            continue
        try:
            return ZoneInfo(name), name, source
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            continue
    return ZoneInfo("UTC"), "UTC", "fallback"


def to_local(ts: int | float | None, tz: ZoneInfo) -> datetime | None:
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), tz)


def unix(dt: datetime) -> int:
    return int(dt.timestamp())


def _hour12(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt:%M} {dt:%p}"


def display(dt: datetime) -> str:
    """``Sun Sep 14, 2026 11:59 PM EDT``"""
    return f"{dt:%a %b} {dt.day}, {dt.year} {_hour12(dt)} {dt:%Z}".strip()


def short(dt: datetime) -> str:
    """``Sep 14, 11:59 PM``"""
    return f"{dt:%b} {dt.day}, {_hour12(dt)}"


def relative(dt: datetime, now: datetime) -> str:
    delta = dt - now
    seconds = delta.total_seconds()
    if abs(seconds) < 60:
        return "now"
    if seconds < 0:
        ago = -seconds
        if ago < 3600:
            return f"{int(ago // 60)} min ago"
        if ago < 86400:
            hours = int(ago // 3600)
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        days = (now.date() - dt.date()).days
        if days == 1:
            return f"yesterday at {_hour12(dt)}"
        return f"{days} days ago"
    days = (dt.date() - now.date()).days
    if days == 0:
        if seconds < 3600:
            return f"in {int(seconds // 60)} min"
        return f"today at {_hour12(dt)}"
    if days == 1:
        return f"tomorrow at {_hour12(dt)}"
    if days < 7:
        return f"{dt:%A} at {_hour12(dt)}"
    return f"in {days} days"


def start_of_day(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def end_of_day(dt: datetime) -> datetime:
    return start_of_day(dt) + timedelta(days=1) - timedelta(seconds=1)


_RELATIVE = re.compile(r"^\s*(\d+)\s*(m|min|mins|minutes?|h|hr|hrs|hours?|d|days?|w|weeks?)\s*(ago)?\s*$", re.I)


def parse_since(value: str | int | float | None, now: datetime) -> datetime:
    """Parse ``since`` inputs: ISO-8601, Unix epoch, ``24h``/``2d``/``1w``, ``yesterday``, ``today``.

    Naive ISO timestamps are interpreted in ``now``'s timezone.
    """
    if value is None:
        return now - timedelta(days=1)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), now.tzinfo)
    text = str(value).strip()
    lowered = text.lower()
    if lowered in ("", "yesterday"):
        return start_of_day(now - timedelta(days=1))
    if lowered == "today":
        return start_of_day(now)
    if lowered in ("week", "this week", "last week"):
        return now - timedelta(days=7)
    if re.fullmatch(r"\d{9,}(\.\d+)?", text):
        return datetime.fromtimestamp(float(text), now.tzinfo)
    match = _RELATIVE.match(text)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()[0]
        factor = {"m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
        return now - timedelta(seconds=amount * factor)
    iso_text = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError as exc:
        raise ValueError(
            f"Could not understand since={value!r}. Use ISO-8601 (2026-09-10T08:00), a Unix timestamp, "
            "or a relative value like 24h, 2d, yesterday."
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
    return parsed


def day_of(dt: datetime) -> date:
    return dt.date()


UTC = timezone.utc
