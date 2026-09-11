from __future__ import annotations

from pydantic import BaseModel, Field

from .common import Freshness, When


class GradeItem(BaseModel):
    id: int | None = None
    name: str
    type: str  # mod | category | course | manual
    module: str | None = None
    cmid: int | None = None
    grade_raw: float | None = None
    grade_display: str | None = None
    grade_min: float | None = None
    grade_max: float | None = None
    percentage: str | None = None
    weight: str | None = None
    range: str | None = None
    letter: str | None = None
    feedback: str | None = None
    graded_at: When | None = None
    submitted_at: When | None = None
    hidden: bool = False


class CourseGradeSummary(BaseModel):
    course_id: int
    course: str
    grade_display: str | None = None
    grade_raw: float | None = None
    access: str = "ok"  # ok | no_grade | hidden | unavailable
    note: str | None = None


class CourseGrade(BaseModel):
    course_id: int
    course: str
    grade_display: str | None = None
    grade_raw: float | None = None
    percentage: str | None = None
    letter: str | None = None
    items: list[GradeItem] = Field(default_factory=list)
    freshness: Freshness | None = None
    warnings: list[str] = Field(default_factory=list)
