from __future__ import annotations

from typing import Any

from .. import runtime
from ..timeutil import parse_since
from ._common import READ_ONLY, dump, nexus_tool


def register(server: Any) -> None:
    @server.tool(name="recent_announcements", annotations=READ_ONLY)
    @nexus_tool
    async def recent_announcements(days: int = 7, course_id: int | None = None, include_all_forums: bool = False) -> dict[str, Any]:
        """Announcements posted by instructors in the last `days` days (the Announcements /
        News forum of each current course), newest first, with author, time, full text
        and link. include_all_forums=true also scans discussion forums."""
        nx = await runtime.get_nexus()
        items, warnings = await nx.notifications.announcements(days=days, course_id=course_id, include_all_forums=include_all_forums)
        return {"announcements": dump(items), "count": len(items), "warnings": warnings}

    @server.tool(name="course_updates", annotations=READ_ONLY)
    @nexus_tool
    async def course_updates(since: str = "24h", course_id: int | None = None) -> dict[str, Any]:
        """What changed in the student's courses since a point in time: modules whose
        settings/files/grades changed, new announcements and Moodle notifications.
        `since` accepts ISO-8601 ("2026-09-10T08:00"), a Unix timestamp, or relative
        values like "24h", "2d", "yesterday". Answers "what changed since yesterday?"."""
        nx = await runtime.get_nexus()
        since_dt = parse_since(since, nx.now())
        return await nx.notifications.course_updates(since_dt, course_id=course_id)
