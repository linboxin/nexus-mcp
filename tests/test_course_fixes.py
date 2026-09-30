"""Regression tests for the Sept 2026 data-quality fixes (news box, full text, Google links,
assignment status, briefing noise, course structure)."""

import json

import httpx
import pytest
import respx

from nexus_mcp import intelligence
from nexus_mcp.google import GoogleDrive, parse_google_link
from nexus_mcp.htmltext import clean_name, clip, html_to_text
from nexus_mcp.models.event import CalendarEvent
from nexus_mcp.moodle.assignments import submission_open, summarize_warnings
from nexus_mcp.timeutil import parse_since
from nexus_mcp.watch import TextWatch
from tests.conftest import DAY, HOUR, NOW, assignment, assignments_payload, course, module, section, submission, submissions_by_id

CSC = "CSC-385 - Computer Graphics"
NEWS_V1 = "<p>News</p><ul><li>(09/14) Project 1 released.</li><li>(09/15) Starter code permissions fixed.</li></ul>"
NEWS_V2 = "<p>News</p><ul><li>(09/25) Office hours move to 1:50 pm Tuesday.</li><li>(09/14) Project 1 released.</li><li>(09/15) Starter code permissions fixed.</li></ul>"
LONG_INTRO = "<p>" + ("Part 1 details. " * 400) + "</p><p>Hand in via cs-gitlab.union.edu.</p>"


# -- text ------------------------------------------------------------------


def test_full_text_by_default_and_honest_clipping():
    long = "<p>" + "x" * 5000 + "</p>"
    assert len(html_to_text(long)) == 5000  # no silent cut
    clipped = clip("a" * 3000, 1000, hint="assignment_details(5) has the full instructions")
    assert clipped.startswith("a" * 1000) and "2,000 more characters" in clipped and "assignment_details(5)" in clipped
    assert clip("short", 100) == "short"
    assert clean_name("Software &amp; Hardware Resources") == "Software & Hardware Resources"


# -- course page: news box, subsections, entities ---------------------------


@pytest.fixture
def course_world(fake):
    state = {"news": NEWS_V1}
    fake.on("core_enrol_get_users_courses", [course(1, CSC, "26/FA.CSC-385-01"), course(9, "Academic Integrity Training", "AcademicIntegrity")])
    fake.on("core_course_get_courses_by_field", {"courses": [], "warnings": []})

    def contents(form):
        general = section(10, "General", [module(20, "label", "Student Hours", description="<p>Tue 2-3</p>")])
        general["summary"] = state["news"]
        week = section(11, "Week 1", [module(21, "resource", "Lecture 1"), module(22, "subsection", "Readings")])
        week["modules"][1]["customdata"] = json.dumps({"sectionid": "30"})
        sub = section(30, "Software &amp; Readings", [module(23, "url", "Chapter 1")])
        sub["component"] = "mod_subsection"
        return [general, week, sub]

    fake.on("core_course_get_contents", contents)
    fake.on("core_course_get_updates_since", {"instances": [{"contextlevel": "module", "id": 21, "updates": [{"name": "submissions", "itemids": [1]}, {"name": "gradeitems", "itemids": [2]}]}], "warnings": []})
    fake.on("mod_forum_get_forums_by_courses", [])
    fake.on("message_popup_get_popup_notifications", {"notifications": [], "unreadcount": 0})
    return state


async def test_course_keeps_week_structure_and_full_news(course_world, nexus):
    detail = await nexus.courses.get_course(1)
    names = [s.name for s in detail.sections]
    assert names == ["General", "Week 1"]  # the subsection is nested, not a stray top-level section
    readings = detail.sections[1].modules[1]
    assert readings.type == "subsection" and readings.subsection is not None
    assert readings.subsection.name == "Software & Readings"  # entities decoded
    assert readings.subsection.modules[0].section == "Week 1 › Software & Readings"
    assert "Starter code permissions fixed." in detail.sections[0].summary  # whole news box


async def test_news_box_edit_is_reported_with_the_new_lines(course_world, nexus):
    since = parse_since("24h", nexus.now())
    first = await nexus.notifications.course_updates(since)
    assert first["module_updates"] == []  # baseline; and "submission changed"/grade-item noise is gone
    course_world["news"] = NEWS_V2
    nexus.cache.invalidate()
    second = await nexus.notifications.course_updates(since)
    [upd] = second["module_updates"]
    assert upd["module"] == "Course page text (General)" and upd["changes"] == ["text edited"]
    assert upd["added"] == ["- (09/25) Office hours move to 1:50 pm Tuesday."] and upd["removed"] == []
    detail = await nexus.courses.get_course(1)
    assert detail.sections[0].summary_last_edit["added"] == upd["added"]


