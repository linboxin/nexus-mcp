import pytest

from nexus_mcp.config import CacheTTLs
from nexus_mcp.errors import NexusNotFoundError, NexusPermissionError
from tests.conftest import (
    DAY,
    HOUR,
    NOW,
    SITE,
    assignment,
    assignments_payload,
    course,
    make_nexus,
    make_settings,
    submission,
    submissions_by_id,
)

CSC = "CSC-385 - Computer Graphics"
WEB = "Web Programming"


@pytest.fixture
def world(fake):
    fake.on(
        "core_enrol_get_users_courses",
        [course(1, CSC, "CSC-385"), course(2, WEB, "WEB"), course(3, "Ancient History", "OLD", start=NOW - 300 * DAY, end=NOW - 100 * DAY)],
    )
    fake.on("core_course_get_courses_by_field", {"courses": [], "warnings": []})
    fake.on(
        "mod_assign_get_assignments",
        assignments_payload(
            {
                1: (
                    CSC,
                    [
                        assignment(1, 1, "Lab 3", due=NOW + 8 * HOUR),
                        assignment(3, 1, "Lab 2", due=NOW - 2 * DAY),
                        assignment(5, 1, "Lab 1", due=NOW - 10 * DAY),
                        assignment(7, 1, "No due date", due=None),
                        assignment(8, 1, "Old thing", due=NOW - 200 * DAY),
                        assignment(9, 1, "Attendance", due=NOW - DAY, nosubmissions=1),
                        assignment(12, 1, "Closed", due=NOW - 4 * DAY, cutoff=NOW - DAY),
                    ],
                ),
                2: (
                    WEB,
                    [
                        assignment(2, 2, "Project Proposal", due=NOW + DAY + 2 * HOUR, grade=50),
                        assignment(4, 2, "Reading Response", due=NOW - 3 * DAY),
                        assignment(6, 2, "Essay", due=NOW - DAY),
                        assignment(10, 2, "Far away", due=NOW + 20 * DAY),
                    ],
                ),
                3: ("Ancient History", [assignment(11, 3, "Ancient", due=NOW - 5 * DAY)]),
            }
        ),
    )
    fake.on(
        "mod_assign_get_submission_status",
        submissions_by_id(
            {
                1: submission("new"),
                2: submission("draft"),
                3: submission("new"),
                4: submission("submitted"),
                5: submission("submitted", graded=True, grade_display="95.00 / 100.00", feedback_comment="<p>Nice work</p>"),
                6: submission("new", extension=NOW + 3 * DAY),
                10: submission("new"),
                11: submission("new"),
                12: submission("new", cansubmit=False),
            }
        ),
    )
    return fake


async def test_upcoming_orders_and_attaches_status(world, nexus):
    items, warnings = await nexus.assignments.upcoming(days=7)
    assert warnings == []
    assert [a.id for a in items] == [1, 2]
    lab3, proposal = items
    assert lab3.status.status == "not_started" and lab3.status.detail.startswith("Not started")
    assert proposal.status.status == "draft"
    assert lab3.due.relative == "today at 8:00 PM"
    assert proposal.due.relative == "tomorrow at 2:00 PM"
    assert lab3.url == f"{SITE}/mod/assign/view.php?id=10"
    assert lab3.points == 100 and proposal.points == 50
    assert lab3.submission_types == ["file upload", "online text"]
    assert lab3.course == CSC and lab3.course_short == "CSC-385"
    # no courseids filter -> one call for all courses, one status call per candidate
    assert len(world.calls_to("mod_assign_get_assignments")) == 1
    assert sorted(f["assignid"] for f in world.calls_to("mod_assign_get_submission_status")) == ["1", "2"]


async def test_upcoming_window_and_submitted_filter(world, nexus):
    items, _ = await nexus.assignments.upcoming(days=30)
    assert [a.id for a in items] == [1, 2, 10]
    items, _ = await nexus.assignments.upcoming(days=30, include_submitted=False)
    assert [a.id for a in items] == [1, 2, 10]
    items, _ = await nexus.assignments.upcoming(days=7, course_id=2)
    assert [a.id for a in items] == [2]
    assert world.calls_to("mod_assign_get_assignments")[-1]["courseids[0]"] == "2"


async def test_overdue_only_actually_overdue(world, nexus):
    items, warnings = await nexus.assignments.overdue()
    assert [a.id for a in items] == [12, 3]
    lab2 = next(a for a in items if a.id == 3)
    assert lab2.is_overdue and lab2.status.status == "overdue"
    assert lab2.can_still_submit is True
    assert "nothing submitted" in lab2.status.detail
    closed = next(a for a in items if a.id == 12)
    assert closed.can_still_submit is False
    assert "cut-off" in closed.status.detail
    assert warnings == []
    checked = sorted(int(f["assignid"]) for f in world.calls_to("mod_assign_get_submission_status"))
    assert checked == [3, 4, 5, 6, 12]  # not 7 (undated), 8 (too old), 9 (no submission), 11 (past course)


