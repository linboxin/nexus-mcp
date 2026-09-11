from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .common import FileRef, Freshness, When

SubmissionState = Literal["not_started", "draft", "submitted", "graded", "overdue"]


class SubmissionStatus(BaseModel):
    assignment_id: int
    status: SubmissionState
    detail: str
    submission_required: bool = True
    moodle_submission_status: str | None = None  # new | draft | submitted | reopened
    moodle_grading_status: str | None = None
    submitted_at: When | None = None
    last_modified: When | None = None
    graded: bool = False
    grade: str | None = None
    graded_at: When | None = None
    feedback: str | None = None
    extension_due: When | None = None
    can_submit: bool | None = None
    can_edit: bool | None = None
    locked: bool | None = None
    attempt_number: int | None = None
    freshness: Freshness | None = None


class Assignment(BaseModel):
    id: int  # assignment instance id (what mod_assign functions take)
    cmid: int  # course-module id (what the URL uses)
    course_id: int
    course: str
    course_short: str | None = None
    name: str
    description: str | None = None
    due: When | None = None
    original_due: When | None = None  # set when a personal extension moved the due date
    opens: When | None = None
    cutoff: When | None = None
    grading_due: When | None = None
    points: float | None = None
    grade_type: str = "none"  # points | scale | none
    url: str
    submission_required: bool = True
    submission_types: list[str] = Field(default_factory=list)
    time_limit_minutes: int | None = None
    team_submission: bool = False
    attachments: list[FileRef] = Field(default_factory=list)
    status: SubmissionStatus | None = None
    status_note: str | None = None
    is_overdue: bool = False
    can_still_submit: bool | None = None


class AssignmentDetail(Assignment):
    instructions: str | None = None
    activity_instructions: str | None = None
    submission_requirements: dict[str, Any] = Field(default_factory=dict)
    grading: dict[str, Any] = Field(default_factory=dict)
    resources: list[FileRef] = Field(default_factory=list)