async def test_current_courses_skip_trainings(course_world, nexus):
    assert [c.short_name for c in await nexus.courses.current_courses()] == ["26/FA.CSC-385-01"]
    everything = await nexus.courses.list_courses("inprogress")
    assert len(everything) == 2  # still listed when asked for explicitly


def test_watch_baseline_then_diff(tmp_path):
    w = TextWatch(tmp_path)
    assert w.observe("k", "a\nb", now=1) is None
    assert w.observe("k", "a\nb", now=2) is None
    change = w.observe("k", "a\nc", now=3)
    assert change == {"added": ["c"], "removed": ["b"], "noticed": 3}
    assert TextWatch(tmp_path).last_change("k") == change  # persisted
    assert [k for k, _ in w.changes_since("k", 3)] == ["k"] and w.changes_since("k", 4) == []


# -- assignments -----------------------------------------------------------


@pytest.fixture
def assign_world(fake):
    fake.on("core_enrol_get_users_courses", [course(1, CSC, "26/FA.CSC-385-01")])
    fake.on("core_course_get_courses_by_field", {"courses": [], "warnings": []})
    fake.on("mod_assign_get_assignments", assignments_payload({1: (CSC, [
        assignment(1, 1, "Homework 1", due=NOW + 10 * HOUR, nosubmissions=1, intro=LONG_INTRO),
        assignment(2, 1, "CSS TRY", due=NOW + 12 * HOUR),
        assignment(3, 1, "Late but open", due=NOW - DAY),
        assignment(4, 1, "Cut off", due=NOW - 2 * DAY, cutoff=NOW - DAY),
    ])}))
    fake.on("mod_assign_get_submission_status", submissions_by_id({
        1: submission("new", cansubmit=False, canedit=False),
        2: submission("new", cansubmit=False, canedit=True),  # Moodle: cansubmit=false on every open assignment
        3: submission("new", cansubmit=False, canedit=True),
        4: submission("new", cansubmit=False, canedit=False),
    }))


async def test_gitlab_work_is_external_not_not_started(assign_world, nexus):
    items, _ = await nexus.assignments.upcoming(days=2)
    hw1 = next(a for a in items if a.name == "Homework 1")
    assert hw1.status.status == "external" and hw1.submitted_elsewhere == "cs-gitlab"
    assert "check cs-gitlab" in hw1.status.detail and hw1.can_still_submit is None
    assert "more characters" in hw1.description and "assignment_details(1)" in hw1.description
    details = await nexus.assignments.details(1)
    assert details.instructions.endswith("Hand in via cs-gitlab.union.edu.")  # full text in details


async def test_open_assignments_are_not_marked_closed(assign_world, nexus):
    items, _ = await nexus.assignments.upcoming(days=2)
    css = next(a for a in items if a.name == "CSS TRY")
    assert css.status.status == "not_started" and css.can_still_submit is True
    overdue, _ = await nexus.assignments.overdue()
    by_name = {a.name: a for a in overdue}
    assert by_name["Late but open"].can_still_submit is True  # no cut-off: late work accepted
    assert by_name["Cut off"].can_still_submit is False


def test_submission_open_rules():
    from nexus_mcp.models.assignment import Assignment, SubmissionStatus
    from nexus_mcp.models.common import When

    def when(ts):
        return When(iso="", display="", short="", relative="", unix=ts)

    base = dict(id=1, cmid=1, course_id=1, course="c", name="a", url="u")
    status = SubmissionStatus(assignment_id=1, status="not_started", detail="", can_edit=True, can_submit=False, locked=False)
    assert submission_open(Assignment(**base, status=status), NOW) is True
    assert submission_open(Assignment(**base, status=status, opens=when(NOW + HOUR)), NOW) is False
    assert submission_open(Assignment(**base, status=status, cutoff=when(NOW - 1)), NOW) is False
    assert submission_open(Assignment(**base, status=status.model_copy(update={"locked": True})), NOW) is False
    assert submission_open(Assignment(**base, submission_required=False), NOW) is None


def test_hidden_assignment_warning_is_plain_english():
    out = summarize_warnings([{"message": "No access rights in module context", "itemid": i} for i in range(6)])
    assert out == ["6 assignments are hidden on Nexus (not released to students yet); Nexus doesn't show their names"]


# -- briefing noise --------------------------------------------------------


