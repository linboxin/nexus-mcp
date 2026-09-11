import pytest

from nexus_mcp.errors import NexusPermissionError
from tests.conftest import DAY, HOUR, NOW, course, make_nexus, site_info

CSC = "CSC-385 - Computer Graphics"
WEB = "Web Programming"


def grade_items_payload(items):
    return {"usergrades": [{"courseid": 1, "userid": 7, "userfullname": "Sam Student", "maxdepth": 2, "gradeitems": items}], "warnings": []}


def item(id, name, itemtype="mod", module="assign", cmid=None, graderaw=None, formatted="-", pct="-", **extra):
    data = {"id": id, "itemname": name, "itemtype": itemtype, "itemmodule": module, "iteminstance": id, "cmid": cmid or id * 10, "graderaw": graderaw, "gradeformatted": formatted, "grademin": 0, "grademax": 100, "percentageformatted": pct, "weightformatted": "20.00 %", "rangeformatted": "0–100", "lettergradeformatted": "-", "feedback": "", "feedbackformat": 1, "gradehiddenbydate": False, "gradeneedsupdate": False, "gradeishidden": False, "gradedategraded": None, "gradedatesubmitted": None}
    data.update(extra)
    return data


@pytest.fixture
def world(fake):
    fake.on("core_enrol_get_users_courses", [course(1, CSC, "CSC-385"), course(2, WEB, "WEB"), course(3, "Old", "OLD", start=NOW - 300 * DAY, end=NOW - 100 * DAY)])
    fake.on("core_course_get_courses_by_field", {"courses": [], "warnings": []})
    fake.on("gradereport_overview_get_course_grades", {"grades": [{"courseid": 1, "grade": "91.25 %", "rawgrade": "91.25"}, {"courseid": 2, "grade": "-", "rawgrade": ""}, {"courseid": 3, "grade": "88.00 %", "rawgrade": "88"}], "warnings": []})
    fake.on(
        "gradereport_user_get_grade_items",
        grade_items_payload(
            [
                item(101, "Lab 1", graderaw=95, formatted="95.00", pct="95.00 %", feedback="<p>Great</p>", gradedategraded=NOW - 5 * DAY, gradedatesubmitted=NOW - 6 * DAY),
                item(102, "Lab 2", graderaw=None, formatted="-", pct="-"),
                item(103, "Midterm", module="quiz", graderaw=80, formatted="80.00", pct="80.00 %", gradedategraded=NOW - 2 * DAY),
                item(104, None, itemtype="course", module=None, graderaw=91.25, formatted="91.25", pct="91.25 %", lettergradeformatted="A-"),
            ]
        ),
    )
    return fake


async def test_current_grades_overview(world, nexus):
    data = await nexus.grades.current_grades()
    by_course = {c["course"]: c for c in data["courses"]}
    assert by_course[CSC]["grade_display"] == "91.25 %" and by_course[CSC]["access"] == "ok"
    assert by_course[WEB]["access"] == "no_grade" and by_course[WEB]["grade_display"] is None
    assert "Old" not in by_course  # past course excluded by default
    assert data["freshness"]["cached"] is False
    assert data["warnings"] == []
    data = await nexus.grades.current_grades(include_past=True)
    assert any(c["course"] == "Old" for c in data["courses"])


async def test_current_grades_falls_back_and_reports_hidden(world, nexus):
    world.error("gradereport_overview_get_course_grades", "nopermissions", "Overview off")

    def items(form):
        if form["courseid"] == "2":
            return {"exception": "required_capability_exception", "errorcode": "nopermissions", "message": "Sorry, but you do not currently have permissions to do that (View the user report)"}
        return grade_items_payload([item(104, None, itemtype="course", module=None, graderaw=91.25, formatted="91.25", pct="91.25 %")])

    world.on("gradereport_user_get_grade_items", items)
    data = await nexus.grades.current_grades()
    by_course = {c["course"]: c for c in data["courses"]}
    assert by_course[CSC]["grade_display"] == "91.25" and by_course[CSC]["access"] == "ok"
    assert by_course[WEB]["access"] == "hidden" and "hidden" in by_course[WEB]["note"]
    assert data["warnings"] and "refused" in data["warnings"][0]


async def test_course_grade_items(world, nexus):
    grade = await nexus.grades.course_grade(1)
    assert grade.course == CSC
    assert grade.grade_display == "91.25" and grade.percentage == "91.25 %" and grade.letter == "A-"
    names = [i.name for i in grade.items]
    assert names == ["Lab 1", "Lab 2", "Midterm", "Course total"]
    lab1 = grade.items[0]
    assert lab1.grade_raw == 95 and lab1.feedback == "Great" and lab1.graded_at is not None
    assert grade.items[1].grade_display == "-" and grade.items[1].grade_raw is None
    assert grade.freshness is not None and grade.freshness.cached is False
    assert world.calls_to("gradereport_user_get_grade_items")[0]["userid"] == "7"


async def test_course_grade_permission_error_raises(world, nexus):
    world.error("gradereport_user_get_grade_items", "nopermissions", "hidden", exception="required_capability_exception")
    with pytest.raises(NexusPermissionError):
        await nexus.grades.course_grade(1)


async def test_grade_history_is_honest(world, nexus):
    history = await nexus.grades.grade_history(1)
    assert history["history_available"] is False
    assert "gradereport_history" in history["note"]
    assert [i["name"] for i in history["timeline"]] == ["Midterm", "Lab 1"]
    assert history["ungraded_items"] == ["Lab 2"]
    assert history["current_course_grade"] == "91.25"


async def test_grades_unavailable_when_functions_missing(fake):
    fake.on("core_webservice_get_site_info", site_info(functions=["core_webservice_get_site_info", "core_user_get_users_by_field", "core_enrol_get_users_courses"]))
    fake.on("core_enrol_get_users_courses", [course(1, CSC)])
    nx = make_nexus()
    await nx.ensure_ready()
    data = await nx.grades.current_grades()
    assert data["courses"][0]["access"] == "unavailable"
    await nx.aclose()
