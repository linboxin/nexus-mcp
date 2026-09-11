from __future__ import annotations

from typing import Any

from .. import runtime
from ._common import READ_ONLY, dump, nexus_tool


def register(server: Any) -> None:
    @server.tool(name="upcoming_assignments", annotations=READ_ONLY)
    @nexus_tool
    async def upcoming_assignments(days: int = 7, course_id: int | None = None, include_submitted: bool = True) -> dict[str, Any]:
        """Assignments due within the next `days` days, ordered by due date, each with its
        Moodle submission status (not_started / draft / submitted / graded / overdue), points
        and URL. This is the one call to make for "what's due this week?". Dates are in the
        student's timezone. Set include_submitted=false to hide finished work."""
        nx = await runtime.get_nexus()
        items, warnings = await nx.assignments.upcoming(days=days, course_id=course_id, include_submitted=include_submitted)
        return {"assignments": dump(items), "count": len(items), "window_days": days, "timezone": nx.site.tz_name, "warnings": warnings}

    @server.tool(name="overdue_assignments", annotations=READ_ONLY)
    @nexus_tool
    async def overdue_assignments(course_id: int | None = None, max_age_days: int = 120) -> dict[str, Any]:
        """Assignments whose (extension-adjusted) due date has passed and that Moodle reports
        as not submitted. Only current courses are checked unless course_id is given; items
        older than max_age_days are ignored. `can_still_submit` says whether Nexus still
        accepts a submission (cut-off date)."""
        nx = await runtime.get_nexus()
        items, warnings = await nx.assignments.overdue(course_id=course_id, max_age_days=max_age_days)
        return {"assignments": dump(items), "count": len(items), "warnings": warnings}

    @server.tool(name="assignment_details", annotations=READ_ONLY)
    @nexus_tool
    async def assignment_details(assignment_id: int) -> dict[str, Any]:
        """Everything about one assignment: instructions, due/cut-off dates, submission
        requirements (types, file limits, word limit), current submission status, grading
        info and attached files. Accepts the assignment id (preferred) or its cmid."""
        nx = await runtime.get_nexus()
        detail = await nx.assignments.details(assignment_id)
        return detail.model_dump(mode="json")

    @server.tool(name="submission_status", annotations=READ_ONLY)
    @nexus_tool
    async def submission_status(assignment_id: int) -> dict[str, Any]:
        """Authoritative submission status for one assignment straight from Moodle:
        not_started, draft, submitted, graded or overdue, with the grade/feedback when
        released. Check `freshness.cached` before calling the value real-time."""
        nx = await runtime.get_nexus()
        status = await nx.assignments.submission_status(assignment_id)
        return status.model_dump(mode="json")
