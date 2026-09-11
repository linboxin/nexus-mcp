from __future__ import annotations

from typing import Any

from .. import runtime
from ._common import READ_ONLY, dump, nexus_tool


def register(server: Any) -> None:
    @server.tool(name="list_courses", annotations=READ_ONLY)
    @nexus_tool
    async def list_courses(classification: str = "inprogress", academic_only: bool = False) -> dict[str, Any]:
        """List the student's Nexus courses with id, name, short name, teacher and term.

        classification (from Moodle start/end dates): "inprogress" (default), "past",
        "future", "hidden", or "all". academic_only=true drops non-academic enrolments
        (trainings, campus resources) that have no term code. Use `id` with other tools.
        """
        nx = await runtime.get_nexus()
        courses = await nx.courses.list_courses(classification, academic_only=academic_only)
        note = None
        if classification == "inprogress" and not courses:
            note = "No in-progress courses were found; try classification='all' (Nexus courses may lack term dates)."
        return {"courses": dump(courses), "count": len(courses), "classification": classification, "academic_only": academic_only, "note": note}

    @server.tool(name="get_course", annotations=READ_ONLY)
    @nexus_tool
    async def get_course(course_id: int) -> dict[str, Any]:
        """Full view of one course: description, instructors, sections with every module,
        the materials (files/pages/books/links) and the major activities (assignments,
        quizzes, forums...). Module ids returned here are course-module ids (cmid)."""
        nx = await runtime.get_nexus()
        detail = await nx.courses.get_course(course_id)
        return detail.model_dump(mode="json")

    @server.tool(name="nexus_status", annotations=READ_ONLY)
    @nexus_tool
    async def nexus_status() -> dict[str, Any]:
        """Who is signed in, which Moodle release Nexus runs, the timezone used for all
        dates, and which capabilities this account's token can use."""
        nx = await runtime.get_nexus()
        capabilities = {
            "courses": nx.has("core_enrol_get_users_courses"),
            "course_contents": nx.has("core_course_get_contents"),
            "assignments": nx.has("mod_assign_get_assignments"),
            "submission_status": nx.has("mod_assign_get_submission_status"),
            "calendar": nx.has("core_calendar_get_calendar_events") or nx.has("core_calendar_get_action_events_by_timesort"),
            "grades": nx.has("gradereport_user_get_grade_items") or nx.has("gradereport_overview_get_course_grades"),
            "announcements": nx.has("mod_forum_get_forums_by_courses") and nx.has("mod_forum_get_forum_discussions"),
            "course_updates": nx.has("core_course_get_updates_since"),
            "notifications": nx.has("message_popup_get_popup_notifications"),
        }
        return {**nx.describe(), "capabilities": capabilities, "cache_ttls": nx.ttl.__dict__}