async def test_overdue_for_past_course_when_asked(world, nexus):
    items, _ = await nexus.assignments.overdue(course_id=3)
    assert [a.id for a in items] == [11]


async def test_extension_moves_due_date(world, nexus):
    status = await nexus.assignments.submission_status(6)
    assert status.status == "not_started"
    assert status.extension_due is not None and status.extension_due.relative == "Monday at 12:00 PM"
    items, _ = await nexus.assignments.overdue(course_id=2)
    assert all(a.id != 6 for a in items)


async def test_graded_and_submitted_statuses(world, nexus):
    graded = await nexus.assignments.submission_status(5)
    assert graded.status == "graded" and graded.grade == "95.00 / 100.00" and graded.feedback == "Nice work"
    assert graded.graded_at is not None and graded.attempt_number == 1
    submitted = await nexus.assignments.submission_status(4)
    assert submitted.status == "submitted" and submitted.submitted_at is not None
    assert submitted.detail.startswith("Submitted, awaiting grade")


async def test_overdue_draft_detail(world, nexus):
    world.on("mod_assign_get_submission_status", submissions_by_id({3: submission("draft")}))
    status = await nexus.assignments.submission_status(3)
    assert status.status == "overdue" and "draft saved but never submitted" in status.detail
    assert status.moodle_submission_status == "draft"


async def test_assignment_without_due_date(world, nexus):
    a = await nexus.assignments.get(7)
    assert a.due is None
    world.on("mod_assign_get_submission_status", submissions_by_id({7: submission("new")}))
    status = await nexus.assignments.submission_status(7)
    assert status.status == "not_started" and status.detail == "Not started"


async def test_unknown_assignment(world, nexus):
    with pytest.raises(NexusNotFoundError):
        await nexus.assignments.submission_status(999)


async def test_lookup_by_cmid_fallback(world, nexus):
    assert (await nexus.assignments.get(30)).id == 3


async def test_submission_status_freshness_flag(world):
    nx = make_nexus(make_settings(cache=CacheTTLs(submission=60, grades=60)))
    await nx.ensure_ready()
    first = await nx.assignments.submission_status(1)
    second = await nx.assignments.submission_status(1)
    assert first.freshness.cached is False
    assert second.freshness.cached is True and "Cached" in second.freshness.note
    assert len(world.calls_to("mod_assign_get_submission_status")) == 1
    await nx.aclose()


async def test_permission_error_on_status_is_reported_not_hidden(world, nexus):
    world.error("mod_assign_get_submission_status", "nopermissions", "Sorry, no")
    items, warnings = await nexus.assignments.overdue(course_id=1)
    assert items == []
    assert warnings and "could not be checked" in warnings[0]
    with pytest.raises(NexusPermissionError):
        await nexus.assignments.submission_status(3)


async def test_details_include_requirements(world, nexus):
    detail = await nexus.assignments.details(1)
    assert detail.instructions == "Do the thing."
    assert detail.submission_requirements["max_files"] == "2"
    assert detail.submission_requirements["accepted_file_types"] == "pdf"
    assert detail.submission_requirements["submission_types"] == ["file upload", "online text"]
    assert detail.grading["max_points"] == 100
    assert detail.status.status == "not_started"


async def test_missing_status_function_sets_note(fake):
    from tests.conftest import site_info

    fake.on("core_webservice_get_site_info", site_info(functions=["core_webservice_get_site_info", "core_user_get_users_by_field", "core_enrol_get_users_courses", "mod_assign_get_assignments"]))
    fake.on("core_enrol_get_users_courses", [course(1, CSC)])
    fake.on("mod_assign_get_assignments", assignments_payload({1: (CSC, [assignment(1, 1, "Lab 3", due=NOW + HOUR)])}))
    nx = make_nexus()
    await nx.ensure_ready()
    items, _ = await nx.assignments.upcoming()
    assert items[0].status is None and "not enabled" in items[0].status_note
    await nx.aclose()


def test_summarize_warnings_collapses_duplicates():
    from nexus_mcp.moodle.assignments import summarize_warnings

    raw = [{"item": "module", "itemid": i, "warningcode": "1", "message": "No access rights in module context"} for i in range(21)]
    raw.append({"item": "course", "itemid": 5, "warningcode": "2", "message": "Course is hidden"})
    out = summarize_warnings(raw)
    assert out == ["21 assignments skipped by Nexus: No access rights in module context (hidden or inaccessible modules)", "Course is hidden"]
