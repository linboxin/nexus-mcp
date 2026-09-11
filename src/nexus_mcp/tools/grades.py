from __future__ import annotations

from typing import Any

from .. import runtime
from ._common import READ_ONLY, nexus_tool


def register(server: Any) -> None:
    @server.tool(name="current_grades", annotations=READ_ONLY)
    @nexus_tool
    async def current_grades(include_past: bool = False) -> dict[str, Any]:
        """Current course total for every course the student can see grades in. Courses
        whose gradebook is hidden are listed with access="hidden" rather than omitted."""
        nx = await runtime.get_nexus()
        return await nx.grades.current_grades(include_past=include_past)

    @server.tool(name="course_grade", annotations=READ_ONLY)
    @nexus_tool
    async def course_grade(course_id: int) -> dict[str, Any]:
        """Grade breakdown for one course: course total, percentage/letter when shown, and
        every grade item (assignments, quizzes, categories) with grade, range, weight,
        feedback and dates."""
        nx = await runtime.get_nexus()
        grade = await nx.grades.course_grade(course_id)
        return grade.model_dump(mode="json")

    @server.tool(name="grade_history", annotations=READ_ONLY)
    @nexus_tool
    async def grade_history(course_id: int) -> dict[str, Any]:
        """What Nexus exposes about grade changes over time for a course. Moodle has no
        grade-history web service, so this returns each item's current grade with its
        graded/submitted date (newest first) and says so explicitly."""
        nx = await runtime.get_nexus()
        return await nx.grades.grade_history(course_id)
