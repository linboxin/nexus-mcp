"""Site + user context loaded once per session (identity, capabilities, timezone)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from ..config import Settings
from ..errors import NexusError
from ..timeutil import resolve_timezone
from .client import MoodleClient


@dataclass(frozen=True)
class SiteContext:
    site_url: str
    site_name: str
    user_id: int
    fullname: str
    username: str
    release: str
    version: str
    functions: frozenset[str]
    tz: ZoneInfo
    tz_name: str
    tz_source: str
    lang: str
    user_is_admin: bool

    def has(self, function: str) -> bool:
        return function in self.functions

    def as_dict(self) -> dict[str, Any]:
        return {
            "site_url": self.site_url,
            "site_name": self.site_name,
            "user_id": self.user_id,
            "fullname": self.fullname,
            "username": self.username,
            "moodle_release": self.release,
            "timezone": self.tz_name,
            "timezone_source": self.tz_source,
            "language": self.lang,
            "function_count": len(self.functions),
        }


async def load_site_context(client: MoodleClient, settings: Settings) -> SiteContext:
    info = await client.get_site_info()
    functions = frozenset(f.get("name", "") for f in info.get("functions", []))
    user_id = int(info["userid"])

    candidates: list[tuple[str | None, str]] = []
    if settings.timezone:
        candidates.append((settings.timezone, "NEXUS_TIMEZONE"))
    account_tz: str | None = None
    if "core_user_get_users_by_field" in functions:
        try:
            users = await client.call("core_user_get_users_by_field", field="id", values=[user_id])
            if users:
                account_tz = users[0].get("timezone")
        except NexusError:
            account_tz = None
    candidates.append((account_tz, "moodle-account"))
    candidates.append((settings.default_timezone, "site-default"))
    tz, tz_name, tz_source = resolve_timezone(candidates)

    return SiteContext(
        site_url=info.get("siteurl") or settings.base_url,
        site_name=info.get("sitename", ""),
        user_id=user_id,
        fullname=info.get("fullname", ""),
        username=info.get("username", ""),
        release=str(info.get("release", "")),
        version=str(info.get("version", "")),
        functions=functions,
        tz=tz,
        tz_name=tz_name,
        tz_source=tz_source,
        lang=info.get("lang", ""),
        user_is_admin=bool(info.get("userissiteadmin")),
    )
