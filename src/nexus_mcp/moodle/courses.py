"""Courses + course contents (core_enrol / core_course functions)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterable

from ..errors import NexusNotFoundError
from ..htmltext import html_to_text
from ..models.course import Course, CourseDetail, CourseModule, CourseSection
from .common import file_ref

if TYPE_CHECKING:
    from .nexus import Nexus

ACTIVITY_MODULES = {
    "assign", "quiz", "forum", "workshop", "lesson", "h5pactivity", "hvp", "lti", "choice", "feedback",
    "scorm", "wiki", "glossary", "data", "turnitintooltwo", "bigbluebuttonbn", "chat", "survey", "zoom",
    "attendance", "questionnaire", "kalvidassign", "vpl",
}
MATERIAL_MODULES = {"resource", "folder", "page", "book", "url", "label", "imscp", "lightboxgallery", "kalvidres"}
COMPLETION_STATES = {0: "incomplete", 1: "complete", 2: "complete_pass", 3: "complete_fail"}
CLASSIFICATIONS = ("inprogress", "past", "future", "hidden", "all")
_TERM_RE = re.compile(r"^(\d{2})[-/](FA|WI|SP|SU)\.", re.IGNORECASE)
_TERM_NAMES = {"FA": "Fall", "WI": "Winter", "SP": "Spring", "SU": "Summer"}


def parse_term(short_name: str | None) -> str | None:
    """Union short names encode the term: ``26/FA.CSC-240-01`` / ``25-WI.CSC-108-01`` -> ``Fall 2026``.

    Non-academic enrolments (trainings, campus resources) have no term code.
    """
    match = _TERM_RE.match(short_name or "")
    if not match:
        return None
    return f"{_TERM_NAMES[match.group(2).upper()]} 20{match.group(1)}"


def classify_course(raw: dict[str, Any], now_ts: int) -> str:
    """Mirror Moodle's timeline classification (past / inprogress / future / hidden)."""
    if raw.get("hidden"):
        return "hidden"
    if raw.get("completed"):
        return "past"
    end = int(raw.get("enddate") or 0)
    start = int(raw.get("startdate") or 0)
    if end and end < now_ts:
        return "past"
    if start and start > now_ts:
        return "future"
    return "inprogress"


