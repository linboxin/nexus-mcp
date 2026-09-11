from __future__ import annotations

from typing import Any

from .. import intelligence, runtime
from ._common import READ_ONLY, nexus_tool


def register(server: Any) -> None:
    @server.tool(name="daily_briefing", annotations=READ_ONLY)
    @nexus_tool
    async def daily_briefing() -> dict[str, Any]:
        """One-call academic briefing: due today, due tomorrow, coming up, overdue work,
        today's events, new announcements/course changes, and a recommended priority
        order. `text` is ready to show; the structured fields back it up."""
        nx = await runtime.get_nexus()
        return await intelligence.daily_briefing(nx)

    @server.tool(name="weekly_briefing", annotations=READ_ONLY)
    @nexus_tool
    async def weekly_briefing(days: int = 7) -> dict[str, Any]:
        """The next 7 days grouped by course: assignments (with status) and calendar
        deadlines, plus anything overdue."""
        nx = await runtime.get_nexus()
        return await intelligence.weekly_briefing(nx, days=days)

    @server.tool(name="workload_analysis", annotations=READ_ONLY)
    @nexus_tool
    async def workload_analysis(days: int = 7) -> dict[str, Any]:
        """Workload for the next `days` days. `facts` are Moodle data (counts per course and
        per day, points, quizzes, overdue); `estimates` are nexus-mcp heuristics for hours
        of effort and are explicitly not instructor estimates."""
        nx = await runtime.get_nexus()
        return await intelligence.workload_analysis(nx, days=days)

    @server.tool(name="what_should_i_do_next", annotations=READ_ONLY)
    @nexus_tool
    async def what_should_i_do_next() -> dict[str, Any]:
        """Prioritised to-do list built from overdue work, upcoming due dates, submission
        status, calendar deadlines and recent announcements. Each item explains why it
        was ranked where it is (HIGH / MEDIUM / LOW)."""
        nx = await runtime.get_nexus()
        return await intelligence.what_should_i_do_next(nx)
