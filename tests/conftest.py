"""Mocked Moodle for the test-suite. No network, no credentials."""

from __future__ import annotations

import urllib.parse
from typing import Any, Callable

import httpx
import pytest
import respx

from nexus_mcp.config import CacheTTLs, Settings
from nexus_mcp.moodle.nexus import Nexus

SITE = "https://nexus.test"
TOKEN = "goodtoken"
NOW = 1789142400  # 2026-09-11 12:00 America/New_York (16:00 UTC)
HOUR = 3600
DAY = 86400

FUNCTIONS = [
    "core_webservice_get_site_info",
    "core_user_get_users_by_field",
    "core_enrol_get_users_courses",
    "core_course_get_courses_by_field",
    "core_course_get_contents",
    "core_course_get_course_module",
    "core_course_get_updates_since",
    "mod_assign_get_assignments",
    "mod_assign_get_submission_status",
    "core_calendar_get_calendar_events",
    "core_calendar_get_action_events_by_timesort",
    "gradereport_overview_get_course_grades",
    "gradereport_user_get_grade_items",
    "mod_forum_get_forums_by_courses",
    "mod_forum_get_forum_discussions",
    "message_popup_get_popup_notifications",
    "mod_page_get_pages_by_courses",
]


def site_info(functions: list[str] | None = None) -> dict[str, Any]:
    return {
        "sitename": "Union's Learning Management System",
        "username": "student1",
        "firstname": "Sam",
        "lastname": "Student",
        "fullname": "Sam Student",
        "lang": "en_us",
        "userid": 7,
        "siteurl": SITE,
        "userpictureurl": "",
        "functions": [{"name": f, "version": "2024100700"} for f in (functions if functions is not None else FUNCTIONS)],
        "downloadfiles": 1,
        "uploadfiles": 1,
        "release": "4.5.4 (Build: 20250414)",
        "version": "2024100704",
        "userissiteadmin": False,
        "theme": "boost",
    }


class FakeMoodle:
    def __init__(self) -> None:
        self.responses: dict[str, Any] = {}
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.files: dict[str, tuple[bytes, str]] = {}
        self.token = TOKEN

    def on(self, fn: str, payload: Any) -> "FakeMoodle":
        self.responses[fn] = payload
        return self

    def error(self, fn: str, errorcode: str, message: str = "", exception: str = "moodle_exception") -> "FakeMoodle":
        return self.on(fn, {"exception": exception, "errorcode": errorcode, "message": message or errorcode})

    def calls_to(self, fn: str) -> list[dict[str, str]]:
        return [form for name, form in self.calls if name == fn]

    def ws_handler(self, request: httpx.Request) -> httpx.Response:
        form = dict(urllib.parse.parse_qsl(request.content.decode()))
        fn = form.get("wsfunction", "")
        self.calls.append((fn, form))
        if form.get("wstoken") != self.token:
            return httpx.Response(200, json={"exception": "moodle_exception", "errorcode": "invalidtoken", "message": "Invalid token - token not found"})
        if fn not in self.responses:
            return httpx.Response(200, json={"exception": "webservice_access_exception", "errorcode": "accessexception", "message": f"Access control exception ({fn} not mocked)"})
        payload = self.responses[fn]
        if callable(payload):
            payload = payload(form)
        if isinstance(payload, httpx.Response):
            return payload
        return httpx.Response(200, json=payload)

    def file_handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?", 1)[0]
        if url in self.files:
            body, ctype = self.files[url]
            return httpx.Response(200, content=body, headers={"content-type": ctype})
        return httpx.Response(404)


@pytest.fixture
def fake() -> Any:
    fm = FakeMoodle()
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        router.post(SITE + "/webservice/rest/server.php").mock(side_effect=fm.ws_handler)
        router.get(url__regex=r"https://nexus\.test/webservice/pluginfile\.php/.*").mock(side_effect=fm.file_handler)
        fm.on("core_webservice_get_site_info", site_info())
        fm.on("core_user_get_users_by_field", [{"id": 7, "timezone": "America/New_York"}])
        yield fm


def make_settings(**overrides: Any) -> Settings:
    cache = overrides.pop("cache", CacheTTLs(courses=900, course_info=900, assignments=120, grades=0, submission=0, calendar=120, announcements=120, materials=600))
    return Settings(base_url=SITE, token=TOKEN, cache=cache, **overrides)


def make_nexus(settings: Settings | None = None, token: str = TOKEN, now: int = NOW) -> Nexus:
    return Nexus.from_settings(settings or make_settings(), token, clock=lambda: float(now))


@pytest.fixture
async def nexus(fake: FakeMoodle) -> Any:
    nx = make_nexus()
    await nx.ensure_ready()
    yield nx
    await nx.aclose()


# --------------------------------------------------------------------------- #
# sample payload builders
# --------------------------------------------------------------------------- #


def course(id: int, fullname: str, shortname: str | None = None, *, start: int = NOW - 30 * DAY, end: int = NOW + 90 * DAY, **extra: Any) -> dict[str, Any]:
    data = {"id": id, "fullname": fullname, "displayname": fullname, "shortname": shortname or fullname.split(" ")[0], "startdate": start, "enddate": end, "hidden": False, "visible": 1, "progress": 40, "completed": False, "summary": "", "category": 3}
    data.update(extra)
    return data


