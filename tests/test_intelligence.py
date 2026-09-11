import pytest

from nexus_mcp import intelligence
from tests.conftest import DAY, HOUR, NOW, assignment, assignments_payload, course, submission, submissions_by_id

CSC = "CSC-385 - Computer Graphics"
WEB = "Web Programming"


@pytest.fixture
def world(fake):
    fake.on("core_enrol_get_users_courses", [course(1, CSC, "CSC-385"), course(2, WEB, "WEB")])
    fake.on("core_course_get_courses_by_field", {"courses": [], "warnings": []})
    fake.on(
        "mod_assign_get_assignments",
        assignments_payload(
            {
                1: (CSC, [assignment(1, 1, "Lab 3", due=NOW + 11 * HOUR + 59 * 60), assignment(3, 1, "Lab 2", due=NOW - 2 * DAY)]),
                2: (WEB, [assignment(2, 2, "Project Proposal", due=NOW + DAY + 6 * HOUR, grade=50), assignment(4, 2, "Reading", due=NOW + 4 * DAY), assignment(5, 2, "Done thing", due=NOW + 2 * DAY)]),
            }
        ),
    )
    fake.on("mod_assign_get_submission_status", submissions_by_id({1: submission("new"), 2: submission("draft"), 3: submission("new"), 4: submission("new"), 5: submission("submitted")}))
    fake.on("core_calendar_get_calendar_events", {"events": [{"id": 50, "name": "Quiz 1 closes", "description": "", "courseid": 2, "modulename": "quiz", "instance": 9, "eventtype": "close", "timestart": NOW + 30 * HOUR, "timeduration": 0}], "warnings": []})
    fake.on("core_calendar_get_action_events_by_timesort", {"events": [], "firstid": 0, "lastid": 0})
    fake.on("mod_forum_get_forums_by_courses", [{"id": 6, "course": 2, "type": "news", "name": "Announcements"}])
    fake.on("mod_forum_get_forum_discussions", {"discussions": [{"id": 3, "discussion": 103, "name": "Proposal template posted", "subject": "Proposal template posted", "message": "<p>See attached</p>", "userfullname": "Prof. Grace Hopper", "created": NOW - 2 * HOUR, "timemodified": NOW - 2 * HOUR, "attachments": []}], "warnings": []})
    fake.on("core_course_get_updates_since", {"instances": [], "warnings": []})
    fake.on("message_popup_get_popup_notifications", {"notifications": [], "unreadcount": 0})
    return fake


async def test_daily_briefing(world, nexus):
    data = await intelligence.daily_briefing(nexus)
    text = data["text"]
    assert text.startswith("Good afternoon.")
    assert "🔴 TODAY\nCSC-385 — Lab 3 — due 11:59 PM — not started" in text
    assert "🟡 TOMORROW\nWeb — Project Proposal — due 6:00 PM — draft saved, not submitted" in text
    assert "⚠️ OVERDUE\nCSC-385 — Lab 2 — Sep 9, 12:00 PM — OVERDUE, not submitted" in text
    assert "📢 NEW\nWeb Programming — announcement: “Proposal template posted”" in text
    assert "Recommended priority:\n1. CSC-385 Lab 2 — Overdue" in text
    assert [a["id"] for a in data["today"]] == [1]
    assert [a["id"] for a in data["tomorrow"]] == [2]
    assert [a["id"] for a in data["due_soon"]] == [5, 4]
    assert data["priorities"][0]["priority"] == "HIGH" and data["priorities"][0]["title"] == "Lab 2"
    assert data["priorities"][1]["title"] == "Lab 3" and data["priorities"][1]["priority"] == "HIGH"
    assert data["timezone"] == "America/New_York"
    assert data["warnings"] == []


async def test_what_should_i_do_next(world, nexus):
    data = await intelligence.what_should_i_do_next(nexus)
    items = data["items"]
    assert [(i["title"], i["priority"]) for i in items] == [
        ("Lab 2", "HIGH"),
        ("Lab 3", "HIGH"),
        ("Project Proposal", "MEDIUM"),
        ("Quiz 1 closes", "MEDIUM"),
        ("Reading", "LOW"),
    ]
    assert "a draft is saved" in items[2]["reason"] and "worth 50 points" in items[2]["reason"]
    assert "Recent announcement" in items[2]["reason"]
    assert data["already_handled"] == [{"title": "Done thing", "course": WEB, "status": "submitted"}]
    assert "1. CSC-385 — Lab 2" in data["text"] and "Priority: HIGH" in data["text"]


async def test_weekly_briefing_groups_by_course(world, nexus):
    data = await intelligence.weekly_briefing(nexus)
    assert set(data["by_course"]) == {CSC, WEB}
    assert data["text"].index("⚠️ OVERDUE") < data["text"].index(CSC)
    assert "- Lab 3 — Sep 11, 11:59 PM (not started)" in data["text"]
    assert "- Quiz 1 closes (quiz closes) — Sep 12, 6:00 PM" in data["text"]


async def test_workload_analysis_separates_facts_from_estimates(world, nexus):
    data = await intelligence.workload_analysis(nexus, days=7)
    facts, estimates = data["facts"], data["estimates"]
    assert facts["assignments_due"] == 4 and facts["assignments_pending"] == 3 and facts["overdue"] == 1
    assert facts["assessments_closing"] == 1 and facts["total_points_due"] == 350
    assert facts["by_course"] == {CSC: 1, WEB: 3}
    assert "NOT instructor estimates" in estimates["disclaimer"]
    assert estimates["total_estimated_hours"] > 0 and len(estimates["items"]) == 5
    assert "nexus-mcp heuristic" in data["text"]


async def test_briefing_survives_partial_failures(world, nexus):
    world.error("core_calendar_get_calendar_events", "nopermissions", "no calendar")
    world.error("mod_forum_get_forums_by_courses", "nopermissions", "no forums")
    data = await intelligence.daily_briefing(nexus)
    assert [a["id"] for a in data["today"]] == [1]
    assert any("calendar" in w for w in data["warnings"]) and any("announcements" in w for w in data["warnings"])


def test_dedupe_warnings_collapses_repeated_skip_notes():
    from nexus_mcp.intelligence import dedupe_warnings

    raw = [
        "21 assignments skipped by Nexus: No access rights in module context (hidden or inaccessible modules)",
        "7 assignments skipped by Nexus: No access rights in module context (hidden or inaccessible modules)",
        "calendar: boom",
        "calendar: boom",
    ]
    assert dedupe_warnings(raw) == [raw[0], "calendar: boom"]
