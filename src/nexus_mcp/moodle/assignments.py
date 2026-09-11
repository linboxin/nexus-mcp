"""Assignments + submission status (mod_assign functions).

``mod_assign_get_assignments`` already applies the student's user/group
overrides (Moodle calls ``update_effective_access``), so ``duedate`` here is
the student's effective due date. Personal *extensions* are only visible via
``mod_assign_get_submission_status`` (``lastattempt.extensionduedate``), which
is why every status-bearing result re-derives the due date.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from ..cache import CacheMeta
from ..errors import NexusError, NexusNotFoundError, NexusPermissionError, NexusUnsupportedError
from ..htmltext import html_to_text
from ..models.assignment import Assignment, AssignmentDetail, SubmissionStatus
from .common import as_float, file_ref

if TYPE_CHECKING:
    from .nexus import Nexus

def summarize_warnings(raw: list[dict[str, Any]]) -> list[str]:
    """Collapse Moodle's per-module warnings into one line per distinct message.

    A real account yields dozens of identical "No access rights in module context"
    warnings (assignments in hidden/finished modules); one counted line is enough.
    """
    counts: dict[str, int] = {}
    for w in raw or []:
        message = str(w.get("message") or w.get("warningcode") or "warning").strip()
        counts[message] = counts.get(message, 0) + 1
    out = []
    for message, n in counts.items():
        if n == 1:
            out.append(message)
        else:
            out.append(f"{n} assignments skipped by Nexus: {message} (hidden or inaccessible modules)")
    return out


SUBMISSION_PLUGIN_LABELS = {
    "onlinetext": "online text",
    "file": "file upload",
    "comments": "submission comments",
    "onlineaudio": "audio recording",
    "onlinevideo": "video recording",
}


def normalize_submission(
    raw: dict[str, Any],
    *,
    assignment: Assignment,
    now_ts: int,
    nx: "Nexus",
    meta: CacheMeta | None = None,
) -> SubmissionStatus:
    """Turn Moodle's ``mod_assign_get_submission_status`` payload into one of
    not_started / draft / submitted / graded / overdue, using only Moodle data."""
    last = raw.get("lastattempt") or {}
    sub = last.get("submission") or last.get("teamsubmission") or {}
    sub_status = str(sub.get("status") or "new").lower()
    grading_status = last.get("gradingstatus")
    feedback = raw.get("feedback") or {}
    grade_info = feedback.get("grade") or {}
    grade_display = html_to_text(feedback.get("gradefordisplay")) or None
    grade_value = as_float(grade_info.get("grade"))
    has_grade = grade_value is not None and grade_value >= 0
    graded = bool(last.get("graded")) or grading_status in ("graded", "released") or has_grade

    extension_ts = int(last.get("extensionduedate") or 0)
    due_ts = assignment.due.unix if assignment.due else 0
    effective_due = extension_ts if extension_ts > 0 else due_ts
    past_due = bool(effective_due) and effective_due < now_ts
    cutoff_ts = assignment.cutoff.unix if assignment.cutoff else 0
    cutoff_passed = bool(cutoff_ts) and cutoff_ts < now_ts
    required = assignment.submission_required

    if sub_status == "submitted":
        status = "graded" if graded else "submitted"
    elif graded:
        status = "graded"
    elif past_due and required:
        status = "overdue"
    elif sub_status in ("draft", "reopened"):
        status = "draft"
    else:
        status = "not_started"

    when_due = nx.when(effective_due)
    rel = when_due.relative if when_due else "no due date"
    if status == "graded":
        detail = f"Graded: {grade_display}" if grade_display else "Graded"
    elif status == "submitted":
        detail = f"Submitted, awaiting grade (due {rel})"
    elif status == "overdue":
        what = "draft saved but never submitted" if sub_status == "draft" else "nothing submitted"
        detail = f"Due {rel}; {what}"
        if cutoff_passed:
            detail += "; cut-off date passed, Nexus no longer accepts submissions"
    elif status == "draft":
        detail = f"Draft saved, not submitted (due {rel})"
    else:
        detail = "Not started" if required else "No online submission required for this assignment"
        if required and when_due:
            detail += f" (due {rel})"

    comments = None
    for plugin in feedback.get("plugins", []) or []:
        if plugin.get("type") == "comments":
            for field in plugin.get("editorfields", []) or []:
                text = html_to_text(field.get("text"), max_len=2000)
                if text:
                    comments = text
    attempt = grade_info.get("attemptnumber", sub.get("attemptnumber"))
    return SubmissionStatus(
        assignment_id=assignment.id,
        status=status,
        detail=detail,
        submission_required=required,
        moodle_submission_status=sub_status,
        moodle_grading_status=grading_status,
        submitted_at=nx.when(sub.get("timemodified")) if sub_status == "submitted" else None,
        last_modified=nx.when(sub.get("timemodified")),
        graded=graded,
        grade=grade_display,
        graded_at=nx.when(feedback.get("gradeddate")),
        feedback=comments,
        extension_due=nx.when(extension_ts) if extension_ts else None,
        can_submit=bool(last["cansubmit"]) if "cansubmit" in last else None,
        can_edit=bool(last["canedit"]) if "canedit" in last else None,
        locked=bool(last["locked"]) if "locked" in last else None,
        attempt_number=int(attempt) + 1 if attempt is not None else None,
        freshness=nx.freshness(meta) if meta else None,
    )


class AssignmentService:
    def __init__(self, nx: "Nexus") -> None:
        self.nx = nx

    # -- raw ---------------------------------------------------------------
    async def raw_assignments(self, course_ids: list[int] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
        nx = self.nx
        nx.require("mod_assign_get_assignments", feature="assignments")
        ids = tuple(sorted({int(i) for i in (course_ids or [])}))

        async def fetch() -> dict[str, Any]:
            payload = await nx.client.call(
                "mod_assign_get_assignments", courseids=list(ids), includenotenrolledcourses=0
            )
            rows: list[dict[str, Any]] = []
            for course in (payload or {}).get("courses", []):
                for a in course.get("assignments", []):
                    item = dict(a)
                    item["_course_name"] = course.get("fullname")
                    item["_course_shortname"] = course.get("shortname")
                    rows.append(item)
            return {"rows": rows, "warnings": summarize_warnings((payload or {}).get("warnings", []))}

        value, _ = await nx.cache.get_or_fetch(("assignments", ids), nx.ttl.assignments, fetch)
        return value["rows"], value["warnings"]

    async def raw_submission_status(self, assignment_id: int) -> tuple[dict[str, Any], CacheMeta]:
        nx = self.nx
        nx.require("mod_assign_get_submission_status", feature="submission status")
        assignment_id = int(assignment_id)

        async def fetch() -> dict[str, Any]:
            return dict(await nx.client.call("mod_assign_get_submission_status", assignid=assignment_id) or {})

        return await nx.cache.get_or_fetch(("submission", assignment_id), nx.ttl.submission, fetch)

    # -- builders ----------------------------------------------------------
    def build(self, a: dict[str, Any]) -> Assignment:
        nx = self.nx
        grade = as_float(a.get("grade"))
        points: float | None = None
        grade_type = "none"
        if grade is not None and grade > 0:
            points, grade_type = grade, "points"
        elif grade is not None and grade < 0:
            grade_type = "scale"
        configs = a.get("configs") or []
        types = sorted(
            {
                SUBMISSION_PLUGIN_LABELS.get(c.get("plugin", ""), c.get("plugin", ""))
                for c in configs
                if c.get("subtype") == "assignsubmission" and c.get("name") == "enabled" and str(c.get("value")) == "1"
            }
        )
        cmid = int(a.get("cmid") or 0)
        return Assignment(
            id=int(a["id"]),
            cmid=cmid,
            course_id=int(a.get("course") or 0),
            course=a.get("_course_name") or "",
            course_short=a.get("_course_shortname"),
            name=a.get("name") or "",
            description=html_to_text(a.get("intro"), max_len=800) or None,
            due=nx.when(a.get("duedate")),
            opens=nx.when(a.get("allowsubmissionsfromdate")),
            cutoff=nx.when(a.get("cutoffdate")),
            grading_due=nx.when(a.get("gradingduedate")),
            points=points,
            grade_type=grade_type,
            url=f"{nx.site_url}/mod/assign/view.php?id={cmid}",
            submission_required=not bool(a.get("nosubmissions")),
            submission_types=types,
            time_limit_minutes=int(a["timelimit"]) // 60 if a.get("timelimit") else None,
            team_submission=bool(a.get("teamsubmission")),
            attachments=[file_ref(nx, f) for f in (a.get("introattachments") or [])],
        )

    async def attach_statuses(self, assignments: list[Assignment]) -> None:
        nx = self.nx
        if not assignments:
            return
        if not nx.has("mod_assign_get_submission_status"):
            for a in assignments:
                a.status_note = "Submission status unavailable: mod_assign_get_submission_status is not enabled on Nexus."
            return
        now_ts = nx.now_ts()

        async def one(a: Assignment) -> None:
            try:
                raw, meta = await self.raw_submission_status(a.id)
            except (NexusPermissionError, NexusNotFoundError, NexusUnsupportedError) as exc:
                a.status_note = exc.message
                return
            a.status = normalize_submission(raw, assignment=a, now_ts=now_ts, nx=nx, meta=meta)
            if a.status.extension_due:
                a.original_due = a.due
                a.due = a.status.extension_due
            a.is_overdue = a.status.status == "overdue"
            if a.cutoff and a.cutoff.unix < now_ts:
                a.can_still_submit = False
            elif a.status.can_submit is not None:
                a.can_still_submit = a.status.can_submit
            elif a.status.status in ("submitted", "graded"):
                a.can_still_submit = None

        await asyncio.gather(*(one(a) for a in assignments))

    # -- public ------------------------------------------------------------
    async def list(
        self, course_id: int | None = None, *, current_only: bool = True, with_status: bool = False
    ) -> tuple[list[Assignment], list[str]]:
        if course_id is not None:
            course_ids: list[int] | None = [int(course_id)]
        elif current_only:
            course_ids = await self.nx.courses.current_course_ids()
        else:
            course_ids = None
        rows, warnings = await self.raw_assignments(course_ids)
        assignments = [self.build(a) for a in rows]
        if with_status:
            await self.attach_statuses(assignments)
        assignments.sort(key=lambda a: (a.due.unix if a.due else 2**40, a.name.lower()))
        return assignments, warnings

    async def upcoming(
        self, days: int = 7, course_id: int | None = None, *, include_submitted: bool = True
    ) -> tuple[list[Assignment], list[str]]:
        rows, warnings = await self.raw_assignments([int(course_id)] if course_id is not None else None)
        now_ts = self.nx.now_ts()
        horizon = now_ts + max(1, int(days)) * 86400
        candidates = [
            self.build(a)
            for a in rows
            if int(a.get("duedate") or 0) and now_ts <= int(a["duedate"]) <= horizon
        ]
        await self.attach_statuses(candidates)
        if not include_submitted:
            candidates = [a for a in candidates if not (a.status and a.status.status in ("submitted", "graded"))]
        candidates.sort(key=lambda a: (a.due.unix if a.due else 2**40, a.course, a.name))
        return candidates, warnings

    async def overdue(
        self, course_id: int | None = None, *, max_age_days: int = 120
    ) -> tuple[list[Assignment], list[str]]:
        course_ids = [int(course_id)] if course_id is not None else await self.nx.courses.current_course_ids()
        rows, warnings = await self.raw_assignments(course_ids)
        now_ts = self.nx.now_ts()
        floor = now_ts - max(1, int(max_age_days)) * 86400
        candidates = [
            self.build(a)
            for a in rows
            if int(a.get("duedate") or 0)
            and floor <= int(a["duedate"]) < now_ts
            and not a.get("nosubmissions")
        ]
        await self.attach_statuses(candidates)
        overdue = [a for a in candidates if a.status is not None and a.status.status == "overdue"]
        unknown = [a for a in candidates if a.status is None]
        if unknown:
            warnings.append(
                f"{len(unknown)} past-due assignment(s) could not be checked for submission status: "
                + "; ".join(f"{a.name} ({a.status_note})" for a in unknown[:5])
            )
        overdue.sort(key=lambda a: (a.due.unix if a.due else 0))
        return overdue, warnings

    async def get(self, assignment_id: int) -> Assignment:
        assignment_id = int(assignment_id)
        rows, _ = await self.raw_assignments(None)
        for a in rows:
            if int(a["id"]) == assignment_id:
                return self.build(a)
        for a in rows:  # be forgiving: the AI may pass the course-module id
            if int(a.get("cmid") or 0) == assignment_id:
                return self.build(a)
        raise NexusNotFoundError(
            f"No assignment with id {assignment_id} is visible to your account.", details={"assignment_id": assignment_id}
        )

    async def submission_status(self, assignment_id: int) -> SubmissionStatus:
        assignment = await self.get(assignment_id)
        raw, meta = await self.raw_submission_status(assignment.id)
        return normalize_submission(raw, assignment=assignment, now_ts=self.nx.now_ts(), nx=self.nx, meta=meta)

    async def details(self, assignment_id: int) -> AssignmentDetail:
        assignment = await self.get(assignment_id)
        rows, _ = await self.raw_assignments(None)
        raw = next(a for a in rows if int(a["id"]) == assignment.id)
        status_error: str | None = None
        raw_status: dict[str, Any] = {}
        try:
            await self.attach_statuses([assignment])
            if assignment.status is None:
                status_error = assignment.status_note
            else:
                raw_status, _ = await self.raw_submission_status(assignment.id)
        except NexusError as exc:
            status_error = exc.message
        configs = {(c.get("subtype"), c.get("plugin"), c.get("name")): c.get("value") for c in raw.get("configs", []) or []}
        requirements: dict[str, Any] = {
            "submission_types": assignment.submission_types,
            "submission_required": assignment.submission_required,
            "requires_submission_statement": bool(raw.get("requiresubmissionstatement")),
            "team_submission": assignment.team_submission,
            "max_attempts": raw.get("maxattempts"),
            "submission_drafts_enabled": bool(raw.get("submissiondrafts")),
        }
        if ("assignsubmission", "file", "maxfilesubmissions") in configs:
            requirements["max_files"] = configs[("assignsubmission", "file", "maxfilesubmissions")]
        if ("assignsubmission", "file", "filetypeslist") in configs:
            requirements["accepted_file_types"] = configs[("assignsubmission", "file", "filetypeslist")] or "any"
        if ("assignsubmission", "file", "maxsubmissionsizebytes") in configs:
            requirements["max_file_size_bytes"] = configs[("assignsubmission", "file", "maxsubmissionsizebytes")]
        if ("assignsubmission", "onlinetext", "wordlimitenabled") in configs and str(
            configs[("assignsubmission", "onlinetext", "wordlimitenabled")]
        ) == "1":
            requirements["word_limit"] = configs.get(("assignsubmission", "onlinetext", "wordlimit"))
        grading: dict[str, Any] = {
            "max_points": assignment.points,
            "grade_type": assignment.grade_type,
            "grading_due": assignment.grading_due.model_dump() if assignment.grading_due else None,
            "blind_marking": bool(raw.get("blindmarking")),
            "marking_workflow": bool(raw.get("markingworkflow")),
        }
        if assignment.status:
            grading["current_grade"] = assignment.status.grade
            grading["graded_at"] = assignment.status.graded_at.model_dump() if assignment.status.graded_at else None
            grading["feedback"] = assignment.status.feedback
        data = (raw_status or {}).get("assignmentdata") or {}
        resources = list(assignment.attachments)
        for f in data.get("attachments", {}).get("intro", []) or []:
            if f.get("fileurl") not in {r.url for r in resources}:
                resources.append(file_ref(self.nx, f))
        activity_instructions = html_to_text(data.get("activity"), max_len=6000) or None
        detail = AssignmentDetail(
            **assignment.model_dump(),
            instructions=html_to_text(raw.get("intro"), max_len=8000) or None,
            activity_instructions=activity_instructions,
            submission_requirements=requirements,
            grading=grading,
            resources=resources,
        )
        if status_error and not detail.status_note:
            detail.status_note = status_error
        return detail