class CourseService:
    def __init__(self, nx: "Nexus") -> None:
        self.nx = nx

    # -- raw ---------------------------------------------------------------
    async def raw_user_courses(self) -> list[dict[str, Any]]:
        nx = self.nx
        nx.require("core_enrol_get_users_courses", feature="listing courses")

        async def fetch() -> list[dict[str, Any]]:
            payload = await nx.client.call("core_enrol_get_users_courses", userid=nx.site.user_id, returnusercount=0)
            return list(payload or [])

        value, _ = await nx.cache.get_or_fetch(("user_courses",), nx.ttl.courses, fetch)
        return value

    async def raw_course_fields(self, course_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
        ids = tuple(sorted({int(i) for i in course_ids}))
        if not ids or not self.nx.has("core_course_get_courses_by_field"):
            return {}

        async def fetch() -> dict[int, dict[str, Any]]:
            payload = await self.nx.client.call(
                "core_course_get_courses_by_field", field="ids", value=",".join(map(str, ids))
            )
            return {int(c["id"]): c for c in (payload or {}).get("courses", [])}

        value, _ = await self.nx.cache.get_or_fetch(("course_fields", ids), self.nx.ttl.course_info, fetch)
        return value

    async def contents(self, course_id: int) -> list[dict[str, Any]]:
        nx = self.nx
        nx.require("core_course_get_contents", feature="course contents")
        course_id = int(course_id)

        async def fetch() -> list[dict[str, Any]]:
            return list(await nx.client.call("core_course_get_contents", courseid=course_id) or [])

        value, _ = await nx.cache.get_or_fetch(("contents", course_id), nx.ttl.course_info, fetch)
        return value

    # -- builders ----------------------------------------------------------
    def build_course(self, raw: dict[str, Any], extra: dict[str, Any] | None, classification: str) -> Course:
        nx = self.nx
        extra = extra or {}
        teachers = [c.get("fullname", "") for c in extra.get("contacts", []) if c.get("fullname")]
        course_id = int(raw["id"])
        term = parse_term(raw.get("shortname"))
        return Course(
            id=course_id,
            name=raw.get("fullname") or raw.get("displayname") or raw.get("shortname") or f"Course {course_id}",
            short_name=raw.get("shortname") or "",
            term=term,
            academic=term is not None,
            teacher=teachers[0] if teachers else None,
            teachers=teachers,
            url=f"{nx.site_url}/course/view.php?id={course_id}",
            category=extra.get("categoryname") or None,
            starts=nx.when(raw.get("startdate")),
            ends=nx.when(raw.get("enddate")),
            classification=classification,
            progress=raw.get("progress") if isinstance(raw.get("progress"), (int, float)) else None,
            hidden=bool(raw.get("hidden")),
            summary=html_to_text(raw.get("summary") or extra.get("summary"), max_len=600) or None,
        )

    def build_module(self, raw: dict[str, Any], section_name: str | None) -> CourseModule:
        nx = self.nx
        files = [file_ref(nx, f) for f in raw.get("contents", []) or [] if f.get("type") == "file"]
        dates = [
            {"label": d.get("label"), "at": (nx.when(d.get("timestamp")) or None)}
            for d in raw.get("dates", []) or []
        ]
        dates = [{"label": d["label"], "at": d["at"].model_dump() if d["at"] else None} for d in dates]
        completion = None
        cdata = raw.get("completiondata")
        if isinstance(cdata, dict) and "state" in cdata:
            completion = COMPLETION_STATES.get(int(cdata.get("state") or 0), "unknown")
        return CourseModule(
            id=int(raw["id"]),
            instance=int(raw["instance"]) if raw.get("instance") is not None else None,
            name=raw.get("name") or "",
            type=raw.get("modname") or "unknown",
            url=raw.get("url"),
            description=html_to_text(raw.get("description"), max_len=500) or None,
            section=section_name,
            visible=bool(raw.get("uservisible", raw.get("visible", 1))),
            files=files,
            dates=dates,
            completion=completion,
            availability=html_to_text(raw.get("availabilityinfo"), max_len=200) or None,
        )

    # -- public ------------------------------------------------------------
    async def list_courses(self, classification: str = "inprogress", *, academic_only: bool = False) -> list[Course]:
        """Courses by Moodle's date classification; ``academic_only`` keeps term-coded courses."""
        if classification not in CLASSIFICATIONS:
            raise ValueError(f"classification must be one of {', '.join(CLASSIFICATIONS)}")
        raw = await self.raw_user_courses()
        fields = await self.raw_course_fields([c["id"] for c in raw])
        now_ts = self.nx.now_ts()
        courses: list[Course] = []
        for c in raw:
            cls = classify_course(c, now_ts)
            if classification != "all" and cls != classification:
                continue
            course = self.build_course(c, fields.get(int(c["id"])), cls)
            if academic_only and not course.academic:
                continue
            courses.append(course)
        courses.sort(key=lambda course: (not course.academic, course.name.lower()))
        return courses

    async def current_courses(self) -> list[Course]:
        """In-progress courses; falls back to everything if Nexus has no term dates."""
        current = await self.list_courses("inprogress")
        if current:
            return current
        return [c for c in await self.list_courses("all") if c.classification != "hidden"]

    async def current_course_ids(self) -> list[int]:
        return [c.id for c in await self.current_courses()]

    async def lookup(self) -> dict[int, Course]:
        return {c.id: c for c in await self.list_courses("all")}

    async def course_name(self, course_id: int, lookup: dict[int, Course] | None = None) -> str:
        table = lookup if lookup is not None else await self.lookup()
        course = table.get(int(course_id))
        return course.name if course else f"Course {course_id}"

    async def get_course(self, course_id: int) -> CourseDetail:
        course_id = int(course_id)
        raw_courses = await self.raw_user_courses()
        match = next((c for c in raw_courses if int(c["id"]) == course_id), None)
        if match is None:
            raise NexusNotFoundError(
                f"Course {course_id} is not among your enrolled Nexus courses.", details={"course_id": course_id}
            )
        extra = (await self.raw_course_fields([course_id])).get(course_id)
        course = self.build_course(match, extra, classify_course(match, self.nx.now_ts()))
        sections_raw = await self.contents(course_id)
        sections: list[CourseSection] = []
        materials: list[CourseModule] = []
        activities: list[CourseModule] = []
        for s in sections_raw:
            name = s.get("name") or f"Section {s.get('section', '')}".strip()
            modules = [self.build_module(m, name) for m in s.get("modules", []) or []]
            sections.append(
                CourseSection(
                    id=int(s.get("id") or 0),
                    name=name,
                    summary=html_to_text(s.get("summary"), max_len=400) or None,
                    modules=modules,
                )
            )
            for m in modules:
                if m.type in MATERIAL_MODULES or (m.files and m.type not in ACTIVITY_MODULES):
                    materials.append(m)
                elif m.type in ACTIVITY_MODULES:
                    activities.append(m)
        description = html_to_text((extra or {}).get("summary") or match.get("summary"), max_len=3000) or None
        return CourseDetail(
            **course.model_dump(),
            description=description,
            instructors=course.teachers,
            sections=sections,
            materials=materials,
            activities=activities,
        )