def assignment(id: int, course_id: int, name: str, *, due: int | None, cmid: int | None = None, grade: float = 100, nosubmissions: int = 0, cutoff: int = 0, intro: str = "<p>Do the thing.</p>", **extra: Any) -> dict[str, Any]:
    data = {
        "id": id, "cmid": cmid or id * 10, "course": course_id, "name": name, "nosubmissions": nosubmissions,
        "duedate": due or 0, "allowsubmissionsfromdate": 0, "cutoffdate": cutoff, "gradingduedate": 0, "grade": grade,
        "intro": intro, "introformat": 1, "introfiles": [], "introattachments": [], "timemodified": NOW - DAY,
        "teamsubmission": 0, "requiresubmissionstatement": 0, "submissiondrafts": 1, "maxattempts": -1, "timelimit": 0,
        "configs": [
            {"plugin": "onlinetext", "subtype": "assignsubmission", "name": "enabled", "value": "1"},
            {"plugin": "file", "subtype": "assignsubmission", "name": "enabled", "value": "1"},
            {"plugin": "file", "subtype": "assignsubmission", "name": "maxfilesubmissions", "value": "2"},
            {"plugin": "file", "subtype": "assignsubmission", "name": "filetypeslist", "value": "pdf"},
        ],
    }
    data.update(extra)
    return data


def assignments_payload(courses: dict[int, tuple[str, list[dict[str, Any]]]]) -> Callable[[dict[str, str]], dict[str, Any]]:
    """Return a responder that honours ``courseids[n]`` filters."""

    def respond(form: dict[str, str]) -> dict[str, Any]:
        wanted = {int(v) for k, v in form.items() if k.startswith("courseids[")}
        out = []
        for cid, (name, items) in courses.items():
            if wanted and cid not in wanted:
                continue
            out.append({"id": cid, "fullname": name, "shortname": name.split(" ")[0], "timemodified": NOW, "assignments": items})
        return {"courses": out, "warnings": []}

    return respond


def submission(status: str = "new", *, graded: bool = False, grade: str | None = None, grade_display: str | None = None, extension: int = 0, cansubmit: bool = True, canedit: bool = True, timemodified: int = NOW - HOUR, feedback_comment: str | None = None, gradingstatus: str | None = None) -> dict[str, Any]:
    last: dict[str, Any] = {
        "submission": {"id": 1, "userid": 7, "attemptnumber": 0, "timecreated": timemodified, "timemodified": timemodified, "status": status, "groupid": 0, "assignment": 1, "latest": 1, "plugins": []},
        "submissionsenabled": True, "locked": False, "graded": graded, "canedit": canedit, "caneditowner": canedit, "cansubmit": cansubmit,
        "extensionduedate": extension, "blindmarking": False, "gradingstatus": gradingstatus or ("graded" if graded else "notgraded"), "usergroups": [],
    }
    payload: dict[str, Any] = {"lastattempt": last, "warnings": []}
    if graded or grade is not None:
        payload["feedback"] = {
            "grade": {"id": 5, "assignment": 1, "userid": 7, "attemptnumber": 0, "timecreated": NOW - HOUR, "timemodified": NOW - HOUR, "grader": 2, "grade": grade or "85.00000"},
            "gradefordisplay": grade_display or "85.00 / 100.00",
            "gradeddate": NOW - HOUR,
            "plugins": [{"type": "comments", "name": "Feedback comments", "editorfields": [{"name": "comments", "text": feedback_comment or "", "format": 1}]}],
        }
    return payload


def submissions_by_id(table: dict[int, dict[str, Any]]) -> Callable[[dict[str, str]], Any]:
    def respond(form: dict[str, str]) -> Any:
        aid = int(form.get("assignid", "0"))
        if aid not in table:
            return {"exception": "moodle_exception", "errorcode": "invalidrecord", "message": "Can't find data record in database table assign."}
        return table[aid]

    return respond


def section(id: int, name: str, modules: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": id, "name": name, "visible": 1, "summary": "", "summaryformat": 1, "section": id, "hiddenbynumsections": 0, "uservisible": True, "modules": modules}


def module(id: int, modname: str, name: str, *, instance: int | None = None, contents: list[dict[str, Any]] | None = None, description: str | None = None, url: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"id": id, "url": url or f"{SITE}/mod/{modname}/view.php?id={id}", "name": name, "instance": instance or id, "modname": modname, "modplural": modname, "visible": 1, "uservisible": True, "visibleoncoursepage": 1, "modicon": "", "indent": 0, "onclick": "", "afterlink": None, "customdata": "\"\"", "noviewlink": modname == "label", "completion": 0}
    if contents is not None:
        data["contents"] = contents
    if description is not None:
        data["description"] = description
    return data


def file_content(filename: str, path: str, *, mimetype: str = "application/pdf", size: int = 1000) -> dict[str, Any]:
    return {"type": "file", "filename": filename, "filepath": "/", "filesize": size, "fileurl": f"{SITE}/webservice/pluginfile.php/{path}/{filename}", "timecreated": NOW - DAY, "timemodified": NOW - DAY, "sortorder": 0, "mimetype": mimetype, "isexternalfile": False, "userid": 2, "author": "Teacher", "license": "unknown"}
