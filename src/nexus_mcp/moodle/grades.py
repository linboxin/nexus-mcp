"""Grades (gradereport_overview / gradereport_user functions)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..cache import CacheMeta
from ..errors import NexusError, NexusPermissionError, NexusUnsupportedError
from ..htmltext import html_to_text
from ..models.common import Freshness
from ..models.grade import CourseGrade, CourseGradeSummary, GradeItem
from .common import as_float

if TYPE_CHECKING:
    from .nexus import Nexus


class GradeService:
    def __init__(self, nx: "Nexus") -> None:
        self.nx = nx

    # -- raw ---------------------------------------------------------------
    async def raw_overview(self) -> tuple[list[dict[str, Any]], CacheMeta]:
        nx = self.nx
        nx.require("gradereport_overview_get_course_grades", feature="the grade overview")

        async def fetch() -> list[dict[str, Any]]:
            payload = await nx.client.call("gradereport_overview_get_course_grades", userid=nx.site.user_id)
            return list((payload or {}).get("grades", []))

        return await nx.cache.get_or_fetch(("grade_overview",), nx.ttl.grades, fetch)

    async def raw_items(self, course_id: int) -> tuple[dict[str, Any], CacheMeta]:
        nx = self.nx
        nx.require("gradereport_user_get_grade_items", feature="course grades")
        course_id = int(course_id)

        async def fetch() -> dict[str, Any]:
            payload = await nx.client.call(
                "gradereport_user_get_grade_items", courseid=course_id, userid=nx.site.user_id
            )
            usergrades = (payload or {}).get("usergrades") or []
            result = dict(usergrades[0]) if usergrades else {"gradeitems": []}
            result["_warnings"] = [
                f"{w.get('warningcode', 'warning')}: {w.get('message', '')}" for w in (payload or {}).get("warnings", [])
            ]
            return result

        return await nx.cache.get_or_fetch(("grade_items", course_id), nx.ttl.grades, fetch)

    # -- builders ----------------------------------------------------------
    def build_item(self, raw: dict[str, Any]) -> GradeItem:
        nx = self.nx
        itemtype = raw.get("itemtype") or "unknown"
        name = raw.get("itemname") or ("Course total" if itemtype == "course" else "(unnamed)")
        return GradeItem(
            id=int(raw["id"]) if raw.get("id") is not None else None,
            name=name,
            type=itemtype,
            module=raw.get("itemmodule") or None,
            cmid=int(raw["cmid"]) if raw.get("cmid") else None,
            grade_raw=as_float(raw.get("graderaw")),
            grade_display=(raw.get("gradeformatted") or None),
            grade_min=as_float(raw.get("grademin")),
            grade_max=as_float(raw.get("grademax")),
            percentage=raw.get("percentageformatted") or None,
            weight=raw.get("weightformatted") or None,
            range=raw.get("rangeformatted") or None,
            letter=raw.get("lettergradeformatted") or None,
            feedback=html_to_text(raw.get("feedback"), max_len=1500) or None,
            graded_at=nx.when(raw.get("gradedategraded")),
            submitted_at=nx.when(raw.get("gradedatesubmitted")),
            hidden=bool(raw.get("gradeishidden")) or bool(raw.get("gradehiddenbydate")),
        )

    # -- public ------------------------------------------------------------
    async def course_grade(self, course_id: int) -> CourseGrade:
        course_id = int(course_id)
        lookup = await self.nx.courses.lookup()
        course_name = await self.nx.courses.course_name(course_id, lookup)
        raw, meta = await self.raw_items(course_id)
        items = [self.build_item(i) for i in raw.get("gradeitems", []) or []]
        total = next((i for i in items if i.type == "course"), None)
        return CourseGrade(
            course_id=course_id,
            course=course_name,
            grade_display=total.grade_display if total else None,
            grade_raw=total.grade_raw if total else None,
            percentage=total.percentage if total else None,
            letter=total.letter if total else None,
            items=items,
            freshness=self.nx.freshness(meta),
            warnings=list(raw.get("_warnings", [])),
        )

    async def current_grades(self, *, include_past: bool = False) -> dict[str, Any]:
        nx = self.nx
        courses = await nx.courses.list_courses("all")
        by_id = {c.id: c for c in courses}
        wanted = [c for c in courses if include_past or c.classification == "inprogress"] or [
            c for c in courses if c.classification != "hidden"
        ]
        summaries: list[CourseGradeSummary] = []
        warnings: list[str] = []
        freshness: Freshness | None = None
        seen: set[int] = set()
        if nx.has("gradereport_overview_get_course_grades"):
            try:
                rows, meta = await self.raw_overview()
                freshness = nx.freshness(meta)
            except NexusPermissionError as exc:
                rows = []
                warnings.append(f"Grade overview refused: {exc.message}")
            for r in rows:
                cid = int(r.get("courseid") or 0)
                if cid not in {c.id for c in wanted}:
                    continue
                seen.add(cid)
                grade = (r.get("grade") or "").strip()
                has = bool(grade) and grade != "-"
                summaries.append(
                    CourseGradeSummary(
                        course_id=cid,
                        course=by_id[cid].name if cid in by_id else f"Course {cid}",
                        grade_display=grade if has else None,
                        grade_raw=as_float(r.get("rawgrade")),
                        access="ok" if has else "no_grade",
                        note=None if has else "No course total is available yet.",
                    )
                )
        else:
            warnings.append(
                "gradereport_overview_get_course_grades is not enabled; falling back to per-course grade items."
            )
        for c in wanted:
            if c.id in seen:
                continue
            if not nx.has("gradereport_user_get_grade_items"):
                summaries.append(
                    CourseGradeSummary(course_id=c.id, course=c.name, access="unavailable", note="Grade functions not enabled on Nexus.")
                )
                continue
            try:
                detail = await self.course_grade(c.id)
                summaries.append(
                    CourseGradeSummary(
                        course_id=c.id,
                        course=c.name,
                        grade_display=detail.grade_display,
                        grade_raw=detail.grade_raw,
                        access="ok" if detail.grade_display and detail.grade_display != "-" else "no_grade",
                        note=None if detail.grade_display else "No course total is available yet.",
                    )
                )
            except NexusPermissionError as exc:
                summaries.append(
                    CourseGradeSummary(course_id=c.id, course=c.name, access="hidden", note=f"Grades hidden for this course: {exc.message}")
                )
            except (NexusUnsupportedError, NexusError) as exc:
                summaries.append(CourseGradeSummary(course_id=c.id, course=c.name, access="unavailable", note=exc.message))
        summaries.sort(key=lambda s: s.course.lower())
        return {
            "courses": [s.model_dump() for s in summaries],
            "freshness": freshness.model_dump() if freshness else None,
            "warnings": warnings,
        }

    async def grade_history(self, course_id: int) -> dict[str, Any]:
        """Moodle exposes no grade-history web service; return what *is* available honestly."""
        grade = await self.course_grade(course_id)
        dated = [i for i in grade.items if i.graded_at or i.submitted_at]
        dated.sort(key=lambda i: (i.graded_at.unix if i.graded_at else i.submitted_at.unix if i.submitted_at else 0), reverse=True)
        return {
            "course_id": grade.course_id,
            "course": grade.course,
            "history_available": False,
            "note": (
                "Nexus (Moodle) does not expose the grade-history report (gradereport_history) through web "
                "services, so previous grade values and edits cannot be retrieved. The list below is the "
                "current grade of each item with the date it was last graded/submitted, ordered newest first."
            ),
            "current_course_grade": grade.grade_display,
            "timeline": [i.model_dump() for i in dated],
            "ungraded_items": [i.name for i in grade.items if i.type != "course" and not i.graded_at],
            "freshness": grade.freshness.model_dump() if grade.freshness else None,
        }
