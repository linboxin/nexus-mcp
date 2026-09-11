from __future__ import annotations

from typing import Any

from .. import runtime
from ._common import READ_ONLY, dump, nexus_tool


def register(server: Any) -> None:
    @server.tool(name="upcoming_events", annotations=READ_ONLY)
    @nexus_tool
    async def upcoming_events(days: int = 14, course_id: int | None = None) -> dict[str, Any]:
        """Calendar events for the next `days` days in chronological order: assignment
        due dates, quiz open/close times, course events (exams, sessions), personal and
        site events, each with course, start/end, description, url and event type."""
        nx = await runtime.get_nexus()
        events, warnings = await nx.calendar.events(days=days, course_id=course_id)
        return {"events": dump(events), "count": len(events), "window_days": days, "timezone": nx.site.tz_name, "warnings": warnings}