def test_calendar_due_event_matches_assignment_by_cmid():
    from nexus_mcp.models.assignment import Assignment

    a = Assignment(id=81243, cmid=907931, course_id=1, course="c", name="Homework 1", url="u")
    action_event = CalendarEvent(id=1, name="Homework 1 is due", module="assign", instance=907931, type="due")
    calendar_event = CalendarEvent(id=2, name="Homework 1 is due", module="assign", instance=81243, type="due")
    exam = CalendarEvent(id=3, name="Exam 1", type="course")
    assert intelligence._covered(action_event, [a]) and intelligence._covered(calendar_event, [a])
    assert not intelligence._covered(exam, [a])
    assert intelligence.short_course("26/FA.CSC-240-01", "26/FA.CSC-240-01 - Web Programming") == "CSC-240"


# -- Google links ----------------------------------------------------------


def test_parse_google_links():
    doc = parse_google_link("https://docs.google.com/document/d/1NaZq4hGRVlSnBwl_6shgSwXGIFgsUtQsqCHbx3ArhB4/edit?usp=sharing")
    assert doc.kind == "document" and doc.file_id == "1NaZq4hGRVlSnBwl_6shgSwXGIFgsUtQsqCHbx3ArhB4" and doc.label == "Google Doc"
    assert parse_google_link("https://docs.google.com/presentation/d/1PXd69YIqbncmjFhjp6-Cg6AVn7GuokbEXB6372bxZoY/edit").kind == "presentation"
    form = parse_google_link("https://docs.google.com/forms/d/e/1FAIpQLSfd0yycyg_yQ4z5Sm1XxrN8XCfUi3skCZnnsdGloik0CCQoPA/viewform")
    assert form.kind == "forms" and not form.readable
    assert parse_google_link("https://drive.google.com/drive/folders/1RagrDyS_QhLNPNL9RzHKTC93fbbrvtBg?usp=drive_link").kind == "folder"
    assert parse_google_link("https://example.com/x") is None


DOC_URL = "https://docs.google.com/document/d/1NaZq4hGRVlSnBwl_6shgSwXGIFgsUtQsqCHbx3ArhB4/edit?usp=sharing"


@pytest.fixture
def google_world(fake):
    fake.on("core_enrol_get_users_courses", [course(1, CSC, "26/FA.CSC-385-01")])
    fake.on("core_course_get_courses_by_field", {"courses": [], "warnings": []})
    fake.on("core_course_get_course_module", {"cm": {"id": 40, "course": 1, "modname": "url"}})
    fake.on("core_course_get_contents", [section(1, "General", [module(40, "url", "Timeline - LIVE", contents=[{"type": "url", "filename": "Timeline", "fileurl": DOC_URL, "timemodified": NOW}])])])
    fake.on("core_course_get_updates_since", {"instances": [], "warnings": []})
    fake.on("mod_forum_get_forums_by_courses", [])
    fake.on("message_popup_get_popup_notifications", {"notifications": [], "unreadcount": 0})


async def test_google_link_without_connection_explains_what_to_do(google_world, nexus):
    got = await nexus.materials.get_material("40")
    assert got.content == DOC_URL and "Union accounts only" in got.note and "nexus-mcp google login" in got.note


async def test_google_doc_is_read_and_edits_are_flagged(google_world, nexus, tmp_path):
    doc = {"text": "Week 1: HTML\nWeek 2: CSS", "modified": "2026-09-20T10:00:00Z"}
    with respx.mock(assert_all_called=False) as g:
        g.post("https://oauth2.googleapis.com/token").respond(json={"access_token": "ya29.x", "expires_in": 3600})
        g.get(url__regex=r".*/files/[^/]+/export.*").mock(side_effect=lambda r: httpx.Response(200, text=doc["text"]))
        g.get(url__regex=r".*/drive/v3/files/[^/?]+\?.*").mock(
            side_effect=lambda r: httpx.Response(200, json={"id": "x", "name": "Timeline", "mimeType": "application/vnd.google-apps.document", "modifiedTime": doc["modified"], "lastModifyingUser": {"displayName": "Shruti Mahajan"}})
        )
        g.route(host="nexus.test").pass_through()
        drive = GoogleDrive(tmp_path)
        drive.creds.save({"client_id": "cid", "client_secret": "s", "refresh_token": "r"})
        nexus._google = drive

        got = await nexus.materials.get_material("40")
        assert got.content == "Week 1: HTML\nWeek 2: CSS" and "Shruti Mahajan" in got.note

        since = parse_since("24h", nexus.now())
        assert (await nexus.notifications.course_updates(since))["module_updates"] == []  # baseline
        doc.update(text="Week 1: HTML\nWeek 2: CSS\nWeek 3: JavaScript", modified="2026-09-21T09:00:00Z")
        nexus.cache.invalidate()
        [upd] = (await nexus.notifications.course_updates(since))["module_updates"]
        assert upd["module"] == "Timeline - LIVE" and upd["changes"] == ["Google file edited"]
        assert upd["added"] == ["Week 3: JavaScript"]
